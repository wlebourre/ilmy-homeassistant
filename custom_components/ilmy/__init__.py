"""The Ilmy integration — prayer times, hadith and verse of the day.

Prayer times and the qibla direction are computed locally; the daily
content (Hijri date, hadith, verse) is fetched anonymously from the Ilmy
services. No account or authentication of any kind is required.
"""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import IlmyClient
from .const import CONF_HADITH_URL, CONF_HIJRI_URL, CONF_QURAN_URL
from .coordinator import IlmyCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.SENSOR]

IlmyConfigEntry = ConfigEntry[IlmyCoordinator]


async def async_setup_entry(hass: HomeAssistant, entry: IlmyConfigEntry) -> bool:
    """Set up Ilmy from a config entry."""
    client = IlmyClient(
        async_get_clientsession(hass),
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
