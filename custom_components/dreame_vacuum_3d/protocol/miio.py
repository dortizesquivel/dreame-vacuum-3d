"""Async miIO client: JSON-RPC over encrypted UDP datagrams, one request at a time."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from time import monotonic
from typing import Any

from . import miio_codec as codec

HANDSHAKE_TTL = 120.0
RETRY_ID_STEP = 100


class MiioTimeoutError(codec.MiioError):
    """The robot didn't answer in time, retries included."""


class MiioCommandError(codec.MiioError):
    """The robot answered with an error."""

    def __init__(self, code: Any, message: Any) -> None:
        super().__init__(f"{message} (code {code})")
        self.code = code
        self.message = message


class _Protocol(asyncio.DatagramProtocol):
    def __init__(self) -> None:
        self.queue: asyncio.Queue[bytes] = asyncio.Queue()

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        self.queue.put_nowait(data)


type _Conn = tuple[asyncio.DatagramTransport, _Protocol]


class MiioClient:
    """Talks to one robot; the endpoint and handshake are set up on demand."""

    def __init__(self, host: str, token: str, *, port: int = 54321, timeout: float = 2.0, retries: int = 2) -> None:
        self._addr = (host, port)
        self._token = bytes.fromhex(token)
        self._timeout = timeout
        self._retries = retries
        self._lock = asyncio.Lock()
        self._conn: _Conn | None = None
        self._session: codec.Header | None = None
        self._handshake_at = 0.0
        self._id = 0

    async def send(self, method: str, params: list | dict | None = None) -> Any:
        """Call method on the robot and return its result."""
        async with self._lock:
            conn = await self._open()
            self._id += 1
            attempt = 0
            while True:
                try:
                    if self._session is None or monotonic() - self._handshake_at > HANDSHAKE_TTL:
                        self._session = await self._handshake(conn)
                    return await self._request(conn, self._session, method, params)
                except MiioTimeoutError:
                    if attempt == self._retries:
                        raise
                    attempt += 1
                    # Robots ignore an id they've already seen.
                    self._session = None
                    self._id += RETRY_ID_STEP

    async def close(self) -> None:
        """Close the socket; the next send opens a new one."""
        if self._conn is not None:
            self._conn[0].close()
        self._conn = self._session = None

    async def _open(self) -> _Conn:
        if self._conn is None or self._conn[0].is_closing():
            self._conn = await asyncio.get_running_loop().create_datagram_endpoint(_Protocol, remote_addr=self._addr)
            self._session = None
        return self._conn

    async def _handshake(self, conn: _Conn) -> codec.Header:
        def accept(packet: bytes) -> codec.Header | None:
            try:
                return codec.parse_header(packet)
            except codec.MiioError:
                return None

        header = await self._exchange(conn, codec.HELLO, accept)
        self._handshake_at = monotonic()
        return header

    async def _request(self, conn: _Conn, session: codec.Header, method: str, params: list | dict | None) -> Any:
        request_id = self._id
        stamp = session.stamp + int(monotonic() - self._handshake_at)
        payload = {"id": request_id, "method": method, "params": params if params is not None else []}
        packet = codec.build(self._token, session.device_id, stamp, payload)

        def accept(packet: bytes) -> dict[str, Any] | None:
            try:
                _header, reply = codec.parse(self._token, packet)
            except codec.MiioError:
                return None
            if isinstance(reply, dict) and reply.get("id") == request_id:
                return reply
            return None

        reply = await self._exchange(conn, packet, accept)
        if "error" in reply:
            error = reply["error"]
            raise MiioCommandError(error.get("code"), error.get("message"))
        return reply.get("result")

    async def _exchange[T](self, conn: _Conn, packet: bytes, accept: Callable[[bytes], T | None]) -> T:
        """Send packet and wait for the first reply that accept() doesn't reject."""
        transport, protocol = conn
        transport.sendto(packet)
        try:
            async with asyncio.timeout(self._timeout):
                while (result := accept(await protocol.queue.get())) is None:
                    pass
        except TimeoutError as err:
            raise MiioTimeoutError(f"no reply from {self._addr[0]}") from err
        return result
