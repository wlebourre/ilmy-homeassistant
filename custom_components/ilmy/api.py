"""Async API client for the Ilmy services (hijri, hadith, quran).

Every endpoint used by the integration is anonymous — no account, token or
sign-in of any kind. All requests carry an identifying User-Agent so the
services can recognise (and contact) the integration.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any

import aiohttp

from .const import USER_AGENT

REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=15)

_LOGGER = logging.getLogger(__name__)


class IlmyError(Exception):
    """Base error for Ilmy API failures."""


class IlmyConnectionError(IlmyError):
    """A service could not be reached or returned an unexpected error."""

    def __init__(self, message: str, retry_after: float | None = None) -> None:
        """Initialize the error; retry_after is the server-provided delay
        (seconds) from a 429 Retry-After header, when present."""
        super().__init__(message)
        self.retry_after = retry_after


class IlmyClient:
    """Client wrapping the three Ilmy HTTP APIs (all anonymous)."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        *,
        hijri_url: str,
        hadith_url: str,
        quran_url: str,
    ) -> None:
        """Initialize the client with base URLs (no trailing slashes)."""
        self._session = session
        self._hijri_url = hijri_url.rstrip("/")
        self._hadith_url = hadith_url.rstrip("/")
        self._quran_url = quran_url.rstrip("/")

    async def _get(
        self,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        service: str,
    ) -> Any:
        """GET a JSON endpoint, raising IlmyError subclasses on failure."""
        try:
            async with self._session.get(
                url,
                params=params,
                headers={"User-Agent": USER_AGENT},
                timeout=REQUEST_TIMEOUT,
            ) as resp:
                if resp.status == 404:
                    return None
                if resp.status != 200:
                    # Do not embed the upstream body in the exception: it
                    # surfaces in logs/UI and may contain sensitive details.
                    # Log a truncated copy at debug level only.
                    body = await resp.text()
                    _LOGGER.debug(
                        "%s: HTTP %s — response body (truncated): %.200s",
                        service,
                        resp.status,
                        body,
                    )
                    raise IlmyConnectionError(
                        f"{service}: HTTP {resp.status}",
                        retry_after=_parse_retry_after(resp) if resp.status == 429 else None,
                    )
                return await resp.json()
        except (aiohttp.ClientError, TimeoutError) as err:
            raise IlmyConnectionError(f"{service}: {err}") from err

    # ── Hijri service ────────────────────────────────────────────────────────

    async def async_get_hijri_date(
        self, day: date, country: str | None = None
    ) -> dict[str, Any] | None:
        """Return the Hijri date for a Gregorian (local civil) date."""
        params: dict[str, Any] = {"date": day.isoformat()}
        if country:
            params["country"] = country.upper()
        return await self._get(
            f"{self._hijri_url}/api/v1/hijri",
            params=params,
            service="hijri",
        )

    # ── Hadith service ───────────────────────────────────────────────────────

    async def async_get_books(self) -> list[dict[str, Any]]:
        """Return the hadith collections with their hadith counts."""
        return await self._get(
            f"{self._hadith_url}/api/v1/books",
            service="hadith",
        )

    async def async_get_hadith_by_ref(
        self, book_slug: str, id_in_book: int
    ) -> dict[str, Any] | None:
        """Return a hadith by canonical reference, or None when unresolvable."""
        return await self._get(
            f"{self._hadith_url}/api/v1/hadiths/by-ref/{book_slug}/{id_in_book}",
            service="hadith",
        )

    # ── Quran service ────────────────────────────────────────────────────────

    async def async_get_surahs(self) -> list[dict[str, Any]]:
        """Return all surahs (used to map surah DB ids to numbers/names)."""
        return await self._get(
            f"{self._quran_url}/api/v1/surahs",
            service="quran",
        )

    async def async_get_aya(
        self, aya_id: int, language: str
    ) -> dict[str, Any] | None:
        """Return a single aya by global id (1..6236) via the window endpoint."""
        data = await self._get(
            f"{self._quran_url}/api/v1/quran/window",
            params={"aya_id": aya_id, "before": 0, "after": 0, "lang": language},
            service="quran",
        )
        if not data or not data.get("ayat"):
            return None
        return data["ayat"][0]


def _parse_retry_after(resp: aiohttp.ClientResponse) -> float | None:
    """Parse a Retry-After header (delta-seconds) into a float, or None."""
    value = resp.headers.get("Retry-After")
    if not value:
        return None
    try:
        return float(value.strip())
    except ValueError:
        return None
