"""Sensor and binary_sensor platforms against the X10+ fixture."""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from custom_components.dreame_vacuum_3d.const import DOMAIN
from tests.integration.helpers import setup_fixture_entry


def _entities(hass: HomeAssistant, domain: str) -> dict[str, er.RegistryEntry]:
    registry = er.async_get(hass)
    return {
        entry.unique_id.split("_", 1)[1]: entry
        for entry in registry.entities.values()
        if entry.domain == domain and entry.platform == DOMAIN
    }


async def test_sensors_from_fixture(hass: HomeAssistant) -> None:
    entry, _miio = await setup_fixture_entry(hass)
    sensors = _entities(hass, "sensor")

    expected_present = {
        "state",
        "status",
        "error",
        "battery_level",
        "cleaning_time",
        "cleaned_area",
        "total_cleaning_time",
        "total_cleaned_area",
        "cleaning_count",
        "main_brush_left",
        "main_brush_time_left",
        "side_brush_left",
        "filter_left",
        "mop_pad_left",
        "channel",
        "task_status",
        "relocation_status",
        "self_wash_base_status",
        "auto_empty_status",
        "dust_collection",
        "low_water_warning",
    }
    assert expected_present <= set(sensors)

    # Dock tank/bag properties are not in the X10+ dump (code != 0).
    assert "clean_water_tank_status" not in sensors
    assert "dirty_water_tank_status" not in sensors
    assert "dust_bag_status" not in sensors
    assert "silver_ion_left" not in sensors
    assert "detergent_left" not in sensors  # answers, but the X10+ has no detergent
    assert hass.states.get(sensors["dust_collection"].entity_id).state == "available"
    assert hass.states.get(sensors["low_water_warning"].entity_id).state == "no_warning"

    state = hass.states.get(sensors["state"].entity_id)
    assert state is not None
    assert state.state == "charging_completed"

    battery = hass.states.get(sensors["battery_level"].entity_id)
    assert battery is not None
    assert float(battery.state) == 100
    assert battery.attributes.get("unit_of_measurement") == "%"
    assert battery.attributes.get("state_class") == "measurement"

    total_area = hass.states.get(sensors["total_cleaned_area"].entity_id)
    assert total_area is not None
    assert float(total_area.state) == 33956
    assert total_area.attributes.get("state_class") == "total_increasing"

    channel = hass.states.get(sensors["channel"].entity_id)
    assert channel is not None
    assert channel.state == "local"
    assert sensors["channel"].unique_id == f"{entry.data['did']}_channel"


async def test_binary_sensors_from_fixture(hass: HomeAssistant) -> None:
    _entry, _miio = await setup_fixture_entry(hass)
    binaries = _entities(hass, "binary_sensor")

    assert "charging_status" in binaries
    assert "water_tank" in binaries  # mop attached via water_tank
    assert "mop_pad_installed" not in binaries
    assert "clean_water_tank_status" not in binaries

    charging = hass.states.get(binaries["charging_status"].entity_id)
    assert charging is not None
    assert charging.state == "on"  # fixture charging_status=1 CHARGING
    assert binaries["charging_status"].translation_key == "charging"

    mop = hass.states.get(binaries["water_tank"].entity_id)
    assert mop is not None
    assert mop.state == "off"  # water_tank=0 NOT_INSTALLED
    assert binaries["water_tank"].translation_key == "mop_attached"


async def test_charging_and_mop_on(hass: HomeAssistant) -> None:
    entry, miio = await setup_fixture_entry(
        hass,
        overrides={(3, 2): 1, (4, 6): 1},  # CHARGING, water tank INSTALLED
    )
    # Overrides apply on refresh after discover; re-refresh to pick them up.
    await entry.runtime_data.coordinator.async_refresh()
    await hass.async_block_till_done()

    binaries = _entities(hass, "binary_sensor")
    assert hass.states.get(binaries["charging_status"].entity_id).state == "on"
    assert hass.states.get(binaries["water_tank"].entity_id).state == "on"
    assert miio.overrides[(3, 2)] == 1
