# Architecture

Dreame Vacuum 3D is a Home Assistant custom integration: Xiaomi cloud login, local miIO control, a per-model capability table, entities, Tasshack-compatible services, events and diagnostics, and a 3D card it serves itself. The card draws the robot on the user's own 3D model of the house; the robot's map is only the reference for where the robot, the dock and the rooms are.

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

 robot ── request_map ──► object name ──► cloud file ──► device/map.py ──► LiveMap ──┬─► current room, room buttons
                                                        (pose, dock,              └─► WebSocket ──► card (three.js)
                                                         rooms, path)                  map/subscribe
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
| `device/map.py` | Decodes map frames: header (robot and dock poses, grid), the saved map's rooms (cells as runs, names, centres), the path, the rooms being cleaned. Bad files raise `MapError` only (fuzzed). |
| `live_map.py` | Asks for a frame every 5 s while the robot moves (one more when it stops, then every 15 min), downloads it, keeps the latest message and pushes it to listeners: the rooms only when they change, the pose with every frame. |
| `websocket.py` | `dreame_vacuum_3d/map/subscribe` (entity read permission): the latest message at once, then each update. Subscriptions are kept per config entry, so they survive a reload. |
| `card/` → `frontend/dreame-vacuum-3d-card.js` | The card's source (three.js, esbuild). The built bundle is committed; CI rebuilds it and fails if it differs. `__init__.py` serves it and adds it to the frontend with a content hash in the URL. |
| `diagnostics.py` | Redacted entry + raw MIoT properties for adding models. |
| `strings.json`, `translations/en.json`, `es.json` | Config flow and entity texts; same keys (see `scripts/check_translations.py`). |

## Data flow

1. **Setup.** Create an aiohttp session with unquoted cookies, build `XiaomiCloud`, log in (password from the entry). Persist `auth` if the cloud renews it. Build `MiioClient(host, token)`, load capabilities for `model` in the executor, construct `DreameDevice`, discover available properties, start the coordinator and platforms, set up the notifier.
2. **Poll.** The coordinator asks the device for properties. While cleaning or returning it polls every ~2 s; while docked, 30 s (60 s with coexist); while sleeping or off, 120 s. Home Assistant entities update only when values change (`always_update=False`).
3. **Control.** Vacuum services and entity writes go through `DreameDevice` → local miIO first; cloud RPC is the fallback channel when local fails.
4. **Events.** The notifier watches task status, errors, warnings and consumable % against the configured threshold and fires domain events (and optional persistent notifications).
5. **Map.** `LiveMap` runs `request_map` on the robot (locally, or through the cloud), downloads the file the robot uploaded, decodes it and sends the card the robot's pose, the dock, the path and the rooms of the saved map the frame carries (`rism`). The card turns map coordinates into the model's with the dock's pose in both.
6. **Diagnostics.** Device page → Download diagnostics returns a redacted entry dump plus `siid.piid` property values (no token, password, auth, did, host, serial).

## Trust boundaries

Xiaomi credentials, the miIO token, cloud cookies and map downloads cross into the integration. What is stored, redacted and sent is in [ASSURANCE.md](ASSURANCE.md).

## Tests

`tests/` runs on Home Assistant's test harness (`pytest-homeassistant-custom-component`): protocol vectors and recorded cloud replies, capabilities, config flow, entities, services, events, diagnostics, and `scripts/check_translations.py`. CI runs ruff, formatting, mypy, the translations check, pytest (coverage floor in `pyproject.toml`), the card's unit tests and bundle check, the DCO sign-off, and HACS + hassfest validation.
