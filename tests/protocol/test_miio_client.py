"""Tests of the async miIO UDP client against a fake robot."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable

import pytest

from custom_components.dreame_vacuum_3d.protocol import miio
from custom_components.dreame_vacuum_3d.protocol.miio_codec import MiioError

from .fake_miio import STAMP, TOKEN_HEX


@pytest.fixture
async def make_client() -> AsyncIterator[Callable[..., miio.MiioClient]]:
    """Build clients to 127.0.0.1 and close them after the test."""
    clients: list[miio.MiioClient] = []

    def make(port: int, **kwargs) -> miio.MiioClient:
        client = miio.MiioClient("127.0.0.1", TOKEN_HEX, port=port, **kwargs)
        clients.append(client)
        return client

    yield make
    for client in clients:
        await client.close()


@pytest.fixture
def clock(monkeypatch) -> list[float]:
    """Replace the client's clock; tests move it by changing clock[0]."""
    now = [100.0]
    monkeypatch.setattr(miio, "monotonic", lambda: now[0])
    return now


async def test_send_returns_result_after_handshake(fake_robot, make_client):
    robot, port = fake_robot
    client = make_client(port)
    assert await client.send("miIO.info") == {"method": "miIO.info", "params": []}
    assert robot.log[0] == ("hello",)
    assert robot.requests == [{"id": robot.requests[0]["id"], "method": "miIO.info", "params": []}]


async def test_params_are_passed_through(fake_robot, make_client):
    _robot, port = fake_robot
    client = make_client(port)
    assert await client.send("set", {"a": 1}) == {"method": "set", "params": {"a": 1}}


async def test_handshake_is_reused_then_renewed(fake_robot, make_client, clock):
    robot, port = fake_robot
    client = make_client(port)
    await client.send("a")
    await client.send("b")
    assert robot.hellos == 1
    clock[0] += 121
    await client.send("c")
    assert robot.hellos == 2


async def test_stamp_follows_elapsed_time(fake_robot, make_client, clock):
    robot, port = fake_robot
    client = make_client(port)
    await client.send("a")
    clock[0] += 5.7
    await client.send("b")
    clock[0] += 60
    await client.send("c")
    assert robot.stamps == [STAMP, STAMP + 5, STAMP + 65]


async def test_ids_increase(fake_robot, make_client):
    robot, port = fake_robot
    client = make_client(port)
    await client.send("a")
    await client.send("b")
    first, second = (request["id"] for request in robot.requests)
    assert second == first + 1


async def test_retry_after_dropped_request(fake_robot, make_client):
    robot, port = fake_robot
    robot.drop_first = 1
    client = make_client(port, timeout=0.1)
    assert await client.send("a") == {"method": "a", "params": []}
    first, second = (request["id"] for request in robot.requests)
    assert second == first + 100
    assert robot.hellos == 2


async def test_timeout_when_robot_never_answers(fake_robot, make_client):
    robot, port = fake_robot
    robot.mute = True
    client = make_client(port, timeout=0.05, retries=1)
    with pytest.raises(miio.MiioTimeoutError):
        await client.send("a")


async def test_timeout_when_requests_are_dropped(fake_robot, make_client):
    robot, port = fake_robot
    robot.drop_first = 99
    client = make_client(port, timeout=0.05, retries=1)
    with pytest.raises(miio.MiioTimeoutError):
        await client.send("a")
    assert len(robot.requests) == 2


async def test_timeout_when_nothing_listens(fake_robot, make_client):
    robot, port = fake_robot
    robot.close()
    client = make_client(port, timeout=0.05, retries=0)
    with pytest.raises(miio.MiioTimeoutError):
        await client.send("a")


async def test_foreign_reply_is_not_accepted(fake_robot, make_client):
    robot, port = fake_robot
    robot.wrong_id = True
    client = make_client(port, timeout=0.05, retries=0)
    with pytest.raises(miio.MiioTimeoutError):
        await client.send("a")


async def test_error_reply_raises(fake_robot, make_client):
    robot, port = fake_robot
    robot.error = {"code": -1, "message": "x"}
    client = make_client(port)
    with pytest.raises(miio.MiioCommandError) as info:
        await client.send("a")
    assert (info.value.code, info.value.message) == (-1, "x")


def test_errors_are_miio_errors():
    assert issubclass(miio.MiioTimeoutError, MiioError)
    assert issubclass(miio.MiioCommandError, MiioError)


async def test_concurrent_sends_are_serialised(fake_robot, make_client):
    robot, port = fake_robot
    robot.delay = 0.005
    client = make_client(port)
    results = await asyncio.gather(*(client.send("m", [i]) for i in range(10)))
    assert results == [{"method": "m", "params": [i]} for i in range(10)]
    exchanges = [entry for entry in robot.log if entry[0] != "hello"]
    ids = [request["id"] for request in robot.requests]
    assert exchanges == [entry for id_ in ids for entry in (("request", id_), ("reply", id_))]


async def test_junk_and_foreign_replies_are_skipped(fake_robot, make_client):
    robot, port = fake_robot
    robot.noise = True
    client = make_client(port)
    assert await client.send("a") == {"method": "a", "params": []}
    assert len(robot.requests) == 1


async def test_close_then_send_reopens(fake_robot, make_client):
    robot, port = fake_robot
    client = make_client(port)
    await client.send("a")
    await client.close()
    await client.close()
    assert await client.send("b") == {"method": "b", "params": []}
    assert len(robot.requests) == 2
