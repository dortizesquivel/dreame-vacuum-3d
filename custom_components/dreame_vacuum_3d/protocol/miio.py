"""Async miIO client: JSON-RPC over encrypted UDP datagrams, one request at a time."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
import secrets
from time import monotonic
from typing import Any

from . import miio_codec as codec

HANDSHAKE_TTL = 120.0
RETRY_ID_STEP = 100
MAX_ID = 9999


class MiioTimeoutError(codec.MiioError):
    """The robot didn't answer in time, retries included."""


class MiioConnectionError(codec.MiioError):
    """The socket couldn't be opened, failed, or was closed."""


class MiioCommandError(codec.MiioError):
    """The robot answered with an error."""

    def __init__(self, code: Any, message: Any) -> None:
        super().__init__(f"{message} (code {code})")
        self.code = code
        self.message = message


class _Protocol(asyncio.DatagramProtocol):
    """Queues datagrams; socket errors are queued too, to wake the waiting request."""

    def __init__(self) -> None:
        self.queue: asyncio.Queue[bytes | Exception] = asyncio.Queue()

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        self.queue.put_nowait(data)

    def error_received(self, exc: Exception) -> None:
        self.queue.put_nowait(exc)

    def connection_lost(self, exc: Exception | None) -> None:
        self.queue.put_nowait(exc or ConnectionAbortedError("socket closed"))


type _Conn = tuple[asyncio.DatagramTransport, _Protocol]


class MiioClient:
    """Talks to one robot; the endpoint and handshake are set up on demand."""

    def __init__(self, host: str, token: str, *, port: int = 54321, timeout: float = 2.0, retries: int = 2) -> None:
        try:
            self._token = bytes.fromhex(token)
        except ValueError:
            self._token = b""
        if len(self._token) != 16:
            raise ValueError("the token must be 32 hex characters")
        self._addr = (host, port)
        self._timeout = timeout
        self._retries = retries
        self._lock = asyncio.Lock()
        self._conn: _Conn | None = None
        self._session: codec.Header | None = None
        self._handshake_at = 0.0
        self._id = secrets.randbelow(9000) + 1
        self._checksum_failed = False

    async def send(self, method: str, params: list | dict | None = None) -> Any:
        """Call method on the robot and return its result.

        A lost reply is retried, so the robot may run the command twice (inherent to miIO).
        """
        async with self._lock:
            self._next_id(1)
            self._checksum_failed = False
            attempt = 0
            reconnected = False
            while True:
                try:
                    conn = await self._open()
                    if self._session is None or monotonic() - self._handshake_at > HANDSHAKE_TTL:
                        self._session = await self._handshake(conn)
                    return await self._request(conn, self._session, method, params)
                except (MiioConnectionError, MiioTimeoutError) as err:
                    self._session = None
                    if isinstance(err, MiioConnectionError):
                        # Reopen once a socket that died under us. A refusal (ICMP) won't change in
                        # milliseconds, and a second probe often gets no ICMP back (rate limits).
                        if reconnected or self._conn is None or not self._conn[0].is_closing():
                            raise
                        reconnected = True
                    if attempt == self._retries:
                        if isinstance(err, MiioTimeoutError) and self._checksum_failed:
                            raise codec.MiioChecksumError("replies failed the checksum; check the token") from err
                        raise
                    attempt += 1
                    # Robots ignore an id they've already seen.
                    self._next_id(RETRY_ID_STEP)

    async def close(self) -> None:
        """Close the socket; the next send opens a new one."""
        if self._conn is not None:
            self._conn[0].close()
        self._conn = self._session = None

    def _next_id(self, step: int) -> int:
        self._id = (self._id + step - 1) % MAX_ID + 1
        return self._id

    async def _open(self) -> _Conn:
        if self._conn is not None and not self._conn[0].is_closing():
            return self._conn
        self._conn = self._session = None
        try:
            async with asyncio.timeout(self._timeout):
                self._conn = await asyncio.get_running_loop().create_datagram_endpoint(
                    _Protocol, remote_addr=self._addr
                )
        except (OSError, TimeoutError) as err:
            raise MiioConnectionError(f"can't open a socket to {self._addr[0]}: {err}") from err
        return self._conn

    async def _handshake(self, conn: _Conn) -> codec.Header:
        def accept(data: bytes) -> codec.Header | None:
            # Only a bare header: a late reply to an old request would carry a stale stamp.
            if len(data) != codec.HEADER_LEN:
                return None
            try:
                return codec.parse_header(data)
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

        def accept(data: bytes) -> dict[str, Any] | None:
            try:
                _header, reply = codec.parse(self._token, data)
            except codec.MiioChecksumError:
                self._checksum_failed = True
                return None
            except codec.MiioError:
                return None
            if isinstance(reply, dict) and reply.get("id") == request_id:
                return reply
            return None

        reply = await self._exchange(conn, packet, accept)
        if "error" in reply:
            error = reply["error"]
            if isinstance(error, dict):
                raise MiioCommandError(error.get("code"), error.get("message"))
            raise MiioCommandError(None, str(error))
        return reply.get("result")

    async def _exchange[T](self, conn: _Conn, packet: bytes, accept: Callable[[bytes], T | None]) -> T:
        """Send packet and wait for the first reply that accept() doesn't reject."""
        transport, protocol = conn
        while not protocol.queue.empty():
            protocol.queue.get_nowait()
        transport.sendto(packet)
        try:
            async with asyncio.timeout(self._timeout):
                while True:
                    item = await protocol.queue.get()
                    if isinstance(item, Exception):
                        raise MiioConnectionError(f"socket error talking to {self._addr[0]}: {item}") from item
                    if (result := accept(item)) is not None:
                        return result
        except TimeoutError as err:
            raise MiioTimeoutError(f"no reply from {self._addr[0]}") from err
