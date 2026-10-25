"""Shared coordinator entity base for all platforms."""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from functools import wraps
from typing import Any

from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import EntityDescription
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_DID, DOMAIN
from .coordinator import DreameDataUpdateCoordinator
from .device.device import DreameDevice
from .protocol.cloud import CloudError
from .protocol.miio_codec import MiioError
from .protocol.miot import MiotError


def command[**P, R](func: Callable[P, Coroutine[Any, Any, R]]) -> Callable[P, Coroutine[Any, Any, R]]:
    """Report a command the robot rejects or doesn't answer as a readable error, not a traceback."""

    @wraps(func)
    async def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        try:
            return await func(*args, **kwargs)
        except (MiioError, MiotError, CloudError) as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="command_failed",
                translation_placeholders={"error": str(err)},
            ) from err

    return wrapper


class DreameEntity(CoordinatorEntity[DreameDataUpdateCoordinator]):
    """Coordinator entity with stable unique_id and translation_key from description.key."""

    _attr_has_entity_name = True
    entity_description: EntityDescription

    def __init__(
        self,
        coordinator: DreameDataUpdateCoordinator,
        description: EntityDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        did = str(coordinator.config_entry.data[CONF_DID])
        self._attr_unique_id = f"{did}_{description.key}"
        self._attr_translation_key = description.translation_key or description.key
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, did)})

    @property
    def device(self) -> DreameDevice:
        """The robot bound to this config entry."""
        return self.coordinator.device
