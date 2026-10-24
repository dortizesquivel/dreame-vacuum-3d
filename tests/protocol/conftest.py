"""Fixtures for the protocol tests (pure Python, no Home Assistant)."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import pytest

from .fake_miio import FakeRobot


@pytest.fixture
async def fake_robot(socket_enabled) -> AsyncIterator[tuple[FakeRobot, int]]:
    """A fake robot listening on 127.0.0.1; yields it and its port."""
    # Home Assistant's plugin blocks sockets after pytest-socket reads the
    # enable_socket mark, so only the socket_enabled fixture lets them through.
    transport, robot = await asyncio.get_running_loop().create_datagram_endpoint(FakeRobot, local_addr=("127.0.0.1", 0))
    try:
        yield robot, transport.get_extra_info("sockname")[1]
    finally:
        robot.close()
