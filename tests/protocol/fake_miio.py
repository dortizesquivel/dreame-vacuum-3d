"""A fake miIO robot on a local UDP socket, for testing the client."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

from custom_components.dreame_vacuum_3d.protocol import miio_codec as codec

TOKEN_HEX = "00112233445566778899aabbccddeeff"
TOKEN = bytes.fromhex(TOKEN_HEX)
DEVICE_ID = 0x1234
STAMP = 1000
# Request replies carry another stamp, so a handshake that takes one shows in later requests.
REPLY_STAMP = 9000


def echo(method: str, params: Any) -> Any:
    """Default handler: answer with what was asked."""
    return {"method": method, "params": params}


class FakeRobot(asyncio.DatagramProtocol):
    """Answers HELLO with a header and requests with handler(method, params).

    Options: drop_first (leave the first n requests unanswered), drop_hellos
    (same for HELLO), wrong_id (answer with id + 1), error (answer with this
    error), mute (answer nothing, not even HELLO), noise (send junk, and a
    full packet, before each answer), delay (seconds before answering a
    request), reply_token (sign replies with this token) and hold (keep every
    answer in held until release()).

    log records ("hello",), ("request", id) and ("reply", id) in order;
    requests holds the decoded payloads and stamps the header stamps.
    """

    def __init__(self, handler: Callable[[str, Any], Any] = echo) -> None:
        self.handler = handler
        self.drop_first = 0
        self.drop_hellos = 0
        self.wrong_id = False
        self.error: Any = None
        self.mute = False
        self.noise = False
        self.delay = 0.0
        self.reply_token = TOKEN
        self.hold = False
        self.held: list[tuple[bytes, tuple[str, int]]] = []
        self._holding = asyncio.Event()
        self.hellos = 0
        self.requests: list[dict[str, Any]] = []
        self.stamps: list[int] = []
        self.log: list[tuple[Any, ...]] = []
        self.transport: asyncio.DatagramTransport | None = None
        self._timers: list[asyncio.TimerHandle] = []

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        assert isinstance(transport, asyncio.DatagramTransport)
        self.transport = transport

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        assert self.transport is not None
        if self.mute:
            return
        if data == codec.HELLO:
            self.hellos += 1
            self.log.append(("hello",))
            if self.hellos <= self.drop_hellos:
                return
            if self.noise:
                self.transport.sendto(bytes(codec.HEADER_LEN), addr)
                self.transport.sendto(self.reply(0), addr)
            head = codec.HELLO[:4] + bytes(4) + DEVICE_ID.to_bytes(4) + STAMP.to_bytes(4) + b"\xff" * 16
            self._send(head, addr)
            return
        header, payload = codec.parse(TOKEN, data)
        self.requests.append(payload)
        self.stamps.append(header.stamp)
        self.log.append(("request", payload["id"]))
        if len(self.requests) <= self.drop_first:
            return
        reply_id = payload["id"] + 1 if self.wrong_id else payload["id"]
        if self.error is not None:
            packet = self.reply(reply_id, error=self.error)
        else:
            packet = self.reply(reply_id, result=self.handler(payload["method"], payload["params"]))
        if self.delay:
            self._timers.append(asyncio.get_running_loop().call_later(self.delay, self._answer, packet, addr, reply_id))
        else:
            self._answer(packet, addr, reply_id)

    def reply(self, reply_id: int, **body: Any) -> bytes:
        """A request reply as the robot would send it."""
        return codec.build(self.reply_token, DEVICE_ID, REPLY_STAMP, {"id": reply_id, **body})

    def _answer(self, packet: bytes, addr: tuple[str, int], reply_id: int) -> None:
        assert self.transport is not None
        self.log.append(("reply", reply_id))
        if self.noise:
            self.transport.sendto(b"junk", addr)
            self.transport.sendto(self.reply(reply_id - 1, result=0), addr)
        self._send(packet, addr)

    def _send(self, packet: bytes, addr: tuple[str, int]) -> None:
        assert self.transport is not None
        if self.hold:
            self.held.append((packet, addr))
            self._holding.set()
        else:
            self.transport.sendto(packet, addr)

    async def wait_held(self) -> None:
        """Wait until an answer is held."""
        async with asyncio.timeout(1):
            await self._holding.wait()

    def take(self) -> list[tuple[bytes, tuple[str, int]]]:
        """Remove the held answers without sending them."""
        held, self.held = self.held, []
        self._holding.clear()
        return held

    def release(self, *extra: tuple[bytes, tuple[str, int]]) -> None:
        """Send extra answers, then the held ones, in order."""
        assert self.transport is not None
        for packet, addr in [*extra, *self.take()]:
            self.transport.sendto(packet, addr)

    def close(self) -> None:
        for timer in self._timers:
            timer.cancel()
        if self.transport is not None:
            self.transport.close()
