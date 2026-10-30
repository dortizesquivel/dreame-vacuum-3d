"""The robot's map for the 3D card: fetched while the robot moves, decoded and pushed to whoever listens.

What the card gets is a reference for where the robot is: its pose, the dock, the rooms (cells and names) and the path.
The house the card draws is the user's own model.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Awaitable, Callable
from dataclasses import asdict, replace
from datetime import UTC, datetime, timedelta
import json
import logging
from time import monotonic, time
from typing import Any

from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util
from homeassistant.util.hass_dict import HassKey

from .const import DOMAIN
from .coordinator import ACTIVE_STATUSES, ACTIVE_TASKS
from .device import map as dmap
from .device.device import DreameDevice
from .device.enums import Status, TaskStatus, parse_enum
from .protocol.cloud import CloudError
from .protocol.miio_codec import MiioError
from .protocol.miot import MiotError

_LOGGER = logging.getLogger(__name__)

TICK = timedelta(seconds=2.5)  # how often to check; while the robot moves and a card shows it, its new frames each tick
UNWATCHED_EVERY = 5  # s between reads while it moves and no card shows the map: half the calls to the cloud
IDLE_EVERY = 900  # s between frames while it stays put: the map rarely changes then
FIRST_RETRY = 30  # s: until a first frame comes (a robot just woken may send none), ask again this soon
WHOLE_EVERY = 60  # s: while it moves, ask it for a whole frame this often (it mostly refuses then)
_ERRORS = (MiioError, MiotError, CloudError, dmap.MapError)

type FileFetcher = Callable[[str], Awaitable[bytes | None]]
type HistoryFetcher = Callable[[str, int], Awaitable[list[dict[str, Any]]]]  # (property "siid.piid", since s)
type Listener = Callable[[dict[str, Any]], None]

# The card's map subscriptions, per config entry: kept across reloads of the entry.
MAP_LISTENERS: HassKey[dict[str, list[Listener]]] = HassKey(f"{DOMAIN}_map_listeners")
# How many cards show each entry's map now (the listeners also hold entities that follow it).
MAP_VIEWERS: HassKey[dict[str, int]] = HassKey(f"{DOMAIN}_map_viewers")


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
        watched: Callable[[], bool] = lambda: True,
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
        self._bad_frame = False  # warned about a frame with a bad saved map, until a whole frame is good again
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
        self._failed_whole: str | None = None  # the logged whole frame whose file couldn't be read
        self._at: tuple[int, int] | None = None  # (map id, frame id) the pose is at
        self._whole_at = -float(WHOLE_EVERY)  # monotonic time of the last whole frame
        self._watched = watched  # whether a card shows the map now
        self._read_at = datetime.min.replace(tzinfo=UTC)  # the last read while the robot moved
        self.last_frames: dict[str, dict[str, Any]] = {}  # what the last whole and partial frames held, for diagnostics
        # The last whole frame and its cells with the partial frames since applied (what the lidar sees now), and
        # how many were applied; None once one is missing, until the next whole frame.
        self._live: tuple[dmap.Frame, bytearray] | None = None
        self._merged = 0
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
    def _schedule(self, now: datetime | None = None) -> None:
        status = parse_enum(Status, self._device.values.get("status"))
        active = status in ACTIVE_STATUSES
        now = now or dt_util.utcnow()
        # Frames while the robot moves (each tick while a card shows them), and one more when it stops, for the whole
        # path. The second's slack keeps reads on every other tick despite the timer's jitter.
        moving = active and (self._watched() or (now - self._read_at).total_seconds() >= UNWATCHED_EVERY - 1)
        due = moving or (self._was_active and not active) or monotonic() >= self._next
        self._was_active = active
        if due and not self._fetching:
            if active:
                self._read_at = now
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
            if self.pose is None:
                self._next = monotonic() + FIRST_RETRY
            if error is not None:
                # Paused mid-task, the robot sends no map, as while it cleans: not worth a warning.
                if self._warned or parse_enum(Status, self._device.values.get("status")) == Status.PAUSED:
                    _LOGGER.debug("Couldn't fetch the map: %s", error)
                else:
                    _LOGGER.warning("Couldn't fetch the map: %s", error)
                    self._warned = True
            return
        self._warned = False
        if not self._loaded and self._store is not None:
            self._known = await self._store.async_load()
            self._loaded = True
        for frame in frames:
            try:
                self._update(frame)
            except dmap.MapError as err:  # the frame itself reads, but the saved map it carries doesn't
                (_LOGGER.debug if self._bad_frame else _LOGGER.warning)("Skipping a map frame: %s", err)
                self._bad_frame = True
            else:
                if frame.frame_type == "I":
                    self._bad_frame = False

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
        entries = [(stamp(r), False, r) for r in partial]
        if whole:
            entries.append((stamp(newest := max(whole, key=stamp)), True, newest))
        frames = []
        for _, is_whole, record in sorted(entries, key=lambda e: (e[0], e[1])):
            if (value := _logged_value(record)) is None:
                continue
            if is_whole:
                try:
                    frames.append(await self._file_frame(value))
                except _ERRORS as err:
                    if value == self._failed_whole:  # the second time: go on without it rather than stall on it
                        _LOGGER.debug("Skipping a logged whole map frame: %s", err)
                        continue
                    self._failed_whole = value  # read these entries again next time, to try it once more
                    raise
                continue
            try:
                frames.append(dmap.decode(value, iv=self._device.caps.map_iv))
            except dmap.MapError as err:
                _LOGGER.debug("Skipping a logged map frame: %s", err)
        self._since = max([since, *(stamp(r) + 1 for r in partial + whole)])
        return frames

    @callback
    def _update(self, frame: dmap.Frame) -> None:
        if frame.frame_type != "I":
            self._apply_partial(frame)
            return
        self._at = (frame.map_id, frame.frame_id)
        self._whole_at = monotonic()
        self.last_frames["I"] = _summary(frame)
        self._check_merge(frame)
        self._live, self._merged = (frame, bytearray(frame.cells)), 0
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
            "zones": list(self._device.zones or []),
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
        """A partial frame on top of the last whole one: where the robot is now and the path since that frame.

        Listeners get only what changed, and the path's new strokes as `path_tail` (a stroke with `joins` continues
        the last one): the whole path grows with every frame, to thousands of points in a long cleaning.
        """
        if self.pose is None or self._at is None or frame.map_id != self._at[0] or frame.frame_id <= self._at[1]:
            return  # nothing to build on yet, another map, or already applied
        if frame.frame_id != self._at[1] + 1:
            self._live = None  # one is missing: what it changed is lost until the next whole frame
        self._at = (frame.map_id, frame.frame_id)
        self.last_frames["P"] = _summary(frame)
        update: dict[str, Any] = {"frame_id": frame.frame_id, "zones": list(self._device.zones or [])}
        if self._live is not None and dmap.merge(self._live[0].grid, self._live[1], frame):
            self._merged += 1
            if self._saved and self._rooms:
                live = replace(self._live[0], cells=bytes(self._live[1]))
                seen = dmap.seen_cells(live, *self._saved[:2])
                if (new := dmap.runs(self._new(self._rooms[1]["map_id"], seen))) != self.pose.get("new_cells"):
                    update["new_cells"] = new
        if frame.robot:
            update["robot"] = list(frame.robot)
            rooms = self.message or {}
            grid = dmap.Grid(**rooms["grid"]) if rooms.get("grid") else None
            update["room"] = dmap.room_at(grid, rooms["cells"], rooms["rooms"], *frame.robot[:2]) if grid else None
        if frame.dock:
            update["dock"] = list(frame.dock)
        if active := dmap.active_rooms(frame):
            update["active"] = active
        if "ai_obstacle" in frame.data or "laser_obstacle" in frame.data:
            update["obstacles"] = dmap.obstacles(frame)
        tail = dmap.path(frame)
        path = [{**s, "points": list(s["points"])} for s in self.pose.get("path", [])]  # earlier messages keep theirs
        for stroke in tail:
            if stroke.get("joins") and path:
                path[-1]["points"].extend(stroke["points"])
            else:
                path.append({k: v for k, v in stroke.items() if k != "joins"})
        self.pose = {**self.pose, **update, "path": path}
        message = {**update, "path_tail": tail} if tail else update
        for cb in list(self._listeners):
            cb(message)

    def _check_merge(self, frame: dmap.Frame) -> None:
        """How well the partial frames rebuilt the map, against the whole frame that follows them, for diagnostics."""
        if self._live is None or not self._merged:
            return
        base, cells = self._live
        if base.map_id == frame.map_id and base.grid == frame.grid:
            different = sum(a != b for a, b in zip(cells, frame.cells, strict=True))
            self.last_frames["merge"] = {"partials": self._merged, "cells": len(cells), "different": different}

    def _new(self, map_id: int, seen: set[tuple[int, int]]) -> set[tuple[int, int]]:
        """What the lidar sees on open floor that it didn't in the cleaning before this one.

        The first frame of a map is the reference: nothing is new then. When a cleaning starts, what the last one saw
        becomes the reference, so what shows up stays new until the next cleaning.
        """
        values = self._device.values
        # Paused, the robot's status isn't active but its task is: a resume continues the same cleaning.
        active = (
            parse_enum(Status, values.get("status")) in ACTIVE_STATUSES
            or parse_enum(TaskStatus, values.get("task_status")) in ACTIVE_TASKS
        )
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


def _summary(frame: dmap.Frame) -> dict[str, Any]:
    """What a frame holds, without where anything is: its grid, its commonest cell values and its data's keys."""
    return {
        "frame_id": frame.frame_id,
        "grid": asdict(frame.grid),
        "cells": len(frame.cells),
        "values": dict(Counter(frame.cells).most_common(8)),
        "keys": sorted(frame.data),
    }


def _logged_value(record: dict[str, Any]) -> str | None:
    """A logged property value: a JSON list with the value first ('["<map>"]')."""
    try:
        value = json.loads(record.get("value") or "null")
    except ValueError:
        return None
    value = value[0] if isinstance(value, list) and value else value
    return value if isinstance(value, str) and value else None
