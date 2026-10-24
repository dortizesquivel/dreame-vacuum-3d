"""Dreame vacuum device: discovery, property refresh, and command payloads."""

from __future__ import annotations

import json
import logging
from random import randrange
from typing import Any

from custom_components.dreame_vacuum_3d.device.capabilities import Capabilities
from custom_components.dreame_vacuum_3d.device.enums import Status
from custom_components.dreame_vacuum_3d.protocol import miot
from custom_components.dreame_vacuum_3d.protocol.miio_codec import MiioError
from custom_components.dreame_vacuum_3d.protocol.miot import Send

_LOGGER = logging.getLogger(__name__)

_DEFAULT_GRID = 100


def _coord(value: float | int) -> int:
    return round(value)


def _pick(values: int | list[int], index: int, default: int) -> int:
    if isinstance(values, list):
        return int(values[index]) if index < len(values) else default
    return int(values)


def _rects(zones: list[int] | list[list[int]]) -> list[list[int]]:
    if not zones:
        raise ValueError("zones required")
    if isinstance(zones[0], list):
        return zones  # type: ignore[return-value]
    return [zones]  # type: ignore[list-item]


def _points(points: list[int] | list[list[int]]) -> list[list[int]]:
    if not points:
        raise ValueError("points required")
    if isinstance(points[0], list):
        return points  # type: ignore[return-value]
    return [points]  # type: ignore[list-item]


class DreameDevice:
    """Talks to one robot over local miIO, falling back to the Xiaomi cloud."""

    def __init__(self, caps: Capabilities, send_local: Send, send_cloud: Send) -> None:
        self.caps = caps
        self._send_local = send_local
        self._send_cloud = send_cloud
        self.channel: str = "local"
        self.available: frozenset[str] = frozenset()
        self.values: dict[str, Any] = {}
        self._remote_control = False

    async def _send(self, method: str, params: Any) -> Any:
        try:
            result = await self._send_local(method, params)
        except MiioError:
            _LOGGER.debug("local %s failed; trying cloud", method)
            result = await self._send_cloud(method, params)
            self.channel = "cloud"
            return result
        self.channel = "local"
        return result

    async def _get(self, keys: list[str]) -> dict[str, Any]:
        props = [self.caps.properties[k] for k in keys]
        raw = await miot.get_properties(self._send, props)
        out: dict[str, Any] = {}
        for (siid, piid), value in raw.items():
            key = self.caps.key_for(siid, piid)
            if key is not None:
                out[key] = value
        return out

    async def async_discover(self) -> None:
        """Probe every capability property once; those that answer become available."""
        got = await self._get(list(self.caps.properties))
        self.available = frozenset(got)
        self.values = dict(got)

    async def async_refresh(self) -> set[str]:
        """Re-read available properties; return the keys whose values changed."""
        if not self.available:
            return set()
        got = await self._get(sorted(self.available))
        changed: set[str] = set()
        for key, value in got.items():
            if self.values.get(key) != value:
                changed.add(key)
                self.values[key] = value
        return changed

    async def _action(self, name: str, params: list[dict] | None = None) -> Any:
        siid, aiid = self.caps.actions[name]
        return await miot.call_action(self._send, siid, aiid, params)

    async def start(self) -> Any:
        return await self._action("start")

    async def pause(self) -> Any:
        return await self._action("pause")

    async def stop(self) -> Any:
        return await self._action("stop")

    async def return_to_base(self) -> Any:
        return await self._action("charge")

    async def locate(self) -> Any:
        return await self._action("locate")

    async def set(self, key: str, value: Any) -> None:
        siid, piid = self.caps.properties[key]
        await miot.set_property(self._send, siid, piid, value)
        self.values[key] = value

    async def reset_consumable(self, name: str) -> Any:
        return await self._action(f"reset_{name}")

    async def _start_custom(self, status: int, parameters: str) -> Any:
        status_piid = self.caps.properties["status"][1]
        props_piid = self.caps.properties["cleaning_properties"][1]
        payload = [
            {"piid": status_piid, "value": status},
            {"piid": props_piid, "value": parameters},
        ]
        return await self._action("start_custom", payload)

    async def clean_segment(
        self,
        segments: int | list[int],
        repeats: int | list[int] = 1,
        suction: int | list[int] = 1,
        water: int | list[int] = 2,
    ) -> Any:
        """Clean rooms by segment id (Tasshack clean_segment payload shape)."""
        selected = segments if isinstance(segments, list) else [segments]
        cleanlist = [
            [
                segment_id,
                max(1, _pick(repeats, index, 1)),
                _pick(suction, index, 1),
                _pick(water, index, 2),
                index + 1,
            ]
            for index, segment_id in enumerate(selected)
        ]
        params = json.dumps({"selects": cleanlist}, separators=(",", ":"))
        return await self._start_custom(Status.SEGMENT_CLEANING.value, params)

    async def clean_zone(
        self,
        zones: list[int] | list[list[int]],
        repeats: int | list[int] = 1,
        suction: int | list[int] = 1,
        water: int | list[int] = 2,
    ) -> Any:
        """Clean rectangles [[x0,y0,x1,y1], ...] (Tasshack clean_zone payload)."""
        areas: list[list[int]] = []
        for index, zone in enumerate(_rects(zones)):
            if len(zone) != 4:
                raise ValueError(f"zone {index} must have 4 coordinates")
            areas.append(
                [
                    _coord(zone[0]),
                    _coord(zone[1]),
                    _coord(zone[2]),
                    _coord(zone[3]),
                    max(1, _pick(repeats, index, 1)),
                    _pick(suction, index, 1),
                    _pick(water, index, 2),
                ]
            )
        params = json.dumps({"areas": areas}, separators=(",", ":"))
        return await self._start_custom(Status.ZONE_CLEANING.value, params)

    async def clean_spot(
        self,
        points: list[int] | list[list[int]],
        repeats: int | list[int] = 1,
        suction: int | list[int] = 1,
        water: int | list[int] = 2,
    ) -> Any:
        """Clean around points (Tasshack clean_spot payload)."""
        cleanlist = [
            [
                _coord(point[0]),
                _coord(point[1]),
                max(1, _pick(repeats, index, 1)),
                _pick(suction, index, 1),
                _pick(water, index, 2),
            ]
            for index, point in enumerate(_points(points))
        ]
        params = json.dumps({"points": cleanlist}, separators=(",", ":"))
        return await self._start_custom(Status.SPOT_CLEANING.value, params)

    async def goto(
        self,
        x: int | float,
        y: int | float,
        *,
        cruising: bool = False,
        grid_size: int = _DEFAULT_GRID,
    ) -> Any:
        """Drive to a point (Tasshack go_to cruising / zone payloads)."""
        if cruising:
            params = json.dumps(
                {"tpoint": [[_coord(x), _coord(y), 0, 0]]},
                separators=(",", ":"),
            )
            return await self._start_custom(Status.CRUISING_POINT.value, params)
        half = grid_size // 2
        areas = [[_coord(x - half), _coord(y - half), _coord(x + half), _coord(y + half), 1, 0, 1]]
        params = json.dumps({"areas": areas}, separators=(",", ":"))
        return await self._start_custom(Status.ZONE_CLEANING.value, params)

    async def remote_control_move_step(
        self, rotation: int = 0, velocity: int = 0, prompt: bool | None = None
    ) -> None:
        """Nudge the robot while remote-controlling (sets remote_control property)."""
        silent = prompt is False or (prompt is None and self._remote_control)
        audio = "false" if silent else "true"
        # Device expects this exact JSON shape (Tasshack remote_control_move_step).
        nonce = randrange(65535)  # noqa: S311 — protocol field, not a secret
        payload = f'{{"spdv":{velocity},"spdw":{rotation},"audio":"{audio}","random":{nonce}}}'
        self._remote_control = True
        await self.set("remote_control", payload)

    async def select_map(self, map_id: int) -> Any:
        """Select a multi-floor map (Tasshack set_selected_map / update_map_data)."""
        piid = self.caps.properties["map_extend_data"][1]
        value = json.dumps({"sm": {}, "mapid": map_id}, separators=(",", ":"))
        return await self._action("update_map_data", [{"piid": piid, "value": value}])
