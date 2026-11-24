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
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv, entity_platform
from homeassistant.helpers.entity_platform import AddEntitiesCallback
import voluptuous as vol

from .const import DOMAIN
from .coordinator import ACTIVE_STATUSES, DreameDataUpdateCoordinator
from .device.enums import DreameVacuumState, Status, SuctionLevel, TaskStatus, parse_enum
from .entity import DreameEntity, command
from .text import MAX_NAME

# Portions of service schemas derived from Tasshack/dreame-vacuum (MIT), see NOTICE.
_NOT_EMPTY = vol.Length(min=1)  # an empty list would reach the robot as a task with nothing in it
_OPTIONAL_INT_OR_LIST = vol.Any(vol.Coerce(int), vol.All([vol.Coerce(int)], _NOT_EMPTY))
_INT_LIST = vol.Any(vol.All([vol.Coerce(int)], _NOT_EMPTY), vol.All(vol.Coerce(int), lambda v: [v]))
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
# vacuum_dock_action: action ↔ (the robot action it needs, the property the dock must report).
_DOCK_ACTIONS: dict[str, tuple[str, str]] = {
    "empty": ("start_auto_empty", "auto_empty_status"),
    "wash": ("start_washing", "self_wash_base_status"),
    "stop_washing": ("start_washing", "self_wash_base_status"),
    "dry": ("start_washing", "self_wash_base_status"),
    "stop_drying": ("start_washing", "self_wash_base_status"),
}

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
            vol.Required("segments"): _OPTIONAL_INT_OR_LIST,
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
                vol.All([vol.ExactSequence([vol.Coerce(int)] * 4)], _NOT_EMPTY),
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
                vol.All([vol.ExactSequence([vol.Coerce(int)] * 2)], _NOT_EMPTY),
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
    platform.async_register_entity_service("vacuum_request_map", None, "async_request_map")
    platform.async_register_entity_service("vacuum_start_fast_mapping", None, "async_start_fast_mapping")
    platform.async_register_entity_service("vacuum_start_mapping", None, "async_start_mapping")
    platform.async_register_entity_service(
        "vacuum_rename_segment",
        {
            vol.Required("segment_id"): vol.All(vol.Coerce(int), vol.Range(min=1, max=63)),
            vol.Required("segment_name"): vol.All(cv.string, vol.Strip, vol.Length(min=1, max=MAX_NAME)),
        },
        "async_rename_segment",
    )
    platform.async_register_entity_service(
        "vacuum_set_cleaning_sequence",
        {vol.Required("cleaning_sequence"): vol.Any(vol.All([vol.Coerce(int)]), vol.All(str, vol.Length(max=0)))},
        "async_set_cleaning_sequence",
    )
    platform.async_register_entity_service(
        "vacuum_set_custom_cleaning",
        {
            vol.Required("segment_id"): _INT_LIST,
            vol.Required("suction_level"): _INT_LIST,
            vol.Required("water_volume"): _INT_LIST,
            vol.Required("repeats"): _INT_LIST,
        },
        "async_set_custom_cleaning",
    )
    platform.async_register_entity_service(
        "vacuum_rename_map",
        {vol.Required("map_id"): cv.positive_int, vol.Required("map_name"): vol.All(cv.string, vol.Strip)},
        "async_rename_map",
    )
    platform.async_register_entity_service(
        "vacuum_delete_map", {vol.Optional("map_id"): cv.positive_int}, "async_delete_map"
    )
    platform.async_register_entity_service(
        "vacuum_backup_map", {vol.Optional("map_id"): cv.positive_int}, "async_backup_map"
    )
    platform.async_register_entity_service(
        "vacuum_restore_map",
        {
            vol.Required("recovery_map_index"): vol.All(vol.Coerce(int), vol.Range(min=1, max=4)),
            vol.Optional("map_id"): cv.positive_int,
        },
        "async_restore_map",
    )
    platform.async_register_entity_service(
        "vacuum_set_obstacle_ignore",
        {
            vol.Required("x"): vol.Coerce(int),
            vol.Required("y"): vol.Coerce(int),
            vol.Required("obstacle_ignored"): cv.boolean,
        },
        "async_set_obstacle_ignore",
    )
    platform.async_register_entity_service(
        "vacuum_dock_action",
        {vol.Required("action"): vol.In(tuple(_DOCK_ACTIONS))},
        "async_dock_action",
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
        # Paused on its way back to the dock, "start" would begin a new cleaning (Tasshack #1942): carry on home.
        if parse_enum(TaskStatus, self.device.values.get("task_status")) is TaskStatus.DOCKING_PAUSED:
            await self.device.return_to_base()
        else:
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
        if f"reset_{consumable}" not in self.device.caps.actions:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="consumable_not_supported",
                translation_placeholders={"consumable": consumable},
            )
        await self.device.reset_consumable(consumable)
        await self.coordinator.async_request_refresh()

    @command
    async def async_dock_action(self, action: str) -> None:
        """Empty the dust bin, or wash or dry the mop at the dock (or stop washing or drying)."""
        needs, reports = _DOCK_ACTIONS[action]
        if needs not in self.device.caps.actions or reports not in self.device.available:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="dock_action_not_supported",
                translation_placeholders={"action": action},
            )
        run = {
            "empty": self.device.start_auto_empty,
            "wash": self.device.start_washing,
            "stop_washing": self.device.stop_washing,
            "dry": self.device.start_drying,
            "stop_drying": self.device.stop_drying,
        }[action]
        await run()
        await self.coordinator.async_request_refresh()

    # Map: fetch it, make a new one, name its rooms and the order and settings it cleans them with, and its copies.

    def _not_supported(self, feature: str) -> ServiceValidationError:
        return ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="not_supported",
            translation_placeholders={"feature": feature},
        )

    def _check(self, feature: str, action: str = "update_map_data") -> None:
        if action not in self.device.caps.actions:
            raise self._not_supported(feature)

    def _idle(self) -> None:
        """Map edits wait for the robot to finish: it saves the map at the end of a task."""
        if parse_enum(Status, self.device.values.get("status")) in ACTIVE_STATUSES:
            raise ServiceValidationError(translation_domain=DOMAIN, translation_key="busy")

    @property
    def _runtime(self) -> Any:
        return self.coordinator.config_entry.runtime_data

    def _map_id(self, map_id: int | None) -> int:
        """The map a call names, or the one the robot uses now."""
        if map_id is not None:
            return map_id
        message = self._runtime.map.message
        current = self._runtime.maps.current
        if current is not None:
            return int(current)
        if message is not None:
            return int(message["map_id"])
        raise ServiceValidationError(translation_domain=DOMAIN, translation_key="no_map")

    def _rooms(self) -> dict[str, dict[str, Any]]:
        rooms = self._runtime.map.rooms
        if not rooms:
            raise ServiceValidationError(translation_domain=DOMAIN, translation_key="no_map")
        return rooms

    def _room(self, room: int) -> str:
        if str(room) not in self._rooms():
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="no_room", translation_placeholders={"room": str(room)}
            )
        return str(room)

    @command
    async def async_request_map(self) -> None:
        """Fetch the map again now."""
        self._check("request_map", "request_map")
        await self._runtime.map.fetch_now()
        await self._runtime.maps.refresh(force=True)

    @command
    async def async_start_fast_mapping(self) -> None:
        """Map the house without cleaning it."""
        self._check("start_fast_mapping", "start_custom")
        self._idle()
        await self.device.start_fast_mapping()
        await self.coordinator.async_request_refresh()

    @command
    async def async_start_mapping(self) -> None:
        """Clean the whole house, making a new map as it goes."""
        self._check("start_mapping", "start_custom")
        self._idle()
        await self.device.start_mapping()
        await self.coordinator.async_request_refresh()

    @command
    async def async_rename_segment(self, segment_id: int, segment_name: str) -> None:
        """Give a room its own name."""
        self._check("rename_segment")
        room = self._room(segment_id)
        map_id = int(self._runtime.map.message["map_id"])
        await self.device.rename_segment(map_id, segment_id, segment_name)
        self._runtime.map.set_room_name(room, segment_name)
        self._runtime.map.refresh_soon()

    @command
    async def async_set_cleaning_sequence(self, cleaning_sequence: list[int] | str) -> None:
        """The order to clean the rooms in; an empty list, the robot's own."""
        self._check("set_cleaning_sequence")
        self._idle()
        order = list(cleaning_sequence) if isinstance(cleaning_sequence, list) else []
        for room in order:
            self._room(room)
        if len(set(order)) != len(order):
            raise ServiceValidationError(translation_domain=DOMAIN, translation_key="repeated_room")
        await self.device.set_cleaning_sequence(order)
        self._runtime.map.refresh_soon()

    @command
    async def async_set_custom_cleaning(
        self, segment_id: list[int], suction_level: list[int], water_volume: list[int], repeats: list[int]
    ) -> None:
        """Rooms' own suction, water and passes (the others keep theirs)."""
        self._check("set_custom_cleaning")
        self._idle()
        if not len(segment_id) == len(suction_level) == len(water_volume) == len(repeats):
            raise ServiceValidationError(translation_domain=DOMAIN, translation_key="count_mismatch")
        ranges = {"suction_level": (suction_level, 0), "water_volume": (water_volume, 1), "repeats": (repeats, 1)}
        for name, (values, low) in ranges.items():
            if any(not low <= v <= 3 for v in values):
                raise ServiceValidationError(
                    translation_domain=DOMAIN, translation_key="out_of_range", translation_placeholders={"field": name}
                )
        rooms = self._rooms()
        settings = {
            int(room): (meta.get("suction", 1), meta.get("water", 2), meta.get("repeats", 1))
            for room, meta in rooms.items()
        }
        for room, suction, water, passes in zip(segment_id, suction_level, water_volume, repeats, strict=True):
            settings[int(self._room(room))] = (suction, water, passes)
        await self.device.set_custom_cleaning(settings)
        for room, suction, water, passes in zip(segment_id, suction_level, water_volume, repeats, strict=True):
            self._runtime.map.set_room_settings(str(room), {"suction": suction, "water": water, "repeats": passes})
        self._runtime.map.refresh_soon()

    @command
    async def async_rename_map(self, map_id: int, map_name: str) -> None:
        """Name a saved map."""
        self._check("rename_map")
        await self.device.rename_map(map_id, map_name)
        await self._runtime.maps.refresh(force=True)

    @command
    async def async_delete_map(self, map_id: int | None = None) -> None:
        """Delete a saved map (the current one if none is named)."""
        self._check("delete_map")
        self._idle()
        await self.device.delete_map(self._map_id(map_id))
        self._runtime.map.refresh_soon()
        await self.coordinator.async_request_refresh()

    @command
    async def async_backup_map(self, map_id: int | None = None) -> None:
        """Save a copy of a map in the cloud."""
        self._check("backup_map", "backup_map")
        self._idle()
        await self.device.backup_map(self._map_id(map_id))
        await self.coordinator.async_request_refresh()

    @command
    async def async_restore_map(self, recovery_map_index: int, map_id: int | None = None) -> None:
        """Put back a copy of a map: 1 is the newest."""
        if "map_recovery" not in self.device.caps.properties:
            raise self._not_supported("restore_map")
        self._idle()
        target = self._map_id(map_id)
        maps = self._runtime.maps
        await maps.refresh(force=True)
        try:
            await maps.restore(target, recovery_map_index)
        except LookupError as err:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="no_backup",
                translation_placeholders={"index": str(recovery_map_index), "map": str(target)},
            ) from err
        self._runtime.map.refresh_soon(15)
        await self.coordinator.async_request_refresh()

    @command
    async def async_set_obstacle_ignore(self, x: int, y: int, obstacle_ignored: bool) -> None:
        """Have the robot go over an obstacle it found (or avoid it again)."""
        self._check("set_obstacle_ignore")
        self._idle()
        obstacles = (self._runtime.map.pose or {}).get("obstacles") or []
        found = next((o for o in obstacles if o["x"] == x and o["y"] == y), None)
        if found is None:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="no_obstacle",
                translation_placeholders={"x": str(x), "y": str(y)},
            )
        await self.device.set_obstacle_ignore(x, y, found["code"], obstacle_ignored)
        found["ignored"] = obstacle_ignored
        self._runtime.map.refresh_soon()


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
