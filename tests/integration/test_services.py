"""Tasshack-compatible vacuum entity services (Node-RED migration)."""

from __future__ import annotations

import json
from typing import Any

from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import entity_registry as er
import pytest
from voluptuous.error import MultipleInvalid

from custom_components.dreame_vacuum_3d.const import DOMAIN
from custom_components.dreame_vacuum_3d.device.enums import Status
from tests.integration.helpers import setup_fixture_entry


def _vacuum_entity_id(hass: HomeAssistant) -> str:
    for entry in er.async_get(hass).entities.values():
        if entry.domain == "vacuum" and entry.platform == DOMAIN:
            return entry.entity_id
    raise AssertionError("vacuum entity not registered")


def _action_payload(miio: Any) -> dict[str, Any]:
    for method, params in miio.calls:
        if method == "action":
            return params
    raise AssertionError(f"no action call in {miio.calls!r}")


async def test_vacuum_clean_segment_node_red_payload(hass: HomeAssistant) -> None:
    """Node-RED Barra job: segments: 6 → Tasshack-shaped start_custom payload."""
    _entry, miio = await setup_fixture_entry(hass)
    entity_id = _vacuum_entity_id(hass)
    miio.calls.clear()

    await hass.services.async_call(
        DOMAIN,
        "vacuum_clean_segment",
        {ATTR_ENTITY_ID: entity_id, "segments": 6},
        blocking=True,
    )

    action = _action_payload(miio)
    assert action["siid"] == 4 and action["aiid"] == 1
    status_in, props_in = action["in"]
    assert status_in == {"piid": 1, "value": Status.SEGMENT_CLEANING.value}
    payload = json.loads(props_in["value"])
    # The robot's own suction (strong, 2) and water (high, 3), as the app does.
    assert payload == {"selects": [[6, 1, 2, 3, 1]]}


async def test_vacuum_clean_zone_and_spot_payloads(hass: HomeAssistant) -> None:
    _entry, miio = await setup_fixture_entry(hass)
    entity_id = _vacuum_entity_id(hass)

    miio.calls.clear()
    await hass.services.async_call(
        DOMAIN,
        "vacuum_clean_zone",
        {ATTR_ENTITY_ID: entity_id, "zone": [819, -263, 4424, 2105], "repeats": 2},
        blocking=True,
    )
    props = _action_payload(miio)["in"][1]["value"]
    assert json.loads(props) == {"areas": [[819, -263, 4424, 2105, 2, 2, 3]]}

    miio.calls.clear()
    await hass.services.async_call(
        DOMAIN,
        "vacuum_clean_spot",
        {ATTR_ENTITY_ID: entity_id, "points": [819, -263], "suction_level": 0, "water_volume": 1},
        blocking=True,
    )
    props = _action_payload(miio)["in"][1]["value"]
    assert json.loads(props) == {"points": [[819, -263, 1, 0, 1]]}


async def test_vacuum_goto_select_map_remote_reset(hass: HomeAssistant) -> None:
    _entry, miio = await setup_fixture_entry(hass)
    entity_id = _vacuum_entity_id(hass)

    miio.calls.clear()
    await hass.services.async_call(
        DOMAIN,
        "vacuum_goto",
        {ATTR_ENTITY_ID: entity_id, "x": 1000, "y": 2000},
        blocking=True,
    )
    areas = json.loads(_action_payload(miio)["in"][1]["value"])["areas"]
    assert areas == [[950, 1950, 1050, 2050, 1, 0, 1]]

    miio.calls.clear()
    await hass.services.async_call(
        DOMAIN,
        "vacuum_select_map",
        {ATTR_ENTITY_ID: entity_id, "map_id": 14},
        blocking=True,
    )
    value = json.loads(_action_payload(miio)["in"][0]["value"])
    assert value == {"sm": {}, "mapid": 14}

    miio.calls.clear()
    await hass.services.async_call(
        DOMAIN,
        "vacuum_remote_control_move_step",
        {ATTR_ENTITY_ID: entity_id, "rotation": 64, "velocity": 100},
        blocking=True,
    )
    assert any(m == "set_properties" for m, _ in miio.calls)
    set_val = next(p[0]["value"] for m, p in miio.calls if m == "set_properties")
    assert '"spdv":100' in set_val and '"spdw":64' in set_val

    miio.calls.clear()
    await hass.services.async_call(
        DOMAIN,
        "vacuum_reset_consumable",
        {ATTR_ENTITY_ID: entity_id, "consumable": "main_brush"},
        blocking=True,
    )
    action = _action_payload(miio)
    assert action["siid"] == 9 and action["aiid"] == 1


async def test_reset_a_consumable_the_robot_lacks(hass: HomeAssistant) -> None:
    _entry, miio = await setup_fixture_entry(hass)  # the X10+ has no detergent cartridge

    miio.calls.clear()
    with pytest.raises(ServiceValidationError) as err:
        await hass.services.async_call(
            DOMAIN,
            "vacuum_reset_consumable",
            {ATTR_ENTITY_ID: _vacuum_entity_id(hass), "consumable": "detergent"},
            blocking=True,
        )
    assert err.value.translation_key == "consumable_not_supported"
    assert miio.calls == []


async def test_invalid_service_data_rejected(hass: HomeAssistant) -> None:
    _entry, _miio = await setup_fixture_entry(hass)
    entity_id = _vacuum_entity_id(hass)

    with pytest.raises(MultipleInvalid):
        await hass.services.async_call(
            DOMAIN,
            "vacuum_clean_segment",
            {ATTR_ENTITY_ID: entity_id},
            blocking=True,
        )

    with pytest.raises(MultipleInvalid):
        await hass.services.async_call(
            DOMAIN,
            "vacuum_reset_consumable",
            {ATTR_ENTITY_ID: entity_id, "consumable": "unicorn"},
            blocking=True,
        )

    with pytest.raises(MultipleInvalid):
        await hass.services.async_call(
            DOMAIN,
            "vacuum_clean_zone",
            {ATTR_ENTITY_ID: entity_id, "zone": [1, 2, 3]},
            blocking=True,
        )
