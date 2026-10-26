"""Adaptive polling coordinator: fast while cleaning, slow while docked or asleep."""

from __future__ import annotations

from datetime import timedelta
import logging
from time import monotonic
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import CONF_COEXIST, DEFAULT_COEXIST, DOMAIN
from .device.device import DreameDevice
from .device.enums import ChargingStatus, Status, parse_enum
from .protocol.cloud import CloudAuthError, CloudConnectionError
from .protocol.miio_codec import MiioError

_LOGGER = logging.getLogger(__name__)

INTERVAL_ACTIVE = 2
INTERVAL_DOCKED = 30
INTERVAL_DOCKED_COEXIST = 60
INTERVAL_SLEEP = 120
STUCK_RETURN = 180  # s charging on its dock while still "returning" before the robot is sent home once more

# Cleaning / returning → poll every 2 s.
ACTIVE_STATUSES = frozenset(
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
        self._home_since: float | None = None  # since when it charges on its dock while it says it's returning
        self._sent_home = False
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
        except (MiioError, CloudConnectionError) as err:
            raise UpdateFailed(str(err)) from err

        self.changed_keys = frozenset(changed)
        self.update_interval = timedelta(seconds=self._interval_seconds())
        self._unstick_return()
        # Return a snapshot of values so always_update=False skips listeners when unchanged.
        return dict(self.device.values)

    def _unstick_return(self) -> None:
        """Sometimes the robot reaches its dock and charges but keeps "returning": the task doesn't end for many
        minutes, or until the battery runs low (seen on the X10+; Tasshack #1609 on an X50). Sending it home once more
        ends it."""
        values = self.device.values
        stuck = (
            parse_enum(Status, values.get("status")) is Status.BACK_HOME
            and parse_enum(ChargingStatus, values.get("charging_status")) is ChargingStatus.CHARGING
        )
        if not stuck:
            self._home_since, self._sent_home = None, False
            return
        if self._home_since is None:
            self._home_since = monotonic()
        if not self._sent_home and monotonic() - self._home_since >= STUCK_RETURN:
            self._sent_home = True
            _LOGGER.info("The robot charges on its dock but still says it's returning: sending it home to end the task")
            self.hass.async_create_background_task(self._send_home(), f"{DOMAIN} end a stuck return")

    async def _send_home(self) -> None:
        try:
            await self.device.return_to_base()
        except (MiioError, CloudConnectionError, CloudAuthError) as err:
            _LOGGER.debug("Couldn't send the robot home: %s", err)

    def _interval_seconds(self) -> int:
        status = parse_enum(Status, self.device.values.get("status"))
        if status in ACTIVE_STATUSES:
            return INTERVAL_ACTIVE
        if status in _SLEEP_STATUSES:
            return INTERVAL_SLEEP
        coexist = self.config_entry.options.get(CONF_COEXIST, DEFAULT_COEXIST)
        return INTERVAL_DOCKED_COEXIST if coexist else INTERVAL_DOCKED
