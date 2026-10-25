"""Fail if strings.json, en.json or es.json is missing a key another one has."""

import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent / "custom_components" / "dreame_vacuum_3d"
TRANSLATIONS = ROOT / "translations"


def keys(node, prefix=""):
    if not isinstance(node, dict):
        return {prefix.rstrip(".")}
    return set().union(*(keys(v, f"{prefix}{k}.") for k, v in node.items()))


files = {"strings.json": keys(json.loads((ROOT / "strings.json").read_text(encoding="utf-8")))}
files.update({f.name: keys(json.loads(f.read_text(encoding="utf-8"))) for f in sorted(TRANSLATIONS.glob("*.json"))})
everything = set().union(*files.values())
ok = True
for name, have in files.items():
    if missing := sorted(everything - have):
        ok = False
        print(f"{name} is missing: {', '.join(missing)}")
print("translations OK" if ok else "")
sys.exit(0 if ok else 1)
