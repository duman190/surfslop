"""Surfline: its spot forecast (LOTUS model, plus the forecaster's daily note).

OFF until you turn it on (`surfslop source surfline on`); read README.md, "Surfline", first.

What this does and doesn't do:
* It reads the same forecast feed surfline.com shows a visitor who isn't signed in: five days,
  no account, no password, no token. Nothing is sent but the spot id.
* It never asks for more days than a signed-out visitor gets and never signs in.
* It identifies itself as surfslop (net.py) and never pretends to be a browser.
* It is slow on purpose. Pages never wait on Surfline: `fetch` only reads what is already on
  disk and puts the spot in a queue. One background worker (in the running portal) reads the
  queue: one spot at a time, PAUSE seconds between its five small requests, SPOT_GAP between
  spots. A spot is read once a day: the home page's spots by the daily edition (daily.py, 4 AM),
  any other spot when someone first opens it, and then not again for TTL.
* It only ever runs in the private portal. `--public` neither reads nor shows Surfline.
* Surfline's site turns automated clients away at times. When it answers 401/403/429, net.py
  leaves it alone for 6 hours; nothing here retries sooner, faster or as a different client.
  The last copy on disk stays in use for up to a day and says how old it is.
"""

from __future__ import annotations

import html as htmllib
import json
import logging
import re
import threading
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from .. import net
from ..spots import TZ, Spot
from . import KMH_TO_KT, M_TO_FT, failed, result

log = logging.getLogger("surfslop")
HOST = "services.surfline.com"
API = f"https://{HOST}/kbyg/spots/forecasts"
FREE_DAYS = 5
TTL = 23 * 3600        # a spot is read once a day
KEEP = 36 * 3600       # a copy stays in use this long, so one refused morning doesn't blank the day
FORCE_AGE = 6 * 3600   # the daily edition re-reads a copy older than this
PAUSE = 10       # seconds between two requests
SPOT_GAP = 180   # seconds between two spots
# Most useful first, in case Surfline stops answering part-way through a spot.
KINDS = (("surf", True), ("conditions", False), ("rating", True), ("wind", True), ("swells", True))
WRITEUP_CHARS = 900
_LENGTH = {"FT": 1.0, "M": M_TO_FT}
_SPEED = {"KTS": 1.0, "KPH": KMH_TO_KT, "MPH": 0.868976}
_queue: list = []
_forced: set = set()
_busy: list = []  # the spot being read right now
_queue_lock = threading.Lock()


def page_url(spot: Spot) -> str:
    return f"https://www.surfline.com/surf-report/{spot.id}/{spot.surfline}"


def _url(kind: str, spot: Spot, hourly: bool) -> str:
    return f"{API}/{kind}?spotId={spot.surfline}&days={FREE_DAYS}" + ("&intervalHours=1" if hourly else "")


def normalize(surf: dict, swells: dict, wind: dict, rating: dict, conditions: dict, units: dict) -> tuple:
    """(points, notes, writeups) from the five responses' "data" blocks. Pure, so tests can feed it
    samples. `notes` is the forecaster's one-line headline per day; `writeups` adds what they wrote
    under it, as plain text, for the local model."""
    length = _LENGTH.get(units.get("waveHeight", "FT"), 1.0)
    swell_length = _LENGTH.get(units.get("swellHeight", "FT"), 1.0)
    speed = _SPEED.get(units.get("windSpeed", "KTS"), 1.0)
    points = {}
    for row in surf.get("surf") or []:
        s = row.get("surf") or {}
        raw = s.get("raw") or {}
        lo, hi = raw.get("min", s.get("min")), raw.get("max", s.get("max"))
        if lo is None or hi is None:
            continue
        points[row["timestamp"]] = {"t": row["timestamp"], "surf": (lo * length, hi * length)}
    for row in swells.get("swells") or []:
        if row["timestamp"] in points:
            trains = sorted((s for s in row.get("swells") or [] if s.get("height")), key=lambda s: -(s.get("impact") or 0))
            points[row["timestamp"]]["swells"] = [(s["height"] * swell_length, s["period"], s["direction"]) for s in trains[:3]]
    for row in wind.get("wind") or []:
        if row["timestamp"] in points and row.get("speed") is not None:
            gust = row.get("gust")
            points[row["timestamp"]]["wind"] = (row["speed"] * speed, gust * speed if gust is not None else None,
                                                row.get("direction"))
    for row in rating.get("rating") or []:  # null for spots Surfline doesn't rate (Mavericks)
        if row["timestamp"] in points:
            points[row["timestamp"]]["rating"] = ((row.get("rating") or {}).get("key") or "").replace("_", " ").title() or None
    notes, writeups = {}, []
    tz = ZoneInfo(TZ)
    for row in conditions.get("conditions") or []:
        day = row.get("forecastDay") or datetime.fromtimestamp(row["timestamp"], tz).strftime("%Y-%m-%d")
        headline = (row.get("headline") or "").strip()
        if headline:
            notes[day] = headline
        words = re.sub(r"\s+", " ", htmllib.unescape(re.sub(r"<[^>]+>", " ", row.get("observation") or ""))).strip()
        if headline or words:
            writeups.append({"day": day, "text": f"{headline} {words}".strip()[:WRITEUP_CHARS]})
    return [points[t] for t in sorted(points)], notes, writeups


def request(spot: Spot, force: bool = False) -> None:
    """Ask the worker to read this spot when its turn comes; `force` re-reads a copy made earlier today."""
    with _queue_lock:
        if spot not in _queue:
            _queue.append(spot)
        if force:
            _forced.add(spot.id)


def pending() -> int:
    """Spots still waiting or being read."""
    with _queue_lock:
        return len(_queue) + len(_busy)


def fetch(spot: Spot) -> list:
    """What is on disk for the spot. Never touches the network; queues the spot when it is due."""
    res = result("surfline", "Surfline", page_url(spot), "spot")
    data, units, due = {}, {}, False
    for kind, hourly in KINDS:
        hit = net.cached(_url(kind, spot, hourly), max_age=KEEP)
        due = due or not hit or time.time() - hit[1] > TTL
        try:
            body = json.loads(hit[0]) if hit else {}
        except ValueError:
            body = {}
        data[kind] = body.get("data") or {}
        units.update((body.get("associated") or {}).get("units") or {})
        if kind == "surf" and hit:
            res["fetched"] = hit[1]
    if due:
        request(spot)
    if not res["fetched"]:
        until = net.blocked_until(HOST)
        if until:  # say plainly that this is an old refusal being honoured, not a new one on every page view
            again = time.strftime("%a %-I:%M %p", time.localtime(until))
            return [failed(res, f"not read yet for this spot. Surfline turned an earlier request away (HTTP 403), so "
                                f"surfslop is waiting and will try again after {again}")]
        res["pending"] = True  # the page looks again in a minute or two
        return [failed(res, "being read now: Surfline is read slowly in the background, give it a minute or two")]
    try:
        res["points"], res["notes"], res["writeups"] = normalize(data["surf"], data["swells"], data["wind"], data["rating"],
                                                                  data["conditions"], units)
    except Exception as e:  # noqa: BLE001  a change in Surfline's format must not take the page down
        log.exception("couldn't read Surfline's answer for %s", spot.name)
        return [failed(res, f"couldn't read Surfline's answer ({type(e).__name__})")]
    if not res["points"]:
        res["error"] = "no forecast in Surfline's answer"
    return [res]


def refresh(spot: Spot, pause: float | None = None, force: bool = False) -> int:
    """Read the spot's five responses, unhurried, and return how many had to be asked for.
    Raises net.FetchError at the first refusal."""
    pause = PAUSE if pause is None else pause
    fresh = FORCE_AGE if force else TTL
    asked = 0
    for kind, hourly in KINDS:
        url = _url(kind, spot, hourly)
        if net.cached(url, max_age=fresh):
            continue
        net.get(url, ttl=fresh, headers={"Accept": "application/json"}, stale_ok=False)
        asked += 1
        time.sleep(pause)
    return asked


def worker(stop: threading.Event, enabled, on_read=None) -> None:
    """The one place Surfline is asked for anything. Runs in the portal for as long as it is up.
    `on_read(spot)` is called after a spot has been read, so its forecast can be rebuilt."""
    while not stop.is_set():
        with _queue_lock:
            spot = _queue.pop(0) if _queue and enabled() and not net.blocked(HOST) else None
            force = spot is not None and spot.id in _forced
            if spot is not None:
                _forced.discard(spot.id)
                _busy.append(spot)
        if spot is None:
            stop.wait(30)
            continue
        asked = 1
        try:
            asked = refresh(spot, force=force)
            if asked:
                log.info("Surfline: read %s", spot.name)
                if on_read:
                    on_read(spot)
        except net.FetchError as e:
            log.warning("Surfline: %s", e)
        finally:
            with _queue_lock:
                _busy.clear()
        stop.wait(SPOT_GAP if asked else 0.1)  # nothing was asked: no need to space the next spot
