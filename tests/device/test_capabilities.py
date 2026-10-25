"""Tests of the declarative capability table."""

from __future__ import annotations

from collections import Counter
from pathlib import Path

import pytest

from custom_components.dreame_vacuum_3d.device.capabilities import MODELS_DIR, load

# Tasshack maps these property names onto the same (siid, piid).
ALIASES = {(7, 10): ("voice_assistant_language", "listen_language_type")}


def _write(models: Path, name: str, text: str) -> None:
    (models / f"{name}.yaml").write_text(text)


@pytest.fixture
def models(tmp_path: Path) -> Path:
    _write(
        tmp_path,
        "dreame_base",
        "properties:\n  state: [2, 1]\n  error: [2, 2]\n  volume: [7, 1]\nactions:\n  start: [2, 1]\n  pause: [2, 2]\n",
    )
    return tmp_path


def test_x10_plus_extends_the_base() -> None:
    caps = load("dreame.vacuum.p2114a")
    base = load("dreame.vacuum.unknown")
    assert caps.model == "dreame.vacuum.p2114a"
    assert caps.name == "Xiaomi Robot Vacuum X10+"
    assert caps.tested is True
    # The X10+ answers these but has no such hardware.
    hidden = {"auto_mount_mop", "detergent_left", "detergent_time_left"}
    assert dict(caps.properties) == {k: v for k, v in base.properties.items() if k not in hidden}
    assert dict(caps.actions) == {k: v for k, v in base.actions.items() if k != "reset_detergent"}


def test_spot_checks_against_tasshack() -> None:
    caps = load("dreame.vacuum.p2114a")
    assert caps.properties["state"] == (2, 1)
    assert caps.properties["battery_level"] == (3, 1)
    assert caps.properties["cleaning_properties"] == (4, 10)
    assert caps.properties["object_name"] == (6, 3)
    assert caps.properties["serial_number"] == (4, 14)
    assert caps.actions["start"] == (2, 1)
    assert caps.actions["start_custom"] == (4, 1)
    assert caps.actions["charge"] == (3, 1)
    assert caps.actions["locate"] == (7, 1)
    assert caps.actions["reset_main_brush"] == (9, 1)
    assert "combined_data" not in caps.properties


def test_override_and_null_removal(models: Path) -> None:
    _write(
        models,
        "dreame.vacuum.x",
        "model: dreame.vacuum.x\nname: X\nextends: dreame_base\ntested: true\n"
        "properties:\n  error: [2, 3]\n  volume: null\n  extra: [9, 9]\nactions:\n  pause: null\n",
    )
    caps = load("dreame.vacuum.x", models)
    assert dict(caps.properties) == {"state": (2, 1), "error": (2, 3), "extra": (9, 9)}
    assert dict(caps.actions) == {"start": (2, 1)}
    assert caps.name == "X"
    assert caps.tested is True


def test_extends_chain(models: Path) -> None:
    _write(models, "mid", "extends: dreame_base\nproperties:\n  extra: [9, 9]\n")
    _write(models, "dreame.vacuum.y", "name: Y\nextends: mid\nproperties:\n  state: null\n")
    caps = load("dreame.vacuum.y", models)
    assert dict(caps.properties) == {"error": (2, 2), "volume": (7, 1), "extra": (9, 9)}
    assert caps.tested is False


def test_cycle_is_rejected(models: Path) -> None:
    _write(models, "a", "extends: b\n")
    _write(models, "b", "extends: a\n")
    _write(models, "dreame.vacuum.z", "extends: a\n")
    with pytest.raises(ValueError, match="cycle"):
        load("dreame.vacuum.z", models)


def test_unknown_family_model_falls_back_to_base(models: Path) -> None:
    caps = load("dreame.vacuum.r9999", models)
    assert caps.model == "dreame.vacuum.r9999"
    assert caps.name is None
    assert caps.tested is False
    assert dict(caps.properties) == {"state": (2, 1), "error": (2, 2), "volume": (7, 1)}


@pytest.mark.parametrize("model", ["roborock.vacuum.s5", "dreame.fan.p1", "", "dreame_base"])
def test_non_dreame_model_is_rejected(model: str) -> None:
    with pytest.raises(ValueError):
        load(model)


@pytest.mark.parametrize("model", ["dreame.vacuum/../x", "dreame.vacuum..x", "dreame.vacuum.a/b", "dreame.vacuum.\\x"])
def test_path_traversal_is_rejected(model: str) -> None:
    with pytest.raises(ValueError):
        load(model)


def test_bad_extends_name_is_rejected(models: Path) -> None:
    _write(models, "dreame.vacuum.w", "extends: ../evil\n")
    with pytest.raises(ValueError):
        load("dreame.vacuum.w", models)


@pytest.mark.parametrize(
    "body",
    ["- not a mapping\n", "properties: [1, 2]\n", "properties:\n  state: [2]\n", "properties:\n  state: [2, x]\n"],
)
def test_malformed_model_file_is_rejected(models: Path, body: str) -> None:
    _write(models, "dreame.vacuum.bad", body)
    with pytest.raises(ValueError):
        load("dreame.vacuum.bad", models)


def test_mappings_are_immutable() -> None:
    caps = load("dreame.vacuum.p2114a")
    with pytest.raises(TypeError):
        caps.properties["state"] = (0, 0)  # type: ignore[index]
    with pytest.raises(TypeError):
        caps.actions["start"] = (0, 0)  # type: ignore[index]
    assert all(type(v) is tuple for v in caps.properties.values())


def test_key_for() -> None:
    caps = load("dreame.vacuum.p2114a")
    assert caps.key_for(2, 1) == "state"
    assert caps.key_for(3, 1) == "battery_level"
    assert caps.key_for(7, 10) == "voice_assistant_language"
    assert caps.key_for(999, 999) is None


@pytest.mark.parametrize("model", sorted(p.stem for p in MODELS_DIR.glob("dreame.vacuum.*.yaml")))
def test_every_shipped_model_loads(model: str) -> None:
    caps = load(model)
    assert caps.model == model
    assert caps.name
    assert caps.properties
    assert caps.actions


def test_no_shared_property_ids_beyond_known_aliases() -> None:
    props = load("dreame.vacuum.p2114a").properties
    shared = {ids for ids, n in Counter(props.values()).items() if n > 1}
    assert shared == set(ALIASES)
    for ids, names in ALIASES.items():
        assert tuple(k for k, v in props.items() if v == ids) == names


def test_tasshack_counts() -> None:
    caps = load("dreame.vacuum.unknown")
    assert len(caps.properties) == 371
    assert len(caps.actions) == 45
