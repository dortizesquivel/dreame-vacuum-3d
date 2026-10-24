"""Number entities: volume and DND window (minutes from midnight)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.number import (
    NumberEntity,
    NumberEntityDescription,
    NumberMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE, EntityCategory, UnitOfTime
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import DreameDataUpdateCoordinator
from .entity import DreameEntity


def _hhmm_to_minutes(value: object) -> float | None:
    if not isinstance(value, str) or ":" not in value:
        return None
    hours, minutes = value.split(":", 1)
    try:
        return int(hours) * 60 + int(minutes)
    except ValueError:
        return None


def _minutes_to_hhmm(value: float) -> str:
    total = int(value) % (24 * 60)
    return f"{total // 60:02d}:{total % 60:02d}"


@dataclass(frozen=True, kw_only=True)
class DreameNumberEntityDescription(NumberEntityDescription):
    """Number for a numeric Dreame property (or HH:MM stored as minutes)."""

    read_fn: Callable[[object], float | None] | None = None
    write_fn: Callable[[float], object] | None = None


NUMBERS: tuple[DreameNumberEntityDescription, ...] = (
    DreameNumberEntityDescription(
        key="volume",
        native_min_value=0,
        native_max_value=100,
        native_step=1,
        mode=NumberMode.SLIDER,
        native_unit_of_measurement=PERCENTAGE,
        entity_category=EntityCategory.CONFIG,
    ),
    DreameNumberEntityDescription(
        key="dnd_start",
        translation_key="dnd_start",
        native_min_value=0,
        native_max_value=1439,
        native_step=1,
        mode=NumberMode.BOX,
        native_unit_of_measurement=UnitOfTime.MINUTES,
        entity_category=EntityCategory.CONFIG,
        read_fn=_hhmm_to_minutes,
        write_fn=_minutes_to_hhmm,
    ),
    DreameNumberEntityDescription(
        key="dnd_end",
        translation_key="dnd_end",
        native_min_value=0,
        native_max_value=1439,
        native_step=1,
        mode=NumberMode.BOX,
        native_unit_of_measurement=UnitOfTime.MINUTES,
        entity_category=EntityCategory.CONFIG,
        read_fn=_hhmm_to_minutes,
        write_fn=_minutes_to_hhmm,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up number entities for available properties."""
    coordinator: DreameDataUpdateCoordinator = entry.runtime_data.coordinator
    available = coordinator.device.available
    async_add_entities(
        DreameNumberEntity(coordinator, description)
        for description in NUMBERS
        if description.key in available
    )


class DreameNumberEntity(DreameEntity, NumberEntity):
    """Numeric setting for one Dreame property."""

    entity_description: DreameNumberEntityDescription

    def __init__(
        self,
        coordinator: DreameDataUpdateCoordinator,
        description: DreameNumberEntityDescription,
    ) -> None:
        super().__init__(coordinator, description)
        self._attr_native_value = self._read()

    @callback
    def _handle_coordinator_update(self) -> None:
        self._attr_native_value = self._read()
        super()._handle_coordinator_update()

    def _read(self) -> float | None:
        raw = self.device.values.get(self.entity_description.key)
        if self.entity_description.read_fn is not None:
            return self.entity_description.read_fn(raw)
        if isinstance(raw, (int, float)):
            return float(raw)
        return None

    async def async_set_native_value(self, value: float) -> None:
        description = self.entity_description
        payload: object = description.write_fn(value) if description.write_fn is not None else int(value)
        await self.device.set(description.key, payload)
        self._attr_native_value = value
        self.async_write_ha_state()
        await self.coordinator.async_request_refresh()
