"""Fire domain events and persistent notifications from property changes.

Portions of the event/notification model derived from Tasshack/dreame-vacuum (MIT), see NOTICE.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components import persistent_notification
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_DEVICE_ID, ATTR_ENTITY_ID, CONF_MAC
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.helpers.translation import async_get_translations

from .const import (
    CONF_CONSUMABLE_THRESHOLD,
    CONF_DID,
    CONF_NOTIFY,
    CONSUMABLE_LIFE,
    DEFAULT_CONSUMABLE_THRESHOLD,
    DOMAIN,
    EVENT_CONSUMABLE,
    EVENT_ERROR,
    EVENT_INFORMATION,
    EVENT_TASK_STATUS,
    NOTIFICATION,
)
from .coordinator import ACTIVE_TASKS, DreameDataUpdateCoordinator
from .device.enums import ErrorCode, LowWaterWarning, TaskStatus, parse_enum


class DreameNotifier:
    """Watches coordinator updates and emits events / persistent notifications."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        coordinator: DreameDataUpdateCoordinator,
    ) -> None:
        self.hass = hass
        self.entry = entry
        self.coordinator = coordinator
        self._prev: dict[str, Any] = dict(coordinator.device.values)
        self._texts: dict[str, str] = {}

    async def async_setup(self) -> None:
        """Load the texts in Home Assistant's language and start listening; call once after the first refresh."""
        language = self.hass.config.language
        # Notification texts live under "exceptions", the only free-text category hassfest accepts.
        for category in ("exceptions", "entity"):
            self._texts |= await async_get_translations(self.hass, language, category, [DOMAIN])
        self.entry.async_on_unload(self.coordinator.async_add_listener(self._async_handle))

    def _text(self, key: str, fallback: str, **placeholders: object) -> str:
        """Translated text for key (relative to the integration), fallback if there's none."""
        return self._texts.get(f"component.{DOMAIN}.{key}", fallback).format(**placeholders)

    def _state_text(self, sensor: str, state: str) -> str:
        return self._text(f"entity.sensor.{sensor}.state.{state}", state.replace("_", " ").capitalize())

    @callback
    def _async_handle(self) -> None:
        changed = self.coordinator.changed_keys
        if not changed:
            return
        values = self.coordinator.device.values
        if "task_status" in changed:
            self._handle_task_status(values)
        if "error" in changed:
            self._handle_error(values)
        if "warn_status" in changed or "low_water_warning" in changed or "cleaning_paused" in changed:
            self._handle_information(values, changed)
        for prop in CONSUMABLE_LIFE:
            if prop in changed:
                self._handle_consumable(prop, values)
        self._prev = dict(values)

    def _notify_enabled(self, category: str) -> bool:
        notify = self.entry.options.get(CONF_NOTIFY, list(NOTIFICATION))
        if notify is True:
            return True
        if notify is False:
            return False
        return category in notify

    def _ids(self) -> tuple[str | None, str | None]:
        did = str(self.entry.data[CONF_DID])
        entity_id = er.async_get(self.hass).async_get_entity_id("vacuum", DOMAIN, f"{did}_vacuum")
        device = dr.async_get(self.hass).async_get_device({(DOMAIN, did)})
        return entity_id, device.id if device else None

    def _fire(self, event_type: str, data: dict[str, Any]) -> None:
        entity_id, device_id = self._ids()
        payload = {ATTR_ENTITY_ID: entity_id, ATTR_DEVICE_ID: device_id, **data}
        self.hass.bus.async_fire(event_type, payload)

    def _nid(self, suffix: str) -> str:
        mac = (self.entry.data.get(CONF_MAC) or "device").replace(":", "").lower()
        return f"{DOMAIN}_{mac}_{suffix}"

    def _create(self, category: str, message: str, suffix: str) -> None:
        if not self._notify_enabled(category):
            return
        persistent_notification.async_create(
            self.hass,
            message,
            title=self.entry.title,
            notification_id=self._nid(suffix),
        )

    def _dismiss(self, suffix: str) -> None:
        persistent_notification.async_dismiss(self.hass, self._nid(suffix))

    def _handle_task_status(self, values: dict[str, Any]) -> None:
        previous = parse_enum(TaskStatus, self._prev.get("task_status"))
        current = parse_enum(TaskStatus, values.get("task_status"))
        if previous is None or current is None:
            return
        if previous not in ACTIVE_TASKS:
            return
        if current is TaskStatus.COMPLETED:
            completed = True
        elif current not in ACTIVE_TASKS:
            completed = False
        else:
            return
        area = values.get("cleaned_area")
        duration = values.get("cleaning_time")
        device = self.coordinator.device
        data = {
            "completed": completed,
            "area": area,
            "duration": duration,
            "segments": device.segments,
        }
        device.segments = None  # they belonged to this task
        self._fire(EVENT_TASK_STATUS, data)
        if completed:
            message = self._text("exceptions.notify_cleanup_completed.message", "Cleaning completed")
            self._create("cleanup_completed", message, "cleanup_completed")
        else:
            self._dismiss("cleanup_completed")

    def _handle_error(self, values: dict[str, Any]) -> None:
        code = parse_enum(ErrorCode, values.get("error"))
        if code is None:
            return
        if code is ErrorCode.NO_ERROR or code.value == 0:
            self._dismiss("error")
            return
        name = code.name.lower()
        self._fire(EVENT_ERROR, {"error": name.replace("_", " "), "code": code.value})
        self._create("error", self._state_text("error", name), "error")

    def _handle_information(self, values: dict[str, Any], changed: frozenset[str]) -> None:
        if "warn_status" in changed:
            warn = values.get("warn_status")
            if isinstance(warn, int) and warn != 0:
                self._fire(EVENT_INFORMATION, {"information": "warn_status", "code": warn})
                message = self._text("exceptions.notify_station_warning.message", "Station warning ({code})", code=warn)
                self._create("information", message, "warn_status")
            else:
                self._dismiss("warn_status")
        if "low_water_warning" in changed:
            low = values.get("low_water_warning")
            if isinstance(low, int) and low != 0:
                self._fire(EVENT_INFORMATION, {"information": "low_water_warning", "code": low})
                warning = parse_enum(LowWaterWarning, low)
                name = warning.name.lower() if warning is not None else str(low)
                self._create("warning", self._state_text("low_water_warning", name), "low_water")
            else:
                self._dismiss("low_water")
        if "cleaning_paused" in changed:
            paused = values.get("cleaning_paused")
            if paused:
                self._fire(EVENT_INFORMATION, {"information": "cleaning_paused"})
                message = self._text("exceptions.notify_cleaning_paused.message", "Cleaning paused")
                self._create("information", message, "cleaning_paused")
            else:
                self._dismiss("cleaning_paused")

    def _handle_consumable(self, prop: str, values: dict[str, Any]) -> None:
        name = CONSUMABLE_LIFE[prop]
        threshold = int(self.entry.options.get(CONF_CONSUMABLE_THRESHOLD, DEFAULT_CONSUMABLE_THRESHOLD))
        current = values.get(prop)
        previous = self._prev.get(prop)
        if not isinstance(current, (int, float)):
            return
        nid = f"consumable_{name}"
        if current <= threshold:
            crossed = previous is None or not isinstance(previous, (int, float)) or previous > threshold
            if crossed or current != previous:
                if crossed:
                    self._fire(
                        EVENT_CONSUMABLE,
                        {"consumable": name, "life_left": int(current)},
                    )
                label = self._text(f"entity.sensor.{prop}.name", name.replace("_", " ").capitalize())
                message = self._text(
                    "exceptions.notify_consumable.message", "{name}: {life}% left", name=label, life=int(current)
                )
                self._create("consumable", message, nid)
        else:
            self._dismiss(nid)
