"""Buttons: reset consumables, empty dust bag, wash mop, dry mop, and clean each room."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import DreameDataUpdateCoordinator
from .device.device import DreameDevice
from .entity import DreameEntity, command
from .live_map import LiveMap

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
        available_fn=lambda device: (
            "start_auto_empty" in device.caps.actions and "auto_empty_status" in device.available
        ),
    ),
    DreameButtonEntityDescription(
        key="wash_mop",
        entity_category=EntityCategory.CONFIG,
        press_fn=lambda device: device.start_washing(),
        available_fn=lambda device: (
            "start_washing" in device.caps.actions and "self_wash_base_status" in device.available
        ),
    ),
    DreameButtonEntityDescription(
        key="dry_mop",
        entity_category=EntityCategory.CONFIG,
        press_fn=lambda device: device.start_drying(),
        available_fn=lambda device: (
            "start_washing" in device.caps.actions and "self_wash_base_status" in device.available
        ),
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
    seen: set[str] = set()

    @callback
    def add_rooms(_message: object = None) -> None:
        message = live_map.message
        if message is None:
            return
        new = [room for room in live_map.rooms if f"{message['map_id']}_{room}" not in seen]
        seen.update(f"{message['map_id']}_{room}" for room in new)
        if new:
            async_add_entities(DreameRoomButton(coordinator, live_map, message["map_id"], room) for room in new)

    add_rooms()
    entry.async_on_unload(live_map.listen(add_rooms))


class DreameButtonEntity(DreameEntity, ButtonEntity):
    """One-shot action button."""

    entity_description: DreameButtonEntityDescription

    @command
    async def async_press(self) -> None:
        await self.entity_description.press_fn(self.device)
        await self.coordinator.async_request_refresh()


class DreameRoomButton(DreameEntity, ButtonEntity):
    """Clean one room of the robot's map."""

    entity_description: ButtonEntityDescription

    def __init__(self, coordinator: DreameDataUpdateCoordinator, live_map: LiveMap, map_id: int, room: str) -> None:
        description = ButtonEntityDescription(key=f"clean_room_{map_id}_{room}", translation_key="clean_room")
        super().__init__(coordinator, description)
        self._map, self._map_id, self._room = live_map, map_id, room
        self._attr_translation_placeholders = {"room": live_map.rooms[room].get("name") or f"#{room}"}

    @property
    def available(self) -> bool:
        message = self._map.message
        on_map = message is not None and message["map_id"] == self._map_id and self._room in self._map.rooms
        return super().available and on_map

    @command
    async def async_press(self) -> None:
        await self.device.clean_segment([int(self._room)])
        await self.coordinator.async_request_refresh()
