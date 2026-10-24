"""Select, switch, number and button platforms against the X10+ fixture."""

from __future__ import annotations

from homeassistant.const import ATTR_ENTITY_ID, STATE_OFF, STATE_ON
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from custom_components.dreame_vacuum_3d.const import DOMAIN
from custom_components.dreame_vacuum_3d.device.enums import CleaningMode, SuctionLevel
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
        "mop_wash_level",
        "carpet_cleaning",
    } <= set(selects)

    # Packed cleaning_mode 204290 → SWEEPING (bits == 2).
    assert hass.states.get(selects["cleaning_mode"].entity_id).state == "sweeping"
    assert hass.states.get(selects["suction_level"].entity_id).state == "strong"
    assert hass.states.get(selects["auto_empty_frequency"].entity_id).state == "3"

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

    assert {"carpet_boost", "dnd", "child_lock", "obstacle_avoidance", "auto_mount_mop", "self_clean"} <= set(
        switches
    )
    assert {"volume", "dnd_start", "dnd_end"} <= set(numbers)

    assert hass.states.get(switches["dnd"].entity_id).state == STATE_ON
    assert hass.states.get(switches["child_lock"].entity_id).state == STATE_OFF
    assert float(hass.states.get(numbers["volume"].entity_id).state) == 100
    # dnd_start 22:00 → 1320 minutes
    assert float(hass.states.get(numbers["dnd_start"].entity_id).state) == 22 * 60
    assert float(hass.states.get(numbers["dnd_end"].entity_id).state) == 8 * 60

    miio.calls.clear()
    await hass.services.async_call(
        "switch",
        "turn_off",
        {ATTR_ENTITY_ID: switches["dnd"].entity_id},
        blocking=True,
    )
    assert any(
        m == "set_properties" and any(p.get("value") is False for p in params) for m, params in miio.calls
    )

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
        "number",
        "set_value",
        {ATTR_ENTITY_ID: numbers["dnd_start"].entity_id, "value": 23 * 60},
        blocking=True,
    )
    assert any(
        m == "set_properties" and any(p.get("value") == "23:00" for p in params) for m, params in miio.calls
    )


async def test_buttons_reset_wash_dry_empty(hass: HomeAssistant) -> None:
    _entry, miio = await setup_fixture_entry(hass)
    buttons = _by_key(hass, "button")

    assert "reset_main_brush" in buttons
    assert "reset_detergent" in buttons
    assert "reset_silver_ion" not in buttons  # property missing on X10+ dump
    assert "wash_mop" in buttons
    assert "dry_mop" in buttons
    assert "start_auto_empty" in buttons
    assert buttons["start_auto_empty"].translation_key == "empty"

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

    miio.calls.clear()
    await hass.services.async_call(
        "button",
        "press",
        {ATTR_ENTITY_ID: buttons["start_auto_empty"].entity_id},
        blocking=True,
    )
    assert ("action", {"did": "call", "siid": 15, "aiid": 1, "in": []}) in miio.calls


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
