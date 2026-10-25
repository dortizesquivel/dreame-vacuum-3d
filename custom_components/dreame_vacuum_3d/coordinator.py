"""Adaptive polling coordinator: fast while cleaning, slow while docked or asleep."""

from __future__ import annotations

from datetime import timedelta
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import CONF_COEXIST, DEFAULT_COEXIST, DOMAIN
from .device.device import DreameDevice
from .device.enums import Status, parse_enum
from .protocol.cloud import CloudAuthError
from .protocol.miio_codec import MiioError

_LOGGER = logging.getLogger(__name__)

INTERVAL_ACTIVE = 2
INTERVAL_DOCKED = 30
INTERVAL_DOCKED_COEXIST = 60
INTERVAL_SLEEP = 120

# Cleaning / returning → poll every 2 s.
_ACTIVE_STATUSES = frozenset(
    {
        Status.CLEANING,
        Status.BACK_HOME,
        Status.PARTIAL_CLEANING,
        Status.FOLLOW_WALL,
        Status.REMOTE_CONTROL,
        Status.SEGMENT_CLEANING,
        Status.ZONE_CLEANING,
        Status.SPOT_CLEANING,
        Status.FAST_MAPPING,
        Status.CRUISING_PATH,
        Status.CRUISING_POINT,
        Status.SUMMON_CLEAN,
        Status.SHORTCUT,
        Status.PERSON_FOLLOW,
        Status.PET_GUARDING,
        Status.AUTO_ARRANGEMENT,
        Status.SMART_ARRANGEMENT,
        Status.ZONED_ARRANGEMENT,
        Status.WATER_CHECK,
    }
)

# Sleeping / powered off → poll every 120 s.
_SLEEP_STATUSES = frozenset({Status.SLEEPING, Status.POWER_OFF})


class DreameDataUpdateCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Polls DreameDevice and only notifies listeners when property values change."""

    config_entry: ConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: ConfigEntry,
        device: DreameDevice,
    ) -> None:
        self.device = device
        self.changed_keys: frozenset[str] = frozenset()
        super().__init__(
            hass,
            _LOGGER,
            config_entry=config_entry,
            name=DOMAIN,
            update_interval=timedelta(seconds=INTERVAL_DOCKED_COEXIST),
            always_update=False,
        )

    @property
    def channel(self) -> str:
        """Active transport channel (`local` or `cloud`)."""
        return self.device.channel

    async def _async_update_data(self) -> dict[str, Any]:
        try:
            changed = await self.device.async_refresh()
        except CloudAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except MiioError as err:
            raise UpdateFailed(str(err)) from err

        self.changed_keys = frozenset(changed)
        self.update_interval = timedelta(seconds=self._interval_seconds())
        # Return a snapshot of values so always_update=False skips listeners when unchanged.
        return dict(self.device.values)

    def _interval_seconds(self) -> int:
        status = parse_enum(Status, self.device.values.get("status"))
        if status in _ACTIVE_STATUSES:
            return INTERVAL_ACTIVE
        if status in _SLEEP_STATUSES:
            return INTERVAL_SLEEP
        coexist = self.config_entry.options.get(CONF_COEXIST, DEFAULT_COEXIST)
        return INTERVAL_DOCKED_COEXIST if coexist else INTERVAL_DOCKED
