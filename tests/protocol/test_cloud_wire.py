"""The cloud client over a real aiohttp session, against a local TLS server posing as the API host."""

from __future__ import annotations

from collections.abc import AsyncIterator
import datetime
import socket
import ssl

import aiohttp
from aiohttp import web
from aiohttp.abc import AbstractResolver, ResolveResult
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID
import pytest

from custom_components.dreame_vacuum_3d.protocol.cloud import CloudAuth, XiaomiCloud

from .fake_cloud import SSECURITY, encrypt_reply

SERVICE_TOKEN = "c2VydmljZS90b2tlbg/a+b=="
AUTH = CloudAuth(user_id="1000001", ssecurity=SSECURITY, service_token=SERVICE_TOKEN, client_id="abcdefghijklmnop")


class _LocalResolver(AbstractResolver):
    """Resolves every host to the local test server."""

    def __init__(self, port: int) -> None:
        self._port = port

    async def resolve(self, host: str, port: int = 0, family: int = socket.AF_INET) -> list[ResolveResult]:
        return [
            ResolveResult(hostname=host, host="127.0.0.1", port=self._port, family=socket.AF_INET, proto=0, flags=0)
        ]

    async def close(self) -> None:
        return None


def _server_tls(tmp_path) -> ssl.SSLContext:
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "de.api.io.mi.com")])
    now = datetime.datetime.now(datetime.UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=5))
        .not_valid_after(now + datetime.timedelta(hours=1))
        .sign(key, hashes.SHA256())
    )
    (tmp_path / "cert.pem").write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    (tmp_path / "key.pem").write_bytes(
        key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    )
    context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    context.load_cert_chain(tmp_path / "cert.pem", tmp_path / "key.pem")
    return context


@pytest.fixture
async def api_server(socket_enabled, tmp_path) -> AsyncIterator[tuple[int, list[web.Request]]]:
    """A local HTTPS server answering every POST with an encrypted {"result": "ok"}."""
    seen: list[web.Request] = []

    async def handle(request: web.Request) -> web.Response:
        seen.append(request)
        fields = await request.post()
        return web.Response(text=encrypt_reply(SSECURITY, str(fields["_nonce"]), {"code": 0, "result": "ok"}))

    app = web.Application()
    app.router.add_post("/{tail:.*}", handle)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0, ssl_context=_server_tls(tmp_path))
    await site.start()
    try:
        yield runner.addresses[0][1], seen
    finally:
        await runner.cleanup()


async def test_cookies_go_out_unquoted(api_server):
    port, seen = api_server
    connector = aiohttp.TCPConnector(resolver=_LocalResolver(port), ssl=False)
    async with aiohttp.ClientSession(connector=connector, cookie_jar=aiohttp.CookieJar(quote_cookie=False)) as session:
        assert await XiaomiCloud(session, "de", AUTH).rpc("1001", "get_properties", []) == "ok"

    [request] = seen
    assert request.path == "/app/v2/home/rpc/1001"
    cookie = request.headers["Cookie"]
    assert f"serviceToken={SERVICE_TOKEN}" in cookie.split("; ")
    assert '"' not in cookie
