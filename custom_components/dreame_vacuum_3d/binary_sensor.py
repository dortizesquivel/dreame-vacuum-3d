"""Binary sensors: charging, mop attached, and dock problems."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import DreameDataUpdateCoordinator
from .device.enums import (
    ChargingStatus,
    CleanWaterTankStatus,
    DirtyWaterTankStatus,
    DustBagStatus,
    WaterTank,
    parse_enum,
)
from .entity import DreameEntity


@dataclass(frozen=True, kw_only=True)
class DreameBinarySensorEntityDescription(BinarySensorEntityDescription):
    """Binary sensor backed by a device property key."""

    is_on_fn: Callable[[object], bool]


def _charging(value: object) -> bool:
    status = parse_enum(ChargingStatus, value)
    return status is ChargingStatus.CHARGING


def _mop_pad_installed(value: object) -> bool:
    return bool(value)


def _water_tank_mop(value: object) -> bool:
    tank = parse_enum(WaterTank, value)
    return tank in {WaterTank.INSTALLED, WaterTank.MOP_INSTALLED}


def _clean_water_problem(value: object) -> bool:
    status = parse_enum(CleanWaterTankStatus, value)
    return status in {
        CleanWaterTankStatus.NOT_INSTALLED,
        CleanWaterTankStatus.LOW_WATER,
    }


def _dirty_water_problem(value: object) -> bool:
    status = parse_enum(DirtyWaterTankStatus, value)
    return status is DirtyWaterTankStatus.NOT_INSTALLED_OR_FULL


def _dust_bag_problem(value: object) -> bool:
    status = parse_enum(DustBagStatus, value)
    return status in {DustBagStatus.NOT_INSTALLED, DustBagStatus.CHECK}


BINARY_SENSORS: tuple[DreameBinarySensorEntityDescription, ...] = (
    DreameBinarySensorEntityDescription(
        key="charging_status",
        translation_key="charging",
        device_class=BinarySensorDeviceClass.BATTERY_CHARGING,
        is_on_fn=_charging,
    ),
    DreameBinarySensorEntityDescription(
        key="mop_pad_installed",
        translation_key="mop_attached",
        device_class=BinarySensorDeviceClass.OCCUPANCY,
        is_on_fn=_mop_pad_installed,
    ),
    DreameBinarySensorEntityDescription(
        key="water_tank",
        translation_key="mop_attached",
        device_class=BinarySensorDeviceClass.OCCUPANCY,
        is_on_fn=_water_tank_mop,
    ),
    DreameBinarySensorEntityDescription(
        key="clean_water_tank_status",
        translation_key="clean_water_problem",
        device_class=BinarySensorDeviceClass.PROBLEM,
        entity_category=EntityCategory.DIAGNOSTIC,
        is_on_fn=_clean_water_problem,
    ),
    DreameBinarySensorEntityDescription(
        key="dirty_water_tank_status",
        translation_key="dirty_water_problem",
        device_class=BinarySensorDeviceClass.PROBLEM,
        entity_category=EntityCategory.DIAGNOSTIC,
        is_on_fn=_dirty_water_problem,
    ),
    DreameBinarySensorEntityDescription(
        key="dust_bag_status",
        translation_key="dust_bag_problem",
        device_class=BinarySensorDeviceClass.PROBLEM,
        entity_category=EntityCategory.DIAGNOSTIC,
        is_on_fn=_dust_bag_problem,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up binary sensors for available properties only."""
    coordinator: DreameDataUpdateCoordinator = entry.runtime_data.coordinator
    available = coordinator.device.available
    # Prefer mop_pad_installed when present; otherwise expose water_tank as mop attached.
    descriptions = []
    for description in BINARY_SENSORS:
        if description.key not in available:
            continue
        if description.key == "water_tank" and "mop_pad_installed" in available:
            continue
        descriptions.append(description)
    async_add_entities(DreameBinarySensorEntity(coordinator, d) for d in descriptions)


class DreameBinarySensorEntity(DreameEntity, BinarySensorEntity):
    """Binary sensor for one Dreame property."""

    entity_description: DreameBinarySensorEntityDescription

    def __init__(
        self,
        coordinator: DreameDataUpdateCoordinator,
        description: DreameBinarySensorEntityDescription,
    ) -> None:
        super().__init__(coordinator, description)
        self._attr_is_on = self._read()

    @callback
    def _handle_coordinator_update(self) -> None:
        self._attr_is_on = self._read()
        super()._handle_coordinator_update()

    def _read(self) -> bool:
        value = self.device.values.get(self.entity_description.key)
        return self.entity_description.is_on_fn(value)
