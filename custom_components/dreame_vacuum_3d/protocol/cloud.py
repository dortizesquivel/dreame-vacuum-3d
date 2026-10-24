"""Async Xiaomi cloud client: account login (captcha, two-factor), devices, RPC and map files.

Portions derived from Tasshack/dreame-vacuum (MIT), see NOTICE.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass, field, fields
from functools import partial
import hashlib
from http.cookies import SimpleCookie
import json
from locale import getlocale
import logging
import re
import secrets
import string
import time
from typing import Any, Self

import aiohttp
from yarl import URL

from . import cloud_crypto as cc
from .miot import Send

_LOGGER = logging.getLogger(__name__)

ACCOUNT_URL = "https://account.xiaomi.com"
SID = "xiaomiio"
TIMEOUT = aiohttp.ClientTimeout(total=10)
_FORM = {"Content-Type": "application/x-www-form-urlencoded"}
_API_HEADERS = {
    "Accept-Encoding": "identity",
    "x-xiaomi-protocal-flag-cli": "PROTOCAL-HTTP2",
    "content-type": "application/x-www-form-urlencoded",
    "MIOT-ENCRYPT-ALGORITHM": "ENCRYPT-RC4",
}
_AUTH_ERROR_CODES = (2, 3)
_AUTH_ERROR_MESSAGES = ("auth err", "invalid signature", "SERVICETOKEN_EXPIRED")
_AUTH_START = "fe/service/identity/authStart"
_IDENTITY_URL = f"{ACCOUNT_URL}/identity"
_GETHOME = {"fg": True, "fetch_share": True, "fetch_share_dev": True, "limit": 100, "app_ver": 7}
_HOME_DEVICES = {"limit": 100, "get_split_device": True, "support_smart_home": True}
_STS_URL = re.compile(r'(https://[a-zA-Z0-9-]*\.?sts\.api\.io\.mi\.com/sts[^"\'\s]*)')


@dataclass
class CloudAuth:
    """A logged-in session, kept in the config entry so the password isn't needed again."""

    user_id: str
    ssecurity: str = field(repr=False)
    service_token: str = field(repr=False)
    client_id: str
    pass_token: str | None = field(default=None, repr=False)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Self:
        return cls(**{f.name: data[f.name] for f in fields(cls) if f.name in data})


class CloudError(Exception):
    """Base of the Xiaomi cloud errors."""


class CloudLoginRequired(CloudError):
    """The account wants a captcha or a two-factor verification before logging in."""

    def __init__(self, *, captcha_png: bytes | None = None, verification_url: str | None = None) -> None:
        super().__init__("captcha_required" if captcha_png else "2fa_required")
        self.captcha_png = captcha_png
        self.verification_url = verification_url


class CloudAuthError(CloudError):
    """Bad credentials or a dead session; the message is the error key ("invalid_auth", ...)."""


class CloudConnectionError(CloudError):
    """The cloud couldn't be reached or answered something unusable."""


@dataclass
class _Login:
    """What a login in progress has learnt; it becomes the CloudAuth only once it succeeds."""

    username: str | None
    user_id: str | None = None
    ssecurity: str | None = field(default=None, repr=False)
    service_token: str | None = field(default=None, repr=False)
    pass_token: str | None = field(default=None, repr=False)
    captcha_ick: str | None = field(default=None, repr=False)
    verification_url: str | None = field(default=None, repr=False)


@dataclass(frozen=True)
class _Reply:
    status: int
    headers: Mapping[str, str]
    body: bytes
    cookies: Mapping[str, str]

    @property
    def text(self) -> str:
        return self.body.decode(errors="replace")

    def ok(self) -> Self:
        if self.status != 200:
            raise CloudConnectionError(f"the Xiaomi cloud answered HTTP {self.status}")
        return self

    def json(self) -> dict[str, Any]:
        """Parse an account server reply, which may start with &&&START&&&."""
        try:
            data = json.loads(self.text.replace("&&&START&&&", ""))
        except ValueError as err:
            raise CloudConnectionError("malformed reply from the Xiaomi cloud") from err
        if not isinstance(data, dict):
            raise CloudConnectionError("malformed reply from the Xiaomi cloud")
        return data


def _absolute(url: str) -> str:
    return url if url.startswith("http") else ACCOUNT_URL + url


def _check_sent(data: dict[str, Any]) -> None:
    """Raise the error key matching a refused verification code request."""
    code = data.get("code")
    if code == 0:
        return
    text = f"{data.get('description') or ''} {data.get('message') or ''}".lower()
    if "frequent" in text or "seconds" in text:
        raise CloudAuthError("2fa_cooldown_active")
    if code == 70022 or "limit" in text:
        raise CloudAuthError("2fa_limit_reached")
    raise CloudAuthError("2fa_send_failed")


def _timezone() -> str:
    offset = -(time.altzone if time.localtime().tm_isdst > 0 else time.timezone) // 60
    return f"GMT{'+' if offset >= 0 else '-'}{abs(offset) // 60:02d}:{abs(offset) % 60:02d}"


class XiaomiCloud:
    """One Xiaomi account on one server region.

    Give each instance its own session, with a cookie jar created with quote_cookie=False: the
    account server keeps its state in cookies, and quoted base64 tokens aren't what it expects.
    The caller closes the session. Keep the same instance through a login's captcha and
    two-factor steps; use a new one (and a new session) for another account.
    """

    def __init__(
        self,
        session: aiohttp.ClientSession,
        country: str,
        auth: CloudAuth | None = None,
    ) -> None:
        if session.cookie_jar.quote_cookie:
            raise ValueError("the session needs a cookie jar with quote_cookie=False")
        self._session = session
        self._country = country
        self._auth = auth
        self._client_id = auth.client_id if auth else "".join(secrets.choice(string.ascii_lowercase) for _ in range(16))
        self._username: str | None = None
        self._pending: _Login | None = None
        self._lock = asyncio.Lock()
        self._files_v2_only = False
        self._user_agent = f"Android-7.1.1-1.0.0-ONEPLUS A3010-136-{self._client_id} APP/xiaomi.smarthome APPV/62830"
        self._locale = getlocale()[0] or "en_US"

    def __repr__(self) -> str:
        return f"XiaomiCloud(country={self._country!r}, user_id={self._auth.user_id if self._auth else None!r})"

    @property
    def api_url(self) -> str:
        return f"https://{'' if self._country == 'cn' else self._country + '.'}api.io.mi.com/app"

    @property
    def auth(self) -> CloudAuth | None:
        return self._auth

    async def login(self, username: str, password: str, *, captcha: str | None = None) -> CloudAuth:
        """Log in, reusing the current session if it still works.

        Raises CloudLoginRequired when a captcha (retry with captcha=) or a two-factor
        verification is needed, CloudAuthError("invalid_auth") on bad credentials. The
        current session stays in use until the new one is complete.
        """
        async with self._lock:
            same_account = self._username in (None, username)
            if same_account and (auth := self._auth) and await self._check(auth):
                return auth
            state = self._resume(username, same_account)
            self._reset_cookies(state)
            try:
                location, sign = await self._service_login(state)
                if not location:
                    location = await self._login_with_password(state, password, sign, captcha)
                await self._fetch_service_token(state, location)
                return self._commit(state, "invalid_auth")
            except CloudError:
                self._pending = state
                raise

    async def check(self) -> bool:
        """Return whether the session still works."""
        return await self._check(self._auth) if self._auth else False

    async def send_2fa_code(self, verification_url: str) -> str:
        """Send a verification code for a CloudLoginRequired; return where it went, masked.

        Raises CloudAuthError("2fa_cooldown_active"), ("2fa_limit_reached") or ("2fa_send_failed").
        """
        if _AUTH_START not in verification_url:
            raise CloudAuthError("2fa_send_failed")
        async with self._lock:
            state = self._pending = self._pending or _Login(None)
            state.verification_url = verification_url
            await self._http("GET", verification_url)
            context = URL(verification_url).query.get("context", "")
            kind, flag, identity = await self._identity_options(context)
            if not identity:
                raise CloudAuthError("2fa_send_failed")
            params = {"sid": SID, "context": context, "mask": "0", "_locale": self._locale}
            reply = await self._http(
                "GET",
                f"{_IDENTITY_URL}/auth/verify{kind}",
                params={"_flag": flag, "_json": "true", **params},
                cookies={"identity_session": identity},
            )
            verify = reply.ok().json()
            _check_sent(verify)
            sent = await self._http(
                "POST",
                f"{_IDENTITY_URL}/auth/send{kind}Ticket",
                params={"_dc": str(int(time.time() * 1000)), **params},
                data={"retry": "0", "icode": "", "_json": "true", "ick": self._cookie("ick") or ""},
                cookies={"identity_session": identity},
            )
            _check_sent(sent.ok().json())
            return verify.get("maskedPhone") or verify.get("maskedEmail") or "*****"

    async def verify_2fa_code(self, code: str) -> CloudAuth:
        """Finish the login with the code send_2fa_code sent; CloudAuthError("2fa_failed") if refused."""
        async with self._lock:
            state = self._pending
            url = state.verification_url if state else None
            context = URL(url).query.get("context") if url else None
            if not (state and code and context):
                raise CloudAuthError("2fa_failed")
            kind, flag, identity = await self._identity_options(context)
            if not identity:
                raise CloudAuthError("2fa_failed")
            reply = await self._http(
                "POST",
                f"{_IDENTITY_URL}/auth/verify{kind}",
                params={
                    "_flag": flag,
                    "_json": "true",
                    "sid": SID,
                    "context": context,
                    "mask": "0",
                    "_locale": self._locale,
                },
                data={
                    "_flag": flag,
                    "ticket": code,
                    "trust": "false",
                    "_json": "true",
                    "ick": self._cookie("ick") or "",
                },
                cookies={"identity_session": identity},
                headers=_FORM,
            )
            location = reply.headers.get("Location") if reply.status in (301, 302) else None
            if reply.status == 200:
                try:
                    data = reply.json()
                except CloudConnectionError:
                    location = reply.headers.get("Location")
                else:
                    if data.get("code") != 0:
                        raise CloudAuthError("2fa_failed")
                    location = data.get("location")
            if not (location and await self._login_after_2fa(state, location)):
                await self._sts_after_2fa(state, context)
            return self._commit(state, "2fa_failed")

    async def devices(self) -> list[dict[str, Any]]:
        """List the account's devices (did, model, name, localip, token, mac, uid...) in own and shared homes.

        Child devices (with a parent_id) are left out, and each device is listed once.
        """
        auth = self._require_auth()
        homes: dict[Any, Any] = {}
        result = await self._result("v2/homeroom/gethome", _GETHOME)
        for home in result.get("homelist") or []:
            homes[home["id"]] = auth.user_id
        result = await self._result("v2/user/get_device_cnt", {"fetch_own": True, "fetch_share": True})
        for home in (result.get("share") or {}).get("share_family") or []:
            homes[home["home_id"]] = home["home_owner"]
        found: list[dict[str, Any]] = []
        for home_id, owner in homes.items():
            params = {"home_id": int(home_id), "home_owner": owner, **_HOME_DEVICES}
            found += (await self._result("v2/home/home_device_list", params)).get("device_info") or []
        result = await self._result("home/device_list", {"getVirtualModel": False, "getHuamiDevices": 0})
        found += result.get("list") or []
        devices: dict[Any, dict[str, Any]] = {}
        for device in found:
            if not device.get("parent_id"):
                devices.setdefault(device.get("mac") or device.get("did"), device)
        return list(devices.values())

    async def rpc(self, did: str, method: str, params: Any) -> Any:
        """Call a miIO method on a device through the cloud; return its result (None if it has none)."""
        return (await self._api(f"v2/home/rpc/{did}", {"method": method, "params": params})).get("result")

    def rpc_sender(self, did: str) -> Send:
        """Return rpc bound to one device, for the miot helpers."""
        return partial(self.rpc, did)

    async def file_url(self, object_name: str, *, model: str = "") -> str | None:
        """Return a download URL for a stored file (a map), None if there's none."""
        return await self._file_url("home/getfileurl", "_v3", object_name, model)

    async def interim_file_url(self, object_name: str, *, model: str = "") -> str | None:
        """Return a download URL for a file still being written (the live map), None if there's none."""
        return await self._file_url("v2/home/get_interim_file_url", "_pro", object_name, model)

    async def download(self, url: str) -> bytes | None:
        """Download a file URL; None if the server doesn't answer 200."""
        reply = await self._http("GET", url)
        return reply.body if reply.status == 200 else None

    def _resume(self, username: str, same_account: bool) -> _Login:
        """Start a login from what's known of this account: its pass token, and a pending captcha."""
        pending = self._pending
        if pending and pending.username == username:
            return _Login(username, pending.user_id, pass_token=pending.pass_token, captcha_ick=pending.captcha_ick)
        if same_account and (auth := self._auth):
            return _Login(username, auth.user_id, pass_token=auth.pass_token)
        return _Login(username)

    def _commit(self, state: _Login, error: str) -> CloudAuth:
        if not (state.user_id and state.ssecurity and state.service_token):
            raise CloudAuthError(error)
        self._auth = CloudAuth(state.user_id, state.ssecurity, state.service_token, self._client_id, state.pass_token)
        self._username = state.username or self._username
        self._pending = None
        return self._auth

    async def _check(self, auth: CloudAuth) -> bool:
        try:
            await self._api("v2/message/v2/check_new_msg", {"begin_at": int(time.time()) - 60}, auth)
        except CloudAuthError:
            return False
        return True

    async def _identity_options(self, context: str) -> tuple[str, str, str | None]:
        """Return how the account receives codes (("Phone", "4") or ("Email", "8")) and the identity session."""
        reply = await self._http(
            "GET",
            f"{_IDENTITY_URL}/list",
            params={"sid": SID, "context": context, "_locale": self._locale},
            headers=_FORM,
        )
        data = reply.ok().json()
        options = data.get("options") or []
        flag = 4 if 4 in options else 8 if 8 in options else data.get("flag", 4)
        return ("Phone" if flag == 4 else "Email"), str(flag), self._reply_cookie(reply, "identity_session")

    async def _login_after_2fa(self, state: _Login, location: str) -> bool:
        """Follow the verified location, which grants a pass token, then log in with it."""
        try:
            if (await self._http("GET", location, headers=_FORM)).status != 200:
                return False
            state.user_id = self._jar_user_id() or state.user_id
            location, _sign = await self._service_login(state)
            if not location:
                return False
            await self._fetch_service_token(state, location)
        except CloudError:
            return False
        return True

    async def _sts_after_2fa(self, state: _Login, context: str) -> None:
        """Fetch the ssecurity and the service token through the identity result page."""
        reply = await self._http(
            "GET",
            f"{_IDENTITY_URL}/result/check",
            params={"sid": SID, "context": context, "_locale": self._locale},
            headers=_FORM,
            allow_redirects=False,
        )
        location = reply.headers.get("Location") if reply.status in (301, 302) else None
        if not location and reply.status == 200 and reply.body:
            location = reply.json().get("location")
        if not location:
            raise CloudAuthError("2fa_failed")
        reply = await self._http("GET", location, headers=_FORM, allow_redirects=False)
        if reply.status == 200 and "Xiaomi Account - Tips" in reply.text:
            reply = await self._http("GET", location, headers=_FORM, allow_redirects=False)
        try:
            state.ssecurity = json.loads(reply.headers.get("extension-pragma", ""))["ssecurity"]
        except (ValueError, KeyError, TypeError) as err:
            raise CloudAuthError("2fa_failed") from err
        sts = reply.headers.get("Location") or ((m := _STS_URL.search(reply.text)) and m.group(1))
        if not sts:
            raise CloudAuthError("2fa_failed")
        reply = await self._http("GET", sts, headers=_FORM)
        if reply.status != 200:
            raise CloudAuthError("2fa_failed")
        state.service_token = self._reply_cookie(reply, "serviceToken")
        state.user_id = self._jar_user_id() or state.user_id

    def _jar_user_id(self) -> str | None:
        return next((m.value for m in self._session.cookie_jar if m.key in ("userId", "cUserId") and m.value), None)

    async def _file_url(self, path: str, v3_suffix: str, object_name: str, model: str) -> str | None:
        v3 = "xiaomi.vacuum." in model and not self._files_v2_only
        reply = await self._api(path + (v3_suffix if v3 else ""), {"obj_name": object_name})
        result = reply.get("result")
        if isinstance(result, dict) and result.get("url"):
            return result["url"]
        if v3 and reply.get("code") == -8:
            self._files_v2_only = True
            return await self._file_url(path, v3_suffix, object_name, model)
        return None

    async def _result(self, path: str, params: Any) -> dict[str, Any]:
        result = (await self._api(path, params)).get("result")
        return result if isinstance(result, dict) else {}

    def _require_auth(self) -> CloudAuth:
        if (auth := self._auth) is None:
            raise CloudAuthError("invalid_auth")
        return auth

    def _reset_cookies(self, state: _Login) -> None:
        jar = self._session.cookie_jar
        jar.clear()
        values = {
            "sdkVersion": "3.8.6",
            "deviceId": self._client_id,
            "passToken": state.pass_token,
            "userId": state.user_id,
        }
        for domain in ("mi.com", "xiaomi.com"):
            cookies = SimpleCookie()
            for name, value in values.items():
                if value:
                    cookies[name] = value
                    cookies[name]["domain"] = domain
                    cookies[name]["path"] = "/"
            jar.update_cookies(cookies, URL(f"https://{domain}/"))

    def _cookie(self, name: str) -> str | None:
        return next((m.value for m in self._session.cookie_jar if m.key == name), None)

    def _reply_cookie(self, reply: _Reply, name: str) -> str | None:
        """Return a cookie the reply set, else one the jar got earlier (e.g. on a redirect)."""
        return reply.cookies.get(name) or self._cookie(name)

    def _pass_token(self, state: _Login, reply: _Reply, data: Mapping[str, Any]) -> str | None:
        # The jar still holds the pass token we started with, so it only fills a gap.
        return data.get("passToken") or reply.cookies.get("passToken") or state.pass_token or self._cookie("passToken")

    async def _http(self, method: str, url: str | URL, **kwargs: Any) -> _Reply:
        headers = {"User-Agent": self._user_agent, **kwargs.pop("headers", {})}
        try:
            async with self._session.request(method, url, headers=headers, timeout=TIMEOUT, **kwargs) as response:
                cookies = {name: morsel.value for name, morsel in response.cookies.items()}
                reply = _Reply(response.status, response.headers, await response.read(), cookies)
        except (aiohttp.ClientError, TimeoutError) as err:
            raise CloudConnectionError(f"can't reach {URL(url).host}: {type(err).__name__}") from err
        _LOGGER.debug("%s %s: HTTP %s", method, URL(url).with_query(None), reply.status)
        return reply

    def _update_login(self, state: _Login, data: dict[str, Any], reply: _Reply) -> None:
        if (user_id := data.get("userId")) is not None:
            state.user_id = str(user_id)
        state.ssecurity = data.get("ssecurity", state.ssecurity)
        state.pass_token = self._pass_token(state, reply, data)

    async def _service_login(self, state: _Login) -> tuple[str | None, str | None]:
        """Step 1: return the STS location if the pass token is still good, and the login sign."""
        reply = await self._http(
            "GET", f"{ACCOUNT_URL}/pass/serviceLogin", params={"sid": SID, "_json": "true"}, headers=_FORM
        )
        data = reply.ok().json()
        if data.get("code") != 0:
            return None, data.get("_sign")
        self._update_login(state, data, reply)
        return data.get("location") or None, data.get("_sign")

    async def _login_with_password(self, state: _Login, password: str, sign: str | None, captcha: str | None) -> str:
        """Step 2: return the STS location, or raise if more than the password is needed."""
        data = {
            "user": state.username,
            "hash": hashlib.md5(password.encode(), usedforsecurity=False).hexdigest().upper(),
            "callback": "https://sts.api.io.mi.com/sts",
            "sid": SID,
            "qs": "%3Fsid%3Dxiaomiio%26_json%3Dtrue",
        }
        if sign:
            data["_sign"] = sign
        params = {"_json": "true"}
        cookies = {}
        if captcha and state.captcha_ick:
            data["captCode"] = captcha
            params["_dc"] = str(int(time.time() * 1000))
            cookies["ick"] = state.captcha_ick
        reply = await self._http(
            "POST", f"{ACCOUNT_URL}/pass/serviceLoginAuth2", params=params, data=data, cookies=cookies, headers=_FORM
        )
        result = reply.ok().json()
        if location := result.get("location"):
            self._update_login(state, result, reply)
            return location
        captcha_png = await self._fetch_captcha(state, _absolute(url)) if (url := result.get("captchaUrl")) else None
        verification_url = _absolute(url) if (url := result.get("notificationUrl")) else None
        if captcha_png or verification_url:
            _LOGGER.debug("Xiaomi login needs %s", "a captcha" if captcha_png else "a two-factor verification")
            state.verification_url = verification_url
            raise CloudLoginRequired(captcha_png=captcha_png, verification_url=verification_url)
        raise CloudAuthError("invalid_auth")

    async def _fetch_captcha(self, state: _Login, url: str) -> bytes | None:
        reply = (await self._http("GET", url)).ok()
        state.captcha_ick = self._reply_cookie(reply, "ick")
        return reply.body if state.captcha_ick else None

    async def _fetch_service_token(self, state: _Login, location: str) -> None:
        """Step 3: follow the STS location, which sets the serviceToken cookie."""
        reply = (await self._http("GET", location, headers=_FORM)).ok()
        if not (token := self._reply_cookie(reply, "serviceToken")):
            raise CloudAuthError("invalid_auth")
        state.service_token = token
        state.pass_token = self._pass_token(state, reply, {})

    def _api_cookies(self, auth: CloudAuth) -> dict[str, str]:
        dst = time.localtime().tm_isdst
        return {
            "userId": auth.user_id,
            "yetAnotherServiceToken": auth.service_token,
            "serviceToken": auth.service_token,
            "locale": self._locale,
            "timezone": _timezone(),
            "is_daylight": str(time.daylight),
            "dst_offset": str(max(dst, 0) * 3600000),
            "channel": "MI_APP_STORE",
        }

    async def _api(self, path: str, params: Any, auth: CloudAuth | None = None) -> dict[str, Any]:
        """POST an encrypted API call with one session (the current one by default); return the reply."""
        auth = auth or self._require_auth()
        url = f"{self.api_url}/{path}"
        nonce = cc.generate_nonce()
        key = cc.signed_nonce(auth.ssecurity, nonce)
        data = {"data": json.dumps(params, separators=(",", ":"))}
        fields = cc.encrypt_params(url, "POST", key, nonce, data, auth.ssecurity)
        reply = await self._http("POST", url, data=fields, headers=_API_HEADERS, cookies=self._api_cookies(auth))
        if reply.status == 401:
            raise CloudAuthError("invalid_auth")
        try:
            result = json.loads(cc.decrypt_response(key, reply.ok().text))
        except ValueError as err:
            raise CloudConnectionError("malformed reply from the Xiaomi cloud") from err
        if not isinstance(result, dict):
            raise CloudConnectionError("malformed reply from the Xiaomi cloud")
        message = str(result.get("message", ""))
        if result.get("code") in _AUTH_ERROR_CODES or any(m in message for m in _AUTH_ERROR_MESSAGES):
            raise CloudAuthError("invalid_auth")
        return result
