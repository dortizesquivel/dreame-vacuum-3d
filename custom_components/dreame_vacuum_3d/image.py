"""The robot's map as a picture (an image entity), for other cards and for notifications.

Drawn here from the map the card gets: the rooms in colours, the walls, the path, the dock and the robot. With
calibration points, so a card such as Xiaomi Vacuum Map Card can turn the picture's pixels into map coordinates.
"""

from __future__ import annotations

from itertools import pairwise
import struct
from typing import Any
import zlib

from homeassistant.components.image import ImageEntity, ImageEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from .coordinator import DreameDataUpdateCoordinator
from .device.map import FLOOR, WALL
from .entity import DreameEntity
from .live_map import LiveMap

SCALE = 2  # pixels per map cell (50 mm)

# Palette: 0 clear, 1 wall, 2 floor not in a room, 3 path, 4 dock, 5 robot, then the rooms' colours.
_PALETTE = [
    (0, 0, 0, 0),
    (84, 92, 102, 255),
    (205, 211, 218, 255),
    (255, 255, 255, 255),
    (46, 204, 113, 255),
    (3, 169, 244, 255),
    (171, 205, 239, 255),
    (249, 199, 132, 255),
    (183, 227, 166, 255),
    (240, 168, 186, 255),
    (199, 180, 237, 255),
    (167, 225, 222, 255),
    (247, 222, 140, 255),
    (214, 192, 168, 255),
]
_CLEAR, _WALL, _FLOOR, _PATH, _DOCK, _ROBOT, _ROOMS = 0, 1, 2, 3, 4, 5, 6


def _chunk(kind: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)


def png(width: int, height: int, pixels: bytearray) -> bytes:
    """A palette PNG of width by height pixels, one palette index per pixel, row by row from the top."""
    rows = b"".join(b"\x00" + bytes(pixels[y * width : (y + 1) * width]) for y in range(height))
    return b"".join(
        (
            b"\x89PNG\r\n\x1a\n",
            _chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 3, 0, 0, 0)),
            _chunk(b"PLTE", b"".join(bytes(c[:3]) for c in _PALETTE)),
            _chunk(b"tRNS", bytes(c[3] for c in _PALETTE)),
            _chunk(b"IDAT", zlib.compress(rows, 6)),
            _chunk(b"IEND", b""),
        )
    )


def pixel(grid: dict[str, int], x: float, y: float) -> tuple[int, int]:
    """The pixel of a map point (mm): the picture's top is the map's largest y, as the app draws it."""
    px = (x - grid["left"]) / grid["size"] * SCALE
    py = grid["height"] * SCALE - (y - grid["top"]) / grid["size"] * SCALE
    return round(px), round(py)


def calibration(grid: dict[str, int]) -> list[dict[str, dict[str, int]]]:
    """Three map points and their pixels (Xiaomi Vacuum Map Card's calibration_points)."""
    left, top, span = grid["left"], grid["top"], 1000 * grid["size"]
    out = []
    for x, y in ((left, top), (left + span, top), (left, top + span)):
        px, py = pixel(grid, x, y)
        out.append({"vacuum": {"x": x, "y": y}, "map": {"x": px, "y": py}})
    return out


class Picture:
    """A map message drawn on a pixel buffer, and how map mm turn into its pixels."""

    def __init__(self, message: dict[str, Any]) -> None:
        grid = self.grid = message["grid"]
        self.left, self.top, self.size = grid["left"], grid["top"], grid["size"]
        self.cells_w, self.cells_h = grid["width"], grid["height"]
        self.width, self.height = max(1, self.cells_w * SCALE), max(1, self.cells_h * SCALE)
        self.pixels = bytearray(self.width * self.height)
        rooms = sorted((r for r in message["cells"] if r not in (WALL, FLOOR)), key=int)
        colour = {room: _ROOMS + i % (len(_PALETTE) - _ROOMS) for i, room in enumerate(rooms)}
        for key, runs in message["cells"].items():
            value = _WALL if key == WALL else _FLOOR if key == FLOOR else colour.get(key, _FLOOR)
            for i in range(0, len(runs) - 2, 3):
                self._cells(runs[i], runs[i + 1], runs[i + 2], value)
        for stroke in message.get("path") or []:
            points = [self.pixel(x, y) for x, y in stroke.get("points", [])]
            for a, b in pairwise(points):
                self._line(a, b, _PATH)
        if dock := message.get("dock"):
            self._dot(self.pixel(dock[0], dock[1]), 5, _DOCK)
        if robot := message.get("robot"):
            self._dot(self.pixel(robot[0], robot[1]), 6, _ROBOT)

    def pixel(self, x: float, y: float) -> tuple[int, int]:
        return pixel(self.grid, x, y)

    def _cells(self, x: int, y: int, n: int, value: int) -> None:
        row = self.cells_h - 1 - y
        if not 0 <= row < self.cells_h:
            return
        x0, x1 = max(0, x) * SCALE, min(self.cells_w, x + n) * SCALE
        if x1 <= x0:
            return
        for dy in range(SCALE):
            start = (row * SCALE + dy) * self.width
            self.pixels[start + x0 : start + x1] = bytes([value]) * (x1 - x0)

    def _put(self, x: int, y: int, value: int) -> None:
        if 0 <= x < self.width and 0 <= y < self.height:
            self.pixels[y * self.width + x] = value

    def _line(self, a: tuple[int, int], b: tuple[int, int], value: int) -> None:
        (x0, y0), (x1, y1) = a, b
        dx, dy = abs(x1 - x0), -abs(y1 - y0)
        sx, sy = (1 if x0 < x1 else -1), (1 if y0 < y1 else -1)
        err = dx + dy
        for _ in range(dx - dy + 1):
            self._put(x0, y0, value)
            if (x0, y0) == (x1, y1):
                break
            e2 = 2 * err
            if e2 >= dy:
                err, x0 = err + dy, x0 + sx
            if e2 <= dx:
                err, y0 = err + dx, y0 + sy

    def _dot(self, centre: tuple[int, int], radius: int, value: int) -> None:
        cx, cy = centre
        for y in range(cy - radius, cy + radius + 1):
            for x in range(cx - radius, cx + radius + 1):
                if (x - cx) ** 2 + (y - cy) ** 2 <= radius * radius:
                    self._put(x, y, value)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """One map picture per robot."""
    coordinator: DreameDataUpdateCoordinator = entry.runtime_data.coordinator
    async_add_entities([DreameMapImage(coordinator, entry.runtime_data.map)])


class DreameMapImage(DreameEntity, ImageEntity):
    """The robot's map, drawn again when it changes."""

    entity_description: ImageEntityDescription

    _attr_content_type = "image/png"

    def __init__(self, coordinator: DreameDataUpdateCoordinator, live_map: LiveMap) -> None:
        DreameEntity.__init__(self, coordinator, ImageEntityDescription(key="map"))
        ImageEntity.__init__(self, coordinator.hass)
        self._map = live_map
        self._image: bytes | None = None
        self._drawn: tuple[Any, ...] | None = None

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(self._map.listen(self._map_changed))
        self._map_changed({})

    @callback
    def _map_changed(self, _message: dict[str, Any]) -> None:
        # A new picture only when what it shows moved: not for each frame of a robot that stays put.
        message = self._map.message
        drawn = None
        if message is not None:
            path = message.get("path") or []
            drawn = (
                message.get("map_id"),
                message.get("frame_id") if message.get("robot") is None else tuple(message["robot"][:2]),
                tuple(message.get("dock") or ()),
                sum(len(s.get("points", [])) for s in path),
                id(message.get("rooms")),
            )
        if drawn == self._drawn:
            return
        self._drawn = drawn
        self._image = None
        self._attr_image_last_updated = dt_util.utcnow()
        self.async_write_ha_state()

    @property
    def available(self) -> bool:
        return super().available and self._map.message is not None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        message = self._map.message
        if message is None or not message.get("grid"):
            return {}
        rooms = {
            room: {"name": meta.get("name") or f"#{room}", "x": meta.get("x"), "y": meta.get("y")}
            for room, meta in sorted(message.get("rooms", {}).items(), key=lambda kv: int(kv[0]))
        }
        return {"map_id": message.get("map_id"), "calibration_points": calibration(message["grid"]), "rooms": rooms}

    def _draw(self) -> bytes | None:
        message = self._map.message
        if message is None or not message.get("grid"):
            return None
        picture = Picture(message)
        return png(picture.width, picture.height, picture.pixels)

    async def async_image(self) -> bytes | None:
        if self._image is None:
            self._image = await self.hass.async_add_executor_job(self._draw)
        return self._image
