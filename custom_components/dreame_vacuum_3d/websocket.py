"""WebSocket API for the 3D card: subscribe to a vacuum's map."""

from __future__ import annotations

from typing import Any

from homeassistant.auth.permissions.const import POLICY_READ
from homeassistant.components import websocket_api
from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import Unauthorized
from homeassistant.helpers import entity_registry as er
import voluptuous as vol

from .const import DOMAIN
from .device.map import MapError
from .live_map import MAP_LISTENERS, MAP_VIEWERS
from .protocol.cloud import CloudError


@callback
def async_register_websocket(hass: HomeAssistant) -> None:
    websocket_api.async_register_command(hass, ws_subscribe_map)
    websocket_api.async_register_command(hass, ws_history)
    websocket_api.async_register_command(hass, ws_history_path)


def _entry(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> ConfigEntry | None:
    """The config entry of the vacuum the message names, if the user may read it; else the error is sent."""
    entity_id = msg["entity_id"]
    if not connection.user.permissions.check_entity(entity_id, POLICY_READ):
        raise Unauthorized(entity_id=entity_id)
    reg = er.async_get(hass).async_get(entity_id)
    entry = hass.config_entries.async_get_entry(reg.config_entry_id) if reg and reg.config_entry_id else None
    if entry is None or entry.domain != DOMAIN or reg is None or reg.domain != "vacuum":
        connection.send_error(msg["id"], websocket_api.ERR_NOT_FOUND, f"{entity_id} isn't a Dreame Vacuum 3D vacuum")
        return None
    return entry


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/map/subscribe",
        vol.Required("entity_id"): str,
    }
)
@callback
def ws_subscribe_map(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Send the vacuum's map now, then the rooms when they change and the robot's pose with each frame."""
    if (entry := _entry(hass, connection, msg)) is None:
        return

    @callback
    def forward(message: dict[str, Any]) -> None:
        connection.send_message(websocket_api.event_message(msg["id"], message))

    # The listener list outlives a reload of the entry, so the card keeps its subscription.
    listeners = hass.data[MAP_LISTENERS].setdefault(entry.entry_id, [])
    listeners.append(forward)
    viewers = hass.data[MAP_VIEWERS]
    viewers[entry.entry_id] = viewers.get(entry.entry_id, 0) + 1

    @callback
    def unsubscribe() -> None:
        listeners.remove(forward)
        viewers[entry.entry_id] -= 1

    connection.subscriptions[msg["id"]] = unsubscribe
    connection.send_result(msg["id"])
    if entry.state is ConfigEntryState.LOADED and (first := entry.runtime_data.map.message) is not None:
        forward(first)


@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/history", vol.Required("entity_id"): str})
@callback
def ws_history(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    """The robot's last cleanings, newest first: when, how long, how much, which rooms, and whether it has a map."""
    if (entry := _entry(hass, connection, msg)) is None:
        return
    cleanings = entry.runtime_data.history.cleanings if entry.state is ConfigEntryState.LOADED else []
    keys = ("start", "duration", "area", "completed", "rooms", "map_id")
    # Not the log file's name: it holds the account's and the robot's ids.
    connection.send_result(
        msg["id"], {"cleanings": [{**{k: c[k] for k in keys}, "has_map": bool(c["file"])} for c in cleanings]}
    )


@websocket_api.websocket_command(
    {vol.Required("type"): f"{DOMAIN}/history/path", vol.Required("entity_id"): str, vol.Required("start"): int}
)
@websocket_api.async_response
async def ws_history_path(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    """One past cleaning's path (and its map and rooms), to replay it."""
    if (entry := _entry(hass, connection, msg)) is None:
        return
    if entry.state is not ConfigEntryState.LOADED:
        connection.send_error(msg["id"], websocket_api.ERR_NOT_FOUND, "The integration isn't loaded")
        return
    try:
        found = await entry.runtime_data.history.path(msg["start"])
    except (CloudError, MapError) as err:
        connection.send_error(
            msg["id"], websocket_api.ERR_HOME_ASSISTANT_ERROR, f"Couldn't get that cleaning's map: {err}"
        )
        return
    if found is None:
        connection.send_error(msg["id"], websocket_api.ERR_NOT_FOUND, "No map for that cleaning")
        return
    connection.send_result(msg["id"], found)
