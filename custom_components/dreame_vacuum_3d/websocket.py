"""WebSocket API for the 3D card: subscribe to a vacuum's map."""

from __future__ import annotations

from typing import Any

from homeassistant.auth.permissions.const import POLICY_READ
from homeassistant.components import websocket_api
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import Unauthorized
from homeassistant.helpers import entity_registry as er
import voluptuous as vol

from .const import DOMAIN
from .live_map import MAP_LISTENERS


@callback
def async_register_websocket(hass: HomeAssistant) -> None:
    websocket_api.async_register_command(hass, ws_subscribe_map)


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
    entity_id = msg["entity_id"]
    if not connection.user.permissions.check_entity(entity_id, POLICY_READ):
        raise Unauthorized(entity_id=entity_id)
    reg = er.async_get(hass).async_get(entity_id)
    entry = hass.config_entries.async_get_entry(reg.config_entry_id) if reg and reg.config_entry_id else None
    if entry is None or entry.domain != DOMAIN or reg is None or reg.domain != "vacuum":
        connection.send_error(msg["id"], websocket_api.ERR_NOT_FOUND, f"{entity_id} isn't a Dreame Vacuum 3D vacuum")
        return

    @callback
    def forward(message: dict[str, Any]) -> None:
        connection.send_message(websocket_api.event_message(msg["id"], message))

    # The listener list outlives a reload of the entry, so the card keeps its subscription.
    listeners = hass.data[MAP_LISTENERS].setdefault(entry.entry_id, [])
    listeners.append(forward)
    connection.subscriptions[msg["id"]] = lambda: listeners.remove(forward)
    connection.send_result(msg["id"])
    if entry.state is ConfigEntryState.LOADED and (first := entry.runtime_data.map.message) is not None:
        forward(first)
