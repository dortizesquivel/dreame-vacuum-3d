"""Switch entities for carpets, DND, child lock, obstacles, AI detection, dock and maps."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from homeassistant.components.switch import SwitchEntity, SwitchEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import DreameDataUpdateCoordinator
from .entity import DreameEntity, command


@dataclass(frozen=True, kw_only=True)
class DreameSwitchEntityDescription(SwitchEntityDescription):
    """Switch for a boolean / 0-1 Dreame property, or one bit of a bitmask property."""

    invert: bool = False
    prop: str | None = None  # the property, when it isn't the key
    bit: int | None = None


def _ai(key: str, bit: int) -> DreameSwitchEntityDescription:
    # Portions derived from Tasshack/dreame-vacuum (MIT), see NOTICE: DreameVacuumAIProperty bits.
    return DreameSwitchEntityDescription(
        key=key, prop="ai_obstacle_detection", bit=bit, entity_category=EntityCategory.CONFIG
    )


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
    DreameSwitchEntityDescription(
        key="resume_cleaning",
        entity_category=EntityCategory.CONFIG,
    ),
    DreameSwitchEntityDescription(
        key="customized_cleaning",
        entity_category=EntityCategory.CONFIG,
    ),
    DreameSwitchEntityDescription(
        key="multi_floor_map",
        entity_category=EntityCategory.CONFIG,
    ),
    DreameSwitchEntityDescription(key="auto_dust_collecting"),
    DreameSwitchEntityDescription(
        key="carpet_recognition",
        entity_category=EntityCategory.CONFIG,
    ),
    DreameSwitchEntityDescription(
        key="water_electrolysis",
        entity_category=EntityCategory.CONFIG,
    ),
    _ai("ai_furniture_detection", 1),
    _ai("ai_obstacle_detection", 2),
    _ai("ai_obstacle_picture", 4),
    _ai("ai_pet_detection", 16),
    _ai("ai_image_upload", 32),
    _ai("fuzzy_obstacle_detection", 256),
    _ai("pet_picture", 512),
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
        if (description.prop or description.key) in available
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
        description = self.entity_description
        value = self.device.values.get(description.prop or description.key)
        if description.bit is not None:
            value = isinstance(value, int) and value & description.bit
        is_on = bool(value)
        return (not is_on) if description.invert else is_on

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._async_set(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._async_set(False)

    @command
    async def _async_set(self, is_on: bool) -> None:
        description = self.entity_description
        key = description.prop or description.key
        # DND is a bool on the X10+; most other switches are 0/1 ints.
        if key == "dnd":
            value: bool | int = bool(is_on)
        elif description.bit is not None:
            mask = self.device.values.get(key)
            mask = mask if isinstance(mask, int) else 0
            value = mask | description.bit if is_on else mask & ~description.bit
        else:
            value = 1 if is_on else 0
        await self.device.set(key, value)
        self._attr_is_on = is_on
        self.async_write_ha_state()
        await self.coordinator.async_request_refresh()
