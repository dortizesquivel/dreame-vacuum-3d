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


def echo(method: str, params: Any) -> Any:
    """Default handler: answer with what was asked."""
    return {"method": method, "params": params}


class FakeRobot(asyncio.DatagramProtocol):
    """Answers HELLO with a header and requests with handler(method, params).

    Options: drop_first (leave the first n requests unanswered), wrong_id (answer
    with id + 1), error (answer with this error), mute (answer nothing, not even
    HELLO), noise (send junk, and a foreign reply, before each answer) and delay
    (seconds before answering a request).

    log records ("hello",), ("request", id) and ("reply", id) in order;
    requests holds the decoded payloads and stamps the header stamps.
    """

    def __init__(self, handler: Callable[[str, Any], Any] = echo) -> None:
        self.handler = handler
        self.drop_first = 0
        self.wrong_id = False
        self.error: dict[str, Any] | None = None
        self.mute = False
        self.noise = False
        self.delay = 0.0
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
            if self.noise:
                self.transport.sendto(b"junk", addr)
            head = codec.HELLO[:4] + bytes(4) + DEVICE_ID.to_bytes(4) + STAMP.to_bytes(4) + b"\xff" * 16
            self._reply(head, addr)
            return
        header, payload = codec.parse(TOKEN, data)
        self.requests.append(payload)
        self.stamps.append(header.stamp)
        self.log.append(("request", payload["id"]))
        if len(self.requests) <= self.drop_first:
            return
        reply_id = payload["id"] + 1 if self.wrong_id else payload["id"]
        if self.error is not None:
            body: dict[str, Any] = {"id": reply_id, "error": self.error}
        else:
            body = {"id": reply_id, "result": self.handler(payload["method"], payload["params"])}
        packet = codec.build(TOKEN, DEVICE_ID, STAMP, body)
        if self.delay:
            self._timers.append(asyncio.get_running_loop().call_later(self.delay, self._reply, packet, addr, reply_id))
        else:
            self._reply(packet, addr, reply_id)

    def _reply(self, packet: bytes, addr: tuple[str, int], reply_id: int | None = None) -> None:
        assert self.transport is not None
        if reply_id is not None:
            self.log.append(("reply", reply_id))
            if self.noise:
                self.transport.sendto(b"junk", addr)
                self.transport.sendto(codec.build(TOKEN, DEVICE_ID, STAMP, {"id": reply_id - 1, "result": 0}), addr)
        self.transport.sendto(packet, addr)

    def close(self) -> None:
        for timer in self._timers:
            timer.cancel()
        if self.transport is not None:
            self.transport.close()
