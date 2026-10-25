"""Events and persistent notifications from property sequences."""

from __future__ import annotations

from homeassistant.components import persistent_notification as pn
from homeassistant.const import ATTR_DEVICE_ID, ATTR_ENTITY_ID
from homeassistant.core import Event, HomeAssistant
from homeassistant.helpers import entity_registry as er

from custom_components.dreame_vacuum_3d.const import (
    CONF_NOTIFY,
    DOMAIN,
    EVENT_CONSUMABLE,
    EVENT_ERROR,
    EVENT_INFORMATION,
    EVENT_TASK_STATUS,
)
from custom_components.dreame_vacuum_3d.device.enums import ErrorCode, TaskStatus
from tests.integration.helpers import setup_fixture_entry

# Fixture property ids (siid, piid).
_TASK = (4, 7)
_ERROR = (2, 2)
_FILTER = (11, 1)
_WARN = (4, 35)
_AREA = (4, 3)
_TIME = (4, 2)


def _vacuum_entity_id(hass: HomeAssistant) -> str:
    for entry in er.async_get(hass).entities.values():
        if entry.domain == "vacuum" and entry.platform == DOMAIN:
            return entry.entity_id
    raise AssertionError("vacuum entity not registered")


def _notifications(hass: HomeAssistant) -> dict[str, dict]:
    return dict(hass.data.get(pn.DOMAIN) or {})


async def test_task_status_completed_and_cancelled(hass: HomeAssistant) -> None:
    entry, miio = await setup_fixture_entry(hass)
    entity_id = _vacuum_entity_id(hass)
    events: list[Event] = []
    hass.bus.async_listen(EVENT_TASK_STATUS, events.append)

    # Fixture task_status=0 (COMPLETED); enter an active task, then finish it.
    miio.overrides[_TASK] = TaskStatus.SEGMENT_CLEANING.value
    await entry.runtime_data.coordinator.async_refresh()
    await hass.async_block_till_done()
    assert events == []

    miio.overrides[_AREA] = 12
    miio.overrides[_TIME] = 8
    miio.overrides[_TASK] = TaskStatus.COMPLETED.value
    await entry.runtime_data.coordinator.async_refresh()
    await hass.async_block_till_done()

    assert len(events) == 1
    data = events[0].data
    assert data[ATTR_ENTITY_ID] == entity_id
    assert data[ATTR_DEVICE_ID]
    assert data["completed"] is True
    assert data["area"] == 12
    assert data["duration"] == 8
    assert any("cleanup_completed" in nid for nid in _notifications(hass))

    # Start a new segment clean, then leave without COMPLETED → cancelled.
    events.clear()
    miio.overrides[_TASK] = TaskStatus.SEGMENT_CLEANING.value
    await entry.runtime_data.coordinator.async_refresh()
    await hass.async_block_till_done()
    assert events == []

    miio.overrides[_TASK] = TaskStatus.DOCKING_PAUSED.value
    await entry.runtime_data.coordinator.async_refresh()
    await hass.async_block_till_done()
    assert len(events) == 1
    assert events[0].data["completed"] is False


async def test_consumable_threshold_cross(hass: HomeAssistant) -> None:
    entry, miio = await setup_fixture_entry(hass)
    entity_id = _vacuum_entity_id(hass)
    events: list[Event] = []
    hass.bus.async_listen(EVENT_CONSUMABLE, events.append)

    # Fixture filter_left=10 already below default 15; raise then drop to cross.
    miio.overrides[_FILTER] = 40
    await entry.runtime_data.coordinator.async_refresh()
    await hass.async_block_till_done()
    assert events == []

    miio.overrides[_FILTER] = 10
    await entry.runtime_data.coordinator.async_refresh()
    await hass.async_block_till_done()

    assert len(events) == 1
    assert events[0].data == {
        ATTR_ENTITY_ID: entity_id,
        ATTR_DEVICE_ID: events[0].data[ATTR_DEVICE_ID],
        "consumable": "filter",
        "life_left": 10,
    }
    nids = _notifications(hass)
    assert any(nid.endswith("consumable_filter") for nid in nids)

    # Recover above threshold → dismiss.
    miio.overrides[_FILTER] = 50
    await entry.runtime_data.coordinator.async_refresh()
    await hass.async_block_till_done()
    assert not any(nid.endswith("consumable_filter") for nid in _notifications(hass))


async def test_error_and_information_events(hass: HomeAssistant) -> None:
    entry, miio = await setup_fixture_entry(hass)
    entity_id = _vacuum_entity_id(hass)
    errors: list[Event] = []
    infos: list[Event] = []
    hass.bus.async_listen(EVENT_ERROR, errors.append)
    hass.bus.async_listen(EVENT_INFORMATION, infos.append)

    miio.overrides[_ERROR] = ErrorCode.DROP.value
    await entry.runtime_data.coordinator.async_refresh()
    await hass.async_block_till_done()

    assert len(errors) == 1
    assert errors[0].data["code"] == ErrorCode.DROP.value
    assert errors[0].data["error"] == "drop"
    assert errors[0].data[ATTR_ENTITY_ID] == entity_id
    assert any(nid.endswith("_error") for nid in _notifications(hass))

    miio.overrides[_ERROR] = ErrorCode.NO_ERROR.value
    await entry.runtime_data.coordinator.async_refresh()
    await hass.async_block_till_done()
    assert not any(nid.endswith("_error") for nid in _notifications(hass))

    miio.overrides[_WARN] = 2
    await entry.runtime_data.coordinator.async_refresh()
    await hass.async_block_till_done()
    assert len(infos) == 1
    assert infos[0].data["information"] == "warn_status"
    assert infos[0].data["code"] == 2
    assert any(nid.endswith("warn_status") for nid in _notifications(hass))


async def test_notify_options_gate_persistent_notifications(hass: HomeAssistant) -> None:
    entry, miio = await setup_fixture_entry(hass)
    hass.config_entries.async_update_entry(entry, options={**entry.options, CONF_NOTIFY: ["error"]})
    await hass.async_block_till_done()

    events: list[Event] = []
    hass.bus.async_listen(EVENT_TASK_STATUS, events.append)

    miio.overrides[_TASK] = TaskStatus.SEGMENT_CLEANING.value
    await entry.runtime_data.coordinator.async_refresh()
    await hass.async_block_till_done()
    events.clear()

    miio.overrides[_TASK] = TaskStatus.COMPLETED.value
    await entry.runtime_data.coordinator.async_refresh()
    await hass.async_block_till_done()

    # Event still fires; cleanup notification skipped (category not enabled).
    assert len(events) == 1
    assert not any("cleanup_completed" in nid for nid in _notifications(hass))


_LOW_WATER = (4, 41)
_PAUSED = (4, 17)


async def test_low_water_and_cleaning_paused_information(hass: HomeAssistant) -> None:
    entry, miio = await setup_fixture_entry(hass)
    infos: list[Event] = []
    hass.bus.async_listen(EVENT_INFORMATION, infos.append)

    miio.overrides[_LOW_WATER] = 1
    await entry.runtime_data.coordinator.async_refresh()
    await hass.async_block_till_done()
    assert any(e.data.get("information") == "low_water_warning" for e in infos)
    assert any(nid.endswith("low_water") for nid in _notifications(hass))

    miio.overrides[_LOW_WATER] = 0
    await entry.runtime_data.coordinator.async_refresh()
    await hass.async_block_till_done()
    assert not any(nid.endswith("low_water") for nid in _notifications(hass))

    infos.clear()
    miio.overrides[_PAUSED] = 1
    await entry.runtime_data.coordinator.async_refresh()
    await hass.async_block_till_done()
    assert any(e.data.get("information") == "cleaning_paused" for e in infos)
    assert any(nid.endswith("cleaning_paused") for nid in _notifications(hass))

    miio.overrides[_PAUSED] = 0
    await entry.runtime_data.coordinator.async_refresh()
    await hass.async_block_till_done()
    assert not any(nid.endswith("cleaning_paused") for nid in _notifications(hass))


async def test_notifications_speak_home_assistants_language(hass: HomeAssistant) -> None:
    hass.config.language = "es"
    entry, miio = await setup_fixture_entry(hass)
    events: list[Event] = []
    hass.bus.async_listen(EVENT_TASK_STATUS, events.append)

    # A room clean started here reports its rooms when it ends.
    await hass.services.async_call(
        DOMAIN, "vacuum_clean_segment", {ATTR_ENTITY_ID: _vacuum_entity_id(hass), "segments": [6, 3]}, blocking=True
    )
    miio.overrides[_TASK] = TaskStatus.SEGMENT_CLEANING.value
    miio.overrides[_FILTER] = 40
    await entry.runtime_data.coordinator.async_refresh()
    miio.overrides[_TASK] = TaskStatus.COMPLETED.value
    miio.overrides[_FILTER] = 10
    miio.overrides[_LOW_WATER] = 5
    await entry.runtime_data.coordinator.async_refresh()
    await hass.async_block_till_done()

    assert events[0].data["segments"] == [6, 3]
    assert entry.runtime_data.coordinator.device.segments is None
    messages = {nid.rsplit("_", 1)[-1]: n["message"] for nid, n in _notifications(hass).items()}
    assert messages["completed"] == "Limpieza completada"
    assert messages["filter"] == "Filtro: queda un 10 %"
    assert messages["water"] == "Poca agua"
