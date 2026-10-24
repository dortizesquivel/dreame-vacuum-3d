# Architecture

Dreame Vacuum 3D is a Home Assistant custom integration. Phase 1 is the backend: Xiaomi cloud login, local miIO control, a per-model capability table, entities, Tasshack-compatible services, events and diagnostics. The 3D map card and live map decoder are later phases.

```
 Xiaomi Home cloud ──► XiaomiCloud (login, captcha, 2FA, device list, map URLs)
                              │
                              ▼
 config entry (host, token, did, auth, password)
                              │
         ┌────────────────────┼────────────────────┐
         ▼                    ▼                    ▼
    MiioClient          DreameDevice         DreameNotifier
    (UDP miIO)          (properties,         (events +
         │               actions)             persistent notify)
         └────────► DreameDataUpdateCoordinator ──► platforms
                    (adaptive poll, always_update=False)
```

## Components

| Path | Role |
|---|---|
| `__init__.py` | Entry setup: cloud session (`CookieJar(quote_cookie=False)`), login, persist renewed auth, miIO client, load capabilities, device, coordinator, platforms, notifier. |
| `config_flow.py` | User / captcha / 2FA / device pick, reauth, reconfigure host, options. |
| `coordinator.py` | Polls the device; `MiioError` → `UpdateFailed`; `CloudAuthError` → `ConfigEntryAuthFailed`. Intervals: cleaning/returning ~2 s, docked 30 s (60 s if coexist), sleeping/off 120 s. |
| `device/capabilities.py` + `device/models/*.yaml` | Property/action siid·piid tables with inheritance; `tested` flag; unknown models get the untested base. |
| `device/device.py` | Reads/writes properties, runs actions; tracks `available` keys and active channel (local / cloud). |
| `protocol/miio*.py`, `miot.py` | Local UDP codec and MIoT get/set/action. |
| `protocol/cloud.py`, `cloud_crypto.py` | Xiaomi cloud login, RPC fallback, map object URLs. |
| `vacuum.py`, `sensor.py`, … | Platforms; entities only if the key is in `device.available`. |
| `services.yaml` + vacuum entity services | Tasshack-compatible names under domain `dreame_vacuum_3d`. |
| `notifications.py` | Bus events and optional persistent notifications. |
| `diagnostics.py` | Redacted entry + raw MIoT properties for adding models. |
| `strings.json`, `translations/en.json`, `es.json` | Config flow and entity texts; same keys (see `scripts/check_translations.py`). |

## Data flow

1. **Setup.** Create an aiohttp session with unquoted cookies, build `XiaomiCloud`, log in (password from the entry). Persist `auth` if the cloud renews it. Build `MiioClient(host, token)`, load capabilities for `model` in the executor, construct `DreameDevice`, discover available properties, start the coordinator and platforms, set up the notifier.
2. **Poll.** The coordinator asks the device for properties. While cleaning or returning it polls every ~2 s; while docked, 30 s (60 s with coexist); while sleeping or off, 120 s. Home Assistant entities update only when values change (`always_update=False`).
3. **Control.** Vacuum services and entity writes go through `DreameDevice` → local miIO first; cloud RPC is the fallback channel when local fails.
4. **Events.** The notifier watches task status, errors, warnings and consumable % against the configured threshold and fires domain events (and optional persistent notifications).
5. **Diagnostics.** Device page → Download diagnostics returns a redacted entry dump plus `siid.piid` property values (no token, password, auth, did, host, serial).

## Trust boundaries

Xiaomi credentials, the miIO token, cloud cookies and map downloads cross into the integration. What is stored, redacted and sent is in [ASSURANCE.md](ASSURANCE.md).

## Tests

`tests/` runs on Home Assistant's test harness (`pytest-homeassistant-custom-component`): protocol vectors and recorded cloud replies, capabilities, config flow, entities, services, events, diagnostics, and `scripts/check_translations.py`. CI runs ruff, the translations check, pytest (coverage floor in `pyproject.toml`), and HACS + hassfest validation.
