"""The daily edition: every morning the home page's spots are made ready before anyone asks.

At `daily_at` (04:00 Pacific by default) the running portal, for each of the seven featured spots:

  1. asks the Surfline reader for today's copy (private portal only; ten seconds between
     requests, three minutes between spots, so about half an hour for the seven) and waits for it;
  2. refreshes the open sources and builds the fused week;
  3. has the local model write the spot's overview, then the regional "where to go".

Everything lands in the ordinary caches, so the first page view of the day, and Muse's Friday
job, read finished work. Overviews then stand until the next edition (ai.py).

If the portal was not running at 4, the edition is built when it starts. A spot that is not on
the home page, or whose morning read failed, is not left out for the day: opening it queues a
Surfline read and writes its overview on the spot, as before.

The public portal (`--public`) builds its own edition from the open sources only.
"""

from __future__ import annotations

import json
import logging
import threading
import time

from . import ai, config, fusion, net
from .sources import surfline
from .spots import FEATURED

log = logging.getLogger("surfslop")
SURFLINE_WAIT = 75 * 60  # give the slow reader this long before fusing without it


def _state_path():
    return config.cache_dir() / "daily.json"


def built(public: bool = False) -> float:
    """When the last edition of this kind was finished (0 if never)."""
    try:
        return json.loads(_state_path().read_text(encoding="utf-8")).get("public" if public else "private", 0)
    except (OSError, ValueError):
        return 0


def _mark(public: bool) -> None:
    try:
        state = json.loads(_state_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        state = {}
    state["public" if public else "private"] = time.time()
    config.write_private(_state_path(), json.dumps(state))


def due(cfg: config.Config, public: bool = False, now: float | None = None) -> bool:
    return built(public) < config.edition_start(cfg.daily_at, now)


def run(cfg: config.Config, public: bool = False, stop: threading.Event | None = None) -> dict:
    """Build the edition now. Returns what was done, for the log and the tests."""
    stop = stop or threading.Event()
    started, done = time.time(), {"surfline": 0, "spots": 0, "overviews": 0}
    if not public and cfg.enabled("surfline"):
        for spot in FEATURED:
            surfline.request(spot, force=True)
        deadline = time.time() + SURFLINE_WAIT
        while surfline.pending() and time.time() < deadline and not net.blocked(surfline.HOST) and not stop.is_set():
            stop.wait(5)
        done["surfline"] = sum(1 for s in FEATURED if (hit := net.cached(surfline._url("surf", s, True))) and hit[1] >= started - surfline.FORCE_AGE)
    for spot in FEATURED:
        if stop.is_set():
            return done
        data = fusion.forecast(spot, cfg, refresh=True, public=public)
        done["spots"] += 1
        if ai.write(ai.spot_stream(data, cfg)):
            done["overviews"] += 1
    if ai.write(ai.region_stream(fusion.overview(cfg, public=public), cfg)):
        done["overviews"] += 1
    _mark(public)
    log.info("daily edition%s built in %.0f min: %d spots, %d overviews, Surfline for %d",
             " (public)" if public else "", (time.time() - started) / 60, done["spots"], done["overviews"], done["surfline"])
    return done


def loop(stop: threading.Event, public: bool = False) -> None:
    """Runs in the portal: builds the edition when one is due, looking every few minutes (a laptop
    that slept through 4 AM builds it when it wakes)."""
    while not stop.is_set():
        try:
            cfg = config.load()
            if due(cfg, public):
                run(cfg, public, stop)
        except Exception:  # noqa: BLE001
            log.exception("building the daily edition failed; trying again in a while")
            stop.wait(15 * 60)
        stop.wait(5 * 60)
