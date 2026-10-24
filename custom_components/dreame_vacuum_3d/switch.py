"""Switch entities for carpets, DND, child lock, obstacles, mop and self-clean."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from homeassistant.components.switch import SwitchEntity, SwitchEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import DreameDataUpdateCoordinator
from .entity import DreameEntity


@dataclass(frozen=True, kw_only=True)
class DreameSwitchEntityDescription(SwitchEntityDescription):
    """Switch for a boolean / 0-1 Dreame property."""

    invert: bool = False


SWITCHES: tuple[DreameSwitchEntityDescription, ...] = (
    DreameSwitchEntityDescription(
        key="carpet_boost",
        entity_category=EntityCategory.CONFIG,
    ),
    DreameSwitchEntityDescription(
        key="dnd",
        entity_category=EntityCategory.CONFIG,
    ),
    DreameSwitchEntityDescription(
        key="child_lock",
        entity_category=EntityCategory.CONFIG,
    ),
    DreameSwitchEntityDescription(
        key="obstacle_avoidance",
        entity_category=EntityCategory.CONFIG,
    ),
    DreameSwitchEntityDescription(
        key="auto_mount_mop",
        entity_category=EntityCategory.CONFIG,
    ),
    DreameSwitchEntityDescription(
        key="self_clean",
        entity_category=EntityCategory.CONFIG,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up switches for available properties."""
    coordinator: DreameDataUpdateCoordinator = entry.runtime_data.coordinator
    available = coordinator.device.available
    async_add_entities(
        DreameSwitchEntity(coordinator, description)
        for description in SWITCHES
        if description.key in available
    )


class DreameSwitchEntity(DreameEntity, SwitchEntity):
    """On/off setting for one Dreame property."""

    entity_description: DreameSwitchEntityDescription

    def __init__(
        self,
        coordinator: DreameDataUpdateCoordinator,
        description: DreameSwitchEntityDescription,
    ) -> None:
        super().__init__(coordinator, description)
        self._attr_is_on = self._read()

    @callback
    def _handle_coordinator_update(self) -> None:
        self._attr_is_on = self._read()
        super()._handle_coordinator_update()

    def _read(self) -> bool:
        value = self.device.values.get(self.entity_description.key)
        is_on = bool(value)
        return (not is_on) if self.entity_description.invert else is_on

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._async_set(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._async_set(False)

    async def _async_set(self, is_on: bool) -> None:
        key = self.entity_description.key
        # DND is a bool on the X10+; most other switches are 0/1 ints.
        if key == "dnd":
            value: bool | int = bool(is_on)
        else:
            value = 1 if is_on else 0
        await self.device.set(key, value)
        self._attr_is_on = is_on
        self.async_write_ha_state()
        await self.coordinator.async_request_refresh()
