"""Map frames: header, rooms, path, and nothing but MapError on bad input."""

from __future__ import annotations

import base64
import contextlib
import zlib

from hypothesis import given, strategies as st
import pytest

from custom_components.dreame_vacuum_3d.device import map as dmap
from tests.map_frames import LEFT, SIZE, TOP, frame, live


def _cells(runs: list[int]) -> set[tuple[int, int]]:
    return {(x, runs[i + 1]) for i in range(0, len(runs), 3) for x in range(runs[i], runs[i] + runs[i + 2])}


def test_header_gives_the_robot_the_dock_and_the_grid() -> None:
    f = dmap.decode(live(urlsafe=True))
    assert (f.map_id, f.frame_id, f.frame_type) == (7, 1, "I")
    assert f.robot == (-850, -400, 90)
    assert f.dock == (-900, -450, 90)
    assert f.grid == dmap.Grid(LEFT, TOP, SIZE, 8, 4)


@pytest.mark.parametrize(
    ("kwargs", "data", "robot", "dock"),
    [
        ({"robot": (32767, 32767, 0)}, {}, None, (-900, -450, 90)),
        ({"dock": (1, 2, 32767)}, {}, (-850, -400, 90), None),
        ({}, {"nr": 1, "nc": 1}, None, None),
    ],
)
def test_unknown_poses(kwargs, data, robot, dock) -> None:
    f = dmap.decode(frame([[0]], data, **kwargs))
    assert (f.robot, f.dock) == (robot, dock)


def test_origin_in_the_data_moves_the_grid() -> None:
    assert dmap.decode(frame([[0]], {"origin": [10, 20]})).grid.left == 10


def test_saved_map_rooms_names_and_walls() -> None:
    f = dmap.saved_map(dmap.decode(live()))
    assert f is not None and f.map_id == 45
    cells, rooms = dmap.rooms(f, saved=True)
    assert _cells(cells["1"]) == {(x, y) for x in range(3) for y in range(3)}
    assert _cells(cells["2"]) == {(x, y) for x in range(4, 8) for y in range(3)}  # the carpet bit is still room 2
    assert _cells(cells[dmap.WALL]) == {(x, 3) for x in (0, 1, 2, 4, 5, 6, 7)}
    assert rooms["1"]["name"] == "Cocina"
    assert rooms["2"]["name"] is None and rooms["2"]["type"] == 1
    # The centre is a cell of the room, in mm.
    x, y = rooms["2"]["x"], rooms["2"]["y"]
    assert ((x - LEFT) // SIZE, (y - TOP) // SIZE) in _cells(cells["2"])


def test_live_frames_without_a_saved_map() -> None:
    cells, rooms = dmap.rooms(dmap.decode(frame([[2, 1, 3, 0]])), saved=False)
    assert rooms == {}
    assert _cells(cells[dmap.WALL]) == {(0, 0)}
    assert _cells(cells[dmap.FLOOR]) == {(1, 0), (2, 0)}
    framed = dmap.decode(frame([[63 << 2, 62 << 2, 5 << 2, 61 << 2, 2]], {"fsm": 1}))
    cells, rooms = dmap.rooms(framed, saved=False)
    assert _cells(cells[dmap.WALL]) == {(0, 0), (4, 0)}
    assert _cells(cells[dmap.FLOOR]) == {(1, 0)}
    assert list(rooms) == ["5"]


def test_path_active_rooms_and_room_at() -> None:
    f = dmap.decode(live())
    assert dmap.path(f) == [[[-900, -450], [-850, -450], [-850, -400]], [[-700, -300], [-690, -290]]]
    assert dmap.active_rooms(f) == [2]
    cells, rooms = dmap.rooms(dmap.saved_map(f), saved=True)  # type: ignore[arg-type]
    assert dmap.room_at(f.grid, cells, rooms, LEFT + 60, TOP + 60) == 1
    assert dmap.room_at(f.grid, cells, rooms, LEFT + 5 * SIZE, TOP + 10) == 2
    assert dmap.room_at(f.grid, cells, rooms, LEFT + 3 * SIZE, TOP + 10) is None  # between the rooms
    assert dmap.saved_map(dmap.decode(frame([[0]]))) is None


@pytest.mark.parametrize(
    "raw",
    [
        "not base64!",
        base64.b64encode(b"not zlib").decode(),
        base64.b64encode(zlib.compress(b"short")).decode(),
        frame([[0, 0]])[:-8] + base64.b64encode(zlib.compress(b"x" * 27)).decode()[:8],
        "abc,key",
    ],
)
def test_bad_files_raise_map_error(raw: str) -> None:
    with pytest.raises(dmap.MapError):
        dmap.decode(raw)


def test_cut_cells_and_bad_json_raise_map_error() -> None:
    header_only = frame([[1, 2, 3]])
    blob = zlib.decompress(base64.b64decode(header_only))
    with pytest.raises(dmap.MapError, match="cut short"):
        dmap.decode(base64.b64encode(zlib.compress(blob[:-1])))
    with pytest.raises(dmap.MapError, match="JSON"):
        dmap.decode(base64.b64encode(zlib.compress(blob + b"{nope")))
    assert dmap.decode(base64.b64encode(zlib.compress(blob + b"[1]")).decode()).data == {}


def test_a_file_that_inflates_too_far_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(dmap, "_MAX_SIZE", 1000)
    with pytest.raises(dmap.MapError, match="too large"):
        dmap.decode(base64.b64encode(zlib.compress(bytes(5000))))


@given(st.binary(max_size=400))
def test_any_bytes_decode_or_raise_map_error(blob: bytes) -> None:
    for raw in (blob, base64.b64encode(zlib.compress(blob))):
        with contextlib.suppress(dmap.MapError):
            dmap.decode(raw)
