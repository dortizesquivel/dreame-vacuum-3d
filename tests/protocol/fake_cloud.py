"""A fake aiohttp session standing in for the Xiaomi account and API servers."""

from __future__ import annotations

import base64
from collections.abc import Callable
from dataclasses import dataclass, field
from http.cookies import SimpleCookie
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


def fixture(name: str) -> dict[str, Any]:
    """Load fixtures/cloud/<name>.json."""
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


@dataclass
class Reply:
    """What the fake server answers; cookies are set with Path=/ on the request host."""

    status: int = 200
    body: bytes | str = b""
    headers: dict[str, str] = field(default_factory=dict)
    cookies: dict[str, str] = field(default_factory=dict)

    @classmethod
    def xiaomi(cls, data: dict[str, Any], *, prefix: str = "&&&START&&&", **kwargs: Any) -> Reply:
        """A JSON reply as the account server writes it."""
        return cls(body=prefix + json.dumps(data), **kwargs)


@dataclass
class Call:
    """A request as the server saw it; cookies merges the jar's and the per-request ones."""

    method: str
    url: URL
    data: dict[str, str] | None
    headers: dict[str, str]
    cookies: dict[str, str]
    allow_redirects: bool


type Handler = Reply | BaseException | Callable[[Call], Reply | BaseException]
type Payload = dict[str, Any] | Reply | BaseException | Callable[[Any], dict[str, Any]]


class _Response:
    def __init__(self, reply: Reply) -> None:
        self.status = reply.status
        self.headers = CIMultiDict(reply.headers)
        self._body = reply.body.encode() if isinstance(reply.body, str) else reply.body

    async def read(self) -> bytes:
        return self._body

    async def __aenter__(self) -> _Response:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None


class FakeSession:
    """Routes requests by method and URL (query ignored) to handlers, keeping a real cookie jar.

    A route with several handlers uses them in turn and then keeps answering with the last.
    api() routes decrypt the request (checking both signatures) and encrypt the reply.
    """

    def __init__(self) -> None:
        self.cookie_jar = aiohttp.CookieJar(quote_cookie=False)
        self.calls: list[Call] = []
        self.api_calls: list[tuple[str, Any]] = []
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
        url: str,
        *,
        params: dict[str, str] | None = None,
        data: dict[str, str] | None = None,
        headers: dict[str, str] | None = None,
        cookies: dict[str, str] | None = None,
        allow_redirects: bool = True,
        timeout: object = None,
    ) -> _Response:
        target = URL(url).update_query(params) if params else URL(url)
        sent = {m.key: m.value for m in self.cookie_jar.filter_cookies(target).values()} | (cookies or {})
        call = Call(method, target, dict(data) if data else None, dict(headers or {}), sent, allow_redirects)
        self.calls.append(call)
        handlers = self._routes.get((method, str(target.with_query(None))))
        if not handlers:
            raise AssertionError(f"no route for {method} {target}")
        handler = handlers.pop(0) if len(handlers) > 1 else handlers[0]
        reply = handler(call) if callable(handler) else handler
        if isinstance(reply, BaseException):
            raise reply
        for name, value in reply.cookies.items():
            cookie = SimpleCookie()
            cookie[name] = value
            cookie[name]["path"] = "/"
            self.cookie_jar.update_cookies(cookie, target)
        return _Response(reply)

    def _api_handler(self, path: str, payload: Payload) -> Callable[[Call], Reply | BaseException]:
        def handle(call: Call) -> Reply | BaseException:
            fields = dict(call.data or {})
            nonce = fields.pop("_nonce")
            assert fields.pop("ssecurity") == SSECURITY
            key = cc.signed_nonce(SSECURITY, nonce)
            signature = fields.pop("signature")
            assert cc.enc_signature(str(call.url), "POST", key, fields) == signature
            plain = {k: cc.decrypt_response(key, v).decode() for k, v in fields.items()}
            assert cc.enc_signature(str(call.url), "POST", key, {"data": plain["data"]}) == plain["rc4_hash__"]
            data = json.loads(plain["data"])
            self.api_calls.append((path, data))
            result = payload(data) if callable(payload) else payload
            if isinstance(result, Reply | BaseException):
                return result
            encrypted = cc.rc4(base64.b64decode(key), json.dumps(result).encode(), drop=cc.RC4_DROP)
            return Reply(body=base64.b64encode(encrypted).decode())

        return handle
