"""Vacuum entity: activity, battery, fan speed, and core cleaning commands."""

from __future__ import annotations

from typing import Any

from homeassistant.components.vacuum import (
    StateVacuumEntity,
    StateVacuumEntityDescription,
    VacuumActivity,
    VacuumEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import config_validation as cv, entity_platform
from homeassistant.helpers.entity_platform import AddEntitiesCallback
import voluptuous as vol

from .coordinator import DreameDataUpdateCoordinator
from .device.enums import DreameVacuumState, SuctionLevel, parse_enum
from .entity import DreameEntity, command

# Portions of service schemas derived from Tasshack/dreame-vacuum (MIT), see NOTICE.
_OPTIONAL_INT_OR_LIST = vol.Any(vol.Coerce(int), [vol.Coerce(int)])
_CONSUMABLES = (
    "main_brush",
    "side_brush",
    "filter",
    "tank_filter",
    "sensor",
    "mop_pad",
    "silver_ion",
    "detergent",
    "squeegee",
    "onboard_dirty_water_tank",
    "dirty_water_channel",
    "deodorizer",
    "wheel",
    "scale_inhibitor",
    "fluffing_roller",
    "roller_mop_filter",
    "water_outlet_filter",
)

# Portions of the state map derived from Tasshack/dreame-vacuum (MIT), see NOTICE.
_STATE_TO_ACTIVITY: dict[DreameVacuumState, VacuumActivity] = {
    DreameVacuumState.UNKNOWN: VacuumActivity.IDLE,
    DreameVacuumState.SWEEPING: VacuumActivity.CLEANING,
    DreameVacuumState.IDLE: VacuumActivity.IDLE,
    DreameVacuumState.PAUSED: VacuumActivity.PAUSED,
    DreameVacuumState.ERROR: VacuumActivity.ERROR,
    DreameVacuumState.RETURNING: VacuumActivity.RETURNING,
    DreameVacuumState.CHARGING: VacuumActivity.DOCKED,
    DreameVacuumState.MOPPING: VacuumActivity.CLEANING,
    DreameVacuumState.DRYING: VacuumActivity.DOCKED,
    DreameVacuumState.WASHING: VacuumActivity.CLEANING,
    DreameVacuumState.RETURNING_TO_WASH: VacuumActivity.RETURNING,
    DreameVacuumState.BUILDING: VacuumActivity.DOCKED,
    DreameVacuumState.SWEEPING_AND_MOPPING: VacuumActivity.CLEANING,
    DreameVacuumState.CHARGING_COMPLETED: VacuumActivity.DOCKED,
    DreameVacuumState.UPGRADING: VacuumActivity.IDLE,
    DreameVacuumState.CLEAN_SUMMON: VacuumActivity.CLEANING,
    DreameVacuumState.STATION_RESET: VacuumActivity.IDLE,
    DreameVacuumState.RETURNING_INSTALL_MOP: VacuumActivity.RETURNING,
    DreameVacuumState.RETURNING_REMOVE_MOP: VacuumActivity.RETURNING,
    DreameVacuumState.WATER_CHECK: VacuumActivity.DOCKED,
    DreameVacuumState.CLEAN_ADD_WATER: VacuumActivity.CLEANING,
    DreameVacuumState.WASHING_PAUSED: VacuumActivity.PAUSED,
    DreameVacuumState.AUTO_EMPTYING: VacuumActivity.DOCKED,
    DreameVacuumState.REMOTE_CONTROL: VacuumActivity.CLEANING,
    DreameVacuumState.SMART_CHARGING: VacuumActivity.DOCKED,
    DreameVacuumState.SECOND_CLEANING: VacuumActivity.CLEANING,
    DreameVacuumState.HUMAN_FOLLOWING: VacuumActivity.CLEANING,
    DreameVacuumState.SPOT_CLEANING: VacuumActivity.CLEANING,
    DreameVacuumState.RETURNING_AUTO_EMPTY: VacuumActivity.RETURNING,
    DreameVacuumState.WAITING_FOR_TASK: VacuumActivity.IDLE,
    DreameVacuumState.STATION_CLEANING: VacuumActivity.CLEANING,
    DreameVacuumState.RETURNING_TO_DRAIN: VacuumActivity.RETURNING,
    DreameVacuumState.DRAINING: VacuumActivity.CLEANING,
    DreameVacuumState.AUTO_WATER_DRAINING: VacuumActivity.CLEANING,
    DreameVacuumState.EMPTYING: VacuumActivity.DOCKED,
    DreameVacuumState.DUST_BAG_DRYING: VacuumActivity.DOCKED,
    DreameVacuumState.DUST_BAG_DRYING_PAUSED: VacuumActivity.PAUSED,
    DreameVacuumState.HEADING_TO_EXTRA_CLEANING: VacuumActivity.CLEANING,
    DreameVacuumState.EXTRA_CLEANING: VacuumActivity.CLEANING,
    DreameVacuumState.FINDING_PET_PAUSED: VacuumActivity.PAUSED,
    DreameVacuumState.FINDING_PET: VacuumActivity.CLEANING,
    DreameVacuumState.SHORTCUT: VacuumActivity.CLEANING,
    DreameVacuumState.MONITORING: VacuumActivity.CLEANING,
    DreameVacuumState.MONITORING_PAUSED: VacuumActivity.PAUSED,
    DreameVacuumState.INITIAL_DEEP_CLEANING: VacuumActivity.CLEANING,
    DreameVacuumState.INITIAL_DEEP_CLEANING_PAUSED: VacuumActivity.PAUSED,
    DreameVacuumState.SANITIZING: VacuumActivity.DOCKED,
    DreameVacuumState.SANITIZING_WITH_DRY: VacuumActivity.DOCKED,
    DreameVacuumState.CHANGING_MOP: VacuumActivity.CLEANING,
    DreameVacuumState.CHANGING_MOP_PAUSED: VacuumActivity.PAUSED,
    DreameVacuumState.FLOOR_MAINTAINING: VacuumActivity.CLEANING,
    DreameVacuumState.FLOOR_MAINTAINING_PAUSED: VacuumActivity.PAUSED,
    DreameVacuumState.REMOTE_PICKUP: VacuumActivity.CLEANING,
    DreameVacuumState.ARRANGING_ITEMS: VacuumActivity.CLEANING,
    DreameVacuumState.PET_GUARDING: VacuumActivity.CLEANING,
    DreameVacuumState.PET_GUARDING_PAUSED: VacuumActivity.PAUSED,
    DreameVacuumState.INSTALLING_MOP: VacuumActivity.CLEANING,
    DreameVacuumState.UNINSTALLING_MOP: VacuumActivity.CLEANING,
    DreameVacuumState.INTELLIGENT_RECHARGING: VacuumActivity.DOCKED,
    DreameVacuumState.ASSISTED_CLEANING: VacuumActivity.CLEANING,
    DreameVacuumState.ENTERING_DOCK: VacuumActivity.RETURNING,
    DreameVacuumState.LEAVING_DOCK: VacuumActivity.CLEANING,
}

_FAN_SPEEDS = tuple(level.name.lower() for level in SuctionLevel if level is not SuctionLevel.UNKNOWN)

VACUUM_DESCRIPTION = StateVacuumEntityDescription(key="vacuum", name=None)


def _optional_cleaning_level(value: object) -> int | list[int] | None:
    """Treat missing / empty Tasshack defaults as unset (device supplies defaults)."""
    if value is None or value == "":
        return None
    return value  # type: ignore[return-value]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the vacuum entity when state is available on the device."""
    coordinator: DreameDataUpdateCoordinator = entry.runtime_data.coordinator
    platform = entity_platform.async_get_current_platform()
    _register_entity_services(platform)
    if "state" not in coordinator.device.available:
        return
    async_add_entities([DreameVacuumEntity(coordinator, VACUUM_DESCRIPTION)])


def _register_entity_services(platform: entity_platform.EntityPlatform) -> None:
    """Register Tasshack-compatible entity services under this integration domain."""
    platform.async_register_entity_service(
        "vacuum_clean_segment",
        {
            vol.Required("segments"): vol.Any(vol.Coerce(int), [vol.Coerce(int)]),
            vol.Optional("repeats"): _OPTIONAL_INT_OR_LIST,
            vol.Optional("suction_level"): _OPTIONAL_INT_OR_LIST,
            vol.Optional("water_volume"): _OPTIONAL_INT_OR_LIST,
        },
        "async_clean_segment",
    )
    platform.async_register_entity_service(
        "vacuum_clean_zone",
        {
            vol.Required("zone"): vol.Any(
                [vol.ExactSequence([vol.Coerce(int)] * 4)],
                vol.ExactSequence([vol.Coerce(int)] * 4),
            ),
            vol.Optional("repeats"): _OPTIONAL_INT_OR_LIST,
            vol.Optional("suction_level"): _OPTIONAL_INT_OR_LIST,
            vol.Optional("water_volume"): _OPTIONAL_INT_OR_LIST,
        },
        "async_clean_zone",
    )
    platform.async_register_entity_service(
        "vacuum_clean_spot",
        {
            vol.Required("points"): vol.Any(
                [vol.ExactSequence([vol.Coerce(int)] * 2)],
                vol.ExactSequence([vol.Coerce(int)] * 2),
            ),
            vol.Optional("repeats"): _OPTIONAL_INT_OR_LIST,
            vol.Optional("suction_level"): _OPTIONAL_INT_OR_LIST,
            vol.Optional("water_volume"): _OPTIONAL_INT_OR_LIST,
        },
        "async_clean_spot_points",
    )
    platform.async_register_entity_service(
        "vacuum_goto",
        {
            vol.Required("x"): vol.Coerce(int),
            vol.Required("y"): vol.Coerce(int),
        },
        "async_goto",
    )
    platform.async_register_entity_service(
        "vacuum_remote_control_move_step",
        {
            vol.Required("rotation"): vol.All(vol.Coerce(int), vol.Range(min=-360, max=360)),
            vol.Required("velocity"): vol.All(vol.Coerce(int), vol.Range(min=-600, max=600)),
        },
        "async_remote_control_move_step",
    )
    platform.async_register_entity_service(
        "vacuum_select_map",
        {vol.Required("map_id"): cv.positive_int},
        "async_select_map",
    )
    platform.async_register_entity_service(
        "vacuum_reset_consumable",
        {vol.Required("consumable"): vol.In(_CONSUMABLES)},
        "async_reset_consumable",
    )


class DreameVacuumEntity(DreameEntity, StateVacuumEntity):
    """Dreame robot vacuum."""

    entity_description: StateVacuumEntityDescription
    _attr_supported_features = (
        VacuumEntityFeature.STATE
        | VacuumEntityFeature.PAUSE
        | VacuumEntityFeature.STOP
        | VacuumEntityFeature.RETURN_HOME
        | VacuumEntityFeature.FAN_SPEED
        | VacuumEntityFeature.LOCATE
        | VacuumEntityFeature.START
    )

    def __init__(
        self,
        coordinator: DreameDataUpdateCoordinator,
        description: StateVacuumEntityDescription,
    ) -> None:
        super().__init__(coordinator, description)
        self._attr_name = None  # device name is the vacuum's name
        self._attr_fan_speed_list = list(_FAN_SPEEDS)
        self._sync_from_device()

    @callback
    def _handle_coordinator_update(self) -> None:
        self._sync_from_device()
        super()._handle_coordinator_update()

    def _sync_from_device(self) -> None:
        state = parse_enum(DreameVacuumState, self.device.values.get("state"))
        self._attr_activity = (
            _STATE_TO_ACTIVITY.get(state, VacuumActivity.IDLE) if state is not None else VacuumActivity.IDLE
        )
        battery = self.device.values.get("battery_level")
        self._attr_extra_state_attributes = {
            "battery_level": battery,
            "status": self.device.values.get("status"),
        }
        suction = parse_enum(SuctionLevel, self.device.values.get("suction_level"))
        self._attr_fan_speed = suction.name.lower() if suction is not None else None

    @command
    async def async_start(self) -> None:
        await self.device.start()
        await self.coordinator.async_request_refresh()

    @command
    async def async_pause(self) -> None:
        await self.device.pause()
        await self.coordinator.async_request_refresh()

    @command
    async def async_stop(self, **kwargs: Any) -> None:
        await self.device.stop()
        await self.coordinator.async_request_refresh()

    @command
    async def async_return_to_base(self, **kwargs: Any) -> None:
        await self.device.return_to_base()
        await self.coordinator.async_request_refresh()

    @command
    async def async_locate(self, **kwargs: Any) -> None:
        await self.device.locate()

    @command
    async def async_set_fan_speed(self, fan_speed: str, **kwargs: Any) -> None:
        key = fan_speed.lower()
        if key not in _FAN_SPEEDS:
            raise ValueError(f"invalid fan speed: {fan_speed!r}")
        await self.device.set("suction_level", SuctionLevel[key.upper()].value)
        self._sync_from_device()
        self.async_write_ha_state()
        await self.coordinator.async_request_refresh()

    @command
    async def async_clean_segment(
        self,
        segments: int | list[int],
        repeats: int | list[int] = 1,
        suction_level: int | list[int] | str | None = None,
        water_volume: int | list[int] | str | None = None,
    ) -> None:
        """Clean rooms by segment id (Node-RED: segments: 6)."""
        await self.device.clean_segment(
            segments,
            repeats=repeats if repeats not in (None, "") else 1,
            **_cleaning_kwargs(suction_level, water_volume),
        )
        await self.coordinator.async_request_refresh()

    @command
    async def async_clean_zone(
        self,
        zone: list[int] | list[list[int]],
        repeats: int | list[int] = 1,
        suction_level: int | list[int] | str | None = None,
        water_volume: int | list[int] | str | None = None,
    ) -> None:
        """Clean one or more rectangles."""
        await self.device.clean_zone(
            zone,
            repeats=repeats if repeats not in (None, "") else 1,
            **_cleaning_kwargs(suction_level, water_volume),
        )
        await self.coordinator.async_request_refresh()

    @command
    async def async_clean_spot_points(
        self,
        points: list[int] | list[list[int]],
        repeats: int | list[int] = 1,
        suction_level: int | list[int] | str | None = None,
        water_volume: int | list[int] | str | None = None,
    ) -> None:
        """Clean around map points (entity service; distinct from VacuumEntity.clean_spot)."""
        await self.device.clean_spot(
            points,
            repeats=repeats if repeats not in (None, "") else 1,
            **_cleaning_kwargs(suction_level, water_volume),
        )
        await self.coordinator.async_request_refresh()

    @command
    async def async_goto(self, x: int, y: int) -> None:
        """Drive to a map coordinate."""
        await self.device.goto(x, y)
        await self.coordinator.async_request_refresh()

    @command
    async def async_remote_control_move_step(self, rotation: int, velocity: int) -> None:
        """Nudge the robot while remote-controlling."""
        await self.device.remote_control_move_step(rotation=rotation, velocity=velocity)

    @command
    async def async_select_map(self, map_id: int) -> None:
        """Select a multi-floor map."""
        await self.device.select_map(map_id)
        await self.coordinator.async_request_refresh()

    @command
    async def async_reset_consumable(self, consumable: str) -> None:
        """Reset a consumable life counter."""
        await self.device.reset_consumable(consumable)
        await self.coordinator.async_request_refresh()


def _cleaning_kwargs(
    suction_level: int | list[int] | str | None,
    water_volume: int | list[int] | str | None,
) -> dict[str, int | list[int]]:
    """Map optional Tasshack service fields onto device kwargs."""
    out: dict[str, int | list[int]] = {}
    suction = _optional_cleaning_level(suction_level)
    water = _optional_cleaning_level(water_volume)
    if suction is not None:
        out["suction"] = suction
    if water is not None:
        out["water"] = water
    return out
