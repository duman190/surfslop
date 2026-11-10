"""Several forecasts in, one out.

For a spot, every source becomes an hourly series of wave-face height (lo, hi) at the break:
"spot" sources already are; "ocean" sources' swell trains go through surf.face_height with the
spot's exposure. Then, hour by hour:

  surf     weighted mean of the sources (WEIGHTS), with the sources' disagreement kept as
           `spread`; a day's `confidence` comes from the spread and how many independent
           source families agree.
  swell    the sources' swell trains, matched by direction and averaged with the same weights:
           the main train and, when there is one, a second (for the map's arrows and the chart).
  energy   kJ per metre of crest in one wave of that swell, out at sea and at the break.
  consistency  0-100, our estimate of how steadily rideable waves arrive (surf.consistency).
  wind     weighted circular mean of the spot-level winds → offshore/onshore → clean/fair/blown.
  tide     NOAA's prediction for the region's station.
  weather  air temperature and sky at the spot (Open-Meteo), with sunrise and sunset per day.

Calibration: while Surfline is on, each open-ocean source is compared with it over the hours both
cover, and the median ratio is remembered per spot (calibration.json). That scale is applied to
the source from then on, including days 6-8 where Surfline's free forecast has ended. It corrects
how this portal brings open-ocean swell to the break; surf-forecast.com forecasts the break
itself, so its numbers are left as they are.
"""

from __future__ import annotations

import bisect
import json
import statistics
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from zoneinfo import ZoneInfo

from . import config, obs, surf
from .sources import nws, openmeteo, surfforecast, surfline
from .spots import BY_ID, FEATURED, REGIONS, TZ, Spot, public

WEIGHTS = {"surfline": 3.0, "nws": 1.5, "surfforecast": 0.8, "openmeteo": 0.8}
# Whose swell trains are fused into the open-ocean swell, most trusted first: the sources that
# forecast the open sea. Surfline gives its swell at an inshore point of its own for each spot, and
# surf-forecast.com's is already cut down to what reaches the break, so those two count through
# their surf heights instead and only stand in here when no open-sea source has a swell.
SWELL_SOURCES = ("nws", "openmeteo-ncep", "openmeteo-meteofrance", "openmeteo-ecmwf")
SWELL_STANDINS = ("surfline", "surfforecast")
SAME_TRAIN = 50  # degrees: trains from two sources this close in direction are the same swell
EDITION_KEEP = 36 * 3600
WIND_WEIGHTS = (("surfline", 2.0), ("openmeteo-ncep", 1.0), ("openmeteo-meteofrance", 1.0),
                ("openmeteo-ecmwf", 1.0), ("surfforecast", 0.7))
DAYS = 8
PERIODS = (("morning", 6, 10), ("midday", 10, 14), ("afternoon", 14, 18))
FRESH = 10 * 60
HOUR = 3600

_cache: dict = {}
_locks: dict = {}
_guard = threading.Lock()


class Series:
    """Values over time with gaps: `at` interpolates, `near` takes the closest point."""

    def __init__(self, pairs):
        pairs = sorted(((t, v) for t, v in pairs if v is not None), key=lambda p: p[0])
        self.t = [t for t, _ in pairs]
        self.v = [v for _, v in pairs]

    def __bool__(self):
        return bool(self.t)

    def at(self, t: float, max_gap: float = 12 * HOUR, edge: float = 3 * HOUR):
        if not self.t:
            return None
        i = bisect.bisect_left(self.t, t)
        if i < len(self.t) and self.t[i] == t:
            return self.v[i]
        if i == 0:
            return self.v[0] if self.t[0] - t <= edge else None
        if i == len(self.t):
            return self.v[-1] if t - self.t[-1] <= edge else None
        t0, t1 = self.t[i - 1], self.t[i]
        if t1 - t0 > max_gap:
            return None
        w = (t - t0) / (t1 - t0)
        return tuple(a + (b - a) * w for a, b in zip(self.v[i - 1], self.v[i]))

    def near(self, t: float, within: float = 6 * HOUR):
        if not self.t:
            return None
        i = bisect.bisect_left(self.t, t)
        best = min((j for j in (i - 1, i) if 0 <= j < len(self.t)), key=lambda j: abs(self.t[j] - t))
        return self.v[best] if abs(self.t[best] - t) <= within else None


def gather(spot: Spot, cfg: config.Config, public: bool = False) -> list:
    """Every source's answer for the spot. `public` leaves out the sources whose forecasts are
    theirs to publish, not ours (Surfline, surf-forecast.com): only NWS and Open-Meteo remain."""
    jobs = []
    if not public and cfg.enabled("surfline") and spot.surfline:
        jobs.append(surfline.fetch)
    if not public and cfg.enabled("surfforecast") and spot.sf:
        jobs.append(surfforecast.fetch)
    if cfg.enabled("nws"):
        jobs.append(nws.fetch)
    if cfg.enabled("openmeteo"):
        jobs.append(openmeteo.fetch)
    if not jobs:
        return []
    with ThreadPoolExecutor(max_workers=len(jobs)) as pool:
        return [res for batch in pool.map(lambda fetch: fetch(spot), jobs) for res in batch]


def _raw_surf(spot: Spot, res: dict, point: dict):
    if res["kind"] == "spot":
        return point.get("surf")
    swells = point.get("swells")
    return surf.face_range(surf.face_height(spot.exposure, swells)) if swells else None


# --- calibration against Surfline ------------------------------------------------------------

def _calibration_path():
    return config.home() / "calibration.json"


def load_calibration() -> dict:
    try:
        return json.loads(_calibration_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


_calibration_lock = threading.Lock()


def calibrate(spot_id: str, series: dict, hours: list, stored: dict, skip=()) -> dict:
    """{source: scale} for this spot; updates `stored` in place when Surfline gives new evidence.
    Sources in `skip` (those that forecast the break themselves) are never scaled."""
    series = {sid: s for sid, s in series.items() if sid not in skip}
    scales = {sid: entry["scale"] for sid, entry in stored.get(spot_id, {}).items() if sid not in skip}
    ref = series.get("surfline")
    if not ref:
        return scales
    updates = {}
    for sid, s in series.items():
        if sid == "surfline":
            continue
        ratios = []
        for t, local in hours:
            a, b = ref.at(t), s.at(t)
            if a and b and 6 <= local.hour < 18 and sum(a) > 1 and sum(b) > 1:
                ratios.append(sum(a) / sum(b))
        if len(ratios) < 12:
            continue
        seen = min(max(statistics.median(ratios), 0.4), 2.5)
        old = scales.get(sid)
        scales[sid] = round(seen if old is None else 0.7 * old + 0.3 * seen, 3)
        updates[sid] = {"scale": scales[sid], "hours": len(ratios), "updated": int(time.time())}
    if updates:
        stored.setdefault(spot_id, {}).update(updates)
        with _calibration_lock:  # several spots are fused at once: merge into what is on disk now
            latest = load_calibration()
            latest.setdefault(spot_id, {}).update(updates)
            config.write_private(_calibration_path(), json.dumps(latest, indent=1))
    return scales


# --- the fusion ------------------------------------------------------------------------------

def _grid(now: float) -> list:
    tz = ZoneInfo(TZ)
    start = datetime.fromtimestamp(now, tz).replace(hour=0, minute=0, second=0, microsecond=0)
    t0 = int(start.timestamp())
    return [(t0 + i * HOUR, datetime.fromtimestamp(t0 + i * HOUR, tz)) for i in range(DAYS * 24)]


def _r(x, digits=1):
    return None if x is None else round(x, digits)


def _ranked(spot: Spot, trains) -> list:
    """A source's swell trains, the one that matters most at this spot first."""
    return sorted((s for s in trains if s[0] and s[1]),
                  key=lambda s: -surf.breaking_height(s[0], s[1]) * surf.exposure(spot.exposure, s[2]))[:3]


def fuse_swells(spot: Spot, found: list) -> list:
    """The main swell train and, if there is one, a second, from several sources' trains.
    `found` is [(weight, [(height_ft, period_s, from_deg), …])], most trusted source first. Trains
    are matched across sources by direction, then height and period are weighted means and the
    direction a weighted circular mean."""
    found = [(w, _ranked(spot, trains)) for w, trains in found]
    found = [(w, trains) for w, trains in found if trains]
    used = [set() for _ in found]
    out = []
    for rank in range(2):
        ref = next((trains[j] for (_, trains), u in zip(found, used) for j in range(len(trains)) if j not in u), None)
        if ref is None:
            break
        members = []
        for k, (w, trains) in enumerate(found):
            free = [j for j in range(len(trains)) if j not in used[k]
                    and (ref[2] is None or trains[j][2] is None or surf.angle_between(trains[j][2], ref[2]) <= SAME_TRAIN)]
            if free:
                best = min(free, key=lambda j: surf.angle_between(trains[j][2], ref[2]) if None not in (trains[j][2], ref[2]) else 0)
                used[k].add(best)
                members.append((w, trains[best]))
        total = sum(w for w, _ in members)
        height = sum(w * s[0] for w, s in members) / total
        period = sum(w * s[1] for w, s in members) / total
        direction = surf.mean_direction([(s[2], w) for w, s in members if s[2] is not None])
        if rank and height < 0.5:
            break
        out.append({"height": _r(height), "period": _r(period, 0), "dir": _r(direction, 0), "from": surf.compass(direction)})
    return out


def fuse(spot: Spot, results: list, tides: list, now: float | None = None, stored: dict | None = None,
         weather: dict | None = None) -> dict:
    now = now or time.time()
    weather = weather or {"hours": {}, "sun": []}
    hours = _grid(now)
    ok = [r for r in results if r["points"]]
    raw = {r["id"]: Series((p["t"], _raw_surf(spot, r, p)) for p in r["points"]) for r in ok}
    swells = {r["id"]: Series((p["t"], tuple(p["swells"]) if p.get("swells") else None) for p in r["points"]) for r in ok}
    winds = {r["id"]: Series((p["t"], p.get("wind")) for p in r["points"]) for r in ok}
    scales = calibrate(spot.id, raw, hours, load_calibration() if stored is None else stored,
                       skip={r["id"] for r in ok if r["kind"] == "spot" and r["id"] != "surfline"})
    weights = {r["id"]: WEIGHTS.get(r["group"], 0.5) for r in ok}
    groups = {r["id"]: r["group"] for r in ok}

    hourly, per_source = [], {r["id"]: [] for r in ok}
    for t, local in hours:
        votes = []
        for sid, s in raw.items():
            v = s.at(t)
            if v:
                k = scales.get(sid, 1.0)
                votes.append((weights[sid], v[0] * k, v[1] * k, sid))
            per_source[sid].append(_r((v[0] + v[1]) / 2 * scales.get(sid, 1.0)) if v else None)
        entry = {"t": t, "time": local.strftime("%Y-%m-%dT%H:%M"), "surf_min": None, "surf_max": None, "n": len(votes)}
        if votes:
            total = sum(w for w, *_ in votes)
            lo, hi = sum(w * a for w, a, _, _ in votes) / total, sum(w * b for w, _, b, _ in votes) / total
            mid = (lo + hi) / 2
            var = sum(w * ((a + b) / 2 - mid) ** 2 for w, a, b, _ in votes) / total
            entry.update(surf_min=_r(lo), surf_max=_r(hi), spread=_r(var ** 0.5 / mid, 2) if mid > 0.75 else 0.0,
                         families=len({groups[sid] for *_, sid in votes}))
        found = [(weights[sid], swells[sid].near(t)) for sid in SWELL_SOURCES if sid in swells]
        found = [(w, trains) for w, trains in found if trains]
        if not found:
            found = [(1.0, swells[sid].near(t)) for sid in SWELL_STANDINS if sid in swells and swells[sid].near(t)][:1]
        trains = fuse_swells(spot, found)
        if trains:
            entry["swells"] = trains
            each = [surf.wave_energy(s["height"], s["period"]) for s in trains]
            offshore = sum(each)
            mid = (entry["surf_min"] + entry["surf_max"]) / 2 if votes else None
            # How much of the open-ocean wave arrives: the fused surf against the same swell on an open beach.
            open_face = surf.face_height((1, 1, 1), [(s["height"], s["period"], s["dir"]) for s in trains])
            share = (mid / open_face if mid is not None and open_face > 0
                     else surf.exposure(spot.exposure, trains[0]["dir"]))
            entry["energy"] = {"offshore": int(round(offshore)), "nearshore": int(round(offshore * min(share, 1.5) ** 2))}
            entry["consistency"] = surf.consistency(mid, trains[0]["period"], each[1] / offshore if len(each) > 1 and offshore else 0.0)
        sky = weather["hours"].get(t)
        if sky:
            entry["weather"] = {"temp_f": int(round(sky[0])), "sky": surf.sky(sky[1]), "night": not sky[2]}
        blow = [(w, winds[sid].near(t, within=3 * HOUR)) for sid, w in WIND_WEIGHTS if sid in winds]
        blow = [(w, v) for w, v in blow if v and v[0] is not None and v[2] is not None]
        if blow:
            # The three Open-Meteo entries carry the same spot wind: count it once.
            seen, uniq = set(), []
            for w, v in blow:
                if v not in seen:
                    seen.add(v)
                    uniq.append((w, v))
            total = sum(w for w, _ in uniq)
            speed = sum(w * v[0] for w, v in uniq) / total
            direction = surf.mean_direction([(v[2], w * max(v[0], 1)) for w, v in uniq])
            gusts = [v[1] for _, v in uniq if v[1] is not None]
            entry["wind"] = {"speed": _r(speed, 0), "gust": _r(max(gusts), 0) if gusts else None, "dir": _r(direction, 0),
                             "from": surf.compass(direction), "type": surf.wind_type(direction, speed, spot.offshore)}
            entry["quality"] = surf.quality(direction, speed, spot.offshore)
        entry["tide"] = _r(obs.tide_at(tides, t))
        hourly.append(entry)

    return {
        "spot": public(spot), "generated": int(now), "timezone": TZ,
        "sources": _source_status(results, scales, weights),
        "days": _days(spot, hours, hourly, ok, raw, scales, tides, weather["sun"]),
        "hours": hourly,
        "source_hours": per_source,
        "writeups": [{"source": r["label"], "day": w["day"], "text": w["text"]} for r in ok for w in r.get("writeups", [])],
    }


def _source_status(results: list, scales: dict, weights: dict) -> list:
    tz = ZoneInfo(TZ)
    out = []
    for r in results:
        last = r["points"][-1]["t"] if r["points"] else None
        out.append({"id": r["id"], "group": r["group"], "label": r["label"], "url": r["url"], "ok": bool(r["points"]),
                    "error": r["error"], "fetched": int(r["fetched"]) if r["fetched"] else None, "kind": r["kind"],
                    "through": datetime.fromtimestamp(last, tz).strftime("%Y-%m-%d") if last else None,
                    "weight": weights.get(r["id"]), "scale": scales.get(r["id"]), "pending": bool(r.get("pending"))})
    return out


def _mean(values):
    values = [v for v in values if v is not None]
    return sum(values) / len(values) if values else None


def _window(spot: Spot, hs: list) -> dict | None:
    """One stretch of a day (a few hourly entries) → its surf, wind, quality and stars."""
    hs = [h for h in hs if h["surf_min"] is not None]
    if not hs:
        return None
    lo, hi = _mean(h["surf_min"] for h in hs), _mean(h["surf_max"] for h in hs)
    out = {"surf_min": _r(lo), "surf_max": _r(hi), "surf": surf.surf_text(lo, hi), "caution": surf.caution(lo, hi),
           "quality": "", "wind": None}
    blow = [h["wind"] for h in hs if h.get("wind")]
    if blow:
        speed = _mean(w["speed"] for w in blow)
        direction = surf.mean_direction([(w["dir"], max(w["speed"], 1)) for w in blow])
        out["wind"] = {"speed": _r(speed, 0), "dir": _r(direction, 0), "from": surf.compass(direction),
                       "type": surf.wind_type(direction, speed, spot.offshore)}
        out["quality"] = surf.quality(direction, speed, spot.offshore)
    period = _mean(h["swells"][0]["period"] for h in hs if h.get("swells"))
    out["stars"] = surf.rate(lo, hi, period, out["quality"])
    return out


def _clock(t: float) -> str:
    return datetime.fromtimestamp(t, ZoneInfo(TZ)).strftime("%-I:%M%p").lower()


def _days(spot: Spot, hours: list, hourly: list, ok: list, raw: dict, scales: dict, tides: list, sun=()) -> list:
    tz = ZoneInfo(TZ)
    suns = {datetime.fromtimestamp(rise, tz).strftime("%Y-%m-%d"): (rise, down) for rise, down in sun}
    by_date: dict = {}
    for (t, local), entry in zip(hours, hourly):
        by_date.setdefault(local.strftime("%Y-%m-%d"), []).append((local, entry))
    days = []
    for date, rows in by_date.items():
        light = [e for local, e in rows if 6 <= local.hour < 18]
        whole = _window(spot, light)
        if not whole or sum(e["surf_min"] is not None for e in light) < 4:
            continue
        local0 = rows[0][0]
        day = {"date": date, "dow": local0.strftime("%a"), "label": local0.strftime("%a %b %-d"), **whole}
        day["periods"] = {name: _window(spot, [e for local, e in rows if a <= local.hour < b]) for name, a, b in PERIODS}
        best = max((p for p in day["periods"].items() if p[1]), key=lambda p: p[1]["stars"], default=None)
        day["best"] = best[0] if best else None
        marks = Counter(p["quality"] for p in day["periods"].values() if p and p["quality"])
        if marks:  # the day's surface: what most of the day is like, the worse one on a tie
            day["quality"] = max(marks, key=lambda q: (marks[q], ("clean", "fair", "blown").index(q)))
        day["stars"] = best[1]["stars"] if best else whole["stars"]
        swells = Counter()
        for e in light:
            if e.get("swells"):
                s = e["swells"][0]
                swells[(round(s["height"]), s["period"], s["from"])] += 1
        if swells:
            (h, p, d), _ = swells.most_common(1)[0]
            day["swell"] = f"{h} ft @ {int(p)} s {d}"
        spread = _mean(e.get("spread") for e in light) or 0.0
        families = round(_mean(e.get("families") for e in light) or 0)
        day["spread"], day["families"] = _r(spread, 2), families
        day["confidence"] = ("high" if families >= 3 and spread < 0.18 else
                             "medium" if families >= 2 and spread < 0.35 else "low")
        day["by_source"] = {}
        start, end = rows[0][1]["t"] + 6 * HOUR, rows[0][1]["t"] + 18 * HOUR
        for r in ok:
            values = [raw[r["id"]].at(e["t"]) for e in light]
            values = [v for v in values if v]
            if not values:
                continue
            k = scales.get(r["id"], 1.0)
            lo, hi = _mean(v[0] for v in values) * k, _mean(v[1] for v in values) * k
            ratings = Counter(p["rating"] for p in r["points"] if start <= p["t"] < end and p.get("rating"))
            day["by_source"][r["id"]] = {"surf": surf.surf_text(lo, hi), "surf_min": _r(lo), "surf_max": _r(hi),
                                         "rating": ratings.most_common(1)[0][0] if ratings else None,
                                         "note": r["notes"].get(date)}
        day["tides"] = [{"t": t, "time": _clock(t), "type": "high" if kind == "H" else "low", "height": _r(h)}
                        for t, h, kind in tides if rows[0][1]["t"] <= t < rows[0][1]["t"] + 24 * HOUR]
        energy = _mean((e.get("energy") or {}).get("nearshore") for e in light)
        day["energy"] = int(round(energy)) if energy is not None else None
        steady = _mean(e.get("consistency") for e in light)
        day["consistency"] = int(round(steady)) if steady is not None else None
        temps = [e["weather"]["temp_f"] for _, e in rows if e.get("weather")]
        skies = Counter(e["weather"]["sky"] for e in light if e.get("weather"))
        day["weather"] = {"low": min(temps), "high": max(temps), "sky": skies.most_common(1)[0][0] if skies else ""} if temps else None
        rise, down = suns.get(date, (None, None))
        day["sun"] = {"sunrise": _clock(rise), "sunset": _clock(down), "sunrise_t": rise, "sunset_t": down} if rise else None
        days.append(day)
    return days


# --- what the server calls -------------------------------------------------------------------

def forecast(spot: Spot, cfg: config.Config | None = None, refresh: bool = False, public: bool = False) -> dict:
    """The spot's fused forecast, at most FRESH seconds old (the sources have their own caches).
    The public edition is built and kept apart from the private one."""
    cfg = cfg or config.load()
    key = (spot.id, public)
    with _guard:
        lock = _locks.setdefault(key, threading.Lock())
    with lock:
        hit = _cache.get(key)
        if hit and not refresh and hit[0] > time.time() and hit[2] == tuple(cfg.disabled):
            return hit[1]
        region = REGIONS[spot.region]
        tides = obs.tide_extremes(region.tide_station)
        data = fuse(spot, gather(spot, cfg, public), tides, weather=openmeteo.spot_weather(spot))
        if not data["days"]:  # every source is unreachable: fall back on this morning's edition
            saved = load_edition(spot, public)
            if saved:
                return saved
        data["tide_station"] = region.tide_name
        data["buoys"] = [b for b in (obs.buoy(s) for s in region.buoys) if b]
        data["off"] = [] if public else [s for s in config.SOURCES if not cfg.enabled(s)]
        data["now"] = next((h for h in data["hours"] if h["t"] + HOUR > time.time() and h["surf_min"] is not None), None)
        data["public"] = public
        if public:
            for source in data["sources"]:
                source["scale"] = None  # how the open sources were tuned stays private
        _cache[key] = (time.time() + FRESH, data, tuple(cfg.disabled))
        return data


def _edition_path(spot: Spot, public: bool):
    return config.cache_dir("edition") / f"{'pub-' if public else ''}{spot.id}.json"


def save_edition(data: dict) -> None:
    """Keep a spot's whole fused forecast, hour by hour, as the daily edition built it."""
    spot = BY_ID[data["spot"]["id"]]
    config.write_private(_edition_path(spot, bool(data.get("public"))), json.dumps({**data, "edition": int(time.time())}))


def load_edition(spot: Spot, public: bool = False) -> dict | None:
    """The spot's forecast from the last daily edition, if it is recent enough to show."""
    try:
        data = json.loads(_edition_path(spot, public).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if time.time() - data.get("edition", 0) < EDITION_KEEP else None


def forget(spot: Spot) -> None:
    """Drop the spot's fused forecast, so the next request rebuilds it from what is on disk now."""
    _cache.pop((spot.id, False), None)


def overview(cfg: config.Config | None = None, spots=FEATURED, days: int = 3, public: bool = False) -> dict:
    """The home page preview: each spot's next few days, without the hourly detail."""
    cfg = cfg or config.load()
    with ThreadPoolExecutor(max_workers=4) as pool:
        full = list(pool.map(lambda s: forecast(s, cfg, public=public), spots))
    rows = []
    for data in full:
        rows.append({"spot": data["spot"], "now": data["now"],
                     "days": [{k: d.get(k) for k in ("date", "dow", "label", "surf", "surf_min", "surf_max", "caution", "quality",
                                                     "stars", "best", "confidence", "swell", "periods")}
                              for d in data["days"][:days]]})
    return {"generated": int(time.time()), "timezone": TZ, "spots": rows, "public": public,
            "sources": full[0]["sources"] if full else [], "off": full[0]["off"] if full else []}

