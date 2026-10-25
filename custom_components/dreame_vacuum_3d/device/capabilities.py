"""Per-model MIoT property and action ids, read from the YAML files in models/.

Portions derived from Tasshack/dreame-vacuum (MIT), see NOTICE.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path
import re
from types import MappingProxyType
from typing import Any

import yaml

MODELS_DIR = Path(__file__).parent / "models"
BASE = "dreame_base"
FAMILY = "dreame.vacuum."

Ids = tuple[int, int]  # (siid, piid) or (siid, aiid)

# Dotted lowercase words only: no "/", "\" or "..", so a name can't leave the models dir.
_NAME = re.compile(r"[a-z0-9_]+(?:\.[a-z0-9_]+)*")


@dataclass(frozen=True)
class Capabilities:
    """What one model exposes over MIoT, keyed by snake_case names of Tasshack's enums."""

    model: str
    name: str | None
    tested: bool
    properties: Mapping[str, Ids]
    actions: Mapping[str, Ids]

    @cached_property
    def _keys(self) -> Mapping[Ids, str]:
        keys: dict[Ids, str] = {}
        for key, ids in self.properties.items():
            keys.setdefault(ids, key)
        return keys

    def key_for(self, siid: int, piid: int) -> str | None:
        """Property name for (siid, piid).

        A few names alias the same ids (voice_assistant_language and listen_language_type are both 7.10);
        the one defined first in the YAML wins.
        """
        return self._keys.get((siid, piid))


def load(model: str, models_dir: Path = MODELS_DIR) -> Capabilities:
    """Capabilities of model; a family model without its own file gets the untested base.

    Blocking (reads YAML): call it in the executor.
    """
    if not model.startswith(FAMILY) or not _NAME.fullmatch(model):
        raise ValueError(f"not a Dreame vacuum model: {model!r}")
    own = models_dir / f"{model}.yaml"
    spec = _read(own) if own.is_file() else {"extends": BASE}
    properties: dict[str, Ids] = {}
    actions: dict[str, Ids] = {}
    for layer in _chain(spec, models_dir):
        _merge(properties, layer.get("properties"))
        _merge(actions, layer.get("actions"))
    return Capabilities(
        model=model,
        name=spec.get("name"),
        tested=spec.get("tested") is True,
        properties=MappingProxyType(properties),
        actions=MappingProxyType(actions),
    )


def _chain(spec: dict[str, Any], models_dir: Path) -> list[dict[str, Any]]:
    """spec and its ancestors, base first."""
    layers = [spec]
    seen: set[str] = set()
    while (parent := layers[-1].get("extends")) is not None:
        if not isinstance(parent, str) or not _NAME.fullmatch(parent):
            raise ValueError(f"bad extends: {parent!r}")
        if parent in seen:
            raise ValueError(f"extends cycle through {parent!r}")
        seen.add(parent)
        layers.append(_read(models_dir / f"{parent}.yaml"))
    return layers[::-1]


def _read(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text())
    if not isinstance(data, dict):
        raise ValueError(f"{path.name}: not a mapping")
    return data


def _merge(into: dict[str, Ids], layer: Any) -> None:
    """Apply a properties/actions layer over into; a null value removes the key."""
    if layer is None:
        return
    if not isinstance(layer, dict):
        raise ValueError(f"expected a mapping, got {layer!r}")
    for key, ids in layer.items():
        if ids is None:
            into.pop(key, None)
        elif isinstance(ids, list) and len(ids) == 2 and all(type(i) is int for i in ids):
            into[key] = (ids[0], ids[1])
        else:
            raise ValueError(f"{key}: expected [siid, id], got {ids!r}")
