"""A fake aiohttp session standing in for the Xiaomi account and API servers."""

from __future__ import annotations

import base64
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from http.cookies import SimpleCookie
import inspect
import json
from pathlib import Path
from typing import Any

import aiohttp
from multidict import CIMultiDict
from yarl import URL

from custom_components.dreame_vacuum_3d.protocol import cloud_crypto as cc

SSECURITY = "dGVzdC1zc2VjdXJpdHkxNg=="
API_URL = "https://de.api.io.mi.com/app"
ACCOUNT = "https://account.xiaomi.com"
FIXTURES = Path(__file__).parent / "fixtures" / "cloud"
REDIRECTS = (301, 302, 303, 307, 308)


def fixture(name: str) -> dict[str, Any]:
    """Load fixtures/cloud/<name>.json."""
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


def encrypt_reply(ssecurity: str, nonce: str, data: Any) -> str:
    """Encrypt an API reply the way the server does, with the request's nonce."""
    key = base64.b64decode(cc.signed_nonce(ssecurity, nonce))
    return base64.b64encode(cc.rc4(key, json.dumps(data).encode(), drop=cc.RC4_DROP)).decode()


@dataclass
class Reply:
    """What the fake server answers.

    A cookie is a value (set with Path=/ on the request host) or a dict with value and
    optional domain and path attributes.
    """

    status: int = 200
    body: bytes | str = b""
    headers: dict[str, str] = field(default_factory=dict)
    cookies: dict[str, str | dict[str, str]] = field(default_factory=dict)

    @classmethod
    def xiaomi(cls, data: dict[str, Any], *, prefix: str = "&&&START&&&", **kwargs: Any) -> Reply:
        """A JSON reply as the account server writes it."""
        return cls(body=prefix + json.dumps(data), **kwargs)

    def set_cookies(self) -> SimpleCookie:
        cookies = SimpleCookie()
        for name, cookie in self.cookies.items():
            attrs = cookie if isinstance(cookie, dict) else {"value": cookie}
            cookies[name] = attrs["value"]
            cookies[name]["path"] = attrs.get("path", "/")
            if "domain" in attrs:
                cookies[name]["domain"] = attrs["domain"]
        return cookies


@dataclass
class Call:
    """A request as the server saw it; cookies merges the jar's and the per-request ones."""

    method: str
    url: URL
    data: dict[str, str] | None
    headers: dict[str, str]
    cookies: dict[str, str]
    allow_redirects: bool
    timeout: object


type Answer = Reply | BaseException
type Handler = Answer | Callable[[Call], Answer | Awaitable[Answer]]
type Payload = dict[str, Any] | Answer | Callable[[Any], Any]


class _Response:
    def __init__(self, reply: Reply) -> None:
        self.status = reply.status
        self.headers = CIMultiDict(reply.headers)
        # Like aiohttp: the final reply's Set-Cookie headers, including any the jar refused.
        self.cookies = reply.set_cookies()
        self._body = reply.body.encode() if isinstance(reply.body, str) else reply.body

    async def read(self) -> bytes:
        return self._body


class _RequestContext:
    def __init__(self, send: Awaitable[_Response]) -> None:
        self._send = send

    async def __aenter__(self) -> _Response:
        return await self._send

    async def __aexit__(self, *exc: object) -> None:
        return None


class FakeSession:
    """Routes requests by method and URL (query ignored) to handlers, keeping a real cookie jar.

    A route with several handlers uses them in turn and then keeps answering with the last.
    Handlers may be coroutines. Redirects are followed like aiohttp does (a POST answered with
    301, 302 or 303 goes on as a GET) unless allow_redirects is False. api() routes decrypt the
    request (checking both signatures) and encrypt the reply.
    """

    def __init__(self) -> None:
        self.cookie_jar = aiohttp.CookieJar(quote_cookie=False)
        self.calls: list[Call] = []
        self.api_calls: list[tuple[str, Any]] = []
        self.ssecurities = {SSECURITY}
        self._routes: dict[tuple[str, str], list[Handler]] = {}

    def on(self, method: str, url: str, *handlers: Handler) -> None:
        self._routes[(method, url)] = list(handlers)

    def api(self, path: str, *payloads: Payload) -> None:
        self.on("POST", f"{API_URL}/{path}", *(self._api_handler(path, p) for p in payloads))

    def requests(self, method: str, url: str) -> list[Call]:
        return [c for c in self.calls if c.method == method and str(c.url.with_query(None)) == url]

    def request(
        self,
        method: str,
        url: str | URL,
        *,
        params: dict[str, str] | None = None,
        data: dict[str, str] | None = None,
        headers: dict[str, str] | None = None,
        cookies: dict[str, str] | None = None,
        allow_redirects: bool = True,
        timeout: object = None,
    ) -> _RequestContext:
        assert timeout is not None, "every request needs a timeout"
        target = URL(url).update_query(params) if params else URL(url)
        return _RequestContext(
            self._send(method, target, data, headers or {}, cookies or {}, allow_redirects, request_timeout=timeout)
        )

    async def _send(
        self,
        method: str,
        url: URL,
        data: dict[str, str] | None,
        headers: dict[str, str],
        cookies: dict[str, str],
        allow_redirects: bool,
        request_timeout: object,
    ) -> _Response:
        for _hop in range(10):
            sent = {m.key: m.value for m in self.cookie_jar.filter_cookies(url).values()} | cookies
            call = Call(
                method, url, dict(data) if data else None, dict(headers), sent, allow_redirects, request_timeout
            )
            self.calls.append(call)
            handlers = self._routes.get((method, str(url.with_query(None))))
            if not handlers:
                raise AssertionError(f"no route for {method} {url}")
            handler = handlers.pop(0) if len(handlers) > 1 else handlers[0]
            reply = handler(call) if callable(handler) else handler
            if inspect.isawaitable(reply):
                reply = await reply
            if isinstance(reply, BaseException):
                raise reply
            self.cookie_jar.update_cookies(reply.set_cookies(), url)
            location = reply.headers.get("Location")
            if not (allow_redirects and reply.status in REDIRECTS and location):
                return _Response(reply)
            url = url.join(URL(location, encoded=True))
            if reply.status == 303 or (reply.status in (301, 302) and method == "POST"):
                method, data = "GET", None
        raise AssertionError("too many redirects")

    def _api_handler(self, path: str, payload: Payload) -> Callable[[Call], Answer]:
        def handle(call: Call) -> Answer:
            fields = dict(call.data or {})
            nonce = fields.pop("_nonce")
            ssecurity = fields.pop("ssecurity")
            assert ssecurity in self.ssecurities
            key = cc.signed_nonce(ssecurity, nonce)
            signature = fields.pop("signature")
            assert cc.enc_signature(str(call.url), "POST", key, fields) == signature
            plain = {k: cc.decrypt_response(key, v).decode() for k, v in fields.items()}
            assert cc.enc_signature(str(call.url), "POST", key, {"data": plain["data"]}) == plain["rc4_hash__"]
            data = json.loads(plain["data"])
            self.api_calls.append((path, data))
            result = payload(data) if callable(payload) else payload
            if isinstance(result, Reply | BaseException):
                return result
            return Reply(body=encrypt_reply(ssecurity, nonce, result))

        return handle
