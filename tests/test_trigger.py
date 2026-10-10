"""Tests for the Ilmy prayer time automation trigger."""

from __future__ import annotations

import asyncio
import re
from datetime import date, datetime, timedelta

import pytest
import voluptuous as vol
from aioresponses import aioresponses

from homeassistant.const import (
    CONF_LANGUAGE,
    CONF_LATITUDE,
    CONF_LONGITUDE,
    CONF_OPTIONS,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.trigger import TriggerConfig
from homeassistant.util import dt as dt_util

from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.ilmy.const import (
    CONF_ASR,
    CONF_HADITH_BOOK,
    CONF_HADITH_URL,
    CONF_HIGH_LATITUDE,
    CONF_HIJRI_URL,
    CONF_METHOD,
    CONF_QURAN_URL,
    CONF_TIME_ZONE_NAME,
    DOMAIN,
    STORAGE_KEY,
    STORAGE_VERSION,
)
from custom_components.ilmy.prayer import compute_prayer_times
from custom_components.ilmy.trigger import (
    IlmyPrayerTimeTrigger,
    async_get_triggers,
)

LAT, LNG = 21.42, 39.82
TZ_NAME = "UTC"
DAY = date(2026, 6, 15)
SETUP_TIME = datetime(2026, 6, 15, 8, 0, tzinfo=dt_util.UTC)

HIJRI_URL = "https://hijri.example.com"
HADITH_URL = "https://hadith.example.com"
QURAN_URL = "https://quran.example.com"

HIJRI_RE = re.compile(r"^https://hijri\.example\.com/api/v1/hijri.*")
BOOKS_RE = re.compile(r"^https://hadith\.example\.com/api/v1/books.*")
BY_REF_RE = re.compile(r"^https://hadith\.example\.com/api/v1/hadiths/by-ref/.*")
SURAHS_RE = re.compile(r"^https://quran\.example\.com/api/v1/surahs.*")
WINDOW_RE = re.compile(r"^https://quran\.example\.com/api/v1/quran/window.*")

HIJRI_PAYLOAD = {
    "day": 29,
    "month": 12,
    "year": 1447,
    "month_name": "Dhul-Hijjah",
    "month_name_ar": "ذو الحجة",
    "formatted": "29 Dhul-Hijjah 1447",
    "formatted_ar": "٢٩ ذو الحجة ١٤٤٧",
    "is_ramadan": False,
}
BOOKS_PAYLOAD = [
    {"slug": "bukhari", "english_title": "Sahih al-Bukhari", "total_hadiths": 7563},
]
HADITH_PAYLOAD = {
    "id_in_book": 756,
    "book_slug": "bukhari",
    "book_english_title": "Sahih al-Bukhari",
    "matn": {"arabic_text": "حديث اليوم", "text_translations": {"en": "Hadith"}},
}
SURAHS_PAYLOAD = [
    {"id": 1, "number": 1, "english_name": "Al-Fatihah", "arabic_name": "الفاتحة"},
]
WINDOW_PAYLOAD = {
    "ayat": [
        {
            "surah_id": 1,
            "aya_number": 1,
            "uthmani_text": "بِسْمِ اللَّهِ",
            "text_translations": {"en": "In the name of Allah"},
            "juz": 1,
            "page": 1,
        }
    ]
}

STORED_CONTENT = {
    "local_date": DAY.isoformat(),
    "hijri": dict(HIJRI_PAYLOAD),
    "hijri_stale": False,
}


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Load the custom integration for every test in this module."""
    yield


@pytest.fixture(autouse=True)
async def unload_entries_after_test(hass: HomeAssistant):
    """Unload entries at test end so no point-in-time timers linger."""
    yield
    for entry in hass.config_entries.async_entries(DOMAIN):
        await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


def make_entry() -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        unique_id="ilmy",
        data={
            CONF_HIJRI_URL: HIJRI_URL,
            CONF_HADITH_URL: HADITH_URL,
            CONF_QURAN_URL: QURAN_URL,
            CONF_LATITUDE: LAT,
            CONF_LONGITUDE: LNG,
            CONF_TIME_ZONE_NAME: TZ_NAME,
            CONF_METHOD: "mwl",
        },
        options={
            CONF_LANGUAGE: "en",
            CONF_HADITH_BOOK: "bukhari",
            CONF_ASR: 1,
            CONF_HIGH_LATITUDE: 0,
        },
    )


async def setup_integration(hass: HomeAssistant) -> None:
    entry = make_entry()
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


def mock_all(m: aioresponses) -> None:
    """Mock every endpoint the daily batch can hit (200 responses)."""
    m.get(HIJRI_RE, status=200, payload=HIJRI_PAYLOAD, repeat=True)
    m.get(BOOKS_RE, status=200, payload=BOOKS_PAYLOAD, repeat=True)
    m.get(BY_REF_RE, status=200, payload=HADITH_PAYLOAD, repeat=True)
    m.get(SURAHS_RE, status=200, payload=SURAHS_PAYLOAD, repeat=True)
    m.get(WINDOW_RE, status=200, payload=WINDOW_PAYLOAD, repeat=True)


def expected_times(day: date) -> dict[str, datetime]:
    times = compute_prayer_times(
        day, LAT, LNG, TZ_NAME, method="mwl", asr_factor=1, high_latitude=0
    )
    return {name: getattr(times, name) for name in
            ("fajr", "sunrise", "dhuhr", "asr", "maghrib", "isha", "midnight")}


async def advance(hass: HomeAssistant, freezer, moment: datetime) -> None:
    freezer.move_to(moment)
    async_fire_time_changed(hass, moment)
    await hass.async_block_till_done()


async def attach_trigger(hass: HomeAssistant, options: dict):
    """Validate options, attach the trigger and capture fired actions."""
    config = await IlmyPrayerTimeTrigger.async_validate_config(
        hass, {CONF_OPTIONS: options}
    )
    trigger = IlmyPrayerTimeTrigger(
        hass,
        TriggerConfig(key="ilmy.prayer_time", options=config[CONF_OPTIONS]),
    )
    fired = []

    @callback
    def run_action(payload, description, context=None):
        fired.append((payload, description))
        return hass.async_create_task(asyncio.sleep(0))

    unsub = await trigger.async_attach_runner(run_action)
    return fired, unsub


# ── Discovery and config validation ────────────────────────────────────────────


async def test_async_get_triggers(hass: HomeAssistant) -> None:
    """The trigger platform exposes ilmy.prayer_time."""
    triggers = await async_get_triggers(hass)
    assert triggers == {"prayer_time": IlmyPrayerTimeTrigger}


async def test_validate_config_accepts_valid(hass: HomeAssistant) -> None:
    """Valid configs pass, with the offset parsed and defaulted."""
    config = await IlmyPrayerTimeTrigger.async_validate_config(
        hass, {CONF_OPTIONS: {"prayer": "maghrib", "offset": "-00:10:00"}}
    )
    assert config[CONF_OPTIONS] == {
        "prayer": "maghrib",
        "offset": timedelta(minutes=-10),
    }

    config = await IlmyPrayerTimeTrigger.async_validate_config(
        hass, {CONF_OPTIONS: {"prayer": "any"}}
    )
    assert config[CONF_OPTIONS]["offset"] == timedelta(0)


async def test_validate_config_rejects_bad_values(hass: HomeAssistant) -> None:
    """Unknown prayers and unparseable offsets are rejected."""
    with pytest.raises(vol.Invalid):
        await IlmyPrayerTimeTrigger.async_validate_config(
            hass, {CONF_OPTIONS: {"prayer": "sunset"}}
        )
    with pytest.raises(vol.Invalid):
        await IlmyPrayerTimeTrigger.async_validate_config(
            hass, {CONF_OPTIONS: {"prayer": "maghrib", "offset": "soon"}}
        )
    with pytest.raises(vol.Invalid):
        await IlmyPrayerTimeTrigger.async_validate_config(
            hass, {CONF_OPTIONS: {"offset": "00:05:00"}}
        )


async def test_validate_complete_config_moves_fields_to_options(
    hass: HomeAssistant,
) -> None:
    """UI-style top-level prayer/offset fields are migrated into options."""
    config = await IlmyPrayerTimeTrigger.async_validate_complete_config(
        hass, {"platform": "ilmy.prayer_time", "prayer": "fajr", "offset": "00:05:00"}
    )
    assert config[CONF_OPTIONS] == {
        "prayer": "fajr",
        "offset": timedelta(minutes=5),
    }
    assert "prayer" not in config


# ── Firing ─────────────────────────────────────────────────────────────────────


async def test_fires_at_configured_prayer(
    hass: HomeAssistant, hass_storage, freezer
) -> None:
    """The trigger fires at the configured prayer time with the event payload."""
    hass_storage[STORAGE_KEY] = {
        "version": STORAGE_VERSION,
        "minor_version": 1,
        "key": STORAGE_KEY,
        "data": {
            "last_fetch_local_date": DAY.isoformat(),
            "content": STORED_CONTENT,
        },
    }
    freezer.move_to(SETUP_TIME)
    dhuhr = expected_times(DAY)["dhuhr"]

    with aioresponses() as m:
        mock_all(m)
        await setup_integration(hass)
        fired, unsub = await attach_trigger(hass, {"prayer": "dhuhr"})

        await advance(hass, freezer, dhuhr - timedelta(seconds=30))
        assert fired == []

        await advance(hass, freezer, dhuhr + timedelta(seconds=5))

    unsub()
    assert fired == [
        (
            {
                "prayer": "dhuhr",
                "time": dhuhr.isoformat(),
                "hijri_date": "29 Dhul-Hijjah 1447",
            },
            "dhuhr prayer time",
        )
    ]


async def test_other_prayer_does_not_fire(
    hass: HomeAssistant, freezer
) -> None:
    """A trigger configured for maghrib stays silent at dhuhr."""
    freezer.move_to(SETUP_TIME)
    times = expected_times(DAY)

    with aioresponses() as m:
        mock_all(m)
        await setup_integration(hass)
        fired, unsub = await attach_trigger(hass, {"prayer": "maghrib"})

        await advance(hass, freezer, times["dhuhr"] + timedelta(seconds=5))
        assert fired == []

        # And it does fire at maghrib.
        await advance(hass, freezer, times["maghrib"] + timedelta(seconds=5))

    unsub()
    assert [payload["prayer"] for payload, _ in fired] == ["maghrib"]


async def test_negative_offset_fires_early(
    hass: HomeAssistant, freezer
) -> None:
    """An offset of -00:10:00 fires 10 minutes before Maghrib, with the
    prayer time itself in the payload."""
    freezer.move_to(SETUP_TIME)
    maghrib = expected_times(DAY)["maghrib"]
    fire_at = maghrib - timedelta(minutes=10)

    with aioresponses() as m:
        mock_all(m)
        await setup_integration(hass)
        fired, unsub = await attach_trigger(
            hass, {"prayer": "maghrib", "offset": "-00:10:00"}
        )

        await advance(hass, freezer, fire_at - timedelta(seconds=30))
        assert fired == []

        await advance(hass, freezer, fire_at + timedelta(seconds=5))

    unsub()
    assert [payload["time"] for payload, _ in fired] == [maghrib.isoformat()]


async def test_any_fires_at_each_prayer(
    hass: HomeAssistant, freezer
) -> None:
    """prayer: any fires at every prayer time."""
    freezer.move_to(SETUP_TIME)
    times = expected_times(DAY)

    with aioresponses() as m:
        mock_all(m)
        await setup_integration(hass)
        fired, unsub = await attach_trigger(hass, {"prayer": "any"})

        await advance(hass, freezer, times["dhuhr"] + timedelta(seconds=5))
        assert [payload["prayer"] for payload, _ in fired] == ["dhuhr"]

        await advance(hass, freezer, times["asr"] + timedelta(seconds=5))

    unsub()
    assert [payload["prayer"] for payload, _ in fired] == ["dhuhr", "asr"]


async def test_rearms_for_the_next_day(
    hass: HomeAssistant, hass_storage, freezer
) -> None:
    """After firing, the trigger re-arms and fires again the next day."""
    hass_storage[STORAGE_KEY] = {
        "version": STORAGE_VERSION,
        "minor_version": 1,
        "key": STORAGE_KEY,
        "data": {
            "last_fetch_local_date": DAY.isoformat(),
            "content": STORED_CONTENT,
        },
    }
    freezer.move_to(SETUP_TIME)
    dhuhr_today = expected_times(DAY)["dhuhr"]
    dhuhr_tomorrow = expected_times(DAY + timedelta(days=1))["dhuhr"]

    with aioresponses() as m:
        mock_all(m)
        await setup_integration(hass)
        fired, unsub = await attach_trigger(hass, {"prayer": "dhuhr"})

        await advance(hass, freezer, dhuhr_today + timedelta(seconds=5))
        assert len(fired) == 1

        await advance(hass, freezer, dhuhr_tomorrow + timedelta(seconds=5))

    unsub()
    assert [payload["time"] for payload, _ in fired] == [
        dhuhr_today.isoformat(),
        dhuhr_tomorrow.isoformat(),
    ]


async def test_rearms_on_coordinator_update(
    hass: HomeAssistant, freezer
) -> None:
    """A coordinator data change re-arms the trigger at the new time."""
    freezer.move_to(SETUP_TIME)
    new_dhuhr = SETUP_TIME + timedelta(hours=1)
    assert new_dhuhr != expected_times(DAY)["dhuhr"]

    with aioresponses() as m:
        mock_all(m)
        await setup_integration(hass)
        fired, unsub = await attach_trigger(hass, {"prayer": "dhuhr"})

        entry = hass.config_entries.async_entries(DOMAIN)[0]
        coordinator = entry.runtime_data
        data = coordinator.data
        data.prayer_times["dhuhr"] = new_dhuhr
        coordinator.async_set_updated_data(data)
        await hass.async_block_till_done()

        await advance(hass, freezer, new_dhuhr + timedelta(seconds=5))

    unsub()
    assert [payload["time"] for payload, _ in fired] == [new_dhuhr.isoformat()]


async def test_no_config_entry_returns_noop_unsub(hass: HomeAssistant) -> None:
    """Without a config entry the trigger attaches as a no-op."""
    config = await IlmyPrayerTimeTrigger.async_validate_config(
        hass, {CONF_OPTIONS: {"prayer": "dhuhr"}}
    )
    trigger = IlmyPrayerTimeTrigger(
        hass,
        TriggerConfig(key="ilmy.prayer_time", options=config[CONF_OPTIONS]),
    )

    @callback
    def run_action(payload, description, context=None):
        raise AssertionError("must not fire")

    unsub = await trigger.async_attach_runner(run_action)
    unsub()  # must not raise
