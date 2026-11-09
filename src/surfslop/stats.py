"""Stats for the public portal: traffic, visitors, popular spots, refusals, latency, AI overviews.

`surfslop serve --public` records every request here and serves this page from a second server
that only the tailnet can reach: it listens on this Mac's Tailscale address (and 127.0.0.1), on
`stats_port` (7875), answers Tailscale and loopback callers only, and refuses anything that
arrived through the tunnel. `--no-stats` leaves it out.

What is kept (stats.json in the private folder, written every 30 s):
  * per day, for a rolling year: requests, by kind, refusals by reason, lookups per spot, time
    per kind, overviews written / served from cache;
  * visitors as keyed hashes (HMAC-SHA256 with a random key kept in the file), never as IPs. A
    visitor is the tunnel's CF-Connecting-IP (an IPv6 /64 counts as one). Hashes are kept for
    31 days, for the monthly figure; older days keep only their count.
  * requests per minute for the last hour, in memory only.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import mimetypes
import secrets
import threading
import time
from collections import deque
from http.server import BaseHTTPRequestHandler
from pathlib import Path

from . import __version__, config, netguard

KEEP_DAYS = 365
HASH_DAYS = 31
FLUSH_EVERY = 30
STATIC = Path(__file__).parent / "static"
STATS_FILES = {"/": "stats.html", "/static/stats.js": "stats.js", "/static/stats.css": "stats.css",
               "/static/app.css": "app.css", "/static/icon.svg": "icon.svg"}


def _day(t: float | None = None) -> str:
    return time.strftime("%Y-%m-%d", time.localtime(t))


class Stats:
    def __init__(self, path: Path | None = None):
        self.path = path or config.home() / "stats.json"
        self.lock = threading.Lock()
        self.minutes: deque = deque(maxlen=60)  # (minute, requests)
        self.started = time.time()
        try:
            saved = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            saved = {}
        self.key = bytes.fromhex(saved.get("key", "")) or secrets.token_bytes(32)
        self.days: dict = saved.get("days", {})
        for day in self.days.values():
            day["visitors"] = set(day.get("visitors", []))

    def _today(self) -> dict:
        return self.days.setdefault(_day(), {"requests": 0, "kinds": {}, "refused": {}, "spots": {}, "ms": {},
                                             "ai": {"written": 0, "cached": 0, "seconds": 0.0}, "visitors": set()})

    def record(self, visitor: str, kind: str, status: int, seconds: float, spot: str | None = None,
               reason: str | None = None) -> None:
        """One request the public portal answered (or refused)."""
        minute = int(time.time() // 60)
        with self.lock:
            day = self._today()
            day["requests"] += 1
            day["kinds"][kind] = day["kinds"].get(kind, 0) + 1
            if status >= 400:
                why = reason or str(status)
                day["refused"][why] = day["refused"].get(why, 0) + 1
            else:
                if spot:
                    day["spots"][spot] = day["spots"].get(spot, 0) + 1
                total = day["ms"].setdefault(kind, [0, 0.0, 0.0])  # count, sum, worst
                total[0] += 1
                total[1] += seconds * 1000
                total[2] = max(total[2], seconds * 1000)
                day["visitors"].add(hmac.new(self.key, visitor.encode(), hashlib.sha256).hexdigest()[:16])
            if self.minutes and self.minutes[-1][0] == minute:
                self.minutes[-1][1] += 1
            else:
                self.minutes.append([minute, 1])

    def overview(self, written: bool, seconds: float = 0.0) -> None:
        with self.lock:
            ai = self._today()["ai"]
            ai["written" if written else "cached"] += 1
            ai["seconds"] += seconds if written else 0

    def flush(self) -> None:
        with self.lock:
            days = sorted(self.days)
            for old in days[:-KEEP_DAYS]:
                del self.days[old]
            for old in days[:-HASH_DAYS]:
                day = self.days.get(old)
                if day and day["visitors"]:
                    day["visitor_count"] = len(day["visitors"])
                    day["visitors"] = set()
            out = {"key": self.key.hex(),
                   "days": {d: {**v, "visitors": sorted(v["visitors"])} for d, v in self.days.items()}}
        config.write_private(self.path, json.dumps(out))

    def run(self, stop: threading.Event) -> None:
        while not stop.wait(FLUSH_EVERY):
            self.flush()
        self.flush()

    def summary(self) -> dict:
        now_minute = int(time.time() // 60)
        with self.lock:
            days = sorted(self.days)
            recent = days[-30:]

            def count(day: dict) -> int:
                return len(day["visitors"]) or day.get("visitor_count", 0)

            def total(field: str) -> dict:
                out: dict = {}
                for d in recent:
                    for name, n in self.days[d][field].items():
                        out[name] = out.get(name, 0) + n
                return dict(sorted(out.items(), key=lambda kv: -kv[1]))

            monthly = set().union(*(self.days[d]["visitors"] for d in recent)) if recent else set()
            today = self.days.get(_day(), {})
            ai = {k: sum(self.days[d]["ai"][k] for d in recent) for k in ("written", "cached", "seconds")}
            per_minute = dict(self.minutes)
            return {
                "version": __version__, "generated": int(time.time()), "up_since": int(self.started),
                "today": {"requests": today.get("requests", 0), "visitors": count(today) if today else 0,
                          "kinds": today.get("kinds", {}),
                          "ms": {k: {"count": v[0], "mean": round(v[1] / v[0]), "worst": round(v[2])}
                                 for k, v in today.get("ms", {}).items() if v[0]}},
                "minutes": [per_minute.get(m, 0) for m in range(now_minute - 59, now_minute + 1)],
                "daily": [{"date": d, "requests": self.days[d]["requests"], "visitors": count(self.days[d])} for d in recent],
                "month": {"requests": sum(self.days[d]["requests"] for d in recent), "visitors": len(monthly),
                          "spots": total("spots"), "refused": total("refused"), "kinds": total("kinds"),
                          "ai": {"written": ai["written"], "cached": ai["cached"],
                                 "mean_seconds": round(ai["seconds"] / ai["written"], 1) if ai["written"] else None}},
                "year": [{"date": d, "visitors": count(self.days[d])} for d in days],
            }


def make_handler(stats: Stats):
    class StatsHandler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = f"surfslop-stats/{__version__}"

        def log_message(self, fmt, *args):
            pass

        def send(self, status: int, body: bytes, kind: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy", "default-src 'self'; base-uri 'none'; form-action 'none'")
            self.send_header("Connection", "close")
            self.end_headers()
            self.close_connection = True
            if self.command != "HEAD":
                self.wfile.write(body)

        def do_GET(self):  # noqa: N802
            refused = netguard.stats_allowed(self.client_address[0], self.headers)
            if refused:
                return self.send(403, json.dumps({"error": refused}).encode(), "application/json")
            path = self.path.split("?", 1)[0]
            if path == "/stats.json":
                return self.send(200, json.dumps(stats.summary()).encode(), "application/json")
            name = STATS_FILES.get(path)
            if not name:
                return self.send(404, b'{"error": "no such page"}', "application/json")
            kind = mimetypes.guess_type(name)[0] or "application/octet-stream"
            self.send(200, (STATIC / name).read_bytes(), kind + ("; charset=utf-8" if kind.startswith("text/") or "javascript" in kind else ""))

        do_HEAD = do_GET  # noqa: N815

    return StatsHandler
