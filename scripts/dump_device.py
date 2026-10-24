#!/usr/bin/env python3
"""Dump a Dreame robot's miIO info, MIoT properties and maps, for test fixtures and for mapping new models.

Run from the repo root:

    python scripts/dump_device.py --country de --user EMAIL --out dumps/x10plus-20261024
    python scripts/dump_device.py --redact dumps/x10plus-20261024/properties.json \
        > tests/fixtures/x10plus/properties.json

The raw dump holds the miIO token and network identifiers: keep it in dumps/ (gitignored) and commit
only redacted output.
"""

from __future__ import annotations

import argparse
import asyncio
from collections.abc import Awaitable, Callable, Iterable, Mapping, Sequence
from datetime import datetime
import getpass
import json
from pathlib import Path
import re
import secrets
import sys
import time
import types
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
# The integration's __init__ imports Home Assistant: register the package bare, so only the
# HA-free protocol and device modules load.
if "custom_components.dreame_vacuum_3d" not in sys.modules:
    _package = types.ModuleType("custom_components.dreame_vacuum_3d")
    _package.__path__ = [str(ROOT / "custom_components" / "dreame_vacuum_3d")]
    sys.modules[_package.__name__] = _package

import aiohttp

from custom_components.dreame_vacuum_3d.device.capabilities import FAMILY, Capabilities, load
from custom_components.dreame_vacuum_3d.protocol import miio_codec as codec
from custom_components.dreame_vacuum_3d.protocol.cloud import CloudError, CloudLoginRequired, XiaomiCloud
from custom_components.dreame_vacuum_3d.protocol.miio import MiioClient, MiioCommandError, MiioTimeoutError
from custom_components.dreame_vacuum_3d.protocol.miot import Prop, Send, get_properties

VERSION = 1
CHUNK = 15
SCAN_SIIDS = range(1, 41)
SCAN_PIIDS = range(1, 111)
VACUUMS = ("dreame.vacuum.", "xiaomi.vacuum.")
LOGIN_ATTEMPTS = 5
LIVE_MAP_EVERY = 3
PACKET_TIMEOUT = 5.0
_TOKEN_HEX = re.compile(r"[0-9a-fA-F]{32}")

# Redaction: property names made of these words, and string values with these shapes.
_SECRET_WORDS = {"serial", "mac", "ip", "localip", "ssid", "bssid", "did", "uid", "token"}
_MAC = re.compile(r"\b[0-9a-f]{2}(?:[:-][0-9a-f]{2}){5}\b", re.IGNORECASE)
_IP = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
_TOKEN = re.compile(r"\b[0-9a-f]{32}\b", re.IGNORECASE)
_SECRET_FIELD = re.compile(r'"(?:mac|ip|localip|local_ip|ssid|bssid|did|uid|token|sn|serial)"\s*:', re.IGNORECASE)


class DumpError(Exception):
    """Something the user has to fix; the message says what."""


def chunked[T](items: Sequence[T], size: int = CHUNK) -> list[Sequence[T]]:
    return [items[start : start + size] for start in range(0, len(items), size)]


async def read_raw(send: Send, props: Sequence[Prop], caps: Capabilities) -> list[dict[str, Any]]:
    """Read props in batches and keep every item's code and value, failures included.

    A prop the reply leaves out gets code None; a batch the robot refuses as a whole gets the error's code.
    """
    items: list[dict[str, Any]] = []
    for batch in chunked(props):
        params = [{"did": f"{siid}.{piid}", "siid": siid, "piid": piid} for siid, piid in batch]
        refused = None
        try:
            reply = await send("get_properties", params)
        except MiioCommandError as err:
            reply, refused = [], err.code
        answers = {a.get("did"): a for a in reply if isinstance(a, dict)} if isinstance(reply, list) else {}
        for siid, piid in batch:
            answer = answers.get(f"{siid}.{piid}", {})
            items.append(
                {
                    "key": caps.key_for(siid, piid),
                    "siid": siid,
                    "piid": piid,
                    "code": answer.get("code", refused),
                    "value": answer.get("value"),
                }
            )
    return items


async def scan(
    send: Send,
    caps: Capabilities,
    *,
    siids: Iterable[int] = SCAN_SIIDS,
    piids: Iterable[int] = SCAN_PIIDS,
    progress: Callable[[str], None] = print,
) -> list[dict[str, Any]]:
    """Sweep siids x piids and return the props that answered (key None if the table doesn't know them)."""
    piids = list(piids)
    hits: list[dict[str, Any]] = []
    for siid in siids:
        found = [i for i in await read_raw(send, [(siid, piid) for piid in piids], caps) if i["code"] == 0]
        hits += found
        unmapped = sum(i["key"] is None for i in found)
        progress(f"scan siid {siid}: {len(found)} answered, {unmapped} unmapped")
    return hits


def redact(items: Iterable[Mapping[str, Any]], device: Mapping[str, Any] | None = None) -> list[dict[str, Any]]:
    """Copy property items with identifying values blanked ("" for strings, None otherwise).

    Blanked: serial numbers and other identifier-named props, values that look like a MAC, an IP, a
    token or JSON with such fields, and values containing the device's did, mac, token or IP.
    """
    needles = set()
    for name in ("did", "mac", "token", "localip"):
        if (text := str((device or {}).get(name) or "").lower()) and len(text) >= 4:
            needles |= {text, text.replace(":", "")}
    redacted = []
    for item in items:
        copy = dict(item)
        if _secret(copy.get("key"), copy.get("value"), needles):
            copy["value"] = "" if isinstance(copy.get("value"), str) else None
        redacted.append(copy)
    return redacted


def _secret(key: Any, value: Any, needles: set[str]) -> bool:
    if isinstance(key, str) and _SECRET_WORDS & set(key.lower().split("_")):
        return True
    if value is None or isinstance(value, bool):
        return False
    text = str(value)
    if isinstance(value, str) and any(p.search(text) for p in (_MAC, _IP, _TOKEN, _SECRET_FIELD)):
        return True
    return any(needle in text.lower() for needle in needles)


def pick_device(
    devices: Iterable[Mapping[str, Any]],
    *,
    model: str | None = None,
    host: str | None = None,
    ask: Callable[[str], str] = input,
) -> Mapping[str, Any]:
    """Choose the vacuum to dump: by model, then by IP if one matches, else ask."""
    vacuums = [d for d in devices if str(d.get("model", "")).startswith(VACUUMS)]
    if model:
        vacuums = [d for d in vacuums if d.get("model") == model]
    if host and (at_host := [d for d in vacuums if d.get("localip") == host]):
        vacuums = at_host
    if not vacuums:
        raise DumpError("no Dreame vacuum on this account" + (f" with model {model}" if model else ""))
    if len(vacuums) == 1:
        return vacuums[0]
    for number, device in enumerate(vacuums, 1):
        print(f"{number}. {device.get('name')} ({device.get('model')}, {device.get('localip')})")
    while True:
        answer = ask("device number: ").strip()
        if answer.isdigit() and 1 <= int(answer) <= len(vacuums):
            return vacuums[int(answer) - 1]


def capabilities(model: str) -> Capabilities:
    """The model's table; xiaomi.vacuum.* models get the Dreame base."""
    return load(model if model.startswith(FAMILY) else FAMILY + "generic")


def save(path: Path, data: Any) -> None:
    if isinstance(data, bytes):
        path.write_bytes(data)
    else:
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")


def append_line(path: Path, data: Any) -> None:
    with path.open("a") as file:
        file.write(json.dumps(data, ensure_ascii=False) + "\n")


async def timed[T](timings: dict[str, float], name: str, step: Awaitable[T]) -> T:
    start = time.monotonic()
    try:
        return await step
    finally:
        timings[name] = round(time.monotonic() - start, 2)


async def login(cloud: XiaomiCloud, user: str, password: str, out: Path) -> None:
    """Log in, asking for the captcha and the verification code when the account wants them."""
    captcha = None
    for _ in range(LOGIN_ATTEMPTS):
        try:
            await cloud.login(user, password, captcha=captcha)
            return
        except CloudLoginRequired as need:
            if need.captcha_png:
                save(out / "captcha.png", need.captcha_png)
                print(f"captcha saved to {out / 'captcha.png'}")
                captcha = (await asyncio.to_thread(input, "captcha text: ")).strip()
            elif need.verification_url:
                print(f"verification code sent to {await cloud.send_2fa_code(need.verification_url)}")
                await cloud.verify_2fa_code((await asyncio.to_thread(input, "verification code: ")).strip())
                return
            else:
                raise
    raise DumpError(f"login still wants a captcha after {LOGIN_ATTEMPTS} attempts")


class _Udp(asyncio.DatagramProtocol):
    def __init__(self) -> None:
        self.queue: asyncio.Queue[bytes] = asyncio.Queue()

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        self.queue.put_nowait(data)


async def capture_packets(host: str, token_hex: str, *, port: int = 54321) -> dict[str, Any]:
    """One real battery_level read and its reply as hex, on a socket of its own.

    The payloads hold nothing identifying, so the packets can become a public fixture; the token can't.
    """
    token = bytes.fromhex(token_hex)
    params = [{"did": "3.1", "siid": 3, "piid": 1}]
    payload = {"id": secrets.randbelow(9000) + 1, "method": "get_properties", "params": params}
    transport, udp = await asyncio.get_running_loop().create_datagram_endpoint(_Udp, remote_addr=(host, port))
    try:
        async with asyncio.timeout(PACKET_TIMEOUT):
            transport.sendto(codec.HELLO)
            while len(hello := await udp.queue.get()) != codec.HEADER_LEN:
                pass
            session = codec.parse_header(hello)
            request = codec.build(token, session.device_id, session.stamp + 1, payload)
            transport.sendto(request)
            while True:
                reply = await udp.queue.get()
                header, decoded = codec.parse(token, reply)
                if isinstance(decoded, dict) and decoded.get("id") == payload["id"]:
                    break
    finally:
        transport.close()
    return {
        "device_id": session.device_id,
        "stamp": session.stamp,
        "request_stamp": session.stamp + 1,
        "reply_stamp": header.stamp,
        "request": payload,
        "reply": decoded,
        "request_hex": request.hex(),
        "reply_hex": reply.hex(),
        "note": "encrypted with the robot's token, which is only in device.json and is never committed",
    }


async def fetch_map(cloud: XiaomiCloud, url: str | None, path: Path) -> bool:
    data = await cloud.download(url) if url else None
    if data is not None:
        save(path, data)
    return data is not None


async def watch(
    client: MiioClient, cloud: XiaomiCloud, did: str, caps: Capabilities, items: list[dict[str, Any]], args: Any
) -> dict[str, int]:
    """Log property changes to watch.jsonl and fetch the live map, for args.watch seconds."""
    last = {(i["siid"], i["piid"]): i["value"] for i in items if i["code"] == 0}
    props = list(last)
    object_ids = caps.properties.get("object_name")
    last_object, ticks, maps = None, 0, 0
    start = time.monotonic()
    while (elapsed := time.monotonic() - start) < args.watch:
        ticks += 1
        try:
            values, source = await get_properties(client.send, props), "local"
        except codec.MiioError:
            try:
                values, source = await get_properties(cloud.rpc_sender(did), props), "cloud"
            except CloudError as err:
                print(f"watch tick {ticks}: local and cloud failed ({err})")
                values, source = {}, None
        changes = {caps.key_for(*p) or f"{p[0]}.{p[1]}": v for p, v in values.items() if p not in last or last[p] != v}
        last.update(values)
        append_line(args.out / "watch.jsonl", {"t": round(elapsed, 1), "source": source, "changes": changes})
        name = values.get(object_ids) if object_ids else None
        if name and (name != last_object or ticks % LIVE_MAP_EVERY == 0):
            last_object = name
            try:
                url = await cloud.interim_file_url(name, model=caps.model)
                maps += await fetch_map(cloud, url, args.out / f"map_live_{ticks}.raw")
            except CloudError as err:
                print(f"watch tick {ticks}: live map failed ({err})")
        print(f"watch tick {ticks}: {len(changes)} changed")
        await asyncio.sleep(max(0.0, args.interval - (time.monotonic() - start - elapsed)))
    return {"ticks": ticks, "live_maps": maps}


def _counts(items: list[dict[str, Any]]) -> tuple[int, int]:
    ok = sum(i["code"] == 0 for i in items)
    return ok, len(items) - ok


async def dump(args: Any, password: str) -> None:
    out: Path = args.out
    timings: dict[str, float] = {}
    summary: dict[str, Any] = {"script_version": VERSION, "started": datetime.now().isoformat(timespec="seconds")}
    skipped: list[str] = []
    async with aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar(quote_cookie=False)) as session:
        cloud = XiaomiCloud(session, args.country)
        await timed(timings, "login", login(cloud, args.user, password, out))
        device = pick_device(await cloud.devices(), model=args.model, host=args.host)
        did, model = str(device.get("did")), str(device.get("model"))
        host, token = args.host or device.get("localip"), args.token or device.get("token")
        if not host or not token or not _TOKEN_HEX.fullmatch(token):
            raise DumpError("the cloud gives no usable IP or token for this robot: pass --host and --token")
        caps = capabilities(model)
        print(f"dumping {device.get('name')} ({model}) at {host}")
        client = MiioClient(host, token)
        try:
            info = await timed(timings, "info", client.send("miIO.info"))
            save(out / "info.json", info)
            info = info if isinstance(info, dict) else {}
            device_info = {
                "did": did,
                "model": model,
                "name": device.get("name"),
                "localip": host,
                "cloud_localip": device.get("localip"),
                "mac": device.get("mac") or info.get("mac"),
                "token": token,
                "firmware": info.get("fw_ver"),
            }
            save(out / "device.json", device_info)
            try:
                save(out / "packets.json", await timed(timings, "packets", capture_packets(host, token)))
            except (TimeoutError, OSError, codec.MiioError) as err:
                skipped.append(f"packets ({type(err).__name__}: {err})")

            props = sorted(set(caps.properties.values()))
            local = await timed(timings, "properties_local", read_raw(client.send, props, caps))
            save(out / "properties.json", local)
            remote = await timed(timings, "properties_cloud", read_raw(cloud.rpc_sender(did), props, caps))
            save(out / "properties_cloud.json", remote)
            summary["local_ok"], summary["local_failed"] = _counts(local)
            summary["cloud_ok"], summary["cloud_failed"] = _counts(remote)

            if args.no_scan:
                skipped.append("scan (--no-scan)")
            else:
                hits = await timed(timings, "scan", scan(client.send, caps))
                save(out / "scan.json", hits)
                summary["scan_hits"], summary["scan_unmapped"] = len(hits), sum(h["key"] is None for h in hits)

            name = next((i["value"] for i in local + remote if i["key"] == "object_name" and i["code"] == 0), None)
            if name:
                save(out / "map_object.json", name)
                url = await cloud.file_url(name, model=model)
                if not await timed(timings, "map_saved", fetch_map(cloud, url, out / "map_saved.raw")):
                    skipped.append("map_saved (the cloud has no file for object_name)")
            else:
                skipped.append("map_object, map_saved (no object_name)")

            summary |= {"timings": timings, "skipped": skipped}
            save(out / "summary.json", summary)
            if args.watch > 0:
                summary["watch"] = await timed(timings, "watch", watch(client, cloud, did, caps, local, args))
                save(out / "summary.json", summary)
        finally:
            await client.close()
    print(f"done: {out}")


def redact_file(path: Path) -> None:
    items = json.loads(path.read_text())
    if not isinstance(items, list) or not all(isinstance(i, dict) for i in items):
        raise DumpError(f"{path}: not a list of property items")
    device_path = path.parent / "device.json"
    device = json.loads(device_path.read_text()) if device_path.is_file() else None
    print(json.dumps(redact(items, device), indent=2, ensure_ascii=False))


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Dump a Dreame robot's miIO info, MIoT properties and maps.")
    parser.add_argument("--redact", type=Path, metavar="FILE", help="print a redacted copy of a properties dump")
    parser.add_argument("--country", help="Xiaomi cloud server: cn, de, us, ru, sg, i2...")
    parser.add_argument("--user", help="Xiaomi account e-mail, phone or id")
    parser.add_argument("--host", help="robot IP (default: from the cloud)")
    parser.add_argument("--token", help="miIO token, 32 hex characters (default: from the cloud)")
    parser.add_argument("--model", help="dump the device of this model when the account has several")
    parser.add_argument("--watch", type=float, default=0, metavar="SECONDS", help="then log changes for this long")
    parser.add_argument("--interval", type=float, default=3, metavar="SECONDS", help="watch period (default 3)")
    parser.add_argument("--no-scan", action="store_true", help="skip the siid/piid sweep")
    parser.add_argument("--out", type=Path, help="output directory (default: dumps/dump-<timestamp>)")
    args = parser.parse_args(argv)
    if args.redact is None:
        if not (args.country and args.user):
            parser.error("--country and --user are required (unless --redact)")
        if args.token and not _TOKEN_HEX.fullmatch(args.token):
            parser.error("--token must be 32 hex characters")
        args.out = args.out or Path("dumps") / f"dump-{datetime.now():%Y%m%d-%H%M%S}"
    return args


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        if args.redact is not None:
            redact_file(args.redact)
        else:
            args.out.mkdir(parents=True, exist_ok=True)
            asyncio.run(dump(args, getpass.getpass(f"Xiaomi password for {args.user}: ")))
    except codec.MiioChecksumError as err:
        print(f"error: {err} (the token doesn't match this robot)", file=sys.stderr)
    except MiioTimeoutError as err:
        print(f"error: {err}: the robot is offline or not at this IP", file=sys.stderr)
    except codec.MiioError as err:
        print(f"error: miIO: {err}", file=sys.stderr)
    except CloudError as err:
        print(f"error: Xiaomi cloud: {err}", file=sys.stderr)
    except DumpError as err:
        print(f"error: {err}", file=sys.stderr)
    except KeyboardInterrupt:
        return 130
    else:
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
