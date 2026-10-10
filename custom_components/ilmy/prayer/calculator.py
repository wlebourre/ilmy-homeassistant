"""Prayer-time calculator.

Pure-Python port of hijri/internal/prayer/calculator.go (PrayTimes.org
algorithm family): low-precision solar declination + equation of time,
hour-angle solves for Fajr/Sunrise/Maghrib/Isha, Dhuhr at solar transit,
Asr from the shadow factor, high-latitude adjustments, and midnight as the
sunset→Fajr midpoint.

Solar math produces fractional UTC hours on the civil date; each result is
rounded to the minute exactly like Go (math.Round, half away from zero) and
delivered as a timezone-aware datetime in the requested IANA timezone. When
the sun never reaches a prayer's defining altitude (polar situations), the
corresponding attribute is None — the Python mirror of Go's zero time.Time
/ "--:--".

Stdlib only (math, datetime, zoneinfo, dataclasses); import-safe and fully
synchronous.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .methods import (
    DEFAULT_METHOD,
    HIGH_LAT_ANGLE_BASED,
    HIGH_LAT_ONE_SEVENTH,
    METHODS,
    CalculationMethod,
)


class PrayerTimesError(ValueError):
    """Invalid prayer-time calculation parameter."""


@dataclass(frozen=True)
class PrayerTimes:
    """Prayer times for one civil date, as timezone-aware datetimes.

    Attributes are None when the sun never reaches the prayer's defining
    altitude and no high-latitude adjustment was requested.
    """

    fajr: datetime | None
    sunrise: datetime | None
    dhuhr: datetime | None
    asr: datetime | None
    maghrib: datetime | None
    isha: datetime | None
    midnight: datetime | None


def _round_go(x: float) -> int:
    """Go's math.Round — half away from zero (Python's round is banker's)."""
    return math.floor(x + 0.5) if x >= 0 else -math.floor(-x + 0.5)


def _fix_angle(a: float) -> float:
    """Normalise an angle to [0, 360)."""
    a = math.fmod(a, 360.0)
    if a < 0:
        a += 360.0
    return a


def _fix_hour(h: float) -> float:
    """Normalise a time value (hours) to [0, 24)."""
    h = math.fmod(h, 24.0)
    if h < 0:
        h += 24.0
    return h


def _julian_day(y: int, m: int, d: int) -> float:
    """Gregorian calendar date → Julian Day Number."""
    if m <= 2:
        y -= 1
        m += 12
    a = math.floor(y / 100)
    b = 2 - a + math.floor(a / 4)
    return (
        math.floor(365.25 * (y + 4716))
        + math.floor(30.6001 * (m + 1))
        + d + b - 1524.5
    )


def _sun_position(jd: float) -> tuple[float, float]:
    """Sun declination (degrees) and equation of time (hours) for a Julian Day.

    Low-precision solar coordinates, same formulas as the Go service.
    """
    D = jd - 2451545.0  # days since J2000.0

    L = _fix_angle(280.460 + 0.9856474 * D)  # mean longitude
    g = _fix_angle(357.528 + 0.9856003 * D)  # mean anomaly
    gr = math.radians(g)

    lam = _fix_angle(L + 1.915 * math.sin(gr) + 0.020 * math.sin(2 * gr))
    lr = math.radians(lam)

    epsilon = 23.439 - 0.0000004 * D  # obliquity of the ecliptic
    er = math.radians(epsilon)

    # Right ascension (hours)
    ra = _fix_hour(math.degrees(math.atan2(math.cos(er) * math.sin(lr), math.cos(lr))) / 15)

    declination = math.degrees(math.asin(math.sin(er) * math.sin(lr)))

    # Equation of time (hours), re-wrapped to (-12, 12]
    eqt = L / 15 - ra
    if eqt > 12:
        eqt -= 24
    elif eqt <= -12:
        eqt += 24

    return declination, eqt


def _mid_day(jd: float, longitude: float) -> float:
    """Local solar noon in hours (UTC offset not yet applied)."""
    return 12 - _sun_position(jd)[1] - longitude / 15


def _sun_angle_time(
    jd: float, latitude: float, longitude: float, angle: float, direction: int
) -> float:
    """Fractional UTC hour at which the sun reaches `angle` above/below horizon.

    direction: 1 = after noon, -1 = before noon. NaN when the sun never
    reaches the angle (polar situation).
    """
    decl, _ = _sun_position(jd)
    d = math.radians(decl)
    l = math.radians(latitude)
    a = math.radians(-angle)

    cos_ha = (math.sin(a) - math.sin(l) * math.sin(d)) / (math.cos(l) * math.cos(d))
    if cos_ha < -1 or cos_ha > 1:
        return math.nan
    ha = math.degrees(math.acos(cos_ha)) / 15

    noon = _mid_day(jd, longitude)
    return noon + ha if direction > 0 else noon - ha


def _asr_time(jd: float, latitude: float, longitude: float, shadow_factor: int) -> float:
    """Asr time for shadow factor (1 = Shafi'i/Maliki/Hanbali, 2 = Hanafi)."""
    decl, _ = _sun_position(jd)
    # Target altitude: angle whose shadow ratio = shadow_factor
    a = math.degrees(
        math.atan(1 / (shadow_factor + math.tan(math.radians(abs(latitude - decl)))))
    )
    return _sun_angle_time(jd, latitude, longitude, -a, 1)


def _high_lat_adjust(
    method: int,
    base: float,
    night_duration: float,
    fajr_angle: float,
    isha_angle: float,
    is_fajr: bool,
) -> float:
    """High-latitude adjustment for Fajr/Isha (port of Go's highLatAdjust)."""
    if method == HIGH_LAT_ANGLE_BASED:
        portion = (fajr_angle if is_fajr else isha_angle) / 60.0
    elif method == HIGH_LAT_ONE_SEVENTH:
        portion = 1.0 / 7.0
    else:  # middle of night (Go's default branch, incl. out-of-range values)
        portion = 0.5
    night = portion * night_duration
    return base - night if is_fajr else base + night


def _hours_to_datetime(
    hours: float, y: int, m: int, d: int, tz: ZoneInfo
) -> datetime | None:
    """Fractional UTC hours on the civil date → aware datetime in `tz`.

    Mirrors Go's hoursToTime: round to the minute (half away from zero), then
    let date arithmetic normalise overflow (hours ≥ 24 or < 0 roll into
    adjacent days). NaN → None (Go's zero time.Time).
    """
    if math.isnan(hours):
        return None
    total_minutes = _round_go(hours * 60)
    base = datetime(y, m, d, tzinfo=timezone.utc)
    return (base + timedelta(minutes=total_minutes)).astimezone(tz)


def compute_prayer_times(
    day: date,
    latitude: float,
    longitude: float,
    timezone: str,
    method: str = DEFAULT_METHOD,
    asr_factor: int = 1,
    high_latitude: int = 0,
) -> PrayerTimes:
    """Compute prayer times for one civil date.

    ``timezone`` is an IANA name (e.g. "Asia/Riyadh"). Returns a PrayerTimes
    whose datetimes are localised to that zone. Raises PrayerTimesError (a
    ValueError) for an unknown method or an out-of-range asr_factor /
    high_latitude.
    """
    if method not in METHODS:
        raise PrayerTimesError(f"unknown calculation method: {method!r}")
    if asr_factor not in (1, 2):
        raise PrayerTimesError(
            f"invalid asr_factor: {asr_factor!r} (expected 1 or 2)"
        )
    if high_latitude not in (0, 1, 2, 3):
        raise PrayerTimesError(
            f"invalid high_latitude: {high_latitude!r} (expected 0, 1, 2 or 3)"
        )
    try:
        tz = ZoneInfo(timezone)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise PrayerTimesError(f"unknown timezone: {timezone!r}") from exc
    mdef: CalculationMethod = METHODS[method]

    # Custom-method overrides are not exposed in this API (unlike Go's
    # CalcParams); the custom method's default angles apply.
    fajr_angle = mdef.fajr_angle
    # Go keeps a single ishaAngle := method.IshaParam — for interval-based
    # methods it is the minutes value (e.g. 90), and that same value feeds
    # the angle-based high-latitude portion (a faithful quirk).
    isha_angle = (
        mdef.isha_angle
        if mdef.isha_angle is not None
        else mdef.isha_interval_minutes
    )

    y, m, d = day.year, day.month, day.day
    jd = _julian_day(y, m, d)

    dhuhr_utc = _mid_day(jd, longitude)
    sunrise_utc = _sun_angle_time(jd, latitude, longitude, 0.833, -1)  # refraction + solar disc
    sunset_utc = _sun_angle_time(jd, latitude, longitude, 0.833, 1)
    fajr_utc = _sun_angle_time(jd, latitude, longitude, fajr_angle, -1)
    asr_utc = _asr_time(jd, latitude, longitude, asr_factor)

    # Night duration (hours) for high-lat adjustments
    if not math.isnan(sunrise_utc) and not math.isnan(sunset_utc):
        night_duration = 24.0 - (sunset_utc - sunrise_utc)
    else:
        night_duration = 12.0

    # Isha UTC
    isha_utc: float
    if mdef.isha_is_interval:
        isha_utc = (
            sunset_utc + mdef.isha_interval_minutes / 60.0
            if not math.isnan(sunset_utc)
            else math.nan
        )
    else:
        isha_utc = _sun_angle_time(jd, latitude, longitude, isha_angle, 1)

    # Maghrib UTC
    maghrib_utc: float
    if mdef.maghrib_interval_minutes > 0:
        maghrib_utc = (
            sunset_utc + mdef.maghrib_interval_minutes / 60.0
            if not math.isnan(sunset_utc)
            else math.nan
        )
    else:
        maghrib_utc = sunset_utc

    # High-latitude adjustments — bases follow the standard PrayTimes
    # formulas; synthetic 12h night centred on solar midnight when sunset is
    # not computable.
    if high_latitude != 0 and (math.isnan(fajr_utc) or math.isnan(isha_utc)):
        fajr_base = sunset_utc + night_duration
        isha_base = sunset_utc
        if math.isnan(sunset_utc):
            isha_base = dhuhr_utc + 6  # synthetic sunset
            fajr_base = dhuhr_utc + 18  # synthetic next sunrise
        if math.isnan(fajr_utc):
            fajr_utc = _high_lat_adjust(
                high_latitude, fajr_base, night_duration, fajr_angle, isha_angle, True
            )
        if math.isnan(isha_utc):
            isha_utc = _high_lat_adjust(
                high_latitude, isha_base, night_duration, fajr_angle, isha_angle, False
            )

    # Midnight = midpoint between Maghrib and next Fajr
    midnight_utc: float
    if not math.isnan(sunset_utc) and not math.isnan(fajr_utc):
        if fajr_utc < sunset_utc:
            midnight_utc = sunset_utc + (24 - sunset_utc + fajr_utc) / 2.0
        else:
            midnight_utc = sunset_utc + (fajr_utc - sunset_utc) / 2.0
    else:
        midnight_utc = dhuhr_utc + 12

    return PrayerTimes(
        fajr=_hours_to_datetime(fajr_utc, y, m, d, tz),
        sunrise=_hours_to_datetime(sunrise_utc, y, m, d, tz),
        dhuhr=_hours_to_datetime(dhuhr_utc, y, m, d, tz),
        asr=_hours_to_datetime(asr_utc, y, m, d, tz),
        maghrib=_hours_to_datetime(maghrib_utc, y, m, d, tz),
        isha=_hours_to_datetime(isha_utc, y, m, d, tz),
        midnight=_hours_to_datetime(midnight_utc, y, m, d, tz),
    )
