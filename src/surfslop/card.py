"""The forecast card: the featured spots × the next days as one PNG, for posting to a chat.

Same layout as the weekly surf poll's picture: a row per spot, the day's wave height, and three
pills for the morning, midday and afternoon surface.
"""

from __future__ import annotations

import io
from datetime import datetime
from zoneinfo import ZoneInfo

from PIL import Image, ImageDraw, ImageFont

from .spots import TZ

PILL = {"clean": (52, 199, 89), "fair": (255, 204, 0), "blown": (255, 149, 0), "": (199, 199, 204)}
BG, CARD, INK, MUTED = (242, 242, 247), (255, 255, 255), (28, 28, 30), (142, 142, 147)


def render(overview: dict) -> bytes:
    rows = overview["spots"]
    days = rows[0]["days"] if rows else []
    scale = 2
    name_w, col_w, row_h, gap, pad, head_h = 250, 150, 172, 20, 28, 100
    width = pad * 2 + name_w + col_w * len(days)
    sources = ", ".join(sorted({s["label"].split(" · ")[0] for s in overview["sources"] if s["ok"]}))
    footer = [f"Fused from {sources}.", datetime.fromtimestamp(overview["generated"], ZoneInfo(TZ)).strftime(
        "Generated %a %b %-d, %-I:%M %p PT · wave face in ft")]
    height = head_h + len(rows) * (row_h + gap) + 150
    image = Image.new("RGB", (width * scale, height * scale), BG)
    d = ImageDraw.Draw(image)
    bold = lambda size: ImageFont.load_default(size=size * scale)  # noqa: E731
    s = lambda *v: tuple(int(x * scale) for x in v)  # noqa: E731

    for i, day in enumerate(days):
        x = pad + name_w + col_w * i + col_w / 2
        d.text(s(x, 30), day["dow"][0], fill=INK, font=bold(30), anchor="mm")
        d.text(s(x, 72), str(int(day["date"][-2:])), fill=INK, font=bold(30), anchor="mm")
    for r, row in enumerate(rows):
        top = head_h + r * (row_h + gap)
        d.rounded_rectangle(s(pad, top, width - pad, top + row_h), 30 * scale, fill=CARD)
        words = row["spot"]["name"].split(" ")
        lines = [words[0], " ".join(words[1:])] if len(words) > 1 else words
        for j, line in enumerate(lines):
            d.text(s(pad + 20, top + row_h / 2 + (j - (len(lines) - 1) / 2) * 40), line, fill=INK, font=bold(30), anchor="lm")
        for i, day in enumerate(row["days"]):
            x = pad + name_w + col_w * i
            d.text(s(x + col_w / 2, top + 58), day["surf"], fill=INK, font=bold(38), anchor="mm")
            for k, name in enumerate(("morning", "midday", "afternoon")):
                quality = (day["periods"].get(name) or {}).get("quality") or ""
                px = x + 6 + k * 47
                d.rounded_rectangle(s(px, top + 132, px + 42, top + 146), 7 * scale, fill=PILL[quality])
    y = head_h + len(rows) * (row_h + gap) + 14
    x = pad
    for name in ("clean", "fair", "blown"):
        d.rounded_rectangle(s(x, y + 4, x + 42, y + 18), 7 * scale, fill=PILL[name])
        d.text(s(x + 52, y + 11), name, fill=MUTED, font=bold(20), anchor="lm")
        x += 140
    d.text(s(width - pad, y + 11), "morning · midday · afternoon", fill=MUTED, font=bold(20), anchor="rm")
    for j, line in enumerate(footer):
        d.text(s(width / 2, y + 62 + j * 30), line, fill=MUTED, font=bold(19), anchor="mm")
    out = io.BytesIO()
    image.save(out, "PNG", optimize=True)
    return out.getvalue()
