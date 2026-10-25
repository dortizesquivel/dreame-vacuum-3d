"""Tests of the async miIO UDP client against a fake robot."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
import socket
import time

import pytest

from custom_components.dreame_vacuum_3d.protocol import miio
from custom_components.dreame_vacuum_3d.protocol.miio_codec import MiioChecksumError, MiioError

from .fake_miio import STAMP, TOKEN_HEX


@pytest.fixture(autouse=True)
def first_id(monkeypatch) -> list[int]:
    """Pin the random first id; tests may change first_id[0] before building a client."""
    start = [1]
    monkeypatch.setattr(miio.secrets, "randbelow", lambda _n: start[0] - 1)
    return start


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


def _ids(robot) -> list[int]:
    return [request["id"] for request in robot.requests]


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


@pytest.mark.parametrize("token", ["", "00" * 15, "zz" * 16, "00" * 17])
def test_bad_token_is_rejected(token):
    with pytest.raises(ValueError, match="32 hex"):
        miio.MiioClient("127.0.0.1", token)


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
    first, second = _ids(robot)
    assert second == first + 1


async def test_first_id_is_random_in_range(fake_robot, make_client, first_id, monkeypatch):
    robot, port = fake_robot
    bounds: list[int] = []
    monkeypatch.setattr(miio.secrets, "randbelow", lambda n: bounds.append(n) or n - 1)
    client = make_client(port)
    await client.send("a")
    assert bounds == [9000]
    assert _ids(robot) == [9001]


async def test_ids_wrap(fake_robot, make_client, first_id):
    robot, port = fake_robot
    first_id[0] = 9998
    robot.drop_first = 1
    client = make_client(port, timeout=0.5)
    await client.send("a")
    await client.send("b")
    assert _ids(robot) == [9999, 100, 101]


async def test_retry_after_dropped_request(fake_robot, make_client):
    robot, port = fake_robot
    robot.drop_first = 1
    client = make_client(port, timeout=0.5)
    assert await client.send("a") == {"method": "a", "params": []}
    first, second = _ids(robot)
    assert second == first + 100
    assert robot.hellos == 2


async def test_handshake_timeout_is_retried(fake_robot, make_client):
    robot, port = fake_robot
    robot.drop_hellos = 1
    client = make_client(port, timeout=0.05, retries=1)
    assert await client.send("a") == {"method": "a", "params": []}
    assert robot.hellos == 2
    assert len(robot.requests) == 1


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


async def test_closed_port_fails_fast(socket_enabled, make_client):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    client = make_client(port, timeout=1.0, retries=5)
    started = time.monotonic()
    with pytest.raises(miio.MiioConnectionError):
        await client.send("a")
    assert time.monotonic() - started < 0.5


async def test_socket_error_on_open(make_client, monkeypatch):
    async def refuse(*_args, **_kwargs):
        raise OSError("no route to host")

    monkeypatch.setattr(asyncio.get_running_loop(), "create_datagram_endpoint", refuse)
    client = make_client(54321)
    with pytest.raises(miio.MiioConnectionError, match="no route"):
        await client.send("a")


async def test_open_times_out(make_client, monkeypatch):
    async def hang(*_args, **_kwargs):
        await asyncio.sleep(10)

    monkeypatch.setattr(asyncio.get_running_loop(), "create_datagram_endpoint", hang)
    client = make_client(54321, timeout=0.05)
    with pytest.raises(miio.MiioConnectionError):
        await client.send("a")


async def test_wrong_token_is_reported(fake_robot, make_client):
    robot, port = fake_robot
    robot.reply_token = bytes(16)
    client = make_client(port, timeout=0.05, retries=1)
    with pytest.raises(MiioChecksumError, match="check the token"):
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


async def test_non_dict_error_reply_raises(fake_robot, make_client):
    robot, port = fake_robot
    robot.error = "boom"
    client = make_client(port)
    with pytest.raises(miio.MiioCommandError) as info:
        await client.send("a")
    assert (info.value.code, info.value.message) == (None, "boom")


def test_errors_are_miio_errors():
    for error in (miio.MiioTimeoutError, miio.MiioCommandError, miio.MiioConnectionError):
        assert issubclass(error, MiioError)


async def test_concurrent_sends_are_serialised(fake_robot, make_client):
    robot, port = fake_robot
    robot.delay = 0.005
    client = make_client(port)
    results = await asyncio.gather(*(client.send("m", [i]) for i in range(10)))
    assert results == [{"method": "m", "params": [i]} for i in range(10)]
    exchanges = [entry for entry in robot.log if entry[0] != "hello"]
    assert exchanges == [entry for id_ in _ids(robot) for entry in (("request", id_), ("reply", id_))]


async def test_junk_and_full_packets_are_skipped(fake_robot, make_client):
    robot, port = fake_robot
    robot.noise = True
    client = make_client(port)
    assert await client.send("a") == {"method": "a", "params": []}
    assert await client.send("b") == {"method": "b", "params": []}
    assert robot.stamps == [STAMP, STAMP]


async def test_late_reply_is_ignored(fake_robot, make_client):
    robot, port = fake_robot
    client = make_client(port, timeout=0.2, retries=0)
    await client.send("warm-up")
    robot.hold = True
    with pytest.raises(miio.MiioTimeoutError):
        await client.send("a")
    [stale] = robot.take()

    task = asyncio.create_task(client.send("b"))
    await robot.wait_held()
    robot.release(stale)
    await robot.wait_held()
    robot.release(stale)
    assert await task == {"method": "b", "params": []}
    assert robot.hellos == 2
    assert robot.stamps[-1] == STAMP


async def test_reply_left_over_from_a_timeout_is_drained(fake_robot, make_client):
    robot, port = fake_robot
    robot.hold = True
    client = make_client(port, timeout=0.2, retries=0)
    with pytest.raises(miio.MiioTimeoutError):
        await client.send("a")
    robot.hold = False
    robot.release()
    await asyncio.sleep(0.02)
    assert await client.send("b") == {"method": "b", "params": []}
    assert robot.stamps[-1] == STAMP


async def test_stale_socket_error_is_drained(fake_robot, make_client):
    _robot, port = fake_robot
    client = make_client(port)
    await client.send("a")
    client._conn[1].error_received(ConnectionRefusedError("late ICMP for an old datagram"))
    assert await client.send("b") == {"method": "b", "params": []}


async def test_cancelled_send_then_send(fake_robot, make_client):
    robot, port = fake_robot
    client = make_client(port)
    await client.send("warm-up")
    robot.hold = True
    task = asyncio.create_task(client.send("a"))
    await robot.wait_held()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    robot.release()
    robot.hold = False
    assert await client.send("b") == {"method": "b", "params": []}


async def test_close_during_send_fails_it(fake_robot, make_client):
    robot, port = fake_robot
    client = make_client(port, timeout=1.0)
    await client.send("warm-up")
    robot.hold = True
    task = asyncio.create_task(client.send("a"))
    await robot.wait_held()
    await client.close()
    with pytest.raises(miio.MiioConnectionError):
        await task
    assert len(robot.requests) == 2
    robot.hold = False
    assert await client.send("b") == {"method": "b", "params": []}


async def test_dead_socket_is_reopened(fake_robot, make_client):
    robot, port = fake_robot
    client = make_client(port, timeout=1.0)
    await client.send("warm-up")
    robot.hold = True
    task = asyncio.create_task(client.send("a"))
    await robot.wait_held()
    robot.hold = False
    robot.take()
    client._conn[0].abort()
    assert await task == {"method": "a", "params": []}
    assert robot.hellos == 2


async def test_dead_socket_is_reopened_once(fake_robot, make_client, monkeypatch):
    robot, port = fake_robot
    client = make_client(port, timeout=1.0)
    await client.send("warm-up")
    robot.hold = True
    task = asyncio.create_task(client.send("a"))
    await robot.wait_held()
    original = client._handshake

    async def handshake_and_die(conn):
        conn[0].abort()
        return await original(conn)

    monkeypatch.setattr(client, "_handshake", handshake_and_die)
    client._conn[0].abort()
    with pytest.raises(miio.MiioConnectionError):
        await task


async def test_close_then_send_reopens(fake_robot, make_client):
    robot, port = fake_robot
    client = make_client(port)
    await client.send("a")
    await client.close()
    await client.close()
    assert await client.send("b") == {"method": "b", "params": []}
    assert len(robot.requests) == 2
