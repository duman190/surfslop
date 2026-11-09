"""Forecast sources. Each one turns its own format into the same shape:

    {"id": "nws", "group": "nws", "label": "NWS marine forecast", "url": "https://…",
     "kind": "ocean" | "spot", "fetched": epoch, "error": None,
     "notes": {"2026-11-09": "one line, shown on the page"},
     "writeups": [{"day": "2026-11-09" | None, "text": "the source's own written forecast"}],
     "points": [{"t": epoch,
                 "swells": [(height_ft, period_s, from_deg), …],   # ocean sources: open-ocean swell trains
                 "surf": (lo_ft, hi_ft),                            # spot sources: wave face at the break
                 "wind": (speed_kt, gust_kt, from_deg),
                 "rating": "FAIR"}]}

"ocean" sources forecast the open sea; fusion.py brings their swell to each break. "spot" sources
already forecast the break. A source that fails returns its error and no points; nothing raises.
"writeups" are the source's own words. They are read by the local model, which writes the overview
in its own words; they are not shown on the page or sent to agents.
"""

from __future__ import annotations

import time

from .. import net

M_TO_FT = 3.28084
KMH_TO_KT = 0.539957


def result(id: str, label: str, url: str, kind: str, *, group: str | None = None) -> dict:
    return {"id": id, "group": group or id, "label": label, "url": url, "kind": kind,
            "fetched": None, "error": None, "notes": {}, "writeups": [], "points": []}


def failed(res: dict, error: Exception | str) -> dict:
    res["error"] = str(error)
    res["points"] = []
    return res


def guarded(fn):
    """Run a fetcher; turn anything that goes wrong into the source's error."""
    def run(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except net.FetchError as e:
            return [failed(result(fn.__module__.rsplit(".", 1)[-1], fn.__module__.rsplit(".", 1)[-1], "", "ocean"), e)]
        except Exception as e:  # noqa: BLE001  a format change must not take the page down
            name = fn.__module__.rsplit(".", 1)[-1]
            return [failed(result(name, name, "", "ocean"), f"couldn't read {name}'s data ({type(e).__name__}: {e})")]
    return run


def now() -> float:
    return time.time()
