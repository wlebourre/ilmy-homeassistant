"""Local prayer-time and Qibla calculation engine.

Pure-Python, stdlib-only port of the verified hijri service engine
(hijri/internal/prayer/{calculator,methods,qibla}.go). Minute-exact parity
with the Go implementation is pinned by tests/test_prayer_engine.py and
tests/test_qibla.py against vectors generated from the Go code itself.
"""

from .calculator import PrayerTimes, PrayerTimesError, compute_prayer_times
from .methods import (
    COUNTRY_METHOD_MAP,
    DEFAULT_METHOD,
    HIGH_LAT_ANGLE_BASED,
    HIGH_LAT_MIDDLE_NIGHT,
    HIGH_LAT_NONE,
    HIGH_LAT_ONE_SEVENTH,
    METHODS,
    CalculationMethod,
    method_for_country,
)
from .qibla import (
    COMPASS_POINTS,
    COMPASS_POINTS_AR,
    KAABA_LAT,
    KAABA_LNG,
    QiblaInfo,
    compass_point,
    compass_point_ar,
    qibla_direction,
)

__all__ = [
    "COMPASS_POINTS",
    "COMPASS_POINTS_AR",
    "COUNTRY_METHOD_MAP",
    "DEFAULT_METHOD",
    "HIGH_LAT_ANGLE_BASED",
    "HIGH_LAT_MIDDLE_NIGHT",
    "HIGH_LAT_NONE",
    "HIGH_LAT_ONE_SEVENTH",
    "KAABA_LAT",
    "KAABA_LNG",
    "METHODS",
    "CalculationMethod",
    "PrayerTimes",
    "PrayerTimesError",
    "QiblaInfo",
    "compass_point",
    "compass_point_ar",
    "compute_prayer_times",
    "method_for_country",
    "qibla_direction",
]
