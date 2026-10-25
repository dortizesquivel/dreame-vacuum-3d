"""Fixtures for the tests that run inside Home Assistant."""

from __future__ import annotations

from unittest.mock import patch

import pytest


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Load the integration from custom_components/ in every test."""
    return


@pytest.fixture(autouse=True)
def no_map_file_wait(monkeypatch):
    """Don't wait between tries for a map file the test cloud never has."""
    monkeypatch.setattr("custom_components.dreame_vacuum_3d.MAP_FILE_WAIT", 0)


@pytest.fixture(autouse=True)
def card_urls(hass):
    """The frontend needs its own build, which tests don't have: mark it loaded and record the card URL."""
    hass.config.components.add("frontend")
    urls: list[str] = []
    with patch(
        "custom_components.dreame_vacuum_3d.add_extra_js_url",
        side_effect=lambda _hass, url, es5=False: urls.append(url),
    ):
        yield urls
