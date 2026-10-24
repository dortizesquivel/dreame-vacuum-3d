"""Tests of the Xiaomi cloud login (account password, captcha) against a fake server."""

from __future__ import annotations

from dataclasses import asdict
import logging
import re

import aiohttp
import pytest

from custom_components.dreame_vacuum_3d.protocol.cloud import (
    CloudAuth,
    CloudAuthError,
    CloudConnectionError,
    CloudLoginRequired,
    XiaomiCloud,
)

from .fake_cloud import ACCOUNT, SSECURITY, FakeSession, Reply, fixture

LOGIN = fixture("login")
SERVICE_LOGIN = f"{ACCOUNT}/pass/serviceLogin"
LOGIN_AUTH = f"{ACCOUNT}/pass/serviceLoginAuth2"
STS = "https://sts.api.io.mi.com/sts"
CAPTCHA = f"{ACCOUNT}/pass/getCode"
SERVICE_TOKEN = "c3ludGhldGljLXNlcnZpY2U/to+ken=="
STORED = CloudAuth(
    user_id="1000001",
    ssecurity=SSECURITY,
    service_token="b2xkLXNlcnZpY2UtdG9rZW4=",
    client_id="abcdefghijklmnop",
    pass_token="V1:b2xkLXBhc3MtdG9rZW4=",
)


@pytest.fixture
async def session() -> FakeSession:
    return FakeSession()


def serve_login(session: FakeSession, *step_2: Reply, prefix: str = "&&&START&&&") -> None:
    session.on("GET", SERVICE_LOGIN, Reply.xiaomi(LOGIN["service_login"], prefix=prefix))
    session.on("POST", LOGIN_AUTH, *(step_2 or [Reply.xiaomi(LOGIN["login_ok"], prefix=prefix)]))
    session.on("GET", STS, Reply(body="ok", cookies={"serviceToken": SERVICE_TOKEN, "userId": "1000001"}))


@pytest.mark.parametrize("prefix", ["&&&START&&&", ""])
async def test_login_with_password(session, prefix):
    serve_login(session, prefix=prefix)
    cloud = XiaomiCloud(session, "de")

    auth = await cloud.login("user@example.com", "secret-password")

    assert auth == CloudAuth(
        user_id="1000001",
        ssecurity=SSECURITY,
        service_token=SERVICE_TOKEN,
        client_id=auth.client_id,
        pass_token=LOGIN["login_ok"]["passToken"],
    )
    assert cloud.auth == auth
    assert re.fullmatch("[a-z]{16}", auth.client_id)
    [step_1] = session.requests("GET", SERVICE_LOGIN)
    assert step_1.url.query == {"sid": "xiaomiio", "_json": "true"}
    assert step_1.cookies == {"sdkVersion": "3.8.6", "deviceId": auth.client_id}
    assert auth.client_id in step_1.headers["User-Agent"]
    [step_2] = session.requests("POST", LOGIN_AUTH)
    assert step_2.url.query == {"_json": "true"}
    assert step_2.data == {
        "user": "user@example.com",
        "hash": "2304D4770A72D09106045FEA654C4188",  # MD5 of the password, upper case
        "callback": "https://sts.api.io.mi.com/sts",
        "sid": "xiaomiio",
        "qs": "%3Fsid%3Dxiaomiio%26_json%3Dtrue",
        "_sign": LOGIN["service_login"]["_sign"],
    }
    [step_3] = session.requests("GET", STS)
    assert str(step_3.url) == LOGIN["login_ok"]["location"]


async def test_login_skips_the_password_when_step_1_has_a_location(session):
    session.api("v2/message/v2/check_new_msg", {"code": 2, "message": "auth err"})
    session.on("GET", SERVICE_LOGIN, Reply.xiaomi(LOGIN["service_login_ok"]))
    session.on("GET", STS, Reply(cookies={"serviceToken": SERVICE_TOKEN}))
    cloud = XiaomiCloud(session, "de", STORED)

    auth = await cloud.login("user@example.com", "secret-password")

    assert not session.requests("POST", LOGIN_AUTH)
    [step_1] = session.requests("GET", SERVICE_LOGIN)
    assert step_1.cookies == {
        "sdkVersion": "3.8.6",
        "deviceId": STORED.client_id,
        "passToken": STORED.pass_token,
        "userId": STORED.user_id,
    }
    assert auth.service_token == SERVICE_TOKEN
    assert auth.client_id == STORED.client_id
    assert auth.pass_token == LOGIN["service_login_ok"]["passToken"]


async def test_login_reuses_a_live_session(session):
    session.api("v2/message/v2/check_new_msg", {"code": 0, "message": "ok", "result": {}})
    cloud = XiaomiCloud(session, "de", STORED)

    assert await cloud.login("user@example.com", "secret-password") == STORED
    assert [c.url.host for c in session.calls] == ["de.api.io.mi.com"]


async def test_login_with_a_wrong_password(session):
    serve_login(session, Reply.xiaomi(LOGIN["wrong_password"]))
    cloud = XiaomiCloud(session, "de")

    with pytest.raises(CloudAuthError, match="invalid_auth"):
        await cloud.login("user@example.com", "wrong")
    assert cloud.auth is None
    assert not session.requests("GET", STS)


async def test_failed_login_forgets_the_old_session(session):
    session.api("v2/message/v2/check_new_msg", {"code": 3, "message": "auth err"})
    serve_login(session, Reply.xiaomi(LOGIN["wrong_password"]))
    cloud = XiaomiCloud(session, "de", STORED)

    with pytest.raises(CloudAuthError):
        await cloud.login("user@example.com", "wrong")
    assert cloud.auth is None


async def test_login_asks_for_a_captcha_and_retries_with_it(session):
    serve_login(session, Reply.xiaomi(LOGIN["captcha"]), Reply.xiaomi(LOGIN["login_ok"]))
    session.on("GET", CAPTCHA, Reply(body=b"\x89PNG fake", cookies={"ick": "c3ludGhldGljLWljaw"}))
    cloud = XiaomiCloud(session, "de")

    with pytest.raises(CloudLoginRequired) as err:
        await cloud.login("user@example.com", "secret-password")
    assert err.value.captcha_png == b"\x89PNG fake"
    assert err.value.verification_url is None
    assert str(session.requests("GET", CAPTCHA)[0].url) == ACCOUNT + LOGIN["captcha"]["captchaUrl"]

    auth = await cloud.login("user@example.com", "secret-password", captcha="x7k2")

    assert auth.service_token == SERVICE_TOKEN
    first, retry = session.requests("POST", LOGIN_AUTH)
    assert "captCode" not in first.data
    assert retry.data["captCode"] == "x7k2"
    assert retry.url.query["_dc"].isdigit()
    assert retry.cookies["ick"] == "c3ludGhldGljLWljaw"


async def test_captcha_without_an_ick_cookie_fails_the_login(session):
    serve_login(session, Reply.xiaomi(LOGIN["captcha"]))
    session.on("GET", CAPTCHA, Reply(body=b"\x89PNG fake"))

    with pytest.raises(CloudAuthError, match="invalid_auth"):
        await XiaomiCloud(session, "de").login("user@example.com", "secret-password")


async def test_login_asks_for_two_factor_verification(session):
    serve_login(session, Reply.xiaomi(LOGIN["notification"]))

    with pytest.raises(CloudLoginRequired) as err:
        await XiaomiCloud(session, "de").login("user@example.com", "secret-password")

    assert err.value.verification_url == ACCOUNT + LOGIN["notification"]["notificationUrl"]
    assert err.value.captcha_png is None


async def test_login_without_a_service_token(session):
    serve_login(session)
    session.on("GET", STS, Reply(body="ok"))

    with pytest.raises(CloudAuthError, match="invalid_auth"):
        await XiaomiCloud(session, "de").login("user@example.com", "secret-password")


@pytest.mark.parametrize(
    "failure",
    [Reply(status=503), aiohttp.ClientConnectionError("boom"), TimeoutError()],
    ids=["http-503", "connection", "timeout"],
)
async def test_login_network_failures(session, failure):
    session.on("GET", SERVICE_LOGIN, failure)

    with pytest.raises(CloudConnectionError):
        await XiaomiCloud(session, "de").login("user@example.com", "secret-password")


@pytest.mark.parametrize("body", ["<html>maintenance</html>", "&&&START&&&[]"])
async def test_login_with_a_malformed_reply(session, body):
    session.on("GET", SERVICE_LOGIN, Reply(body=body))

    with pytest.raises(CloudConnectionError):
        await XiaomiCloud(session, "de").login("user@example.com", "secret-password")


async def test_login_without_a_sign(session):
    serve_login(session)
    session.on("GET", SERVICE_LOGIN, Reply.xiaomi({"code": 70016}))

    await XiaomiCloud(session, "de").login("user@example.com", "secret-password")

    assert "_sign" not in session.requests("POST", LOGIN_AUTH)[0].data


@pytest.mark.parametrize(
    ("reply", "expected"),
    [
        ({"code": 0, "message": "ok", "result": {}}, True),
        ({"code": 2, "message": "auth err"}, False),
        ({"code": 3, "message": "something"}, False),
        ({"code": -1, "message": "invalid signature"}, False),
        ({"code": -1, "message": "SERVICETOKEN_EXPIRED"}, False),
        (Reply(status=401), False),
    ],
)
async def test_check(session, reply, expected):
    session.api("v2/message/v2/check_new_msg", reply)
    cloud = XiaomiCloud(session, "de", STORED)

    assert await cloud.check() is expected

    [call] = session.requests("POST", "https://de.api.io.mi.com/app/v2/message/v2/check_new_msg")
    assert call.cookies["serviceToken"] == call.cookies["yetAnotherServiceToken"] == STORED.service_token
    assert call.cookies["userId"] == STORED.user_id
    assert call.headers["MIOT-ENCRYPT-ALGORITHM"] == "ENCRYPT-RC4"
    [(_path, data)] = session.api_calls
    assert isinstance(data["begin_at"], int)


async def test_check_without_a_session(session):
    assert await XiaomiCloud(session, "de").check() is False
    assert not session.calls


@pytest.mark.parametrize(
    "reply",
    [Reply(status=500), Reply(body="bm90IGpzb24="), Reply(body="not base64!"), lambda data: [1, 2]],
    ids=["http-500", "not-json", "not-base64", "not-an-object"],
)
async def test_check_failures(session, reply):
    session.api("v2/message/v2/check_new_msg", reply)

    with pytest.raises(CloudConnectionError):
        await XiaomiCloud(session, "de", STORED).check()


@pytest.mark.parametrize(
    ("country", "url"), [("de", "https://de.api.io.mi.com/app"), ("cn", "https://api.io.mi.com/app")]
)
async def test_api_url(session, country, url):
    assert XiaomiCloud(session, country).api_url == url


async def test_the_session_must_not_quote_cookies():
    async with aiohttp.ClientSession() as quoting:
        with pytest.raises(ValueError, match="quote_cookie"):
            XiaomiCloud(quoting, "de")


def test_auth_round_trips_through_a_dict():
    assert CloudAuth.from_dict(asdict(STORED)) == STORED
    assert CloudAuth.from_dict({k: v for k, v in asdict(STORED).items() if k != "pass_token"}).pass_token is None


async def test_no_secrets_in_logs_or_reprs(session, caplog):
    caplog.set_level(logging.DEBUG)
    serve_login(session)
    session.api("v2/message/v2/check_new_msg", {"code": 0, "result": {}})
    cloud = XiaomiCloud(session, "de")

    auth = await cloud.login("user@example.com", "secret-password")
    await cloud.check()

    assert caplog.records
    shown = caplog.text + repr(auth) + repr(cloud)
    for secret in ("secret-password", auth.ssecurity, auth.service_token, auth.pass_token, "nonce=synthetic"):
        assert secret not in shown
