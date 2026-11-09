"""The forecast as plain text: Markdown for agents, and the data block the local model reads."""

from __future__ import annotations

import time
from datetime import datetime
from zoneinfo import ZoneInfo

from . import surf
from .spots import TZ

SHORT = {"surfline": "Surfline", "surfforecast": "surf-forecast", "nws": "NWS", "openmeteo-ncep": "GFS-Wave",
         "openmeteo-ecmwf": "ECMWF", "openmeteo-meteofrance": "MFWAM"}
OFF_NOTE = {"surfline": "Surfline is switched off (on the Mac: `surfslop source surfline on`)."}


def when(t: float) -> str:
    return datetime.fromtimestamp(t, ZoneInfo(TZ)).strftime("%a %b %-d, %-I:%M %p %Z")


def _ago(t: float) -> str:
    minutes = max(int((time.time() - t) / 60), 0)
    return f"{minutes} min ago" if minutes < 90 else f"{minutes // 60} h ago"


def _wind(w: dict | None) -> str:
    return f"{int(w['speed'])} kt {w['from']} {w['type']}" if w else "no wind data"


def _periods(day: dict) -> str:
    parts = []
    for name in ("morning", "midday", "afternoon"):
        p = day["periods"].get(name)
        if p:
            parts.append(f"{name} {p['quality'] or '?'} ({_wind(p['wind'])})")
    return "; ".join(parts)


def _by_source(day: dict) -> str:
    parts = []
    for sid, v in day["by_source"].items():
        rating = f" [{v['rating']}]" if v.get("rating") else ""
        parts.append(f"{SHORT.get(sid, sid)} {v['surf']}{rating}")
    return ", ".join(parts)


def _now(data: dict) -> str:
    now = data.get("now")
    if not now:
        return "no current estimate"
    line = f"{surf.surf_text(now['surf_min'], now['surf_max'])} ft"
    if now.get("swells"):
        s = now["swells"][0]
        line += f", swell {s['height']} ft @ {int(s['period'])} s from {s['from']} ({int(s['dir'])}°)"
    if now.get("wind"):
        line += f", wind {_wind(now['wind'])}, {now.get('quality')}"
    if now.get("tide") is not None:
        line += f", tide {now['tide']} ft"
    return line


def _buoys(data: dict) -> list:
    out = []
    for b in data.get("buoys", []):
        direction = f" from {surf.compass(b['direction'])} ({int(b['direction'])}°)" if b.get("direction") is not None else ""
        water = f", water {b['water_f']}°F" if b.get("water_f") else ""
        out.append(f"Buoy {b['station']} {b['name']}: {b['height_ft']} ft @ {int(b['period_s'])} s{direction}{water} ({_ago(b['t'])})")
    return out


def forecast_md(data: dict, ai: str | None = None) -> str:
    spot = data["spot"]
    lines = [f"# {spot['name']} surf forecast", "",
             f"{spot['region']} · faces {surf.compass(spot['faces_deg'])} · offshore wind is from "
             f"{surf.compass(spot['offshore_wind_from_deg'])} · generated {when(data['generated'])}", ""]
    if spot.get("about"):
        lines += [spot["about"], ""]
    lines += [f"**Now:** {_now(data)}", ""]
    lines += [f"- {b}" for b in _buoys(data)] + [""]
    if ai:
        lines += ["## Overview (local AI, from the numbers below)", "", ai.strip(), ""]
    lines += ["## Next days (fused)", "",
              "| Day | Surf (ft) | Stars | Best | Swell | Morning / midday / afternoon | Confidence |",
              "|---|---|---|---|---|---|---|"]
    for d in data["days"]:
        quality = " / ".join((d["periods"].get(n) or {}).get("quality") or "?" for n in ("morning", "midday", "afternoon"))
        size = d["surf"] + (f" ({d['caution']})" if d.get("caution") else "")
        lines.append(f"| {d['label']} | {size} | {d['stars']}/5 | {d.get('best') or '–'} | {d.get('swell', '–')} | {quality} | {d['confidence']} |")
    lines += ["", "## Each source, per day (wave face, ft)", ""]
    ids = [s["id"] for s in data["sources"] if s["ok"]]
    lines += ["| Day | Fused | " + " | ".join(SHORT.get(i, i) for i in ids) + " |", "|---|---|" + "---|" * len(ids)]
    for d in data["days"]:
        cells = [(d["by_source"].get(i) or {}).get("surf", "–") for i in ids]
        lines.append(f"| {d['label']} | {d['surf']} | " + " | ".join(cells) + " |")
    lines += ["", "## Wind and tides", ""]
    for d in data["days"]:
        tides = ", ".join(f"{t['type']} {t['time']} {t['height']} ft" for t in d["tides"])
        lines.append(f"- **{d['label']}**: {_periods(d)}. Tides ({data.get('tide_station', '')}): {tides or 'n/a'}.")
    notes = [(d["label"], SHORT.get(sid, sid), v["note"]) for d in data["days"] for sid, v in d["by_source"].items() if v.get("note")]
    if notes:
        lines += ["", "## Forecaster notes", ""] + [f"- {label} ({who}): {note}" for label, who, note in notes]
    lines += ["", "## Sources", ""]
    for s in data["sources"]:
        state = f"ok, through {s['through']}" if s["ok"] else f"unavailable: {s['error']}"
        scale = f", scaled ×{s['scale']} to Surfline" if s.get("scale") else ""
        lines.append(f"- {s['label']}: {state}{scale}. {s['url']}")
    lines += [f"- {OFF_NOTE.get(o, o + ' is switched off.')}" for o in data.get("off", [])]
    return "\n".join(lines) + "\n"


def overview_md(data: dict) -> str:
    rows = data["spots"]
    labels = [d["label"] for d in rows[0]["days"]] if rows else []
    lines = ["# Bay Area surf: next days (fused forecast)", "", f"Generated {when(data['generated'])}. "
             "Each cell: wave face in feet, then morning/midday/afternoon surface (clean, fair or blown by wind).", "",
             "| Spot | " + " | ".join(labels) + " |", "|---|" + "---|" * len(labels)]
    for row in rows:
        cells = []
        for d in row["days"]:
            quality = "/".join(((d["periods"].get(n) or {}).get("quality") or "?") for n in ("morning", "midday", "afternoon"))
            cells.append(f"{d['surf']} ft · {quality}")
        lines.append(f"| {row['spot']['name']} | " + " | ".join(cells) + " |")
    lines += ["", "Sources: " + ", ".join(s["label"] for s in data["sources"] if s["ok"]) + "."]
    lines += [OFF_NOTE.get(o, o + " is switched off.") for o in data.get("off", [])]
    return "\n".join(lines) + "\n"


def ai_block(data: dict) -> str:
    """Everything the model may use, as terse lines."""
    spot = data["spot"]
    lines = [f"SPOT: {spot['name']} ({spot['region']}). Faces {surf.compass(spot['faces_deg'])}. "
             f"Offshore wind blows from {surf.compass(spot['offshore_wind_from_deg'])}. {spot.get('about', '')}".strip(),
             f"NOW ({when(data['generated'])}): {_now(data)}"]
    lines += _buoys(data)
    lines.append("DAILY (fused surf in ft of wave face | swell | surface by time of day | tides | confidence | each source):")
    for d in data["days"]:
        tides = ", ".join(f"{t['type']} {t['time']} {t['height']}ft" for t in d["tides"])
        lines.append(f"{d['label']}: {d['surf']} ft{' (' + d['caution'] + ')' if d.get('caution') else ''} | {d.get('swell', 'n/a')} | {_periods(d)} | {tides} | "
                     f"confidence {d['confidence']} | {_by_source(d)}")
    labels = {d["date"]: d["label"] for d in data["days"]}
    writeups = [w for w in data.get("writeups", []) if w["day"] is None or w["day"] in labels]
    if writeups:
        lines.append("WRITTEN FORECASTS FROM THE SOURCES (their words; surf-forecast.com's heights are open-water swell, "
                     "not wave face):")
        for w in writeups:
            who = "Surfline's forecaster" if w["source"] == "Surfline" else w["source"]
            lines.append(f"{who}{', ' + labels[w['day']] if w['day'] else ''}: {w['text']}")
    return "\n".join(lines)


def region_block(data: dict) -> str:
    lines = [f"TODAY IS {when(data['generated'])}.", "SPOTS, south to north (surf in ft of wave face; morning/midday/afternoon surface):"]
    for row in data["spots"]:
        days = []
        for d in row["days"]:
            quality = "/".join(((d["periods"].get(n) or {}).get("quality") or "?") for n in ("morning", "midday", "afternoon"))
            days.append(f"{d['dow']} {d['surf']} {quality} conf {d['confidence']}")
        lines.append(f"{row['spot']['name']} ({row['spot']['about'] or row['spot']['region']}): " + "; ".join(days))
    return "\n".join(lines)
