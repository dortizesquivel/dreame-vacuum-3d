# Security policy

## Reporting a vulnerability

Please report it privately through GitHub: **[report a vulnerability](https://github.com/dortizesquivel/dreame-vacuum-3d/security/advisories/new)** (the repository's Security tab → Report a vulnerability). Don't open a public issue for it. Include what you found, the version and how to reproduce it.

What to expect:

- An acknowledgement within 7 days.
- A first assessment within 14 days: whether it's confirmed and how serious it is.
- A fixed release as soon as possible, usually within 30 days for a confirmed vulnerability, published with a GitHub security advisory that credits you unless you'd rather not.
- Please keep it private until the fix is released, or for 90 days after your report, whichever comes first.

## Supported versions

Fixes go into the latest release. HACS offers it as an update.

## What the integration can do

Dreame Vacuum 3D is a Home Assistant integration (Python, running inside Home Assistant) with a dashboard card (JavaScript, running in the browser). The card talks to Home Assistant only, and loads the 3D model of the house from the URL in its configuration.

- **Stores** Xiaomi username and password, cloud session (`auth`), local IP, miIO token and device id in the config entry (Home Assistant `.storage`). Treat backups of `/config` as containing those secrets.
- **Talks to** Xiaomi's regional cloud (login, device list, RPC fallback, map object URLs) and to the robot on the LAN (UDP miIO).
- **Controls** the vacuum through Home Assistant services and entities (start, pause, rooms, zones, consumable resets, …).
- **Exports** diagnostics that redact credentials, token, identifiers and omit the serial number — safe to attach to an issue after a quick check.
- **Doesn't send telemetry** to any other server.

The [assurance case](docs/ASSURANCE.md) explains the threat model and redaction in more detail.

## How it's checked

- **[CI](.github/workflows/ci.yml)** on every pull request: ruff, mypy, translation key check, pytest with a coverage floor, the card's lint, unit and browser tests.
- **[CodeQL](.github/workflows/codeql.yml)**: static security analysis of the Python, the card and the workflows, on every pull request and weekly.
- **[OpenSSF Scorecard](.github/workflows/scorecard.yml)** and monthly [Dependabot](.github/dependabot.yml) updates of the actions, npm and pip dependencies.
- **[Validate](.github/workflows/validate.yml)**: HACS and hassfest, also weekly.
- **Workflows**: every action is pinned to a commit; tokens are read-only except in the release job.

## Verifying a release

Each release carries `dreame_vacuum_3d.zip` (what HACS installs) with a signed build-provenance attestation. To check a downloaded copy:

```bash
gh attestation verify dreame_vacuum_3d.zip --repo dortizesquivel/dreame-vacuum-3d
```

Checking out the tag and running `python scripts/release.py package` rebuilds a byte-for-byte identical zip.
