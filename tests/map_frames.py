"""Build map files like the robot uploads them: base64 of zlib(27-byte header + cells + JSON)."""

from __future__ import annotations

import base64
import json
import struct
from typing import Any
import zlib

LEFT, TOP, SIZE = -1000, -500, 50

# A saved map, 8 by 4 cells: room 1 on the left, room 2 on the right, an edge (wall) row on top.
#   row 3: walls (0x80 | room)
#   rows 0-2: 1 1 1 0 2 2 2 2
SAVED_ROWS = [
    [1, 1, 1, 0, 2, 2, 2, 2],
    [1, 1, 1, 0, 2, 2, 2 | 0x40, 2],
    [1, 1, 1, 0, 2, 2, 2, 2],
    [0x81, 0x81, 0x81, 0, 0x82, 0x82, 0x82, 0x82],
]
SEG_INF = {
    "1": {"type": 0, "name": base64.b64encode(b"Cocina").decode(), "nei_id": [2]},
    "2": {"type": 1, "name": "", "nei_id": [1]},
}


def frame(
    rows: list[list[int]],
    data: dict[str, Any] | None = None,
    *,
    map_id: int = 7,
    frame_id: int = 1,
    kind: str = "I",
    robot: tuple[int, int, int] = (-850, -400, 90),
    dock: tuple[int, int, int] = (-900, -450, 90),
    urlsafe: bool = False,
) -> str:
    height, width = len(rows), len(rows[0]) if rows else 0
    header = struct.pack("<hhbhhhhhhhhhhh", map_id, frame_id, ord(kind), *robot, *dock, SIZE, width, height, LEFT, TOP)
    cells = bytes(v for row in rows for v in row)
    tail = json.dumps(data).encode() if data is not None else b""
    text = base64.b64encode(zlib.compress(header + cells + tail)).decode()
    return text.replace("/", "_").replace("+", "-") if urlsafe else text


def saved(**kwargs: Any) -> str:
    return frame(SAVED_ROWS, {"seg_inf": SEG_INF}, map_id=45, frame_id=0, robot=(0, 0, 0), **kwargs)


def live(data: dict[str, Any] | None = None, **kwargs: Any) -> str:
    """A live I-frame of the same house, carrying the saved map as `rism`."""
    rows = [[3 if v else 0 for v in row] for row in SAVED_ROWS]
    return frame(
        rows, {"rism": saved(), "tr": "M-900,-450L50,0L0,50S-700,-300L10,10", "sa": [[2, 1]], **(data or {})}, **kwargs
    )
