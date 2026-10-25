"""Tests of the Xiaomi cloud API calls (devices, RPC, files) against a fake server."""

from __future__ import annotations

import json

import aiohttp
import pytest

from custom_components.dreame_vacuum_3d.protocol import miot
from custom_components.dreame_vacuum_3d.protocol.cloud import (
    CloudAuth,
    CloudAuthError,
    CloudConnectionError,
    XiaomiCloud,
)

from .fake_cloud import API_URL, SSECURITY, FakeSession, Reply, fixture

DEVICES = fixture("devices")
AUTH = CloudAuth(
    user_id="1000001", ssecurity=SSECURITY, service_token="c2VydmljZS90b2tlbg==", client_id="abcdefghijklmnop"
)
OBJECT = "1000001/1001/0"
FILE_URL = "https://cdn.example.com/map/1001?signature=synthetic"


@pytest.fixture
async def session() -> FakeSession:
    return FakeSession()


@pytest.fixture
async def cloud(session) -> XiaomiCloud:
    return XiaomiCloud(session, "de", AUTH)


def serve_devices(session: FakeSession) -> None:
    session.api("v2/homeroom/gethome", DEVICES["gethome"])
    session.api("v2/user/get_device_cnt", DEVICES["device_cnt"])
    session.api("v2/home/home_device_list", lambda data: DEVICES[f"home_{data['home_id']}"])
    session.api("home/device_list", DEVICES["device_list"])


async def test_devices_merges_own_shared_and_listed_devices(session, cloud):
    serve_devices(session)

    devices = await cloud.devices()

    assert [d["did"] for d in devices] == ["1001", "2001", "3001"]
    assert devices[0] == DEVICES["home_100001"]["result"]["device_info"][0]
    calls = dict(session.api_calls[:2])
    assert calls["v2/homeroom/gethome"] == {
        "fg": True,
        "fetch_share": True,
        "fetch_share_dev": True,
        "limit": 100,
        "app_ver": 7,
    }
    assert calls["v2/user/get_device_cnt"] == {"fetch_own": True, "fetch_share": True}
    homes = [(d["home_id"], d["home_owner"]) for p, d in session.api_calls if p == "v2/home/home_device_list"]
    assert homes == [(100001, "1000001"), (200002, 2000002)]
    assert session.api_calls[-1] == ("home/device_list", {"getVirtualModel": False, "getHuamiDevices": 0})


async def test_devices_without_homes(session, cloud):
    session.api("v2/homeroom/gethome", {"code": 0, "result": None})
    session.api("v2/user/get_device_cnt", {"code": 0, "result": {"share": None}})
    session.api("home/device_list", DEVICES["device_list"])

    assert [d["did"] for d in await cloud.devices()] == ["1001", "3001"]


async def test_devices_skip_malformed_entries(session, cloud):
    vacuum = DEVICES["device_list"]["result"]["list"][1]
    homes = [None, "home", {"name": "no id"}, {"id": "not a number"}, {"id": "100001"}]
    session.api("v2/homeroom/gethome", {"code": 0, "result": {"homelist": homes}})
    families = [None, {"home_id": 200002}, {"home_owner": 2000002}, {"home_id": 200002, "home_owner": 2000002}]
    session.api("v2/user/get_device_cnt", {"code": 0, "result": {"share": {"share_family": families}}})
    session.api(
        "v2/home/home_device_list",
        lambda data: {"code": 0, "result": {"device_info": {"did": "1"}} if data["home_id"] == 100001 else ["x"]},
    )
    session.api("home/device_list", {"code": 0, "result": {"list": [None, "1001", {"name": "no ids"}, vacuum]}})

    assert await cloud.devices() == [vacuum]
    homes = [(d["home_id"], d["home_owner"]) for p, d in session.api_calls if p == "v2/home/home_device_list"]
    assert homes == [(100001, "1000001"), (200002, 2000002)]


@pytest.mark.parametrize("result", [None, [], "x", {"homelist": "x"}, {"homelist": None}])
async def test_devices_with_odd_results(session, cloud, result):
    session.api("v2/homeroom/gethome", {"code": 0, "result": result})
    session.api("v2/user/get_device_cnt", {"code": 0, "result": {"share": ["x"]}})
    session.api("home/device_list", {"code": 0, "result": {"list": {"did": "1"}}})

    assert await cloud.devices() == []


async def test_rpc_returns_the_result(session, cloud):
    session.api("v2/home/rpc/1001", {"code": 0, "message": "ok", "result": [{"did": "2.1", "code": 0, "value": 6}]})

    result = await cloud.rpc("1001", "get_properties", [{"did": "2.1", "siid": 2, "piid": 1}])

    assert result == [{"did": "2.1", "code": 0, "value": 6}]
    assert session.api_calls == [
        ("v2/home/rpc/1001", {"method": "get_properties", "params": [{"did": "2.1", "siid": 2, "piid": 1}]})
    ]


async def test_rpc_without_a_result(session, cloud):
    session.api("v2/home/rpc/1001", {"code": -2, "message": "device offline"})

    assert await cloud.rpc("1001", "get_properties", []) is None


async def test_rpc_sender_plugs_into_miot(session, cloud):
    session.api("v2/home/rpc/1001", lambda data: {"code": 0, "result": [{**data["params"][0], "code": 0, "value": 6}]})

    assert await miot.get_properties(cloud.rpc_sender("1001"), [(2, 1)]) == {(2, 1): 6}


async def test_requests_are_encrypted_and_signed(session, cloud):
    session.api("v2/home/rpc/1001", {"code": 0, "result": "ok"})

    await cloud.rpc("1001", "action", {"did": "call", "siid": 2, "aiid": 1, "in": []})

    [call] = session.calls
    assert str(call.url) == f"{API_URL}/v2/home/rpc/1001"
    assert list(call.data) == ["data", "rc4_hash__", "signature", "ssecurity", "_nonce"]
    with pytest.raises(ValueError):
        json.loads(call.data["data"])
    assert call.cookies["serviceToken"] == AUTH.service_token
    assert call.cookies["channel"] == "MI_APP_STORE"


@pytest.mark.parametrize(
    "reply",
    [
        {"code": 2, "message": "auth err"},
        {"code": 3, "message": "auth err"},
        {"code": -1, "message": "invalid signature"},
        {"code": -1, "message": "SERVICETOKEN_EXPIRED"},
        Reply(status=401),
    ],
)
async def test_expired_session(session, cloud, reply):
    session.api("v2/home/rpc/1001", reply)

    with pytest.raises(CloudAuthError, match="invalid_auth"):
        await cloud.rpc("1001", "get_properties", [])


@pytest.mark.parametrize("failure", [Reply(status=502), aiohttp.ClientConnectionError("boom"), TimeoutError()])
async def test_rpc_connection_errors(session, cloud, failure):
    session.api("v2/home/rpc/1001", failure)

    with pytest.raises(CloudConnectionError):
        await cloud.rpc("1001", "get_properties", [])


async def test_calls_need_a_session(session):
    with pytest.raises(CloudAuthError, match="invalid_auth"):
        await XiaomiCloud(session, "de").rpc("1001", "get_properties", [])
    assert not session.calls


@pytest.mark.parametrize(
    ("method", "v2"), [("file_url", "home/getfileurl"), ("interim_file_url", "v2/home/get_interim_file_url")]
)
async def test_file_urls(session, cloud, method, v2):
    session.api(v2, {"code": 0, "result": {"url": FILE_URL}})

    assert await getattr(cloud, method)(OBJECT) == FILE_URL
    assert await getattr(cloud, method)(OBJECT, model="dreame.vacuum.r2228o") == FILE_URL
    assert session.api_calls == [(v2, {"obj_name": OBJECT}), (v2, {"obj_name": OBJECT})]


@pytest.mark.parametrize(
    ("method", "v2", "v3"),
    [
        ("file_url", "home/getfileurl", "home/getfileurl_v3"),
        ("interim_file_url", "v2/home/get_interim_file_url", "v2/home/get_interim_file_url_pro"),
    ],
)
async def test_file_urls_v3_with_fallback(session, cloud, method, v2, v3):
    session.api(v3, {"code": 0, "result": {"url": FILE_URL}}, {"code": -8, "message": "not supported"})
    session.api(v2, {"code": 0, "result": {"url": FILE_URL}})

    assert await getattr(cloud, method)(OBJECT, model="xiaomi.vacuum.d109gl") == FILE_URL
    assert await getattr(cloud, method)(OBJECT, model="xiaomi.vacuum.d109gl") == FILE_URL
    assert await getattr(cloud, method)(OBJECT, model="xiaomi.vacuum.d109gl") == FILE_URL
    assert [p for p, _ in session.api_calls] == [v3, v3, v2, v2]


async def test_file_url_fallback_is_per_model(session, cloud):
    session.api(
        "home/getfileurl_v3", {"code": -8, "message": "not supported"}, {"code": 0, "result": {"url": FILE_URL}}
    )
    session.api("home/getfileurl", {"code": 0, "result": {"url": FILE_URL}})

    assert await cloud.file_url(OBJECT, model="xiaomi.vacuum.old") == FILE_URL
    assert await cloud.file_url(OBJECT, model="xiaomi.vacuum.new") == FILE_URL
    assert await cloud.file_url(OBJECT, model="xiaomi.vacuum.old") == FILE_URL
    assert [p for p, _ in session.api_calls] == [
        "home/getfileurl_v3",
        "home/getfileurl",
        "home/getfileurl_v3",
        "home/getfileurl",
    ]


@pytest.mark.parametrize(
    "reply",
    [{"code": 0, "result": None}, {"code": 0, "result": {"url": ""}}, {"code": -8, "message": "not supported"}],
)
async def test_file_url_missing(session, cloud, reply):
    session.api("home/getfileurl", reply)

    assert await cloud.file_url(OBJECT) is None


async def test_download(session, cloud):
    url = "https://cdn.example.com/map/1001?Signature=a%2Fb%3D&Expires=%31"
    session.on("GET", "https://cdn.example.com/map/1001", Reply(body=b"\x1f\x8bmap"))

    assert await cloud.download(url) == b"\x1f\x8bmap"
    assert str(session.calls[0].url) == url


async def test_download_missing_file(session, cloud):
    session.on("GET", "https://cdn.example.com/map/1001", Reply(status=404))

    assert await cloud.download(FILE_URL) is None


@pytest.mark.parametrize("failure", [Reply(status=403), Reply(status=500), aiohttp.ClientConnectionError("boom")])
async def test_download_errors(session, cloud, failure):
    session.on("GET", "https://cdn.example.com/map/1001", failure)

    with pytest.raises(CloudConnectionError):
        await cloud.download(FILE_URL)


async def test_errors_dont_chain_signed_urls(session, cloud):
    session.on("GET", "https://cdn.example.com/map/1001", aiohttp.ClientConnectionError(f"can't fetch {FILE_URL}"))

    with pytest.raises(CloudConnectionError) as err:
        await cloud.download(FILE_URL)

    assert "signature" not in str(err.value)
    assert err.value.__cause__ is None
    assert err.value.__suppress_context__
