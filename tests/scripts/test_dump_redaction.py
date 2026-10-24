"""Tests of scripts/dump_device.py: redaction, the property readers and the command line."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
from typing import Any

import pytest

from custom_components.dreame_vacuum_3d.device.capabilities import load
from custom_components.dreame_vacuum_3d.protocol import miio_codec as codec
from custom_components.dreame_vacuum_3d.protocol.miio import MiioCommandError

from ..protocol.fake_miio import DEVICE_ID, STAMP, TOKEN, TOKEN_HEX, FakeRobot

SCRIPT = Path(__file__).parents[2] / "scripts" / "dump_device.py"
_spec = importlib.util.spec_from_file_location("dump_device", SCRIPT)
assert _spec and _spec.loader
dump = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(dump)

CAPS = load("dreame.vacuum.p2114a")
DEVICE = {"did": "123456789", "mac": "AA:BB:CC:DD:EE:FF", "token": TOKEN_HEX, "localip": "192.168.1.50"}


def item(key: str | None, value: Any, siid: int = 1, piid: int = 1) -> dict[str, Any]:
    return {"key": key, "siid": siid, "piid": piid, "code": 0, "value": value}


def values(items: list[dict[str, Any]]) -> list[Any]:
    return [i["value"] for i in items]


def test_redact_drops_identifier_props() -> None:
    items = [
        item("serial_number", "R2216123456"),
        item("serial", 12345),
        item("wifi_ssid", "Home"),
        item("battery_level", 87),
        item("save_water_tips", True),
        item(None, "kept"),
    ]
    assert values(dump.redact(items)) == ["", None, "", 87, True, "kept"]


@pytest.mark.parametrize(
    "value",
    [
        "aa:bb:cc:dd:ee:ff",
        "mac AA-BB-CC-DD-EE-01",
        "10.0.0.7",
        "at 192.168.1.2:54321",
        "00112233445566778899aabbccddeeff",
        '{"ssid": "Home", "rssi": -40}',
        '{"localIp":"x"}',
    ],
)
def test_redact_blanks_identifier_shapes(value: str) -> None:
    assert values(dump.redact([item("clean_log_file_name", value)])) == [""]


@pytest.mark.parametrize("value", ["4.3.9_1416", "1.2.3", '{"mode": 2}', "2026-10-24 12:00"])
def test_redact_keeps_ordinary_strings(value: str) -> None:
    assert values(dump.redact([item("firmware_version", value)])) == [value]


def test_redact_blanks_the_device_ids() -> None:
    items = [
        item("object_name", "1001/123456789/0"),
        item("clean_log_file_name", "log_aabbccddeeff.tar"),
        item("custom", 123456789),
        item("other", 12345),
    ]
    assert values(dump.redact(items, DEVICE)) == ["", "", None, 12345]
    assert values(dump.redact(items)) == ["1001/123456789/0", "log_aabbccddeeff.tar", 123456789, 12345]


def test_redact_keeps_the_structure() -> None:
    items = [item("serial_number", "R22", 4, 14) | {"code": -4001}, item("state", 1, 2, 1)]
    original = json.loads(json.dumps(items))
    redacted = dump.redact(items, DEVICE)
    assert items == original
    assert [list(i) for i in redacted] == [list(i) for i in items]
    assert [(i["key"], i["siid"], i["piid"], i["code"]) for i in redacted] == [
        ("serial_number", 4, 14, -4001),
        ("state", 2, 1, 0),
    ]


def test_redact_cli_reads_the_sibling_device_json(tmp_path: Path, capsys) -> None:
    (tmp_path / "device.json").write_text(json.dumps(DEVICE))
    (tmp_path / "properties.json").write_text(json.dumps([item("object_name", "x/123456789"), item("state", 2)]))
    assert dump.main(["--redact", str(tmp_path / "properties.json")]) == 0
    assert values(json.loads(capsys.readouterr().out)) == ["", 2]


def test_redact_cli_rejects_other_json(tmp_path: Path, capsys) -> None:
    (tmp_path / "properties.json").write_text("{}")
    assert dump.main(["--redact", str(tmp_path / "properties.json")]) == 1
    assert "not a list of property items" in capsys.readouterr().err


class FakeSend:
    """get_properties replies: odd piids answer with their piid, even ones fail, piid 7 is left out."""

    def __init__(self) -> None:
        self.calls: list[list[dict[str, Any]]] = []

    async def __call__(self, method: str, params: Any) -> Any:
        assert method == "get_properties"
        self.calls.append(params)
        reply = []
        for p in params:
            if p["piid"] == 7:
                continue
            ok = p["piid"] % 2 == 1
            reply.append({"did": p["did"], "siid": p["siid"], "piid": p["piid"], "code": 0 if ok else -4001})
            if ok:
                reply[-1]["value"] = p["piid"]
        return reply


async def test_read_raw_keeps_every_code_in_chunks() -> None:
    send = FakeSend()
    props = [(4, piid) for piid in range(1, 21)]
    items = await dump.read_raw(send, props, CAPS)
    assert [len(c) for c in send.calls] == [15, 5]
    assert send.calls[0][0] == {"did": "4.1", "siid": 4, "piid": 1}
    assert [(i["siid"], i["piid"]) for i in items] == props
    assert items[0] == {"key": "status", "siid": 4, "piid": 1, "code": 0, "value": 1}
    assert items[1] == {"key": "cleaning_time", "siid": 4, "piid": 2, "code": -4001, "value": None}
    assert items[6]["code"] is None
    assert items[13]["key"] == "serial_number"


async def test_read_raw_records_a_refused_batch() -> None:
    async def refuse(method: str, params: Any) -> Any:
        raise MiioCommandError(-9999, "user ack timeout")

    items = await dump.read_raw(refuse, [(2, 1), (2, 2)], CAPS)
    assert [(i["key"], i["code"], i["value"]) for i in items] == [("state", -9999, None), ("error", -9999, None)]


async def test_read_raw_tolerates_a_malformed_reply() -> None:
    async def junk(method: str, params: Any) -> Any:
        return {"oops": True}

    assert [i["code"] for i in await dump.read_raw(junk, [(2, 1)], CAPS)] == [None]


async def test_scan_sweeps_each_siid_in_chunks_and_keeps_hits() -> None:
    send = FakeSend()
    progress: list[str] = []
    hits = await dump.scan(send, CAPS, siids=range(4, 6), progress=progress.append)
    assert len(send.calls) == 16
    assert all(len(c) <= 15 for c in send.calls)
    assert sorted(p["piid"] for c in send.calls[:8] for p in c) == list(range(1, 111))
    assert all(h["code"] == 0 and h["piid"] % 2 == 1 and h["piid"] != 7 for h in hits)
    assert len(hits) == 2 * 54
    assert hits[0]["key"] == "status"
    assert any(h["key"] is None for h in hits)
    assert progress[0].startswith("scan siid 4: 54 answered, ")
    assert len(progress) == 2


def test_pick_device_filters_and_asks() -> None:
    devices = [
        {"did": "1", "model": "dreame.vacuum.p2114a", "localip": "10.0.0.2", "name": "X10+"},
        {"did": "2", "model": "xiaomi.vacuum.c102", "localip": "10.0.0.3", "name": "Other"},
        {"did": "3", "model": "lumi.plug.v1", "localip": "10.0.0.4"},
    ]
    assert dump.pick_device(devices, model="xiaomi.vacuum.c102")["did"] == "2"
    assert dump.pick_device(devices, host="10.0.0.2")["did"] == "1"
    answers = iter(["9", "x", "2"])
    assert dump.pick_device(devices, host="10.9.9.9", ask=lambda _p: next(answers))["did"] == "2"
    with pytest.raises(dump.DumpError, match=r"with model dreame\.vacuum\.nope"):
        dump.pick_device(devices, model="dreame.vacuum.nope")


def test_capabilities_of_a_xiaomi_model_use_the_base() -> None:
    assert dict(dump.capabilities("xiaomi.vacuum.c102").properties) == dict(load("dreame.vacuum.unknown").properties)


@pytest.fixture
async def fake_robot(socket_enabled) -> AsyncIterator[tuple[FakeRobot, int]]:
    transport, robot = await asyncio.get_running_loop().create_datagram_endpoint(FakeRobot, local_addr=("127.0.0.1", 0))
    try:
        yield robot, transport.get_extra_info("sockname")[1]
    finally:
        robot.close()


async def test_capture_packets_round_trips(fake_robot: tuple[FakeRobot, int]) -> None:
    robot, port = fake_robot
    packets = await dump.capture_packets("127.0.0.1", TOKEN_HEX, port=port)
    assert (packets["device_id"], packets["stamp"], packets["request_stamp"]) == (DEVICE_ID, STAMP, STAMP + 1)
    header, request = codec.parse(TOKEN, bytes.fromhex(packets["request_hex"]))
    assert request == packets["request"] == robot.requests[0]
    assert request["method"] == "get_properties"
    assert request["params"] == [{"did": "3.1", "siid": 3, "piid": 1}]
    assert (header.device_id, header.stamp) == (DEVICE_ID, STAMP + 1)
    header, reply = codec.parse(TOKEN, bytes.fromhex(packets["reply_hex"]))
    assert reply == packets["reply"]
    assert reply["id"] == request["id"]
    assert TOKEN_HEX not in json.dumps(packets)
    assert header.stamp == packets["reply_stamp"]


def test_parse_args_rules(capsys) -> None:
    args = dump.parse_args(["--country", "de", "--user", "me"])
    assert args.out.parent == Path("dumps")
    assert (args.watch, args.interval, args.no_scan) == (0, 3, False)
    for argv in (["--country", "de"], ["--country", "de", "--user", "me", "--token", "abc"]):
        with pytest.raises(SystemExit) as exit_info:
            dump.parse_args(argv)
        assert exit_info.value.code == 2


def test_help_runs_without_home_assistant() -> None:
    result = subprocess.run(  # noqa: S603
        [sys.executable, str(SCRIPT), "--help"], capture_output=True, text=True, check=False, timeout=30
    )
    assert result.returncode == 0
    assert "--redact" in result.stdout
    probe = (
        "import importlib.util, sys;"
        f"s = importlib.util.spec_from_file_location('d', {str(SCRIPT)!r});"
        "s.loader.exec_module(importlib.util.module_from_spec(s));"
        "print('homeassistant' in sys.modules)"
    )
    result = subprocess.run(  # noqa: S603
        [sys.executable, "-c", probe], capture_output=True, text=True, check=False, timeout=30
    )
    assert result.stdout.strip() == "False"
