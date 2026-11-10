"""The portal: one read-only HTTP server for the web page and for agents.

    GET /                              the web page (also /?spot=steamer-lane)
    GET /llms.txt                      how an agent uses this, in Markdown
    GET /v1/status                     sources and settings
    GET /v1/spots[?q=ocean]            the spots
    GET /v1/overview[?days=3&spots=featured|all&format=json|md&ai=1]
    GET /v1/forecast/{spot}[?format=json|md&hours=0&ai=1]
    GET /v1/forecast/{spot}/ai         the written overview, streamed (server-sent events)
    GET /v1/overview/ai                the regional overview, streamed
    GET /v1/satellite/{spot}.jpg[?arrows=1&t=EPOCH]
    GET /v1/card.png[?days=3]          the featured spots as a picture

Everything is GET; nothing here changes anything. A request that carries a body is refused and
its connection closed, so a body can never be read as a second request on a kept-alive connection.

Private (the default): who may call is netguard.allowed: this Mac, your network, your tailnet.

Public (`serve --public`, see PUBLIC.md): for a Cloudflare Tunnel on this Mac.
  * Listens on 127.0.0.1 only; the Host must be one of your public names (or a local one).
  * The visitor is Cloudflare's CF-Connecting-IP, trusted only from loopback; limits are per visitor.
  * Only the open sources are fused and shown: no Surfline, no surf-forecast.com, no forecaster
    text; overviews are written from the open data and stored apart from the private ones.
  * Visitors cannot spend the local model freely: an overview is written once per spot per day,
    a few at a time, a capped number an hour; `ai=1` and `refresh=1` do nothing.
  * /v1/status is trimmed; error messages name nothing on this Mac; visitors are logged as hashes.
  * A second server, for the tailnet only, shows the stats (stats.py).
"""

from __future__ import annotations

import gzip
import hashlib
import json
import logging
import mimetypes
import signal
import threading
import time
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

from . import __version__, ai, card, config, daily, fusion, net, netguard, satellite, spots, surf, text
from . import stats as statistics
from .sources import surfline

log = logging.getLogger("surfslop")
STATIC = Path(__file__).parent / "static"
PAGE_FILES = {"index.html", "app.js", "app.css", "icon.svg", "apple-touch-icon.png", "icon-192.png", "icon-512.png",
              "manifest.webmanifest"}
# Where phones and browsers look for an icon without being told.
ROOT_ICONS = {"/apple-touch-icon.png": "apple-touch-icon.png", "/apple-touch-icon-precomposed.png": "apple-touch-icon.png",
              "/favicon.ico": "icon-192.png"}
SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Cross-Origin-Resource-Policy": "same-origin",
    "Content-Security-Policy": "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; "
                               "connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'",
}
MAX_IN_FLIGHT = 64  # requests being answered at once; past that, 503

GUIDE = """# surfslop: Bay Area surf forecasts for agents

Read-only. Base URL: the address you were given (e.g. http://100.x.y.z:7873). No token: the Mac
knows you by your Tailscale address.

- `GET /v1/overview?format=md` the featured spots × next 3 days (add `days=7`, `spots=all`, `ai=1`
  for a written "where to go").
- `GET /v1/forecast/{spot}?format=md` one spot, a week: fused forecast, every source's own numbers,
  wind, tides, buoys. `ai=1` adds a written overview (allow 60 s). `format=json&hours=0` for data.
- `GET /v1/spots?q=name` spot ids. Names and nicknames work in place of an id ("OB", "linda mar").
- `GET /v1/satellite/{spot}.jpg?arrows=1` satellite picture with the swell and wind drawn on it
  (`t=EPOCH` for another hour).
- `GET /v1/card.png?days=3` the overview as a picture, ready to post.
- `GET /v1/status` which sources are up.

Wave heights are feet of wave face at the break. "clean / fair / blown" is the wind's effect on the
surface, for the morning (6-10), midday (10-14) and afternoon (14-18), Pacific time. Confidence is
how well the sources agree. Say which sources a number came from when it matters.
"""


class Limiter:
    """What one public visitor, and all of them together, may ask for."""

    RATE, BURST = 1.0, 60          # requests: one a second, bursts of sixty (a page load is about ten)
    PICTURE_COST = 3               # a drawn picture counts as three
    AI_EACH, AI_TOTAL = 2, 8       # overview streams open at once: per visitor, in all
    WRITES_AN_HOUR = 40            # overviews the local model may write for visitors in an hour
    MAX_VISITORS = 20000

    def __init__(self):
        self.lock = threading.Lock()
        self.buckets: dict = {}    # visitor -> [tokens, last seen]
        self.streams: dict = {}    # visitor -> open overview streams
        self.writes: deque = deque()

    def allow(self, visitor: str, cost: float = 1) -> bool:
        now = time.time()
        with self.lock:
            if len(self.buckets) > self.MAX_VISITORS:
                for name in [v for v, (_, seen) in self.buckets.items() if now - seen > 120]:
                    del self.buckets[name]
            tokens, seen = self.buckets.get(visitor, (self.BURST, now))
            tokens = min(self.BURST, tokens + (now - seen) * self.RATE)
            if tokens < cost:
                self.buckets[visitor] = [tokens, now]
                return False
            self.buckets[visitor] = [tokens - cost, now]
            return True

    def stream_open(self, visitor: str) -> bool:
        with self.lock:
            if self.streams.get(visitor, 0) >= self.AI_EACH or sum(self.streams.values()) >= self.AI_TOTAL:
                return False
            self.streams[visitor] = self.streams.get(visitor, 0) + 1
            return True

    def stream_closed(self, visitor: str) -> None:
        with self.lock:
            left = self.streams.get(visitor, 1) - 1
            if left > 0:
                self.streams[visitor] = left
            else:
                self.streams.pop(visitor, None)

    def may_write(self) -> bool:
        now = time.time()
        with self.lock:
            while self.writes and now - self.writes[0] > 3600:
                self.writes.popleft()
            if len(self.writes) >= self.WRITES_AN_HOUR:
                return False
            self.writes.append(now)
            return True


class Portal:
    """How this server was started: private or public, and what goes with public."""

    def __init__(self, public: bool = False, hosts=(), stats: statistics.Stats | None = None):
        self.public, self.hosts, self.stats = public, tuple(hosts), stats
        self.limiter = Limiter() if public else None
        self.in_flight = threading.BoundedSemaphore(MAX_IN_FLIGHT)
        self.memo: dict = {}
        self.memo_lock = threading.Lock()

    def remember(self, key, seconds: float, build):
        """Drawn pictures are kept a few minutes, so reloading one costs nothing."""
        with self.memo_lock:
            hit = self.memo.get(key)
            if hit and hit[0] > time.time():
                return hit[1]
        value = build()
        with self.memo_lock:
            if len(self.memo) > 96:
                self.memo.clear()
            self.memo[key] = (time.time() + seconds, value)
        return value


def _int(q: dict, name: str, default: int, lo: int, hi: int) -> int:
    try:
        return min(max(int(q.get(name, [default])[0]), lo), hi)
    except ValueError:
        return default


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = f"surfslop/{__version__}"
    portal = Portal()

    def log_message(self, fmt, *args):  # the default writes every request to stderr
        pass

    # --- replies ---

    def reply(self, status: int, body: bytes, content_type: str, cache: str = "no-store", extra: dict | None = None) -> None:
        if len(body) > 1024 and "gzip" in self.headers.get("Accept-Encoding", "") and not content_type.startswith("image/"):
            body = gzip.compress(body, 5)
            encoding = "gzip"
        else:
            encoding = None
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", cache)
        if encoding:
            self.send_header("Content-Encoding", encoding)
        self.common_headers()
        for name, value in (extra or {}).items():
            self.send_header(name, value)
        if self.close_connection:
            self.send_header("Connection", "close")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)
        self.status = status

    def common_headers(self) -> None:
        for name, value in SECURITY_HEADERS.items():
            self.send_header(name, value)
        if self.portal.public:
            self.send_header("Strict-Transport-Security", "max-age=31536000")

    def json(self, data, status: int = 200, extra: dict | None = None) -> None:
        self.reply(status, json.dumps(data, separators=(",", ":")).encode(), "application/json; charset=utf-8", extra=extra)

    def markdown(self, words: str) -> None:
        self.reply(200, words.encode(), "text/markdown; charset=utf-8")

    def error(self, status: int, code: str, message: str, extra: dict | None = None) -> None:
        self.reason = code
        self.json({"error": {"code": code, "message": message}}, status, extra)

    def events(self, pieces) -> None:
        """Server-sent events: each piece of text as it is written, then `done` (or `unavailable`)."""
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.common_headers()
        self.end_headers()
        self.close_connection = True
        self.status = 200

        def send(event: str, data) -> None:
            self.wfile.write(f"event: {event}\ndata: {json.dumps(data)}\n\n".encode())
            self.wfile.flush()

        try:
            try:
                for piece in pieces:
                    send("text", piece)
                send("done", {})
            except ai.Unavailable as e:
                send("unavailable", str(e))
        except (BrokenPipeError, ConnectionResetError):
            pass  # the reader left; closing the generator stops the model

    # --- every request ---

    def refuse_method(self):
        """POST, PUT and the rest: nothing here takes them."""
        self.close_connection = True
        self.status, self.reason = 0, ""
        self.error(405, "method_not_allowed", "this portal is read-only: GET and HEAD", {"Allow": "GET, HEAD"})

    do_POST = do_PUT = do_PATCH = do_DELETE = do_OPTIONS = refuse_method  # noqa: N815

    def has_body(self) -> bool:
        length = self.headers.get("Content-Length", "0").strip()
        return bool(self.headers.get("Transfer-Encoding")) or not length.isdigit() or int(length) > 0

    def do_GET(self):  # noqa: N802
        started, self.status, self.reason, self.kind, self.spot = time.time(), 0, "", "other", None
        portal, peer = self.portal, self.client_address[0]
        self.visitor = netguard.visitor(peer, self.headers.get("CF-Connecting-IP")) if portal.public else peer
        if self.has_body():
            self.close_connection = True  # never read the body as the next request
            self.error(400, "body_not_allowed", "requests to this portal carry no body")
        elif not portal.in_flight.acquire(blocking=False):
            self.error(503, "busy", "the portal is busy; try again in a moment", {"Retry-After": "2"})
        else:
            try:
                self.route(peer)
            except (BrokenPipeError, ConnectionResetError):
                return
            except net.FetchError as e:
                self.error(502, "source_unavailable", "a forecast source is unavailable right now" if portal.public else str(e))
            except Exception:  # noqa: BLE001
                log.exception("%s failed", self.path)
                self.error(500, "internal_error", "something went wrong; try again later")
            finally:
                portal.in_flight.release()
        took = time.time() - started
        if portal.stats:
            portal.stats.record(self.visitor, self.kind, self.status, took, self.spot, self.reason or None)
        if self.path.startswith("/v1") and (portal.public or not netguard.is_loopback(peer)):
            who = "visitor " + hashlib.sha256(self.visitor.encode()).hexdigest()[:8] if portal.public else peer
            log.info("%s from %s: %d in %.1fs", self.path, who, self.status, took)

    do_HEAD = do_GET  # noqa: N815

    def guard(self, peer: str, cfg: config.Config) -> bool:
        """True when the request may proceed; otherwise the refusal has been sent."""
        portal = self.portal
        if portal.public:
            refused = netguard.public_allowed(peer, self.headers.get("Host"), portal.hosts)
            if refused:
                self.error(403, "forbidden", "not available here")
                return False
        else:
            refused = netguard.allowed(peer, self.headers.get("Host"), cfg.peers)
            if refused:
                log.warning("refused %s from %s: %s", self.path, peer, refused)
                self.error(403, "forbidden", refused)
                return False
        # A page on another site may link here, but may not read the API through a visitor's browser.
        if self.headers.get("Sec-Fetch-Site") in ("cross-site", "same-site") and self.headers.get("Sec-Fetch-Mode") != "navigate":
            self.error(403, "cross_site", "requests from other sites aren't answered")
            return False
        return True

    def route(self, peer: str) -> None:
        cfg, portal, public = config.load(), self.portal, self.portal.public
        if not self.guard(peer, cfg):
            return
        url = urlsplit(self.path)
        path, q = unquote(url.path).rstrip("/") or "/", parse_qs(url.query)
        parts = path.strip("/").split("/")
        md = q.get("format", [""])[0] == "md"
        want_ai = q.get("ai", ["0"])[0] == "1" and not public  # a visitor can't make the model write on demand
        arrows = q.get("arrows", ["0"])[0] == "1"
        picture = path == "/v1/card.png" or (parts[:2] == ["v1", "satellite"] and arrows)
        if public and not portal.limiter.allow(self.visitor, Limiter.PICTURE_COST if picture else 1):
            return self.error(429, "slow_down", "too many requests; wait a little", {"Retry-After": "5"})

        if path == "/":
            self.kind = "page"
            return self.static("index.html")
        if parts[0] == "static" and len(parts) == 2:
            self.kind = "page"
            return self.static(parts[1])
        if path in ROOT_ICONS:
            self.kind = "page"
            return self.static(ROOT_ICONS[path])
        if path in ("/llms.txt", "/v1", "/v1/help"):
            return self.markdown(GUIDE)
        if path == "/v1/status":
            return self.json(self.status_data(cfg))
        if path == "/v1/spots":
            return self.json({"spots": [spots.public(s) for s in spots.search(q.get("q", [""])[0])]})
        if path == "/v1/overview/ai":
            self.kind = "ai"
            data = fusion.overview(cfg, public=public)
            return self.overview_stream(ai.region_cached(data, cfg), lambda: ai.region_stream(data, cfg))
        if path == "/v1/overview":
            self.kind = "overview"
            chosen = spots.SPOTS if q.get("spots", [""])[0] == "all" else spots.FEATURED
            data = fusion.overview(cfg, chosen, _int(q, "days", 3, 1, fusion.DAYS), public=public)
            region = fusion.overview(cfg, public=public)
            written = ai.write(ai.region_stream(region, cfg)) if want_ai else (ai.region_cached(region, cfg) or {}).get("text")
            if md:
                return self.markdown(text.overview_md(data) + (f"\n## Overview (local AI)\n\n{written}\n" if written else ""))
            return self.json({**data, "overview": written})
        if path == "/v1/card.png":
            self.kind = "image"
            days = _int(q, "days", 3, 1, 5)
            body = portal.remember(("card", days), 600, lambda: card.render(fusion.overview(cfg, spots.FEATURED, days, public=public)))
            return self.reply(200, body, "image/png")
        if len(parts) >= 3 and parts[:2] == ["v1", "forecast"]:
            spot = spots.find(parts[2])
            if not spot:
                return self.error(404, "unknown_spot", f'no spot called "{parts[2][:60]}"; see /v1/spots')
            self.kind, self.spot = "forecast", spot.id
            refresh = q.get("refresh", ["0"])[0] == "1" and not public and netguard.is_loopback(peer)
            data = fusion.forecast(spot, cfg, refresh=refresh, public=public)
            if parts[3:] == ["ai"]:
                self.kind = "ai"
                return self.overview_stream(ai.spot_cached(data, cfg), lambda: ai.spot_stream(data, cfg))
            if parts[3:]:
                return self.error(404, "not_found", "no such page")
            stored = ai.spot_cached(data, cfg) or {}
            written = ai.write(ai.spot_stream(data, cfg)) if want_ai else stored.get("text")
            if md:
                return self.markdown(text.forecast_md(data, written))
            out = {**data, "overview": written, "overview_written": stored.get("generated") if written else None}
            out.pop("writeups", None)  # the sources' own words stay on the Mac; the overview is ours
            if q.get("hours", ["1"])[0] == "0":
                out.pop("hours"), out.pop("source_hours")
            return self.json(out)
        if len(parts) == 3 and parts[:2] == ["v1", "satellite"] and parts[2].endswith(".jpg"):
            spot = spots.find(parts[2][:-4])
            if not spot:
                return self.error(404, "unknown_spot", "no such spot; see /v1/spots")
            self.kind, self.spot = "image", spot.id
            if not arrows:
                return self.reply(200, satellite.base(spot), "image/jpeg", cache="max-age=604800")
            data = fusion.forecast(spot, cfg, public=public)
            when = _int(q, "t", int(time.time()), 0, 2 ** 40)
            hour = min((h for h in data["hours"] if h.get("swells")), key=lambda h: abs(h["t"] - when), default=None)
            if not hour:
                return self.reply(200, satellite.base(spot), "image/jpeg")
            caption = f"{spot.name} · {text.when(hour['t'])} · {surf.surf_text(hour['surf_min'], hour['surf_max'])} ft"
            body = portal.remember(("sat", spot.id, hour["t"], public), 600,
                                   lambda: satellite.annotated(spot, hour["swells"], hour.get("wind"), caption))
            return self.reply(200, body, "image/jpeg")
        return self.error(404, "not_found", "no such page; see /llms.txt")

    def overview_stream(self, stored: dict | None, make) -> None:
        """Stream an overview. In public, one already written is free; writing a new one is rationed."""
        portal = self.portal
        if not portal.public:
            return self.events(make())
        if stored:
            if portal.stats:
                portal.stats.overview(written=False)
            return self.events(iter([stored["text"]]))
        if not portal.limiter.stream_open(self.visitor):
            return self.events(_unavailable("the forecaster is busy; the overview will be here in a few minutes"))
        try:
            if not portal.limiter.may_write():
                return self.events(_unavailable("the forecaster is resting; try again in a while"))
            started = time.time()
            self.events(make())
            if portal.stats:
                portal.stats.overview(written=True, seconds=time.time() - started)
        finally:
            portal.limiter.stream_closed(self.visitor)

    def static(self, name: str) -> None:
        if name not in PAGE_FILES:
            return self.error(404, "not_found", "no such file")
        kind = "application/manifest+json" if name.endswith(".webmanifest") else mimetypes.guess_type(name)[0] or "application/octet-stream"
        if kind.startswith("text/") or kind.endswith(("javascript", "json")):
            kind += "; charset=utf-8"
        self.reply(200, (STATIC / name).read_bytes(), kind, cache="max-age=86400" if kind.startswith("image/png") else "no-cache")

    def status_data(self, cfg: config.Config) -> dict:
        data = fusion.forecast(spots.FEATURED[0], cfg, public=self.portal.public)
        if self.portal.public:  # nothing about this Mac, its settings or why a source failed
            return {"version": __version__, "generated": data["generated"], "spots": len(spots.SPOTS),
                    "sources": [{"id": s["id"], "label": s["label"], "ok": s["ok"], "through": s["through"]} for s in data["sources"]]}
        return {"version": __version__, "sources": data["sources"], "off": data["off"],
                "ai": {"url": cfg.llm_url or None}, "spots": len(spots.SPOTS), "generated": data["generated"],
                "edition": {"at": cfg.daily_at, "built": int(daily.built()) or None},
                "surfline": {"waiting": surfline.pending(), "paused": net.blocked(surfline.HOST)}}


def _unavailable(why: str):
    raise ai.Unavailable(why)
    yield  # pragma: no cover  (makes this a generator, so the refusal travels as an event)


def _warm(stop: threading.Event, public: bool) -> None:
    """Keep the featured spots' forecasts fresh so the home page opens at once."""
    while True:
        try:
            fusion.overview(config.load(), public=public)
        except Exception:  # noqa: BLE001
            log.exception("refreshing the featured spots failed")
        if stop.wait(max(config.load().refresh_minutes, 10) * 60):
            return


def hosts_for(listen: str) -> list:
    if listen == "lan":
        return ["0.0.0.0"]
    if listen == "tailnet":
        address = netguard.tailscale_ip()
        if not address:
            raise RuntimeError("Can't find this Mac's Tailscale address. Is Tailscale running and connected?")
        return [address, "127.0.0.1"]
    return ["127.0.0.1"]


def _start(host: str, port: int, handler) -> ThreadingHTTPServer:
    httpd = ThreadingHTTPServer((host, port), handler)
    httpd.daemon_threads = True
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd


def serve(listen: str | None = None, port: int | None = None, public: bool = False, hostnames=(),
          with_stats: bool = True, stats_port: int | None = None) -> None:
    """Runs until SIGTERM / SIGINT (launchd sends SIGTERM)."""
    cfg = config.load()
    stop, servers = threading.Event(), []
    if public:
        names = tuple(hostnames) or tuple(cfg.public_hosts)
        stats = statistics.Stats() if with_stats else None
        portal = Portal(public=True, hosts=names, stats=stats)
        port = port or cfg.public_port
        servers.append(_start("127.0.0.1", port, type("PublicHandler", (Handler,), {"portal": portal})))
        log.info("surfslop --public listening on http://127.0.0.1:%d for the tunnel; answers the names: %s", port,
                 ", ".join(names) or "localhost only (add your domain: --hostname surf.example.com)")
        if stats:
            threading.Thread(target=stats.run, args=(stop,), daemon=True).start()
            address = netguard.tailscale_ip()
            for host in ([address] if address else []) + ["127.0.0.1"]:
                servers.append(_start(host, stats_port or cfg.stats_port, statistics.make_handler(stats)))
                log.info("stats on http://%s:%d (tailnet only)", host, stats_port or cfg.stats_port)
            if not address:
                log.warning("Tailscale isn't running: the stats page is on 127.0.0.1 only")
    else:
        listen, port = listen or cfg.listen, port or cfg.port
        handler = type("PrivateHandler", (Handler,), {"portal": Portal()})
        for host in hosts_for(listen):
            servers.append(_start(host, port, handler))
            log.info("surfslop listening on http://%s:%d", host, port)
        threading.Thread(target=surfline.worker, args=(stop, lambda: config.load().enabled("surfline"), fusion.forget),
                         daemon=True).start()
    threading.Thread(target=_warm, args=(stop, public), daemon=True).start()
    threading.Thread(target=daily.loop, args=(stop, public), daemon=True).start()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())
    stop.wait()
    log.info("stopping")
    for httpd in servers:
        httpd.shutdown()
