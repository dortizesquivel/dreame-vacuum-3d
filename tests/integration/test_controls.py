"""Select, switch, number and button platforms against the X10+ fixture."""

from __future__ import annotations

import asyncio

from homeassistant.const import ATTR_ENTITY_ID, STATE_OFF, STATE_ON
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
import pytest

from custom_components.dreame_vacuum_3d.const import DOMAIN
from custom_components.dreame_vacuum_3d.device.enums import CleaningMode, SuctionLevel
from custom_components.dreame_vacuum_3d.protocol.miot import MiotError
from tests.integration import helpers
from tests.integration.helpers import setup_fixture_entry


def _by_key(hass: HomeAssistant, domain: str) -> dict[str, er.RegistryEntry]:
    registry = er.async_get(hass)
    out: dict[str, er.RegistryEntry] = {}
    for entry in registry.entities.values():
        if entry.domain != domain or entry.platform != DOMAIN:
            continue
        # unique_id = "{did}_{key}"
        out[entry.unique_id.split("_", 1)[1]] = entry
    return out


async def test_selects_created_and_write(hass: HomeAssistant) -> None:
    _entry, miio = await setup_fixture_entry(hass)
    selects = _by_key(hass, "select")

    assert {
        "cleaning_mode",
        "suction_level",
        "water_volume",
        "auto_empty_frequency",
        "drying_time",
        "mop_wash_level",
        "carpet_cleaning",
        "carpet_sensitivity",
    } <= set(selects)

    # Packed cleaning_mode 204290 → SWEEPING (bits == 2).
    assert hass.states.get(selects["cleaning_mode"].entity_id).state == "sweeping"
    assert hass.states.get(selects["suction_level"].entity_id).state == "strong"
    assert hass.states.get(selects["auto_empty_frequency"].entity_id).state == "3"
    assert hass.states.get(selects["drying_time"].entity_id).state == "2"
    assert hass.states.get(selects["carpet_sensitivity"].entity_id).state == "medium"
    await hass.services.async_call(
        "select",
        "select_option",
        {ATTR_ENTITY_ID: selects["carpet_sensitivity"].entity_id, "option": "high"},
        blocking=True,
    )
    assert miio.overrides[(4, 28)] == 3

    miio.calls.clear()
    await hass.services.async_call(
        "select",
        "select_option",
        {ATTR_ENTITY_ID: selects["drying_time"].entity_id, "option": "4"},
        blocking=True,
    )
    assert miio.overrides[(4, 40)] == 4

    miio.calls.clear()
    await hass.services.async_call(
        "select",
        "select_option",
        {ATTR_ENTITY_ID: selects["suction_level"].entity_id, "option": "quiet"},
        blocking=True,
    )
    assert any(
        m == "set_properties" and any(p.get("value") == SuctionLevel.QUIET.value for p in params)
        for m, params in miio.calls
    )
    assert hass.states.get(selects["suction_level"].entity_id).state == "quiet"

    miio.calls.clear()
    await hass.services.async_call(
        "select",
        "select_option",
        {ATTR_ENTITY_ID: selects["cleaning_mode"].entity_id, "option": "mopping"},
        blocking=True,
    )
    # Packed write keeps high bytes, sets low bits to 1 (mopping).
    set_calls = [params for m, params in miio.calls if m == "set_properties"]
    assert set_calls
    written = set_calls[0][0]["value"]
    assert isinstance(written, int) and written > 3
    assert (written & 0x03) == 1
    assert hass.states.get(selects["cleaning_mode"].entity_id).state == "mopping"


async def test_switches_and_numbers(hass: HomeAssistant) -> None:
    _entry, miio = await setup_fixture_entry(hass)
    switches = _by_key(hass, "switch")
    numbers = _by_key(hass, "number")
    times = _by_key(hass, "time")

    assert {"carpet_boost", "dnd", "child_lock", "obstacle_avoidance", "self_clean", "resume_cleaning"} <= set(switches)
    assert hass.states.get(switches["tight_mopping"].entity_id).state == STATE_OFF
    await hass.services.async_call(
        "switch", "turn_on", {ATTR_ENTITY_ID: switches["tight_mopping"].entity_id}, blocking=True
    )
    assert miio.overrides[(4, 29)] in (1, True)
    assert "auto_mount_mop" not in switches  # answers, but the X10+ doesn't mount its mop
    assert set(numbers) == {"volume"}
    assert set(times) == {"dnd_start", "dnd_end"}

    assert hass.states.get(switches["dnd"].entity_id).state == STATE_ON
    assert hass.states.get(switches["child_lock"].entity_id).state == STATE_OFF
    assert float(hass.states.get(numbers["volume"].entity_id).state) == 100
    assert hass.states.get(times["dnd_start"].entity_id).state == "22:00:00"
    assert hass.states.get(times["dnd_end"].entity_id).state == "08:00:00"

    miio.calls.clear()
    await hass.services.async_call(
        "switch",
        "turn_off",
        {ATTR_ENTITY_ID: switches["dnd"].entity_id},
        blocking=True,
    )
    assert any(m == "set_properties" and any(p.get("value") is False for p in params) for m, params in miio.calls)

    miio.calls.clear()
    await hass.services.async_call(
        "number",
        "set_value",
        {ATTR_ENTITY_ID: numbers["volume"].entity_id, "value": 40},
        blocking=True,
    )
    assert any(m == "set_properties" and any(p.get("value") == 40 for p in params) for m, params in miio.calls)

    miio.calls.clear()
    await hass.services.async_call(
        "time",
        "set_value",
        {ATTR_ENTITY_ID: times["dnd_start"].entity_id, "time": "23:30:00"},
        blocking=True,
    )
    assert any(m == "set_properties" and any(p.get("value") == "23:30" for p in params) for m, params in miio.calls)


async def test_ai_switches_flip_one_bit(hass: HomeAssistant) -> None:
    entry, miio = await setup_fixture_entry(hass)
    switches = _by_key(hass, "switch")
    # Fixture 4.22 = 287: furniture, obstacle, picture, fluid, pet and fuzzy on; image upload and pet picture off.
    assert hass.states.get(switches["ai_pet_detection"].entity_id).state == STATE_ON
    assert hass.states.get(switches["ai_image_upload"].entity_id).state == STATE_OFF

    for service, key, expected in [
        ("turn_off", "ai_pet_detection", 287 - 16),
        ("turn_on", "ai_image_upload", 287 + 32),
    ]:
        miio.overrides[(4, 22)] = 287
        await entry.runtime_data.coordinator.async_refresh()
        miio.calls.clear()
        await hass.services.async_call("switch", service, {ATTR_ENTITY_ID: switches[key].entity_id}, blocking=True)
        writes = [p for m, params in miio.calls if m == "set_properties" for p in params]
        assert writes == [{"did": "set", "siid": 4, "piid": 22, "value": expected}]


async def test_ai_switches_flipped_together_keep_both_bits(hass: HomeAssistant) -> None:
    entry, miio = await setup_fixture_entry(hass)
    switches = _by_key(hass, "switch")
    send = miio.send.side_effect

    async def slow_send(method: str, params: object = None) -> object:
        await asyncio.sleep(0)  # the robot takes a moment to answer
        return await send(method, params)

    miio.send.side_effect = slow_send
    await asyncio.gather(
        *(
            hass.services.async_call("switch", service, {ATTR_ENTITY_ID: switches[key].entity_id}, blocking=True)
            for service, key in (("turn_off", "ai_pet_detection"), ("turn_on", "ai_image_upload"))
        )
    )
    assert miio.overrides[(4, 22)] == 287 - 16 + 32

    # A poll that read the mask before the last write and ended after it: the next flip starts from the robot's.
    entry.runtime_data.coordinator.device.values["ai_obstacle_detection"] = 287
    await hass.services.async_call(
        "switch", "turn_off", {ATTR_ENTITY_ID: switches["fuzzy_obstacle_detection"].entity_id}, blocking=True
    )
    assert miio.overrides[(4, 22)] == 287 - 16 + 32 - 256


async def test_failed_command_is_a_readable_error(hass: HomeAssistant) -> None:
    _entry, miio = await setup_fixture_entry(hass)
    switches = _by_key(hass, "switch")
    miio.send.side_effect = MiotError(-4004)
    with pytest.raises(HomeAssistantError) as err:
        await hass.services.async_call("switch", "turn_off", {ATTR_ENTITY_ID: switches["dnd"].entity_id}, blocking=True)
    assert err.value.translation_key == "command_failed"


async def test_buttons_reset_wash_dry_empty(hass: HomeAssistant) -> None:
    _entry, miio = await setup_fixture_entry(hass)
    buttons = _by_key(hass, "button")

    assert "reset_main_brush" in buttons
    assert "reset_detergent" not in buttons  # no detergent on the X10+
    assert "reset_silver_ion" not in buttons  # property missing on X10+ dump
    assert "wash_mop" in buttons
    assert "dry_mop" in buttons
    assert "start_auto_empty" in buttons
    assert buttons["start_auto_empty"].translation_key == "empty"
    # The dock's actions are everyday controls, not hidden under configuration.
    for key in ("start_auto_empty", "wash_mop", "stop_washing", "dry_mop", "stop_drying"):
        assert buttons[key].entity_category is None
    assert "clear_warning" in buttons

    miio.calls.clear()
    await hass.services.async_call(
        "button",
        "press",
        {ATTR_ENTITY_ID: buttons["reset_main_brush"].entity_id},
        blocking=True,
    )
    assert ("action", {"did": "call", "siid": 9, "aiid": 1, "in": []}) in miio.calls

    miio.calls.clear()
    await hass.services.async_call(
        "button",
        "press",
        {ATTR_ENTITY_ID: buttons["wash_mop"].entity_id},
        blocking=True,
    )
    wash = next(c for c in miio.calls if c[0] == "action")[1]
    assert wash["siid"] == 4 and wash["aiid"] == 4
    assert wash["in"][0]["value"] == "2,1"

    miio.calls.clear()
    await hass.services.async_call(
        "button",
        "press",
        {ATTR_ENTITY_ID: buttons["dry_mop"].entity_id},
        blocking=True,
    )
    dry = next(c for c in miio.calls if c[0] == "action")[1]
    assert dry["in"][0]["value"] == "3,1"

    for key, value in (("stop_washing", "2,0"), ("stop_drying", "3,0")):
        miio.calls.clear()
        await hass.services.async_call("button", "press", {ATTR_ENTITY_ID: buttons[key].entity_id}, blocking=True)
        stop = next(c for c in miio.calls if c[0] == "action")[1]
        assert stop["siid"] == 4 and stop["aiid"] == 4
        assert stop["in"][0]["value"] == value

    miio.calls.clear()
    await hass.services.async_call(
        "button", "press", {ATTR_ENTITY_ID: buttons["clear_warning"].entity_id}, blocking=True
    )
    assert ("action", {"did": "call", "siid": 4, "aiid": 3, "in": []}) in miio.calls

    miio.calls.clear()
    await hass.services.async_call(
        "button",
        "press",
        {ATTR_ENTITY_ID: buttons["start_auto_empty"].entity_id},
        blocking=True,
    )
    assert ("action", {"did": "call", "siid": 15, "aiid": 1, "in": []}) in miio.calls


async def test_dock_buttons_absent_without_a_dock(hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch) -> None:
    """A robot whose dock reports neither auto-empty nor self-wash gets none of the dock's buttons."""
    rows = helpers.fixture_by_key()
    for key in ("auto_empty_status", "self_wash_base_status", "warn_status"):
        rows[key] = {**rows[key], "code": -1, "value": None}
    monkeypatch.setattr(helpers, "fixture_by_key", lambda: rows)
    await setup_fixture_entry(hass)
    buttons = _by_key(hass, "button")
    for key in ("start_auto_empty", "wash_mop", "stop_washing", "dry_mop", "stop_drying", "clear_warning"):
        assert key not in buttons
    assert "reset_main_brush" in buttons


async def test_device_cleaning_mode_helpers() -> None:
    from custom_components.dreame_vacuum_3d.device.capabilities import load
    from custom_components.dreame_vacuum_3d.device.device import DreameDevice
    from tests.device.test_device import FakeSend

    caps = load("dreame.vacuum.p2114a")
    device = DreameDevice(caps, FakeSend(), FakeSend())
    await device.async_discover()
    assert device.cleaning_mode() == CleaningMode.SWEEPING.value
    await device.set_cleaning_mode(CleaningMode.MOPPING.value)
    assert device.values["cleaning_mode"] & 0x03 == 1
    assert device.cleaning_mode() == CleaningMode.MOPPING.value
