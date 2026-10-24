"""Tests of the Xiaomi two-factor login (identity flow) against a fake server."""

from __future__ import annotations

import json

import pytest

from custom_components.dreame_vacuum_3d.protocol.cloud import CloudAuthError, CloudLoginRequired, XiaomiCloud

from .fake_cloud import ACCOUNT, SSECURITY, FakeSession, Reply, fixture

LOGIN = fixture("login")
TWO_FA = fixture("two_factor")
AUTH_START = f"{ACCOUNT}/fe/service/identity/authStart"
IDENTITY = f"{ACCOUNT}/identity"
CONTEXT = "c3ludGhldGljLWNvbnRleHQ"
SERVICE_TOKEN = "c3ludGhldGljLXNlcnZpY2U/to+ken=="
STS = "https://sts.api.io.mi.com/sts"
FINISH = f"{IDENTITY}/finish"
AUTH2_END = f"{ACCOUNT}/pass/serviceLoginAuth2/end"
IDENTITY_SESSION = "c3ludGhldGljLWlkZW50aXR5"


@pytest.fixture
async def session() -> FakeSession:
    session = FakeSession()
    session.on("GET", f"{ACCOUNT}/pass/serviceLogin", Reply.xiaomi(LOGIN["service_login"]))
    session.on("POST", f"{ACCOUNT}/pass/serviceLoginAuth2", Reply.xiaomi(LOGIN["notification"]))
    session.on("GET", AUTH_START, Reply(body="<html>verify</html>"))
    return session


async def start(session: FakeSession) -> tuple[XiaomiCloud, str]:
    """Log in until the cloud asks for the two-factor verification."""
    cloud = XiaomiCloud(session, "de")
    with pytest.raises(CloudLoginRequired) as err:
        await cloud.login("user@example.com", "secret-password")
    assert err.value.verification_url
    return cloud, err.value.verification_url


def serve_identity(session: FakeSession, kind: str = "phone", *, list_reply: str | None = None) -> str:
    key = kind.capitalize()
    cookies = {"identity_session": IDENTITY_SESSION}
    session.on("GET", f"{IDENTITY}/list", Reply.xiaomi(TWO_FA[list_reply or f"list_{kind}"], cookies=cookies))
    session.on("GET", f"{IDENTITY}/auth/verify{key}", Reply.xiaomi(TWO_FA[f"verify_{kind}"]))
    session.on("POST", f"{IDENTITY}/auth/send{key}Ticket", Reply.xiaomi(TWO_FA["sent"]))
    return key


@pytest.mark.parametrize(
    ("kind", "list_reply", "masked"),
    [
        ("phone", None, "+49*****1234"),
        ("email", None, "u***@example.com"),
        ("email", "list_flag_only", "u***@example.com"),
    ],
)
async def test_send_code(session, kind, list_reply, masked):
    key = serve_identity(session, kind, list_reply=list_reply)
    cloud, url = await start(session)

    assert await cloud.send_2fa_code(url) == masked

    flag = "4" if kind == "phone" else "8"
    assert str(session.requests("GET", AUTH_START)[0].url) == url
    [listing] = session.requests("GET", f"{IDENTITY}/list")
    assert listing.url.query == {"sid": "xiaomiio", "context": CONTEXT, "_locale": listing.url.query["_locale"]}
    [verify] = session.requests("GET", f"{IDENTITY}/auth/verify{key}")
    assert verify.url.query["_flag"] == flag
    assert verify.url.query["context"] == CONTEXT
    assert verify.cookies["identity_session"] == IDENTITY_SESSION
    [send] = session.requests("POST", f"{IDENTITY}/auth/send{key}Ticket")
    assert send.url.query["context"] == CONTEXT
    assert send.cookies["identity_session"] == IDENTITY_SESSION
    assert send.data == {"retry": "0", "icode": "", "_json": "true", "ick": ""}


async def test_send_code_falls_back_to_a_masked_placeholder(session):
    serve_identity(session)
    session.on("GET", f"{IDENTITY}/auth/verifyPhone", Reply.xiaomi({"code": 0}))
    cloud, url = await start(session)

    assert await cloud.send_2fa_code(url) == "*****"


@pytest.mark.parametrize(
    ("step", "reply", "error"),
    [
        ("verify", "too_frequent", "2fa_cooldown_active"),
        ("send", "too_frequent", "2fa_cooldown_active"),
        ("verify", "limit", "2fa_limit_reached"),
        ("send", "limit", "2fa_limit_reached"),
        ("send", "send_error", "2fa_send_failed"),
    ],
)
async def test_send_code_errors(session, step, reply, error):
    serve_identity(session)
    url = f"{IDENTITY}/auth/verifyPhone" if step == "verify" else f"{IDENTITY}/auth/sendPhoneTicket"
    session.on("GET" if step == "verify" else "POST", url, Reply.xiaomi(TWO_FA[reply]))
    cloud, verification_url = await start(session)

    with pytest.raises(CloudAuthError, match=error):
        await cloud.send_2fa_code(verification_url)


async def test_send_code_without_an_identity_session(session):
    serve_identity(session)
    session.on("GET", f"{IDENTITY}/list", Reply.xiaomi(TWO_FA["list_phone"]))
    cloud, url = await start(session)

    with pytest.raises(CloudAuthError, match="2fa_send_failed"):
        await cloud.send_2fa_code(url)
    assert not session.requests("GET", f"{IDENTITY}/auth/verifyPhone")


async def test_send_code_needs_an_identity_url(session):
    with pytest.raises(CloudAuthError, match="2fa_send_failed"):
        await XiaomiCloud(session, "de").send_2fa_code(f"{ACCOUNT}/fe/service/other?context={CONTEXT}")
    assert not session.calls


def serve_verification(session: FakeSession, verified: str = "verified") -> None:
    session.on("POST", f"{IDENTITY}/auth/verifyPhone", Reply.xiaomi(TWO_FA[verified]))


async def test_wrong_code(session):
    serve_identity(session)
    serve_verification(session, "wrong_code")
    cloud, url = await start(session)
    await cloud.send_2fa_code(url)

    with pytest.raises(CloudAuthError, match="2fa_failed"):
        await cloud.verify_2fa_code("000000")
    assert cloud.auth is None


async def test_correct_code_finishes_the_login_through_the_pass_token(session):
    serve_identity(session)
    serve_verification(session)
    session.on("GET", FINISH, Reply(body="ok", cookies={"passToken": LOGIN["service_login_ok"]["passToken"]}))
    cloud, url = await start(session)
    await cloud.send_2fa_code(url)
    session.on("GET", f"{ACCOUNT}/pass/serviceLogin", Reply.xiaomi(LOGIN["service_login_ok"]))
    session.on("GET", STS, Reply(body="ok", cookies={"serviceToken": SERVICE_TOKEN}))

    auth = await cloud.verify_2fa_code("123456")

    assert (auth.user_id, auth.ssecurity, auth.service_token) == ("1000001", SSECURITY, SERVICE_TOKEN)
    assert auth.pass_token == LOGIN["service_login_ok"]["passToken"]
    assert cloud.auth == auth
    [verify] = session.requests("POST", f"{IDENTITY}/auth/verifyPhone")
    assert verify.data == {"_flag": "4", "ticket": "123456", "trust": "false", "_json": "true", "ick": ""}
    assert verify.url.query["context"] == CONTEXT
    assert verify.cookies["identity_session"] == IDENTITY_SESSION
    assert not session.requests("GET", f"{IDENTITY}/result/check")


def serve_sts(session: FakeSession, *auth2_end: Reply) -> None:
    session.on(
        "GET", f"{IDENTITY}/result/check", Reply(status=302, headers={"Location": TWO_FA["result_check"]["location"]})
    )
    pragma = {"extension-pragma": json.dumps(TWO_FA["pragma"]), "Location": TWO_FA["sts_location"]}
    session.on("GET", AUTH2_END, *(auth2_end or [Reply(status=302, headers=pragma)]))
    session.on("GET", STS, Reply(body="ok", cookies={"serviceToken": SERVICE_TOKEN, "userId": "1000001"}))


PRAGMA = json.dumps(TWO_FA["pragma"])
STS_IN_BODY = Reply(
    body=f'<script>location.href="{TWO_FA["sts_location"]}"</script>', headers={"extension-pragma": PRAGMA}
)


@pytest.mark.parametrize(
    "auth2_end",
    [
        [],
        [STS_IN_BODY],
        [Reply(body="<title>Xiaomi Account - Tips</title>"), STS_IN_BODY],
    ],
    ids=["redirect", "url-in-body", "tips-page"],
)
async def test_correct_code_finishes_the_login_through_sts(session, auth2_end):
    serve_identity(session)
    serve_verification(session, "verified_without_location")
    serve_sts(session, *auth2_end)
    cloud, url = await start(session)
    await cloud.send_2fa_code(url)

    auth = await cloud.verify_2fa_code("123456")

    assert (auth.user_id, auth.ssecurity, auth.service_token) == ("1000001", SSECURITY, SERVICE_TOKEN)
    [check] = session.requests("GET", f"{IDENTITY}/result/check")
    assert not check.allow_redirects
    assert check.url.query["context"] == CONTEXT
    assert all(not c.allow_redirects for c in session.requests("GET", AUTH2_END))
    assert str(session.requests("GET", STS)[0].url) == TWO_FA["sts_location"]


async def test_result_check_location_in_the_body(session):
    serve_identity(session)
    serve_verification(session, "verified_without_location")
    serve_sts(session)
    session.on("GET", f"{IDENTITY}/result/check", Reply.xiaomi(TWO_FA["result_check"]))
    cloud, url = await start(session)
    await cloud.send_2fa_code(url)

    assert (await cloud.verify_2fa_code("123456")).service_token == SERVICE_TOKEN


@pytest.mark.parametrize("failure", ["refused", "finish-error", "no-service-token"])
async def test_falls_back_to_sts_when_the_pass_token_login_fails(session, failure):
    serve_identity(session)
    serve_verification(session)
    session.on("GET", FINISH, Reply(status=500) if failure == "finish-error" else Reply(body="ok"))
    serve_sts(session)
    cloud, url = await start(session)
    await cloud.send_2fa_code(url)
    if failure == "no-service-token":
        session.on("GET", f"{ACCOUNT}/pass/serviceLogin", Reply.xiaomi(LOGIN["service_login_ok"]))
        session.on("GET", STS, Reply(body="ok"), Reply(body="ok", cookies={"serviceToken": SERVICE_TOKEN}))

    assert (await cloud.verify_2fa_code("123456")).service_token == SERVICE_TOKEN
    assert session.requests("GET", f"{IDENTITY}/result/check")


@pytest.mark.parametrize(
    "verified",
    [
        Reply(body="<html>done</html>", headers={"Location": "https://account.xiaomi.com/identity/finish?x=1"}),
        Reply(status=302, headers={"Location": "https://account.xiaomi.com/identity/finish?x=1"}),
    ],
    ids=["html", "redirect"],
)
async def test_verify_location_from_headers(session, verified):
    serve_identity(session)
    session.on("POST", f"{IDENTITY}/auth/verifyPhone", verified)
    session.on("GET", FINISH, Reply(body="ok"))
    serve_sts(session)
    cloud, url = await start(session)
    await cloud.send_2fa_code(url)

    await cloud.verify_2fa_code("123456")

    assert session.requests("GET", FINISH)


NO_PRAGMA = Reply(status=302, headers={"Location": TWO_FA["sts_location"]})
BAD_PRAGMA = Reply(status=302, headers={"extension-pragma": "{oops", "Location": TWO_FA["sts_location"]})
NO_STS = Reply(body="<html></html>", headers={"extension-pragma": PRAGMA})


@pytest.mark.parametrize(
    "broken",
    ["no-check-location", "no-pragma", "bad-pragma", "no-sts-url", "sts-error", "no-service-token"],
)
async def test_sts_failures(session, broken):
    serve_identity(session)
    serve_verification(session, "verified_without_location")
    serve_sts(session)
    match broken:
        case "no-check-location":
            session.on("GET", f"{IDENTITY}/result/check", Reply(status=200))
        case "no-pragma":
            session.on("GET", AUTH2_END, NO_PRAGMA)
        case "bad-pragma":
            session.on("GET", AUTH2_END, BAD_PRAGMA)
        case "no-sts-url":
            session.on("GET", AUTH2_END, NO_STS)
        case "sts-error":
            session.on("GET", STS, Reply(status=500))
        case "no-service-token":
            session.on("GET", STS, Reply(body="ok"))
    cloud, url = await start(session)
    await cloud.send_2fa_code(url)

    with pytest.raises(CloudAuthError, match="2fa_failed"):
        await cloud.verify_2fa_code("123456")


async def test_verify_needs_a_sent_code(session):
    with pytest.raises(CloudAuthError, match="2fa_failed"):
        await XiaomiCloud(session, "de").verify_2fa_code("123456")


async def test_verify_needs_a_context(session):
    serve_identity(session)
    cloud = XiaomiCloud(session, "de")
    await cloud.send_2fa_code(f"{AUTH_START}?sid=xiaomiio")
    session.calls.clear()

    with pytest.raises(CloudAuthError, match="2fa_failed"):
        await cloud.verify_2fa_code("123456")
    assert not session.calls


async def test_verify_without_an_identity_session(session):
    serve_identity(session)
    cloud, url = await start(session)
    await cloud.send_2fa_code(url)
    session.cookie_jar.clear()
    session.on("GET", f"{IDENTITY}/list", Reply.xiaomi(TWO_FA["list_phone"]))

    with pytest.raises(CloudAuthError, match="2fa_failed"):
        await cloud.verify_2fa_code("123456")
    assert not session.requests("POST", f"{IDENTITY}/auth/verifyPhone")
