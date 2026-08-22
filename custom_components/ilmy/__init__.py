"""The Ilmy integration — prayer times, hadith and verse of the day.

Authentication is OAuth2 (authorization-code flow) against the Ilmy IAM
service: users sign in with their ilmy account during the config flow and
Home Assistant refreshes the tokens automatically.
"""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import config_entry_oauth2_flow
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import IlmyClient
from .const import (
    CONF_HADITH_URL,
    CONF_HIJRI_URL,
    CONF_QURAN_URL,
    DOMAIN,
    OAUTH_AUTHORIZE_URL,
    OAUTH_CLIENT_ID,
    OAUTH_TOKEN_URL,
)
from .coordinator import IlmyCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.SENSOR]

IlmyConfigEntry = ConfigEntry[IlmyCoordinator]


class IlmyOAuth2Implementation(
    config_entry_oauth2_flow.LocalOAuth2ImplementationWithPkce
):
    """OAuth2 implementation pointing at the Ilmy IAM service.

    Sends a PKCE S256 code challenge on the authorize request and the
    matching code_verifier on the token exchange — requires Home Assistant
    2025.4+ (the first release shipping LocalOAuth2ImplementationWithPkce).

    Public client — no secret; IAM identifies the client by client_id and
    validates the redirect_uri against its registered allowlist.
    """

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialize the implementation with the IAM endpoints."""
        super().__init__(
            hass,
            DOMAIN,
            OAUTH_CLIENT_ID,
            OAUTH_AUTHORIZE_URL,
            OAUTH_TOKEN_URL,
            client_secret="",  # public client — no client secret
            code_verifier_length=128,
        )

    @property
    def name(self) -> str:
        """Display name of the implementation."""
        return "Ilmy"


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Register the OAuth2 implementation so the config flow can use it."""
    config_entry_oauth2_flow.async_register_implementation(
        hass, DOMAIN, IlmyOAuth2Implementation(hass)
    )
    return True


async def async_setup_entry(hass: HomeAssistant, entry: IlmyConfigEntry) -> bool:
    """Set up Ilmy from a config entry."""
    implementation = (
        await config_entry_oauth2_flow.async_get_config_entry_implementation(
            hass, entry
        )
    )
    oauth_session = config_entry_oauth2_flow.OAuth2Session(hass, entry, implementation)

    # Fail fast (and trigger re-auth) when the tokens are no longer usable.
    try:
        await oauth_session.async_ensure_token_valid()
    except Exception as err:
        raise ConfigEntryAuthFailed(
            "could not refresh the Ilmy access token — please re-authenticate"
        ) from err

    client = IlmyClient(
        async_get_clientsession(hass),
        oauth_session,
        hijri_url=entry.data[CONF_HIJRI_URL],
        hadith_url=entry.data[CONF_HADITH_URL],
        quran_url=entry.data[CONF_QURAN_URL],
    )
    coordinator = IlmyCoordinator(hass, entry, client)
    await coordinator.async_config_entry_first_refresh()

    entry.runtime_data = coordinator
    entry.async_on_unload(coordinator.async_shutdown)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: IlmyConfigEntry) -> bool:
    """Unload an Ilmy config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def _async_update_listener(hass: HomeAssistant, entry: IlmyConfigEntry) -> None:
    """Reload the entry when options change."""
    await hass.config_entries.async_reload(entry.entry_id)
