"""translations/en.json, es.json and strings.json must expose the same keys."""

from __future__ import annotations

from pathlib import Path
import subprocess
import sys

SCRIPT = Path(__file__).parents[2] / "scripts" / "check_translations.py"


def test_translations_have_the_same_keys() -> None:
    result = subprocess.run(  # noqa: S603
        [sys.executable, str(SCRIPT)], check=False, capture_output=True, text=True
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "translations OK" in result.stdout
