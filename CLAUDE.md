# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A single-file Domoticz Python plugin (`plugin.py`) that exposes an AWTRIX NG smart pixel clock as a set of Domoticz virtual devices. This repo is a port of the author's earlier AWTRIX 3 plugin — see "Backward-compatibility contract" below, it is the dominant constraint on changes here.

Everything lives in `plugin.py`. `icons/`, `images/`, `samples/` are assets/docs; `AWTRIXNG-Icons.zip` is the Domoticz device icon pack loaded at startup.

## Commands

There is no test suite, no linter config, and no package manifest. CI (`.github/workflows/validate.yml`) runs exactly two checks, both reproducible locally:

```bash
python .github/scripts/validate_plugin.py   # parses the XML docstring header of plugin.py
python -m compileall .                      # syntax check
```

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

`UNIT_POWER` is bidirectional: commanded via `PATCH /api/v1/display`, and reconciled from the device state on each heartbeat.

**All HTTP goes through `BasePlugin._request`**, which owns the base URL, optional `HTTPBasicAuth`, the 5s timeout, error logging, and the JSON-or-text-or-None return convention. It returns `None` on any failure, so callers must not assume a dict — `onHeartbeat` guards with `isinstance(stats, dict)`. Add new endpoint calls through this method rather than calling `requests` directly.

## Backward-compatibility contract

The user's existing Domoticz automations and the scripts in `samples/` must keep working against this NG rewrite. Device set and behaviour may grow, but must not change or shrink. Concretely, when touching a unit, check `samples/*.txt` for how it is actually driven — the samples are the spec:

- Device *names* matter. dzVents resolves devices by `"<hardware name> - <device name>"`, e.g. `AWTRIX NG - Send Custom App`, `AWTRIX NG - Sleep Mode`, `AWTRIX NG - Overlay`, `AWTRIX NG - Power`. Renaming a device in `onStart` breaks every script referencing it.
- Selector *level values* matter. `dzvents-weather-overlay-sample.txt` calls `switchSelector(30)` expecting Drizzle, `40` Storm, `50` Thunder, `60` Frost. Level is `10 * index` into the `|`-separated options string, so reordering `OVERLAY_OPTIONS` / `TRANSITION_OPTIONS` silently remaps existing automations. Append new entries; never insert.
- How payload devices are *written* matters. The samples use `setDescription(json)` + `switchOn()` (custom app) and `updateText(seconds)` + `switchOn()` (sleep mode), i.e. they set the payload out-of-band and then trigger with a switch command. A unit whose `onCommand` branch reads the payload from `Command` will not see anything those scripts set — read `Devices[Unit].Description` / `.sValue` instead where the samples imply it.

## AWTRIX NG API mapping

NG renamed essentially every field from the AWTRIX 3 short-key convention (`BRI`, `TCOL`, `ABRI`, `TEFF`, `lux`, `matrix`) to camelCase. Ported code carrying the old keys will be accepted-and-ignored or rejected rather than failing loudly, so verify against the spec — `https://blueforcer.github.io/awtrix-ng/api/openapi.yaml`, docs at `https://blueforcer.github.io/awtrix-ng/`.

Confirmed NG shapes relevant to this plugin:

| Purpose | Endpoint | Body / fields |
| --- | --- | --- |
| Device + sensor state | `GET /api/v1/device` | `lightLevel` (0–100 %, *not* lux), `temperature`, `humidity`, `matrixPower`, `brightness`, `currentApp` |
| Power / overlay | `PATCH /api/v1/display` | `power`, `overlay` (name from `capabilities.overlays`, `null`/`""` clears), `overlaySettings` — **overlay lives here, not in settings** |
| Settings | `PATCH /api/v1/settings` | `autoBrightness`, `brightness` (0–255), `transitionEffect` (name from `capabilities.transitions`), `textColor` (`"#RRGGBB"`), `appDurationMs`, `transitionDurationMs` |
| Notification | `POST /api/v1/notifications` | pushed-app fields + `name`, `hold`, `stack`, `wakeup`, `sound`, `soundRtttl` |
| Dismiss | `DELETE /api/v1/notifications/active` | — |
| Custom app | `PUT /api/v1/apps/pushed/{name}` | object or array; array creates indexed instances |
| App rotation | `POST /api/v1/apps/next` / `/previous` | empty |
| Audio | `POST /api/v1/audio/play` | exactly one of `sound`, `mp3`, `melody`, `track`, `rtttl`, `station`, `index`, `url` |
| Sleep | `POST /api/v1/device/sleep` | `durationMs` (integer, **milliseconds**) |
| Discover valid names | `GET /api/v1/capabilities` | effects, overlays, transitions, palettes |

Unused-but-available NG capabilities worth knowing about when asked to extend the plugin: `PUT /api/v1/display/moodlight`, `PUT/DELETE /api/v1/indicators/{1..3}`, `PUT /api/v1/apps/active`, `PUT /api/v1/apps/order`, Berry scripting via `/api/v1/apps/script/{name}`.
