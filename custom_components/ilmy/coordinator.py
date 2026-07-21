"""Data coordinator for the Ilmy integration.

Fetches today's prayer times from the hijri service, the hadith of the day
from the hadith service and the verse of the day from the quran service.
Everything is point-in-time scheduled (midnight refresh, prayer events) —
there is no polling interval.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from functools import partial
from zoneinfo import ZoneInfo

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.event import async_track_point_in_time
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import IlmyAuthError, IlmyClient, IlmyError
from .const import (
    CONF_ASR,
    CONF_HADITH_BOOK,
    CONF_METHOD,
    CONF_TIME_ZONE_NAME,
    DEFAULT_ASR,
    DEFAULT_HADITH_BOOK,
    DEFAULT_LANGUAGE,
    DEFAULT_METHOD,
    DOMAIN,
    EVENT_PRAYER,
    HADITH_404_RETRIES,
    PRAYERS,
    TOTAL_AYAT,
)
from homeassistant.const import CONF_LANGUAGE

_LOGGER = logging.getLogger(__name__)

# How far past local midnight to run the daily refresh
MIDNIGHT_BUFFER = timedelta(seconds=30)

_FRACTION_RE = re.compile(r"\.\d+")


def _parse_datetime(value: str) -> datetime:
    """Parse an RFC3339 timestamp, tolerating Go nanosecond fractions."""
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        # datetime.fromisoformat accepts at most 6 fractional digits;
        # truncate longer fractions (Go emits nanoseconds).
        return datetime.fromisoformat(_FRACTION_RE.sub("", value, count=1))


@dataclass
class IlmyData:
    """Data exposed to the Ilmy entities."""

    prayer_times: dict[str, datetime] = field(default_factory=dict)
    hijri: dict = field(default_factory=dict)
    qibla: dict = field(default_factory=dict)
    next_prayer: str | None = None
    next_prayer_time: datetime | None = None
    hadith: dict | None = None
    verse: dict | None = None


class IlmyCoordinator(DataUpdateCoordinator[IlmyData]):
    """Coordinates fetches and all point-in-time scheduling."""

    config_entry: ConfigEntry

    def __init__(
        self, hass: HomeAssistant, config_entry: ConfigEntry, client: IlmyClient
    ) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            config_entry=config_entry,
            name=DOMAIN,
            update_interval=None,
        )
        self.client = client
        self._unsub_trackers: list = []
        self._content_date = None
        self._books: dict[str, dict] | None = None  # slug -> BookSummary
        self._surah_map: dict[int, dict] | None = None  # db surah id -> info

    # ── Options / location helpers ───────────────────────────────────────────

    @property
    def _tz(self) -> ZoneInfo:
        return ZoneInfo(self.config_entry.data[CONF_TIME_ZONE_NAME])

    def _option(self, key: str, default):
        return self.config_entry.options.get(key, default)

    # ── Scheduling ───────────────────────────────────────────────────────────

    def _track(self, action, point_in_time: datetime) -> None:
        self._unsub_trackers.append(
            async_track_point_in_time(self.hass, action, point_in_time)
        )

    @callback
    def _cancel_trackers(self) -> None:
        while self._unsub_trackers:
            self._unsub_trackers.pop()()

    @callback
    def async_shutdown(self) -> None:
        """Cancel all scheduled callbacks (called on entry unload)."""
        self._cancel_trackers()

    def _schedule_midnight(self) -> None:
        now_local = datetime.now(self._tz)
        midnight = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
        self._track(self._async_midnight, midnight + timedelta(days=1) + MIDNIGHT_BUFFER)

    def _schedule_next_prayer(self) -> None:
        """Schedule the event for the soonest upcoming prayer time."""
        now = dt_util.utcnow()
        upcoming = [
            (dt, name)
            for name, dt in self.data.prayer_times.items()
            if dt > now
        ]
        if not upcoming:
            # All of today's times have passed; the midnight refresh will
            # fetch tomorrow's times and schedule the next prayer then.
            return
        next_dt, next_name = min(upcoming)
        self._track(
            partial(self._async_prayer_reached, prayer=next_name),
            next_dt,
        )

    async def _async_midnight(self, _now: datetime) -> None:
        """Midnight callback: fetch the new day's times and content."""
        await self.async_refresh()

    async def _async_prayer_reached(self, _now: datetime, prayer: str) -> None:
        """Fire the prayer event and reschedule for the following prayer."""
        data = self.data
        prayer_dt = data.prayer_times.get(prayer)
        self.hass.bus.async_fire(
            EVENT_PRAYER,
            {
                "prayer": prayer,
                "time": prayer_dt.isoformat() if prayer_dt else None,
                "hijri_date": data.hijri.get("formatted"),
            },
        )
        _LOGGER.debug("Fired %s for %s", EVENT_PRAYER, prayer)

        now = dt_util.utcnow()
        upcoming = [(dt, name) for name, dt in data.prayer_times.items() if dt > now]
        if upcoming:
            next_dt, data.next_prayer = min(upcoming)
            data.next_prayer_time = next_dt
        else:
            data.next_prayer = None
            data.next_prayer_time = None
        self._schedule_next_prayer()
        self.async_set_updated_data(data)

    # ── Fetching ─────────────────────────────────────────────────────────────

    async def _async_update_data(self) -> IlmyData:
        """Fetch prayer times and (daily) content, then re-arm scheduling."""
        entry = self.config_entry
        try:
            response = await self.client.async_get_prayer_times(
                entry.data["latitude"],
                entry.data["longitude"],
                entry.data[CONF_TIME_ZONE_NAME],
                self._option(CONF_METHOD, DEFAULT_METHOD),
                self._option(CONF_ASR, DEFAULT_ASR),
            )
        except IlmyAuthError as err:
            # Access token rejected and unrefreshable — ask the user to
            # sign in again via the OAuth2 re-auth flow.
            raise ConfigEntryAuthFailed(str(err)) from err
        except IlmyError as err:
            raise UpdateFailed(str(err)) from err

        times = response.get("times") or {}
        data = self.data or IlmyData()
        data.prayer_times = {
            name: dt
            for name in PRAYERS
            if (raw := times.get(name)) and (dt := _parse_datetime(raw))
        }
        data.hijri = response.get("hijri") or {}
        data.qibla = response.get("qibla") or {}

        now = dt_util.utcnow()
        upcoming = [(dt, name) for name, dt in data.prayer_times.items() if dt > now]
        if upcoming:
            data.next_prayer_time, data.next_prayer = min(upcoming)
        else:
            data.next_prayer = None
            data.next_prayer_time = None

        today = datetime.now(self._tz).date()
        if self._content_date != today or data.hadith is None or data.verse is None:
            hadith, verse = await self._async_fetch_daily_content(today)
            # Keep the previous content if today's fetch failed
            data.hadith = hadith or data.hadith
            data.verse = verse or data.verse
            if hadith or verse:
                self._content_date = today

        # Re-arm all point-in-time callbacks against the fresh data
        self._cancel_trackers()
        self._schedule_midnight()
        self._schedule_next_prayer()

        return data

    async def _async_fetch_daily_content(
        self, today
    ) -> tuple[dict | None, dict | None]:
        """Fetch hadith of the day and verse of the day (best effort)."""
        day_of_year = today.timetuple().tm_yday
        language = self._option(CONF_LANGUAGE, DEFAULT_LANGUAGE)
        hadith = verse = None
        try:
            hadith = await self._async_fetch_hadith(day_of_year, language)
        except IlmyError as err:
            _LOGGER.warning("Could not fetch hadith of the day: %s", err)
        try:
            verse = await self._async_fetch_verse(day_of_year, language)
        except IlmyError as err:
            _LOGGER.warning("Could not fetch verse of the day: %s", err)
        return hadith, verse

    async def _async_fetch_hadith(
        self, day_of_year: int, language: str
    ) -> dict | None:
        """Deterministically pick and fetch today's hadith."""
        if self._books is None:
            books = await self.client.async_get_books()
            self._books = {b["slug"]: b for b in books}

        book_slug = self._option(CONF_HADITH_BOOK, DEFAULT_HADITH_BOOK)
        book = self._books.get(book_slug) or next(iter(self._books.values()))
        total = book.get("total_hadiths")
        if not total:
            return None

        start = (day_of_year % total) + 1
        detail = None
        for offset in range(HADITH_404_RETRIES):
            id_in_book = ((start - 1 + offset) % total) + 1
            detail = await self.client.async_get_hadith_by_ref(
                book["slug"], id_in_book
            )
            if detail:
                break
        if not detail:
            return None

        matn = detail.get("matn") or {}
        book_title = detail.get("book_english_title") or book.get("english_title")
        return {
            "reference": f"{book_title} {detail['id_in_book']}",
            "id_in_book": detail["id_in_book"],
            "book_slug": detail.get("book_slug"),
            "book": book_title,
            "book_arabic": detail.get("book_arabic_title"),
            "chapter": detail.get("chapter_english_title"),
            "chapter_arabic": detail.get("chapter_arabic_title"),
            "arabic": matn.get("arabic_text"),
            "translation": (matn.get("text_translations") or {}).get(language),
        }

    async def _async_fetch_verse(
        self, day_of_year: int, language: str
    ) -> dict | None:
        """Deterministically pick and fetch today's verse."""
        if self._surah_map is None:
            surahs = await self.client.async_get_surahs()
            self._surah_map = {s["id"]: s for s in surahs}

        aya_id = (day_of_year % TOTAL_AYAT) + 1
        aya = await self.client.async_get_aya(aya_id, language)
        if not aya:
            return None

        surah = self._surah_map.get(aya.get("surah_id")) or {}
        surah_number = surah.get("number")
        return {
            "reference": f"{surah_number}:{aya['aya_number']}",
            "surah_number": surah_number,
            "surah_name": surah.get("english_name"),
            "surah_name_ar": surah.get("arabic_name"),
            "aya_number": aya["aya_number"],
            "uthmani_text": aya.get("uthmani_text"),
            "translation": (aya.get("text_translations") or {}).get(language),
            "juz": aya.get("juz"),
            "page": aya.get("page"),
        }
