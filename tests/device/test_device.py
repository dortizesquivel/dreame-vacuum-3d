"""Tests of DreameDevice discovery, refresh, channel fallback and command payloads."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from custom_components.dreame_vacuum_3d.device.capabilities import load
from custom_components.dreame_vacuum_3d.device.device import DreameDevice
from custom_components.dreame_vacuum_3d.device.enums import (
    DreameVacuumState,
    ErrorCode,
    Status,
    SuctionLevel,
    parse_enum,
)
from custom_components.dreame_vacuum_3d.protocol.miio import MiioCommandError
from custom_components.dreame_vacuum_3d.protocol.miio_codec import MiioError

FIXTURE = Path(__file__).parents[1] / "fixtures" / "x10plus" / "properties.json"


def _fixture_by_key() -> dict[str, dict[str, Any]]:
    return {item["key"]: item for item in json.loads(FIXTURE.read_text(encoding="utf-8"))}


class FakeSend:
    """Answers get_properties from the X10+ dump; records every RPC call."""

    def __init__(
        self,
        by_key: dict[str, dict[str, Any]] | None = None,
        *,
        fail: Exception | None = None,
        overrides: dict[tuple[int, int], Any] | None = None,
    ) -> None:
        self.by_siid_piid = {
            (item["siid"], item["piid"]): item for item in (by_key or _fixture_by_key()).values()
        }
        self.fail = fail
        self.overrides = overrides or {}
        self.calls: list[tuple[str, Any]] = []

    async def __call__(self, method: str, params: Any) -> Any:
        self.calls.append((method, params))
        if self.fail is not None:
            raise self.fail
        if method == "get_properties":
            out = []
            for item in params:
                key = (item["siid"], item["piid"])
                if key in self.overrides:
                    code, value = 0, self.overrides[key]
                else:
                    row = self.by_siid_piid.get(key)
                    if row is None:
                        code, value = -1, None
                    else:
                        code, value = row["code"], row["value"]
                out.append(
                    {
                        "did": item["did"],
                        "siid": item["siid"],
                        "piid": item["piid"],
                        "code": code,
                        "value": value,
                    }
                )
            return out
        if method == "set_properties":
            return [{"did": p["did"], "siid": p["siid"], "piid": p["piid"], "code": 0} for p in params]
        if method == "action":
            return {"did": "call", "siid": params["siid"], "aiid": params["aiid"], "code": 0, "out": []}
        raise AssertionError(f"unexpected method {method}")


@pytest.fixture
def caps():
    return load("dreame.vacuum.p2114a")


@pytest.fixture
def device(caps) -> DreameDevice:
    return DreameDevice(caps, FakeSend(), FakeSend())


def test_parse_enum_known_and_unknown(caplog: pytest.LogCaptureFixture) -> None:
    assert parse_enum(SuctionLevel, 2) is SuctionLevel.STRONG
    assert parse_enum(DreameVacuumState, 1) is DreameVacuumState.SWEEPING
    assert parse_enum(ErrorCode, 0) is ErrorCode.NO_ERROR
    with caplog.at_level("DEBUG"):
        assert parse_enum(SuctionLevel, 99) is None
    assert any("unknown SuctionLevel" in r.message for r in caplog.records)


async def test_discover_available_from_fixture(caps) -> None:
    local = FakeSend()
    cloud = FakeSend()
    device = DreameDevice(caps, local, cloud)
    await device.async_discover()

    expected = {k for k, row in _fixture_by_key().items() if row["code"] == 0 and k in caps.properties}
    assert device.available == frozenset(expected)
    # Live dump: docked after charge (CHARGING_COMPLETED).
    assert device.values["state"] == 13
    assert device.values["battery_level"] == 100
    assert device.values["charging_status"] == 1

    assert "off_peak_charging" not in device.available
    assert "off_peak_charging" not in device.values
    assert device.channel == "local"
    assert cloud.calls == []


async def test_refresh_returns_changed_keys(caps) -> None:
    local = FakeSend()
    device = DreameDevice(caps, local, FakeSend())
    await device.async_discover()
    local.calls.clear()
    local.overrides[(3, 1)] = 80  # battery_level
    local.overrides[(2, 1)] = 2  # state IDLE

    changed = await device.async_refresh()
    assert changed == {"battery_level", "state"}
    assert device.values["battery_level"] == 80
    assert device.values["state"] == 2
    # Only available props are requested.
    requested = {(p["siid"], p["piid"]) for _m, params in local.calls for p in params}
    assert requested == {caps.properties[k] for k in device.available}


async def test_cloud_fallback_then_return_to_local(caps, monkeypatch: pytest.MonkeyPatch) -> None:
    now = 1000.0
    monkeypatch.setattr("custom_components.dreame_vacuum_3d.device.device.monotonic", lambda: now)
    local = FakeSend(fail=MiioError("udp down"))
    cloud = FakeSend()
    device = DreameDevice(caps, local, cloud)
    await device.async_discover()
    assert device.channel == "cloud"
    assert cloud.calls
    assert all(m == "get_properties" for m, _ in cloud.calls)

    # While on the cloud, local isn't retried (each retry waits for its timeouts).
    local.fail = None
    local.calls.clear()
    await device.async_refresh()
    assert device.channel == "cloud"
    assert local.calls == []

    # After the retry window, local is back.
    now += 301
    cloud.calls.clear()
    await device.async_refresh()
    assert device.channel == "local"
    assert local.calls
    assert cloud.calls == []


async def test_command_error_keeps_trying_local(caps) -> None:
    """A robot that answers locally with an error still works locally."""
    local = FakeSend(fail=MiioCommandError(-1, "busy"))
    cloud = FakeSend()
    device = DreameDevice(caps, local, cloud)
    await device.async_discover()
    assert device.channel == "cloud"
    local.fail = None
    await device.async_refresh()
    assert device.channel == "local"


async def test_action_commands_payloads(device: DreameDevice) -> None:
    local: FakeSend = device._send_local  # type: ignore[attr-defined]
    for method, expected in [
        (device.start, (2, 1)),
        (device.pause, (2, 2)),
        (device.stop, (4, 2)),
        (device.return_to_base, (3, 1)),
        (device.locate, (7, 1)),
    ]:
        local.calls.clear()
        await method()
        assert local.calls == [
            ("action", {"did": "call", "siid": expected[0], "aiid": expected[1], "in": []})
        ]


async def test_set_and_reset_consumable(device: DreameDevice) -> None:
    local: FakeSend = device._send_local  # type: ignore[attr-defined]
    await device.set("suction_level", 3)
    assert local.calls[-1] == ("set_properties", [{"did": "set", "siid": 4, "piid": 4, "value": 3}])

    local.calls.clear()
    await device.reset_consumable("main_brush")
    assert local.calls == [("action", {"did": "call", "siid": 9, "aiid": 1, "in": []})]


async def test_clean_segment_payload(device: DreameDevice) -> None:
    local: FakeSend = device._send_local  # type: ignore[attr-defined]
    await device.clean_segment([6, 3], repeats=1, suction=1, water=2)
    # Tasshack: start_custom(SEGMENT_CLEANING, {"selects":[[id,repeat,fan,water,index],...]})
    payload = json.dumps({"selects": [[6, 1, 1, 2, 1], [3, 1, 1, 2, 2]]}, separators=(",", ":"))
    assert local.calls == [
        (
            "action",
            {
                "did": "call",
                "siid": 4,
                "aiid": 1,
                "in": [
                    {"piid": 1, "value": Status.SEGMENT_CLEANING.value},
                    {"piid": 10, "value": payload},
                ],
            },
        )
    ]


async def test_clean_segment_defaults_to_the_robots_settings(caps) -> None:
    """Like the app: no suction/water given → the robot's own; order 1 for every room on the X10+."""
    local = FakeSend()
    device = DreameDevice(caps, local, FakeSend())
    await device.async_discover()  # fixture: suction strong (2), water high (3), customized_cleaning present
    local.calls.clear()
    await device.clean_segment([6, 3])
    payload = json.dumps({"selects": [[6, 1, 2, 3, 1], [3, 1, 2, 3, 1]]}, separators=(",", ":"))
    assert local.calls[-1][1]["in"][1] == {"piid": 10, "value": payload}
    assert device.segments == [6, 3]

    await device.clean_zone([1000, 2000, 3000, 4000])
    assert '"areas":[[1000,2000,3000,4000,1,2,3]]' in local.calls[-1][1]["in"][1]["value"]
    await device.clean_spot([1500, 2500], suction=[0])
    assert '"points":[[1500,2500,1,0,3]]' in local.calls[-1][1]["in"][1]["value"]


async def test_clean_zone_payload(device: DreameDevice) -> None:
    local: FakeSend = device._send_local  # type: ignore[attr-defined]
    zones = [[1000, 2000, 3000, 4000], [500, 600, 2500, 2600]]
    await device.clean_zone(zones, repeats=2, suction=2, water=3)
    areas = [
        [1000, 2000, 3000, 4000, 2, 2, 3],
        [500, 600, 2500, 2600, 2, 2, 3],
    ]
    payload = json.dumps({"areas": areas}, separators=(",", ":"))
    assert local.calls[-1][1]["in"] == [
        {"piid": 1, "value": Status.ZONE_CLEANING.value},
        {"piid": 10, "value": payload},
    ]


async def test_clean_spot_payload(device: DreameDevice) -> None:
    local: FakeSend = device._send_local  # type: ignore[attr-defined]
    await device.clean_spot([[1500, 2500]], repeats=1, suction=0, water=1)
    payload = json.dumps({"points": [[1500, 2500, 1, 0, 1]]}, separators=(",", ":"))
    assert local.calls[-1][1]["in"] == [
        {"piid": 1, "value": Status.SPOT_CLEANING.value},
        {"piid": 10, "value": payload},
    ]


async def test_goto_cruising_and_zone_payloads(device: DreameDevice) -> None:
    local: FakeSend = device._send_local  # type: ignore[attr-defined]
    await device.goto(100, 200, cruising=True)
    payload = json.dumps({"tpoint": [[100, 200, 0, 0]]}, separators=(",", ":"))
    assert local.calls[-1][1]["in"] == [
        {"piid": 1, "value": Status.CRUISING_POINT.value},
        {"piid": 10, "value": payload},
    ]

    local.calls.clear()
    await device.goto(1000, 2000, cruising=False)
    # No map → grid_size 100 → half 50 (Tasshack go_to non-cruising path).
    areas = [[950, 1950, 1050, 2050, 1, 0, 1]]
    payload = json.dumps({"areas": areas}, separators=(",", ":"))
    assert local.calls[-1][1]["in"] == [
        {"piid": 1, "value": Status.ZONE_CLEANING.value},
        {"piid": 10, "value": payload},
    ]


async def test_remote_control_move_step(device: DreameDevice) -> None:
    local: FakeSend = device._send_local  # type: ignore[attr-defined]
    with patch("custom_components.dreame_vacuum_3d.device.device.randrange", return_value=42):
        await device.remote_control_move_step(rotation=10, velocity=5)
    assert local.calls[-1] == (
        "set_properties",
        [
            {
                "did": "set",
                "siid": 4,
                "piid": 15,
                "value": '{"spdv":5,"spdw":10,"audio":"true","random":42}',
            }
        ],
    )
    local.calls.clear()
    with patch("custom_components.dreame_vacuum_3d.device.device.randrange", return_value=7):
        await device.remote_control_move_step(rotation=-5, velocity=0)
    # Second step: audio false once remote control is already active.
    assert local.calls[-1][1][0]["value"] == '{"spdv":0,"spdw":-5,"audio":"false","random":7}'


async def test_select_map_payload(device: DreameDevice) -> None:
    local: FakeSend = device._send_local  # type: ignore[attr-defined]
    await device.select_map(3)
    value = json.dumps({"sm": {}, "mapid": 3}, separators=(",", ":"))
    assert local.calls == [
        (
            "action",
            {
                "did": "call",
                "siid": 6,
                "aiid": 2,
                "in": [{"piid": 4, "value": value}],
            },
        )
    ]


async def test_set_unknown_key_raises(device: DreameDevice) -> None:
    with pytest.raises(KeyError):
        await device.set("no_such_prop", 1)


async def test_reset_unknown_consumable_raises(device: DreameDevice) -> None:
    with pytest.raises(KeyError):
        await device.reset_consumable("unicorn")


async def test_refresh_before_discover_is_noop(caps) -> None:
    device = DreameDevice(caps, FakeSend(), FakeSend())
    assert await device.async_refresh() == set()


async def test_clean_helpers_accept_flat_coords_and_short_lists(device: DreameDevice) -> None:
    local: FakeSend = device._send_local  # type: ignore[attr-defined]
    await device.clean_zone([1000, 2000, 3000, 4000], repeats=[2], suction=[1], water=[3])
    assert '"areas":[[1000,2000,3000,4000,2,1,3]]' in local.calls[-1][1]["in"][1]["value"]

    local.calls.clear()
    await device.clean_spot([1500, 2500], repeats=[1, 9], suction=[0], water=[1, 9])
    assert '"points":[[1500,2500,1,0,1]]' in local.calls[-1][1]["in"][1]["value"]

    local.calls.clear()
    await device.clean_segment(6, repeats=[1], suction=[2], water=[3])
    assert '"selects":[[6,1,2,3,1]]' in local.calls[-1][1]["in"][1]["value"]


async def test_clean_zone_and_spot_reject_empty(device: DreameDevice) -> None:
    with pytest.raises(ValueError, match="zones"):
        await device.clean_zone([])
    with pytest.raises(ValueError, match="points"):
        await device.clean_spot([])
    with pytest.raises(ValueError, match="4 coordinates"):
        await device.clean_zone([[1, 2, 3]])


async def test_remote_control_prompt_override(device: DreameDevice) -> None:
    local: FakeSend = device._send_local  # type: ignore[attr-defined]
    with patch("custom_components.dreame_vacuum_3d.device.device.randrange", return_value=1):
        await device.remote_control_move_step(prompt=False)
    assert '"audio":"false"' in local.calls[-1][1][0]["value"]
    local.calls.clear()
    with patch("custom_components.dreame_vacuum_3d.device.device.randrange", return_value=2):
        await device.remote_control_move_step(prompt=True)
    assert '"audio":"true"' in local.calls[-1][1][0]["value"]
