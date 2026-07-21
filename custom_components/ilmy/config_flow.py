"""Config flow for the Ilmy integration.

Authentication is OAuth2 (authorization-code flow) against the Ilmy IAM
service: the user signs in with their ilmy account in the browser and Home
Assistant stores and refreshes the tokens.  Step 1 collects service URLs and
location, the external OAuth2 login follows, and step 2 collects the prayer
and content settings.
"""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import OptionsFlow
from homeassistant.const import CONF_LANGUAGE, CONF_LATITUDE, CONF_LONGITUDE
from homeassistant.core import callback
from homeassistant.helpers import config_entry_oauth2_flow
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
    TimeZoneSelector,
)

from .api import IlmyAuthError, IlmyClient, IlmyError
from .const import (
    CONF_ASR,
    CONF_HADITH_BOOK,
    CONF_HADITH_URL,
    CONF_HIJRI_URL,
    CONF_METHOD,
    CONF_QURAN_URL,
    CONF_TIME_ZONE_NAME,
    DEFAULT_ASR,
    DEFAULT_HADITH_BOOK,
    DEFAULT_HADITH_URL,
    DEFAULT_HIJRI_URL,
    DEFAULT_LANGUAGE,
    DEFAULT_METHOD,
    DEFAULT_QURAN_URL,
    DOMAIN,
    SUPPORTED_LANGUAGES,
)

_LOGGER = logging.getLogger(__name__)

ASR_OPTIONS = [
    SelectOptionDict(value="1", label="Shafi'i / Maliki / Hanbali"),
    SelectOptionDict(value="2", label="Hanafi"),
]


def _user_schema(hass) -> vol.Schema:
    """Service URLs + location, prefilled from the HA configuration."""
    return vol.Schema(
        {
            vol.Required(CONF_HIJRI_URL, default=DEFAULT_HIJRI_URL): TextSelector(
                TextSelectorConfig(type=TextSelectorType.URL)
            ),
            vol.Required(CONF_HADITH_URL, default=DEFAULT_HADITH_URL): TextSelector(
                TextSelectorConfig(type=TextSelectorType.URL)
            ),
            vol.Required(CONF_QURAN_URL, default=DEFAULT_QURAN_URL): TextSelector(
                TextSelectorConfig(type=TextSelectorType.URL)
            ),
            vol.Required(
                CONF_LATITUDE, default=hass.config.latitude
            ): vol.Coerce(float),
            vol.Required(
                CONF_LONGITUDE, default=hass.config.longitude
            ): vol.Coerce(float),
            vol.Required(
                CONF_TIME_ZONE_NAME, default=hass.config.time_zone
            ): TimeZoneSelector(),
        }
    )


def _normalize_settings(user_input: dict[str, Any]) -> dict[str, Any]:
    """Normalize the settings form values for storage in entry options."""
    return {
        CONF_METHOD: user_input[CONF_METHOD],
        CONF_ASR: int(user_input[CONF_ASR]),
        CONF_LANGUAGE: user_input[CONF_LANGUAGE],
        CONF_HADITH_BOOK: user_input[CONF_HADITH_BOOK],
    }


async def _async_settings_schema(
    client: IlmyClient, current: dict[str, Any]
) -> vol.Schema:
    """Build the settings schema, fetching dropdown options from the services.

    Falls back to minimal static option lists (always including the current
    value) when a service cannot be reached.
    """
    current_method = current.get(CONF_METHOD, DEFAULT_METHOD)
    try:
        methods = await client.async_get_methods()
        method_options = [
            SelectOptionDict(value=m["id"], label=m.get("name") or m["id"])
            for m in methods
        ]
    except IlmyError:
        method_options = []
    if not any(o["value"] == current_method for o in method_options):
        method_options.append(
            SelectOptionDict(value=current_method, label=current_method)
        )

    current_book = current.get(CONF_HADITH_BOOK, DEFAULT_HADITH_BOOK)
    try:
        books = await client.async_get_books()
        book_options = [
            SelectOptionDict(
                value=b["slug"], label=b.get("english_title") or b["slug"]
            )
            for b in books
            if b.get("total_hadiths")
        ]
    except IlmyError:
        book_options = []
    if not any(o["value"] == current_book for o in book_options):
        book_options.append(SelectOptionDict(value=current_book, label=current_book))

    current_language = current.get(CONF_LANGUAGE, DEFAULT_LANGUAGE)

    return vol.Schema(
        {
            vol.Required(CONF_METHOD, default=current_method): SelectSelector(
                SelectSelectorConfig(
                    options=method_options, mode=SelectSelectorMode.DROPDOWN
                )
            ),
            vol.Required(CONF_ASR, default=str(current.get(CONF_ASR, DEFAULT_ASR))): (
                SelectSelector(
                    SelectSelectorConfig(
                        options=ASR_OPTIONS, mode=SelectSelectorMode.DROPDOWN
                    )
                )
            ),
            vol.Required(CONF_LANGUAGE, default=current_language): SelectSelector(
                SelectSelectorConfig(
                    options=SUPPORTED_LANGUAGES, mode=SelectSelectorMode.DROPDOWN
                )
            ),
            vol.Required(CONF_HADITH_BOOK, default=current_book): SelectSelector(
                SelectSelectorConfig(
                    options=book_options, mode=SelectSelectorMode.DROPDOWN
                )
            ),
        }
    )


class IlmyConfigFlow(config_entry_oauth2_flow.AbstractOAuth2FlowHandler, domain=DOMAIN):
    """Handle the Ilmy config flow (OAuth2 login via the Ilmy IAM service)."""

    DOMAIN = DOMAIN
    VERSION = 1

    def __init__(self) -> None:
        """Initialize the flow."""
        super().__init__()
        self._data: dict[str, Any] = {}
        self._oauth_data: dict[str, Any] = {}

    @property
    def logger(self) -> logging.Logger:
        """Return the flow logger."""
        return _LOGGER

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> Any:
        """Step 1: service URLs and location, then external OAuth2 login."""
        if user_input is not None:
            await self.async_set_unique_id(DOMAIN)
            self._abort_if_unique_id_configured()
            self._data = user_input
            # Continue with the OAuth2 flow: redirect the browser to the
            # IAM /authorize page (sign in with the ilmy account).
            return await super().async_step_user()

        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(
                _user_schema(self.hass), user_input
            ),
        )

    async def async_oauth_create_entry(self, data: dict[str, Any]) -> Any:
        """Handle the token data from the OAuth2 callback.

        Instead of creating the entry immediately, collect the prayer and
        content settings first.
        """
        self._oauth_data = data
        return await self.async_step_settings()

    async def async_step_settings(
        self, user_input: dict[str, Any] | None = None
    ) -> Any:
        """Step 2: calculation method, Asr school, language, hadith book."""
        errors: dict[str, str] = {}
        client = self._validation_client()

        if user_input is not None:
            errors = await self._async_validate(client, user_input)
            if not errors:
                return self.async_create_entry(
                    title="Ilmy",
                    data={**self._data, **self._oauth_data},
                    options=_normalize_settings(user_input),
                )

        return self.async_show_form(
            step_id="settings",
            data_schema=await _async_settings_schema(client, user_input or {}),
            errors=errors,
        )

    def _validation_client(self) -> IlmyClient:
        """Build a client using the freshly-obtained access token directly."""
        return IlmyClient(
            async_get_clientsession(self.hass),
            None,
            hijri_url=self._data[CONF_HIJRI_URL],
            hadith_url=self._data[CONF_HADITH_URL],
            quran_url=self._data[CONF_QURAN_URL],
            static_token=self._oauth_data["token"]["access_token"],
        )

    async def _async_validate(
        self, client: IlmyClient, user_input: dict[str, Any]
    ) -> dict[str, str]:
        """Validate connectivity to all three services with the user's token."""
        errors: dict[str, str] = {}
        try:
            await client.async_get_prayer_times(
                self._data[CONF_LATITUDE],
                self._data[CONF_LONGITUDE],
                self._data[CONF_TIME_ZONE_NAME],
                user_input.get(CONF_METHOD, DEFAULT_METHOD),
                int(user_input.get(CONF_ASR, DEFAULT_ASR)),
            )
        except IlmyAuthError:
            errors["base"] = "invalid_auth"
        except IlmyError as err:
            _LOGGER.warning("hijri service validation failed: %s", err)
            errors["base"] = "cannot_connect"
            return errors
        try:
            await client.async_get_books()
            await client.async_get_surahs()
        except IlmyError as err:
            _LOGGER.warning("content service validation failed: %s", err)
            errors["base"] = "cannot_connect"
        return errors

    @staticmethod
    @callback
    def async_get_options_flow(config_entry) -> OptionsFlow:
        """Return the options flow."""
        return IlmyOptionsFlowHandler()


class IlmyOptionsFlowHandler(OptionsFlow):
    """Allow changing the settings (method, asr, language, book)."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> Any:
        """Show/process the settings form."""
        if user_input is not None:
            return self.async_create_entry(data=_normalize_settings(user_input))

        entry = self.config_entry
        implementation = (
            await config_entry_oauth2_flow.async_get_config_entry_implementation(
                self.hass, entry
            )
        )
        client = IlmyClient(
            async_get_clientsession(self.hass),
            config_entry_oauth2_flow.OAuth2Session(self.hass, entry, implementation),
            hijri_url=entry.data[CONF_HIJRI_URL],
            hadith_url=entry.data[CONF_HADITH_URL],
            quran_url=entry.data[CONF_QURAN_URL],
        )
        return self.async_show_form(
            step_id="init",
            data_schema=await _async_settings_schema(client, entry.options),
        )
