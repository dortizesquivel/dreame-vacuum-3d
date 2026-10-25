"""Adaptive polling coordinator: intervals, errors, and change notifications."""

from __future__ import annotations

from datetime import timedelta
from typing import Any
from unittest.mock import Mock

from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import UpdateFailed
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.dreame_vacuum_3d.const import CONF_COEXIST, DOMAIN
from custom_components.dreame_vacuum_3d.coordinator import (
    INTERVAL_ACTIVE,
    INTERVAL_DOCKED,
    INTERVAL_DOCKED_COEXIST,
    INTERVAL_SLEEP,
    DreameDataUpdateCoordinator,
)
from custom_components.dreame_vacuum_3d.device.enums import Status
from custom_components.dreame_vacuum_3d.protocol.cloud import CloudAuthError, CloudConnectionError
from custom_components.dreame_vacuum_3d.protocol.miio_codec import MiioError


class FakeDevice:
    """Minimal stand-in for DreameDevice used by the coordinator."""

    def __init__(self, status: Status = Status.CHARGING) -> None:
        self.values: dict[str, Any] = {"status": status.value, "battery_level": 80}
        self.channel = "local"
        self.available: frozenset[str] = frozenset(self.values)
        self.next_changed: set[str] = set()
        self.error: Exception | None = None

    async def async_refresh(self) -> set[str]:
        if self.error is not None:
            raise self.error
        changed = self.next_changed
        self.next_changed = set()
        return changed


def _entry(**options: Any) -> MockConfigEntry:
    return MockConfigEntry(domain=DOMAIN, data={}, options=options)


async def _make(hass, device: FakeDevice, entry: MockConfigEntry | None = None) -> DreameDataUpdateCoordinator:
    config_entry = entry or _entry()
    config_entry.add_to_hass(hass)
    coordinator = DreameDataUpdateCoordinator(hass, config_entry, device)  # type: ignore[arg-type]
    # Unit tests are outside config-entry setup; async_refresh is enough.
    await coordinator.async_refresh()
    return coordinator


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (Status.CLEANING, INTERVAL_ACTIVE),
        (Status.SEGMENT_CLEANING, INTERVAL_ACTIVE),
        (Status.ZONE_CLEANING, INTERVAL_ACTIVE),
        (Status.SPOT_CLEANING, INTERVAL_ACTIVE),
        (Status.BACK_HOME, INTERVAL_ACTIVE),
        (Status.SLEEPING, INTERVAL_SLEEP),
        (Status.POWER_OFF, INTERVAL_SLEEP),
    ],
)
async def test_interval_by_status(hass, status: Status, expected: int) -> None:
    device = FakeDevice(status)
    coordinator = await _make(hass, device)
    assert coordinator.update_interval == timedelta(seconds=expected)


async def test_docked_interval_coexist_default_is_60s(hass) -> None:
    device = FakeDevice(Status.CHARGING)
    coordinator = await _make(hass, device)  # coexist defaults to True
    assert coordinator.update_interval == timedelta(seconds=INTERVAL_DOCKED_COEXIST)


async def test_docked_interval_without_coexist_is_30s(hass) -> None:
    device = FakeDevice(Status.IDLE)
    coordinator = await _make(hass, device, _entry(**{CONF_COEXIST: False}))
    assert coordinator.update_interval == timedelta(seconds=INTERVAL_DOCKED)


async def test_interval_adapts_when_status_changes(hass) -> None:
    device = FakeDevice(Status.CHARGING)
    coordinator = await _make(hass, device)
    assert coordinator.update_interval == timedelta(seconds=INTERVAL_DOCKED_COEXIST)

    device.values["status"] = Status.CLEANING.value
    await coordinator.async_refresh()
    assert coordinator.update_interval == timedelta(seconds=INTERVAL_ACTIVE)


@pytest.mark.parametrize("error", [MiioError("unreachable"), CloudConnectionError("can't reach de.api.io.mi.com")])
async def test_unreachable_robot_marks_update_failed(hass, error: Exception) -> None:
    device = FakeDevice()
    coordinator = await _make(hass, device)
    assert coordinator.last_update_success is True

    device.error = error
    await coordinator.async_refresh()
    assert coordinator.last_update_success is False
    assert isinstance(coordinator.last_exception, UpdateFailed)


async def test_cloud_auth_error_raises_reauth(hass) -> None:
    device = FakeDevice()
    device.error = CloudAuthError("invalid_auth")
    entry = _entry()
    entry.add_to_hass(hass)
    coordinator = DreameDataUpdateCoordinator(hass, entry, device)  # type: ignore[arg-type]

    with pytest.raises(ConfigEntryAuthFailed):
        await coordinator._async_update_data()


async def test_exposes_device_channel_and_changed_keys(hass) -> None:
    device = FakeDevice()
    device.channel = "cloud"
    device.next_changed = {"battery_level"}
    coordinator = await _make(hass, device)

    assert coordinator.device is device
    assert coordinator.channel == "cloud"
    assert coordinator.changed_keys == frozenset({"battery_level"})


async def test_no_notify_when_nothing_changes(hass) -> None:
    device = FakeDevice()
    device.next_changed = {"battery_level"}
    coordinator = await _make(hass, device)

    listener = Mock()
    coordinator.async_add_listener(listener)
    listener.reset_mock()

    # Second poll: no property changes → listeners must stay quiet (always_update=False).
    await coordinator.async_refresh()
    listener.assert_not_called()
    assert coordinator.changed_keys == frozenset()
