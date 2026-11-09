"""surf-forecast.com: the break's own 7-day table (morning, afternoon, night).

Its headline swell is the one it judges to reach the break (others are marked sheltered), so this
is a per-break opinion, not open-ocean data. One page per break, at most every 3 hours; its
robots.txt allows /breaks/*/forecasts/* for every client.
"""

from __future__ import annotations

import html as htmllib
import re
from datetime import datetime
from zoneinfo import ZoneInfo

from .. import net, surf
from ..spots import TZ, Spot
from . import KMH_TO_KT, M_TO_FT, guarded, result

TTL = 3 * 3600
# A fully open beach breaks at roughly this share of the textbook breaker height (see surf.py).
OPEN_BEACH = 0.75
_ROW = r'<tr class="forecast-table__row" data-row="%s">(.*?)</tr>'


def _cells(html: str, row: str) -> list:
    m = re.search(_ROW % re.escape(row), html, re.S)
    return re.findall(r"<td\b.*?</td>", m.group(1), re.S) if m else []


def _num(pattern: str, text: str) -> float | None:
    m = re.search(pattern, text)
    return float(m.group(1)) if m else None


def parse(html: str) -> list:
    """The table's columns as points (no "t" yet: "date" and "hour" in local time)."""
    columns = []
    for cell in _cells(html, "days"):
        date = re.search(r'data-date="(\d{4}-\d\d-\d\d)"', cell)
        span = _num(r'colspan="(\d+)"', cell) or 1
        columns += [date.group(1) if date else None] * int(span)
    hours = [_num(r'data-hour="(\d+)"', c) for c in _cells(html, "time")]
    swell, wind = _cells(html, "swell"), _cells(html, "wind")
    rating, state = _cells(html, "rating"), _cells(html, "wind-state")
    points = []
    for i in range(min(len(columns), len(hours), len(swell))):
        height = _num(r'data-height="([\d.]+)"', swell[i])
        period = _num(r'forecast-table__swell-period">\s*([\d.]+)', swell[i])
        turn = _num(r'swell-icon__arrow"[^>]*rotate\(([\d.\-]+)\)', swell[i])
        if columns[i] is None or hours[i] is None or height is None or not period:
            continue
        point = {"date": columns[i], "hour": int(hours[i]),
                 "swells": [(height * M_TO_FT, period, (turn + 180) % 360 if turn is not None else None)]}
        if i < len(wind):
            speed = _num(r'data-speed="([\d.]+)"', wind[i])
            turn = _num(r'wind-icon__arrow"[^>]*rotate\(([\d.\-]+)\)', wind[i])
            if speed is not None and turn is not None:
                point["wind"] = (speed * KMH_TO_KT, None, (turn + 180) % 360)
        if i < len(rating):
            stars = _num(r'star-rating__rating[^>]*>\s*(\d+)\s*<', rating[i])
            point["rating"] = f"{int(stars)}/10" if stars is not None else None
        if i < len(state):
            point["wind_state"] = re.sub(r"<[^>]+>", "", state[i]).strip()
        points.append(point)
    return points


def _plain(markup: str) -> str:
    return re.sub(r"\s+", " ", htmllib.unescape(re.sub(r"<[^>]+>", " ", markup))).strip()


def _imperial(words: str) -> str:
    words = re.sub(r"(\d+(?:\.\d+)?)\s?m\b", lambda m: f"{float(m.group(1)) * M_TO_FT:.0f} ft", words)
    return re.sub(r"(-?\d+)\s?°C", lambda m: f"{int(m.group(1)) * 9 / 5 + 32:.0f}°F", words)


def summary(html: str) -> list:
    """The page's own sentences about the days ahead: the table's weather summaries and the two
    paragraphs on the next swell and the biggest one. Heights converted to feet."""
    lines = []
    for cell in _cells(html, "summary"):
        words = _plain(cell)
        if len(words) > 20:
            lines.append(words)
    body = re.sub(r"(?s)<(script|style)\b.*?</\1>", " ", html)
    for para in re.findall(r"(?s)<p\b[^>]*>(.*?)</p>", body):
        words = _plain(para)
        if words.startswith(("The most powerful waves expected", "The surf forecast for")) and len(words) < 700:
            lines.append(words)
    return [_imperial(line) for line in lines[:4]]


@guarded
def fetch(spot: Spot) -> list:
    url = f"https://www.surf-forecast.com/breaks/{spot.sf}/forecasts/latest/six_day"
    res = result("surfforecast", "surf-forecast.com", url, "spot")
    body, res["fetched"] = net.get(url, ttl=TTL)
    page = body.decode("utf-8", "replace")
    res["writeups"] = [{"day": None, "text": line} for line in summary(page)]
    tz = ZoneInfo(TZ)
    for p in parse(page):
        when = datetime.strptime(p.pop("date"), "%Y-%m-%d").replace(hour=p.pop("hour"), tzinfo=tz)
        p["t"] = int(when.timestamp())
        height, period, _ = p["swells"][0]
        p["surf"] = surf.face_range(surf.breaking_height(height, period) * OPEN_BEACH)
        res["points"].append(p)
    if not res["points"]:
        res["error"] = "couldn't find the forecast table on the page"
    return [res]
