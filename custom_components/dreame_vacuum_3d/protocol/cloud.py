"""Async Xiaomi cloud client: account login and the RC4-encrypted app API.

Portions derived from Tasshack/dreame-vacuum (MIT), see NOTICE.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, fields
import hashlib
from http.cookies import SimpleCookie
import json
import locale
import logging
import secrets
import string
import time
from typing import Any, Self

import aiohttp
from yarl import URL

from . import cloud_crypto as cc

_LOGGER = logging.getLogger(__name__)

ACCOUNT_HOST = "account.xiaomi.com"
ACCOUNT_URL = f"https://{ACCOUNT_HOST}"
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


@dataclass(frozen=True)
class _Reply:
    status: int
    headers: Mapping[str, str]
    body: bytes

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


def _timezone() -> str:
    offset = -(time.altzone if time.localtime().tm_isdst > 0 else time.timezone) // 60
    return f"GMT{'+' if offset >= 0 else '-'}{abs(offset) // 60:02d}:{abs(offset) % 60:02d}"


class XiaomiCloud:
    """One Xiaomi account on one server region.

    The session must have its own cookie jar, created with quote_cookie=False: the account
    server keeps its state in cookies, and quoted base64 tokens aren't what it expects.
    """

    def __init__(self, session: aiohttp.ClientSession, country: str, auth: CloudAuth | None = None) -> None:
        if session.cookie_jar.quote_cookie:
            raise ValueError("the session needs a cookie jar with quote_cookie=False")
        self._session = session
        self._country = country
        self._client_id = auth.client_id if auth else "".join(secrets.choice(string.ascii_lowercase) for _ in range(16))
        self._user_id = auth.user_id if auth else None
        self._ssecurity = auth.ssecurity if auth else None
        self._service_token = auth.service_token if auth else None
        self._pass_token = auth.pass_token if auth else None
        self._captcha_ick: str | None = None
        self._user_agent = f"Android-7.1.1-1.0.0-ONEPLUS A3010-136-{self._client_id} APP/xiaomi.smarthome APPV/62830"
        self._locale = locale.getlocale()[0] or "en_US"

    def __repr__(self) -> str:
        return f"XiaomiCloud(country={self._country!r}, user_id={self._user_id!r})"

    @property
    def api_url(self) -> str:
        return f"https://{'' if self._country == 'cn' else self._country + '.'}api.io.mi.com/app"

    @property
    def auth(self) -> CloudAuth | None:
        if not (self._user_id and self._ssecurity and self._service_token):
            return None
        return CloudAuth(self._user_id, self._ssecurity, self._service_token, self._client_id, self._pass_token)

    async def login(self, username: str, password: str, *, captcha: str | None = None) -> CloudAuth:
        """Log in, reusing the current session if it still works.

        Raises CloudLoginRequired when a captcha (retry with captcha=) or a two-factor
        verification is needed, CloudAuthError("invalid_auth") on bad credentials.
        """
        if (auth := self.auth) and await self.check():
            return auth
        self._reset_cookies()
        try:
            location, sign = await self._service_login()
            if not location:
                location = await self._login_with_password(username, password, sign, captcha)
            await self._fetch_service_token(location)
        except CloudError:
            self._ssecurity = None
            raise
        return self._require_auth()

    async def check(self) -> bool:
        """Return whether the session still works."""
        if self.auth is None:
            return False
        try:
            await self._api("v2/message/v2/check_new_msg", {"begin_at": int(time.time()) - 60})
        except CloudAuthError:
            return False
        return True

    def _require_auth(self) -> CloudAuth:
        if (auth := self.auth) is None:
            raise CloudAuthError("invalid_auth")
        return auth

    def _reset_cookies(self) -> None:
        jar = self._session.cookie_jar
        jar.clear()
        values = {
            "sdkVersion": "3.8.6",
            "deviceId": self._client_id,
            "passToken": self._pass_token,
            "userId": self._user_id,
        }
        for domain in ("mi.com", "xiaomi.com"):
            cookies = SimpleCookie()
            for name, value in values.items():
                if value:
                    cookies[name] = value
                    cookies[name]["domain"] = domain
                    cookies[name]["path"] = "/"
            jar.update_cookies(cookies, URL(f"https://{domain}/"))

    def _cookie(self, name: str, domain: str | None = None) -> str | None:
        """Return a cookie from the jar, only one set for domain (or a subdomain) if given."""
        for morsel in self._session.cookie_jar:
            if morsel.key == name and (domain is None or morsel["domain"].endswith(domain)):
                return morsel.value
        return None

    async def _http(self, method: str, url: str, **kwargs: Any) -> _Reply:
        headers = {"User-Agent": self._user_agent, **kwargs.pop("headers", {})}
        try:
            async with self._session.request(method, url, headers=headers, timeout=TIMEOUT, **kwargs) as response:
                reply = _Reply(response.status, response.headers, await response.read())
        except (aiohttp.ClientError, TimeoutError) as err:
            raise CloudConnectionError(f"can't reach {URL(url).host}: {type(err).__name__}") from err
        _LOGGER.debug("%s %s: HTTP %s", method, URL(url).with_query(None), reply.status)
        return reply

    def _update_login(self, data: dict[str, Any]) -> None:
        if (user_id := data.get("userId")) is not None:
            self._user_id = str(user_id)
        self._ssecurity = data.get("ssecurity", self._ssecurity)
        self._pass_token = data.get("passToken") or self._cookie("passToken", ACCOUNT_HOST) or self._pass_token

    async def _service_login(self) -> tuple[str | None, str | None]:
        """Step 1: return the STS location if the pass token is still good, and the login sign."""
        reply = await self._http(
            "GET", f"{ACCOUNT_URL}/pass/serviceLogin", params={"sid": SID, "_json": "true"}, headers=_FORM
        )
        data = reply.ok().json()
        if data.get("code") != 0:
            return None, data.get("_sign")
        self._update_login(data)
        return data.get("location") or None, data.get("_sign")

    async def _login_with_password(self, username: str, password: str, sign: str | None, captcha: str | None) -> str:
        """Step 2: return the STS location, or raise if more than the password is needed."""
        data = {
            "user": username,
            "hash": hashlib.md5(password.encode(), usedforsecurity=False).hexdigest().upper(),
            "callback": "https://sts.api.io.mi.com/sts",
            "sid": SID,
            "qs": "%3Fsid%3Dxiaomiio%26_json%3Dtrue",
        }
        if sign:
            data["_sign"] = sign
        params = {"_json": "true"}
        cookies = {}
        if captcha and self._captcha_ick:
            data["captCode"] = captcha
            params["_dc"] = str(int(time.time() * 1000))
            cookies["ick"] = self._captcha_ick
        reply = await self._http(
            "POST", f"{ACCOUNT_URL}/pass/serviceLoginAuth2", params=params, data=data, cookies=cookies, headers=_FORM
        )
        result = reply.ok().json()
        if location := result.get("location"):
            self._update_login(result)
            return location
        captcha_png = await self._fetch_captcha(_absolute(url)) if (url := result.get("captchaUrl")) else None
        verification_url = _absolute(url) if (url := result.get("notificationUrl")) else None
        if captcha_png or verification_url:
            _LOGGER.debug("Xiaomi login needs %s", "a captcha" if captcha_png else "a two-factor verification")
            raise CloudLoginRequired(captcha_png=captcha_png, verification_url=verification_url)
        raise CloudAuthError("invalid_auth")

    async def _fetch_captcha(self, url: str) -> bytes | None:
        reply = (await self._http("GET", url)).ok()
        self._captcha_ick = self._cookie("ick")
        return reply.body if self._captcha_ick else None

    async def _fetch_service_token(self, location: str) -> None:
        """Step 3: follow the STS location, which sets the serviceToken cookie."""
        (await self._http("GET", location, headers=_FORM)).ok()
        if not (token := self._cookie("serviceToken")):
            raise CloudAuthError("invalid_auth")
        self._service_token = token
        self._pass_token = self._cookie("passToken", ACCOUNT_HOST) or self._pass_token

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

    async def _api(self, path: str, params: Any) -> dict[str, Any]:
        """POST an encrypted API call and return the decrypted reply."""
        auth = self._require_auth()
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
