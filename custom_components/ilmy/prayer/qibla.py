"""Qibla direction.

Pure-Python port of hijri/internal/prayer/qibla.go: great-circle bearing
from North (clockwise) to the Kaaba, with the same 16-point compass
bucketing and Arabic labels.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

KAABA_LAT = 21.42251  # 21°25'21"N
KAABA_LNG = 39.82616  # 39°49'34"E

COMPASS_POINTS = (
    "N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
    "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW",
)
COMPASS_POINTS_AR = (
    "شمال", "شمال شمال شرق", "شمال شرق", "شرق شمال شرق",
    "شرق", "شرق جنوب شرق", "جنوب شرق", "جنوب جنوب شرق",
    "جنوب", "جنوب جنوب غرب", "جنوب غرب", "غرب جنوب غرب",
    "غرب", "غرب شمال غرب", "شمال غرب", "شمال شمال غرب",
)


@dataclass(frozen=True)
class QiblaInfo:
    """Qibla bearing result (degrees from North, clockwise)."""

    degrees: float
    compass: str
    direction_ar: str


def _round_go(x: float) -> int:
    """Go's math.Round — half away from zero."""
    return math.floor(x + 0.5) if x >= 0 else -math.floor(-x + 0.5)


def compass_point(bearing: float) -> str:
    """16-point compass label for a bearing in degrees."""
    return COMPASS_POINTS[_round_go(bearing / 22.5) % 16]


def compass_point_ar(bearing: float) -> str:
    """Arabic 16-point compass label for a bearing in degrees."""
    return COMPASS_POINTS_AR[_round_go(bearing / 22.5) % 16]


def qibla_direction(latitude: float, longitude: float) -> QiblaInfo:
    """Qibla direction (bearing from North, clockwise) — great-circle formula."""
    lat1 = math.radians(latitude)
    lat2 = math.radians(KAABA_LAT)
    d_lon = math.radians(KAABA_LNG) - math.radians(longitude)

    x = math.sin(d_lon) * math.cos(lat2)
    y = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(d_lon)

    bearing = math.fmod(math.degrees(math.atan2(x, y)) + 360, 360)

    return QiblaInfo(
        degrees=_round_go(bearing * 100) / 100,
        compass=compass_point(bearing),
        direction_ar=compass_point_ar(bearing),
    )
