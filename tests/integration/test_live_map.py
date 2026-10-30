"""The live map: from the robot to the room entities and the card's WebSocket subscription."""

from __future__ import annotations

from datetime import timedelta
import json
import logging
import time
from unittest.mock import AsyncMock, patch

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
import pytest
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.dreame_vacuum_3d.const import DOMAIN
from custom_components.dreame_vacuum_3d.device import map as dmap
from custom_components.dreame_vacuum_3d.device.capabilities import load
from custom_components.dreame_vacuum_3d.device.enums import Status, TaskStatus
from custom_components.dreame_vacuum_3d.live_map import FIRST_RETRY, IDLE_EVERY
from custom_components.dreame_vacuum_3d.protocol.cloud import CloudAuthError, CloudConnectionError
from custom_components.dreame_vacuum_3d.protocol.miio import MiioTimeoutError
from tests.integration.helpers import setup_fixture_entry
from tests.integration.test_init import ENTRY_DATA
from tests.map_frames import SAVED_ROWS, SEG_INF, encrypt, frame, live, saved

DID = ENTRY_DATA["did"]
IN_ROOM_1 = (-925, -425, 90)  # cell (1, 1)


def _log_reads(miio) -> int:
    """Reads of the cloud's log of map frames (not of its events, the cleaning history)."""
    return sum(1 for c in miio.cloud.device_data.await_args_list if c.kwargs.get("kind", "prop") == "prop")


def _map_requests(miio) -> int:
    return sum(1 for method, params in miio.calls if method == "action" and (params["siid"], params["aiid"]) == (6, 1))


def _entity(hass: HomeAssistant, platform: str, key: str) -> str:
    entity_id = er.async_get(hass).async_get_entity_id(platform, DOMAIN, f"{DID}_{key}")
    assert entity_id, key
    return entity_id


async def test_map_feeds_the_room_entities(hass: HomeAssistant, card_urls: list[str]) -> None:
    entry, miio = await setup(hass)
    message = entry.runtime_data.map.message
    assert message["map_id"] == 45
    assert message["robot"] == list(IN_ROOM_1) and message["dock"] == [-900, -450, 90]
    assert message["room"] == 1 and message["active"] == [2]
    assert message["path"][0] == {"mode": "both", "points": [[-900, -450], [-850, -450], [-850, -400]]}
    assert set(message["rooms"]) == {"1", "2"} and "255" in message["cells"]
    assert message["obstacles"] == [] and message["furniture"] == []
    assert message["new_cells"] == []  # the live frame has the same walls as the saved map

    room = hass.states.get(_entity(hass, "sensor", "current_room"))
    assert room.state == "Cocina" and room.attributes["segment_id"] == 1
    assert hass.states.get(_entity(hass, "button", "clean_room_45_1")).name.endswith("Clean Cocina")
    assert hass.states.get(_entity(hass, "button", "clean_room_45_2")).name.endswith("Clean #2")

    miio.calls.clear()
    await hass.services.async_call(
        "button", "press", {"entity_id": _entity(hass, "button", "clean_room_45_1")}, blocking=True
    )
    actions = [params for method, params in miio.calls if method == "action"]
    assert any('"selects":[[1,' in str(p["in"]) for p in actions)

    assert any(url.startswith(f"/{DOMAIN}/dreame-vacuum-3d-card.js?v=") for url in card_urls)


async def test_without_a_map_the_room_sensor_is_unknown(hass: HomeAssistant) -> None:
    entry, _miio = await setup_fixture_entry(hass)
    assert entry.runtime_data.map.message is None
    assert hass.states.get(_entity(hass, "sensor", "current_room")).state == "unknown"


async def test_until_a_first_map_comes_it_asks_again_soon(hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch) -> None:
    # Right after Home Assistant starts, a sleeping robot may send no map: don't wait IDLE_EVERY for the next try.
    _entry, miio = await setup_fixture_entry(hass)
    start = _map_requests(miio)
    later = time.monotonic() + FIRST_RETRY + 1
    monkeypatch.setattr("custom_components.dreame_vacuum_3d.live_map.monotonic", lambda: later)
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=5), fire_all=True)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert _map_requests(miio) == start + 1
    assert FIRST_RETRY < IDLE_EVERY


async def test_websocket_subscription(hass: HomeAssistant, hass_ws_client) -> None:
    entry, _miio = await setup(hass)
    client = await hass_ws_client(hass)
    await client.send_json_auto_id({"type": f"{DOMAIN}/map/subscribe", "entity_id": "vacuum.x10"})
    assert (await client.receive_json())["success"]
    first = (await client.receive_json())["event"]
    assert first["rooms"]["1"]["name"] == "Cocina" and first["room"] == 1

    # A new frame of the same saved map: only the robot's side.
    await entry.runtime_data.map.refresh()
    update = (await client.receive_json())["event"]
    assert "cells" not in update and update["robot"] == list(IN_ROOM_1)

    # The subscription survives a reload of the entry, and gets the rooms again from the new map.
    await setup_reload(hass, entry)
    again = (await client.receive_json())["event"]
    assert "cells" in again

    await client.send_json_auto_id({"type": f"{DOMAIN}/map/subscribe", "entity_id": "sensor.x10_battery"})
    error = await client.receive_json()
    assert not error["success"] and error["error"]["code"] == "not_found"


async def test_frames_while_moving_then_one_more(hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("custom_components.dreame_vacuum_3d.live_map.UNWATCHED_EVERY", 0)  # a read each tick
    entry, miio = await setup(hass)
    device = entry.runtime_data.coordinator.device
    start = _map_requests(miio)

    ticks = iter(range(1, 100))

    def status(value: Status) -> None:
        # In the robot too: the coordinator reads it again as time moves on.
        miio.overrides[(4, 1)] = device.values["status"] = value.value

    def tick() -> None:
        async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=5 * next(ticks)), fire_all=True)

    tick()
    await hass.async_block_till_done(wait_background_tasks=True)
    assert _map_requests(miio) == start  # docked: nothing new

    # Cleaning: each tick reads what the robot logged in the cloud; a whole frame only every WHOLE_EVERY.
    status(Status.CLEANING)
    tick()
    await hass.async_block_till_done(wait_background_tasks=True)
    assert _map_requests(miio) == start
    assert _log_reads(miio) == 2  # its partial frames (6.1) and whole ones (6.3)
    monkeypatch.setattr("custom_components.dreame_vacuum_3d.live_map.WHOLE_EVERY", 0)
    tick()
    await hass.async_block_till_done(wait_background_tasks=True)
    assert _map_requests(miio) == start + 1

    status(Status.IDLE)
    tick()
    await hass.async_block_till_done(wait_background_tasks=True)
    assert _map_requests(miio) == start + 2  # the frame with the whole path

    monkeypatch.setattr("custom_components.dreame_vacuum_3d.live_map.monotonic", lambda: 10.0**9)
    tick()
    await hass.async_block_till_done(wait_background_tasks=True)
    assert _map_requests(miio) == start + 3  # IDLE_EVERY later
    assert IDLE_EVERY >= 60


async def test_each_tick_only_while_a_card_shows_the_map(hass: HomeAssistant, hass_ws_client, freezer) -> None:
    entry, miio = await setup(hass)
    device = entry.runtime_data.coordinator.device
    miio.overrides[(4, 1)] = device.values["status"] = Status.CLEANING.value

    async def ticks(n: int) -> int:  # two calls a read: the partial frames (6.1) and the whole ones (6.3)
        miio.cloud.device_data.reset_mock()
        for _ in range(n):
            freezer.tick(timedelta(seconds=2.5))
            async_fire_time_changed(hass)
            await hass.async_block_till_done(wait_background_tasks=True)
        return _log_reads(miio) // 2

    assert await ticks(4) == 2  # nobody looking: every other tick
    client = await hass_ws_client(hass)
    await client.send_json_auto_id({"type": f"{DOMAIN}/map/subscribe", "entity_id": "vacuum.x10"})
    assert (await client.receive_json())["success"]
    assert await ticks(4) == 4  # a card open: each tick
    await client.send_json_auto_id({"type": "unsubscribe_events", "subscription": 1})
    while (reply := await client.receive_json())["type"] == "event":
        pass  # the map's messages until then
    assert reply["success"]
    assert await ticks(4) == 2


def _map_warnings(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    """The integration's own warnings about the map (asyncio's slow-callback warning names live_map.py too)."""
    return [
        r
        for r in caplog.records
        if r.levelno == logging.WARNING
        and r.name.startswith("custom_components.dreame_vacuum_3d")
        and "map" in r.getMessage()
    ]


async def test_errors_keep_the_last_map_and_warn_once(hass: HomeAssistant, caplog: pytest.LogCaptureFixture) -> None:
    entry, miio = await setup(hass)
    live_map = entry.runtime_data.map
    before = live_map.message
    device = entry.runtime_data.coordinator.device
    device.request_map = AsyncMock(side_effect=MiioTimeoutError("no answer"))
    with caplog.at_level(logging.DEBUG):
        await live_map.refresh()
        await live_map.refresh()
    warnings = _map_warnings(caplog)
    assert len(warnings) == 1
    assert live_map.message == before

    device.request_map = AsyncMock(return_value=(None, None))
    await live_map.refresh()
    miio.cloud.download.return_value = None
    device.request_map = AsyncMock(return_value=(None, "1000001/123/0"))
    await live_map.refresh()
    assert live_map.message == before

    # A map sent inline needs no cloud.
    device.request_map = AsyncMock(return_value=(live(robot=(-700, -400, 0)), None))
    await live_map.refresh()
    assert live_map.message["robot"] == [-700, -400, 0] and live_map.message["room"] == 2


async def test_a_malformed_saved_map_warns_once_and_keeps_the_last_map(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    entry, _miio = await setup(hass)
    live_map = entry.runtime_data.map
    before = live_map.message
    device = entry.runtime_data.coordinator.device
    device.request_map = AsyncMock(return_value=(live({"rism": "not a map"}, robot=IN_ROOM_1), None))
    with caplog.at_level(logging.DEBUG):
        await live_map.refresh()
        await live_map.refresh()
    assert len(_map_warnings(caplog)) == 1
    assert "Skipping a map frame" in caplog.text
    assert live_map.message == before

    # Good again, the next bad one is news.
    device.request_map = AsyncMock(return_value=(live(robot=IN_ROOM_1), None))
    await live_map.refresh()
    device.request_map = AsyncMock(return_value=(live({"rism": "still not a map"}, robot=IN_ROOM_1), None))
    await live_map.refresh()
    assert len(_map_warnings(caplog)) == 2


@pytest.mark.parametrize(
    "data",
    [
        {"sa": [["kitchen", 1]]},
        {"sa": [[None]]},
        {"rism": frame(SAVED_ROWS, {"seg_inf": {"1": "kitchen"}}, map_id=45, frame_id=0, robot=(0, 0, 0))},
        {"rism": frame(SAVED_ROWS, {"seg_inf": {"1": {"name": "cocinañ"}}}, map_id=45, frame_id=0, robot=(0, 0, 0))},
    ],
)
async def test_odd_map_data_is_left_out(hass: HomeAssistant, data: dict) -> None:
    entry, _miio = await setup(hass)
    device = entry.runtime_data.coordinator.device
    device.request_map = AsyncMock(return_value=(live(data, robot=(-700, -400, 0)), None))
    await entry.runtime_data.map.refresh()  # used to raise, in a task nobody awaits: an error in the log each tick
    assert entry.runtime_data.map.message["robot"] == [-700, -400, 0]


async def test_a_map_with_a_bad_origin_is_a_map_error(hass: HomeAssistant, caplog: pytest.LogCaptureFixture) -> None:
    entry, _miio = await setup(hass)
    device = entry.runtime_data.coordinator.device
    device.request_map = AsyncMock(return_value=(live({"origin": ["left", 0]}), None))
    await entry.runtime_data.map.refresh()
    await entry.runtime_data.map.refresh()
    assert len(_map_warnings(caplog)) == 1


async def test_a_zone_cleaning_tells_the_card_its_zones_until_the_task_ends(hass: HomeAssistant) -> None:
    entry, _miio = await setup(hass)
    live_map = entry.runtime_data.map
    assert live_map.message["zones"] == []
    device = entry.runtime_data.coordinator.device
    await device.clean_zone([100, 200, 300, 400])
    device.request_map = AsyncMock(return_value=(live(robot=IN_ROOM_1), None))
    await live_map.refresh()
    assert live_map.message["zones"] == [[100, 200, 300, 400]]

    # While it cleans it mostly sends partial frames: the zones go with them.
    seen = []
    live_map.listen(seen.append)
    await device.clean_zone([[1, 2, 3, 4], [5, 6, 7, 8]])
    map_id, frame_id = live_map._at
    live_map._apply_partial(dmap.decode(frame([[0]], map_id=map_id, frame_id=frame_id + 1, kind="P")))
    assert seen[-1]["zones"] == [[1, 2, 3, 4], [5, 6, 7, 8]]

    # Rooms next: they aren't a zone's, and the other way round.
    await device.clean_segment(3)
    assert device.zones is None
    await device.clean_zone([100, 200, 300, 400])
    assert device.segments is None


async def test_no_warning_while_paused(hass: HomeAssistant, caplog: pytest.LogCaptureFixture) -> None:
    entry, _miio = await setup(hass)
    device = entry.runtime_data.coordinator.device
    device.values["status"] = Status.PAUSED.value
    device.request_map = AsyncMock(return_value=(None, None))  # paused mid-task, as while cleaning: no map
    with caplog.at_level(logging.DEBUG):
        await entry.runtime_data.map.refresh()
    assert "the robot sent no map" in caplog.text
    assert not _map_warnings(caplog)


def _with(*things: tuple[int, int], map_id: int = 45) -> str:
    """A live frame of the test house where the lidar sees these cells, and an obstacle and a sofa."""
    rows = [[3 if v else 0 for v in row] for row in SAVED_ROWS]
    for x, y in things:
        rows[y][x] = 2
    data = {
        "rism": frame(SAVED_ROWS, {"seg_inf": SEG_INF}, map_id=map_id, frame_id=0),
        "ai_obstacle": [[-925, -425, 133, 0.99]],
        "ai_furniture_new": [[0, 0, 4, 1, -1000, -500, 150, 100]],
    }
    return frame(rows, data)


async def test_obstacles_furniture_and_what_is_new_since_the_last_cleaning(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("custom_components.dreame_vacuum_3d.live_map.WHOLE_EVERY", 0)  # a whole frame each time
    entry, _miio = await setup(hass)  # its first frame, with nothing on the floor, is the reference

    async def frame_with(*things: tuple[int, int], map_id: int = 45) -> list[int]:
        entry.runtime_data.coordinator.device.request_map = AsyncMock(
            return_value=(_with(*things, map_id=map_id), None)
        )
        await entry.runtime_data.map.refresh()
        return entry.runtime_data.map.pose["new_cells"]

    def status(value: Status) -> None:
        entry.runtime_data.coordinator.device.values["status"] = value.value

    status(Status.CLEANING)
    assert await frame_with((7, 0)) == [7, 0, 1]  # the robot finds a box
    pose = entry.runtime_data.map.pose
    assert pose["obstacles"] == [{"x": -925, "y": -425, "type": "shoes", "confidence": 99, "source": "camera"}]
    assert pose["furniture"][0]["type"] == "two_seat_sofa"
    status(Status.IDLE)
    assert await frame_with((7, 0)) == [7, 0, 1]  # still new after the cleaning

    # After a restart the reference comes back from storage, so the box is still new…
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=10))
    await hass.async_block_till_done()
    await setup_reload(hass, entry)
    assert await frame_with((7, 0)) == [7, 0, 1]

    # …until the next cleaning starts: then the box is what was there last time.
    status(Status.CLEANING)
    assert await frame_with((7, 0)) == []
    assert await frame_with((7, 0), (5, 0)) == [5, 0, 1]

    # Another saved map starts a new reference.
    assert await frame_with((7, 0), (5, 0), map_id=46) == []


async def test_what_is_new_carries_over_a_pause(hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("custom_components.dreame_vacuum_3d.live_map.WHOLE_EVERY", 0)  # a whole frame each time
    entry, _miio = await setup(hass)  # its first frame, with nothing on the floor, is the reference
    device = entry.runtime_data.coordinator.device
    device.request_map = AsyncMock(return_value=(_with((7, 0)), None))  # a box on the floor

    for status, task in (
        (Status.CLEANING, TaskStatus.AUTO_CLEANING),
        (Status.PAUSED, TaskStatus.AUTO_CLEANING_PAUSED),
        (Status.CLEANING, TaskStatus.AUTO_CLEANING),  # resumed: still the same cleaning
    ):
        device.values["status"], device.values["task_status"] = status.value, task.value
        await entry.runtime_data.map.refresh()
        assert entry.runtime_data.map.pose["new_cells"] == [7, 0, 1]


def _logged(*frames: str, start: int = 100) -> list[dict]:
    """Cloud log entries for these frames, newest first as the cloud lists them."""
    return [{"time": start + i, "value": json.dumps([f])} for i, f in enumerate(frames)][::-1]


async def test_partial_frames_from_the_cloud_log_while_cleaning(hass: HomeAssistant) -> None:
    entry, miio = await setup(hass)  # a whole frame of map 7, frame 1, robot in room 1
    live_map = entry.runtime_data.map
    entry.runtime_data.coordinator.device.values["status"] = Status.CLEANING.value
    sent: list[dict] = []
    live_map.listen(sent.append)
    p2 = frame([[0]], {"tr": "l-850,-400L50,0", "sa": [[2, 1]]}, kind="P", frame_id=2, robot=(-800, -400, 0))
    p3 = frame([[0]], {"tr": "l-800,-400L0,50", "ai_obstacle": [[-700, -400, 134, 0.9]]}, kind="P", frame_id=3,
               robot=(-700, -400, 45))  # fmt: skip
    other_map = frame([[0]], {}, kind="P", map_id=8, frame_id=4)
    now = int(time.time())  # cloud time, about the same as ours
    miio.cloud.device_data.side_effect = lambda _did, key, _since: (
        [*_logged(p2, p3, other_map, "not json", start=now), {"time": now - 1, "value": '[""]'}] if key == "6.1" else []
    )
    await live_map.refresh()

    pose = live_map.pose
    assert pose["frame_id"] == 3 and pose["robot"] == [-700, -400, 45] and pose["room"] == 2
    assert pose["active"] == [2]
    assert pose["obstacles"] == [{"x": -700, "y": -400, "type": "sock", "confidence": 90, "source": "camera"}]
    # The live frame's path was M-900,-450 L50,0 L0,50 then S…, M…; each P continues the last stroke.
    assert pose["path"][-1]["points"][-3:] == [[-800, -400], [-800, -400], [-800, -350]]
    assert len(sent) == 2 and "cells" not in sent[0]
    # Listeners get what changed and the path's new strokes, not the whole path again.
    assert "path" not in sent[0] and "furniture" not in sent[0] and "obstacles" not in sent[0]
    assert sent[0]["path_tail"] == [{"mode": "vacuum", "points": [[-850, -400], [-800, -400]], "joins": True}]
    assert sent[1]["obstacles"] == pose["obstacles"] and sent[1]["robot"] == [-700, -400, 45]
    frames = live_map.last_frames  # for diagnostics: no positions
    assert frames["P"]["frame_id"] == 3 and "ai_obstacle" in frames["P"]["keys"] and "robot" not in frames["P"]
    # The log is read from where it was left; frames already applied are skipped.
    miio.cloud.device_data.side_effect = lambda _did, key, _since: _logged(p2, p3, start=now) if key == "6.1" else []
    await live_map.refresh()
    assert miio.cloud.device_data.await_args.args[2] == now + 4
    assert len(sent) == 2


async def test_partial_frames_show_what_the_lidar_sees_while_cleaning(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    entry, miio = await setup(hass)  # a whole frame of map 7, frame 1: open floor, nothing on it
    live_map = entry.runtime_data.map
    entry.runtime_data.coordinator.device.values["status"] = Status.CLEANING.value
    sent: list[dict] = []
    live_map.listen(sent.append)
    rows = [[0] * 8 for _ in SAVED_ROWS]
    rows[0][5] = 255  # floor (3) + 255 = wall (2): a box on open floor
    box = frame(rows, {}, kind="P", frame_id=2)
    now = int(time.time())
    miio.cloud.device_data.side_effect = lambda _did, key, _since, **_kw: (
        _logged(box, start=now) if key == "6.1" else []
    )
    await live_map.refresh()
    assert live_map.pose["new_cells"] == [5, 0, 1] and sent[-1]["new_cells"] == [5, 0, 1]

    # The whole frame that follows matches what the partial frames built (diagnostics say so).
    whole_rows = [[3 if v else 0 for v in row] for row in SAVED_ROWS]
    whole_rows[0][5] = 2
    entry.runtime_data.coordinator.device.request_map = AsyncMock(
        return_value=(frame(whole_rows, {"rism": saved()}, frame_id=3), None)
    )
    miio.cloud.device_data.side_effect = lambda *_args, **_kw: []
    monkeypatch.setattr("custom_components.dreame_vacuum_3d.live_map.WHOLE_EVERY", 0)  # ask for it now
    await live_map.refresh()
    monkeypatch.setattr("custom_components.dreame_vacuum_3d.live_map.WHOLE_EVERY", 60)
    assert live_map.last_frames["merge"] == {"partials": 1, "cells": 32, "different": 0}

    # A partial frame missing (5 after 3): the ones after it can't be added, until the next whole frame.
    rows[0][5], rows[1][6] = 0, 255
    late = frame(rows, {}, kind="P", frame_id=5)
    miio.cloud.device_data.side_effect = lambda _did, key, _since, **_kw: (
        _logged(late, start=now) if key == "6.1" else []
    )
    await live_map.refresh()
    assert live_map.pose["frame_id"] == 5 and live_map.pose["new_cells"] == [5, 0, 1]


async def test_a_whole_frame_from_the_cloud_log(hass: HomeAssistant) -> None:
    entry, miio = await setup(hass)
    live_map = entry.runtime_data.map
    entry.runtime_data.coordinator.device.values["status"] = Status.CLEANING.value
    miio.cloud.device_data.side_effect = lambda _did, key, _since: (
        [{"time": 5, "value": json.dumps(["1000001/123/1"])}] if key == "6.3" else []
    )
    miio.cloud.download.return_value = live(robot=(-700, -400, 0), frame_id=9).encode()
    await live_map.refresh()
    assert live_map.pose["frame_id"] == 9 and live_map.pose["room"] == 2
    miio.cloud.interim_file_url.assert_awaited_with("1000001/123/1", model="dreame.vacuum.p2114a")


@pytest.mark.parametrize("second_try_ok", [True, False])
async def test_partial_frames_survive_a_whole_frame_that_fails(hass: HomeAssistant, second_try_ok: bool) -> None:
    entry, miio = await setup(hass)  # a whole frame of map 7, frame 1
    live_map = entry.runtime_data.map
    entry.runtime_data.coordinator.device.values["status"] = Status.CLEANING.value
    sent: list[dict] = []
    live_map.listen(sent.append)
    now = int(time.time())
    p2 = frame([[0]], {}, kind="P", frame_id=2, robot=(-800, -400, 0))
    log = {"6.1": _logged(p2, start=now), "6.3": [{"time": now + 1, "value": json.dumps(["1000001/123/1"])}]}
    miio.cloud.device_data.side_effect = lambda _did, key, since: [r for r in log[key] if r["time"] >= since]
    miio.cloud.download.return_value = None  # the whole frame's file isn't there

    await live_map.refresh()
    assert sent == []

    if second_try_ok:
        miio.cloud.download.return_value = live(robot=(-700, -400, 0), frame_id=9).encode()
    await live_map.refresh()
    assert sent[0]["frame_id"] == 2 and sent[0]["robot"] == [-800, -400, 0]  # the partial frame read with it
    assert [m["frame_id"] for m in sent] == ([2, 9] if second_try_ok else [2])
    await live_map.refresh()
    assert miio.cloud.device_data.await_args.args[2] == now + 2  # the log is read on from after both


async def test_a_cloud_log_error_falls_back_to_asking_the_robot(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    entry, miio = await setup(hass)
    live_map = entry.runtime_data.map
    entry.runtime_data.coordinator.device.values["status"] = Status.CLEANING.value
    miio.cloud.device_data.side_effect = CloudConnectionError("down")
    await live_map.refresh()  # no whole frame due yet: nothing, and a warning
    assert "Couldn't fetch the map: down" in caplog.text
    monkeypatch.setattr("custom_components.dreame_vacuum_3d.live_map.WHOLE_EVERY", 0)
    miio.cloud.download.return_value = live(robot=(-700, -400, 0), frame_id=5).encode()
    await live_map.refresh()
    assert live_map.pose["frame_id"] == 5


async def test_live_map_without_saved_rooms_and_unknown_robot(hass: HomeAssistant) -> None:
    entry, _miio = await setup(hass)
    live_map = entry.runtime_data.map
    entry.runtime_data.coordinator.device.request_map = AsyncMock(
        return_value=(frame([[2, 3]], {"oc": 1}, robot=(32767, 32767, 0)), None)
    )
    await live_map.refresh()
    message = live_map.message
    assert message["rooms"] == {} and message["robot"] == message["dock"]


async def test_map_file_logs_in_again(hass: HomeAssistant) -> None:
    entry, miio = await setup(hass)
    cloud = miio.cloud
    cloud.interim_file_url.side_effect = [CloudAuthError("expired"), "https://example.com/map"]
    await entry.runtime_data.map.refresh()
    cloud.login.assert_awaited()
    assert entry.runtime_data.map.message["map_id"] == 45


async def test_encrypted_frames_and_a_late_upload(hass: HomeAssistant) -> None:
    """The X10+ names the file "<user>/<did>/<n>,<key>" and encrypts it; the file shows up a moment later."""
    iv = load("dreame.vacuum.p2114a").map_iv
    sealed = encrypt(live(robot=IN_ROOM_1), "k3y", iv)
    entry, miio = await setup_fixture_entry(hass, map_file=sealed, map_key="k3y")
    assert entry.runtime_data.map.message["room"] == 1
    miio.cloud.interim_file_url.assert_awaited_with("1000001/123/0", model="dreame.vacuum.p2114a")

    miio.cloud.download.side_effect = [None, None, sealed.encode()]
    await entry.runtime_data.map.refresh()
    assert miio.cloud.download.await_count == 1 + 3
    miio.cloud.download.side_effect = None
    miio.cloud.download.return_value = None
    miio.cloud.interim_file_url.return_value = None
    await entry.runtime_data.map.refresh()
    assert entry.runtime_data.map.message["room"] == 1


async def setup(hass: HomeAssistant):
    return await setup_fixture_entry(hass, map_file=live(robot=IN_ROOM_1))


async def setup_reload(hass: HomeAssistant, entry) -> None:
    miio = entry.runtime_data.miio
    with (
        patch("custom_components.dreame_vacuum_3d.XiaomiCloud", return_value=miio.cloud),
        patch("custom_components.dreame_vacuum_3d.MiioClient", return_value=miio),
    ):
        assert await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()
