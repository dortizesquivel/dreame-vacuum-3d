"""Buttons: reset consumables, empty dust bag, wash/dry the mop (and stop), clear a warning, and clean each room."""

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
from .live_map import LiveMap
from .rooms import DreameRoomEntity, track_rooms

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
    reads_map: bool = False  # the press fetches the map again, for the card and the map's entities


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


def _has_auto_empty(device: DreameDevice) -> bool:
    return "start_auto_empty" in device.caps.actions and "auto_empty_status" in device.available


def _has_self_wash(device: DreameDevice) -> bool:
    return "start_washing" in device.caps.actions and "self_wash_base_status" in device.available


# The dock's own actions are everyday controls, not configuration: they show on the device page and dashboards.
BUTTONS: tuple[DreameButtonEntityDescription, ...] = (
    *(_make_reset(name) for name in _RESET_REQUIRES),
    DreameButtonEntityDescription(
        key="start_auto_empty",
        translation_key="empty",
        press_fn=lambda device: device.start_auto_empty(),
        available_fn=_has_auto_empty,
    ),
    DreameButtonEntityDescription(
        key="wash_mop",
        press_fn=lambda device: device.start_washing(),
        available_fn=_has_self_wash,
    ),
    DreameButtonEntityDescription(
        key="stop_washing",
        press_fn=lambda device: device.stop_washing(),
        available_fn=_has_self_wash,
    ),
    DreameButtonEntityDescription(
        key="dry_mop",
        press_fn=lambda device: device.start_drying(),
        available_fn=_has_self_wash,
    ),
    DreameButtonEntityDescription(
        key="stop_drying",
        press_fn=lambda device: device.stop_drying(),
        available_fn=_has_self_wash,
    ),
    DreameButtonEntityDescription(
        key="request_map",
        entity_category=EntityCategory.DIAGNOSTIC,
        press_fn=lambda device: device.request_map(),
        reads_map=True,
        available_fn=lambda device: "request_map" in device.caps.actions and "frame_info" in device.caps.properties,
    ),
    DreameButtonEntityDescription(
        key="start_fast_mapping",
        entity_category=EntityCategory.CONFIG,
        press_fn=lambda device: device.start_fast_mapping(),
        available_fn=lambda device: "start_custom" in device.caps.actions and "status" in device.available,
    ),
    DreameButtonEntityDescription(
        key="clear_warning",
        entity_category=EntityCategory.CONFIG,
        press_fn=lambda device: device.clear_warning(),
        available_fn=lambda device: "clear_warning" in device.caps.actions and "warn_status" in device.available,
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
        DreameButtonEntity(coordinator, description) for description in BUTTONS if description.available_fn(device)
    )

    # One "Clean <room>" button per room on the robot's map, added as the map arrives.
    live_map: LiveMap = entry.runtime_data.map
    track_rooms(
        entry,
        async_add_entities,
        lambda map_id, room: [DreameRoomButton(coordinator, live_map, map_id, room)],
    )


class DreameButtonEntity(DreameEntity, ButtonEntity):
    """One-shot action button."""

    entity_description: DreameButtonEntityDescription

    @command
    async def async_press(self) -> None:
        if self.entity_description.reads_map:
            live_map: LiveMap = self.coordinator.config_entry.runtime_data.map
            await live_map.fetch_now()
            return
        await self.entity_description.press_fn(self.device)
        await self.coordinator.async_request_refresh()


class DreameRoomButton(DreameRoomEntity, ButtonEntity):
    """Clean one room of the robot's map."""

    entity_description: ButtonEntityDescription

    def __init__(self, coordinator: DreameDataUpdateCoordinator, live_map: LiveMap, map_id: int, room: str) -> None:
        description = ButtonEntityDescription(key=f"clean_room_{map_id}_{room}", translation_key="clean_room")
        super().__init__(coordinator, live_map, map_id, room, description)

    @command
    async def async_press(self) -> None:
        await self.device.clean_segment([int(self._room)])
        await self.coordinator.async_request_refresh()
