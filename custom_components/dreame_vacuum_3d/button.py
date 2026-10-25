"""Buttons: reset consumables, empty dust bag, wash mop, dry mop."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import DreameDataUpdateCoordinator
from .device.device import DreameDevice
from .entity import DreameEntity, command

# reset_<name> action ↔ property that must be available for the button to appear.
_RESET_REQUIRES: dict[str, str] = {
    "main_brush": "main_brush_left",
    "side_brush": "side_brush_left",
    "filter": "filter_left",
    "sensor": "sensor_dirty_left",
    "mop_pad": "mop_pad_left",
    "silver_ion": "silver_ion_left",
    "detergent": "detergent_left",
    "tank_filter": "tank_filter_left",
    "squeegee": "squeegee_left",
    "deodorizer": "deodorizer_left",
}


@dataclass(frozen=True, kw_only=True)
class DreameButtonEntityDescription(ButtonEntityDescription):
    """Button that runs a device coroutine."""

    press_fn: Callable[[DreameDevice], Awaitable[object]]
    available_fn: Callable[[DreameDevice], bool]


def _make_reset(name: str) -> DreameButtonEntityDescription:
    prop = _RESET_REQUIRES[name]
    action = f"reset_{name}"

    def _available(device: DreameDevice, *, p: str = prop, a: str = action) -> bool:
        return p in device.available and a in device.caps.actions

    return DreameButtonEntityDescription(
        key=action,
        entity_category=EntityCategory.CONFIG,
        press_fn=lambda device: device.reset_consumable(name),
        available_fn=_available,
    )


BUTTONS: tuple[DreameButtonEntityDescription, ...] = (
    *(_make_reset(name) for name in _RESET_REQUIRES),
    DreameButtonEntityDescription(
        key="start_auto_empty",
        translation_key="empty",
        entity_category=EntityCategory.CONFIG,
        press_fn=lambda device: device.start_auto_empty(),
        available_fn=lambda device: "start_auto_empty" in device.caps.actions
        and "auto_empty_status" in device.available,
    ),
    DreameButtonEntityDescription(
        key="wash_mop",
        entity_category=EntityCategory.CONFIG,
        press_fn=lambda device: device.start_washing(),
        available_fn=lambda device: "start_washing" in device.caps.actions
        and "self_wash_base_status" in device.available,
    ),
    DreameButtonEntityDescription(
        key="dry_mop",
        entity_category=EntityCategory.CONFIG,
        press_fn=lambda device: device.start_drying(),
        available_fn=lambda device: "start_washing" in device.caps.actions
        and "self_wash_base_status" in device.available,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up buttons the device can actually run."""
    coordinator: DreameDataUpdateCoordinator = entry.runtime_data.coordinator
    device = coordinator.device
    async_add_entities(
        DreameButtonEntity(coordinator, description)
        for description in BUTTONS
        if description.available_fn(device)
    )


class DreameButtonEntity(DreameEntity, ButtonEntity):
    """One-shot action button."""

    entity_description: DreameButtonEntityDescription

    @command
    async def async_press(self) -> None:
        await self.entity_description.press_fn(self.device)
        await self.coordinator.async_request_refresh()
