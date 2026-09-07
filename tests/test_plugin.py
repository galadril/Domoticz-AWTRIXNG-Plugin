"""Offline tests for the AWTRIX NG plugin.

`plugin.py` can only be imported inside Domoticz, which also means it can never
be exercised on a build machine. These tests stub out `Domoticz` and `requests`
and drive the plugin against a fake panel that enforces the real AWTRIX NG
status codes, so the request payloads and the Domoticz device contract are
checked without any hardware.

Run with `python tests/test_plugin.py` - no test framework required.
"""

import importlib.util
import json
import os
import re
import sys
import types

PLUGIN = os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, "plugin.py")

FAILED = []
LOGS = []
ERRORS = []


def check(label, condition, detail=""):
    if condition:
        print("  ok   {}".format(label))
    else:
        print("  FAIL {} {}".format(label, detail))
        FAILED.append(label)


def equal(label, actual, expected):
    check(label, actual == expected, "\n         expected: {!r}\n         actual:   {!r}".format(expected, actual))


# --------------------------------------------------------------- Domoticz stub

class Image:
    def __init__(self, ident):
        self.ID = ident


class Device:
    def __init__(self, **kw):
        self.Name = kw.get("Name")
        self.Unit = kw.get("Unit")
        self.TypeName = kw.get("TypeName")
        self.Type = kw.get("Type")
        self.Subtype = kw.get("Subtype")
        self.Switchtype = kw.get("Switchtype")
        self.Options = kw.get("Options")
        self.Description = kw.get("Description", "")
        self.nValue = 0
        self.sValue = ""
        self.Color = ""
        self.BatteryLevel = 255

    def Create(self):
        DEVICES[self.Unit] = self

    def Update(self, **kw):
        for key, value in kw.items():
            setattr(self, key, value)


DEVICES = {}
IMAGES = {"AWTRIXNG": Image(7)}

domoticz = types.ModuleType("Domoticz")
domoticz.Device = Device
domoticz.Image = lambda *a, **k: types.SimpleNamespace(Create=lambda: None)
domoticz.Debugging = lambda *a: None
domoticz.Heartbeat = lambda *a: None
domoticz.Log = LOGS.append
domoticz.Debug = lambda m: None
domoticz.Error = ERRORS.append
sys.modules["Domoticz"] = domoticz


# ------------------------------------------------------------ fake AWTRIX NG

HOST = "192.168.1.50"
CALLS = []
OFFLINE = [False]

CAPABILITIES = {
    "transitions": ["Random", "Slide", "Dim", "Zoom", "Rotate", "Pixelate", "Curtain",
                    "Ripple", "Blink", "Reload", "Fade", "Cover", "Uncover", "Split",
                    "Blinds", "Blocks", "Flash", "Diamond", "Wave", "Rain", "Melt",
                    "Interlace"],
    "overlays": ["Snow", "Rain", "Drizzle", "Storm", "Thunder", "Frost"],
}

STATE = {
    "/api/v1/capabilities": CAPABILITIES,
    "/api/v1/device": {
        "temperature": 21.4, "humidity": 55.0, "lightLevel": 42.5,
        "matrixPower": True, "batteryPercent": 88, "currentApp": "Time",
        "indicators": [{"on": True, "color": "#FF0000"},
                       {"on": False, "color": "#000000"},
                       {"on": True, "color": "#0000FF"}],
    },
    "/api/v1/settings": {
        "autoBrightness": False, "brightness": 204, "transitionEffect": "Zoom",
        "textColor": "#00FF00", "timeMode": 3, "scroll": {"mode": "bounce"},
        "autoTransition": True,
    },
    "/api/v1/display": {"power": True, "brightness": 204, "overlay": "Rain"},
}

APP_NAME_PATTERN = re.compile(r"[A-Za-z0-9_-]{1,32}$")


class Response:
    def __init__(self, status, body=None):
        self.status_code = status
        self.text = json.dumps(body) if body is not None else ""

    def json(self):
        return json.loads(self.text)


def fake_request(method, url, json=None, auth=None, timeout=None):
    """Reject exactly what a real panel rejects, so bad payloads cannot pass."""
    body = json
    path = url.split(HOST, 1)[1]
    CALLS.append((method, path, body))

    if OFFLINE[0]:
        raise Exception("ConnectionError: panel unplugged")

    # A JSON body is required on these routes; {} is a 422.
    if method in ("PUT", "PATCH", "POST") and path in (
            "/api/v1/display/moodlight", "/api/v1/settings", "/api/v1/display",
            "/api/v1/notifications", "/api/v1/audio/play", "/api/v1/device/sleep"):
        if not body:
            return Response(422, {"error": "validationFailed"})

    if method == "GET":
        return Response(200, STATE.get(path))

    if path.startswith("/api/v1/apps/pushed/"):
        name = path.rsplit("/", 1)[1]
        if not APP_NAME_PATTERN.match(name):
            return Response(400, {"error": "invalidName", "field": "name"})
        if not body:
            return Response(422, {"error": "validationFailed"})

    if path == "/api/v1/apps/active" and "name" not in (body or {}):
        return Response(422, {"error": "validationFailed", "field": "name"})

    if path.startswith("/api/v1/indicators/"):
        if method == "PUT" and not body:
            return Response(422, {"error": "validationFailed"})
        if path.rsplit("/", 1)[1] not in ("1", "2", "3"):
            return Response(404, {"error": "notFound"})

    # Names are checked against capabilities; an unknown one is a hard 422.
    if path == "/api/v1/display":
        overlay = (body or {}).get("overlay")
        if overlay is not None and overlay.lower() not in [o.lower() for o in CAPABILITIES["overlays"]]:
            return Response(422, {"error": "validationFailed", "field": "overlay"})
    if path == "/api/v1/settings":
        effect = (body or {}).get("transitionEffect")
        if effect is not None and effect.lower() not in [t.lower() for t in CAPABILITIES["transitions"]]:
            return Response(422, {"error": "validationFailed", "field": "transitionEffect"})

    return Response(200)


requests = types.ModuleType("requests")
requests.request = fake_request
requests_auth = types.ModuleType("requests.auth")
requests_auth.HTTPBasicAuth = lambda user, password: ("basic", user)
requests.auth = requests_auth
sys.modules["requests"] = requests
sys.modules["requests.auth"] = requests_auth


# ------------------------------------------------------------------- load it

spec = importlib.util.spec_from_file_location("awtrixng", PLUGIN)
plugin = importlib.util.module_from_spec(spec)
plugin.Parameters = {"Address": HOST, "Username": "", "Password": "",
                     "Mode1": "39762", "Mode6": "0"}
plugin.Devices = DEVICES
plugin.Images = IMAGES
spec.loader.exec_module(plugin)
plugin.Parameters = {"Address": HOST, "Username": "", "Password": "",
                     "Mode1": "39762", "Mode6": "0"}
plugin.Devices = DEVICES
plugin.Images = IMAGES


def press(unit, description=None, command="On", level=0, color=""):
    """Set a description then switch the device, the way every sample does."""
    if description is not None:
        DEVICES[unit].Description = description
    CALLS.clear()
    plugin.onCommand(unit, command, level, color)
    return list(CALLS)


def only(calls):
    equal("exactly one request", len(calls), 1)
    return calls[0] if calls else (None, None, None)


# =============================================================== the tests

print("\n[onStart] devices")
plugin.onStart()

EXPECTED_DEVICES = {
    1: ("Power", "Switch"), 2: ("Lux", "Illumination"), 3: ("Temp+Hum", "Temp+Hum"),
    4: ("Send Notification", None), 5: ("Send Custom App", None),
    6: ("Send Settings", None), 7: ("Next App", None), 8: ("Previous App", None),
    9: ("Dismiss Notification", None), 10: ("RTTTL", None),
    11: ("Transition effect", "Selector Switch"), 12: ("Overlay", "Selector Switch"),
    13: ("Text color", "RGB"), 14: ("Brightness", "Dimmer"), 15: ("Sleep Mode", None),
    16: ("Switch To App", None), 17: ("Moodlight", "RGB"),
    18: ("Indicator Top", "RGB"), 19: ("Indicator Middle", "RGB"),
    20: ("Indicator Bottom", "RGB"), 21: ("Clock layout", "Selector Switch"),
    22: ("Text scroll", "Selector Switch"), 23: ("Auto Transition", "Switch"),
}
equal("all units created", sorted(DEVICES), sorted(EXPECTED_DEVICES))
for unit, (name, typename) in EXPECTED_DEVICES.items():
    equal("unit {} name".format(unit), DEVICES[unit].Name, name)
    equal("unit {} type".format(unit), DEVICES[unit].TypeName, typename)

# The payload devices must be push buttons reading their description, because
# dzVents does setDescription(...) then switchOn() and carries no payload.
for unit in (4, 5, 6, 7, 8, 9, 10, 15, 16):
    equal("unit {} is a push button".format(unit),
          (DEVICES[unit].Type, DEVICES[unit].Subtype, DEVICES[unit].Switchtype),
          (244, 73, 9))

equal("overlay selector entries", DEVICES[12].Options["LevelNames"],
      "Off|Snow|Rain|Drizzle|Storm|Thunder|Frost")
check("transition selector keeps AWTRIX 3 order",
      DEVICES[11].Options["LevelNames"].startswith(
          "Off|Random|Slide|Dim|Zoom|Rotate|Pixelate|Curtain|Ripple|Blink|Reload|Fade"))

print("\n[custom app] the main automation: array via description, no appname")
payload = [{"text": "21.4C", "icon": 2355}, {"text": "412W", "icon": 95}]
method, path, body = only(press(5, json.dumps(payload)))
equal("method", method, "PUT")
equal("path", path, "/api/v1/apps/pushed/Domoticz")
equal("payload reaches the panel untouched", body, payload)

method, path, body = only(press(5, '[{"appname": "My Room!!", "text": "x"}]'))
equal("appname sanitised into the path", path, "/api/v1/apps/pushed/MyRoom")
check("appname stripped from the body", "appname" not in body[0])

method, path, body = only(press(5, '{"appname": "' + "x" * 40 + '", "text": "y"}'))
check("over-long appname truncated to 32", APP_NAME_PATTERN.match(path.rsplit("/", 1)[1]))

CALLS.clear()
DEVICES[5].Description = ""
plugin.onCommand(5, "On", 0, "")
equal("empty description sends nothing", CALLS, [])

print("\n[notifications] all three description forms")
equal("json form", only(press(4, '{"text": "600 L", "icon": 9766}'))[2],
      {"text": "600 L", "icon": 9766})
equal("icon;text form", only(press(4, "9766;Check it!"))[2],
      {"icon": "9766", "text": "Check it!"})
equal("plain text uses the default icon", only(press(4, "Hello, AWTRIX!"))[2],
      {"icon": "39762", "text": "Hello, AWTRIX!"})
equal("dismiss route", only(press(9))[:2], ("DELETE", "/api/v1/notifications/active"))

print("\n[selectors] level values are a compatibility contract")
# dzvents-weather-overlay-sample.txt relies on exactly these levels.
for level, overlay in ((0, None), (10, "Snow"), (20, "Rain"), (30, "Drizzle"),
                       (40, "Storm"), (50, "Thunder"), (60, "Frost")):
    method, path, body = only(press(12, command="Set Level", level=level))
    equal("overlay level {} -> {}".format(level, overlay), (path, body["overlay"]),
          ("/api/v1/display", overlay))
for level, effect in ((10, "Random"), (20, "Slide"), (30, "Dim"), (40, "Zoom"), (120, "Cover")):
    equal("transition level {} -> {}".format(level, effect),
          only(press(11, command="Set Level", level=level))[2],
          {"transitionEffect": effect})

print("\n[display and settings]")
equal("power on", only(press(1, command="On"))[:3],
      ("PATCH", "/api/v1/display", {"power": True}))
equal("power off", only(press(1, command="Off"))[:3],
      ("PATCH", "/api/v1/display", {"power": False}))
equal("brightness 60% scales to 0-255", only(press(14, command="Set Level", level=60))[2],
      {"autoBrightness": False, "brightness": 153})
equal("brightness off restores auto-brightness", only(press(14, command="Off"))[2],
      {"autoBrightness": True})
equal("text colour is sent as #RRGGBB",
      only(press(13, command="Set Color", level=100, color='{"r":0,"g":255,"b":0}'))[2],
      {"textColor": "#00FF00"})
check("Color field is valid JSON for Domoticz", json.loads(DEVICES[13].Color)["g"] == 255)
equal("text colour off returns to white", only(press(13, command="Off"))[2],
      {"textColor": "#FFFFFF"})
equal("clock layout", only(press(21, command="Set Level", level=40))[2], {"timeMode": 4})
equal("scroll mode", only(press(22, command="Set Level", level=30))[2], {"scroll": "bounce"})
equal("auto transition", only(press(23, command="Off"))[2], {"autoTransition": False})
equal("settings passthrough", only(press(6, '{"appDurationMs": 10000}'))[2],
      {"appDurationMs": 10000})

print("\n[audio, sleep, apps]")
equal("rtttl", only(press(10, "Simpsons:d=4,o=5,b=160:c.6"))[:3],
      ("POST", "/api/v1/audio/play", {"rtttl": "Simpsons:d=4,o=5,b=160:c.6"}))
equal("next app", only(press(7))[:2], ("POST", "/api/v1/apps/next"))
equal("previous app", only(press(8))[:2], ("POST", "/api/v1/apps/previous"))
equal("switch to app", only(press(16, "Time"))[:3],
      ("PUT", "/api/v1/apps/active", {"name": "Time"}))

# Sleep takes seconds from the description, or from sValue for updateText().
equal("sleep from description converts to ms", only(press(15, "1800"))[:3],
      ("POST", "/api/v1/device/sleep", {"durationMs": 1800000}))
DEVICES[15].sValue = "900"
equal("sleep falls back to sValue", only(press(15, ""))[2], {"durationMs": 900000})
DEVICES[15].sValue = ""
equal("sleep defaults when unset", only(press(15, ""))[2], {"durationMs": 60000})

print("\n[moodlight and indicators]")
method, path, body = only(press(17, command="Set Color", level=70, color='{"r":255,"g":128,"b":0}'))
equal("moodlight put", (method, path), ("PUT", "/api/v1/display/moodlight"))
equal("moodlight colour and brightness", body, {"brightness": 178, "color": "#FF8000"})
equal("moodlight off deletes", only(press(17, command="Off"))[:2],
      ("DELETE", "/api/v1/display/moodlight"))
equal("indicator put", only(press(18, command="Set Color", level=100, color='{"r":255,"g":0,"b":0}'))[:3],
      ("PUT", "/api/v1/indicators/1", {"color": "#FF0000"}))
equal("indicator off deletes", only(press(20, command="Off"))[:2],
      ("DELETE", "/api/v1/indicators/3"))

print("\n[heartbeat] panel state is mapped onto the devices")
CALLS.clear()
plugin.onHeartbeat()
equal("polls device, settings and display", [c[1] for c in CALLS],
      ["/api/v1/device", "/api/v1/settings", "/api/v1/display"])
equal("temp, humidity and comfort index", DEVICES[3].sValue, "21.4;55.0;1")
equal("battery level", DEVICES[3].BatteryLevel, 88)
equal("light level", DEVICES[2].sValue, "42.5")
equal("power reconciled", (DEVICES[1].nValue, DEVICES[1].sValue), (1, "ON"))
equal("transition read back", DEVICES[11].sValue, "40")
equal("overlay read back", DEVICES[12].sValue, "20")
equal("brightness read back as a percentage", DEVICES[14].sValue, "80")
equal("clock layout read back", DEVICES[21].sValue, "30")
equal("scroll read back", DEVICES[22].sValue, "30")
equal("auto transition read back", DEVICES[23].nValue, 1)
equal("indicator state echoed from the panel",
      [(DEVICES[u].nValue, json.loads(DEVICES[u].Color)["r"]) for u in (18, 19, 20)],
      [(1, 255), (0, 0), (1, 0)])

CALLS.clear()
for _ in range(3):
    plugin.onHeartbeat()
equal("settings and display are slow-polled", [c[1] for c in CALLS],
      ["/api/v1/device"] * 3)

print("\n[resilience] an unreachable panel must not be retried at full cadence")
OFFLINE[0] = True
attempts = []
for _ in range(12):
    CALLS.clear()
    plugin.onHeartbeat()
    attempts.append(len(CALLS))
OFFLINE[0] = False
equal("backs off after consecutive failures", attempts,
      [1, 0, 1, 0, 0, 0, 1, 0, 0, 0, 0, 0])
check("logged the outage", any("unreachable" in m for m in LOGS))

print("\n[regression] no AWTRIX 3 field names may reach the panel")
LEGACY = ("BRI", "ABRI", "TCOL", "TEFF", "OVERLAY", "ATIME", "TSPEED", "WD", "WDCA")
plugin.Devices = DEVICES
emitted = []
for unit in sorted(DEVICES):
    DEVICES[unit].Description = '{"text": "x"}' if unit in (4, 5) else "1"
    CALLS.clear()
    try:
        plugin.onCommand(unit, "Set Level", 10, '{"r":1,"g":2,"b":3}')
    except Exception:
        pass
    emitted.extend(json.dumps(c[2]) for c in CALLS if c[2])
blob = " ".join(emitted)
for key in LEGACY:
    check("no legacy key {!r} in any payload".format(key), '"{}"'.format(key) not in blob)
check("no legacy 'sleep' key", '"sleep"' not in blob)

print("\n" + "=" * 60)
if FAILED:
    print("{} check(s) FAILED:".format(len(FAILED)))
    for label in FAILED:
        print("  - {}".format(label))
    sys.exit(1)
print("all checks passed")
