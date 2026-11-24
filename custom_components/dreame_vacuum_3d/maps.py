"""The robot's saved maps (floors), and the copies of each it keeps in the cloud to restore.

The robot names two files in the cloud: its map list (6.8: the saved maps, their names and the current one) and its
recovery list (6.9: for each map, the copies it saved, automatically or by backup_map). Both are read again when the
robot names a new file.

Portions derived from Tasshack/dreame-vacuum (MIT), see NOTICE.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
import json
import logging
from typing import Any

from homeassistant.core import CALLBACK_TYPE, callback

from .device import map as dmap
from .device.device import DreameDevice
from .protocol.cloud import CloudError
from .protocol.miio_codec import MiioError

_LOGGER = logging.getLogger(__name__)

type FileFetcher = Callable[[str], Awaitable[bytes | None]]
type UrlFetcher = Callable[[str], Awaitable[str | None]]
type Listener = Callable[[], None]

_ERRORS = (CloudError, MiioError, dmap.MapError, ValueError, KeyError, TypeError)


def _object_name(value: Any) -> str | None:
    """The file a 6.8 / 6.9 value names: '{"object_name": ..., "md5": ...}' (or a bare name)."""
    if not isinstance(value, str) or not value:
        return None
    try:
        data = json.loads(value)
    except ValueError:
        return value
    if isinstance(data, dict):
        name = data.get("object_name") or data.get("obj_name")
        return name if isinstance(name, str) and name else None
    return None


class SavedMaps:
    """The saved maps {id: name} and each one's restorable copies."""

    def __init__(
        self, device: DreameDevice, fetch_interim: FileFetcher, fetch_file: FileFetcher, file_url: UrlFetcher
    ) -> None:
        self._device = device
        self._fetch_interim = fetch_interim  # the map list: a file the robot keeps writing
        self._fetch_file = fetch_file  # the recovery list
        self._file_url = file_url  # a copy's file, for the robot to download
        self.maps: dict[int, str | None] = {}  # map id → its name (None: the robot hasn't one)
        self.current: int | None = None
        self.recovery: dict[int, list[dict[str, Any]]] = {}  # map id → its copies, newest first: {time, object_name}
        self._names: tuple[str | None, str | None] = (None, None)  # the files read last
        self._listeners: list[Listener] = []

    @callback
    def listen(self, cb: Listener) -> CALLBACK_TYPE:
        self._listeners.append(cb)
        return lambda: self._listeners.remove(cb)

    def name(self, map_id: int) -> str:
        return self.maps.get(map_id) or f"#{map_id}"

    async def refresh(self, *, force: bool = False) -> None:
        """Read the lists again if the robot names other files than last time (or force)."""
        values = self._device.values
        names = (_object_name(values.get("map_list")), _object_name(values.get("recovery_map_list")))
        if names == self._names and not force:
            return
        changed = False
        try:
            if names[0] and (force or names[0] != self._names[0]):
                changed = await self._read_maps(names[0]) or changed
            if names[1] and (force or names[1] != self._names[1]):
                changed = await self._read_recovery(names[1]) or changed
        except _ERRORS as err:
            _LOGGER.debug("Couldn't read the saved maps: %s", err)
            return
        self._names = names
        if changed:
            for cb in list(self._listeners):
                cb()

    async def _read_maps(self, object_name: str) -> bool:
        raw = await self._fetch_interim(object_name)
        if raw is None:
            return False
        info = json.loads(raw.decode())
        maps: dict[int, str | None] = {}
        for item in info.get("mapstr") or []:
            if not isinstance(item, dict) or not isinstance(item.get("map"), str):
                continue
            try:
                frame = dmap.decode(item["map"], iv=self._device.caps.map_iv)
            except dmap.MapError as err:
                _LOGGER.debug("Skipping a saved map: %s", err)
                continue
            name = item.get("name")
            maps[frame.map_id] = name if isinstance(name, str) and name else None
        current = info.get("curr_id")
        current = int(current) if isinstance(current, int | str) and str(current).lstrip("-").isdigit() else None
        changed = (maps, current) != (self.maps, self.current)
        self.maps, self.current = maps, current
        return changed

    async def _read_recovery(self, object_name: str) -> bool:
        raw = await self._fetch_file(object_name)
        if raw is None:
            return False
        recovery: dict[int, list[dict[str, Any]]] = {}
        for item in json.loads(raw.decode()):
            if not isinstance(item, dict) or "id" not in item:
                continue
            copies = [
                {"time": int(c.get("time") or 0), "object_name": c["objname"]}
                for c in item.get("info") or []
                if isinstance(c, dict) and isinstance(c.get("objname"), str) and c["objname"]
            ]
            recovery[int(item["id"])] = sorted(copies, key=lambda c: c["time"], reverse=True)
        changed = recovery != self.recovery
        self.recovery = recovery
        return changed

    async def restore(self, map_id: int, index: int) -> None:
        """Put back copy number index (1: the newest) of a map."""
        copies = self.recovery.get(map_id) or []
        if not 1 <= index <= len(copies):
            raise LookupError(f"map {map_id} has no copy {index}")
        url = await self._file_url(copies[index - 1]["object_name"])
        if not url:
            raise LookupError(f"the cloud has no file for copy {index} of map {map_id}")
        await self._device.restore_map(map_id, url)
