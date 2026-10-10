"""Config flow for the Ilmy integration.

No account or sign-in is required: prayer times are computed locally and
the Ilmy services are queried anonymously (one daily content batch). The
single step collects the service URLs, the location and the content
settings; the calculation method defaults from the HA-configured country
and can be changed later in the options flow.
"""

from __future__ import annotations

import logging
import zoneinfo
from datetime import date
from typing import Any

import aiohttp
import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, OptionsFlow
from homeassistant.const import CONF_LANGUAGE, CONF_LATITUDE, CONF_LONGITUDE
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .api import IlmyClient, IlmyError
from .const import (
    BOOK_KIND_COLLECTION,
    CONF_ASR,
    CONF_HADITH_BOOK,
    CONF_HADITH_URL,
    CONF_HIGH_LATITUDE,
    CONF_HIJRI_URL,
    CONF_METHOD,
    CONF_QURAN_URL,
    CONF_TIME_ZONE_NAME,
    DEFAULT_ASR,
    DEFAULT_HADITH_BOOK,
    DEFAULT_HADITH_URL,
    DEFAULT_HIGH_LATITUDE,
    DEFAULT_HIJRI_URL,
    DEFAULT_LANGUAGE,
    DEFAULT_QURAN_URL,
    DOMAIN,
    HADITH_BOOK_ALL,
    HADITH_BOOK_SAHIHAIN,
    LANGUAGES,
    USER_AGENT,
)
from .prayer import METHODS, method_for_country
from .url_validation import validate_service_url

_LOGGER = logging.getLogger(__name__)

BOOKS_TIMEOUT = aiohttp.ClientTimeout(total=10)

ASR_OPTIONS = [
    SelectOptionDict(value="1", label="Shafi'i / Maliki / Hanbali"),
    SelectOptionDict(value="2", label="Hanafi"),
]

HIGH_LATITUDE_OPTIONS = [
    SelectOptionDict(value="0", label="None"),
    SelectOptionDict(value="1", label="Angle-based (angle/60 of the night)"),
    SelectOptionDict(value="2", label="Middle of the night"),
    SelectOptionDict(value="3", label="One-seventh of the night"),
]

METHOD_OPTIONS = [
    SelectOptionDict(value=m.id, label=m.name) for m in METHODS.values()
]

# All IANA time zones, for the time zone dropdowns (HA has no dedicated
# time zone selector; this mirrors the core jewish_calendar approach).
TIME_ZONE_OPTIONS = sorted(zoneinfo.available_timezones())

TIME_ZONE_SELECTOR = SelectSelector(
    SelectSelectorConfig(
        options=TIME_ZONE_OPTIONS, mode=SelectSelectorMode.DROPDOWN, sort=True
    )
)

# Synthetic choices rotating across collections, always offered first.
SYNTHETIC_BOOK_OPTIONS = [
    SelectOptionDict(value=HADITH_BOOK_ALL, label="All collections"),
    SelectOptionDict(
        value=HADITH_BOOK_SAHIHAIN,
        label="The Two Sahihs — Al-Bukhari & Muslim",
    ),
]

# Well-known collection slugs, used when the hadith service cannot be
# reached to build the dropdown live.
FALLBACK_BOOK_OPTIONS = [
    *SYNTHETIC_BOOK_OPTIONS,
    SelectOptionDict(value="bukhari", label="Sahih al-Bukhari"),
    SelectOptionDict(value="muslim", label="Sahih Muslim"),
    SelectOptionDict(value="abudawud", label="Sunan Abi Dawud"),
    SelectOptionDict(value="tirmidhi", label="Jami' at-Tirmidhi"),
    SelectOptionDict(value="nasai", label="Sunan an-Nasa'i"),
    SelectOptionDict(value="ibnmajah", label="Sunan Ibn Majah"),
    SelectOptionDict(value="malik", label="Muwatta Malik"),
    SelectOptionDict(value="ahmed", label="Musnad Ahmad"),
    SelectOptionDict(value="darimi", label="Sunan ad-Darimi"),
    SelectOptionDict(value="mustadrak", label="Al-Mustadrak — Al-Hakim"),
    SelectOptionDict(value="bayhaqi", label="Sunan al-Bayhaqi"),
]


async def _async_book_options(
    session: aiohttp.ClientSession, hadith_url: str, current: str
) -> list[SelectOptionDict]:
    """Hadith book dropdown options, fetched live with a static fallback.

    Only collections are offered (commentaries/grading books are skipped),
    plus the synthetic multi-collection choices.
    """
    url = f"{hadith_url.rstrip('/')}/api/v1/books"
    try:
        async with session.get(
            url, headers={"User-Agent": USER_AGENT}, timeout=BOOKS_TIMEOUT
        ) as resp:
            if resp.status == 200:
                books = await resp.json()
                options = [
                    SelectOptionDict(
                        value=b["slug"], label=b.get("english_title") or b["slug"]
                    )
                    for b in books
                    # Older services have no kind field: treat as collection.
                    if b.get("total_hadiths")
                    and b.get("kind", BOOK_KIND_COLLECTION) == BOOK_KIND_COLLECTION
                ]
                if options:
                    return [*SYNTHETIC_BOOK_OPTIONS, *options]
    except (aiohttp.ClientError, TimeoutError) as err:
        _LOGGER.debug("Could not fetch hadith book list: %s", err)
    options = list(FALLBACK_BOOK_OPTIONS)
    if not any(o["value"] == current for o in options):
        options.append(SelectOptionDict(value=current, label=current))
    return options


class IlmyConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the Ilmy config flow (no authentication required)."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> Any:
        """Single step: service URLs, location and content settings."""
        errors: dict[str, str] = {}
        if user_input is not None:
            # Anonymous daily queries are sent to these URLs; https is
            # required except for localhost/RFC-1918 (self-hosted) hosts.
            for key in (CONF_HIJRI_URL, CONF_HADITH_URL, CONF_QURAN_URL):
                error = validate_service_url(user_input[key])
                if error is not None:
                    errors[key] = error
            if not errors:
                await self.async_set_unique_id(DOMAIN)
                self._abort_if_unique_id_configured()
                errors = await self._async_validate_connectivity(user_input)
            if not errors:
                return self.async_create_entry(
                    title="Ilmy",
                    data={
                        CONF_HIJRI_URL: user_input[CONF_HIJRI_URL],
                        CONF_HADITH_URL: user_input[CONF_HADITH_URL],
                        CONF_QURAN_URL: user_input[CONF_QURAN_URL],
                        CONF_LATITUDE: user_input[CONF_LATITUDE],
                        CONF_LONGITUDE: user_input[CONF_LONGITUDE],
                        CONF_TIME_ZONE_NAME: user_input[CONF_TIME_ZONE_NAME],
                        # Not asked in this step — derived from the HA
                        # country, changeable in the options flow.
                        CONF_METHOD: method_for_country(self.hass.config.country),
                    },
                    options={
                        CONF_LANGUAGE: user_input[CONF_LANGUAGE],
                        CONF_HADITH_BOOK: user_input[CONF_HADITH_BOOK],
                    },
                )

        hadith_url = (user_input or {}).get(CONF_HADITH_URL, DEFAULT_HADITH_URL)
        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(
                await self._async_user_schema(hadith_url), user_input
            ),
            errors=errors,
        )

    async def _async_user_schema(self, hadith_url: str) -> vol.Schema:
        """Build the user-step schema (book options fetched live)."""
        book_options = await _async_book_options(
            async_get_clientsession(self.hass), hadith_url, DEFAULT_HADITH_BOOK
        )
        return vol.Schema(
            {
                vol.Required(CONF_HIJRI_URL, default=DEFAULT_HIJRI_URL): TextSelector(
                    TextSelectorConfig(type=TextSelectorType.URL)
                ),
                vol.Required(
                    CONF_HADITH_URL, default=DEFAULT_HADITH_URL
                ): TextSelector(TextSelectorConfig(type=TextSelectorType.URL)),
                vol.Required(CONF_QURAN_URL, default=DEFAULT_QURAN_URL): TextSelector(
                    TextSelectorConfig(type=TextSelectorType.URL)
                ),
                vol.Required(
                    CONF_LATITUDE, default=self.hass.config.latitude
                ): vol.Coerce(float),
                vol.Required(
                    CONF_LONGITUDE, default=self.hass.config.longitude
                ): vol.Coerce(float),
                vol.Required(
                    CONF_TIME_ZONE_NAME, default=self.hass.config.time_zone
                ): TIME_ZONE_SELECTOR,
                vol.Required(CONF_LANGUAGE, default=DEFAULT_LANGUAGE): SelectSelector(
                    SelectSelectorConfig(
                        options=LANGUAGES, mode=SelectSelectorMode.DROPDOWN
                    )
                ),
                vol.Required(
                    CONF_HADITH_BOOK, default=DEFAULT_HADITH_BOOK
                ): SelectSelector(
                    SelectSelectorConfig(
                        options=book_options, mode=SelectSelectorMode.DROPDOWN
                    )
                ),
            }
        )

    async def _async_validate_connectivity(
        self, user_input: dict[str, Any]
    ) -> dict[str, str]:
        """One anonymous GET per service (/hijri, /books, /surahs)."""
        client = IlmyClient(
            async_get_clientsession(self.hass),
            hijri_url=user_input[CONF_HIJRI_URL],
            hadith_url=user_input[CONF_HADITH_URL],
            quran_url=user_input[CONF_QURAN_URL],
        )
        try:
            await client.async_get_hijri_date(date.today(), self.hass.config.country)
            await client.async_get_books()
            await client.async_get_surahs()
        except IlmyError as err:
            _LOGGER.warning("Service validation failed: %s", err)
            return {"base": "cannot_connect"}
        return {}

    @staticmethod
    @callback
    def async_get_options_flow(config_entry) -> OptionsFlow:
        """Return the options flow."""
        return IlmyOptionsFlowHandler()


class IlmyOptionsFlowHandler(OptionsFlow):
    """Allow changing the settings, including the location."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> Any:
        """Show/process the settings form."""
        entry = self.config_entry

        if user_input is not None:
            return self.async_create_entry(
                data={
                    CONF_METHOD: user_input[CONF_METHOD],
                    CONF_ASR: int(user_input[CONF_ASR]),
                    CONF_HIGH_LATITUDE: int(user_input[CONF_HIGH_LATITUDE]),
                    CONF_LANGUAGE: user_input[CONF_LANGUAGE],
                    CONF_HADITH_BOOK: user_input[CONF_HADITH_BOOK],
                    CONF_LATITUDE: user_input[CONF_LATITUDE],
                    CONF_LONGITUDE: user_input[CONF_LONGITUDE],
                    CONF_TIME_ZONE_NAME: user_input[CONF_TIME_ZONE_NAME],
                }
            )

        current_method = entry.options.get(
            CONF_METHOD, entry.data.get(CONF_METHOD, method_for_country(None))
        )
        current_book = entry.options.get(CONF_HADITH_BOOK, DEFAULT_HADITH_BOOK)
        current_language = entry.options.get(CONF_LANGUAGE, DEFAULT_LANGUAGE)
        current_latitude = entry.options.get(
            CONF_LATITUDE, entry.data[CONF_LATITUDE]
        )
        current_longitude = entry.options.get(
            CONF_LONGITUDE, entry.data[CONF_LONGITUDE]
        )
        current_tz = entry.options.get(
            CONF_TIME_ZONE_NAME, entry.data[CONF_TIME_ZONE_NAME]
        )

        book_options = await _async_book_options(
            async_get_clientsession(self.hass),
            entry.data[CONF_HADITH_URL],
            current_book,
        )
        if not any(o["value"] == current_book for o in book_options):
            book_options.append(
                SelectOptionDict(value=current_book, label=current_book)
            )

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_METHOD, default=current_method): SelectSelector(
                        SelectSelectorConfig(
                            options=METHOD_OPTIONS, mode=SelectSelectorMode.DROPDOWN
                        )
                    ),
                    vol.Required(
                        CONF_ASR, default=str(entry.options.get(CONF_ASR, DEFAULT_ASR))
                    ): SelectSelector(
                        SelectSelectorConfig(
                            options=ASR_OPTIONS, mode=SelectSelectorMode.DROPDOWN
                        )
                    ),
                    vol.Required(
                        CONF_HIGH_LATITUDE,
                        default=str(
                            entry.options.get(CONF_HIGH_LATITUDE, DEFAULT_HIGH_LATITUDE)
                        ),
                    ): SelectSelector(
                        SelectSelectorConfig(
                            options=HIGH_LATITUDE_OPTIONS,
                            mode=SelectSelectorMode.DROPDOWN,
                        )
                    ),
                    vol.Required(CONF_LANGUAGE, default=current_language): (
                        SelectSelector(
                            SelectSelectorConfig(
                                options=LANGUAGES, mode=SelectSelectorMode.DROPDOWN
                            )
                        )
                    ),
                    vol.Required(CONF_HADITH_BOOK, default=current_book): (
                        SelectSelector(
                            SelectSelectorConfig(
                                options=book_options, mode=SelectSelectorMode.DROPDOWN
                            )
                        )
                    ),
                    vol.Required(
                        CONF_LATITUDE, default=current_latitude
                    ): vol.Coerce(float),
                    vol.Required(
                        CONF_LONGITUDE, default=current_longitude
                    ): vol.Coerce(float),
                    vol.Required(
                        CONF_TIME_ZONE_NAME, default=current_tz
                    ): TIME_ZONE_SELECTOR,
                }
            ),
        )
