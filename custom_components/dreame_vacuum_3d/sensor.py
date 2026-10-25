"""Sensors: status, battery, cleaning stats, consumables, dock, and channel."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
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
    entities = [
        DreameSensorEntity(coordinator, description)
        for description in SENSORS
        if description.always or description.key in available
    ]
    async_add_entities(entities)


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
