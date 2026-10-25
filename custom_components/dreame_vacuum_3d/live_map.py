"""The robot's map for the 3D card: fetched while the robot moves, decoded and pushed to whoever listens.

What the card gets is a reference for where the robot is: its pose, the dock, the rooms (cells and names) and the path.
The house the card draws is the user's own model.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import asdict
from datetime import timedelta
import json
import logging
from time import monotonic, time
from typing import Any

from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.storage import Store
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

TICK = timedelta(seconds=5)  # how often to check; while the robot moves, its new frames each tick
IDLE_EVERY = 900  # s between frames while it stays put: the map rarely changes then
WHOLE_EVERY = 60  # s: while it moves, ask it for a whole frame this often (it mostly refuses then)
_ERRORS = (MiioError, MiotError, CloudError, dmap.MapError)

type FileFetcher = Callable[[str], Awaitable[bytes | None]]
type HistoryFetcher = Callable[[str, int], Awaitable[list[dict[str, Any]]]]  # (property "siid.piid", since s)
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
        store: Store[dict[str, Any]] | None = None,
        fetch_history: HistoryFetcher | None = None,
    ) -> None:
        self._hass = hass
        self._device = device
        self._fetch_file = fetch_file
        self._listeners = listeners  # shared with the WebSocket subscriptions, which outlive a reload
        self._rooms: tuple[str, dict[str, Any]] | None = None  # (the saved map it came from, grid + cells + rooms)
        self._saved: tuple[dmap.Grid, bytearray, list[dict[str, Any]]] | None = None  # its grid, open floor, furniture
        self._fetching = False
        self._next = 0.0
        self._was_active = False
        self._warned = False
        # What the lidar saw on open floor: in the last cleaning, and in the one before this one (the reference for
        # what is new), kept in .storage so a restart doesn't make everything new.
        self._store = store
        self._known: dict[str, Any] | None = None
        self._loaded = store is None
        self._in_task = False
        self._reference: tuple[int, set[tuple[int, int]]] | None = None  # (id of the list, its cells and neighbours)
        # While it cleans the robot logs its frames in the cloud: partial ones (P) in 6.1, a whole one's file in 6.3.
        self._fetch_history = fetch_history
        self._since = 0  # cloud time (s) of the next log entries to read
        self._at: tuple[int, int] | None = None  # (map id, frame id) the pose is at
        self._whole_at = -float(WHOLE_EVERY)  # monotonic time of the last whole frame
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
        frames: list[dmap.Frame] = []
        error: Exception | None = None
        try:
            active = parse_enum(Status, self._device.values.get("status")) in ACTIVE_STATUSES
            if active and self._fetch_history is not None:
                try:
                    frames = await self._logged_frames(self._fetch_history)
                except _ERRORS as err:
                    error = err
            # Docked, the robot answers request_map; cleaning, it mostly sends nothing, so only now and then.
            if not active or self._at is None or monotonic() - self._whole_at >= WHOLE_EVERY:
                try:
                    frames.append(await self._frame())
                except _ERRORS as err:
                    error = error or err
        finally:
            self._fetching = False
            self._next = monotonic() + IDLE_EVERY
        if not frames:
            if error is not None:
                (_LOGGER.debug if self._warned else _LOGGER.warning)("Couldn't fetch the map: %s", error)
                self._warned = True
            return
        self._warned = False
        if not self._loaded and self._store is not None:
            self._known = await self._store.async_load()
            self._loaded = True
        for frame in frames:
            self._update(frame)

    async def _frame(self) -> dmap.Frame:
        inline, name = await self._device.request_map()
        if inline:
            return dmap.decode(inline, iv=self._device.caps.map_iv)
        if not name:
            raise dmap.MapError("the robot sent no map")
        return await self._file_frame(name)

    async def _file_frame(self, name: str) -> dmap.Frame:
        name, _, key = name.partition(",")  # "<user id>/<did>/<n>,<key>": the key the file is encrypted with
        raw = await self._fetch_file(name)
        if raw is None:
            raise dmap.MapError("the cloud has no file for the map")
        return dmap.decode(raw, key=key or None, iv=self._device.caps.map_iv)

    async def _logged_frames(self, fetch: HistoryFetcher) -> list[dmap.Frame]:
        """The frames the robot logged in the cloud since the last look, in the order it logged them: its partial
        frames, and the newest whole one it uploaded, if any, in its place among them."""
        props = self._device.caps.properties
        since = self._since or int(time()) - 60
        partial = await fetch("{}.{}".format(*props["map_data"]), since)
        whole = await fetch("{}.{}".format(*props["object_name"]), since)
        stamp = lambda r: int(r.get("time") or 0)  # noqa: E731
        self._since = max([since, *(stamp(r) + 1 for r in partial + whole)])
        entries = [(stamp(r), False, r) for r in partial]
        if whole:
            entries.append((stamp(newest := max(whole, key=stamp)), True, newest))
        frames = []
        for _, is_whole, record in sorted(entries, key=lambda e: (e[0], e[1])):
            if (value := _logged_value(record)) is None:
                continue
            if is_whole:
                frames.append(await self._file_frame(value))
                continue
            try:
                frames.append(dmap.decode(value, iv=self._device.caps.map_iv))
            except dmap.MapError as err:
                _LOGGER.debug("Skipping a logged map frame: %s", err)
        return frames

    @callback
    def _update(self, frame: dmap.Frame) -> None:
        if frame.frame_type != "I":
            self._apply_partial(frame)
            return
        self._at = (frame.map_id, frame.frame_id)
        self._whole_at = monotonic()
        rism = frame.data.get("rism")
        source = rism if isinstance(rism, str) and rism else f"live:{frame.map_id}:{frame.frame_id}"
        if self._rooms is None or self._rooms[0] != source:
            saved = dmap.saved_map(frame)
            base = saved or frame
            cells, info = dmap.rooms(base, saved=saved is not None)
            self._rooms = (source, {"map_id": base.map_id, "grid": asdict(base.grid), "cells": cells, "rooms": info})
            self._saved = (saved.grid, dmap.floor_mask(saved), dmap.furniture(saved)) if saved else None
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
            "obstacles": dmap.obstacles(frame),
            "furniture": dmap.furniture(frame) or (self._saved[2] if self._saved else []),
            "new_cells": dmap.runs(self._new(rooms["map_id"], dmap.seen_cells(frame, *self._saved[:2])))
            if self._saved
            else [],
        }
        self.pose = pose
        message = {**rooms, **pose} if changed else pose
        for cb in list(self._listeners):
            cb(message)

    @callback
    def _apply_partial(self, frame: dmap.Frame) -> None:
        """A partial frame on top of the last whole one: where the robot is now and the path since that frame."""
        if self.pose is None or self._at is None or frame.map_id != self._at[0] or frame.frame_id <= self._at[1]:
            return  # nothing to build on yet, another map, or already applied
        self._at = (frame.map_id, frame.frame_id)
        pose = {**self.pose, "frame_id": frame.frame_id}
        if frame.robot:
            pose["robot"] = list(frame.robot)
            rooms = self.message or {}
            grid = dmap.Grid(**rooms["grid"]) if rooms.get("grid") else None
            pose["room"] = dmap.room_at(grid, rooms["cells"], rooms["rooms"], *frame.robot[:2]) if grid else None
        if frame.dock:
            pose["dock"] = list(frame.dock)
        path = [{**s, "points": list(s["points"])} for s in pose.get("path", [])]
        for stroke in dmap.path(frame):
            if stroke.pop("joins", False) and path:
                path[-1]["points"].extend(stroke["points"])
            else:
                path.append(stroke)
        pose["path"] = path
        if active := dmap.active_rooms(frame):
            pose["active"] = active
        if "ai_obstacle" in frame.data or "laser_obstacle" in frame.data:
            pose["obstacles"] = dmap.obstacles(frame)
        self.pose = pose
        for cb in list(self._listeners):
            cb(pose)

    def _new(self, map_id: int, seen: set[tuple[int, int]]) -> set[tuple[int, int]]:
        """What the lidar sees on open floor that it didn't in the cleaning before this one.

        The first frame of a map is the reference: nothing is new then. When a cleaning starts, what the last one saw
        becomes the reference, so what shows up stays new until the next cleaning.
        """
        active = parse_enum(Status, self._device.values.get("status")) in ACTIVE_STATUSES
        known = self._known
        if not known or known.get("map_id") != map_id:
            known = self._known = {"map_id": map_id, "before": sorted(seen), "last": sorted(seen)}
            self._save()
        elif active and not self._in_task:
            known["before"] = known["last"]
            self._save()
        if self._in_task and not active:
            self._save()
        self._in_task = active
        known["last"] = sorted(seen)
        before = known["before"]
        if self._reference is None or self._reference[0] != id(before):
            # A cell or its neighbour: the lidar's outline wobbles by a cell between cleanings.
            near = {(x + dx, y + dy) for x, y in before for dx in (-1, 0, 1) for dy in (-1, 0, 1)}
            self._reference = (id(before), near)
        return seen - self._reference[1]

    @callback
    def _save(self) -> None:
        if self._store is not None:
            self._store.async_delay_save(lambda: self._known or {}, 5)


def _logged_value(record: dict[str, Any]) -> str | None:
    """A logged property value: a JSON list with the value first ('["<map>"]')."""
    try:
        value = json.loads(record.get("value") or "null")
    except ValueError:
        return None
    value = value[0] if isinstance(value, list) and value else value
    return value if isinstance(value, str) and value else None
