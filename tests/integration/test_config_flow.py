"""Config flow: Xiaomi login (captcha / 2FA), device pick, reauth and options."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant import config_entries
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
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.dreame_vacuum_3d.const import (
    CONF_AUTH,
    CONF_COEXIST,
    CONF_CONSUMABLE_THRESHOLD,
    CONF_DID,
    CONF_NOTIFY,
    DOMAIN,
)
from custom_components.dreame_vacuum_3d.protocol.cloud import (
    CloudAuth,
    CloudAuthError,
    CloudLoginRequired,
)

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
VACUUM = {
    "did": "1001",
    "name": "X10+",
    "model": "dreame.vacuum.p2114a",
    "localip": "192.168.1.50",
    "token": "00112233445566778899aabbccddeeff",
    "mac": "AA:BB:CC:00:00:01",
}
OTHER_VACUUM = {
    "did": "2001",
    "name": "Other",
    "model": "xiaomi.vacuum.d109gl",
    "localip": "192.168.1.60",
    "token": "ffeeddccbbaa99887766554433221100",
    "mac": "AA:BB:CC:00:00:03",
}
ENTRY_DATA = {
    CONF_COUNTRY: COUNTRY,
    CONF_USERNAME: USER,
    CONF_PASSWORD: PASSWORD,
    CONF_AUTH: asdict(AUTH),
    CONF_DID: VACUUM["did"],
    CONF_MODEL: VACUUM["model"],
    CONF_HOST: VACUUM["localip"],
    CONF_TOKEN: VACUUM["token"],
    CONF_MAC: VACUUM["mac"],
}


def _cloud(*, devices: list[dict[str, Any]] | None = None, auth: CloudAuth = AUTH) -> MagicMock:
    cloud = MagicMock()
    cloud.auth = auth
    cloud.login = AsyncMock(return_value=auth)
    cloud.send_2fa_code = AsyncMock(return_value="u***@example.com")
    cloud.verify_2fa_code = AsyncMock(return_value=auth)
    cloud.devices = AsyncMock(return_value=devices if devices is not None else [VACUUM])
    return cloud


async def _init(hass: HomeAssistant):
    return await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})


async def test_happy_path_creates_entry(hass: HomeAssistant) -> None:
    cloud = _cloud()
    with patch("custom_components.dreame_vacuum_3d.config_flow.XiaomiCloud", return_value=cloud):
        result = await _init(hass)
        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == "user"

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_USERNAME: USER, CONF_PASSWORD: PASSWORD, CONF_COUNTRY: COUNTRY},
        )
        assert result["type"] is FlowResultType.CREATE_ENTRY
        assert result["title"] == "X10+"
        assert result["result"].unique_id == "1001"
        assert result["data"] == ENTRY_DATA
        cloud.login.assert_awaited_once_with(USER, PASSWORD, captcha=None)


async def test_captcha_then_device(hass: HomeAssistant) -> None:
    cloud = _cloud()
    png = b"\x89PNG\r\n\x1a\n"
    cloud.login = AsyncMock(
        side_effect=[CloudLoginRequired(captcha_png=png), AUTH],
    )
    with patch("custom_components.dreame_vacuum_3d.config_flow.XiaomiCloud", return_value=cloud):
        result = await _init(hass)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_USERNAME: USER, CONF_PASSWORD: PASSWORD, CONF_COUNTRY: COUNTRY},
        )
        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == "captcha"
        assert "![captcha](data:image/png;base64," in result["description_placeholders"]["captcha"]

        result = await hass.config_entries.flow.async_configure(result["flow_id"], {"captcha": "AB12"})
        assert result["type"] is FlowResultType.CREATE_ENTRY
        assert result["data"][CONF_DID] == "1001"
        assert cloud.login.await_args_list[1].kwargs["captcha"] == "AB12"


async def test_2fa_then_device(hass: HomeAssistant) -> None:
    cloud = _cloud()
    url = "https://account.xiaomi.com/fe/service/identity/authStart?context=abc"
    cloud.login = AsyncMock(side_effect=CloudLoginRequired(verification_url=url))
    with patch("custom_components.dreame_vacuum_3d.config_flow.XiaomiCloud", return_value=cloud):
        result = await _init(hass)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_USERNAME: USER, CONF_PASSWORD: PASSWORD, CONF_COUNTRY: COUNTRY},
        )
        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == "2fa"
        assert result["description_placeholders"]["dest"] == "u***@example.com"
        cloud.send_2fa_code.assert_awaited_once_with(url)

        result = await hass.config_entries.flow.async_configure(result["flow_id"], {"code": "123456"})
        assert result["type"] is FlowResultType.CREATE_ENTRY
        cloud.verify_2fa_code.assert_awaited_once_with("123456")


async def test_bad_password(hass: HomeAssistant) -> None:
    cloud = _cloud()
    cloud.login = AsyncMock(side_effect=CloudAuthError("invalid_auth"))
    with patch("custom_components.dreame_vacuum_3d.config_flow.XiaomiCloud", return_value=cloud):
        result = await _init(hass)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_USERNAME: USER, CONF_PASSWORD: "wrong", CONF_COUNTRY: COUNTRY},
        )
        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == "user"
        assert result["errors"] == {"base": "invalid_auth"}


async def test_no_robots_shows_unsupported(hass: HomeAssistant) -> None:
    cloud = _cloud(devices=[OTHER_VACUUM])
    with patch("custom_components.dreame_vacuum_3d.config_flow.XiaomiCloud", return_value=cloud):
        result = await _init(hass)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_USERNAME: USER, CONF_PASSWORD: PASSWORD, CONF_COUNTRY: COUNTRY},
        )
        assert result["type"] is FlowResultType.FORM
        assert result["errors"] == {"base": "no_devices"}
        assert "xiaomi.vacuum.d109gl" in result["description_placeholders"]["devices"]


async def test_already_configured(hass: HomeAssistant) -> None:
    MockConfigEntry(domain=DOMAIN, data=ENTRY_DATA, unique_id="1001").add_to_hass(hass)
    cloud = _cloud()
    with patch("custom_components.dreame_vacuum_3d.config_flow.XiaomiCloud", return_value=cloud):
        result = await _init(hass)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_USERNAME: USER, CONF_PASSWORD: PASSWORD, CONF_COUNTRY: COUNTRY},
        )
        assert result["type"] is FlowResultType.ABORT
        assert result["reason"] == "already_configured"


async def test_reauth_updates_auth(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(domain=DOMAIN, data=ENTRY_DATA, unique_id="1001")
    entry.add_to_hass(hass)
    new_auth = CloudAuth(
        user_id=AUTH.user_id,
        ssecurity=AUTH.ssecurity,
        service_token="bmV3LXRva2Vu",
        client_id=AUTH.client_id,
        pass_token=AUTH.pass_token,
    )
    cloud = _cloud(auth=new_auth)
    with patch("custom_components.dreame_vacuum_3d.config_flow.XiaomiCloud", return_value=cloud):
        result = await entry.start_reauth_flow(hass)
        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == "reauth_confirm"

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_PASSWORD: "new-secret"},
        )
        assert result["type"] is FlowResultType.ABORT
        assert result["reason"] == "reauth_successful"
        assert entry.data[CONF_PASSWORD] == "new-secret"
        assert entry.data[CONF_AUTH]["service_token"] == "bmV3LXRva2Vu"


async def test_options_save(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(domain=DOMAIN, data=ENTRY_DATA, unique_id="1001")
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "init"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_COEXIST: False,
            CONF_NOTIFY: ["error", "consumable"],
            CONF_CONSUMABLE_THRESHOLD: 20,
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options[CONF_COEXIST] is False
    assert entry.options[CONF_NOTIFY] == ["error", "consumable"]
    assert entry.options[CONF_CONSUMABLE_THRESHOLD] == 20


async def test_reconfigure_updates_host(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(domain=DOMAIN, data=ENTRY_DATA, unique_id="1001")
    entry.add_to_hass(hass)

    result = await entry.start_reconfigure_flow(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reconfigure"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_HOST: "192.168.1.99"},
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert entry.data[CONF_HOST] == "192.168.1.99"


async def test_device_choice_when_several(hass: HomeAssistant) -> None:
    second = {
        "did": "1002",
        "name": "L10s",
        "model": "dreame.vacuum.r2228o",
        "localip": "192.168.1.51",
        "token": "aabbccddeeff00112233445566778899",
        "mac": "AA:BB:CC:00:00:02",
    }
    cloud = _cloud(devices=[VACUUM, second])
    with patch("custom_components.dreame_vacuum_3d.config_flow.XiaomiCloud", return_value=cloud):
        result = await _init(hass)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_USERNAME: USER, CONF_PASSWORD: PASSWORD, CONF_COUNTRY: COUNTRY},
        )
        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == "device"

        key = next(iter(result["data_schema"].schema))
        choices = list(result["data_schema"].schema[key].container)
        assert len(choices) == 2
        chosen = next(c for c in choices if "L10s" in c)
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {"device": chosen})
        assert result["type"] is FlowResultType.CREATE_ENTRY
        assert result["data"][CONF_DID] == "1002"
        assert result["title"] == "L10s"
