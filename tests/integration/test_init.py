"""Config entry setup: device, coordinator, registry, unload and NotReady."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.config_entries import SOURCE_REAUTH, ConfigEntryState
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
from homeassistant.helpers import device_registry as dr, entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.dreame_vacuum_3d.const import CONF_AUTH, CONF_DID, DOMAIN
from custom_components.dreame_vacuum_3d.protocol.cloud import CloudAuth, CloudAuthError, CloudLoginRequired
from custom_components.dreame_vacuum_3d.protocol.miio_codec import MiioError

USER = "user@example.com"
PASSWORD = "secret"
COUNTRY = "de"
AUTH = CloudAuth(
    user_id="1000001",
    ssecurity="dGVzdC1zc2VjdXJpdHkxNg==",
    service_token="c2VydmljZS90b2tlbg==",
    client_id="abcdefghijklmnop",
    pass_token="cGFzcw==",
)
AUTH_RENEWED = CloudAuth(
    user_id="1000001",
    ssecurity="bmV3LXNzZWN1cml0eS16eQ==",
    service_token="bmV3LXNlcnZpY2UvdG9rZW4=",
    client_id="abcdefghijklmnop",
    pass_token="cGFzcw==",
)
ENTRY_DATA = {
    CONF_COUNTRY: COUNTRY,
    CONF_USERNAME: USER,
    CONF_PASSWORD: PASSWORD,
    CONF_AUTH: asdict(AUTH),
    CONF_DID: "1001",
    CONF_MODEL: "dreame.vacuum.p2114a",
    CONF_HOST: "192.168.1.50",
    CONF_TOKEN: "00112233445566778899aabbccddeeff",
    CONF_MAC: "AA:BB:CC:00:00:01",
}
INFO = {
    "model": "dreame.vacuum.p2114a",
    "fw_ver": "4.3.9_1616",
    "mac": "AA:BB:CC:00:00:01",
}


def _property_reply(params: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "did": item["did"],
            "siid": item["siid"],
            "piid": item["piid"],
            "code": 0,
            "value": 1 if item["piid"] == 1 else 0,
        }
        for item in params
    ]


async def _send(method: str, params: Any = None) -> Any:
    if method == "miIO.info":
        return INFO
    if method == "get_properties":
        return _property_reply(params or [])
    if method == "set_properties":
        return [{"did": p["did"], "siid": p["siid"], "piid": p["piid"], "code": 0} for p in (params or [])]
    if method == "action":
        return {"code": 0, "out": []}
    raise AssertionError(f"unexpected method {method}")


def _mock_miio() -> MagicMock:
    miio = MagicMock()
    miio.send = AsyncMock(side_effect=_send)
    miio.close = AsyncMock()
    return miio


def _mock_cloud(*, auth: CloudAuth = AUTH) -> MagicMock:
    async def _rpc(_did: str, method: str, params: Any = None) -> Any:
        return await _send(method, params)

    cloud = MagicMock()
    cloud.auth = auth
    cloud.login = AsyncMock(return_value=auth)
    cloud.rpc = AsyncMock(side_effect=_rpc)
    return cloud


def _entry() -> MockConfigEntry:
    return MockConfigEntry(domain=DOMAIN, data=ENTRY_DATA, unique_id=ENTRY_DATA[CONF_DID], title="X10+")


async def test_setup_without_a_token_uses_the_cloud(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(domain=DOMAIN, data={**ENTRY_DATA, CONF_HOST: "", CONF_TOKEN: ""}, unique_id="1001")
    entry.add_to_hass(hass)
    cloud = _mock_cloud()

    with patch("custom_components.dreame_vacuum_3d.XiaomiCloud", return_value=cloud):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.LOADED
    assert entry.runtime_data.coordinator.device.channel == "cloud"
    assert entry.runtime_data.coordinator.device.available
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_setup_and_unload(hass: HomeAssistant) -> None:
    entry = _entry()
    entry.add_to_hass(hass)
    miio = _mock_miio()
    cloud = _mock_cloud()

    with (
        patch(
            "custom_components.dreame_vacuum_3d.XiaomiCloud",
            return_value=cloud,
        ),
        patch(
            "custom_components.dreame_vacuum_3d.MiioClient",
            return_value=miio,
        ),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.LOADED
    assert entry.runtime_data.coordinator.device.available
    assert entry.runtime_data.miio is miio

    registry = dr.async_get(hass)
    device = registry.async_get_device_by_identifier((DOMAIN, ENTRY_DATA[CONF_DID]), entry.entry_id)
    assert device is not None
    assert device.model == ENTRY_DATA[CONF_MODEL]
    assert device.sw_version == INFO["fw_ver"]
    assert (dr.CONNECTION_NETWORK_MAC, dr.format_mac(ENTRY_DATA[CONF_MAC])) in device.connections

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.NOT_LOADED
    miio.close.assert_awaited_once()


async def test_setup_persists_renewed_auth(hass: HomeAssistant) -> None:
    entry = _entry()
    entry.add_to_hass(hass)
    cloud = _mock_cloud(auth=AUTH_RENEWED)
    cloud.login = AsyncMock(return_value=AUTH_RENEWED)

    with (
        patch("custom_components.dreame_vacuum_3d.XiaomiCloud", return_value=cloud),
        patch("custom_components.dreame_vacuum_3d.MiioClient", return_value=_mock_miio()),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert entry.data[CONF_AUTH] == asdict(AUTH_RENEWED)


async def test_setup_raises_not_ready_when_robot_silent(hass: HomeAssistant) -> None:
    entry = _entry()
    entry.add_to_hass(hass)

    async def _fail(_method: str, _params: Any = None) -> Any:
        raise MiioError("no reply")

    miio = _mock_miio()
    miio.send = AsyncMock(side_effect=_fail)
    cloud = _mock_cloud()
    cloud.rpc = AsyncMock(side_effect=MiioError("no reply"))

    with (
        patch("custom_components.dreame_vacuum_3d.XiaomiCloud", return_value=cloud),
        patch("custom_components.dreame_vacuum_3d.MiioClient", return_value=miio),
    ):
        assert not await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.SETUP_RETRY


async def test_setup_auth_error_on_login(hass: HomeAssistant) -> None:
    entry = _entry()
    entry.add_to_hass(hass)
    cloud = _mock_cloud()
    cloud.login = AsyncMock(side_effect=CloudAuthError("invalid_auth"))

    with (
        patch("custom_components.dreame_vacuum_3d.XiaomiCloud", return_value=cloud),
        patch("custom_components.dreame_vacuum_3d.MiioClient", return_value=_mock_miio()),
    ):
        assert not await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.SETUP_ERROR


async def test_miio_info_falls_back_to_cloud(hass: HomeAssistant) -> None:
    entry = _entry()
    entry.add_to_hass(hass)
    miio = _mock_miio()

    async def _local(method: str, params: Any = None) -> Any:
        if method == "miIO.info":
            raise MiioError("no reply")
        return await _send(method, params)

    miio.send = AsyncMock(side_effect=_local)
    cloud = _mock_cloud()

    with (
        patch("custom_components.dreame_vacuum_3d.XiaomiCloud", return_value=cloud),
        patch("custom_components.dreame_vacuum_3d.MiioClient", return_value=miio),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    cloud.rpc.assert_any_await(ENTRY_DATA[CONF_DID], "miIO.info", [])
    device = dr.async_get(hass).async_get_device_by_identifier((DOMAIN, ENTRY_DATA[CONF_DID]), entry.entry_id)
    assert device is not None
    assert device.sw_version == INFO["fw_ver"]


async def _setup_on_cloud(hass: HomeAssistant) -> tuple[MockConfigEntry, MagicMock, MagicMock]:
    """Set up normally, then lose the local channel so every call goes through the cloud."""
    entry = _entry()
    entry.add_to_hass(hass)
    miio = _mock_miio()
    cloud = _mock_cloud()
    with (
        patch("custom_components.dreame_vacuum_3d.XiaomiCloud", return_value=cloud),
        patch("custom_components.dreame_vacuum_3d.MiioClient", return_value=miio),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    miio.send.side_effect = MiioError("no reply")
    return entry, miio, cloud


async def test_expired_cloud_session_logs_in_again(hass: HomeAssistant) -> None:
    entry, _miio, cloud = await _setup_on_cloud(hass)
    expired = True

    async def _rpc(_did: str, method: str, params: Any = None) -> Any:
        if expired:
            raise CloudAuthError("invalid_auth")
        return await _send(method, params)

    async def _login(*_args: Any, **_kwargs: Any) -> CloudAuth:
        nonlocal expired
        expired = False
        cloud.auth = AUTH_RENEWED
        return AUTH_RENEWED

    cloud.rpc.side_effect = _rpc
    cloud.login.reset_mock()
    cloud.login.side_effect = _login

    coordinator = entry.runtime_data.coordinator
    await coordinator.async_refresh()

    assert coordinator.last_update_success is True
    cloud.login.assert_awaited_once_with(USER, PASSWORD)
    assert entry.data[CONF_AUTH] == asdict(AUTH_RENEWED)
    assert not hass.config_entries.flow.async_progress_by_handler(DOMAIN, match_context={"source": SOURCE_REAUTH})


async def test_login_that_needs_a_person_asks_for_reauth(hass: HomeAssistant) -> None:
    entry, _miio, cloud = await _setup_on_cloud(hass)
    cloud.rpc.side_effect = CloudAuthError("invalid_auth")
    cloud.login.side_effect = CloudLoginRequired(verification_url="https://account.xiaomi.com/verify")

    await entry.runtime_data.coordinator.async_refresh()
    await hass.async_block_till_done()

    assert hass.config_entries.flow.async_progress_by_handler(DOMAIN, match_context={"source": SOURCE_REAUTH})


async def test_setup_removes_the_old_dnd_numbers(hass: HomeAssistant) -> None:
    registry = er.async_get(hass)
    entry = _entry()
    entry.add_to_hass(hass)
    old = registry.async_get_or_create("number", DOMAIN, f"{ENTRY_DATA[CONF_DID]}_dnd_start", config_entry=entry)
    with (
        patch("custom_components.dreame_vacuum_3d.XiaomiCloud", return_value=_mock_cloud()),
        patch("custom_components.dreame_vacuum_3d.MiioClient", return_value=_mock_miio()),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    assert registry.async_get(old.entity_id) is None


async def test_failed_first_refresh_closes_the_udp_client(hass: HomeAssistant) -> None:
    entry = _entry()
    entry.add_to_hass(hass)
    discovered = False

    async def _robot(method: str, params: Any = None) -> Any:
        nonlocal discovered
        if method == "miIO.info":
            discovered = True
        elif discovered:
            raise MiioError("no reply")
        return await _send(method, params)

    async def _rpc(_did: str, method: str, params: Any = None) -> Any:
        return await _robot(method, params)

    miio = _mock_miio()
    miio.send.side_effect = _robot
    cloud = _mock_cloud()
    cloud.rpc.side_effect = _rpc

    with (
        patch("custom_components.dreame_vacuum_3d.XiaomiCloud", return_value=cloud),
        patch("custom_components.dreame_vacuum_3d.MiioClient", return_value=miio),
    ):
        assert not await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.SETUP_RETRY
    miio.close.assert_awaited_once()
