"""Config flow for Dreame Vacuum 3D: Xiaomi login, captcha, two-factor and device pick.

The Xiaomi password is stored in the config entry so the integration can re-login
automatically when the session expires (see ASSURANCE when that doc exists).
"""

from __future__ import annotations

import base64
from collections.abc import Mapping
from dataclasses import asdict
from typing import Any

from aiohttp import CookieJar
from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.const import (
    CONF_COUNTRY,
    CONF_HOST,
    CONF_MAC,
    CONF_MODEL,
    CONF_PASSWORD,
    CONF_TOKEN,
    CONF_USERNAME,
)
from homeassistant.core import callback
from homeassistant.data_entry_flow import AbortFlow
from homeassistant.helpers.aiohttp_client import async_create_clientsession
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    SelectSelector,
    SelectSelectorConfig,
)
import voluptuous as vol

from .const import (
    CONF_AUTH,
    CONF_COEXIST,
    CONF_CONSUMABLE_THRESHOLD,
    CONF_DID,
    CONF_NOTIFY,
    COUNTRIES,
    DEFAULT_COEXIST,
    DEFAULT_CONSUMABLE_THRESHOLD,
    DOMAIN,
    NOTIFICATION,
)
from .device.capabilities import FAMILY
from .protocol.cloud import CloudAuthError, CloudConnectionError, CloudLoginRequired, XiaomiCloud

STEP_USER = vol.Schema(
    {
        vol.Required(CONF_USERNAME): str,
        vol.Required(CONF_PASSWORD): str,
        vol.Required(CONF_COUNTRY, default="de"): vol.In(COUNTRIES),
    }
)


class DreameVacuum3DConfigFlow(ConfigFlow, domain=DOMAIN):
    """Add a Dreame vacuum through the Xiaomi cloud."""

    VERSION = 1

    def __init__(self) -> None:
        self._username: str | None = None
        self._password: str | None = None
        self._country: str | None = None
        self._cloud: XiaomiCloud | None = None
        self._captcha_png: bytes | None = None
        self._verification_url: str | None = None
        self._verification_dest: str | None = None
        self._devices: dict[str, dict[str, Any]] = {}
        self._unsupported: list[str] = []

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return DreameVacuum3DOptionsFlow()

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        placeholders = {"devices": self._unsupported_text()}

        if user_input is not None:
            self._username = user_input[CONF_USERNAME]
            self._password = user_input[CONF_PASSWORD]
            self._country = user_input[CONF_COUNTRY]
            self._cloud = None
            next_step = await self._try_login()
            if next_step is None:
                return await self.async_step_device()
            if next_step in ("captcha", "2fa"):
                return await getattr(self, f"async_step_{next_step}")()
            errors["base"] = next_step
            placeholders["devices"] = self._unsupported_text()

        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(
                STEP_USER,
                {
                    CONF_USERNAME: self._username,
                    CONF_PASSWORD: self._password,
                    CONF_COUNTRY: self._country or "de",
                },
            ),
            errors=errors,
            description_placeholders=placeholders,
        )

    async def async_step_captcha(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            next_step = await self._try_login(captcha=user_input["captcha"])
            if next_step is None:
                return await self._after_login()
            if next_step == "2fa":
                return await self.async_step_2fa()
            errors["base"] = "wrong_captcha" if next_step == "captcha" else next_step

        if not self._captcha_png:
            return await self.async_step_user()

        image = base64.b64encode(self._captcha_png).decode()
        return self.async_show_form(
            step_id="captcha",
            data_schema=vol.Schema({vol.Required("captcha"): str}),
            errors=errors,
            description_placeholders={"captcha": f"![captcha](data:image/png;base64,{image})"},
        )

    async def async_step_2fa(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}

        cloud = self._cloud
        if user_input is not None:
            if cloud is None:
                return await self.async_step_user()
            try:
                await cloud.verify_2fa_code(user_input["code"])
            except CloudAuthError as err:
                errors["base"] = str(err) or "2fa_failed"
            except CloudConnectionError:
                errors["base"] = "cannot_connect"
            else:
                return await self._after_login()
        elif cloud and self._verification_url and self._verification_dest is None:
            try:
                self._verification_dest = await cloud.send_2fa_code(self._verification_url)
            except CloudAuthError as err:
                return await self._user_with_error(str(err) or "2fa_send_failed")
            except CloudConnectionError:
                return await self._user_with_error("cannot_connect")

        return self.async_show_form(
            step_id="2fa",
            data_schema=vol.Schema({vol.Required("code"): str}),
            errors=errors,
            description_placeholders={"dest": self._verification_dest or "*****"},
        )

    async def async_step_device(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return await self._create_from_device(self._devices[user_input["device"]])

        if error := await self._load_devices():
            return await self._user_with_error(error)

        if len(self._devices) == 1:
            return await self._create_from_device(next(iter(self._devices.values())))

        return self.async_show_form(
            step_id="device",
            data_schema=vol.Schema({vol.Required("device"): vol.In(list(self._devices))}),
        )

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        self._username = entry_data[CONF_USERNAME]
        self._country = entry_data[CONF_COUNTRY]
        self._password = entry_data.get(CONF_PASSWORD)
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            self._password = user_input[CONF_PASSWORD]
            self._cloud = None
            next_step = await self._try_login()
            if next_step is None:
                return await self._finish_reauth()
            if next_step in ("captcha", "2fa"):
                return await getattr(self, f"async_step_{next_step}")()
            errors["base"] = next_step

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_PASSWORD): str}),
            errors=errors,
        )

    async def async_step_reconfigure(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        entry = self._get_reconfigure_entry()
        if user_input is not None:
            return self.async_update_reload_and_abort(entry, data_updates={CONF_HOST: user_input[CONF_HOST]})

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=vol.Schema({vol.Required(CONF_HOST, default=entry.data.get(CONF_HOST, "")): str}),
        )

    async def _after_login(self) -> ConfigFlowResult:
        if self.source == config_entries.SOURCE_REAUTH:
            return await self._finish_reauth()
        return await self.async_step_device()

    async def _finish_reauth(self) -> ConfigFlowResult:
        cloud = self._cloud
        if cloud is None or cloud.auth is None or self._password is None:
            return await self.async_step_reauth_confirm()
        return self.async_update_reload_and_abort(
            self._get_reauth_entry(),
            data_updates={CONF_PASSWORD: self._password, CONF_AUTH: asdict(cloud.auth)},
        )

    async def _user_with_error(self, error: str) -> ConfigFlowResult:
        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(
                STEP_USER,
                {
                    CONF_USERNAME: self._username,
                    CONF_PASSWORD: self._password,
                    CONF_COUNTRY: self._country or "de",
                },
            ),
            errors={"base": error},
            description_placeholders={"devices": self._unsupported_text()},
        )

    async def _try_login(self, *, captcha: str | None = None) -> str | None:
        """Log in; return None on success, a step id (`captcha` / `2fa`), or an error key."""
        username, password, country = self._username, self._password, self._country
        if not (username and password and country):
            return "invalid_auth"
        cloud = self._ensure_cloud()
        try:
            await cloud.login(username, password, captcha=captcha)
        except CloudLoginRequired as err:
            if err.captcha_png:
                self._captcha_png = err.captcha_png
                return "captcha"
            self._verification_url = err.verification_url
            return "2fa"
        except CloudAuthError as err:
            return str(err) or "invalid_auth"
        except CloudConnectionError:
            return "cannot_connect"
        return None

    def _ensure_cloud(self) -> XiaomiCloud:
        if self._cloud is None:
            country = self._country or "de"
            session = async_create_clientsession(self.hass, cookie_jar=CookieJar(quote_cookie=False))
            self._cloud = XiaomiCloud(session, country)
        return self._cloud

    async def _load_devices(self) -> str | None:
        cloud = self._cloud
        if cloud is None:
            return "invalid_auth"
        try:
            devices = await cloud.devices()
        except CloudAuthError as err:
            return str(err) or "invalid_auth"
        except CloudConnectionError:
            return "cannot_connect"

        supported: dict[str, dict[str, Any]] = {}
        unsupported: list[str] = []
        configured_dreame = False
        for device in devices:
            model = str(device.get("model") or "")
            did = device.get("did")
            if not model.startswith(FAMILY):
                if model:
                    unsupported.append(model)
                continue
            if did is None:
                continue
            if self.hass.config_entries.async_entry_for_domain_unique_id(DOMAIN, str(did)):
                configured_dreame = True
                continue
            label = f"{device.get('name') or model} ({did})"
            supported[label] = device

        self._devices = supported
        self._unsupported = unsupported
        if supported:
            return None
        if configured_dreame:
            raise AbortFlow("already_configured")
        return "no_devices"

    def _unsupported_text(self) -> str:
        return "".join(f" ({model})" for model in self._unsupported)

    async def _create_from_device(self, device: dict[str, Any]) -> ConfigFlowResult:
        cloud = self._cloud
        if cloud is None or cloud.auth is None:
            return await self._user_with_error("invalid_auth")
        did = str(device["did"])
        await self.async_set_unique_id(did)
        self._abort_if_unique_id_configured()
        # Password kept for automatic re-login when the cloud session expires.
        return self.async_create_entry(
            title=str(device.get("name") or device.get("model") or did),
            data={
                CONF_COUNTRY: self._country,
                CONF_USERNAME: self._username,
                CONF_PASSWORD: self._password,
                CONF_AUTH: asdict(cloud.auth),
                CONF_DID: did,
                CONF_MODEL: device.get("model"),
                CONF_HOST: device.get("localip") or "",
                CONF_TOKEN: device.get("token") or "",
                CONF_MAC: device.get("mac") or "",
            },
            options={
                CONF_COEXIST: DEFAULT_COEXIST,
                CONF_NOTIFY: list(NOTIFICATION),
                CONF_CONSUMABLE_THRESHOLD: DEFAULT_CONSUMABLE_THRESHOLD,
            },
        )


class DreameVacuum3DOptionsFlow(OptionsFlow):
    """Options: coexist polling, notification categories, consumable threshold."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        options = self.config_entry.options
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_COEXIST,
                        default=options.get(CONF_COEXIST, DEFAULT_COEXIST),
                    ): bool,
                    vol.Required(
                        CONF_NOTIFY,
                        default=options.get(CONF_NOTIFY, list(NOTIFICATION)),
                    ): SelectSelector(
                        SelectSelectorConfig(
                            options=list(NOTIFICATION),
                            multiple=True,
                            translation_key="notifications",
                        )
                    ),
                    vol.Required(
                        CONF_CONSUMABLE_THRESHOLD,
                        default=options.get(CONF_CONSUMABLE_THRESHOLD, DEFAULT_CONSUMABLE_THRESHOLD),
                    ): NumberSelector(
                        NumberSelectorConfig(min=1, max=100, step=1, unit_of_measurement="%"),
                    ),
                }
            ),
        )
