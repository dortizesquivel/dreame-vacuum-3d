"""MIoT spec calls (properties by siid/piid, actions by siid/aiid) over any miIO-style RPC."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

Prop = tuple[int, int]  # (siid, piid)
Send = Callable[[str, Any], Awaitable[Any]]


class MiotError(Exception):
    """The device rejected a MIoT call or answered something unusable.

    Not a MiioError: these calls run over the local miIO client or the cloud alike.
    """

    def __init__(self, code: Any, message: str | None = None) -> None:
        super().__init__(message or f"MIoT error {code}")
        self.code = code


async def get_properties(send: Send, props: list[Prop], *, chunk: int = 15) -> dict[Prop, Any]:
    """Read props in batches of chunk; props the device can't read are left out."""
    values: dict[Prop, Any] = {}
    for start in range(0, len(props), chunk):
        by_did = {f"{siid}.{piid}": (siid, piid) for siid, piid in props[start : start + chunk]}
        params = [{"did": did, "siid": siid, "piid": piid} for did, (siid, piid) in by_did.items()]
        reply = await send("get_properties", params)
        for item in reply if isinstance(reply, list) else []:
            if not isinstance(item, dict) or item.get("code") != 0:
                continue
            did = item.get("did")
            if isinstance(did, str) and did in by_did:
                values[by_did[did]] = item.get("value")
    return values


async def set_property(send: Send, siid: int, piid: int, value: Any) -> None:
    """Write one property."""
    reply = await send("set_properties", [{"did": "set", "siid": siid, "piid": piid, "value": value}])
    if not isinstance(reply, list) or not reply:
        raise MiotError(None, f"malformed set_properties reply: {reply!r}")
    for item in reply:
        code = item.get("code") if isinstance(item, dict) else None
        if code != 0:
            raise MiotError(code)


async def call_action(send: Send, siid: int, aiid: int, params: list[dict] | None = None) -> Any:
    """Run an action and return the device's reply."""
    reply = await send("action", {"did": "call", "siid": siid, "aiid": aiid, "in": params or []})
    if not isinstance(reply, dict):
        raise MiotError(None, f"malformed action reply: {reply!r}")
    if reply.get("code") != 0:
        raise MiotError(reply.get("code"))
    return reply
