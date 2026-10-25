"""Map frames from a Dreame robot: where it is, where its dock is, its rooms and the path it drove.

The robot uploads each frame to the Xiaomi cloud as base64 of zlib data: a 27-byte header, one byte per 50 mm cell and
a JSON tail. Only what the 3D card needs is decoded; the house itself is the user's own 3D model.

Portions derived from Tasshack/dreame-vacuum (MIT), see NOTICE.
"""

from __future__ import annotations

import base64
import binascii
from collections.abc import Callable
from dataclasses import dataclass
import hashlib
from itertools import groupby
import json
import re
import struct
from typing import Any
import zlib

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

# map id, frame id, frame type, robot x, y, angle, dock x, y, angle, cell size (mm), width, height, left, top
_HEADER = struct.Struct("<hhbhhhhhhhhhhh")
_UNKNOWN = 32767  # a pose the robot doesn't know
_MAX_SIZE = 16 * 1024 * 1024  # a real frame is well under 1 MB; anything this big isn't a map
_PATH = re.compile(r"([MWSLl])(-?\d+),(-?\d+)")

WALL = "255"  # cells keys: a room's id, or one of these
FLOOR = "254"  # floor that isn't in a room yet

type Pose = tuple[int, int, int]
type Runs = dict[str, list[int]]


class MapError(ValueError):
    """A map file that can't be read."""


@dataclass(frozen=True)
class Grid:
    left: int
    top: int
    size: int
    width: int
    height: int


@dataclass(frozen=True)
class Frame:
    map_id: int
    frame_id: int
    frame_type: str  # "I": the whole map, "P": changes since the last frame
    robot: Pose | None
    dock: Pose | None
    grid: Grid
    cells: bytes
    data: dict[str, Any]


def decode(raw: str | bytes, *, key: str | None = None, iv: str | None = None) -> Frame:
    """Decode one map file, as the cloud serves it.

    Live frames can be encrypted (AES-CBC): with the key the robot names with the file ("name,key", or "map,key" when
    the map comes inline) and the model's IV.
    """
    text = (raw.decode("latin-1") if isinstance(raw, bytes) else raw).strip()  # base64 is ASCII; never fails here
    if "," in text:
        text, _, inline_key = text.partition(",")
        key = key or inline_key
    try:
        payload = base64.b64decode(text.replace("_", "/").replace("-", "+"))
        if key:
            payload = _decrypt(payload, key, iv)
        inflate = zlib.decompressobj()
        blob = inflate.decompress(payload, _MAX_SIZE)
    except (binascii.Error, zlib.error, ValueError) as err:
        raise MapError(f"not a map file: {err}") from err
    if inflate.unconsumed_tail:
        raise MapError("map file too large")
    if len(blob) < _HEADER.size:
        raise MapError("map header cut short")
    map_id, frame_id, kind, rx, ry, ra, cx, cy, ca, size, width, height, left, top = _HEADER.unpack_from(blob)
    end = _HEADER.size + width * height
    if size <= 0 or width < 0 or height < 0 or len(blob) < end:
        raise MapError("map cells cut short")
    try:
        data = json.loads(blob[end:]) if len(blob) > end else {}
    except (UnicodeDecodeError, ValueError) as err:
        raise MapError(f"map data isn't JSON: {err}") from err
    if not isinstance(data, dict):
        data = {}
    origin = data.get("origin")
    if isinstance(origin, list) and len(origin) > 1:
        left, top = int(origin[0]), int(origin[1])
    return Frame(
        map_id=map_id,
        frame_id=frame_id,
        frame_type=chr(kind) if 32 < kind < 127 else str(kind),
        robot=None if data.get("nr") else _pose(rx, ry, ra),
        dock=None if data.get("nc") else _pose(cx, cy, ca),
        grid=Grid(left, top, size, width, height),
        cells=blob[_HEADER.size : end],
        data=data,
    )


def _decrypt(data: bytes, key: str, iv: str | None) -> bytes:
    if not iv:
        raise MapError("the map is encrypted, and this model's IV isn't known")
    if len(data) % 16:
        raise MapError("encrypted map cut short")
    aes = hashlib.sha256(key.encode()).hexdigest()[:32].encode()
    decryptor = Cipher(algorithms.AES(aes), modes.CBC(iv.encode())).decryptor()
    return decryptor.update(data) + decryptor.finalize()  # zlib stops at its end: the padding is left over


def _pose(x: int, y: int, angle: int) -> Pose | None:
    return None if angle == _UNKNOWN or (x == _UNKNOWN and y == _UNKNOWN) else (x, y, angle)


def saved_map(frame: Frame) -> Frame | None:
    """The saved map a live frame carries (`rism`): the one with the rooms."""
    raw = frame.data.get("rism")
    return decode(raw) if isinstance(raw, str) and raw else None


def _runs(frame: Frame, key: Callable[[int], str | None]) -> Runs:
    """Cells as runs [x, y, n, …] per key, row by row (row 0 is the map's smallest y)."""
    out: Runs = {}
    width = frame.grid.width
    for y in range(frame.grid.height):
        x = 0
        for value, run in groupby(frame.cells[y * width : (y + 1) * width]):
            n = sum(1 for _ in run)
            if (k := key(value)) is not None:
                out.setdefault(k, []).extend((x, y, n))
            x += n
    return out


def _saved_cell(value: int) -> str | None:
    # A saved map: the room's id in the low 6 bits, its edge (a wall) in the top bit; 0x40 is carpet.
    room = value & 0x3F
    if value & 0x80:
        return WALL
    return str(room) if room else None


def _live_cell(value: int) -> str | None:
    # A live map before it is saved: 2 is a wall, 1 and 3 floor (as the app draws them).
    kind = value & 0x3F
    return WALL if kind == 2 else FLOOR if kind in (1, 3) else None


def _framed_cell(value: int) -> str | None:
    # A live map with rooms (`fsm`): the room's id in the top 6 bits; 63 wall, 62 floor, 61 unknown.
    room = value >> 2
    if room == 63:
        return WALL
    if room == 62:
        return FLOOR
    if 0 < room < 61:
        return str(room)
    return _live_cell(value) if room == 0 else None


def rooms(frame: Frame, *, saved: bool) -> tuple[Runs, dict[str, dict[str, Any]]]:
    """The cells of each room and the rooms' names, types and centres (mm), from a saved map or a live frame."""
    key = _saved_cell if saved else _framed_cell if frame.data.get("fsm") == 1 else _live_cell
    cells = _runs(frame, key)
    info: dict[str, dict[str, Any]] = {}
    seg_inf = frame.data.get("seg_inf")
    for room, runs in cells.items():
        if room in (WALL, FLOOR):
            continue
        meta = seg_inf.get(room, {}) if isinstance(seg_inf, dict) else {}
        x, y = _centre(frame.grid, runs)
        info[room] = {"name": _name(meta.get("name")), "type": meta.get("type", 0), "x": x, "y": y}
    return cells, info


def _name(value: Any) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return base64.b64decode(value).decode().strip() or None
    except binascii.Error, UnicodeDecodeError:
        return None


def _centre(grid: Grid, runs: list[int]) -> tuple[int, int]:
    """The room's cell nearest its centroid (inside the room even when it is L-shaped), in mm."""
    total, sx, sy = 0, 0.0, 0.0
    for i in range(0, len(runs), 3):
        x, y, n = runs[i : i + 3]
        total += n
        sx += n * x + n * (n - 1) / 2
        sy += n * y
    mx, my = sx / total, sy / total
    best, bx, by = float("inf"), 0, 0
    for i in range(0, len(runs), 3):
        x, y, n = runs[i : i + 3]
        cx = min(max(round(mx), x), x + n - 1)
        if (d := (cx - mx) ** 2 + (y - my) ** 2) < best:
            best, bx, by = d, cx, y
    return grid.left + bx * grid.size + grid.size // 2, grid.top + by * grid.size + grid.size // 2


# What the robot did along a stroke of its path.
_MODES = {"S": "vacuum", "W": "both", "M": "mop"}


def path(frame: Frame) -> list[dict[str, Any]]:
    """The path in mm, as strokes {"mode": vacuum | mop | both, "points": [[x, y], …]}.

    S, W and M start a stroke at a point; L continues it, relative to the last point; l (in P-frames) continues the
    previous frame's stroke.
    """
    strokes: list[dict[str, Any]] = []
    x = y = 0
    for op, a, b in _PATH.findall(str(frame.data.get("tr") or "")):
        if op == "L" and strokes:
            x, y = x + int(a), y + int(b)
            strokes[-1]["points"].append([x, y])
        else:
            x, y = int(a), int(b)
            mode = _MODES.get(op) or (strokes[-1]["mode"] if strokes else "vacuum")
            strokes.append({"mode": mode, "points": [[x, y]]})
    return strokes


def active_rooms(frame: Frame) -> list[int]:
    """Rooms the robot is cleaning (`sa`: [[room, …], …])."""
    sa = frame.data.get("sa")
    return [int(s[0]) for s in sa if isinstance(s, list) and s] if isinstance(sa, list) else []


def room_at(grid: Grid, cells: Runs, info: dict[str, Any], x: int, y: int) -> int | None:
    """Which room a point (mm) is in."""
    cx, cy = (x - grid.left) // grid.size, (y - grid.top) // grid.size
    for room in info:
        runs = cells.get(room, [])
        for i in range(0, len(runs), 3):
            if runs[i + 1] == cy and runs[i] <= cx < runs[i] + runs[i + 2]:
                return int(room)
    return None
