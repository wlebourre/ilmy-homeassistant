"""Sensors for the Ilmy integration."""

from __future__ import annotations

from datetime import datetime

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import IlmyConfigEntry
from .const import DOMAIN, PRAYERS
from .coordinator import IlmyCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: IlmyConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Ilmy sensors."""
    coordinator = entry.runtime_data
    entities: list[SensorEntity] = [
        IlmyPrayerTimeSensor(coordinator, entry, prayer) for prayer in PRAYERS
    ]
    entities += [
        IlmyNextPrayerSensor(coordinator, entry),
        IlmyHijriDateSensor(coordinator, entry),
        IlmyQiblaSensor(coordinator, entry),
        IlmyHadithSensor(coordinator, entry),
        IlmyVerseSensor(coordinator, entry),
    ]
    async_add_entities(entities)


class IlmyBaseSensor(CoordinatorEntity[IlmyCoordinator], SensorEntity):
    """Base class for Ilmy sensors."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: IlmyCoordinator,
        entry: IlmyConfigEntry,
        description: SensorEntityDescription,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{entry.entry_id}_{description.key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name="Ilmy",
            manufacturer="Ilmy",
        )


class IlmyPrayerTimeSensor(IlmyBaseSensor):
    """A prayer time, exposed as a timestamp sensor.

    Timestamp sensors can be used directly in `time` automation triggers
    (`at: sensor.ilmy_maghrib`), optionally with an offset.
    """

    def __init__(
        self, coordinator: IlmyCoordinator, entry: IlmyConfigEntry, prayer: str
    ) -> None:
        """Initialize the prayer time sensor."""
        super().__init__(
            coordinator,
            entry,
            SensorEntityDescription(
                key=prayer,
                translation_key=prayer,
                device_class=SensorDeviceClass.TIMESTAMP,
            ),
        )
        self._prayer = prayer

    @property
    def native_value(self) -> datetime | None:
        """Return the prayer time as an aware datetime."""
        return self.coordinator.data.prayer_times.get(self._prayer)


class IlmyNextPrayerSensor(IlmyBaseSensor):
    """The next upcoming prayer."""

    def __init__(self, coordinator: IlmyCoordinator, entry: IlmyConfigEntry) -> None:
        """Initialize the sensor."""
        super().__init__(
            coordinator,
            entry,
            SensorEntityDescription(
                key="next_prayer",
                translation_key="next_prayer",
                icon="mdi:clock-outline",
            ),
        )

    @property
    def native_value(self) -> str | None:
        """Return the name of the next prayer."""
        return self.coordinator.data.next_prayer

    @property
    def extra_state_attributes(self) -> dict:
        """Return the next prayer time."""
        dt = self.coordinator.data.next_prayer_time
        return {"time": dt.isoformat() if dt else None}


class IlmyHijriDateSensor(IlmyBaseSensor):
    """The current Hijri date."""

    def __init__(self, coordinator: IlmyCoordinator, entry: IlmyConfigEntry) -> None:
        """Initialize the sensor."""
        super().__init__(
            coordinator,
            entry,
            SensorEntityDescription(
                key="hijri_date",
                translation_key="hijri_date",
                icon="mdi:calendar-star",
            ),
        )

    @property
    def native_value(self) -> str | None:
        """Return the formatted Hijri date."""
        return self.coordinator.data.hijri.get("formatted")

    @property
    def extra_state_attributes(self) -> dict:
        """Return the Hijri date parts (stale when the last fetch failed)."""
        hijri = self.coordinator.data.hijri
        return {
            "day": hijri.get("day"),
            "month": hijri.get("month"),
            "year": hijri.get("year"),
            "month_name": hijri.get("month_name"),
            "month_name_ar": hijri.get("month_name_ar"),
            "formatted_ar": hijri.get("formatted_ar"),
            "is_ramadan": hijri.get("is_ramadan"),
            "stale": self.coordinator.data.hijri_stale,
        }


class IlmyQiblaSensor(IlmyBaseSensor):
    """The qibla direction for the configured location."""

    def __init__(self, coordinator: IlmyCoordinator, entry: IlmyConfigEntry) -> None:
        """Initialize the sensor."""
        super().__init__(
            coordinator,
            entry,
            SensorEntityDescription(
                key="qibla",
                translation_key="qibla",
                icon="mdi:compass",
                native_unit_of_measurement="°",
                suggested_display_precision=1,
            ),
        )

    @property
    def native_value(self) -> float | None:
        """Return the qibla direction in degrees from north."""
        return self.coordinator.data.qibla.get("direction")

    @property
    def extra_state_attributes(self) -> dict:
        """Return the compass direction."""
        return {
            "compass": self.coordinator.data.qibla.get("compass"),
            "direction_ar": self.coordinator.data.qibla.get("direction_ar"),
        }


class IlmyHadithSensor(IlmyBaseSensor):
    """The hadith of the day."""

    def __init__(self, coordinator: IlmyCoordinator, entry: IlmyConfigEntry) -> None:
        """Initialize the sensor."""
        super().__init__(
            coordinator,
            entry,
            SensorEntityDescription(
                key="hadith_of_the_day",
                translation_key="hadith_of_the_day",
                icon="mdi:book-open-variant",
            ),
        )

    @property
    def native_value(self) -> str | None:
        """Return the hadith reference (e.g. 'Sahih al-Bukhari 756')."""
        hadith = self.coordinator.data.hadith
        return hadith.get("reference") if hadith else None

    @property
    def extra_state_attributes(self) -> dict:
        """Return the hadith text and metadata (long text lives here, not
        in the state, which is limited to 255 characters)."""
        hadith = self.coordinator.data.hadith or {}
        return {
            "arabic": hadith.get("arabic"),
            "translation": hadith.get("translation"),
            "book": hadith.get("book"),
            "book_arabic": hadith.get("book_arabic"),
            "chapter": hadith.get("chapter"),
            "chapter_arabic": hadith.get("chapter_arabic"),
            "book_slug": hadith.get("book_slug"),
            "id_in_book": hadith.get("id_in_book"),
        }


class IlmyVerseSensor(IlmyBaseSensor):
    """The verse of the day."""

    def __init__(self, coordinator: IlmyCoordinator, entry: IlmyConfigEntry) -> None:
        """Initialize the sensor."""
        super().__init__(
            coordinator,
            entry,
            SensorEntityDescription(
                key="verse_of_the_day",
                translation_key="verse_of_the_day",
                icon="mdi:book-open-page-variant",
            ),
        )

    @property
    def native_value(self) -> str | None:
        """Return the verse reference (e.g. '2:255')."""
        verse = self.coordinator.data.verse
        return verse.get("reference") if verse else None

    @property
    def extra_state_attributes(self) -> dict:
        """Return the verse text and metadata."""
        verse = self.coordinator.data.verse or {}
        return {
            "uthmani_text": verse.get("uthmani_text"),
            "translation": verse.get("translation"),
            "surah_number": verse.get("surah_number"),
            "surah_name": verse.get("surah_name"),
            "surah_name_ar": verse.get("surah_name_ar"),
            "aya_number": verse.get("aya_number"),
            "juz": verse.get("juz"),
            "page": verse.get("page"),
        }
