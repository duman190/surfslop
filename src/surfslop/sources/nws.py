"""National Weather Service: the Bay Area office's marine forecast grid (api.weather.gov).

Forecasters edit these grids by hand, so this is the one open source with a human in the loop.
Read at each region's offshore point: wave height, period, primary and secondary swell.
"""

from __future__ import annotations

import re
from datetime import datetime

from .. import net
from ..spots import REGIONS, Spot
from . import M_TO_FT, guarded, result

TTL = 3600
_DURATION = re.compile(r"P(?:(\d+)D)?(?:T(?:(\d+)H)?)?")


def _hourly(prop: dict | None, scale: float = 1.0) -> dict:
    """{epoch: value}: each value repeated for every hour of its validTime span."""
    out = {}
    for item in (prop or {}).get("values", []):
        if item.get("value") is None:
            continue
        start, _, span = item["validTime"].partition("/")
        m = _DURATION.fullmatch(span or "PT1H")
        hours = (int(m.group(1) or 0) * 24 + int(m.group(2) or 0)) if m else 1
        t0 = int(datetime.fromisoformat(start).timestamp())
        for h in range(max(hours, 1)):
            out[t0 + h * 3600] = item["value"] * scale
    return out


@guarded
def fetch(spot: Spot) -> list:
    lat, lon = REGIONS[spot.region].offshore_point
    res = result("nws", "NWS marine forecast", f"https://marine.weather.gov/MapClick.php?lat={lat}&lon={lon}", "ocean")
    point, _ = net.get_json(f"https://api.weather.gov/points/{lat},{lon}", ttl=30 * 86400,
                            headers={"Accept": "application/geo+json"})
    grid, res["fetched"] = net.get_json(point["properties"]["forecastGridData"], ttl=TTL,
                                        headers={"Accept": "application/geo+json"})
    p = grid["properties"]
    height, period = _hourly(p.get("primarySwellHeight"), M_TO_FT), _hourly(p.get("wavePeriod"))
    direction = _hourly(p.get("primarySwellDirection"))
    height2, period2 = _hourly(p.get("secondarySwellHeight"), M_TO_FT), _hourly(p.get("wavePeriod2"))
    direction2 = _hourly(p.get("secondarySwellDirection"))
    total = _hourly(p.get("waveHeight"), M_TO_FT)
    last_dir = None
    for t in sorted(set(height) | set(total)):
        h = height.get(t) or total.get(t)
        last_dir = direction.get(t, last_dir)
        if not h or not period.get(t):
            continue
        swells = [(h, period[t], last_dir)]
        if height2.get(t, 0) >= 0.5 and period2.get(t):
            swells.append((height2[t], period2[t], direction2.get(t)))
        res["points"].append({"t": t, "swells": swells})
    if not res["points"]:
        res["error"] = "the forecast grid has no wave data right now"
    return [res]
