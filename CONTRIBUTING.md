# Contributing

Thanks for helping. Bug reports, ideas and pull requests are welcome.

## Reporting

- **Bugs**: open an [issue](https://github.com/dortizesquivel/dreame-vacuum-3d/issues) with Home Assistant version, integration version, vacuum model (`dreame.vacuum.…`), and logs filtered by `dreame_vacuum_3d`. Remove passwords, tokens, `did`, MAC and serial before pasting.
- **Security problems**: don't open an issue; report them privately as [SECURITY.md](SECURITY.md) explains.

## Pull requests

`main` is protected: every change goes in through a pull request. A pull request is merged when:

- **The CI passes.** That covers ruff, the translations check (`python scripts/check_translations.py`), pytest with its coverage floor, HACS validation and hassfest.
- **New functionality comes with tests** under `tests/`.
- **The docs follow the change** when behaviour or settings change.

## Sign-off (DCO)

Every commit in a pull request certifies the [Developer Certificate of Origin 1.1](https://developercertificate.org/): that you wrote the change or otherwise have the right to submit it under the project's licence. Sign it off with `git commit -s`, which adds a line like:

```
Signed-off-by: Your Name <you@example.com>
```

## Style

- Match the code around you. Python follows Home Assistant conventions (async; executor jobs for blocking work).
- `ruff check .` must pass. User-visible text is in English and Spanish: `strings.json`, `translations/en.json` and `translations/es.json` must share the same keys.
- No runtime Python dependencies beyond Home Assistant.

## Adding a model via diagnostics

Only `dreame.vacuum.p2114a` (Xiaomi Robot Vacuum X10+) is marked **tested**. Other Dreame models inherit the untested family base and expose whichever properties the robot actually returns.

To contribute a model:

1. Install the integration and add the vacuum.
2. Open the device page → **Download diagnostics**.
3. Confirm the JSON has no password, token or `auth` (they should already be redacted). Strip anything else you don't want public.
4. Open an issue or PR with:
   - the model id (`dreame.vacuum.…`) and product name;
   - the diagnostics file (or the `properties` / `available` sections);
   - whether each important entity works (vacuum, mop, station, consumables).
5. For a PR: add `custom_components/dreame_vacuum_3d/device/models/<model>.yaml` (usually `extends: dreame_base`, set `name` and `tested: true` only after you've verified it on hardware), plus a short note in the README models table.

`scripts/dump_device.py` can also capture a local dump during development; keep `dumps/` out of git.

## Running the suite

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements_test.txt
ruff check .
python scripts/check_translations.py
pytest
```

Contributions are released under the project's [MIT licence](LICENSE). Portions derived from Tasshack/dreame-vacuum remain attributed in [NOTICE](NOTICE).
