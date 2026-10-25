"""The robot's map for the 3D card: fetched while the robot moves, decoded and pushed to whoever listens.

What the card gets is a reference for where the robot is: its pose, the dock, the rooms (cells and names) and the path.
The house the card draws is the user's own model.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import asdict
from datetime import timedelta
import logging
from time import monotonic
from typing import Any

from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.util.hass_dict import HassKey

from .const import DOMAIN
from .coordinator import ACTIVE_STATUSES
from .device import map as dmap
from .device.device import DreameDevice
from .device.enums import Status, parse_enum
from .protocol.cloud import CloudError
from .protocol.miio_codec import MiioError
from .protocol.miot import MiotError

_LOGGER = logging.getLogger(__name__)

TICK = timedelta(seconds=5)  # how often to check; while the robot moves, a frame each tick
IDLE_EVERY = 900  # s between frames while it stays put: the map rarely changes then

type FileFetcher = Callable[[str], Awaitable[bytes | None]]
type Listener = Callable[[dict[str, Any]], None]

# The card's map subscriptions, per config entry: kept across reloads of the entry.
MAP_LISTENERS: HassKey[dict[str, list[Listener]]] = HassKey(f"{DOMAIN}_map_listeners")


class LiveMap:
    """The latest map message, and when to fetch the next frame."""

    def __init__(
        self,
        hass: HomeAssistant,
        device: DreameDevice,
        fetch_file: FileFetcher,
        listeners: list[Listener],
    ) -> None:
        self._hass = hass
        self._device = device
        self._fetch_file = fetch_file
        self._listeners = listeners  # shared with the WebSocket subscriptions, which outlive a reload
        self._rooms: tuple[str, dict[str, Any]] | None = None  # (the saved map it came from, grid + cells + rooms)
        self._fetching = False
        self._next = 0.0
        self._was_active = False
        self._warned = False
        self.pose: dict[str, Any] | None = None  # robot, dock, path, room, active of the latest frame

    @property
    def message(self) -> dict[str, Any] | None:
        """Everything the card needs, or None before the first frame."""
        if self.pose is None or self._rooms is None:
            return None
        return {**self._rooms[1], **self.pose}

    @property
    def rooms(self) -> dict[str, dict[str, Any]]:
        return self._rooms[1]["rooms"] if self._rooms else {}

    @callback
    def listen(self, cb: Listener) -> CALLBACK_TYPE:
        """Call cb with each new message: the rooms only when they changed, the pose every time."""
        self._listeners.append(cb)
        return lambda: self._listeners.remove(cb)

    @callback
    def start(self) -> CALLBACK_TYPE:
        """Fetch a first frame now, then keep checking; returns the stop function."""
        self._schedule()
        return async_track_time_interval(self._hass, self._schedule, TICK)

    @callback
    def _schedule(self, _now: object = None) -> None:
        status = parse_enum(Status, self._device.values.get("status"))
        active = status in ACTIVE_STATUSES
        # A frame when the robot moves, and one more when it stops, for the whole path.
        due = active or self._was_active or monotonic() >= self._next
        self._was_active = active
        if due and not self._fetching:
            self._fetching = True
            self._hass.async_create_background_task(self.refresh(), "dreame_vacuum_3d map")

    async def refresh(self) -> None:
        try:
            frame = await self._frame()
        except (MiioError, MiotError, CloudError, dmap.MapError) as err:
            (_LOGGER.debug if self._warned else _LOGGER.warning)("Couldn't fetch the map: %s", err)
            self._warned = True
            return
        finally:
            self._fetching = False
            self._next = monotonic() + IDLE_EVERY
        self._warned = False
        self._update(frame)

    async def _frame(self) -> dmap.Frame:
        inline, name = await self._device.request_map()
        if inline:
            return dmap.decode(inline)
        if not name:
            raise dmap.MapError("the robot sent no map")
        raw = await self._fetch_file(name)
        if raw is None:
            raise dmap.MapError("the cloud has no file for the map")
        return dmap.decode(raw)

    @callback
    def _update(self, frame: dmap.Frame) -> None:
        rism = frame.data.get("rism")
        source = rism if isinstance(rism, str) and rism else f"live:{frame.map_id}:{frame.frame_id}"
        if self._rooms is None or self._rooms[0] != source:
            saved = dmap.saved_map(frame)
            base = saved or frame
            cells, info = dmap.rooms(base, saved=saved is not None)
            self._rooms = (source, {"map_id": base.map_id, "grid": asdict(base.grid), "cells": cells, "rooms": info})
            changed = True
        else:
            changed = False
        last = self.pose or {}
        dock = frame.dock or (tuple(last["dock"]) if last.get("dock") else None)
        # Docked ("oc"), the robot is on the dock; with no pose at all, it stays where it was last seen.
        robot = frame.robot or (dock if frame.data.get("oc") else None)
        robot = robot or (tuple(last["robot"]) if last.get("robot") else None)
        rooms = self._rooms[1]
        grid = dmap.Grid(**rooms["grid"])
        pose = {
            "frame_id": frame.frame_id,
            "robot": list(robot) if robot else None,
            "dock": list(dock) if dock else None,
            "path": dmap.path(frame),
            "room": dmap.room_at(grid, rooms["cells"], rooms["rooms"], robot[0], robot[1]) if robot else None,
            "active": dmap.active_rooms(frame) or list(self._device.segments or []),
        }
        self.pose = pose
        message = {**rooms, **pose} if changed else pose
        for cb in list(self._listeners):
            cb(message)
