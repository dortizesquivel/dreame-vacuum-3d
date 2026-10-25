"""Select entities for mode, suction, water, auto-empty and mop wash."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import IntEnum

from homeassistant.components.select import SelectEntity, SelectEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import DreameDataUpdateCoordinator
from .device.enums import (
    CarpetCleaning,
    CleaningMode,
    MopWashLevel,
    SuctionLevel,
    WaterVolume,
    parse_enum,
)
from .entity import DreameEntity, command


def _options(enum_cls: type[IntEnum]) -> list[str]:
    return [m.name.lower() for m in enum_cls if m.name != "UNKNOWN"]


@dataclass(frozen=True, kw_only=True)
class DreameSelectEntityDescription(SelectEntityDescription):
    """Select backed by a property (or a custom current/set pair)."""

    current_fn: Callable[[DreameDataUpdateCoordinator], str | None] | None = None
    select_fn: Callable[[DreameDataUpdateCoordinator, str], Awaitable[None]] | None = None
    enum_cls: type[IntEnum] | None = None


async def _set_enum(
    coordinator: DreameDataUpdateCoordinator, key: str, enum_cls: type[IntEnum], option: str
) -> None:
    member = enum_cls[option.upper()]
    await coordinator.device.set(key, member.value)


def _current_enum(coordinator: DreameDataUpdateCoordinator, key: str, enum_cls: type[IntEnum]) -> str | None:
    parsed = parse_enum(enum_cls, coordinator.device.values.get(key))
    if parsed is None or parsed.name == "UNKNOWN":
        return None
    return parsed.name.lower()


def _cleaning_mode_option(coordinator: DreameDataUpdateCoordinator) -> str | None:
    mode = coordinator.device.cleaning_mode()
    if mode is None:
        return None
    try:
        return CleaningMode(mode).name.lower()
    except ValueError:
        return None


SELECTS: tuple[DreameSelectEntityDescription, ...] = (
    DreameSelectEntityDescription(
        key="cleaning_mode",
        options=_options(CleaningMode),
        current_fn=_cleaning_mode_option,
        select_fn=lambda c, option: c.device.set_cleaning_mode(CleaningMode[option.upper()].value),
    ),
    DreameSelectEntityDescription(
        key="suction_level",
        options=_options(SuctionLevel),
        enum_cls=SuctionLevel,
    ),
    DreameSelectEntityDescription(
        key="water_volume",
        options=_options(WaterVolume),
        enum_cls=WaterVolume,
    ),
    DreameSelectEntityDescription(
        key="auto_empty_frequency",
        options=["1", "2", "3"],
        entity_category=EntityCategory.CONFIG,
        current_fn=lambda c: (
            str(v)
            if isinstance((v := c.device.values.get("auto_empty_frequency")), int) and 1 <= v <= 3
            else None
        ),
        select_fn=lambda c, option: c.device.set("auto_empty_frequency", int(option)),
    ),
    DreameSelectEntityDescription(
        key="mop_wash_level",
        options=_options(MopWashLevel),
        enum_cls=MopWashLevel,
        entity_category=EntityCategory.CONFIG,
    ),
    DreameSelectEntityDescription(
        key="carpet_cleaning",
        options=_options(CarpetCleaning),
        enum_cls=CarpetCleaning,
        entity_category=EntityCategory.CONFIG,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up selects for available properties."""
    coordinator: DreameDataUpdateCoordinator = entry.runtime_data.coordinator
    available = coordinator.device.available
    async_add_entities(
        DreameSelectEntity(coordinator, description)
        for description in SELECTS
        if description.key in available
    )


class DreameSelectEntity(DreameEntity, SelectEntity):
    """Select for one Dreame setting."""

    entity_description: DreameSelectEntityDescription

    def __init__(
        self,
        coordinator: DreameDataUpdateCoordinator,
        description: DreameSelectEntityDescription,
    ) -> None:
        super().__init__(coordinator, description)
        self._attr_current_option = self._read()

    @callback
    def _handle_coordinator_update(self) -> None:
        self._attr_current_option = self._read()
        super()._handle_coordinator_update()

    def _read(self) -> str | None:
        description = self.entity_description
        if description.current_fn is not None:
            return description.current_fn(self.coordinator)
        if description.enum_cls is not None:
            return _current_enum(self.coordinator, description.key, description.enum_cls)
        return None

    @command
    async def async_select_option(self, option: str) -> None:
        description = self.entity_description
        if description.select_fn is not None:
            await description.select_fn(self.coordinator, option)
        elif description.enum_cls is not None:
            await _set_enum(self.coordinator, description.key, description.enum_cls, option)
        else:
            raise HomeAssistantError(f"cannot set {description.key}")
        self._attr_current_option = option
        self.async_write_ha_state()
        await self.coordinator.async_request_refresh()
