"""Surf arithmetic: open-ocean swell → breaking wave face at a spot, wind → clean / fair / blown.

All heights are feet, periods seconds, wind knots, directions degrees true that the swell or
wind comes FROM.
"""

from __future__ import annotations

import math

FT = 0.3048
G = 9.81
COMPASS = ("N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE", "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW")


def compass(deg: float | None) -> str:
    return "" if deg is None else COMPASS[int((deg % 360) / 22.5 + 0.5) % 16]


def compass_deg(name: str) -> float | None:
    name = name.strip().upper()
    return COMPASS.index(name) * 22.5 if name in COMPASS else None


def angle_between(a: float, b: float) -> float:
    return abs((a - b + 180) % 360 - 180)


def mean_direction(pairs) -> float | None:
    """Weighted circular mean of (degrees, weight) pairs."""
    x = sum(w * math.sin(math.radians(d)) for d, w in pairs)
    y = sum(w * math.cos(math.radians(d)) for d, w in pairs)
    if abs(x) < 1e-9 and abs(y) < 1e-9:
        return None
    return math.degrees(math.atan2(x, y)) % 360


def breaking_height(height_ft: float, period_s: float) -> float:
    """Breaker height of a deep-water swell on an open beach (Komar & Gaughan 1972):
    Hb = 0.39 g^(1/5) (T H0²)^(2/5). A 3 ft swell breaks at ~4 ft at 8 s and ~6 ft at 17 s."""
    if not height_ft or not period_s or height_ft <= 0 or period_s <= 0:
        return 0.0
    h = height_ft * FT
    return 0.39 * G ** 0.2 * (period_s * h * h) ** 0.4 / FT


# Swell direction → which of the spot's three exposure numbers applies (interpolated between).
_ANCHORS = ((150, 0, 0.5), (180, 0, 1.0), (205, 0, 1.0), (265, 1, 1.0), (300, 2, 1.0), (330, 2, 0.8), (360, 2, 0.45))


def exposure(factors, direction: float | None) -> float:
    """Share of an open-ocean swell's breaking height that reaches the spot."""
    if direction is None:
        return factors[1]
    d = direction % 360
    if d < _ANCHORS[0][0]:  # E to SSE: the coast faces away
        return factors[0] * 0.3
    for (d0, i0, m0), (d1, i1, m1) in zip(_ANCHORS, _ANCHORS[1:]):
        if d <= d1:
            t = (d - d0) / (d1 - d0)
            return factors[i0] * m0 * (1 - t) + factors[i1] * m1 * t
    return factors[2] * 0.45


def face_height(factors, swells, scale: float = 1.0) -> float:
    """Wave face (ft) at a spot from open-ocean swell trains [(height_ft, period_s, direction)].
    Trains add up by energy, so a second small swell barely matters."""
    energy = 0.0
    for height, period, direction in swells:
        energy += (breaking_height(height, period) * exposure(factors, direction)) ** 2
    return math.sqrt(energy) * scale


def face_range(center: float) -> tuple:
    """A forecast is a range: sets run above the average wave."""
    return center * 0.85, center * 1.15


def surf_text(lo: float | None, hi: float | None) -> str:
    """"3-4", "8-10", "flat": whole feet, at least a foot apart, wider when it is big."""
    if lo is None or hi is None:
        return "–"
    if hi < 0.75:
        return "flat"
    a = max(int(round(lo)), 0)
    b = max(int(round(hi)), a + 1)
    if b >= 12 and b - a < 3:
        b = a + 3
    elif b >= 7 and b - a < 2:
        b = a + 2
    return f"{a}-{b}"


def wind_type(direction: float | None, speed: float | None, offshore: float) -> str:
    if direction is None or speed is None:
        return ""
    if speed < 4:
        return "glassy"
    a = angle_between(direction, offshore)
    return "offshore" if a <= 40 else "cross-off" if a <= 75 else "cross" if a <= 105 else "cross-on" if a <= 140 else "onshore"


# Up to this many knots the surface is clean / still fair, by wind type.
_LIMITS = {"glassy": (99, 99), "offshore": (18, 28), "cross-off": (12, 20), "cross": (7, 14),
           "cross-on": (5, 10), "onshore": (5, 10)}


def quality(direction: float | None, speed: float | None, offshore: float) -> str:
    """clean / fair / blown: what the wind does to the wave face."""
    kind = wind_type(direction, speed, offshore)
    if not kind:
        return ""
    clean, fair = _LIMITS[kind]
    return "clean" if speed <= clean else "fair" if speed <= fair else "blown"


def caution(lo: float | None, hi: float | None) -> str:
    """Who the size is for. Deliberately blunt: it is the same at every spot."""
    if lo is None or hi is None:
        return ""
    mid = (lo + hi) / 2
    return "experts only" if mid >= 10 else "not for beginners" if mid > 4 else ""


def rate(lo: float | None, hi: float | None, period: float | None, q: str) -> int:
    """0-5 stars: is it worth the drive? Size, then swell period, then wind."""
    if lo is None or hi is None or hi < 1.5:
        return 0
    mid = (lo + hi) / 2
    # Bigger is better up to well overhead; past that fewer people can surf it at all.
    stars = 1.0 if mid < 2 else 2.0 if mid < 3 else 3.0 if mid < 5 else 3.5 if mid < 10 else 2.5 if mid < 15 else 1.5
    if period:
        stars += 1.0 if period >= 14 else 0.5 if period >= 11 else -0.5 if period < 8 else 0
    stars += {"clean": 0.5, "fair": -0.5, "blown": -1.5}.get(q, 0)
    return max(0, min(5, int(stars)))
