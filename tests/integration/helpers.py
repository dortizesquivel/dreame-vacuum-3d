"""Shared helpers for integration platform tests."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.dreame_vacuum_3d.const import DOMAIN
from tests.integration.test_init import ENTRY_DATA, INFO, _mock_cloud

FIXTURE = Path(__file__).parents[1] / "fixtures" / "x10plus" / "properties.json"


def fixture_by_key() -> dict[str, dict[str, Any]]:
    return {item["key"]: item for item in json.loads(FIXTURE.read_text(encoding="utf-8"))}


def fixture_values(*, only_ok: bool = True) -> dict[str, Any]:
    """Property key → value from the X10+ dump (successful reads by default)."""
    out: dict[str, Any] = {}
    for key, row in fixture_by_key().items():
        if only_ok and row.get("code") != 0:
            continue
        out[key] = row.get("value")
    return out


class FixtureMiio:
    """miIO client that answers get_properties from the X10+ fixture and records writes."""

    def __init__(self, overrides: dict[tuple[int, int], Any] | None = None) -> None:
        by_key = fixture_by_key()
        self.by_siid_piid = {(item["siid"], item["piid"]): item for item in by_key.values()}
        self.overrides = dict(overrides or {})
        self.calls: list[tuple[str, Any]] = []
        self.send = AsyncMock(side_effect=self._send)
        self.close = AsyncMock()

    async def rpc(self, _did: str, method: str, params: Any = None) -> Any:
        """The same robot reached through the cloud."""
        return await self._send(method, params)

    async def _send(self, method: str, params: Any = None) -> Any:
        self.calls.append((method, params))
        if method == "miIO.info":
            return INFO
        if method == "get_properties":
            out = []
            for item in params or []:
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
            for item in params or []:
                self.overrides[(item["siid"], item["piid"])] = item["value"]
            return [
                {"did": p["did"], "siid": p["siid"], "piid": p["piid"], "code": 0} for p in (params or [])
            ]
        if method == "action":
            return {"did": "call", "siid": params["siid"], "aiid": params["aiid"], "code": 0, "out": []}
        raise AssertionError(f"unexpected method {method}")


async def setup_fixture_entry(
    hass: HomeAssistant,
    *,
    overrides: dict[tuple[int, int], Any] | None = None,
) -> tuple[MockConfigEntry, FixtureMiio]:
    """Load the integration against the X10+ property fixture."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data=ENTRY_DATA,
        unique_id=ENTRY_DATA["did"],
        title="X10+",
    )
    entry.add_to_hass(hass)
    miio = FixtureMiio(overrides)
    cloud = _mock_cloud()
    cloud.rpc = AsyncMock(side_effect=miio.rpc)

    with (
        patch("custom_components.dreame_vacuum_3d.XiaomiCloud", return_value=cloud),
        patch("custom_components.dreame_vacuum_3d.MiioClient", return_value=miio),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    return entry, miio
