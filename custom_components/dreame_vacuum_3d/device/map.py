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


# Things on the floor the robot names (Tasshack's ObstacleType). Others it reports become "obstacle", except these.
_OBSTACLES = {
    129: "scale", 130: "thread", 131: "wire", 132: "toy", 133: "shoes", 134: "sock", 135: "pet_waste",
    136: "trash_can", 137: "fabric", 138: "power_strip", 139: "liquid_stain", 142: "obstacle", 158: "pet",
    163: "cleaning_tools", 169: "stain", 202: "stain", 204: "leg", 205: "particles", 206: "stain", 209: "food_bowl",
    210: "pet_bed", 225: "hair", 226: "paper", 229: "sundry",
}  # fmt: skip
# The dock itself, codes the app hides, rooms it couldn't enter, spots it got stuck, stains it already cleaned.
_NOT_OBSTACLES = {128, 160, 166, 200, 201, 207, 214, 215, 216, 217, 228}
_IGNORED = {2, 3, 4}  # ignored automatically, an error, hidden

_FURNITURE = {
    1: "single_bed", 2: "double_bed", 3: "armchair", 4: "two_seat_sofa", 5: "three_seat_sofa", 6: "dining_table",
    7: "nightstand", 8: "coffee_table", 9: "toilet", 10: "litter_box", 11: "pet_bed", 12: "food_bowl",
    13: "pet_toilet", 14: "refrigerator", 15: "washing_machine", 16: "litter_box", 17: "air_conditioner",
    18: "tv_cabinet", 19: "bookshelf", 20: "shoe_cabinet", 21: "wardrobe", 22: "plant", 23: "floor_mirror",
    24: "l_shaped_sofa", 25: "coffee_table", 26: "table", 29: "armchair", 30: "three_seat_sofa",
}  # fmt: skip


def obstacles(frame: Frame) -> list[dict[str, Any]]:
    """What the robot found on the floor: [{x, y (mm), type, confidence (%), source: camera | lidar}]."""
    out: list[dict[str, Any]] = []
    for key, source in (("ai_obstacle", "camera"), ("laser_obstacle", "lidar")):
        items = frame.data.get(key)
        for o in items if isinstance(items, list) else []:
            if not isinstance(o, list) or len(o) < 3:
                continue
            try:
                x, y, kind = float(o[0]), float(o[1]), int(o[2])
                confidence = round(float(o[3]) * 100) if len(o) > 3 and str(o[3]).strip() else 100
            except TypeError, ValueError:
                continue
            # The app hides "small objects", and anything it is less than half sure of.
            small = len(o) > 17 and str(o[17]) == "1" and (128 <= kind <= 138 or kind == 142)
            if kind in _NOT_OBSTACLES or small or confidence < 50 or _ignore_status(o) in _IGNORED:
                continue
            out.append({"x": round(x), "y": round(y), "type": _OBSTACLES.get(kind, "obstacle"),
                        "confidence": confidence, "source": source})  # fmt: skip
    return out


def _ignore_status(o: list[Any]) -> int:
    value = o[18] if len(o) > 18 else o[-1] if len(str(o[-1])) == 1 else 0
    try:
        return int(value)
    except TypeError, ValueError:
        return 0


def furniture(frame: Frame) -> list[dict[str, Any]]:
    """Furniture the robot's camera recognised: [{type, x0, y0, width, height (mm), angle (degrees)}]."""
    key = next((k for k in ("ai_furniture_user", "ai_furniture_new", "ai_furniture") if frame.data.get(k)), None)
    out: list[dict[str, Any]] = []
    for f in frame.data[key] if key and isinstance(frame.data[key], list) else []:
        if not isinstance(f, list) or len(f) < 8:  # older entries are only a point
            continue
        try:
            kind, x0, y0, width, height = int(f[2]), int(f[4]), int(f[5]), abs(int(f[6])), abs(int(f[7]))
            angle = float(f[8]) if len(f) > 8 else 0.0
        except TypeError, ValueError:
            continue
        if kind not in _FURNITURE or not width or not height:
            continue
        if key == "ai_furniture" and len(f) > 8 and angle in (0, 180):  # this list has them the other way round
            angle = 180 - angle
        out.append({"type": _FURNITURE[kind], "x0": x0, "y0": y0, "width": width, "height": height, "angle": angle})
    return out


def floor_mask(saved: Frame, margin: int = 2) -> bytearray:
    """1 for each cell of a room's floor at least `margin` cells from the saved map's walls, else 0."""
    width, height = saved.grid.width, saved.grid.height
    mask = bytearray(1 if (v & 0x3F) and not v & 0x80 else 0 for v in saved.cells)
    for i, v in enumerate(saved.cells):
        if v & 0x80:
            x, y = i % width, i // width
            for yy in range(max(0, y - margin), min(height, y + margin + 1)):
                start = yy * width
                mask[start + max(0, x - margin) : start + min(width, x + margin + 1)] = bytes(
                    min(width, x + margin + 1) - max(0, x - margin)
                )
    return mask


def seen_cells(live: Frame, saved: Grid, mask: bytearray) -> set[tuple[int, int]]:
    """Cells where the live map sees a wall or an obstacle on what the saved map has as open floor (furniture legs,
    chairs, a box…), as (x, y) in the saved map's grid. The saved map keeps only the rooms and their edges."""
    key = _framed_cell if live.data.get("fsm") == 1 else _live_cell
    walls = bytes(1 if key(v) == WALL else 0 for v in range(256))
    width, size = live.grid.width, live.grid.size
    hits: set[tuple[int, int]] = set()
    for y in range(live.grid.height):
        row = live.cells[y * width : (y + 1) * width].translate(walls)
        for run in re.finditer(b"\x01+", row):
            sy = int((live.grid.top + (y + 0.5) * size - saved.top) // saved.size)
            for x in range(run.start(), run.end()):
                sx = int((live.grid.left + (x + 0.5) * size - saved.left) // saved.size)
                if 0 <= sx < saved.width and 0 <= sy < saved.height and mask[sy * saved.width + sx]:
                    hits.add((sx, sy))
    return hits


def runs(cells: set[tuple[int, int]]) -> list[int]:
    """Cells as runs [x, y, n, …], row by row."""
    out: list[int] = []
    for x, y in sorted(cells, key=lambda c: (c[1], c[0])):
        if out and out[-2] == y and out[-3] + out[-1] == x:
            out[-1] += 1
        else:
            out.extend((x, y, 1))
    return out


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
