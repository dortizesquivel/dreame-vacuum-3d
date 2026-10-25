"""Smoke: the integration loads a config entry end-to-end."""

from __future__ import annotations

from unittest.mock import patch

from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.dreame_vacuum_3d.const import DOMAIN
from tests.integration.test_init import ENTRY_DATA, _mock_cloud, _mock_miio


async def test_entry_sets_up(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(domain=DOMAIN, data=ENTRY_DATA, unique_id=ENTRY_DATA["did"])
    entry.add_to_hass(hass)
    with (
        patch("custom_components.dreame_vacuum_3d.XiaomiCloud", return_value=_mock_cloud()),
        patch("custom_components.dreame_vacuum_3d.MiioClient", return_value=_mock_miio()),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
