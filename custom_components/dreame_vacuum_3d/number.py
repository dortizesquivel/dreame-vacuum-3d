"""Number entities: volume."""

from __future__ import annotations

from homeassistant.components.number import (
    NumberEntity,
    NumberEntityDescription,
    NumberMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE, EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import DreameDataUpdateCoordinator
from .entity import DreameEntity, command

NUMBERS: tuple[NumberEntityDescription, ...] = (
    NumberEntityDescription(
        key="volume",
        native_min_value=0,
        native_max_value=100,
        native_step=1,
        mode=NumberMode.SLIDER,
        native_unit_of_measurement=PERCENTAGE,
        entity_category=EntityCategory.CONFIG,
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

    entity_description: NumberEntityDescription

    def __init__(
        self,
        coordinator: DreameDataUpdateCoordinator,
        description: NumberEntityDescription,
    ) -> None:
        super().__init__(coordinator, description)
        self._attr_native_value = self._read()

    @callback
    def _handle_coordinator_update(self) -> None:
        self._attr_native_value = self._read()
        super()._handle_coordinator_update()

    def _read(self) -> float | None:
        raw = self.device.values.get(self.entity_description.key)
        if isinstance(raw, (int, float)):
            return float(raw)
        return None

    @command
    async def async_set_native_value(self, value: float) -> None:
        await self.device.set(self.entity_description.key, int(value))
        self._attr_native_value = value
        self.async_write_ha_state()
        await self.coordinator.async_request_refresh()
