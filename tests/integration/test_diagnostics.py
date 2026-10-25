"""Diagnostics must never leak credentials or serial numbers."""

from __future__ import annotations

import json

from homeassistant.core import HomeAssistant

from custom_components.dreame_vacuum_3d.diagnostics import async_get_config_entry_diagnostics
from tests.integration.helpers import setup_fixture_entry
from tests.integration.test_init import ENTRY_DATA, PASSWORD, USER


async def test_diagnostics_redacts_secrets_and_omits_serial(hass: HomeAssistant) -> None:
    entry, _miio = await setup_fixture_entry(hass)
    # Put a recognizable serial on the live device values.
    entry.runtime_data.coordinator.device.values["serial_number"] = "R2216SECRET99"

    dump = await async_get_config_entry_diagnostics(hass, entry)
    blob = json.dumps(dump)

    secrets = [
        PASSWORD,
        USER,
        ENTRY_DATA["token"],
        ENTRY_DATA["did"],
        ENTRY_DATA["host"],
        ENTRY_DATA["mac"],
        ENTRY_DATA["auth"]["ssecurity"],
        ENTRY_DATA["auth"]["service_token"],
        "R2216SECRET99",
    ]
    for secret in secrets:
        assert secret not in blob, f"leaked {secret!r}"

    assert "serial_number" not in dump["properties"]
    assert "serial_number" not in dump["available"]
    assert "4.14" not in dump["properties"]  # serial_number siid.piid
    # Non-secret property from the X10+ fixture is present (state = 2.1).
    assert "2.1" in dump["properties"]
    assert dump["model"] == "dreame.vacuum.p2114a"
    assert dump["channel"] in {"local", "cloud"}
    assert dump["entry"]["data"]["password"] == "**REDACTED**"
    assert dump["entry"]["unique_id"] == "**REDACTED**"
