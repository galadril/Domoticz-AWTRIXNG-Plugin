# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A single-file Domoticz Python plugin (`plugin.py`) that exposes an AWTRIX NG smart pixel clock as a set of Domoticz virtual devices. This repo is a port of the author's earlier AWTRIX 3 plugin — see "Backward-compatibility contract" below, it is the dominant constraint on changes here.

Everything lives in `plugin.py`. `icons/`, `images/`, `samples/` are assets/docs; `AWTRIXNG-Icons.zip` is the Domoticz device icon pack loaded at startup.

## Commands

There is no package manifest. CI (`.github/workflows/validate.yml`) runs four checks, all reproducible locally:

```bash
python .github/scripts/validate_plugin.py   # parses the XML docstring header of plugin.py
python -m compileall -q .                   # syntax check
ruff check .                                # lint
python tests/test_plugin.py                 # offline behaviour tests
```

`compileall` leaves `__pycache__` directories behind; they're gitignored.

`validate_plugin.py` hardcodes `plugin.py` as its target and asserts the header has a `<plugin>` root with `key`/`name`/`author`/`version` attributes plus `<description>` and `<params>` children. It exits non-zero on failure.

`plugin.py` cannot be imported or run standalone — `import Domoticz` and the injected globals only exist inside Domoticz's embedded interpreter. To exercise real behaviour, copy the file to `domoticz/plugins/Domoticz-AWTRIXNG-Plugin/plugin.py`, restart Domoticz, and add the hardware via `Setup -> Hardware`. Set the `Debug Level` param to `All` (`-1`) to get `Domoticz.Debug` output including every HTTP request/response line from `_request`.

Runtime dependency: `requests`.

## Architecture

**The XML docstring at the top of `plugin.py` is the plugin manifest.** Domoticz parses it to render the hardware setup form; the `field` attributes (`Address`, `Username`, `Password`, `Mode1`, `Mode6`) become keys in the `Parameters` dict. Adding a config option means adding a `<param>` there *and* reading `Parameters["ModeN"]`. Bump `version` in the same header when behaviour changes.

**Domoticz injects globals, it does not pass them.** `Parameters`, `Devices`, and `Images` are undefined names as far as any static analyser is concerned; they appear in the module namespace at load time. `Devices` is keyed by unit number, `Images` by image key (`"AWTRIXNG"`, derived from the zip filename).

**Module-level callbacks delegate to a `BasePlugin` singleton.** The bottom of the file defines the fixed set of hooks Domoticz looks up by name (`onStart`, `onStop`, `onConnect`, `onMessage`, `onCommand`, `onHeartbeat`) which forward to `global _plugin`. `onConnect`/`onMessage` are intentionally no-ops — this plugin uses blocking `requests` calls, not Domoticz's async transport layer.

**The `UNIT_*` constants are the plugin's public API.** Each is a Domoticz unit number, and every unit maps to one device created idempotently in `onStart` (`if UNIT_X not in Devices`) and handled by one branch of the `if/elif` chain in `onCommand`. Unit numbers are persisted in the user's Domoticz database, so **never renumber or reuse a `UNIT_*` value** — existing installs would silently rewire automations to the wrong device. New capabilities get new unit numbers appended.

**Three control-flow shapes across the units:**

- *Sensors* (`UNIT_LUX`, `UNIT_TEMPHUM`) are write-only from `onHeartbeat`, which polls `GET /api/v1/device` every 30s.
- *Actions* (next/prev app, dismiss) are push buttons (`Switchtype=9`) that fire a request and reset themselves.
- *Payload devices* (`UNIT_NOTIFICATION`, `UNIT_CUSTOMAPP`, `UNIT_SETTINGS`, `UNIT_RTTTL`) carry a user-authored string that is forwarded to the device. `parsePushMessage` defines the three accepted forms (raw JSON object/array, `icon;text`, or bare text falling back to the `Mode1` default icon) — this format is documented in the README and relied on by the dzVents samples, so it is a compatibility surface.

`UNIT_POWER` is bidirectional: commanded via `PATCH /api/v1/display`, and reconciled from the device state on each heartbeat. Colour devices (`UNIT_TEXTCOLOR`, `UNIT_MOODLIGHT`, the three indicators) share `commandColor` / `hexColor` / `confirmColor` — note that Domoticz hands colour to `onCommand` as a **JSON string, not a dict**, and expects the `Color=` it gets back to be JSON too (`str(dict)` emits single quotes and the colour is silently dropped).

**`onHeartbeat` is on the plugin thread and must stay cheap.** Each request blocks for up to the 5s timeout, so an unplugged panel would otherwise stall Domoticz for 15s every 30s. The heartbeat therefore polls `/api/v1/device` first and, on failure, sets `skipTicks` to back off exponentially to ~8 minutes; `/api/v1/settings` and `/api/v1/display` are only read every `SLOW_POLL_TICKS` heartbeats since they exist purely to keep the UI in step.

**All HTTP goes through `BasePlugin._request`**, which owns the base URL, optional `HTTPBasicAuth`, the 5s timeout, error logging, and the JSON-or-text-or-None return convention. It returns `None` on any failure, so callers must not assume a dict — `onHeartbeat` guards with `isinstance(stats, dict)`. Add new endpoint calls through this method rather than calling `requests` directly.

## Backward-compatibility contract

The user's existing Domoticz automations and the scripts in `samples/` must keep working against this NG rewrite. Device set and behaviour may grow, but must not change or shrink. Concretely, when touching a unit, check `samples/*.txt` for how it is actually driven — the samples are the spec:

- **Device names matter.** dzVents resolves devices by `"<hardware name> - <device name>"`, e.g. `AWTRIXNG - Send Custom App`, `AWTRIX NG - Sleep Mode`, `AWTRIX NG - Overlay`, `AWTRIXNG - Power`. Renaming a device in `createDevices` breaks every script referencing it.
- **Payload devices are push buttons whose payload lives in the description.** `UNIT_NOTIFICATION`, `UNIT_CUSTOMAPP`, `UNIT_SETTINGS`, `UNIT_RTTTL` and `UNIT_SLEEP` are `Type=244, Subtype=73, Switchtype=9` and read `Devices[Unit].Description`, because every real automation does `setDescription(payload)` then `switchOn()`. They must **not** be `TypeName="Text"` units that read the payload out of `Command` — dzVents `switchOn()` carries no payload, so those scripts would silently send nothing. `UNIT_SLEEP` additionally falls back to `sValue` for scripts that use `updateText()`.
- **Selector level values matter.** `dzvents-weather-overlay-sample.txt` calls `switchSelector(30)` expecting Drizzle, `40` Storm, `50` Thunder, `60` Frost. Level is `10 * index` into `TRANSITION_NAMES` / `OVERLAY_NAMES`, so reordering either list silently remaps existing automations. Append only, never insert. Indices 0–11 of `TRANSITION_NAMES` and all of `OVERLAY_NAMES` reproduce the AWTRIX 3 plugin's ordering; NG's eleven extra transitions are appended after `Fade`. NG ships exactly six weather overlays, so `OVERLAY_NAMES` is complete — don't invent entries (an unknown name is a hard 422).
- **`Brightness` "Off" means auto-brightness ON**, matching AWTRIX 3. It does not mean "dark".
- **Custom apps default to the app name `Domoticz`** when the payload carries no `appname`. Changing that default orphans whatever the previous name pushed. An array payload creates indexed apps `Domoticz0..n`, which is how the multi-page automation works.
- Feature parity with the AWTRIX 3 plugin also covers things easy to drop on a port: the humidity-derived comfort index in the `Temp+Hum` sValue, `BatteryLevel` on the `Temp+Hum` and `Power` devices, and the heartbeat pushing device state *back* into the selector/colour/brightness/overlay devices so the Domoticz UI tracks changes made on the panel itself.

## AWTRIX NG API mapping

NG renamed essentially every field from the AWTRIX 3 short-key convention (`BRI`, `TCOL`, `ABRI`, `TEFF`, `lux`, `matrix`) to camelCase. Ported code carrying the old keys will be accepted-and-ignored or rejected rather than failing loudly, so verify against the spec — `https://blueforcer.github.io/awtrix-ng/api/openapi.yaml`, docs at `https://blueforcer.github.io/awtrix-ng/`.

Confirmed NG shapes relevant to this plugin:

| Purpose | Endpoint | Body / fields |
| --- | --- | --- |
| Device + sensor state | `GET /api/v1/device` | `lightLevel` (0–100 %, explicitly *not* lux), `temperature`, `humidity`, `matrixPower`, `batteryPercent`, `brightness`, `currentApp` |
| Display state | `GET /api/v1/display` | `power`, `brightness`, `overlay`, `overlaySettings` |
| Power / overlay | `PATCH /api/v1/display` | `power`, `overlay` (name from `capabilities.overlays`, `null`/`""` clears), `overlaySettings` — **overlay lives here, not in settings** |
| Settings (r/w, same schema both ways) | `GET`/`PATCH /api/v1/settings` | `autoBrightness`, `brightness` (0–255), `transitionEffect` (name from `capabilities.transitions`), `textColor`, `appDurationMs`, `transitionDurationMs` |
| Notification | `POST /api/v1/notifications` | pushed-app fields + `name`, `hold`, `stack`, `wakeup`, `sound`, `soundRtttl` |
| Dismiss | `DELETE /api/v1/notifications/active` | — |
| Custom app | `PUT /api/v1/apps/pushed/{name}` | object or array; array creates indexed apps `{name}0..n` |
| App rotation | `POST /api/v1/apps/next` / `/previous` | empty |
| Audio | `POST /api/v1/audio/play` | exactly one of `sound`, `mp3`, `melody`, `track`, `rtttl`, `station`, `index`, `url` |
| Sleep | `POST /api/v1/device/sleep` | `durationMs` (integer, **milliseconds**) |
| Discover valid names | `GET /api/v1/capabilities` | `effects`, `overlays`, `transitions`, `palettes`, `audio`, `gpio` |

Conventions that bite:

- Colors are a union: `"#RRGGBB"` / `"RRGGBB"` / `[r,g,b]` / `["HSV",h,s,v]` on input, always `"#RRGGBB"` on output. `NullableColor` tracks `null` (inherit/off) separately from `#000000`.
- All durations are integer **milliseconds** with an `...Ms` suffix.
- `effect` / `overlay` / `transitionEffect` / `palette` names are matched case-insensitively but an unknown name is a hard **422**, and for array payloads nothing at all is stored. `onStart` therefore caches `/api/v1/capabilities` into `self.transitions` / `self.overlays` and `resolveName` rejects unsupported selector entries locally instead of firing a doomed request.
- Pushed-app names must match `^[A-Za-z0-9_-]{1,32}$` or the request is a **400**; `customAppName` sanitises to exactly that.
- **`icon` must be a string** (an ID = filename without extension, or inline base64 over 64 chars). A non-string `icon` is *ignored* rather than rejected, so a numeric one from dzVents renders text with no icon and no error — the exact bug class that motivates the tests. `normaliseIcons` coerces it on both the notification and pushed-app paths; don't remove it. A missing icon file also falls back to the icon-less layout silently, so "no icon" has two possible causes.
- An empty body or `{}` on `PUT /api/v1/apps/pushed/{name}` is a **422** — use the `DELETE` route to remove an app.
- Request bodies must be `Content-Type: application/json` or the request is a **415**; `requests`' `json=` kwarg handles this, so don't switch to `data=`.
- **Validation is all-or-nothing and unknown keys are fatal.** AWTRIX 3 ignored keys it did not recognise; NG rejects the whole payload with 422 and names the field. This is why `_request` logs `response.text` on failure — that body is the only thing that identifies the offending key. Never strip it from the error path.

Unused-but-available NG capabilities worth knowing about when asked to extend the plugin: `PUT /api/v1/apps/order`, `PUT /api/v1/audio/melodies/{name}`, `POST /api/v1/audio/stop`, `PUT /api/v1/audio/stations`, `GET /api/v1/logs`, and Berry scripting via `/api/v1/apps/script/{name}`. `PUT /api/v1/apps/active` was tried as a "Switch To App" push button and removed as not useful — unit 16 is retired and must not be reused.


## Tests

`tests/test_plugin.py` is the whole suite — plain asserts, no framework, run it with `python tests/test_plugin.py`. It stubs `Domoticz` and `requests` in `sys.modules`, injects `Parameters`/`Devices`/`Images` into the plugin's namespace, and drives a fake panel that enforces the real status codes (422 on unknown overlay/effect names or an empty body, 400 on a bad app name, 404 on a bad indicator id).

It exists to pin the things that break automations *silently*: unit numbers, device names and types, selector level values, and the exact JSON sent to each endpoint. The last block asserts that no AWTRIX 3 field name (`BRI`, `TCOL`, `TEFF`, `OVERLAY`, `sleep`, …) can reach the panel from any unit — that class of bug produces a working-looking plugin that does nothing, so it's worth a regression test rather than a code review.

When adding a device, add it to `EXPECTED_DEVICES` and assert its payload. A failure there usually means an existing automation would have broken too.

`ruff.toml` exempts `plugin.py` from `F821` (the injected globals) and `E501` (the manifest tag and default RTTTL melody can't be wrapped), and disables `UP032` repo-wide because `str.format()` is the established style here.
