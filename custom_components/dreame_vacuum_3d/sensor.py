"""Sensors: status, battery, cleaning stats, consumables, dock, and channel."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import IntEnum
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    PERCENTAGE,
    EntityCategory,
    UnitOfArea,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import StateType
from homeassistant.util import dt as dt_util

from .coordinator import DreameDataUpdateCoordinator
from .device.enums import (
    AutoEmptyStatus,
    CleanWaterTankStatus,
    DirtyWaterTankStatus,
    DreameVacuumState,
    DustBagStatus,
    DustCollection,
    ErrorCode,
    LowWaterWarning,
    RelocationStatus,
    SelfWashBaseStatus,
    Status,
    TaskStatus,
    parse_enum,
)
from .entity import DreameEntity
from .history import CleaningHistory
from .live_map import LiveMap


def _enum_options(enum_cls: type[IntEnum]) -> list[str]:
    return [member.name.lower() for member in enum_cls if member.name != "UNKNOWN"]


def _enum_name(enum_cls: type[IntEnum], value: object) -> str | None:
    parsed = parse_enum(enum_cls, value)
    if parsed is None:
        return None
    return parsed.name.lower()


@dataclass(frozen=True, kw_only=True)
class DreameSensorEntityDescription(SensorEntityDescription):
    """Sensor tied to a device property key (or synthetic channel)."""

    value_fn: Callable[[dict[str, Any], str], StateType] | None = None
    always: bool = False  # True for diagnostic channel (not a MIoT property)


def _enum_sensor(
    key: str, enum_cls: type[IntEnum], category: EntityCategory | None = EntityCategory.DIAGNOSTIC
) -> DreameSensorEntityDescription:
    return DreameSensorEntityDescription(
        key=key,
        device_class=SensorDeviceClass.ENUM,
        options=_enum_options(enum_cls),
        entity_category=category,
        value_fn=lambda values, k: _enum_name(enum_cls, values.get(k)),
    )


def _raw(values: dict[str, Any], key: str) -> StateType:
    value = values.get(key)
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float, str)) or value is None:
        return value
    return str(value)


SENSORS: tuple[DreameSensorEntityDescription, ...] = (
    _enum_sensor("state", DreameVacuumState, None),
    _enum_sensor("status", Status, None),
    _enum_sensor("task_status", TaskStatus, None),
    _enum_sensor("error", ErrorCode),
    _enum_sensor("relocation_status", RelocationStatus),
    _enum_sensor("self_wash_base_status", SelfWashBaseStatus),
    _enum_sensor("auto_empty_status", AutoEmptyStatus),
    _enum_sensor("dust_collection", DustCollection),
    _enum_sensor("low_water_warning", LowWaterWarning),
    DreameSensorEntityDescription(
        key="battery_level",
        device_class=SensorDeviceClass.BATTERY,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=PERCENTAGE,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    DreameSensorEntityDescription(
        key="cleaning_time",
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTime.MINUTES,
    ),
    DreameSensorEntityDescription(
        key="cleaned_area",
        device_class=SensorDeviceClass.AREA,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfArea.SQUARE_METERS,
    ),
    DreameSensorEntityDescription(
        key="total_cleaning_time",
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.TOTAL_INCREASING,
        native_unit_of_measurement=UnitOfTime.MINUTES,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    DreameSensorEntityDescription(
        key="total_cleaned_area",
        device_class=SensorDeviceClass.AREA,
        state_class=SensorStateClass.TOTAL_INCREASING,
        native_unit_of_measurement=UnitOfArea.SQUARE_METERS,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    DreameSensorEntityDescription(
        key="cleaning_count",
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    DreameSensorEntityDescription(
        key="main_brush_left",
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=PERCENTAGE,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    DreameSensorEntityDescription(
        key="main_brush_time_left",
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTime.HOURS,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    DreameSensorEntityDescription(
        key="side_brush_left",
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=PERCENTAGE,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    DreameSensorEntityDescription(
        key="side_brush_time_left",
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTime.HOURS,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    DreameSensorEntityDescription(
        key="filter_left",
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=PERCENTAGE,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    DreameSensorEntityDescription(
        key="filter_time_left",
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTime.HOURS,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    DreameSensorEntityDescription(
        key="sensor_dirty_left",
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=PERCENTAGE,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    DreameSensorEntityDescription(
        key="sensor_dirty_time_left",
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTime.HOURS,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    DreameSensorEntityDescription(
        key="mop_pad_left",
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=PERCENTAGE,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    DreameSensorEntityDescription(
        key="mop_pad_time_left",
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTime.HOURS,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    DreameSensorEntityDescription(
        key="silver_ion_left",
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=PERCENTAGE,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    DreameSensorEntityDescription(
        key="silver_ion_time_left",
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTime.HOURS,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    DreameSensorEntityDescription(
        key="detergent_left",
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=PERCENTAGE,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    DreameSensorEntityDescription(
        key="detergent_time_left",
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTime.HOURS,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    _enum_sensor("clean_water_tank_status", CleanWaterTankStatus),
    _enum_sensor("dirty_water_tank_status", DirtyWaterTankStatus),
    _enum_sensor("dust_bag_status", DustBagStatus),
    DreameSensorEntityDescription(
        key="channel",
        entity_category=EntityCategory.DIAGNOSTIC,
        device_class=SensorDeviceClass.ENUM,
        options=["local", "cloud"],
        always=True,
        value_fn=lambda _values, _key: None,  # filled from coordinator.channel
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up sensors that the robot actually exposes (plus diagnostic channel)."""
    coordinator: DreameDataUpdateCoordinator = entry.runtime_data.coordinator
    available = coordinator.device.available
    entities: list[SensorEntity] = [
        DreameSensorEntity(coordinator, description)
        for description in SENSORS
        if description.always or description.key in available
    ]
    live_map: LiveMap = entry.runtime_data.map
    history: CleaningHistory = entry.runtime_data.history
    entities.append(DreameRoomSensor(coordinator, live_map))
    entities.append(DreameLastCleaningSensor(coordinator, history, live_map))
    async_add_entities(entities)

    # When each room of the robot's map was last cleaned, added as the map arrives (like its "Clean" buttons).
    seen: set[str] = set()

    @callback
    def add_rooms(_message: object = None) -> None:
        message = live_map.message
        if message is None:
            return
        new = [room for room in live_map.rooms if f"{message['map_id']}_{room}" not in seen]
        seen.update(f"{message['map_id']}_{room}" for room in new)
        if new:
            async_add_entities(
                DreameRoomLastCleanedSensor(coordinator, history, live_map, message["map_id"], room) for room in new
            )

    add_rooms()
    entry.async_on_unload(live_map.listen(add_rooms))


class DreameSensorEntity(DreameEntity, SensorEntity):
    """One Dreame property (or the transport channel) as a sensor."""

    entity_description: DreameSensorEntityDescription

    def __init__(
        self,
        coordinator: DreameDataUpdateCoordinator,
        description: DreameSensorEntityDescription,
    ) -> None:
        super().__init__(coordinator, description)
        self._attr_native_value = self._read()

    @callback
    def _handle_coordinator_update(self) -> None:
        self._attr_native_value = self._read()
        super()._handle_coordinator_update()

    def _read(self) -> StateType:
        description = self.entity_description
        if description.key == "channel":
            return self.coordinator.channel
        if description.value_fn is not None:
            return description.value_fn(self.device.values, description.key)
        return _raw(self.device.values, description.key)


class DreameRoomSensor(DreameEntity, SensorEntity):
    """The room the robot is in, from its map."""

    entity_description: SensorEntityDescription

    def __init__(self, coordinator: DreameDataUpdateCoordinator, live_map: LiveMap) -> None:
        super().__init__(coordinator, SensorEntityDescription(key="current_room"))
        self._map = live_map

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(self._map.listen(lambda _message: self.async_write_ha_state()))

    @property
    def native_value(self) -> str | None:
        room = (self._map.pose or {}).get("room")
        if room is None:
            return None
        return self._map.rooms.get(str(room), {}).get("name") or str(room)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"segment_id": (self._map.pose or {}).get("room")}


class DreameLastCleaningSensor(DreameEntity, SensorEntity):
    """When the robot last cleaned, with how long, how much and which rooms (from the cloud's history)."""

    entity_description: SensorEntityDescription

    def __init__(self, coordinator: DreameDataUpdateCoordinator, history: CleaningHistory, live_map: LiveMap) -> None:
        super().__init__(
            coordinator, SensorEntityDescription(key="last_cleaning", device_class=SensorDeviceClass.TIMESTAMP)
        )
        self._history, self._map = history, live_map

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(self._history.listen(self.async_write_ha_state))

    @property
    def native_value(self) -> datetime | None:
        last = self._history.cleanings[0] if self._history.cleanings else None
        return dt_util.utc_from_timestamp(last["start"]) if last else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        if not self._history.cleanings:
            return {}
        last = self._history.cleanings[0]
        rooms = [self._map.rooms.get(str(r), {}).get("name") or f"#{r}" for r in last["rooms"]]
        return {"duration": last["duration"], "area": last["area"], "completed": last["completed"], "rooms": rooms}


class DreameRoomLastCleanedSensor(DreameEntity, SensorEntity):
    """When the robot last cleaned one room of its map, and how often it did in the last week."""

    entity_description: SensorEntityDescription

    def __init__(
        self,
        coordinator: DreameDataUpdateCoordinator,
        history: CleaningHistory,
        live_map: LiveMap,
        map_id: int,
        room: str,
    ) -> None:
        description = SensorEntityDescription(
            key=f"last_cleaned_{map_id}_{room}",
            translation_key="room_last_cleaned",
            device_class=SensorDeviceClass.TIMESTAMP,
        )
        super().__init__(coordinator, description)
        self._history, self._map, self._map_id, self._room = history, live_map, map_id, int(room)
        self._attr_translation_placeholders = {"room": live_map.rooms[room].get("name") or f"#{room}"}

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(self._history.listen(self.async_write_ha_state))

    @property
    def available(self) -> bool:
        message = self._map.message
        return super().available and message is not None and message["map_id"] == self._map_id

    @property
    def native_value(self) -> datetime | None:
        last = self._history.last_cleaned(self._map_id, self._room)
        return dt_util.utc_from_timestamp(last["start"]) if last else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        week = (dt_util.utcnow() - timedelta(days=7)).timestamp()
        mine = [c for c in self._history.cleanings if c["map_id"] == self._map_id and self._room in c["rooms"]]
        return {"cleanings_last_7_days": sum(c["start"] >= week for c in mine)}
