"""Editing the robot's map from Home Assistant: rooms' names, settings and order, saved maps, obstacles, the picture."""

from __future__ import annotations

import base64
import json
from typing import Any
from unittest.mock import AsyncMock

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import entity_registry as er
import pytest

from custom_components.dreame_vacuum_3d.const import DOMAIN
from custom_components.dreame_vacuum_3d.device.enums import Status
from custom_components.dreame_vacuum_3d.maps import _object_name
from tests.integration.helpers import setup_fixture_entry
from tests.integration.test_init import ENTRY_DATA
from tests.map_frames import live, saved

DID = ENTRY_DATA["did"]
IN_ROOM_1 = (-925, -425, 90)
# Room 1: turbo, high water, twice, first; room 2: quiet, low water, once, second (water kept one higher).
CLEANSET = {"1": [3, 4, 2, 1], "2": [0, 2, 1, 2]}
OBSTACLE = [-700.0, -400.0, 134, 0.9]


def _entity(hass: HomeAssistant, platform: str, key: str) -> str:
    entity_id = er.async_get(hass).async_get_entity_id(platform, DOMAIN, f"{DID}_{key}")
    assert entity_id, key
    return entity_id


def _map_edits(miio) -> list[Any]:
    """What update_map_data (6.2) sent, decoded."""
    return [
        json.loads(params["in"][0]["value"])
        for method, params in miio.calls
        if method == "action" and (params["siid"], params["aiid"]) == (6, 2)
    ]


def _actions(miio, siid: int, aiid: int) -> list[Any]:
    return [p["in"] for m, p in miio.calls if m == "action" and (p["siid"], p["aiid"]) == (siid, aiid)]


async def setup(hass: HomeAssistant, data: dict[str, Any] | None = None, **kwargs: Any):
    data = {"cleanset": json.dumps(CLEANSET), "ai_obstacle": [OBSTACLE], **(data or {})}
    return await setup_fixture_entry(hass, map_file=live(data, robot=IN_ROOM_1), **kwargs)


async def call(hass: HomeAssistant, service: str, **data: Any) -> None:
    await hass.services.async_call(DOMAIN, service, {"entity_id": "vacuum.x10", **data}, blocking=True)


async def test_each_rooms_own_settings(hass: HomeAssistant) -> None:
    entry, miio = await setup(hass)
    suction = hass.states.get(_entity(hass, "select", "room_45_1_suction"))
    assert suction.state == "turbo" and suction.name.endswith("Cocina suction")
    assert hass.states.get(_entity(hass, "select", "room_45_1_water")).state == "high"
    assert hass.states.get(_entity(hass, "select", "room_45_1_repeats")).state == "2"
    assert hass.states.get(_entity(hass, "select", "room_45_2_suction")).state == "quiet"
    assert entry.runtime_data.map.rooms["2"]["order"] == 2

    # A change sends every room's settings, the others as they are; it shows at once.
    await hass.services.async_call(
        "select",
        "select_option",
        {"entity_id": _entity(hass, "select", "room_45_2_water"), "option": "medium"},
        blocking=True,
    )
    assert _map_edits(miio)[-1] == {"customeClean": [[1, 3, 4, 2], [2, 0, 3, 1]]}
    await hass.async_block_till_done()
    assert hass.states.get(_entity(hass, "select", "room_45_2_water")).state == "medium"


async def test_settings_from_a_partial_frame_reach_the_card(hass: HomeAssistant) -> None:
    entry, _miio = await setup(hass)
    live_map = entry.runtime_data.map
    messages: list[dict[str, Any]] = []
    live_map.listen(messages.append)
    from custom_components.dreame_vacuum_3d.device import map as dmap

    changed = {"1": [1, 3, 1, 1], "2": [0, 2, 1, 2]}
    partial = dmap.decode(live({"cleanset": changed}, kind="P", frame_id=2, robot=IN_ROOM_1))
    live_map._update(partial)
    assert messages[-1]["rooms"]["1"]["suction"] == 1
    assert "rooms" not in live_map.pose
    # The same settings again: nothing new for the card.
    live_map._update(dmap.decode(live({"cleanset": changed}, kind="P", frame_id=3, robot=IN_ROOM_1)))
    assert "rooms" not in messages[-1]


async def test_rename_a_room(hass: HomeAssistant) -> None:
    entry, miio = await setup(hass)
    name = _entity(hass, "text", "room_45_2_name")
    assert hass.states.get(name).state == "unknown"
    await hass.services.async_call("text", "set_value", {"entity_id": name, "value": " Salón "}, blocking=True)
    encoded = base64.b64encode("Salón".encode()).decode()
    assert _map_edits(miio)[-1] == {"nsr": {"2": {"name": encoded, "type": 0, "index": 0}}, "mapid": 45}
    await hass.async_block_till_done()
    assert hass.states.get(name).state == "Salón"
    assert entry.runtime_data.map.rooms["2"]["name"] == "Salón"
    # The room's other entities take its new name.
    assert hass.states.get(_entity(hass, "button", "clean_room_45_2")).name.endswith("Clean Salón")

    await call(hass, "vacuum_rename_segment", segment_id=1, segment_name="Cocina nueva")
    assert _map_edits(miio)[-1]["nsr"]["1"]["name"] == base64.b64encode(b"Cocina nueva").decode()
    with pytest.raises(ServiceValidationError, match="isn't on the map"):
        await call(hass, "vacuum_rename_segment", segment_id=9, segment_name="Nada")


async def test_cleaning_order_and_rooms_settings_by_service(hass: HomeAssistant) -> None:
    _entry, miio = await setup(hass)
    await call(hass, "vacuum_set_cleaning_sequence", cleaning_sequence=[2, 1])
    assert _map_edits(miio)[-1] == {"cleanOrder": [2, 1]}
    await call(hass, "vacuum_set_cleaning_sequence", cleaning_sequence=[])
    assert _map_edits(miio)[-1] == {"cleanOrder": []}
    with pytest.raises(ServiceValidationError, match="twice"):
        await call(hass, "vacuum_set_cleaning_sequence", cleaning_sequence=[1, 1])
    with pytest.raises(ServiceValidationError, match="isn't on the map"):
        await call(hass, "vacuum_set_cleaning_sequence", cleaning_sequence=[5])

    await call(hass, "vacuum_set_custom_cleaning", segment_id=[2], suction_level=[2], water_volume=[3], repeats=[3])
    assert _map_edits(miio)[-1] == {"customeClean": [[1, 3, 4, 2], [2, 2, 4, 3]]}
    with pytest.raises(ServiceValidationError, match="as many values"):
        await call(
            hass, "vacuum_set_custom_cleaning", segment_id=[1, 2], suction_level=[2], water_volume=[3], repeats=[1]
        )
    with pytest.raises(ServiceValidationError, match="water_volume"):
        await call(hass, "vacuum_set_custom_cleaning", segment_id=[1], suction_level=[2], water_volume=[0], repeats=[1])


async def test_map_edits_wait_for_the_robot_to_finish(hass: HomeAssistant) -> None:
    _entry, miio = await setup(hass, overrides={(4, 1): Status.CLEANING.value})
    for service, data in [
        ("vacuum_set_cleaning_sequence", {"cleaning_sequence": [1]}),
        ("vacuum_start_fast_mapping", {}),
        ("vacuum_delete_map", {"map_id": 45}),
    ]:
        with pytest.raises(ServiceValidationError, match="busy"):
            await call(hass, service, **data)
    assert _map_edits(miio) == []


async def test_without_a_map_there_is_nothing_to_edit(hass: HomeAssistant) -> None:
    await setup_fixture_entry(hass)
    with pytest.raises(ServiceValidationError, match="no map"):
        await call(hass, "vacuum_set_custom_cleaning", segment_id=[1], suction_level=[1], water_volume=[1], repeats=[1])
    with pytest.raises(ServiceValidationError, match="no map"):
        await call(hass, "vacuum_backup_map")


async def test_mapping_and_fetching_the_map(hass: HomeAssistant) -> None:
    entry, miio = await setup(hass)
    await call(hass, "vacuum_start_fast_mapping")
    assert _actions(miio, 4, 1)[-1] == [{"piid": 1, "value": Status.FAST_MAPPING.value}]
    await call(hass, "vacuum_start_mapping")
    assert _actions(miio, 4, 1)[-1] == [{"piid": 1, "value": Status.CLEANING.value}, {"piid": 10, "value": "3"}]

    requests = len(_actions(miio, 6, 1))
    await call(hass, "vacuum_request_map")
    assert len(_actions(miio, 6, 1)) == requests + 1
    await hass.services.async_call(
        "button", "press", {"entity_id": _entity(hass, "button", "request_map")}, blocking=True
    )
    assert len(_actions(miio, 6, 1)) == requests + 2
    # One already on its way: no second one.
    entry.runtime_data.map._fetching = True
    await entry.runtime_data.map.fetch_now()
    assert len(_actions(miio, 6, 1)) == requests + 2


async def _with_saved_maps(hass: HomeAssistant, entry) -> None:
    maps = entry.runtime_data.maps
    files = {
        "list": json.dumps(
            {"mapstr": [{"map": saved(), "name": "Planta baja"}, {"map": "junk"}, "odd"], "curr_id": 45}
        ).encode(),
        "recovery": json.dumps(
            [{"id": 45, "info": [{"time": 100, "objname": "old"}, {"time": 200, "objname": "new"}, {"time": 1}]}, "x"]
        ).encode(),
    }
    maps._fetch_interim = AsyncMock(side_effect=lambda name: files["list"])
    maps._fetch_file = AsyncMock(side_effect=lambda name: files["recovery"])
    maps._file_url = AsyncMock(side_effect=lambda name: f"https://example.com/{name}")
    device = entry.runtime_data.coordinator.device
    device.values["map_list"] = '{"object_name":"u/d/9","md5":"x"}'
    device.values["recovery_map_list"] = '{"object_name":"u/d/8"}'
    await maps.refresh()
    await hass.async_block_till_done()


async def test_saved_maps(hass: HomeAssistant) -> None:
    entry, miio = await setup(hass)
    select = _entity(hass, "select", "selected_map")
    assert hass.states.get(select).state == "unavailable"  # the fixture's robot names no map list
    await _with_saved_maps(hass, entry)
    state = hass.states.get(select)
    assert state.state == "Planta baja" and state.attributes["options"] == ["Planta baja"]
    assert state.attributes["backups"] == {"45": 2}
    # Read again only when the robot names another file.
    maps = entry.runtime_data.maps
    await maps.refresh()
    assert maps._fetch_interim.await_count == 1

    await hass.services.async_call(
        "select", "select_option", {"entity_id": select, "option": "Planta baja"}, blocking=True
    )
    assert _map_edits(miio)[-1] == {"sm": {}, "mapid": 45}

    await call(hass, "vacuum_rename_map", map_id=45, map_name="Abajo")
    assert _map_edits(miio)[-1] == {"nrism": {"45": {"name": "Abajo"}}}
    await call(hass, "vacuum_rename_map", map_id=45, map_name="")
    assert _map_edits(miio)[-1] == {"nrism": {"45": {"name": None}}}
    await call(hass, "vacuum_delete_map")
    assert _map_edits(miio)[-1] == {"cm": {}, "mapid": 45}
    await call(hass, "vacuum_backup_map", map_id=45)
    assert _actions(miio, 6, 3)[-1] == [{"piid": 4, "value": "45"}]

    # Copy 1 is the newest.
    await call(hass, "vacuum_restore_map", recovery_map_index=1)
    restore = json.loads(miio.overrides[(6, 10)])
    assert restore == {"map_id": 45, "map_url": "https://example.com/new"}
    with pytest.raises(ServiceValidationError, match="no copy 3"):
        await call(hass, "vacuum_restore_map", recovery_map_index=3)
    maps._file_url = AsyncMock(return_value=None)
    with pytest.raises(ServiceValidationError, match="no copy 2"):
        await call(hass, "vacuum_restore_map", recovery_map_index=2)


async def test_saved_maps_that_cant_be_read(hass: HomeAssistant) -> None:
    entry, _miio = await setup(hass)
    maps = entry.runtime_data.maps
    device = entry.runtime_data.coordinator.device
    device.values["map_list"] = '{"object_name":"u/d/9"}'
    device.values["recovery_map_list"] = "u/d/8"
    maps._fetch_interim = AsyncMock(return_value=None)
    maps._fetch_file = AsyncMock(return_value=b"not json")
    await maps.refresh()
    assert maps.maps == {} and maps._names == (None, None)  # tried again next time
    maps._fetch_file = AsyncMock(return_value=None)
    await maps.refresh()
    assert maps._names == ("u/d/9", "u/d/8")
    assert maps.name(3) == "#3"


def test_the_files_the_robot_names() -> None:
    assert _object_name('{"object_name":"a/b/9","md5":"x"}') == "a/b/9"
    assert _object_name('{"obj_name":"a/b/8"}') == "a/b/8"
    assert _object_name("a/b/7") == "a/b/7"
    assert _object_name('{"md5":"x"}') is None
    assert _object_name("[1]") is None
    assert _object_name("") is None
    assert _object_name(None) is None


async def test_ignore_an_obstacle(hass: HomeAssistant) -> None:
    entry, miio = await setup(hass)
    assert entry.runtime_data.map.pose["obstacles"][0]["ignored"] is False
    await call(hass, "vacuum_set_obstacle_ignore", x=-700, y=-400, obstacle_ignored=True)
    assert _map_edits(miio)[-1] == {"obstacleignore": [-700, -400, 134, 1]}
    assert entry.runtime_data.map.pose["obstacles"][0]["ignored"] is True
    with pytest.raises(ServiceValidationError, match="no obstacle"):
        await call(hass, "vacuum_set_obstacle_ignore", x=1, y=2, obstacle_ignored=False)


async def test_the_map_as_a_picture(hass: HomeAssistant, hass_client) -> None:
    entry, _miio = await setup(hass)
    image = _entity(hass, "image", "map")
    state = hass.states.get(image)
    assert state.attributes["map_id"] == 45
    assert state.attributes["rooms"]["1"]["name"] == "Cocina"
    assert len(state.attributes["calibration_points"]) == 3
    client = await hass_client()
    reply = await client.get(f"/api/image_proxy/{image}")
    assert reply.status == 200
    body = await reply.read()
    assert body.startswith(b"\x89PNG\r\n\x1a\n")
    # Drawn again only when what it shows changes.
    before = hass.states.get(image).state
    await entry.runtime_data.map.refresh()
    await hass.async_block_till_done()
    assert hass.states.get(image).state == before


async def test_a_robot_that_cant_edit_its_map(hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch) -> None:
    from custom_components.dreame_vacuum_3d.device import capabilities

    real = capabilities.load

    def without(model: str, *args: Any) -> Any:
        caps = real(model, *args)
        actions = {k: v for k, v in caps.actions.items() if k not in ("update_map_data", "backup_map")}
        properties = {k: v for k, v in caps.properties.items() if k != "map_recovery"}
        return type(caps)(caps.model, caps.name, caps.tested, properties, actions, caps.map_iv)

    monkeypatch.setattr("custom_components.dreame_vacuum_3d.load", without)
    await setup(hass)
    assert er.async_get(hass).async_get_entity_id("text", DOMAIN, f"{DID}_room_45_1_name") is None
    for service, data in [
        ("vacuum_rename_segment", {"segment_id": 1, "segment_name": "x"}),
        ("vacuum_backup_map", {}),
        ("vacuum_restore_map", {"recovery_map_index": 1}),
    ]:
        with pytest.raises(ServiceValidationError, match="can't do"):
            await call(hass, service, **data)
