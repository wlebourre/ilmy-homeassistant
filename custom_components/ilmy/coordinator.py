"""Data coordinator for the Ilmy integration.

Prayer times and the qibla direction are computed locally by the bundled
prayer engine; the Hijri date, hadith of the day and verse of the day are
fetched anonymously from the Ilmy services once per day, in a single batch
shortly after local Maghrib (with jitter, backoff and a persistent cache —
steady state is exactly 3 GETs per day). Everything is point-in-time
scheduled (midnight recompute, prayer events, daily batch) — there is no
polling interval.
"""

from __future__ import annotations

import logging
import random
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from functools import partial
from zoneinfo import ZoneInfo

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_LANGUAGE, CONF_LATITUDE, CONF_LONGITUDE
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.event import async_track_point_in_time
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from .api import IlmyClient, IlmyConnectionError, IlmyError
from .const import (
    BOOK_KIND_COLLECTION,
    CONF_ASR,
    CONF_HADITH_BOOK,
    CONF_HIGH_LATITUDE,
    CONF_METHOD,
    CONF_TIME_ZONE_NAME,
    DEFAULT_ASR,
    DEFAULT_HADITH_BOOK,
    DEFAULT_HIGH_LATITUDE,
    DEFAULT_LANGUAGE,
    DEFAULT_METHOD,
    DOMAIN,
    EVENT_PRAYER,
    FETCH_JITTER_MINUTES,
    FIRST_FETCH_DELAY_MINUTES,
    HADITH_404_RETRIES,
    HADITH_BOOK_ALL,
    HADITH_BOOK_SAHIHAIN,
    PRAYERS,
    RETRY_DELAYS_MINUTES,
    SAHIHAIN_SLUGS,
    STATIC_CACHE_TTL_DAYS,
    STORAGE_KEY,
    STORAGE_VERSION,
    TOTAL_AYAT,
)
from .prayer import compute_prayer_times, qibla_direction

_LOGGER = logging.getLogger(__name__)

# How far past local midnight to run the daily recompute
MIDNIGHT_BUFFER = timedelta(seconds=30)

# Local time used to anchor the daily batch when Maghrib is not computable
# (polar day/night — prayer times are None then)
FALLBACK_BATCH_TIME = time(18, 0)


@dataclass
class IlmyData:
    """Data exposed to the Ilmy entities."""

    prayer_times: dict[str, datetime | None] = field(default_factory=dict)
    hijri: dict = field(default_factory=dict)
    hijri_stale: bool = False
    qibla: dict = field(default_factory=dict)
    next_prayer: str | None = None
    next_prayer_time: datetime | None = None
    hadith: dict | None = None
    verse: dict | None = None


class IlmyCoordinator(DataUpdateCoordinator[IlmyData]):
    """Coordinates local computation and all point-in-time scheduling."""

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
        self._store: Store = Store(hass, STORAGE_VERSION, STORAGE_KEY)
        self._stored: dict = {}
        self._tomorrow_times: dict[str, datetime | None] = {}
        self._books: dict[str, dict] | None = None  # slug -> BookSummary
        self._surah_map: dict[int, dict] | None = None  # db surah id -> info

    # ── Options / location helpers ───────────────────────────────────────────

    def _location(self) -> tuple[float, float, str]:
        """Effective location: options override the config entry data."""
        options = self.config_entry.options
        data = self.config_entry.data
        return (
            options.get(CONF_LATITUDE, data[CONF_LATITUDE]),
            options.get(CONF_LONGITUDE, data[CONF_LONGITUDE]),
            options.get(CONF_TIME_ZONE_NAME, data[CONF_TIME_ZONE_NAME]),
        )

    @property
    def _tz(self) -> ZoneInfo:
        return ZoneInfo(self._location()[2])

    @property
    def tomorrow_times(self) -> dict[str, datetime | None]:
        """Prayer times computed for tomorrow (None for polar day/night)."""
        return self._tomorrow_times

    def _option(self, key: str, default):
        return self.config_entry.options.get(
            key, self.config_entry.data.get(key, default)
        )

    # ── Local computation ────────────────────────────────────────────────────

    def _compute_times(self, day: date) -> dict[str, datetime | None]:
        """Compute prayer times for one civil date with the local engine."""
        latitude, longitude, tz_name = self._location()
        times = compute_prayer_times(
            day,
            latitude,
            longitude,
            tz_name,
            method=self._option(CONF_METHOD, DEFAULT_METHOD),
            asr_factor=self._option(CONF_ASR, DEFAULT_ASR),
            high_latitude=self._option(CONF_HIGH_LATITUDE, DEFAULT_HIGH_LATITUDE),
        )
        return {name: getattr(times, name) for name in PRAYERS}

    def _compute_qibla(self) -> dict:
        """Compute the qibla direction for the configured location."""
        latitude, longitude, _ = self._location()
        info = qibla_direction(latitude, longitude)
        return {
            "direction": info.degrees,
            "compass": info.compass,
            "direction_ar": info.direction_ar,
        }

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

    @staticmethod
    def _upcoming_prayers(
        *times_per_day: dict[str, datetime | None],
    ) -> list[tuple[datetime, str]]:
        """Future (time, prayer) pairs across the given days (None skipped)."""
        now = dt_util.utcnow()
        upcoming: list[tuple[datetime, str]] = []
        for times in times_per_day:
            upcoming += [
                (dt, name)
                for name, dt in times.items()
                if dt is not None and dt > now
            ]
        return upcoming

    def _schedule_next_prayer(
        self,
        today_times: dict[str, datetime | None],
        tomorrow_times: dict[str, datetime | None],
    ) -> None:
        """Schedule the event for the soonest upcoming prayer time."""
        upcoming = self._upcoming_prayers(today_times, tomorrow_times)
        if not upcoming:
            # No computable time left (polar day/night); the midnight
            # recompute will try again with the next day's times.
            return
        next_dt, next_name = min(upcoming)
        self._track(
            partial(self._async_prayer_reached, prayer=next_name),
            next_dt,
        )

    def _refresh_next_prayer(self, data: IlmyData) -> None:
        """Update the next_prayer attributes from the current schedule."""
        upcoming = self._upcoming_prayers(data.prayer_times, self._tomorrow_times)
        if upcoming:
            data.next_prayer_time, data.next_prayer = min(upcoming)
        else:
            data.next_prayer = None
            data.next_prayer_time = None

    def _maghrib_or_fallback(
        self, times: dict[str, datetime | None], day: date
    ) -> datetime:
        """Local datetime of Maghrib on `day` (18:00 fallback when the sun
        never sets — polar day/night, so the batch still runs daily)."""
        maghrib = times.get("maghrib")
        if maghrib is not None:
            return maghrib
        return datetime.combine(day, FALLBACK_BATCH_TIME, tzinfo=self._tz)

    def _fetched_today(self, today: date, base_today: datetime) -> bool:
        """Fetch-once guard: True when today's batch already ran (restart-
        safe — HA restarts after Maghrib must never cause extra fetches)."""
        return (
            self._stored.get("last_fetch_local_date") == today.isoformat()
            and datetime.now(self._tz) >= base_today
        )

    def _schedule_daily_batch(
        self,
        today_times: dict[str, datetime | None],
        tomorrow_times: dict[str, datetime | None],
    ) -> None:
        """Arm the daily content batch at Maghrib + jitter (or tomorrow's)."""
        now_local = datetime.now(self._tz)
        today = now_local.date()
        base_today = self._maghrib_or_fallback(today_times, today)
        if (
            self._stored.get("last_fetch_local_date") is None
            and now_local >= base_today
        ):
            # First-ever setup after Maghrib: fetch once shortly after
            # startup instead of leaving the content sensors empty until
            # tomorrow's Maghrib.
            self._arm_catchup_batch()
        elif not self._fetched_today(today, base_today) and now_local < base_today:
            self._arm_batch(today, today_times)
        else:
            # Today's Maghrib has passed — either the batch already ran
            # (guard) or it gave up / never ran; wait for tomorrow's
            # Maghrib rather than hammering the services.
            tomorrow = today + timedelta(days=1)
            self._arm_batch(tomorrow, tomorrow_times)

    def _arm_catchup_batch(self) -> None:
        """Arm the first-ever batch a few minutes after setup, so it does
        not fire mid-startup and concurrent installs do not herd."""
        delay = timedelta(minutes=random.uniform(*FIRST_FETCH_DELAY_MINUTES))
        when = dt_util.utcnow() + delay
        _LOGGER.debug("First-setup content batch scheduled for %s", when.isoformat())
        self._track(partial(self._async_daily_batch, attempt=0), when)

    def _arm_batch(self, day: date, times: dict[str, datetime | None]) -> None:
        """Schedule the daily batch at Maghrib plus a random jitter, so a
        fleet of installs does not hit the services at the same second."""
        jitter = timedelta(minutes=random.uniform(0, FETCH_JITTER_MINUTES))
        when = self._maghrib_or_fallback(times, day) + jitter
        _LOGGER.debug("Daily content batch scheduled for %s", when.isoformat())
        self._track(partial(self._async_daily_batch, attempt=0), when)

    async def _async_midnight(self, _now: datetime) -> None:
        """Midnight callback: recompute the new day and re-arm everything."""
        await self.async_refresh()

    async def _async_prayer_reached(self, _now: datetime, prayer: str) -> None:
        """Fire the prayer event and reschedule for the following prayer."""
        data = self.data
        prayer_dt = data.prayer_times.get(prayer) or self._tomorrow_times.get(prayer)
        self.hass.bus.async_fire(
            EVENT_PRAYER,
            {
                "prayer": prayer,
                "time": prayer_dt.isoformat() if prayer_dt else None,
                "hijri_date": data.hijri.get("formatted"),
            },
        )
        _LOGGER.debug("Fired %s for %s", EVENT_PRAYER, prayer)

        self._refresh_next_prayer(data)
        self._schedule_next_prayer(data.prayer_times, self._tomorrow_times)
        self.async_set_updated_data(data)

    # ── Refresh (first setup + midnight recompute) ───────────────────────────

    async def _async_update_data(self) -> IlmyData:
        """Recompute times locally, then re-arm all point-in-time callbacks."""
        if not self._stored:
            self._stored = await self._store.async_load() or {}

        data = self.data
        if data is None:
            data = IlmyData()
            self._restore_content(data)

        today = datetime.now(self._tz).date()
        data.prayer_times = self._compute_times(today)
        self._tomorrow_times = self._compute_times(today + timedelta(days=1))
        data.qibla = self._compute_qibla()
        self._refresh_next_prayer(data)

        self._cancel_trackers()
        self._schedule_midnight()
        self._schedule_next_prayer(data.prayer_times, self._tomorrow_times)
        self._schedule_daily_batch(data.prayer_times, self._tomorrow_times)

        return data

    def _restore_content(self, data: IlmyData) -> None:
        """Restore the last persisted daily content after a restart, so the
        sensors show the last known content until the next batch runs."""
        content = self._stored.get("content")
        if not isinstance(content, dict):
            return
        data.hijri = content.get("hijri") or {}
        data.hijri_stale = bool(content.get("hijri_stale", False))
        data.hadith = content.get("hadith")
        data.verse = content.get("verse")
        _LOGGER.debug(
            "Restored persisted daily content from %s",
            content.get("local_date"),
        )

    # ── Daily content batch ──────────────────────────────────────────────────

    async def _async_daily_batch(self, _now: datetime, attempt: int = 0) -> None:
        """Run the daily content batch, with scheduled retries on failure."""
        data = self.data
        today = datetime.now(self._tz).date()
        base_today = self._maghrib_or_fallback(data.prayer_times, today)
        if self._fetched_today(today, base_today):
            _LOGGER.debug("Daily batch already done for %s — skipping", today)
            return
        try:
            await self._async_run_batch(today)
        except IlmyConnectionError as err:
            if attempt < len(RETRY_DELAYS_MINUTES):
                delay_minutes = float(RETRY_DELAYS_MINUTES[attempt])
                if (
                    err.retry_after is not None
                    and err.retry_after / 60 > delay_minutes
                ):
                    delay_minutes = err.retry_after / 60
                retry_at = dt_util.utcnow() + timedelta(minutes=delay_minutes)
                _LOGGER.info(
                    "Daily content batch failed (%s) — retry %d/%d at %s",
                    err,
                    attempt + 1,
                    len(RETRY_DELAYS_MINUTES),
                    retry_at.isoformat(),
                )
                self._track(
                    partial(self._async_daily_batch, attempt=attempt + 1),
                    retry_at,
                )
            else:
                _LOGGER.info(
                    "Daily content batch failed after %d retries (%s) — "
                    "keeping previous content until the next Maghrib",
                    len(RETRY_DELAYS_MINUTES),
                    err,
                )
            return

        self._stored["last_fetch_local_date"] = today.isoformat()
        # Persist the batch results so a restart shows the last known
        # content immediately instead of unknown until the next batch.
        self._stored["content"] = {
            "local_date": today.isoformat(),
            "hijri": data.hijri,
            "hijri_stale": data.hijri_stale,
            "hadith": data.hadith,
            "verse": data.verse,
        }
        await self._store.async_save(self._stored)
        _LOGGER.info("Daily content batch completed for %s", today.isoformat())
        self.async_set_updated_data(data)

    async def _async_run_batch(self, today: date) -> None:
        """Fetch hijri date, hadith and verse of the day, in that order."""
        data = self.data
        language = self._option(CONF_LANGUAGE, DEFAULT_LANGUAGE)
        day_of_year = today.timetuple().tm_yday

        # Hijri date — a failure here is non-fatal to the rest of the
        # batch: keep the last value and mark it stale.
        try:
            hijri = await self.client.async_get_hijri_date(
                today, self.hass.config.country
            )
        except IlmyError as err:
            _LOGGER.warning("Could not fetch the Hijri date: %s", err)
            data.hijri_stale = True
        else:
            if hijri:
                data.hijri = hijri
                data.hijri_stale = False
            else:
                data.hijri_stale = True

        hadith = await self._async_fetch_hadith(day_of_year, language)
        if hadith:
            data.hadith = hadith
        verse = await self._async_fetch_verse(day_of_year, language)
        if verse:
            data.verse = verse

    # ── Static caches (books / surahs, 30-day TTL, persistent) ───────────────

    def _load_static_cache(self, key: str) -> list[dict] | None:
        """Return a cached static list younger than the TTL, else None."""
        entry = self._stored.get(key)
        if not entry:
            return None
        try:
            fetched_at = date.fromisoformat(entry["fetched_at"])
        except (KeyError, TypeError, ValueError):
            return None
        if date.today() - fetched_at >= timedelta(days=STATIC_CACHE_TTL_DAYS):
            return None
        return entry.get("data")

    async def _save_static_cache(self, key: str, payload: list[dict]) -> None:
        self._stored[key] = {
            "fetched_at": date.today().isoformat(),
            "data": payload,
        }
        await self._store.async_save(self._stored)

    async def _async_books(self) -> dict[str, dict]:
        """Hadith collections by slug, from the persistent cache or fresh."""
        if self._books is None:
            cached = self._load_static_cache("books")
            if cached is not None:
                books = cached
            else:
                books = await self.client.async_get_books()
                await self._save_static_cache("books", books)
            self._books = {b["slug"]: b for b in books}
        return self._books

    async def _async_surah_map(self) -> dict[int, dict]:
        """Surah DB id -> info, from the persistent cache or fresh."""
        if self._surah_map is None:
            cached = self._load_static_cache("surahs")
            if cached is not None:
                surahs = cached
            else:
                surahs = await self.client.async_get_surahs()
                await self._save_static_cache("surahs", surahs)
            self._surah_map = {s["id"]: s for s in surahs}
        return self._surah_map

    # ── Daily picks (deterministic rotation, unchanged algorithm) ────────────

    def _rotation_books(self, books: dict[str, dict]) -> list[dict]:
        """Books the daily hadith rotates across, in rotation order."""
        book_slug = self._option(CONF_HADITH_BOOK, DEFAULT_HADITH_BOOK)
        if book_slug == HADITH_BOOK_ALL:
            selection = [
                b
                for b in books.values()
                # Older caches/services have no kind field: treat as
                # collection.
                if b.get("kind", BOOK_KIND_COLLECTION) == BOOK_KIND_COLLECTION
            ]
        elif book_slug == HADITH_BOOK_SAHIHAIN:
            selection = [books[s] for s in SAHIHAIN_SLUGS if s in books]
        else:
            book = books.get(book_slug) or next(iter(books.values()))
            selection = [book]
        return [b for b in selection if b.get("total_hadiths")]

    async def _async_fetch_hadith(
        self, day_of_year: int, language: str
    ) -> dict | None:
        """Deterministically pick and fetch today's hadith."""
        books = await self._async_books()
        selection = self._rotation_books(books)
        if not selection:
            return None

        # Map the day onto one global index across the selection's combined
        # hadith ranges (a single book is just a range of one).
        combined = sum(b["total_hadiths"] for b in selection)
        start = day_of_year % combined
        idx = 0
        while start >= selection[idx]["total_hadiths"]:
            start -= selection[idx]["total_hadiths"]
            idx += 1

        detail = None
        for _ in range(HADITH_404_RETRIES):
            book = selection[idx]
            detail = await self.client.async_get_hadith_by_ref(
                book["slug"], start + 1
            )
            if detail:
                break
            # Numbers that exist only as supplementary chains are not
            # resolvable: walk forward, continuing into the next book when
            # this one is exhausted.
            start += 1
            if start >= book["total_hadiths"]:
                idx = (idx + 1) % len(selection)
                start = 0
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
        surah_map = await self._async_surah_map()

        aya_id = (day_of_year % TOTAL_AYAT) + 1
        aya = await self.client.async_get_aya(aya_id, language)
        if not aya:
            return None

        surah = surah_map.get(aya.get("surah_id")) or {}
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
