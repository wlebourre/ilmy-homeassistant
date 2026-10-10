"""Tests for the Ilmy coordinator: local prayer times, scheduling and the
daily content batch (all HTTP mocked)."""

from __future__ import annotations

import random
import re
from datetime import date, datetime, timedelta

import pytest
from aioresponses import aioresponses

from homeassistant.const import CONF_LANGUAGE, CONF_LATITUDE, CONF_LONGITUDE
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
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
    EVENT_PRAYER,
    FETCH_JITTER_MINUTES,
    RETRY_DELAYS_MINUTES,
    STORAGE_KEY,
    STORAGE_VERSION,
    USER_AGENT,
)
from custom_components.ilmy.prayer import compute_prayer_times

LAT, LNG = 21.42, 39.82
TZ_NAME = "UTC"
# A Wednesday well clear of any DST boundary (the tz is UTC anyway).
DAY = date(2026, 6, 15)
SETUP_TIME = datetime(2026, 6, 15, 12, 0, tzinfo=dt_util.UTC)

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
    "book_arabic_title": "صحيح البخاري",
    "chapter_english_title": "Prayers",
    "chapter_arabic_title": "كتاب الصلاة",
    "matn": {
        "arabic_text": "حديث اليوم",
        "text_translations": {"en": "The hadith of the day"},
    },
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
    "hadith": {
        "reference": "Sahih al-Bukhari 100",
        "id_in_book": 100,
        "book_slug": "bukhari",
        "book": "Sahih al-Bukhari",
        "arabic": "قديم",
        "translation": "Previous hadith",
    },
    "verse": {
        "reference": "1:2",
        "surah_number": 1,
        "surah_name": "Al-Fatihah",
        "aya_number": 2,
        "uthmani_text": "الْحَمْدُ لِلَّهِ",
        "translation": "All praise is due to Allah",
        "juz": 1,
        "page": 1,
    },
}

JITTER_MINUTES = 5.0


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


@pytest.fixture
def fixed_jitter(monkeypatch):
    """Deterministic jitter for the daily batch (Maghrib + 5 minutes)."""
    monkeypatch.setattr(random, "uniform", lambda a, b: JITTER_MINUTES)


def make_entry(**kwargs) -> MockConfigEntry:
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
        **kwargs,
    )


async def setup_integration(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


def mock_all(m: aioresponses, repeat: bool = True) -> None:
    """Mock every endpoint the daily batch can hit (200 responses)."""
    m.get(HIJRI_RE, status=200, payload=HIJRI_PAYLOAD, repeat=repeat)
    m.get(BOOKS_RE, status=200, payload=BOOKS_PAYLOAD, repeat=repeat)
    m.get(BY_REF_RE, status=200, payload=HADITH_PAYLOAD, repeat=repeat)
    m.get(SURAHS_RE, status=200, payload=SURAHS_PAYLOAD, repeat=repeat)
    m.get(WINDOW_RE, status=200, payload=WINDOW_PAYLOAD, repeat=repeat)


def count_calls(m: aioresponses, pattern: re.Pattern) -> int:
    """How many requests matched one of the mocked endpoints."""
    return sum(
        len(calls)
        for (method, url), calls in m.requests.items()
        if pattern.match(str(url))
    )


def total_calls(m: aioresponses) -> int:
    return sum(len(calls) for calls in m.requests.values())


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


# ── Entities / local prayer-time computation ─────────────────────────────────


async def test_creates_12_sensors_with_locally_computed_times(
    hass: HomeAssistant, freezer
) -> None:
    """All 12 sensors exist; prayer-time sensors expose tz-aware timestamps
    matching the local prayer engine, minute-exact."""
    freezer.move_to(SETUP_TIME)
    with aioresponses() as m:
        mock_all(m)
        await setup_integration(hass, make_entry())

    registry = er.async_get(hass)
    entities = er.async_entries_for_config_entry(registry, hass.config_entries.async_entries(DOMAIN)[0].entry_id)
    assert len(entities) == 12

    expected = expected_times(DAY)
    for name, dt in expected.items():
        state = hass.states.get(f"sensor.ilmy_{name}")
        assert state is not None, name
        parsed = dt_util.parse_datetime(state.state)
        assert parsed is not None, name
        assert parsed.tzinfo is not None, f"{name} must be timezone-aware"
        assert parsed.utcoffset() is not None
        # Minute-exact against the local engine (no hardcoded times).
        assert parsed.replace(second=0, microsecond=0) == dt.replace(
            second=0, microsecond=0
        ), name

    # The batch has not run yet (before Maghrib): no HTTP at all.
    assert total_calls(m) == 0


# ── Daily batch scheduling ────────────────────────────────────────────────────


async def test_daily_batch_fires_after_maghrib_with_cached_statics(
    hass: HomeAssistant, hass_storage, freezer, fixed_jitter
) -> None:
    """With books/surahs in the persistent cache the batch is exactly 3 GETs:
    hijri + hadith by-ref + quran window."""
    hass_storage[STORAGE_KEY] = {
        "version": STORAGE_VERSION,
        "minor_version": 1,
        "key": STORAGE_KEY,
        "data": {
            "books": {"fetched_at": DAY.isoformat(), "data": BOOKS_PAYLOAD},
            "surahs": {"fetched_at": DAY.isoformat(), "data": SURAHS_PAYLOAD},
        },
    }
    freezer.move_to(SETUP_TIME)
    maghrib = expected_times(DAY)["maghrib"]

    with aioresponses() as m:
        mock_all(m)
        await setup_integration(hass, make_entry())
        assert total_calls(m) == 0

        # Just before Maghrib + jitter: still nothing.
        before = maghrib + timedelta(minutes=JITTER_MINUTES, seconds=-30)
        await advance(hass, freezer, before)
        assert total_calls(m) == 0

        # At Maghrib + jitter the batch runs.
        await advance(
            hass, freezer, maghrib + timedelta(minutes=JITTER_MINUTES, seconds=5)
        )

    assert count_calls(m, HIJRI_RE) == 1
    assert count_calls(m, BY_REF_RE) == 1
    assert count_calls(m, WINDOW_RE) == 1
    assert count_calls(m, BOOKS_RE) == 0
    assert count_calls(m, SURAHS_RE) == 0
    assert total_calls(m) == 3

    assert hass.states.get("sensor.ilmy_hijri_date").state == "29 Dhul-Hijjah 1447"
    assert (
        hass.states.get("sensor.ilmy_hadith_of_the_day").state
        == "Sahih al-Bukhari 756"
    )
    assert hass.states.get("sensor.ilmy_verse_of_the_day").state == "1:1"


async def test_daily_batch_first_run_is_5_gets(
    hass: HomeAssistant, freezer, fixed_jitter
) -> None:
    """Without cached books/surahs the batch fetches them too: 5 GETs."""
    freezer.move_to(SETUP_TIME)
    maghrib = expected_times(DAY)["maghrib"]

    with aioresponses() as m:
        mock_all(m)
        await setup_integration(hass, make_entry())
        await advance(
            hass, freezer, maghrib + timedelta(minutes=JITTER_MINUTES, seconds=5)
        )

    assert count_calls(m, HIJRI_RE) == 1
    assert count_calls(m, BOOKS_RE) == 1
    assert count_calls(m, BY_REF_RE) == 1
    assert count_calls(m, SURAHS_RE) == 1
    assert count_calls(m, WINDOW_RE) == 1
    assert total_calls(m) == 5


async def test_fetch_once_guard_after_restart(
    hass: HomeAssistant, hass_storage, freezer
) -> None:
    """A restart after Maghrib with today's batch already done performs no
    HTTP calls, and the persisted content is restored into the sensors."""
    hass_storage[STORAGE_KEY] = {
        "version": STORAGE_VERSION,
        "minor_version": 1,
        "key": STORAGE_KEY,
        "data": {
            "last_fetch_local_date": DAY.isoformat(),
            "content": STORED_CONTENT,
        },
    }
    maghrib = expected_times(DAY)["maghrib"]
    # Well past Maghrib (but before midnight).
    freezer.move_to(maghrib + timedelta(hours=1))

    with aioresponses() as m:
        mock_all(m)
        await setup_integration(hass, make_entry())
        # Let any (wrongly) armed callbacks fire well past their window.
        await advance(hass, freezer, maghrib + timedelta(hours=2))

    assert total_calls(m) == 0
    assert hass.states.get("sensor.ilmy_hijri_date").state == "29 Dhul-Hijjah 1447"
    assert (
        hass.states.get("sensor.ilmy_hadith_of_the_day").state
        == "Sahih al-Bukhari 100"
    )
    assert hass.states.get("sensor.ilmy_verse_of_the_day").state == "1:2"


async def test_first_setup_after_maghrib_arms_catchup(
    hass: HomeAssistant, freezer, monkeypatch
) -> None:
    """A fresh install after Maghrib fetches the daily content once, a few
    minutes after setup (FIRST_FETCH_DELAY_MINUTES is 1-5)."""
    monkeypatch.setattr(random, "uniform", lambda a, b: 3.0)
    maghrib = expected_times(DAY)["maghrib"]
    start = maghrib + timedelta(hours=1)
    freezer.move_to(start)

    with aioresponses() as m:
        mock_all(m)
        await setup_integration(hass, make_entry())
        # Nothing mid-startup.
        assert total_calls(m) == 0

        await advance(hass, freezer, start + timedelta(minutes=2))
        assert total_calls(m) == 0

        await advance(hass, freezer, start + timedelta(minutes=3, seconds=5))

    assert total_calls(m) == 5  # first run: books/surahs not cached yet
    assert hass.states.get("sensor.ilmy_hijri_date").state == "29 Dhul-Hijjah 1447"


# ── Failure handling ──────────────────────────────────────────────────────────


async def test_total_failure_retries_3_times_and_keeps_content(
    hass: HomeAssistant, hass_storage, freezer, fixed_jitter
) -> None:
    """When the services are down the batch retries on the RETRY_DELAYS_MINUTES
    backoff (3 retries max), keeping the previous content."""
    hass_storage[STORAGE_KEY] = {
        "version": STORAGE_VERSION,
        "minor_version": 1,
        "key": STORAGE_KEY,
        "data": {"content": STORED_CONTENT},
    }
    freezer.move_to(SETUP_TIME)
    maghrib = expected_times(DAY)["maghrib"]
    t0 = maghrib + timedelta(minutes=JITTER_MINUTES, seconds=5)

    with aioresponses() as m:
        m.get(HIJRI_RE, status=500, repeat=True)
        m.get(BOOKS_RE, status=500, repeat=True)
        m.get(BY_REF_RE, status=500, repeat=True)
        m.get(SURAHS_RE, status=500, repeat=True)
        m.get(WINDOW_RE, status=500, repeat=True)
        await setup_integration(hass, make_entry())

        await advance(hass, freezer, t0)
        # Initial attempt: hijri fails (non-fatal), books fails -> retry 1.
        assert count_calls(m, HIJRI_RE) == 1
        assert count_calls(m, BOOKS_RE) == 1

        # Retry 1 after RETRY_DELAYS_MINUTES[0] = 15 minutes.
        await advance(hass, freezer, t0 + timedelta(minutes=14))
        assert count_calls(m, BOOKS_RE) == 1
        await advance(hass, freezer, t0 + timedelta(minutes=16))
        assert count_calls(m, BOOKS_RE) == 2

        # Retry 2 after +30, retry 3 after +60 (cumulative).
        await advance(hass, freezer, t0 + timedelta(minutes=16 + 31))
        assert count_calls(m, BOOKS_RE) == 3
        await advance(hass, freezer, t0 + timedelta(minutes=16 + 31 + 61))
        assert count_calls(m, BOOKS_RE) == 4
        assert count_calls(m, HIJRI_RE) == 4

        # Retries exhausted: nothing more, ever.
        await advance(hass, freezer, t0 + timedelta(hours=4))
        assert count_calls(m, BOOKS_RE) == 4

    # Previous content kept.
    assert hass.states.get("sensor.ilmy_hijri_date").state == "29 Dhul-Hijjah 1447"
    assert (
        hass.states.get("sensor.ilmy_hadith_of_the_day").state
        == "Sahih al-Bukhari 100"
    )
    assert hass.states.get("sensor.ilmy_verse_of_the_day").state == "1:2"


async def test_retry_honors_retry_after_header(
    hass: HomeAssistant, freezer, fixed_jitter
) -> None:
    """A 429 Retry-After longer than the backoff delay wins."""
    freezer.move_to(SETUP_TIME)
    maghrib = expected_times(DAY)["maghrib"]
    t0 = maghrib + timedelta(minutes=JITTER_MINUTES, seconds=5)

    with aioresponses() as m:
        m.get(HIJRI_RE, status=200, payload=HIJRI_PAYLOAD, repeat=True)
        m.get(
            BOOKS_RE,
            status=429,
            headers={"Retry-After": "1800"},  # 30 minutes > 15-minute backoff
            repeat=True,
        )
        m.get(BY_REF_RE, status=200, payload=HADITH_PAYLOAD, repeat=True)
        m.get(SURAHS_RE, status=200, payload=SURAHS_PAYLOAD, repeat=True)
        m.get(WINDOW_RE, status=200, payload=WINDOW_PAYLOAD, repeat=True)
        await setup_integration(hass, make_entry())

        await advance(hass, freezer, t0)
        assert count_calls(m, BOOKS_RE) == 1

        # The default 15-minute backoff passes without a retry...
        await advance(hass, freezer, t0 + timedelta(minutes=16))
        assert count_calls(m, BOOKS_RE) == 1

        # ...the retry fires only after the 30-minute Retry-After.
        await advance(hass, freezer, t0 + timedelta(minutes=31))
        assert count_calls(m, BOOKS_RE) == 2


async def test_hijri_only_failure_marks_stale_without_retry(
    hass: HomeAssistant, hass_storage, freezer, fixed_jitter
) -> None:
    """A hijri-only failure is non-fatal: the rest of the batch completes,
    the hijri sensor keeps its value with stale=True, no retry is armed."""
    hass_storage[STORAGE_KEY] = {
        "version": STORAGE_VERSION,
        "minor_version": 1,
        "key": STORAGE_KEY,
        "data": {
            "content": STORED_CONTENT,
            "books": {"fetched_at": DAY.isoformat(), "data": BOOKS_PAYLOAD},
            "surahs": {"fetched_at": DAY.isoformat(), "data": SURAHS_PAYLOAD},
        },
    }
    freezer.move_to(SETUP_TIME)
    maghrib = expected_times(DAY)["maghrib"]
    t0 = maghrib + timedelta(minutes=JITTER_MINUTES, seconds=5)

    with aioresponses() as m:
        m.get(HIJRI_RE, status=500, repeat=True)
        m.get(BY_REF_RE, status=200, payload=HADITH_PAYLOAD, repeat=True)
        m.get(WINDOW_RE, status=200, payload=WINDOW_PAYLOAD, repeat=True)
        await setup_integration(hass, make_entry())
        await advance(hass, freezer, t0)

        assert count_calls(m, HIJRI_RE) == 1
        assert count_calls(m, BY_REF_RE) == 1
        assert count_calls(m, WINDOW_RE) == 1

        # No retry is scheduled for a hijri-only failure.
        await advance(hass, freezer, t0 + timedelta(hours=2))
        assert count_calls(m, HIJRI_RE) == 1

    hijri_state = hass.states.get("sensor.ilmy_hijri_date")
    assert hijri_state.state == "29 Dhul-Hijjah 1447"
    assert hijri_state.attributes["stale"] is True
    # The rest of the batch still updated.
    assert (
        hass.states.get("sensor.ilmy_hadith_of_the_day").state
        == "Sahih al-Bukhari 756"
    )


# ── Prayer event ──────────────────────────────────────────────────────────────


async def test_prayer_event_fires_at_prayer_time(
    hass: HomeAssistant, hass_storage, freezer
) -> None:
    """The ilmy_prayer_time event fires at a prayer time with prayer, time
    and hijri_date in the payload."""
    hass_storage[STORAGE_KEY] = {
        "version": STORAGE_VERSION,
        "minor_version": 1,
        "key": STORAGE_KEY,
        "data": {
            "last_fetch_local_date": DAY.isoformat(),
            "content": STORED_CONTENT,
        },
    }
    # 08:00 UTC: before Dhuhr (~09:21 UTC for Makkah computed in the UTC tz).
    freezer.move_to(datetime(2026, 6, 15, 8, 0, tzinfo=dt_util.UTC))
    dhuhr = expected_times(DAY)["dhuhr"]
    assert dhuhr > dt_util.utcnow()

    events = []

    with aioresponses() as m:
        mock_all(m)
        await setup_integration(hass, make_entry())
        hass.bus.async_listen(EVENT_PRAYER, events.append)

        await advance(hass, freezer, dhuhr - timedelta(seconds=30))
        assert events == []

        await advance(hass, freezer, dhuhr + timedelta(seconds=5))

    assert len(events) == 1
    assert events[0].data == {
        "prayer": "dhuhr",
        "time": dhuhr.isoformat(),
        "hijri_date": "29 Dhul-Hijjah 1447",
    }


# ── Headers ───────────────────────────────────────────────────────────────────


async def test_user_agent_sent_on_requests(
    hass: HomeAssistant, freezer, fixed_jitter
) -> None:
    """Every request to the Ilmy services carries the identifying User-Agent."""
    freezer.move_to(SETUP_TIME)
    maghrib = expected_times(DAY)["maghrib"]

    with aioresponses() as m:
        mock_all(m)
        await setup_integration(hass, make_entry())
        await advance(
            hass, freezer, maghrib + timedelta(minutes=JITTER_MINUTES, seconds=5)
        )

    assert total_calls(m) == 5
    for calls in m.requests.values():
        for call in calls:
            assert call.kwargs["headers"]["User-Agent"] == USER_AGENT


# ── Multi-book hadith rotation (all / sahihain) ────────────────────────────────

DOY = DAY.timetuple().tm_yday  # 166

SMALL_BOOKS = [
    {"slug": "bukhari", "english_title": "Sahih al-Bukhari",
     "total_hadiths": 5, "kind": "collection"},
    {"slug": "muslim", "english_title": "Sahih Muslim",
     "total_hadiths": 3, "kind": "collection"},
    # A commentary: never offered, never part of the "all" rotation.
    {"slug": "nawawi40", "english_title": "Sharh al-Arba'in",
     "total_hadiths": 4, "kind": "commentary"},
    {"slug": "abudawud", "english_title": "Sunan Abi Dawud",
     "total_hadiths": 4, "kind": "collection"},
]


def make_entry_with_book(book: str) -> MockConfigEntry:
    entry = make_entry()
    return MockConfigEntry(
        domain=DOMAIN,
        unique_id="ilmy",
        data=dict(entry.data),
        options={
            CONF_LANGUAGE: "en",
            CONF_HADITH_BOOK: book,
            CONF_ASR: 1,
            CONF_HIGH_LATITUDE: 0,
        },
    )


def seed_small_books(hass_storage) -> None:
    """Cache the small books/surahs lists so the batch is hijri+by-ref+window."""
    hass_storage[STORAGE_KEY] = {
        "version": STORAGE_VERSION,
        "minor_version": 1,
        "key": STORAGE_KEY,
        "data": {
            "books": {"fetched_at": DAY.isoformat(), "data": SMALL_BOOKS},
            "surahs": {"fetched_at": DAY.isoformat(), "data": SURAHS_PAYLOAD},
        },
    }


def by_ref_calls(m: aioresponses, slug: str, num: int) -> int:
    return sum(
        len(calls)
        for (method, url), calls in m.requests.items()
        if str(url).endswith(f"/by-ref/{slug}/{num}")
    )


async def run_daily_batch(hass, freezer, fixed_jitter, m: aioresponses, entry):
    """Set up at 08:00 and advance past the Maghrib+jitter daily batch."""
    freezer.move_to(SETUP_TIME)
    maghrib = expected_times(DAY)["maghrib"]
    await setup_integration(hass, entry)
    await advance(
        hass, freezer, maghrib + timedelta(minutes=JITTER_MINUTES, seconds=5)
    )


async def test_hadith_pick_all_collections_rotates_across_books(
    hass: HomeAssistant, hass_storage, freezer, fixed_jitter
) -> None:
    """'all' rotates over the combined ranges of the collections only (the
    commentary is excluded), in API order: 166 % 12 = 10 -> abudawud #3."""
    seed_small_books(hass_storage)
    with aioresponses() as m:
        mock_all(m)
        await run_daily_batch(hass, freezer, fixed_jitter, m,
                              make_entry_with_book("all"))

        assert by_ref_calls(m, "abudawud", 3) == 1
        assert count_calls(m, BY_REF_RE) == 1


async def test_hadith_pick_sahihain_rotates_over_bukhari_muslim(
    hass: HomeAssistant, hass_storage, freezer, fixed_jitter
) -> None:
    """'sahihain' rotates over [bukhari, muslim]: 166 % 8 = 6 -> muslim #2."""
    seed_small_books(hass_storage)
    with aioresponses() as m:
        mock_all(m)
        await run_daily_batch(hass, freezer, fixed_jitter, m,
                              make_entry_with_book("sahihain"))

        assert by_ref_calls(m, "muslim", 2) == 1
        assert count_calls(m, BY_REF_RE) == 1


async def test_hadith_pick_single_book_unchanged(
    hass: HomeAssistant, hass_storage, freezer, fixed_jitter
) -> None:
    """A single configured book behaves as before: 166 % 5 = 1 -> bukhari #2."""
    seed_small_books(hass_storage)
    with aioresponses() as m:
        mock_all(m)
        await run_daily_batch(hass, freezer, fixed_jitter, m,
                              make_entry_with_book("bukhari"))

        assert by_ref_calls(m, "bukhari", 2) == 1
        assert count_calls(m, BY_REF_RE) == 1


async def test_hadith_walk_continues_into_next_book(
    hass: HomeAssistant, hass_storage, freezer, fixed_jitter
) -> None:
    """On 404 the walk advances the id and, once the book is exhausted,
    continues into the next book of the rotation (abudawud -> bukhari #1)."""
    seed_small_books(hass_storage)
    with aioresponses() as m:
        m.get(HIJRI_RE, status=200, payload=HIJRI_PAYLOAD, repeat=True)
        # 'all' maps the day to abudawud #3; both remaining abudawud ids 404,
        # then the walk wraps into bukhari #1.
        m.get(re.compile(r".*/by-ref/abudawud/3$"), status=404)
        m.get(re.compile(r".*/by-ref/abudawud/4$"), status=404)
        m.get(re.compile(r".*/by-ref/bukhari/1$"), status=200,
              payload=HADITH_PAYLOAD)
        m.get(WINDOW_RE, status=200, payload=WINDOW_PAYLOAD, repeat=True)
        await run_daily_batch(hass, freezer, fixed_jitter, m,
                              make_entry_with_book("all"))

        assert by_ref_calls(m, "abudawud", 3) == 1
        assert by_ref_calls(m, "abudawud", 4) == 1
        assert by_ref_calls(m, "bukhari", 1) == 1

    assert (
        hass.states.get("sensor.ilmy_hadith_of_the_day").state
        == "Sahih al-Bukhari 756"
    )


async def test_books_cache_stores_kind(
    hass: HomeAssistant, freezer, fixed_jitter, hass_storage
) -> None:
    """The persistent books cache keeps the kind field, so a restart still
    distinguishes collections from commentaries."""
    books_with_kind = [
        {"slug": "bukhari", "english_title": "Sahih al-Bukhari",
         "total_hadiths": 7563, "kind": "collection"},
        {"slug": "nawawi40", "english_title": "Sharh al-Arba'in",
         "total_hadiths": 42, "kind": "commentary"},
    ]
    freezer.move_to(SETUP_TIME)
    maghrib = expected_times(DAY)["maghrib"]

    with aioresponses() as m:
        m.get(HIJRI_RE, status=200, payload=HIJRI_PAYLOAD, repeat=True)
        m.get(BOOKS_RE, status=200, payload=books_with_kind, repeat=True)
        m.get(BY_REF_RE, status=200, payload=HADITH_PAYLOAD, repeat=True)
        m.get(SURAHS_RE, status=200, payload=SURAHS_PAYLOAD, repeat=True)
        m.get(WINDOW_RE, status=200, payload=WINDOW_PAYLOAD, repeat=True)
        await setup_integration(hass, make_entry())
        await advance(
            hass, freezer, maghrib + timedelta(minutes=JITTER_MINUTES, seconds=5)
        )

    assert (
        hass_storage[STORAGE_KEY]["data"]["books"]["data"] == books_with_kind
    )
