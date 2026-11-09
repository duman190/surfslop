"""What is measured rather than forecast: NOAA buoys (NDBC) and tide predictions (CO-OPS)."""

from __future__ import annotations

import bisect
import math
import time
from datetime import datetime, timedelta, timezone

from . import net
from .spots import BUOYS
from .sources import M_TO_FT

MS_TO_KT = 1.94384


def _f(value: str) -> float | None:
    try:
        return None if value in ("MM", "N/A") else float(value)
    except ValueError:
        return None


def buoy(station: str) -> dict | None:
    """The buoy's latest wave reading (they report every 30-60 minutes), or None."""
    try:
        body, _ = net.get(f"https://www.ndbc.noaa.gov/data/realtime2/{station}.txt", ttl=20 * 60)
    except net.FetchError:
        return None
    lines = body.decode("ascii", "replace").splitlines()
    if not lines or not lines[0].startswith("#"):
        return None
    cols = lines[0].lstrip("#").split()
    for line in lines[2:40]:
        row = dict(zip(cols, line.split()))
        height, period = _f(row.get("WVHT", "MM")), _f(row.get("DPD", "MM"))
        if height is None or period is None:
            continue
        try:
            when = datetime(int(row["YY"]), int(row["MM"]), int(row["DD"]), int(row["hh"]), int(row["mm"]),
                            tzinfo=timezone.utc)
        except (KeyError, ValueError):
            continue
        if time.time() - when.timestamp() > 6 * 3600:
            return None  # stale: the buoy is offline or adrift
        wind, water = _f(row.get("WSPD", "MM")), _f(row.get("WTMP", "MM"))
        return {
            "station": station, "name": BUOYS.get(station, station), "t": int(when.timestamp()),
            "height_ft": round(height * M_TO_FT, 1), "period_s": period, "direction": _f(row.get("MWD", "MM")),
            "wind_kt": round(wind * MS_TO_KT) if wind is not None else None, "wind_dir": _f(row.get("WDIR", "MM")),
            "water_f": round(water * 9 / 5 + 32) if water is not None else None,
            "url": f"https://www.ndbc.noaa.gov/station_page.php?station={station}",
        }
    return None


def tide_extremes(station: str) -> list:
    """[(epoch, height_ft, "H" | "L")] from yesterday through the next 9 days; [] when unavailable."""
    start = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y%m%d")
    url = ("https://api.tidesandcurrents.noaa.gov/api/prod/datagetter?product=predictions&application=surfslop"
           f"&begin_date={start}&range=264&datum=MLLW&station={station}&time_zone=gmt&units=english"
           "&interval=hilo&format=json")
    try:
        data, _ = net.get_json(url, ttl=12 * 3600)
    except net.FetchError:
        return []
    out = []
    for p in data.get("predictions", []):
        when = datetime.strptime(p["t"], "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)
        out.append((int(when.timestamp()), float(p["v"]), p["type"]))
    return out


def tide_at(extremes: list, t: float) -> float | None:
    """Tide height at a time: a half cosine between the high and low on either side."""
    times = [e[0] for e in extremes]
    i = bisect.bisect_right(times, t)
    if i == 0 or i == len(extremes):
        return None
    (t0, h0, _), (t1, h1, _) = extremes[i - 1], extremes[i]
    phase = (t - t0) / (t1 - t0)
    return h0 + (h1 - h0) * (1 - math.cos(math.pi * phase)) / 2
