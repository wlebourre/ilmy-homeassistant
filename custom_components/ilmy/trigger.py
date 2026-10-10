"""Offer the Ilmy prayer time automation trigger."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from functools import partial
from typing import Any

import voluptuous as vol

from homeassistant.const import CONF_OPTIONS
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.automation import move_top_level_schema_fields_to_options
from homeassistant.helpers.event import async_track_point_in_time
from homeassistant.helpers.trigger import Trigger, TriggerActionRunner, TriggerConfig
from homeassistant.helpers.typing import ConfigType
from homeassistant.util import dt as dt_util

from .const import DOMAIN, PRAYERS

_LOGGER = logging.getLogger(__name__)

TRIGGER_PRAYER_TIME = "prayer_time"

CONF_PRAYER = "prayer"
CONF_OFFSET = "offset"

PRAYER_ANY = "any"

_OPTIONS_SCHEMA_DICT = {
    vol.Required(CONF_PRAYER): vol.In([PRAYER_ANY, *PRAYERS]),
    vol.Optional(CONF_OFFSET, default=timedelta(0)): cv.time_period,
}

_CONFIG_SCHEMA = vol.Schema({vol.Required(CONF_OPTIONS): _OPTIONS_SCHEMA_DICT})


async def async_get_triggers(hass: HomeAssistant) -> dict[str, type[Trigger]]:
    """Return the triggers provided by Ilmy."""
    return {TRIGGER_PRAYER_TIME: IlmyPrayerTimeTrigger}


class IlmyPrayerTimeTrigger(Trigger):
    """Trigger at a computed prayer time, with an optional offset."""

    _options: dict[str, Any]
    _run_action: TriggerActionRunner
    _unsub_timer: CALLBACK_TYPE | None = None
    _fire_at: datetime | None = None

    @classmethod
    async def async_validate_complete_config(
        cls, hass: HomeAssistant, complete_config: ConfigType
    ) -> ConfigType:
        """Validate complete config, migrating UI top-level fields."""
        complete_config = move_top_level_schema_fields_to_options(
            complete_config, _OPTIONS_SCHEMA_DICT
        )
        return await super().async_validate_complete_config(hass, complete_config)

    @classmethod
    async def async_validate_config(
        cls, hass: HomeAssistant, config: ConfigType
    ) -> ConfigType:
        """Validate config."""
        return _CONFIG_SCHEMA(config)

    def __init__(self, hass: HomeAssistant, config: TriggerConfig) -> None:
        """Initialize trigger."""
        super().__init__(hass, config)
        assert config.options is not None
        self._options = config.options

    async def async_attach_runner(
        self, run_action: TriggerActionRunner
    ) -> CALLBACK_TYPE:
        """Attach the trigger to an action runner."""
        self._run_action = run_action
        entries = self._hass.config_entries.async_entries(DOMAIN)
        coordinator = getattr(entries[0], "runtime_data", None) if entries else None
        if coordinator is None:
            _LOGGER.error(
                "The prayer time trigger cannot be attached: no Ilmy config "
                "entry is set up"
            )
            return lambda: None
        self._coordinator = coordinator
        self._schedule()
        unsub_listener = coordinator.async_add_listener(self._schedule)

        @callback
        def _async_remove() -> None:
            self._cancel_timer()
            unsub_listener()

        return _async_remove

    def _next_occurrence(self) -> tuple[datetime, str, datetime] | None:
        """Soonest future (fire time, prayer name, prayer time) per the options."""
        prayer = self._options[CONF_PRAYER]
        offset = self._options[CONF_OFFSET]
        data = self._coordinator.data
        if data is None:
            return None
        now = dt_util.utcnow()
        candidates = [
            (dt + offset, name, dt)
            for times in (data.prayer_times, self._coordinator.tomorrow_times)
            for name, dt in times.items()
            if dt is not None
            and dt + offset > now
            and (prayer == PRAYER_ANY or name == prayer)
        ]
        return min(candidates) if candidates else None

    @callback
    def _cancel_timer(self) -> None:
        if self._unsub_timer is not None:
            self._unsub_timer()
            self._unsub_timer = None
            self._fire_at = None

    @callback
    def _schedule(self, *, force: bool = False) -> None:
        """(Re)arm the point-in-time callback for the next occurrence."""
        if not force and self._fire_at is not None and self._fire_at <= dt_util.utcnow():
            # The armed timer is due right now (e.g. the coordinator fired
            # its own prayer event at the same instant and notified
            # listeners) — let it run; it re-arms after firing.
            return
        self._cancel_timer()
        occurrence = self._next_occurrence()
        if occurrence is None:
            # No computable time left (polar day/night); the midnight
            # recompute notifies listeners and re-arms with fresh times.
            _LOGGER.debug("No upcoming prayer time to schedule the trigger on")
            return
        fire_at, prayer, prayer_dt = occurrence
        self._unsub_timer = async_track_point_in_time(
            self._hass,
            partial(self._async_time_reached, prayer=prayer, prayer_dt=prayer_dt),
            fire_at,
        )
        self._fire_at = fire_at

    @callback
    def _async_time_reached(
        self, _now: datetime, prayer: str, prayer_dt: datetime
    ) -> None:
        """Run the action and re-arm for the following occurrence."""
        data = self._coordinator.data
        self._run_action(
            {
                "prayer": prayer,
                "time": prayer_dt.isoformat(),
                "hijri_date": data.hijri.get("formatted") if data else None,
            },
            f"{prayer} prayer time",
        )
        self._schedule(force=True)
