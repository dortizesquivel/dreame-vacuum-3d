"""The robot's past cleanings, for the entities and the card: when, how long, which rooms, and their paths."""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Awaitable, Callable
import logging
from typing import Any

from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .coordinator import ACTIVE_STATUSES
from .device import history as dhistory, map as dmap
from .device.device import DreameDevice
from .device.enums import Status, parse_enum
from .protocol.cloud import CloudError

_LOGGER = logging.getLogger(__name__)

KEEP = 30  # cleanings kept, newest first
FIRST_AFTER = 20  # s after setup before the first look
AFTER_TASK = (60, 600, 1800)  # s after a cleaning ends: the cloud gets its event later, sometimes much later
PATHS = 5  # cleanings' maps kept in memory, for replays and the coverage
_ERRORS = (CloudError, dmap.MapError)

type EventFetcher = Callable[[str, int], Awaitable[list[dict[str, Any]]]]  # (key, since in s), newest first
type FileFetcher = Callable[[str], Awaitable[bytes | None]]


class CleaningHistory:
    """The last cleanings and the rooms each one cleaned; a cleaning's path on request."""

    def __init__(
        self,
        hass: HomeAssistant,
        device: DreameDevice,
        fetch_events: EventFetcher,
        fetch_file: FileFetcher,
        store: Store[dict[str, Any]],
    ) -> None:
        self._hass, self._device = hass, device
        self._fetch_events, self._fetch_file, self._store = fetch_events, fetch_file, store
        # Newest first: {start (s), duration (min), area (m²), completed, file, rooms, map_id}.
        self.cleanings: list[dict[str, Any]] = []
        self._known: dict[str, dict[str, Any]] | None = None  # by log file: {map_id, rooms}, kept in .storage
        self._frames: OrderedDict[str, dmap.Frame] = OrderedDict()
        self._listeners: list[Callable[[], None]] = []
        self._was_active = False
        self._warned = False

    @callback
    def listen(self, cb: Callable[[], None]) -> CALLBACK_TYPE:
        """Call cb whenever the cleanings change."""
        self._listeners.append(cb)
        return lambda: self._listeners.remove(cb)

    @callback
    def start(self, coordinator: DataUpdateCoordinator[Any]) -> CALLBACK_TYPE:
        """Look soon, and after each cleaning ends; returns the stop function."""
        cancels = [async_call_later(self._hass, FIRST_AFTER, self._later)]

        @callback
        def on_update() -> None:
            active = parse_enum(Status, self._device.values.get("status")) in ACTIVE_STATUSES
            if self._was_active and not active:
                cancels.extend(async_call_later(self._hass, delay, self._later) for delay in AFTER_TASK)
            self._was_active = active

        remove = coordinator.async_add_listener(on_update)

        @callback
        def stop() -> None:
            remove()
            for cancel in cancels:
                cancel()

        return stop

    @callback
    def _later(self, _now: object) -> None:
        self._hass.async_create_background_task(self.refresh(), "dreame_vacuum_3d history")

    async def refresh(self) -> None:
        caps = self._device.caps
        since = self._device.values.get("first_cleaning_date")
        try:
            records = await self._fetch_events(dhistory.events_key(caps), since if isinstance(since, int) else 0)
        except _ERRORS as err:
            (_LOGGER.debug if self._warned else _LOGGER.warning)("Couldn't read the cleaning history: %s", err)
            self._warned = True
            return
        self._warned = False
        if self._known is None:
            self._known = (await self._store.async_load() or {}).get("cleanings", {})
        cleanings = dhistory.parse(records, caps)[:KEEP]
        for cleaning in cleanings:
            known = self._known.get(cleaning["file"]) if cleaning["file"] else None
            if cleaning["file"] and known is None:
                try:
                    frame = await self._frame(cleaning["file"])
                    known = {"map_id": _map_id(frame), "rooms": dmap.cleaned_rooms(frame)}
                except _ERRORS as err:
                    _LOGGER.debug("No map for the cleaning at %s: %s", cleaning["start"], err)
                else:
                    self._known[cleaning["file"]] = known
            cleaning.update(known or {"map_id": None, "rooms": []})
        files = {c["file"] for c in cleanings if c["file"]}
        self._known = {file: v for file, v in self._known.items() if file in files}
        self._store.async_delay_save(lambda: {"cleanings": self._known or {}}, 5)
        if cleanings != self.cleanings:
            self.cleanings = cleanings
            for cb in list(self._listeners):
                cb()

    async def path(self, start: int) -> dict[str, Any] | None:
        """A past cleaning's path, to replay it: {start, map_id, path, rooms}; None if it has no map."""
        cleaning = next((c for c in self.cleanings if c["start"] == start), None)
        if cleaning is None or not cleaning["file"]:
            return None
        frame = await self._frame(cleaning["file"])
        return {"start": start, "map_id": _map_id(frame), "path": dmap.path(frame), "rooms": cleaning["rooms"]}

    def last_cleaned(self, map_id: int, room: int) -> dict[str, Any] | None:
        """The newest cleaning of a room of a map, or None."""
        return next((c for c in self.cleanings if c["map_id"] == map_id and room in c["rooms"]), None)

    async def _frame(self, file: str) -> dmap.Frame:
        if file in self._frames:
            self._frames.move_to_end(file)
            return self._frames[file]
        name, _, key = file.partition(",")  # "<object name>,<key>": the key the file is encrypted with
        raw = await self._fetch_file(name)
        if raw is None:
            raise dmap.MapError("the cloud has no file for the cleaning")
        frame = dmap.decode(raw, key=key or None, iv=self._device.caps.map_iv)
        self._frames[file] = frame
        while len(self._frames) > PATHS:
            self._frames.popitem(last=False)
        return frame


def _map_id(frame: dmap.Frame) -> int:
    """The saved map a cleaning's frame belongs to (the live map's ids are of the saved one too)."""
    saved = dmap.saved_map(frame)
    return (saved or frame).map_id
