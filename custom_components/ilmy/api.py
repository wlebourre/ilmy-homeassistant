"""Async API client for the Ilmy services (hijri, hadith, quran).

All three services accept the user's IAM access token
(`Authorization: Bearer <token>`), so the content each user sees follows
their account's role/permissions.  Tokens are supplied by Home Assistant's
OAuth2 session machinery, which also refreshes them automatically.
"""

from __future__ import annotations

import logging
from typing import Any

import aiohttp
from homeassistant.helpers import config_entry_oauth2_flow

REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=15)

_LOGGER = logging.getLogger(__name__)


class IlmyError(Exception):
    """Base error for Ilmy API failures."""


class IlmyConnectionError(IlmyError):
    """A service could not be reached or returned an unexpected error."""


class IlmyAuthError(IlmyError):
    """The access token was rejected (expired/revoked — re-authenticate)."""


class IlmyClient:
    """Client wrapping the three Ilmy HTTP APIs."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        oauth_session: config_entry_oauth2_flow.OAuth2Session | None,
        hijri_url: str,
        hadith_url: str,
        quran_url: str,
        static_token: str | None = None,
    ) -> None:
        """Initialize the client with base URLs (no trailing slashes).

        oauth_session may be None for anonymous (unauthenticated) access.
        static_token is a raw access token used directly, e.g. when
        validating the URLs during the config flow before the config
        entry (and its OAuth2 session) exists.
        """
        self._session = session
        self._oauth = oauth_session
        self._static_token = static_token
        self._hijri_url = hijri_url.rstrip("/")
        self._hadith_url = hadith_url.rstrip("/")
        self._quran_url = quran_url.rstrip("/")

    async def _auth_headers(self) -> dict[str, str]:
        """Return the Authorization header with a valid access token."""
        if self._static_token is not None:
            return {"Authorization": f"Bearer {self._static_token}"}
        if self._oauth is None:
            return {}
        try:
            await self._oauth.async_ensure_token_valid()
        except Exception as err:
            raise IlmyAuthError(f"could not refresh the access token: {err}") from err
        return {"Authorization": f"Bearer {self._oauth.token['access_token']}"}

    async def _get(
        self,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        service: str,
    ) -> Any:
        """GET a JSON endpoint, raising IlmyError subclasses on failure."""
        try:
            headers = await self._auth_headers()
        except IlmyAuthError:
            raise
        try:
            async with self._session.get(
                url, params=params, headers=headers, timeout=REQUEST_TIMEOUT
            ) as resp:
                if resp.status == 401:
                    raise IlmyAuthError(f"{service}: access token rejected")
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
                    raise IlmyConnectionError(f"{service}: HTTP {resp.status}")
                return await resp.json()
        except (aiohttp.ClientError, TimeoutError) as err:
            raise IlmyConnectionError(f"{service}: {err}") from err

    # ── Hijri / prayer times service ─────────────────────────────────────────

    async def async_get_prayer_times(
        self,
        latitude: float,
        longitude: float,
        time_zone: str,
        method: str,
        asr: int,
    ) -> dict[str, Any]:
        """Return today's prayer times, Hijri date and qibla direction."""
        return await self._get(
            f"{self._hijri_url}/api/v1/times",
            params={
                "lat": latitude,
                "lng": longitude,
                "tz": time_zone,
                "method": method,
                "asr": asr,
            },
            service="hijri",
        )

    async def async_get_methods(self) -> list[dict[str, Any]]:
        """Return the available calculation methods."""
        return await self._get(
            f"{self._hijri_url}/api/v1/methods",
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
