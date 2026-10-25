# Security assurance case

Why we believe Dreame Vacuum 3D meets its security requirements for phase 1: what it must protect, where untrusted input comes in, and how that is checked. For reporting a vulnerability, see [SECURITY.md](../SECURITY.md).

## What it must protect

The integration runs inside Home Assistant with Home Assistant's rights. It handles:

1. **Xiaomi account credentials** — username and password used to log into the Xiaomi Home cloud.
2. **Cloud session (`auth`)** — cookies / tokens that keep the session (including captcha and 2FA completion).
3. **Device token** — 16-byte miIO token that encrypts local UDP traffic.
4. **Device identifiers** — `did`, MAC, host, serial number.
5. **Maps** (when enabled in later phases) — floor plans of the home downloaded from Xiaomi's cloud.

Security requirements for this phase:

1. **Credentials stay in Home Assistant storage** — password and `auth` are in the config entry; they are not logged, not put in entity states, and are redacted from diagnostics.
2. **Token and identifiers are not exported** — diagnostics and logs must not leak token, password, `auth`, `did`, host, MAC, username or serial.
3. **No unexpected outbound use of credentials** — the integration talks only to Xiaomi's regional cloud (login, device list, RPC fallback, map object URLs) and to the robot on the LAN. No telemetry to third parties.
4. **Robustness** — malformed cloud or miIO replies must not crash Home Assistant or expose secrets in error messages shown to users.
5. **Trustworthy releases** — what HACS installs must be the code in this repository, built by CI.

## Threat model and trust boundaries

| Boundary | Input / data | Who controls it | Main threats |
|---|---|---|---|
| Config flow / entry | Username, password, country, captcha, 2FA code | The Home Assistant admin | Phishing of the form; password stored in `.storage` readable by anyone with `/config` access (same as other cloud integrations) |
| Xiaomi cloud | Login JSON, device list, RPC, map URLs | Xiaomi, the network | Session theft; hostile JSON; redirect to unexpected hosts |
| Local miIO | Encrypted UDP replies | The robot, the LAN | Replay; wrong token; oversized payloads |
| Map files | zlib + base64 frames the robot uploads | The robot, Xiaomi, the network | Hostile or corrupt data crashing the decoder; a zip bomb |
| Card WebSocket | `dreame_vacuum_3d/map/subscribe` | Any logged-in Home Assistant user | Reading the floor plan of a vacuum the user has no access to |
| Diagnostics download | Admin-triggered export | Home Assistant admins | Accidental paste of secrets into a public issue |
| Build and release | GitHub Actions, release zip | Upstream actions, the CI | Tampered release; compromised action |

## Design principles applied

- **Least privilege.** Cloud is used for login, device discovery, session renew and fallback RPC; day-to-day control prefers local miIO. Workflow tokens in CI are read-only except for the release job.
- **Redaction by default.** Diagnostics use `async_redact_data` on password, `auth`, token, `did`, MAC, host, username, `unique_id` and nested session fields (`ssecurity`, `service_token`, …). `serial_number` is omitted from the properties dump, and the Xiaomi user id and `did` are masked inside property values (map file names carry both).
- **Password kept for re-login.** Xiaomi sessions expire; the entry stores the password so the integration can renew without a full reauth every time. That is intentional and documented; treat Home Assistant backups as containing it.
- **Fail closed on auth.** An expired session is renewed once with the saved password; if that fails, or Xiaomi asks for a captcha or a verification code, `ConfigEntryAuthFailed` (reauth). Connection failures → `ConfigEntryNotReady`. Details stay in the log, not in user-visible entity attributes.
- **Open design.** Source is public; release zips carry build provenance and can be rebuilt from the tag.

## Sensitive data inventory

| Data | Where stored | Leaves Home Assistant? | In diagnostics? |
|---|---|---|---|
| Xiaomi password | Config entry | Only to Xiaomi login endpoints | Redacted |
| Cloud `auth` / cookies | Config entry (updated on renew) | Xiaomi cloud requests | Redacted |
| miIO token | Config entry | LAN (key derivation only; not sent in clear) | Redacted |
| `did`, MAC, host, username | Config entry | Cloud / LAN as needed for protocol | Redacted |
| Serial number | Device property | Not by this integration | Omitted |
| Floor plan (rooms, names, path) | In memory only (`LiveMap`) | Only to the card, over Home Assistant's WebSocket, for users who can read the vacuum entity | Not included |

## Common weaknesses countered

| Weakness | Countermeasure |
|---|---|
| Exposure of secrets (CWE-200) | Redacted diagnostics; no secrets in entity states; NOTICE/README document what is stored |
| Use of hard-coded credentials | None; user supplies account and token comes from the cloud device list |
| Supply chain (CWE-829, CWE-494) | Actions pinned to commit SHAs; release zip attested; reproducible package from `scripts/release.py` |
| Improper error handling | Protocol errors mapped to HA exceptions; user-facing flow errors are fixed keys from `strings.json` |
| Untrusted input (CWE-20) | The map decoder checks sizes against the header and raises only `MapError` on anything else, fuzzed with Hypothesis; a failed frame keeps the last map and logs one warning |

## Evidence

- **Tests:** protocol crypto vectors, cloud login/2FA/captcha with recorded replies, config flow, diagnostics redaction tests, coverage floor in CI.
- **Static checks:** ruff (including security rules) on every pull request.
- **Validation:** hassfest and HACS action weekly and on every PR.
- **Releases:** `gh attestation verify dreame_vacuum_3d.zip --repo dortizesquivel/dreame-vacuum-3d`; `python scripts/release.py package` at the tag rebuilds the zip.
