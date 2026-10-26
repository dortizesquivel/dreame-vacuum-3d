"""The robot's cleaning history: the events it sends the Xiaomi cloud when its status changes.

Each event holds the status service's properties at the end of a cleaning: when it started, how long it took, the
area, whether it finished and the name of its log file, a whole map frame with the cleaning's path.

Portions derived from Tasshack/dreame-vacuum (MIT), see NOTICE.
"""

from __future__ import annotations

import json
from typing import Any

from .capabilities import Capabilities


def events_key(caps: Capabilities) -> str:
    """The cloud key of the events: the status property's "siid.piid"."""
    siid, piid = caps.properties["status"]
    return f"{siid}.{piid}"


def parse(records: list[dict[str, Any]], caps: Capabilities) -> list[dict[str, Any]]:
    """Cleanings from the cloud's event records (newest first): [{start (s), duration (min), area (m²), completed,
    file ("<object name>,<key>" or None)}]. The robot sends some events twice; the second of each start is dropped."""
    siid = caps.properties["status"][0]
    names = {piid: key for key, (s, piid) in caps.properties.items() if s == siid}
    out: list[dict[str, Any]] = []
    for record in records:
        raw = record.get("history") or record.get("value")
        try:
            items = json.loads(raw) if isinstance(raw, str) else raw
        except ValueError:
            continue
        if not isinstance(items, list):
            continue
        values = {
            names[piid]: i.get("value", i.get("val"))
            for i in items
            if isinstance(i, dict) and (piid := i.get("piid")) in names
        }
        start = values.get("cleaning_start_time")
        if not isinstance(start, int) or isinstance(start, bool) or start <= 0 or (out and out[-1]["start"] == start):
            continue
        file = values.get("clean_log_file_name")
        status = values.get("clean_log_status")
        out.append(
            {
                "start": start,
                "duration": _number(values.get("cleaning_time")),
                "area": _number(values.get("cleaned_area")),
                "completed": None if status is None else status in (1, True),
                "file": file if isinstance(file, str) and len(file) > 1 else None,
            }
        )
    return out


def _number(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None
