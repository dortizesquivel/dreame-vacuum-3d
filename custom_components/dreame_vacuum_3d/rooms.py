"""Entities for each room of the robot's map: added as the map shows the room, unavailable once it doesn't."""

from __future__ import annotations

from collections.abc import Callable, Iterable

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import callback
from homeassistant.helpers.entity import Entity, EntityDescription

from .coordinator import DreameDataUpdateCoordinator
from .entity import DreameEntity
from .live_map import LiveMap


@callback
def track_rooms(
    entry: ConfigEntry, add: Callable[[Iterable[Entity]], None], make: Callable[[int, str], Iterable[Entity]]
) -> None:
    """Add make(map id, room)'s entities for each room on the map, now and as new ones show up."""
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
            add([entity for room in new for entity in make(message["map_id"], room)])

    add_rooms()
    entry.async_on_unload(live_map.listen(add_rooms))


class DreameRoomEntity(DreameEntity):
    """One room's entity: named after the room, there while the map has it."""

    def __init__(
        self,
        coordinator: DreameDataUpdateCoordinator,
        live_map: LiveMap,
        map_id: int,
        room: str,
        description: EntityDescription,
    ) -> None:
        super().__init__(coordinator, description)
        self._map, self._map_id, self._room = live_map, map_id, room
        self._attr_translation_placeholders = {"room": live_map.rooms[room].get("name") or f"#{room}"}

    @property
    def available(self) -> bool:
        message = self._map.message
        on_map = message is not None and message["map_id"] == self._map_id and self._room in self._map.rooms
        return super().available and on_map

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(self._map.listen(self._map_changed))

    @property
    def settings(self) -> dict | None:
        """The room as the map has it now: its name, type and own settings."""
        return self._map.rooms.get(self._room) if self.available else None

    @callback
    def _map_changed(self, message: dict) -> None:
        if "rooms" in message or "map_id" in message:
            room = self._map.rooms.get(self._room)
            name = (room.get("name") if room else None) or f"#{self._room}"
            if room is not None and self._attr_translation_placeholders.get("room") != name:
                self._attr_translation_placeholders = {"room": name}
                # Home Assistant keeps the name it worked out: setting and dropping _attr_name makes it work it out again.
                self._attr_name = None
                del self._attr_name
            self.async_write_ha_state()
