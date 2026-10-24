"""Tests of the MIoT property and action helpers."""

from __future__ import annotations

from typing import Any

import pytest

from custom_components.dreame_vacuum_3d.protocol import miot
from custom_components.dreame_vacuum_3d.protocol.miio_codec import MiioError

_ECHO = object()


class FakeSend:
    """Records (method, params); answers reply, or like a MIoT device by default."""

    def __init__(self, *, codes: dict[tuple[int, int], int] | None = None, reply: Any = _ECHO) -> None:
        self.calls: list[tuple[str, Any]] = []
        self.codes = codes or {}
        self.reply = reply

    async def __call__(self, method: str, params: Any) -> Any:
        self.calls.append((method, params))
        if self.reply is not _ECHO:
            return self.reply
        return [
            {
                "did": item["did"],
                "siid": item["siid"],
                "piid": item["piid"],
                "code": self.codes.get((item["siid"], item["piid"]), 0),
                "value": item["siid"] * 100 + item["piid"],
            }
            for item in params
        ]


async def test_get_properties_request_and_result():
    send = FakeSend()
    assert await miot.get_properties(send, [(2, 1), (3, 1)]) == {(2, 1): 201, (3, 1): 301}
    assert send.calls == [
        (
            "get_properties",
            [{"did": "2.1", "siid": 2, "piid": 1}, {"did": "3.1", "siid": 3, "piid": 1}],
        )
    ]


async def test_get_properties_in_chunks():
    send = FakeSend()
    props = [(4, piid) for piid in range(1, 41)]
    result = await miot.get_properties(send, props)
    assert [len(params) for _method, params in send.calls] == [15, 15, 10]
    assert result == {prop: 400 + prop[1] for prop in props}


async def test_get_properties_custom_chunk():
    send = FakeSend()
    await miot.get_properties(send, [(2, 1), (2, 2), (2, 3)], chunk=2)
    assert [len(params) for _method, params in send.calls] == [2, 1]


async def test_get_properties_keys_come_from_the_did_we_sent():
    send = FakeSend(reply=[{"did": "2.1", "siid": 9, "piid": 9, "code": 0, "value": "v"}])
    assert await miot.get_properties(send, [(2, 1)]) == {(2, 1): "v"}


@pytest.mark.parametrize(
    "reply",
    [
        pytest.param(None, id="none"),
        pytest.param({"code": 0}, id="dict"),
        pytest.param([None, "x", 3], id="non-dict-items"),
        pytest.param([{"did": "9.9", "code": 0, "value": 1}], id="unknown-did"),
        pytest.param([{"did": ["2.1"], "code": 0, "value": 1}], id="unhashable-did"),
        pytest.param([{"siid": 2, "piid": 1, "code": 0, "value": 1}], id="no-did"),
        pytest.param([{"did": "2.1", "value": 1}], id="no-code"),
    ],
)
async def test_get_properties_skips_malformed_replies(reply):
    send = FakeSend(reply=reply)
    assert await miot.get_properties(send, [(2, 1)]) == {}


async def test_get_properties_skips_failed_props():
    send = FakeSend(codes={(2, 2): -4001})
    assert await miot.get_properties(send, [(2, 1), (2, 2)]) == {(2, 1): 201}


async def test_get_properties_of_nothing_sends_nothing():
    send = FakeSend()
    assert await miot.get_properties(send, []) == {}
    assert send.calls == []


async def test_set_property():
    send = FakeSend(reply=[{"did": "set", "siid": 4, "piid": 4, "code": 0}])
    await miot.set_property(send, 4, 4, 2)
    assert send.calls == [("set_properties", [{"did": "set", "siid": 4, "piid": 4, "value": 2}])]


async def test_set_property_error_raises():
    send = FakeSend(reply=[{"did": "set", "siid": 4, "piid": 4, "code": -4004}])
    with pytest.raises(miot.MiotError) as info:
        await miot.set_property(send, 4, 4, 2)
    assert info.value.code == -4004


@pytest.mark.parametrize(
    "reply",
    [
        pytest.param(None, id="none"),
        pytest.param([], id="empty"),
        pytest.param({"code": 0}, id="dict"),
        pytest.param([None], id="non-dict-item"),
        pytest.param([{"did": "set"}], id="no-code"),
    ],
)
async def test_set_property_malformed_reply_raises(reply):
    send = FakeSend(reply=reply)
    with pytest.raises(miot.MiotError):
        await miot.set_property(send, 4, 4, 2)


@pytest.mark.parametrize("reply", [None, [], "ok"])
async def test_call_action_malformed_reply_raises(reply):
    send = FakeSend(reply=reply)
    with pytest.raises(miot.MiotError) as info:
        await miot.call_action(send, 4, 1)
    assert info.value.code is None


def test_miot_error_is_transport_agnostic():
    assert not issubclass(miot.MiotError, MiioError)


async def test_call_action_payload_and_reply():
    reply = {"did": "call", "siid": 4, "aiid": 1, "code": 0, "out": []}
    send = FakeSend(reply=reply)
    params = [{"piid": 1, "value": 18}, {"piid": 10, "value": '{"selects":[[6,1,1,2,1]]}'}]
    assert await miot.call_action(send, 4, 1, params) == reply
    assert send.calls == [
        (
            "action",
            {
                "did": "call",
                "siid": 4,
                "aiid": 1,
                "in": [{"piid": 1, "value": 18}, {"piid": 10, "value": '{"selects":[[6,1,1,2,1]]}'}],
            },
        )
    ]


async def test_call_action_without_params():
    send = FakeSend(reply={"code": 0})
    await miot.call_action(send, 3, 1)
    assert send.calls == [("action", {"did": "call", "siid": 3, "aiid": 1, "in": []})]


async def test_call_action_error_raises():
    send = FakeSend(reply={"did": "call", "siid": 4, "aiid": 1, "code": -4003})
    with pytest.raises(miot.MiotError) as info:
        await miot.call_action(send, 4, 1)
    assert info.value.code == -4003
