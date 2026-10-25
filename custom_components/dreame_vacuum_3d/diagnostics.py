"""Diagnostics dump for supporting new models (redacted entry + raw MIoT props)."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import REDACTED, async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    CONF_HOST,
    CONF_MAC,
    CONF_PASSWORD,
    CONF_TOKEN,
    CONF_USERNAME,
)
from homeassistant.core import HomeAssistant

from .const import CONF_AUTH, CONF_DID

# Keys stripped from the config entry (and nested auth) before export.
TO_REDACT = {
    CONF_PASSWORD,
    CONF_AUTH,
    CONF_TOKEN,
    CONF_DID,
    CONF_MAC,
    CONF_HOST,
    CONF_USERNAME,
    "unique_id",  # config entry unique_id is the cloud did
    "ssecurity",
    "service_token",
    "pass_token",
    "user_id",
    "client_id",
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return redacted entry data and raw MIoT properties (no serial_number)."""
    device = entry.runtime_data.coordinator.device
    # Map object names (6.8, 6.9) carry "<user id>/<did>/..." inside the value.
    private = [str(v) for v in (entry.data.get(CONF_DID), (entry.data.get(CONF_AUTH) or {}).get("user_id")) if v]
    properties: dict[str, Any] = {}
    for key, value in sorted(device.values.items()):
        if key == "serial_number":
            continue
        prop = device.caps.properties.get(key)
        if prop is None:
            continue
        siid, piid = prop
        if isinstance(value, str):
            for secret in private:
                value = value.replace(secret, REDACTED)
        properties[f"{siid}.{piid}"] = value

    return {
        "entry": async_redact_data(entry.as_dict(), TO_REDACT),
        "channel": device.channel,
        "model": device.caps.model,
        "tested": device.caps.tested,
        "available": sorted(k for k in device.available if k != "serial_number"),
        "properties": properties,
    }
