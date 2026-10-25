"""Time entities: the do-not-disturb window."""

from __future__ import annotations

from datetime import time

from homeassistant.components.time import TimeEntity, TimeEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import DreameDataUpdateCoordinator
from .entity import DreameEntity, command

TIMES: tuple[TimeEntityDescription, ...] = (
    TimeEntityDescription(key="dnd_start", entity_category=EntityCategory.CONFIG),
    TimeEntityDescription(key="dnd_end", entity_category=EntityCategory.CONFIG),
)


def _parse(value: object) -> time | None:
    """The robot keeps these times as "HH:MM"."""
    if not isinstance(value, str):
        return None
    try:
        return time.fromisoformat(value)
    except ValueError:
        return None


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up time entities for available properties."""
    coordinator: DreameDataUpdateCoordinator = entry.runtime_data.coordinator
    available = coordinator.device.available
    async_add_entities(
        DreameTimeEntity(coordinator, description) for description in TIMES if description.key in available
    )


class DreameTimeEntity(DreameEntity, TimeEntity):
    """A time of day kept by the robot."""

    def __init__(
        self,
        coordinator: DreameDataUpdateCoordinator,
        description: TimeEntityDescription,
    ) -> None:
        super().__init__(coordinator, description)
        self._attr_native_value = _parse(self.device.values.get(description.key))

    @callback
    def _handle_coordinator_update(self) -> None:
        self._attr_native_value = _parse(self.device.values.get(self.entity_description.key))
        super()._handle_coordinator_update()

    @command
    async def async_set_value(self, value: time) -> None:
        await self.device.set(self.entity_description.key, value.strftime("%H:%M"))
        self._attr_native_value = value
        self.async_write_ha_state()
        await self.coordinator.async_request_refresh()
