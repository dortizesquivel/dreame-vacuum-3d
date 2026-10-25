"""Vacuum entity: activity transitions and cleaning commands."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

from homeassistant.components.vacuum import VacuumActivity, VacuumEntityFeature
from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.dreame_vacuum_3d.const import DOMAIN
from custom_components.dreame_vacuum_3d.device.enums import DreameVacuumState, SuctionLevel
from tests.integration.helpers import FixtureMiio, setup_fixture_entry
from tests.integration.test_init import ENTRY_DATA, _mock_cloud


def _vacuum_entity_id(hass: HomeAssistant) -> str:
    registry = er.async_get(hass)
    for entry in registry.entities.values():
        if entry.domain == "vacuum" and entry.platform == DOMAIN:
            return entry.entity_id
    raise AssertionError("vacuum entity not registered")


async def test_vacuum_created_from_fixture(hass: HomeAssistant) -> None:
    entry, _miio = await setup_fixture_entry(hass)
    entity_id = _vacuum_entity_id(hass)
    registry = er.async_get(hass)
    entity = registry.async_get(entity_id)
    assert entity is not None
    assert entity.unique_id == f"{entry.data['did']}_vacuum"
    assert entity.translation_key == "vacuum"

    state = hass.states.get(entity_id)
    assert state is not None
    # Fixture state=13 (CHARGING_COMPLETED) → docked; suction_level=2 → strong; battery 100.
    assert state.state == VacuumActivity.DOCKED
    assert state.attributes.get("fan_speed") == "strong"
    assert state.attributes.get("battery_level") == 100


async def test_vacuum_absent_without_state(hass: HomeAssistant) -> None:
    class NoStateMiio(FixtureMiio):
        async def _send(self, method: str, params: Any = None) -> Any:
            if method == "get_properties":
                self.calls.append((method, params))
                out = []
                for item in params or []:
                    key = (item["siid"], item["piid"])
                    row = self.by_siid_piid.get(key)
                    if key == (2, 1) or row is None:
                        code, value = -1, None
                    else:
                        code, value = row["code"], row["value"]
                    out.append(
                        {
                            "did": item["did"],
                            "siid": item["siid"],
                            "piid": item["piid"],
                            "code": code,
                            "value": value,
                        }
                    )
                return out
            return await super()._send(method, params)

    entry = MockConfigEntry(domain=DOMAIN, data=ENTRY_DATA, unique_id=ENTRY_DATA["did"], title="X10+")
    entry.add_to_hass(hass)
    miio = NoStateMiio()
    cloud = _mock_cloud()
    cloud.rpc = AsyncMock(side_effect=miio.rpc)
    with (
        patch("custom_components.dreame_vacuum_3d.XiaomiCloud", return_value=cloud),
        patch("custom_components.dreame_vacuum_3d.MiioClient", return_value=miio),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert not any(e.domain == "vacuum" and e.platform == DOMAIN for e in er.async_get(hass).entities.values())
    assert "state" not in entry.runtime_data.coordinator.device.available


async def test_activity_transitions(hass: HomeAssistant) -> None:
    entry, miio = await setup_fixture_entry(hass)
    entity_id = _vacuum_entity_id(hass)
    device = entry.runtime_data.coordinator.device

    cases = [
        (DreameVacuumState.IDLE, VacuumActivity.IDLE),
        (DreameVacuumState.PAUSED, VacuumActivity.PAUSED),
        (DreameVacuumState.RETURNING, VacuumActivity.RETURNING),
        (DreameVacuumState.CHARGING, VacuumActivity.DOCKED),
        (DreameVacuumState.ERROR, VacuumActivity.ERROR),
        (DreameVacuumState.SWEEPING, VacuumActivity.CLEANING),
        (DreameVacuumState.MOPPING, VacuumActivity.CLEANING),
        (DreameVacuumState.SPOT_CLEANING, VacuumActivity.CLEANING),
    ]
    for dreame_state, activity in cases:
        miio.overrides[(2, 1)] = dreame_state.value
        await entry.runtime_data.coordinator.async_refresh()
        await hass.async_block_till_done()
        assert device.values["state"] == dreame_state.value
        assert hass.states.get(entity_id).state == activity


async def test_commands_send_miot(hass: HomeAssistant) -> None:
    _entry, miio = await setup_fixture_entry(hass)
    entity_id = _vacuum_entity_id(hass)

    async def call(service: str, **extra: Any) -> None:
        miio.calls.clear()
        await hass.services.async_call(
            "vacuum",
            service,
            {ATTR_ENTITY_ID: entity_id, **extra},
            blocking=True,
        )

    await call("start")
    assert ("action", {"did": "call", "siid": 2, "aiid": 1, "in": []}) in miio.calls

    await call("pause")
    assert ("action", {"did": "call", "siid": 2, "aiid": 2, "in": []}) in miio.calls

    await call("stop")
    assert ("action", {"did": "call", "siid": 4, "aiid": 2, "in": []}) in miio.calls

    await call("return_to_base")
    assert ("action", {"did": "call", "siid": 3, "aiid": 1, "in": []}) in miio.calls

    await call("locate")
    assert ("action", {"did": "call", "siid": 7, "aiid": 1, "in": []}) in miio.calls

    # No spot clean: HA's has no point, and (0, 0) isn't the base.
    assert not hass.states.get(entity_id).attributes["supported_features"] & VacuumEntityFeature.CLEAN_SPOT

    await call("set_fan_speed", fan_speed="quiet")
    assert any(
        m == "set_properties" and any(p.get("value") == SuctionLevel.QUIET.value for p in (params or []))
        for m, params in miio.calls
    )
    assert hass.states.get(entity_id).attributes.get("fan_speed") == "quiet"
