"""A satellite picture of each spot, north up, with the swell drawn on it.

Imagery: the USGS National Map (USGSImageryOnly; aerial imagery in the public domain). Tiles are
fetched once, stitched and kept on disk: the coast doesn't move.

`base()` is the plain picture (the web page draws live arrows over it as you scrub through the
forecast). `annotated()` burns the arrows in, for agents and for sharing.
"""

from __future__ import annotations

import io
import math

from PIL import Image, ImageDraw, ImageFont

from . import config, net, surf
from .spots import Spot

TILES = "https://basemap.nationalmap.gov/arcgis/rest/services/USGSImageryOnly/MapServer/tile/{z}/{y}/{x}"
CREDIT = "Imagery: USGS National Map"
ZOOM = 15          # ~3.8 m per pixel here: about 3.6 km across, enough to see how the coast lies
SIZE = (960, 640)
TILE = 256
SWELL_COLORS = ((64, 196, 255), (190, 160, 255))
WIND_COLOR = (255, 214, 10)


def _pixel(lat: float, lon: float, zoom: int) -> tuple:
    n = TILE * 2 ** zoom
    x = (lon + 180) / 360 * n
    y = (1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * n
    return x, y


def base(spot: Spot, zoom: int = ZOOM) -> bytes:
    """JPEG, SIZE pixels, centred on the spot. Raises net.FetchError if the tiles can't be had."""
    path = config.cache_dir("sat") / f"{spot.id}-{zoom}.jpg"
    if path.exists():
        return path.read_bytes()
    cx, cy = _pixel(spot.lat, spot.lon, zoom)
    left, top = int(cx - SIZE[0] / 2), int(cy - SIZE[1] / 2)
    image = Image.new("RGB", SIZE)
    for tx in range(left // TILE, (left + SIZE[0] - 1) // TILE + 1):
        for ty in range(top // TILE, (top + SIZE[1] - 1) // TILE + 1):
            body, _ = net.get(TILES.format(z=zoom, x=tx, y=ty), ttl=180 * 86400)
            tile = Image.open(io.BytesIO(body), formats=("JPEG", "PNG"))  # nothing else is decoded from the network
            image.paste(tile.convert("RGB"), (tx * TILE - left, ty * TILE - top))
    out = io.BytesIO()
    image.save(out, "JPEG", quality=86)
    path.write_bytes(out.getvalue())
    return out.getvalue()


def _unit(bearing: float) -> tuple:
    """Screen vector (x right, y down) for a compass bearing, north up."""
    r = math.radians(bearing)
    return math.sin(r), -math.cos(r)


def swell_arrow(faces: float, direction: float, i: int = 0) -> tuple:
    """(tail, tip, crests) in picture pixels for the i-th swell train. The arrow sits out to sea in
    front of the spot (along the way the beach faces) and points the way the swell travels; the
    crests cross it at right angles. static/app.js draws the same thing: keep the two in step."""
    cx, cy = SIZE[0] / 2, SIZE[1] / 2
    fx, fy = _unit(faces)
    ax, ay = cx + fx * (150 + i * 95), cy + fy * (150 + i * 95)
    ux, uy = _unit(direction + 180)
    half = 105 if i == 0 else 78
    crests = []
    for k in range(3 if i == 0 else 0):
        mx, my = ax - ux * (half - 14 - k * 42), ay - uy * (half - 14 - k * 42)
        crests.append(((mx - uy * 58, my + ux * 58), (mx + uy * 58, my - ux * 58)))
    return (ax - ux * half, ay - uy * half), (ax + ux * half, ay + uy * half), crests


def _arrow(draw: ImageDraw.ImageDraw, tail: tuple, tip: tuple, color: tuple, width: int) -> None:
    dx, dy = tip[0] - tail[0], tip[1] - tail[1]
    length = math.hypot(dx, dy) or 1
    ux, uy = dx / length, dy / length
    head = width * 3.2
    neck = (tip[0] - ux * head, tip[1] - uy * head)
    for w, c in ((width + 4, (0, 0, 0)), (width, color)):  # dark outline, then the colour
        draw.line([tail, neck], fill=c, width=w)
        grow = (w - width) / 2
        draw.polygon([(tip[0] + ux * grow, tip[1] + uy * grow),
                      (neck[0] - uy * (head * 0.62 + grow), neck[1] + ux * (head * 0.62 + grow)),
                      (neck[0] + uy * (head * 0.62 + grow), neck[1] - ux * (head * 0.62 + grow))], fill=c)


def _label(draw: ImageDraw.ImageDraw, xy: tuple, words: str, color: tuple, font, anchor: str = "la") -> None:
    box = draw.textbbox(xy, words, font=font, anchor=anchor)
    draw.rounded_rectangle((box[0] - 7, box[1] - 5, box[2] + 7, box[3] + 5), 7, fill=(8, 14, 22, 205))
    draw.text(xy, words, fill=color, font=font, anchor=anchor)


def annotated(spot: Spot, swells: list | None = None, wind: dict | None = None, caption: str = "") -> bytes:
    """The picture with the swell arriving (arrows and wave crests), the wind, and a caption."""
    image = Image.open(io.BytesIO(base(spot)), formats=("JPEG",)).convert("RGB")
    draw = ImageDraw.Draw(image, "RGBA")
    font, small = ImageFont.load_default(size=22), ImageFont.load_default(size=16)
    cx, cy = SIZE[0] / 2, SIZE[1] / 2
    lines = []
    for i, s in enumerate((swells or [])[:2]):
        if s.get("dir") is None:
            continue
        color = SWELL_COLORS[i]
        tail, tip, crests = swell_arrow(spot.faces, s["dir"], i)
        for a, b in crests:
            draw.line([a, b], fill=(*color, 150), width=3)
        _arrow(draw, tail, tip, color, 9 if i == 0 else 6)
        lines.append((f"{'Swell' if i == 0 else '2nd swell'} {s['height']:g} ft @ {int(s['period'])} s from {s['from']} {int(s['dir'])}°", color))
    draw.ellipse((cx - 9, cy - 9, cx + 9, cy + 9), fill=(255, 255, 255), outline=(0, 0, 0), width=3)
    _label(draw, (cx + 16, cy + 12), spot.name, (255, 255, 255), font)
    if wind and wind.get("dir") is not None:
        wx, wy = SIZE[0] - 74, 74
        draw.ellipse((wx - 52, wy - 52, wx + 52, wy + 52), fill=(8, 14, 22, 190), outline=(255, 255, 255, 90), width=2)
        ux, uy = _unit(wind["dir"] + 180)
        _arrow(draw, (wx - ux * 36, wy - uy * 36), (wx + ux * 36, wy + uy * 36), WIND_COLOR, 6)
        draw.text((wx, wy - 66), "N", fill=(255, 255, 255), font=small, anchor="mm")
        lines.append((f"Wind {int(wind['speed'])} kt from {wind['from']} ({wind['type']})", WIND_COLOR))
    y = 16
    if caption:
        _label(draw, (16, y), caption, (255, 255, 255), font)
        y += 40
    for words, color in lines:
        _label(draw, (16, y), words, color, small)
        y += 32
    _label(draw, (SIZE[0] - 12, SIZE[1] - 12), CREDIT, (200, 210, 220), ImageFont.load_default(size=12), anchor="rd")
    out = io.BytesIO()
    image.save(out, "JPEG", quality=88)
    return out.getvalue()
