"""The cleaning history, read from the events the robot sends the cloud."""

from __future__ import annotations

import json

from custom_components.dreame_vacuum_3d.device import history
from custom_components.dreame_vacuum_3d.device.capabilities import load

CAPS = load("dreame.vacuum.p2114a")


def _event(start: object, *, status: int = 1, file: str | None = "1000001/123/h,key", field: str = "value") -> dict:
    # The status service's properties (siid 4): status, cleaning time, area, start time, log status and file.
    items = [{"piid": 1, "value": 2}, {"piid": 2, "value": 12}, {"piid": 3, "val": 20}, {"piid": 8, "value": start}]
    items += [{"piid": 13, "value": status}, {"piid": 99, "value": "ignored"}, "not an item"]
    if file is not None:
        items.append({"piid": 9, "value": file})
    return {"time": 1, field: json.dumps(items)}


def test_events_come_from_the_status_property() -> None:
    assert history.events_key(CAPS) == "4.1"


def test_cleanings_newest_first_once_each() -> None:
    records = [
        _event(200),
        _event(200),  # the robot sends some twice
        _event(100, status=0, file="", field="history"),
        _event(True),
        _event(0),
        {"value": "not json"},
        {"value": "{}"},
        {"value": "[]"},
    ]
    assert history.parse(records, CAPS) == [
        {"start": 200, "duration": 12, "area": 20, "completed": True, "file": "1000001/123/h,key"},
        {"start": 100, "duration": 12, "area": 20, "completed": False, "file": None},
    ]
