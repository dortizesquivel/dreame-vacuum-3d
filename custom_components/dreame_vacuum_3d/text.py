"""Text entities: each room's name on the robot's map, to rename it."""

from __future__ import annotations

from homeassistant.components.text import TextEntity, TextEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import DreameDataUpdateCoordinator
from .entity import command
from .live_map import LiveMap
from .rooms import DreameRoomEntity, track_rooms

MAX_NAME = 20  # characters the app lets a room's name have


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """One name per room, when the robot can rename rooms."""
    coordinator: DreameDataUpdateCoordinator = entry.runtime_data.coordinator
    device = coordinator.device
    if "update_map_data" not in device.caps.actions or "map_extend_data" not in device.caps.properties:
        return
    live_map: LiveMap = entry.runtime_data.map
    track_rooms(
        entry,
        async_add_entities,
        lambda map_id, room: [DreameRoomName(coordinator, live_map, map_id, room)],
    )


class DreameRoomName(DreameRoomEntity, TextEntity):
    """A room's name; setting it renames the room on the robot's map."""

    entity_description: TextEntityDescription

    _attr_native_min = 1
    _attr_native_max = MAX_NAME

    def __init__(self, coordinator: DreameDataUpdateCoordinator, live_map: LiveMap, map_id: int, room: str) -> None:
        description = TextEntityDescription(
            key=f"room_{map_id}_{room}_name", translation_key="room_name", entity_category=EntityCategory.CONFIG
        )
        super().__init__(coordinator, live_map, map_id, room, description)

    @property
    def native_value(self) -> str | None:
        room = self._map.rooms.get(self._room)
        return (room.get("name") or None) if room else None

    @command
    async def async_set_value(self, value: str) -> None:
        name = value.strip()
        await self.device.rename_segment(self._map_id, int(self._room), name)
        self._map.set_room_name(self._room, name)
        self._map.refresh_soon()
