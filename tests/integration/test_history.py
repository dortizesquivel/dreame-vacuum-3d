"""The cleaning history: from the cloud's events to the last-cleaning sensors and the card's WebSocket commands."""

from __future__ import annotations

from datetime import timedelta
import json
import logging
import time
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
import pytest
from pytest_homeassistant_custom_component.common import async_fire_time_changed, flush_store

from custom_components.dreame_vacuum_3d.const import DOMAIN
from custom_components.dreame_vacuum_3d.device.enums import Status
from custom_components.dreame_vacuum_3d.history import AFTER_TASK, FIRST_AFTER
from custom_components.dreame_vacuum_3d.protocol.cloud import CloudConnectionError
from tests.integration.helpers import setup_fixture_entry
from tests.integration.test_init import ENTRY_DATA
from tests.map_frames import live

DID = ENTRY_DATA["did"]
NOW = int(time.time())
RECENT, OLD = NOW - 3600, NOW - 10 * 86400  # an hour ago, and ten days ago


def _event(start: int, file: str) -> dict[str, Any]:
    items = [{"piid": 2, "value": 12}, {"piid": 3, "value": 20}, {"piid": 8, "value": start}, {"piid": 13, "value": 1}]
    return {"time": start, "value": json.dumps([*items, {"piid": 9, "value": file}])}


EVENTS = [_event(RECENT, "1000001/123/h1"), _event(OLD, "")]


def _entity(hass: HomeAssistant, key: str) -> str:
    entity_id = er.async_get(hass).async_get_entity_id("sensor", DOMAIN, f"{DID}_{key}")
    assert entity_id, key
    return entity_id


async def setup(hass: HomeAssistant, events: list[dict[str, Any]] = EVENTS):
    # The live map (saved map 45, rooms 1 "Cocina" and 2) is also what a past cleaning's log file holds here.
    entry, miio = await setup_fixture_entry(hass, map_file=live(robot=(-925, -425, 90)))
    miio.cloud.device_data.side_effect = lambda _did, _key, _since, limit=20, kind="prop": (
        events if kind == "event" else []
    )
    return entry, miio


async def test_sensors_and_the_cards_commands(hass: HomeAssistant, hass_ws_client) -> None:
    entry, miio = await setup(hass)
    await entry.runtime_data.history.refresh()
    await hass.async_block_till_done()
    key = miio.cloud.device_data.await_args
    assert key.args[1] == "4.1" and key.kwargs["kind"] == "event"

    last = hass.states.get(_entity(hass, "last_cleaning"))
    assert dt_util.parse_datetime(last.state).timestamp() == RECENT
    assert last.attributes["duration"] == 12 and last.attributes["area"] == 20
    assert last.attributes["completed"] is True and last.attributes["rooms"] == ["#2"]
    room2 = hass.states.get(_entity(hass, "last_cleaned_45_2"))
    assert dt_util.parse_datetime(room2.state).timestamp() == RECENT
    assert room2.attributes["cleanings_last_7_days"] == 1
    assert hass.states.get(_entity(hass, "last_cleaned_45_1")).state == "unknown"

    client = await hass_ws_client(hass)
    await client.send_json_auto_id({"type": f"{DOMAIN}/history", "entity_id": "vacuum.x10"})
    cleanings = (await client.receive_json())["result"]["cleanings"]
    assert cleanings[0] == {
        "start": RECENT, "duration": 12, "area": 20, "completed": True, "rooms": [2], "map_id": 45, "has_map": True,
    }  # fmt: skip
    assert cleanings[1]["has_map"] is False and "file" not in cleanings[0]

    await client.send_json_auto_id({"type": f"{DOMAIN}/history/path", "entity_id": "vacuum.x10", "start": RECENT})
    found = (await client.receive_json())["result"]
    assert found["map_id"] == 45 and found["rooms"] == [2] and found["path"][0]["mode"] == "both"
    for start in (OLD, 1):  # no map, no such cleaning
        await client.send_json_auto_id({"type": f"{DOMAIN}/history/path", "entity_id": "vacuum.x10", "start": start})
        assert (await client.receive_json())["error"]["code"] == "not_found"


async def test_each_map_is_downloaded_once_and_its_rooms_kept(hass: HomeAssistant, hass_storage) -> None:
    entry, miio = await setup(hass)
    history = entry.runtime_data.history
    await history.refresh()
    await history.refresh()
    assert miio.cloud.file_url.await_count == 1
    assert miio.cloud.file_url.await_args.args[0] == "1000001/123/h1"
    await flush_store(history._store)
    stored = hass_storage[f"{DOMAIN}.{entry.entry_id}.history"]["data"]
    assert stored == {"cleanings": {"1000001/123/h1": {"map_id": 45, "rooms": [2]}}}


async def test_errors_warn_once_and_keep_going(
    hass: HomeAssistant, hass_ws_client, caplog: pytest.LogCaptureFixture
) -> None:
    entry, miio = await setup(hass)
    history = entry.runtime_data.history
    miio.cloud.device_data.side_effect = CloudConnectionError("down")
    with caplog.at_level(logging.DEBUG):
        await history.refresh()
        await history.refresh()
    assert len([r for r in caplog.records if r.levelno == logging.WARNING and "history" in r.getMessage()]) == 1

    # A cleaning whose log file the cloud doesn't have: no rooms, and no path to replay.
    miio.cloud.device_data.side_effect = lambda *_args, **_kwargs: EVENTS
    miio.cloud.file_url.return_value = None
    await history.refresh()
    assert history.cleanings[0]["rooms"] == [] and history.cleanings[0]["map_id"] is None
    client = await hass_ws_client(hass)
    await client.send_json_auto_id({"type": f"{DOMAIN}/history/path", "entity_id": "vacuum.x10", "start": RECENT})
    assert (await client.receive_json())["error"]["code"] == "home_assistant_error"


async def test_it_looks_after_setup_and_after_each_cleaning(hass: HomeAssistant) -> None:
    entry, miio = await setup(hass)
    events = lambda: sum(1 for c in miio.cloud.device_data.await_args_list if c.kwargs.get("kind") == "event")  # noqa: E731
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=FIRST_AFTER + 1))
    await hass.async_block_till_done(wait_background_tasks=True)
    assert events() == 1

    coordinator, device = entry.runtime_data.coordinator, entry.runtime_data.coordinator.device
    for status in (Status.CLEANING, Status.CHARGING):  # a cleaning, then back on the dock
        device.values["status"] = status.value
        coordinator.async_update_listeners()
    for after in AFTER_TASK:  # a minute after, and again later in case the cloud was slow
        async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=FIRST_AFTER + after + 2))
        await hass.async_block_till_done(wait_background_tasks=True)
    assert events() == 1 + len(AFTER_TASK)
