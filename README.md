# Dreame Vacuum 3D for Home Assistant

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/integration)
[![GitHub release](https://img.shields.io/github/v/release/dortizesquivel/dreame-vacuum-3d)](https://github.com/dortizesquivel/dreame-vacuum-3d/releases)
[![CI](https://github.com/dortizesquivel/dreame-vacuum-3d/actions/workflows/ci.yml/badge.svg)](https://github.com/dortizesquivel/dreame-vacuum-3d/actions/workflows/ci.yml)
[![Validate](https://github.com/dortizesquivel/dreame-vacuum-3d/actions/workflows/validate.yml/badge.svg)](https://github.com/dortizesquivel/dreame-vacuum-3d/actions/workflows/validate.yml)
[![License: MIT](https://img.shields.io/github/license/dortizesquivel/dreame-vacuum-3d)](LICENSE)
[![Home Assistant 2026.10+](https://img.shields.io/badge/Home%20Assistant-2026.10%2B-blue.svg)](https://www.home-assistant.io/)

A Home Assistant integration for Dreame robot vacuums: local miIO control, Xiaomi cloud login for maps and fallback, Tasshack-compatible cleaning services, and a capability table per model. Phase 1 ships the backend (entities, services, events, diagnostics). The 3D map card comes in a later phase.

**Contents:** [Requirements](#requirements) · [Installation](#installation) · [Configuration](#configuration) · [Models](#models) · [Services](#services) · [Events](#events) · [Options](#options) · [Development](#development) · [Credits](#credits) · [License](#license)

## Requirements

- **Home Assistant 2026.10 or newer.**
- **[HACS](https://hacs.xyz/)**, to install and update it (or [install manually](#manual)).
- A **Dreame** vacuum on a **Xiaomi Home** account (same credentials as the Xiaomi/Mi Home app).
- Local network access to the robot (UDP miIO on port 54321).

## Installation

### Via HACS (recommended)

1. In Home Assistant, open **HACS** → the three-dot menu → **Custom repositories**.
2. Add `https://github.com/dortizesquivel/dreame-vacuum-3d` as category **Integration**.
3. Search for **Dreame Vacuum 3D**, download it and restart Home Assistant.

Or open it in HACS on your own Home Assistant with this button, then select **Download** and restart:

[![Open your Home Assistant instance and open this repository in HACS.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=dortizesquivel&repository=dreame-vacuum-3d&category=integration)

**Updates:** new versions show up in HACS and under **Settings → Updates**.

### Manual

1. Download `dreame_vacuum_3d.zip` from the [latest release](https://github.com/dortizesquivel/dreame-vacuum-3d/releases/latest).
2. Extract it into `/config/custom_components/dreame_vacuum_3d/`, so that `manifest.json` ends up at `/config/custom_components/dreame_vacuum_3d/manifest.json`.
3. Restart Home Assistant.

## Configuration

1. Go to **Settings → Devices & services → + Add integration**.
2. Search for **Dreame Vacuum 3D**.
3. Sign in with your Xiaomi Home email/ID, password and server country. Complete captcha or two-factor verification if asked.
4. Pick the Dreame vacuum from the list.

The entry stores the local IP, token, cloud session and account password (needed to renew the session). See [ASSURANCE](docs/ASSURANCE.md) for what is kept and how diagnostics redact it.

If you still run [Tasshack/dreame-vacuum](https://github.com/Tasshack/dreame-vacuum) or another Dreame integration on the same robot, leave **Coexist** enabled (the default) so this integration polls more slowly while docked.

## Models

Capabilities come from YAML under `custom_components/dreame_vacuum_3d/device/models/`. A model with no dedicated file inherits the untested family base.

| Model ID | Name | Status |
|---|---|---|
| `dreame.vacuum.p2114a` | Xiaomi Robot Vacuum X10+ | **Tested** |
| Other Dreame vacuums | Family base (`dreame_base`) | **Untested** — entities appear only for properties the robot returns |

To help add a model, download diagnostics from the device page and open an issue or PR: see [CONTRIBUTING.md](CONTRIBUTING.md).

## Services

Entity services under domain `dreame_vacuum_3d` (names match Tasshack for Node-RED migration: `dreame_vacuum.*` → `dreame_vacuum_3d.*`). Target a `vacuum` entity from this integration.

| Service | Purpose |
|---|---|
| `vacuum_clean_segment` | Clean room segment(s); optional repeats, suction, water |
| `vacuum_clean_zone` | Clean zone rectangle(s) |
| `vacuum_clean_spot` | Spot-clean at point(s) |
| `vacuum_goto` | Go to coordinates (mm) |
| `vacuum_remote_control_move_step` | One remote-control step |
| `vacuum_select_map` | Select map / floor by `map_id` |
| `vacuum_reset_consumable` | Reset a consumable life counter |

Map-edit services (restricted zones, merge/split/rename segments) are planned for a later phase.

## Events

Fired on the Home Assistant bus (payload always includes `entity_id` and `device_id`):

| Event | When |
|---|---|
| `dreame_vacuum_3d_task_status` | Cleaning task status changes |
| `dreame_vacuum_3d_consumable` | A consumable drops to or below the warning threshold |
| `dreame_vacuum_3d_error` | Robot reports an error |
| `dreame_vacuum_3d_information` | Warnings / informational codes (low water, paused, etc.) |

Persistent notifications can be enabled per category in the options.

## Options

From the integration's **Configure** button:

| Option | Description | Default |
|---|---|---|
| Coexist | Slower docked polling while another Dreame integration is installed | on |
| Notification categories | Persistent notifications for cleanup completed, consumables, information, warnings, errors | as configured |
| Consumable warning threshold | Remaining % that triggers a consumable event/notification | 15 |

Reconfigure the local IP if the robot's address changed. Reauthenticate when the cloud session expires.

## Development

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements_test.txt
ruff check .
python scripts/check_translations.py
pytest
```

`main` is protected: changes go in through a pull request. Required checks are **Python tests** (ruff, translations, pytest with a coverage floor) and **Validate** (HACS + hassfest). Every commit must be signed off (`git commit -s`); see [CONTRIBUTING.md](CONTRIBUTING.md).

Layout: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md). Security model: [docs/ASSURANCE.md](docs/ASSURANCE.md) and [SECURITY.md](SECURITY.md).

### Releasing

```bash
gh workflow run release.yml -f bump=patch
gh workflow run release.yml -f bump=minor -f dry_run=true
```

The [Release workflow](.github/workflows/release.yml) bumps `manifest.json`, updates [`CHANGELOG.md`](CHANGELOG.md), tags and publishes `dreame_vacuum_3d.zip` with a signed build-provenance attestation.

## Credits

This integration ports protocol knowledge, property maps and service names from **[Tasshack/dreame-vacuum](https://github.com/Tasshack/dreame-vacuum)** (MIT). See [NOTICE](NOTICE) for the upstream copyright. Dreame Vacuum 3D is an independent project and is not affiliated with Tasshack, Dreame or Xiaomi.

## License

[MIT](LICENSE). Portions derived from Tasshack/dreame-vacuum remain under MIT as stated in [NOTICE](NOTICE).
