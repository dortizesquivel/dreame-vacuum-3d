"""Dreame Vacuum 3D: a Dreame robot vacuum integration with a 3D map card."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from aiohttp import CookieJar
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    CONF_COUNTRY,
    CONF_HOST,
    CONF_MAC,
    CONF_MODEL,
    CONF_PASSWORD,
    CONF_TOKEN,
    CONF_USERNAME,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.helpers.aiohttp_client import async_create_clientsession

from .const import CONF_AUTH, CONF_DID, DOMAIN
from .coordinator import DreameDataUpdateCoordinator
from .device.capabilities import load
from .device.device import DreameDevice
from .notifications import DreameNotifier
from .protocol.cloud import (
    CloudAuth,
    CloudAuthError,
    CloudConnectionError,
    CloudLoginRequired,
    XiaomiCloud,
)
from .protocol.miio import MiioClient
from .protocol.miio_codec import MiioError
from .protocol.miot import Send

PLATFORMS: list[str] = [
    "vacuum",
    "sensor",
    "binary_sensor",
    "select",
    "switch",
    "number",
    "button",
    "time",
]


@dataclass
class RuntimeData:
    """What one config entry keeps while it is loaded."""

    coordinator: DreameDataUpdateCoordinator
    cloud: XiaomiCloud
    miio: MiioClient
    notifier: DreameNotifier


type DreameConfigEntry = ConfigEntry[RuntimeData]


async def async_setup_entry(hass: HomeAssistant, entry: DreameConfigEntry) -> bool:
    """Set up a vacuum from a config entry."""
    session = async_create_clientsession(hass, cookie_jar=CookieJar(quote_cookie=False))
    cloud = XiaomiCloud(
        session,
        entry.data[CONF_COUNTRY],
        CloudAuth.from_dict(entry.data[CONF_AUTH]),
    )
    try:
        await cloud.login(entry.data[CONF_USERNAME], entry.data[CONF_PASSWORD])
    except (CloudAuthError, CloudLoginRequired) as err:
        raise ConfigEntryAuthFailed(str(err)) from err
    except CloudConnectionError as err:
        raise ConfigEntryNotReady(str(err)) from err
    _persist_auth(hass, entry, cloud)

    miio = MiioClient(entry.data[CONF_HOST], entry.data[CONF_TOKEN])
    did = str(entry.data[CONF_DID])
    caps = await hass.async_add_executor_job(load, entry.data[CONF_MODEL])
    device = DreameDevice(caps, miio.send, _cloud_sender(hass, entry, cloud, did))

    try:
        await device.async_discover()
        info = await _miio_info(miio, cloud, did)
    except CloudAuthError as err:
        await miio.close()
        raise ConfigEntryAuthFailed(str(err)) from err
    except (MiioError, CloudConnectionError) as err:
        await miio.close()
        raise ConfigEntryNotReady(str(err)) from err

    coordinator = DreameDataUpdateCoordinator(hass, entry, device)
    try:
        await coordinator.async_config_entry_first_refresh()
    except Exception:
        await miio.close()
        raise

    notifier = DreameNotifier(hass, entry, coordinator)
    entry.runtime_data = RuntimeData(
        coordinator=coordinator, cloud=cloud, miio=miio, notifier=notifier
    )
    _async_register_device(hass, entry, info)
    _async_remove_dnd_numbers(hass, did)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    # Listen after entities exist so events can resolve entity_id / device_id.
    await notifier.async_setup()
    return True


async def async_unload_entry(hass: HomeAssistant, entry: DreameConfigEntry) -> bool:
    """Unload a config entry and close the local UDP client."""
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        await entry.runtime_data.miio.close()
    return unload_ok


def _persist_auth(hass: HomeAssistant, entry: ConfigEntry, cloud: XiaomiCloud) -> None:
    auth = cloud.auth
    if auth is None:
        return
    payload = asdict(auth)
    if payload != entry.data.get(CONF_AUTH):
        hass.config_entries.async_update_entry(entry, data={**entry.data, CONF_AUTH: payload})


def _cloud_sender(hass: HomeAssistant, entry: ConfigEntry, cloud: XiaomiCloud, did: str) -> Send:
    """Cloud RPC that logs in again with the saved password when the session expires.

    Only a login that needs a person (captcha, two-factor) ends in a reauth.
    """

    async def send(method: str, params: Any) -> Any:
        try:
            return await cloud.rpc(did, method, params)
        except CloudAuthError:
            try:
                await cloud.login(entry.data[CONF_USERNAME], entry.data[CONF_PASSWORD])
            except CloudLoginRequired as err:
                raise CloudAuthError("invalid_auth") from err
            _persist_auth(hass, entry, cloud)
            return await cloud.rpc(did, method, params)

    return send


def _async_remove_dnd_numbers(hass: HomeAssistant, did: str) -> None:
    """0.1.0 had the do-not-disturb window as numbers of minutes; they are time entities now."""
    registry = er.async_get(hass)
    for key in ("dnd_start", "dnd_end"):
        if entity_id := registry.async_get_entity_id("number", DOMAIN, f"{did}_{key}"):
            registry.async_remove(entity_id)


async def _miio_info(miio: MiioClient, cloud: XiaomiCloud, did: str) -> dict[str, Any]:
    """Read `miIO.info` locally, falling back to the cloud."""
    try:
        result = await miio.send("miIO.info")
    except MiioError:
        result = await cloud.rpc(did, "miIO.info", [])
    return result if isinstance(result, dict) else {}


def _async_register_device(
    hass: HomeAssistant, entry: ConfigEntry, info: dict[str, Any]
) -> None:
    mac = entry.data.get(CONF_MAC) or info.get("mac") or ""
    connections = {(dr.CONNECTION_NETWORK_MAC, dr.format_mac(mac))} if mac else set()
    dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, str(entry.data[CONF_DID]))},
        connections=connections,
        manufacturer="Dreame",
        name=entry.title,
        model=entry.data.get(CONF_MODEL) or info.get("model"),
        sw_version=info.get("fw_ver"),
    )
