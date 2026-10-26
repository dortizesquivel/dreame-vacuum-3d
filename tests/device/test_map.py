"""Map frames: header, rooms, path, and nothing but MapError on bad input."""

from __future__ import annotations

import base64
import contextlib
import zlib

from hypothesis import given, strategies as st
import pytest

from custom_components.dreame_vacuum_3d.device import map as dmap
from tests.map_frames import LEFT, SIZE, TOP, encrypt, frame, live


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
    assert dmap.path(f) == [
        {"mode": "both", "points": [[-900, -450], [-850, -450], [-850, -400]]},
        {"mode": "vacuum", "points": [[-700, -300], [-690, -290]]},
        {"mode": "mop", "points": [[0, 0]]},
        {"mode": "mop", "points": [[5, 5]], "joins": True},
    ]
    assert dmap.active_rooms(f) == [2]
    cells, rooms = dmap.rooms(dmap.saved_map(f), saved=True)  # type: ignore[arg-type]
    assert dmap.room_at(f.grid, cells, rooms, LEFT + 60, TOP + 60) == 1
    assert dmap.room_at(f.grid, cells, rooms, LEFT + 5 * SIZE, TOP + 10) == 2
    assert dmap.room_at(f.grid, cells, rooms, LEFT + 3 * SIZE, TOP + 10) is None  # between the rooms
    assert dmap.saved_map(dmap.decode(frame([[0]]))) is None


IV = "0123456789abcdef"


def test_encrypted_frames_with_the_file_key_and_the_model_iv() -> None:
    sealed = encrypt(live(), "k3y", IV)
    assert dmap.decode(sealed, key="k3y", iv=IV).map_id == 7
    assert dmap.decode(f"{sealed},k3y", iv=IV).map_id == 7  # inline, the key rides along
    with pytest.raises(dmap.MapError, match="IV"):
        dmap.decode(sealed, key="k3y")
    with pytest.raises(dmap.MapError):
        dmap.decode(sealed, key="wrong", iv=IV)
    with pytest.raises(dmap.MapError, match="cut short"):
        dmap.decode(base64.b64encode(base64.b64decode(sealed)[:-1]), key="k3y", iv=IV)


@pytest.mark.parametrize(
    "raw",
    [
        "not base64!",
        base64.b64encode(b"not zlib").decode(),
        base64.b64encode(zlib.compress(b"short")).decode(),
        frame([[0, 0]])[:-8] + base64.b64encode(zlib.compress(b"x" * 27)).decode()[:8],
        "abc,key",  # encrypted, but no IV
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


def test_obstacles_as_the_app_shows_them() -> None:
    f = dmap.decode(
        frame(
            [[0]],
            {
                "ai_obstacle": [
                    [-4672.6, 6268.6, 133, 0.99, "1700", "", "", "", "", "", "", "", "", "", "", "", "", "0", 0],
                    [100, 200, 134, "0.4"],  # not sure enough
                    [100, 200, 128, 0.9],  # the dock
                    [100, 200, 131, 0.9, *[""] * 13, "1"],  # a "small object"
                    [100, 200, 132, 0.9, *[""] * 14, 4],  # hidden
                    [300, 400, 999, 0.8],  # a code it doesn't name: an obstacle
                    [1, 2],
                    "junk",
                    [1, 2, "x", 0.9],
                ],
                "laser_obstacle": [[500, 600, 142], [700, 800, 135, 0.6, "id"]],
            },
        )
    )
    assert dmap.obstacles(f) == [
        {"x": -4673, "y": 6269, "type": "shoes", "confidence": 99, "source": "camera"},
        {"x": 300, "y": 400, "type": "obstacle", "confidence": 80, "source": "camera"},
        {"x": 500, "y": 600, "type": "obstacle", "confidence": 100, "source": "lidar"},
        {"x": 700, "y": 800, "type": "pet_waste", "confidence": 60, "source": "lidar"},
    ]
    assert dmap.obstacles(dmap.decode(frame([[0]], {"ai_obstacle": "nope"}))) == []


def test_furniture_prefers_the_newest_list() -> None:
    old = [[0, 0, 2, 3, -100, -200, 1750, 2000], [0, 0, 6, 3, 0, 0, 800, 400, 0]]
    f = dmap.decode(frame([[0]], {"ai_furniture": old}))
    assert dmap.furniture(f) == [
        {"type": "double_bed", "x0": -100, "y0": -200, "width": 1750, "height": 2000, "angle": 0.0},
        {"type": "dining_table", "x0": 0, "y0": 0, "width": 800, "height": 400, "angle": 180.0},  # flipped
    ]
    new = [[0, 0, 5, 2, -1725, -825, -2300, 750, 90], [0, 0, 99, 1, 0, 0, 5, 5], [0, 0, 4, 1], [0, 0, 4, 1, 0, 0, 0, 5]]
    f = dmap.decode(frame([[0]], {"ai_furniture": old, "ai_furniture_new": new}))
    assert dmap.furniture(f) == [
        {"type": "three_seat_sofa", "x0": -1725, "y0": -825, "width": 2300, "height": 750, "angle": 90.0}
    ]
    assert dmap.furniture(dmap.decode(frame([[0]], {"ai_furniture": [[0, 0, "x", 1, 0, 0, 1, 1]]}))) == []


def test_seen_cells_are_walls_on_open_floor_away_from_old_walls() -> None:
    # Saved: a 10 by 6 room (id 1) with an edge of walls all round.
    rows = [[0x81] * 10] + [[0x81] + [1] * 8 + [0x81] for _ in range(4)] + [[0x81] * 10]
    saved = dmap.decode(frame(rows))
    mask = dmap.floor_mask(saved, margin=1)
    assert sum(mask) == 6 * 2  # 8 by 4 floor, less one cell along each wall
    # Live, half a cell to the left: the old walls, a new 2-cell thing in the middle and one by the wall.
    live_rows = [[2 if v & 0x80 else 3 for v in row] for row in rows]
    live_rows[3][4] = live_rows[3][5] = 2
    live_rows[1][1] = 2
    live = dmap.Frame(
        1, 1, "I", None, None, dmap.Grid(LEFT - 25, TOP, SIZE, 10, 6), bytes(v for row in live_rows for v in row), {}
    )
    # Live cell 4's centre is on saved cell 4's edge.
    assert dmap.runs(dmap.seen_cells(live, saved.grid, mask)) == [4, 3, 2]
    framed = dmap.Frame(1, 1, "I", None, None, saved.grid, bytes([63 << 2] * 60), {"fsm": 1})
    assert len(dmap.seen_cells(framed, saved.grid, mask)) == 12  # the whole open floor
    assert dmap.runs({(5, 1), (3, 0), (4, 0), (7, 0)}) == [3, 0, 2, 7, 0, 1, 5, 1, 1]


def test_the_rooms_a_cleaning_cleaned() -> None:
    # The rooms it was sent to; with none (a whole-house cleaning), the rooms its path went through.
    assert dmap.cleaned_rooms(dmap.decode(live())) == [2]
    assert dmap.cleaned_rooms(dmap.decode(live({"sa": []}))) == [1]
