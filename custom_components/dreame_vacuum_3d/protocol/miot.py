"""MIoT spec calls (properties by siid/piid, actions by siid/aiid) over any miIO-style RPC."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

Prop = tuple[int, int]  # (siid, piid)
Send = Callable[[str, Any], Awaitable[Any]]


class MiotError(Exception):
    """The device rejected a MIoT call."""

    def __init__(self, code: Any) -> None:
        super().__init__(f"MIoT error {code}")
        self.code = code


async def get_properties(send: Send, props: list[Prop], *, chunk: int = 15) -> dict[Prop, Any]:
    """Read props in batches of chunk; props the device can't read are left out."""
    values: dict[Prop, Any] = {}
    for start in range(0, len(props), chunk):
        params = [{"did": f"{siid}.{piid}", "siid": siid, "piid": piid} for siid, piid in props[start : start + chunk]]
        for item in await send("get_properties", params):
            if item.get("code") == 0:
                values[item["siid"], item["piid"]] = item.get("value")
    return values


async def set_property(send: Send, siid: int, piid: int, value: Any) -> None:
    """Write one property."""
    reply = await send("set_properties", [{"did": "set", "siid": siid, "piid": piid, "value": value}])
    for item in reply:
        if item.get("code") != 0:
            raise MiotError(item.get("code"))


async def call_action(send: Send, siid: int, aiid: int, params: list[dict] | None = None) -> Any:
    """Run an action and return the device's reply."""
    reply = await send("action", {"did": "call", "siid": siid, "aiid": aiid, "in": params or []})
    if reply.get("code") != 0:
        raise MiotError(reply.get("code"))
    return reply
