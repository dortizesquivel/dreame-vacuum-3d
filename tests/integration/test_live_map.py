"""The live map: from the robot to the room entities and the card's WebSocket subscription."""

from __future__ import annotations

from datetime import timedelta
import logging
from unittest.mock import AsyncMock, patch

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
import pytest
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.dreame_vacuum_3d.const import DOMAIN
from custom_components.dreame_vacuum_3d.device.capabilities import load
from custom_components.dreame_vacuum_3d.device.enums import Status
from custom_components.dreame_vacuum_3d.live_map import IDLE_EVERY
from custom_components.dreame_vacuum_3d.protocol.cloud import CloudAuthError
from custom_components.dreame_vacuum_3d.protocol.miio import MiioTimeoutError
from tests.integration.helpers import setup_fixture_entry
from tests.integration.test_init import ENTRY_DATA
from tests.map_frames import encrypt, frame, live

DID = ENTRY_DATA["did"]
IN_ROOM_1 = (-925, -425, 90)  # cell (1, 1)


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
    entry, miio = await setup(hass)
    device = entry.runtime_data.coordinator.device
    start = _map_requests(miio)

    def tick() -> None:
        async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=5), fire_all=True)

    tick()
    await hass.async_block_till_done(wait_background_tasks=True)
    assert _map_requests(miio) == start  # docked: nothing new

    device.values["status"] = Status.CLEANING.value
    tick()
    await hass.async_block_till_done(wait_background_tasks=True)
    assert _map_requests(miio) == start + 1

    device.values["status"] = Status.IDLE.value
    tick()
    await hass.async_block_till_done(wait_background_tasks=True)
    assert _map_requests(miio) == start + 2  # the frame with the whole path

    monkeypatch.setattr("custom_components.dreame_vacuum_3d.live_map.monotonic", lambda: 10.0**9)
    tick()
    await hass.async_block_till_done(wait_background_tasks=True)
    assert _map_requests(miio) == start + 3  # IDLE_EVERY later
    assert IDLE_EVERY >= 60


async def test_errors_keep_the_last_map_and_warn_once(hass: HomeAssistant, caplog: pytest.LogCaptureFixture) -> None:
    entry, miio = await setup(hass)
    live_map = entry.runtime_data.map
    before = live_map.message
    device = entry.runtime_data.coordinator.device
    device.request_map = AsyncMock(side_effect=MiioTimeoutError("no answer"))
    with caplog.at_level(logging.DEBUG):
        await live_map.refresh()
        await live_map.refresh()
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING and "map" in r.getMessage()]
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
