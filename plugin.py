"""
<plugin key="AWTRIXNG" name="AWTRIX NG" author="Mark Heinis" version="1.0.0" wikilink="https://github.com/galadril/Domoticz-AWTRIXNG-Plugin/wiki" externallink="https://github.com/galadril/Domoticz-AWTRIXNG-Plugin">
    <description>
        <h2>AWTRIX NG Plugin</h2><br/>
        Integrates an AWTRIX NG Smart Pixel Clock with Domoticz.<br/>
        <p>
        Enter the payload for the push buttons (Send Notification, Send Custom App,
        Send Settings, RTTTL, Sleep Mode) in the <b>description</b> of the device,
        then switch the button on.
        </p><p>
        Please download icon 39762 as default Domoticz icon on your device:
        <a href="https://developer.lametric.com/icons">https://developer.lametric.com/icons</a>.
        </p><p>
        More on AWTRIX NG is available on <a href="https://blueforcer.github.io/awtrix-ng/">its documentation site</a>.
        A list of shared automation flows is available on the
        <a href="https://flows.blueforcer.de/search?provider=domoticz">AWTRIX Flows website</a>.
        </p>
    </description>
    <params>
        <param field="Address" label="IP Address" width="200px" required="true" default="192.168.1.100"/>
        <param field="Username" label="Username" width="200px" required="false" default=""/>
        <param field="Password" label="Password" width="200px" required="false" default="" password="true"/>
        <param field="Mode1" label="Default Icon ID" width="100px" required="true" default="39762"/>
        <param field="Mode6" label="Debug" width="200px">
            <options>
                <option label="None" value="0" default="true"/>
                <option label="Python Only" value="2"/>
                <option label="Basic Debugging" value="62"/>
                <option label="Basic+Messages" value="126"/>
                <option label="Connections Only" value="16"/>
                <option label="Connections+Queue" value="144"/>
                <option label="All" value="-1"/>
            </options>
        </param>
    </params>
</plugin>
"""

import Domoticz
import json
import re
import requests
from requests.auth import HTTPBasicAuth

UNIT_POWER = 1
UNIT_LUX = 2
UNIT_TEMPHUM = 3
UNIT_NOTIFICATION = 4
UNIT_CUSTOMAPP = 5
UNIT_SETTINGS = 6
UNIT_NEXTAPP = 7
UNIT_PREVAPP = 8
UNIT_DISMISS = 9
UNIT_RTTTL = 10
UNIT_TRANSITION = 11
UNIT_OVERLAY = 12
UNIT_TEXTCOLOR = 13
UNIT_BRIGHTNESS = 14
UNIT_SLEEP = 15
# 16 was a "Switch To App" push button. Retired - do not reuse the number, a
# reinstall would rewire it to whatever takes its place.
UNIT_MOODLIGHT = 17
UNIT_INDICATOR1 = 18
UNIT_INDICATOR2 = 19
UNIT_INDICATOR3 = 20
UNIT_CLOCKLAYOUT = 21
UNIT_SCROLL = 22
UNIT_AUTOTRANSITION = 23

INDICATOR_UNITS = {UNIT_INDICATOR1: 1, UNIT_INDICATOR2: 2, UNIT_INDICATOR3: 3}

# Selector level == 10 * index into these lists. The order is a compatibility
# contract with existing automations (see samples/) - only ever append.
TRANSITION_NAMES = [
    # Indices 0-11 keep the AWTRIX 3 ordering; do not reorder.
    "Off", "Random", "Slide", "Dim", "Zoom", "Rotate",
    "Pixelate", "Curtain", "Ripple", "Blink", "Reload", "Fade",
    # Added by AWTRIX NG.
    "Cover", "Uncover", "Split", "Blinds", "Blocks", "Flash",
    "Diamond", "Wave", "Rain", "Melt", "Interlace",
]
# NG ships exactly these six weather overlays.
OVERLAY_NAMES = ["Off", "Snow", "Rain", "Drizzle", "Storm", "Thunder", "Frost"]
# Settings.scroll accepts the bare mode string as shorthand for {"mode": ...}.
SCROLL_NAMES = ["Static", "Wrap", "Loop", "Bounce"]
# Settings.timeMode is the clock layout, 0-6.
CLOCK_LAYOUT_NAMES = ["Layout 0", "Layout 1", "Layout 2", "Layout 3",
                      "Layout 4", "Layout 5", "Layout 6"]

IMAGE_KEY = "AWTRIXNG"
DEFAULT_APP_NAME = "Domoticz"
DEFAULT_SLEEP_SECONDS = 60
COLOR_WHITE = {"r": 255, "g": 255, "b": 255}

# Sensors are read every heartbeat; settings and display state only exist to
# keep the Domoticz UI in step with changes made on the panel itself, so they
# are read every Nth heartbeat instead.
SLOW_POLL_TICKS = 4


def selectorOptions(names, offHidden):
    return {
        "LevelActions": "|" * (len(names) - 1),
        "LevelNames": "|".join(names),
        "LevelOffHidden": "true" if offHidden else "false",
        "SelectorStyle": "1",
    }


class BasePlugin:
    def __init__(self):
        self.baseUrl = ""
        self.auth = None
        self.iconId = "39762"
        self.selectedTextColor = COLOR_WHITE.copy()
        # Firmware spelling of every supported name, keyed by lowercase name.
        self.transitions = {}
        self.overlays = {}
        self.failures = 0
        self.skipTicks = 0
        self.slowTick = 0

    def onStart(self):
        debugLevel = int(Parameters["Mode6"])
        if debugLevel > 0:
            Domoticz.Debugging(debugLevel)
        Domoticz.Log("AWTRIX NG plugin started")

        self.iconId = Parameters.get("Mode1", "39762") or "39762"
        self.baseUrl = "http://{}".format(Parameters["Address"])

        if Parameters.get("Username"):
            self.auth = HTTPBasicAuth(Parameters["Username"], Parameters.get("Password", ""))

        if IMAGE_KEY not in Images:
            Domoticz.Image("AWTRIXNG-Icons.zip").Create()
        image = Images[IMAGE_KEY].ID

        self.createDevices(image)
        self.loadCapabilities()
        Domoticz.Heartbeat(30)

    def loadCapabilities(self):
        """Cache the effect/overlay names this firmware accepts.

        Unknown names are rejected with 422, so the selectors are validated
        against this list rather than firing blind requests.
        """
        caps = self._request("GET", "/api/v1/capabilities")
        if not isinstance(caps, dict):
            Domoticz.Debug("Capabilities unavailable; selector names will not be pre-validated")
            return

        self.transitions = {str(n).lower(): str(n) for n in caps.get("transitions", []) or []}
        self.overlays = {str(n).lower(): str(n) for n in caps.get("overlays", []) or []}

        for name in TRANSITION_NAMES[1:]:
            if self.transitions and name.lower() not in self.transitions:
                Domoticz.Log("Transition '{}' is not supported by this firmware".format(name))
        for name in OVERLAY_NAMES[1:]:
            if self.overlays and name.lower() not in self.overlays:
                Domoticz.Log("Overlay '{}' is not supported by this firmware".format(name))

    def createDevices(self, image):
        if UNIT_POWER not in Devices:
            Domoticz.Device(Name="Power", Unit=UNIT_POWER, TypeName="Switch", Image=image).Create()
            self.sendNotification({"icon": self.iconId, "text": DEFAULT_APP_NAME})

        if UNIT_LUX not in Devices:
            Domoticz.Device(Name="Lux", Unit=UNIT_LUX, TypeName="Illumination", Image=image).Create()

        if UNIT_TEMPHUM not in Devices:
            Domoticz.Device(Name="Temp+Hum", Unit=UNIT_TEMPHUM, TypeName="Temp+Hum", Image=image).Create()

        # Payload devices are push buttons; the payload lives in the description.
        if UNIT_NOTIFICATION not in Devices:
            Domoticz.Device(Name="Send Notification", Unit=UNIT_NOTIFICATION,
                            Type=244, Subtype=73, Switchtype=9, Image=image,
                            Description="Enter notification text here").Create()

        if UNIT_CUSTOMAPP not in Devices:
            Domoticz.Device(Name="Send Custom App", Unit=UNIT_CUSTOMAPP,
                            Type=244, Subtype=73, Switchtype=9, Image=image,
                            Description="Enter custom app text here").Create()

        if UNIT_SETTINGS not in Devices:
            Domoticz.Device(Name="Send Settings", Unit=UNIT_SETTINGS,
                            Type=244, Subtype=73, Switchtype=9, Image=image,
                            Description='{"brightness": 120}').Create()

        if UNIT_NEXTAPP not in Devices:
            Domoticz.Device(Name="Next App", Unit=UNIT_NEXTAPP,
                            Type=244, Subtype=73, Switchtype=9, Image=image,
                            Description="Advance to the next app").Create()

        if UNIT_PREVAPP not in Devices:
            Domoticz.Device(Name="Previous App", Unit=UNIT_PREVAPP,
                            Type=244, Subtype=73, Switchtype=9, Image=image,
                            Description="Go back to the previous app").Create()

        if UNIT_DISMISS not in Devices:
            Domoticz.Device(Name="Dismiss Notification", Unit=UNIT_DISMISS,
                            Type=244, Subtype=73, Switchtype=9, Image=image,
                            Description="Dismiss the active notification").Create()

        if UNIT_RTTTL not in Devices:
            Domoticz.Device(Name="RTTTL", Unit=UNIT_RTTTL,
                            Type=244, Subtype=73, Switchtype=9, Image=image,
                            Description="The Simpsons:d=4,o=5,b=160:c.6,e6,f#6,8a6,g.6,e6,c6,8a,8f#,8f#,8f#,2g,8p,8p,8f#,8f#,8f#,8g,a#.,8c6,8c6,8c6,c6").Create()

        if UNIT_TRANSITION not in Devices:
            Domoticz.Device(Name="Transition effect", Unit=UNIT_TRANSITION, TypeName="Selector Switch",
                            Options=selectorOptions(TRANSITION_NAMES, True), Image=image).Create()

        if UNIT_OVERLAY not in Devices:
            Domoticz.Device(Name="Overlay", Unit=UNIT_OVERLAY, TypeName="Selector Switch",
                            Options=selectorOptions(OVERLAY_NAMES, False), Image=image).Create()

        if UNIT_TEXTCOLOR not in Devices:
            Domoticz.Device(Name="Text color", Unit=UNIT_TEXTCOLOR, TypeName="RGB", Image=image).Create()

        if UNIT_BRIGHTNESS not in Devices:
            Domoticz.Device(Name="Brightness", Unit=UNIT_BRIGHTNESS, TypeName="Dimmer", Image=image).Create()

        if UNIT_SLEEP not in Devices:
            Domoticz.Device(Name="Sleep Mode", Unit=UNIT_SLEEP,
                            Type=244, Subtype=73, Switchtype=9, Image=image,
                            Description="Send the device into sleep mode for X seconds (input X in the description)").Create()

        if UNIT_MOODLIGHT not in Devices:
            Domoticz.Device(Name="Moodlight", Unit=UNIT_MOODLIGHT, TypeName="RGB", Image=image).Create()

        for unit, corner in (
            (UNIT_INDICATOR1, "Top"),
            (UNIT_INDICATOR2, "Middle"),
            (UNIT_INDICATOR3, "Bottom"),
        ):
            if unit not in Devices:
                Domoticz.Device(Name="Indicator {}".format(corner), Unit=unit,
                                TypeName="RGB", Image=image).Create()

        if UNIT_CLOCKLAYOUT not in Devices:
            Domoticz.Device(Name="Clock layout", Unit=UNIT_CLOCKLAYOUT, TypeName="Selector Switch",
                            Options=selectorOptions(CLOCK_LAYOUT_NAMES, False), Image=image).Create()

        if UNIT_SCROLL not in Devices:
            Domoticz.Device(Name="Text scroll", Unit=UNIT_SCROLL, TypeName="Selector Switch",
                            Options=selectorOptions(SCROLL_NAMES, False), Image=image).Create()

        if UNIT_AUTOTRANSITION not in Devices:
            Domoticz.Device(Name="Auto Transition", Unit=UNIT_AUTOTRANSITION,
                            TypeName="Switch", Image=image).Create()

    def onStop(self):
        Domoticz.Log("AWTRIX NG plugin stopped")

    # ------------------------------------------------------------------ HTTP

    def _request(self, method, path, jsonBody=None):
        url = self.baseUrl + path
        try:
            response = requests.request(method, url, json=jsonBody, auth=self.auth, timeout=5)
            Domoticz.Debug("{} {} {} -> {}".format(method, url, jsonBody if jsonBody is not None else "", response.status_code))
            if response.status_code >= 300:
                Domoticz.Error("AWTRIX NG request failed: {} {} -> {} {}".format(method, url, response.status_code, response.text))
                return None
            if response.text:
                try:
                    return response.json()
                except ValueError:
                    return response.text
            return None
        except Exception as e:
            Domoticz.Error("AWTRIX NG request exception: {} {} -> {}".format(method, url, str(e)))
            return None

    # --------------------------------------------------------------- helpers

    def description(self, Unit):
        try:
            return (Devices[Unit].Description or "").strip()
        except Exception:
            return ""

    @staticmethod
    def commandColor(Color):
        """Domoticz hands colour to onCommand as a JSON string, not a dict."""
        info = Color
        if isinstance(info, str) and info:
            try:
                info = json.loads(info)
            except ValueError:
                return None
        if isinstance(info, dict):
            return {key: int(info.get(key, COLOR_WHITE[key])) for key in ("r", "g", "b")}
        return None

    @staticmethod
    def hexColor(rgb):
        return "#{:02X}{:02X}{:02X}".format(rgb["r"], rgb["g"], rgb["b"])

    @staticmethod
    def confirmColor(rgb):
        # Domoticz parses this field as JSON, so it must not be a Python repr:
        # str({...}) emits single quotes and the colour is silently dropped.
        confirm = {"ColorMode": 3}
        confirm.update(rgb)
        return json.dumps(confirm)

    def dimmerLevel(self, Unit, Command, Level, default=100):
        """'On' arrives without a level, so fall back to the last one we stored."""
        if Command != "On":
            return max(0, min(int(Level), 100))
        try:
            previous = int(Devices[Unit].sValue)
        except (TypeError, ValueError):
            previous = 0
        return previous if previous > 0 else default

    def parsePushMessage(self, message):
        """Accepts a JSON object/array, 'icon;text', or a bare message."""
        message = message.strip()
        if message.startswith("{") or message.startswith("["):
            try:
                return json.loads(message)
            except ValueError:
                Domoticz.Error("Invalid JSON in message: {}".format(message))
                return None
        if ";" in message:
            icon, text = message.split(";", 1)
            return {"icon": icon.strip(), "text": text.strip()}
        return {"icon": self.iconId, "text": message}

    @staticmethod
    def normaliseIcons(payload):
        """Coerce a numeric `icon` to the string AWTRIX NG requires.

        NG resolves `icon` as an ID string (or inline base64 over 64 chars) and
        **ignores a non-string icon** rather than rejecting the payload, so a
        numeric one renders the text with no icon and no error at all. dzVents
        serialises Lua numbers as JSON numbers and AWTRIX 3 accepted those, so
        convert here instead of making every existing script change.
        """
        entries = payload if isinstance(payload, list) else [payload]
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            icon = entry.get("icon")
            if isinstance(icon, bool) or not isinstance(icon, (int, float)):
                continue
            entry["icon"] = str(int(icon))
            Domoticz.Debug("Coerced numeric icon {} to {!r}".format(icon, entry["icon"]))
        return payload

    def customAppName(self, payload):
        """Pull 'appname' out of the payload, sanitised for use in the URL path."""
        entries = payload if isinstance(payload, list) else [payload]
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            appname = str(entry.pop("appname", "") or "")
            if not appname:
                continue
            # The API rejects anything outside ^[A-Za-z0-9_-]{1,32}$ with 400.
            sanitized = re.sub(r"[^A-Za-z0-9_-]", "", appname)[:32]
            if sanitized:
                if sanitized != appname:
                    Domoticz.Error("appname: '{}' was sanitized to: '{}'".format(appname, sanitized))
                return sanitized
        return DEFAULT_APP_NAME

    # -------------------------------------------------------------- commands

    def sendNotification(self, payload):
        if payload is None:
            return
        self._request("POST", "/api/v1/notifications", self.normaliseIcons(payload))

    def sendCustomApp(self, payload):
        if payload is None:
            return
        name = self.customAppName(payload)
        self._request("PUT", "/api/v1/apps/pushed/{}".format(name), self.normaliseIcons(payload))

    def sendSettings(self, message):
        try:
            payload = json.loads(message)
        except ValueError:
            Domoticz.Error("Invalid settings JSON: {}".format(message))
            return
        self._request("PATCH", "/api/v1/settings", payload)

    def resolveName(self, supported, name, kind):
        """Map our canonical name onto the firmware's own spelling."""
        if not supported:
            return name
        resolved = supported.get(name.lower())
        if resolved is None:
            Domoticz.Error("{} '{}' is not supported by this firmware".format(kind, name))
        return resolved

    def setTransition(self, Level):
        index = int(Level / 10)
        if index <= 0 or index >= len(TRANSITION_NAMES):
            return
        name = self.resolveName(self.transitions, TRANSITION_NAMES[index], "Transition")
        if name is None:
            return
        self._request("PATCH", "/api/v1/settings", {"transitionEffect": name})
        Devices[UNIT_TRANSITION].Update(nValue=Level, sValue=str(Level))

    def setOverlay(self, Level):
        index = int(Level / 10)
        if index < 0 or index >= len(OVERLAY_NAMES):
            return
        # Overlay lives on the display resource in NG; null clears it.
        if index == 0:
            payload = {"overlay": None}
        else:
            name = self.resolveName(self.overlays, OVERLAY_NAMES[index], "Overlay")
            if name is None:
                return
            payload = {"overlay": name}
        self._request("PATCH", "/api/v1/display", payload)
        Devices[UNIT_OVERLAY].Update(nValue=Level, sValue=str(Level))

    def setTextColor(self, Command, Level, Color):
        newColor = COLOR_WHITE.copy()
        dimmerState = 0
        dimmerLevel = Level

        if Command == "Off":
            dimmerLevel = 0
        elif Command == "Set Level":
            dimmerState = 1
            newColor = self.selectedTextColor.copy()
        else:
            dimmerState = 1
            newColor = self.commandColor(Color) or COLOR_WHITE.copy()

        self._request("PATCH", "/api/v1/settings", {"textColor": self.hexColor(newColor)})

        Devices[UNIT_TEXTCOLOR].Update(nValue=dimmerState, sValue=str(dimmerLevel),
                                       Color=self.confirmColor(newColor))
        if dimmerState:
            self.selectedTextColor.update(newColor)

    def setMoodlight(self, Command, Level, Color):
        if Command == "Off":
            self._request("DELETE", "/api/v1/display/moodlight")
            Devices[UNIT_MOODLIGHT].Update(nValue=0, sValue="0")
            return

        # Both fields are sticky on the panel, so only send what changed.
        level = self.dimmerLevel(UNIT_MOODLIGHT, Command, Level)
        payload = {"brightness": int(round(level * 255 / 100))}
        rgb = self.commandColor(Color)
        if rgb:
            payload["color"] = self.hexColor(rgb)

        self._request("PUT", "/api/v1/display/moodlight", payload)
        Devices[UNIT_MOODLIGHT].Update(nValue=1, sValue=str(level),
                                       Color=self.confirmColor(rgb) if rgb else Devices[UNIT_MOODLIGHT].Color)

    def setIndicator(self, Unit, Command, Level, Color):
        indicator = INDICATOR_UNITS[Unit]
        if Command == "Off":
            self._request("DELETE", "/api/v1/indicators/{}".format(indicator))
            Devices[Unit].Update(nValue=0, sValue="0")
            return

        rgb = self.commandColor(Color) or self.commandColor(Devices[Unit].Color) or COLOR_WHITE.copy()
        if rgb == {"r": 0, "g": 0, "b": 0}:
            # A colour resolving to 0 switches the indicator off on the panel.
            rgb = COLOR_WHITE.copy()
        self._request("PUT", "/api/v1/indicators/{}".format(indicator), {"color": self.hexColor(rgb)})
        Devices[Unit].Update(nValue=1, sValue=str(self.dimmerLevel(Unit, Command, Level)),
                             Color=self.confirmColor(rgb))

    def setBrightness(self, Command, Level):
        # "Off" hands control back to the light sensor, matching the AWTRIX 3 plugin.
        if Command == "Off":
            self._request("PATCH", "/api/v1/settings", {"autoBrightness": True})
            Devices[UNIT_BRIGHTNESS].Update(nValue=0, sValue="0")
            return

        percent = max(0, min(int(Level), 100))
        self._request("PATCH", "/api/v1/settings",
                      {"autoBrightness": False, "brightness": int(round(percent * 255 / 100))})
        Devices[UNIT_BRIGHTNESS].Update(nValue=1, sValue=str(percent))

    def sleep(self):
        raw = self.description(UNIT_SLEEP)
        if not raw:
            # dzVents scripts using updateText() land the duration in sValue.
            raw = (Devices[UNIT_SLEEP].sValue or "").strip()
        try:
            seconds = int(float(raw))
        except (TypeError, ValueError):
            seconds = DEFAULT_SLEEP_SECONDS
        if seconds <= 0:
            seconds = DEFAULT_SLEEP_SECONDS
        self._request("POST", "/api/v1/device/sleep", {"durationMs": seconds * 1000})
        Domoticz.Log("AWTRIX NG asleep for {} seconds".format(seconds))

    def onCommand(self, Unit, Command, Level, Color):
        Domoticz.Debug("onCommand called for Unit {}: Command '{}', Level: {}, Color: {}".format(Unit, Command, Level, Color))

        if Unit == UNIT_POWER:
            state = Command.upper() == "ON"
            self._request("PATCH", "/api/v1/display", {"power": state})
            Devices[Unit].Update(nValue=1 if state else 0, sValue="ON" if state else "OFF")

        elif Unit == UNIT_NOTIFICATION:
            message = self.description(Unit)
            if not message:
                Domoticz.Error("Description field is empty. Cannot send notification.")
                return
            self.sendNotification(self.parsePushMessage(message))

        elif Unit == UNIT_CUSTOMAPP:
            message = self.description(Unit)
            if not message:
                Domoticz.Error("Description field is empty. Cannot send custom app.")
                return
            self.sendCustomApp(self.parsePushMessage(message))

        elif Unit == UNIT_SETTINGS:
            message = self.description(Unit)
            if not message:
                Domoticz.Error("Description field is empty. Cannot send settings.")
                return
            self.sendSettings(message)

        elif Unit == UNIT_NEXTAPP:
            self._request("POST", "/api/v1/apps/next")

        elif Unit == UNIT_PREVAPP:
            self._request("POST", "/api/v1/apps/previous")

        elif Unit == UNIT_DISMISS:
            self._request("DELETE", "/api/v1/notifications/active")

        elif Unit == UNIT_RTTTL:
            rtttl = self.description(Unit)
            if not rtttl:
                Domoticz.Error("Description field is empty. Cannot play RTTTL.")
                return
            self._request("POST", "/api/v1/audio/play", {"rtttl": rtttl})

        elif Unit == UNIT_TRANSITION:
            self.setTransition(Level)

        elif Unit == UNIT_OVERLAY:
            self.setOverlay(Level)

        elif Unit == UNIT_TEXTCOLOR:
            self.setTextColor(Command, Level, Color)

        elif Unit == UNIT_BRIGHTNESS:
            self.setBrightness(Command, Level)

        elif Unit == UNIT_SLEEP:
            self.sleep()

        elif Unit == UNIT_MOODLIGHT:
            self.setMoodlight(Command, Level, Color)

        elif Unit in INDICATOR_UNITS:
            self.setIndicator(Unit, Command, Level, Color)

        elif Unit == UNIT_CLOCKLAYOUT:
            index = int(Level / 10)
            if 0 <= index < len(CLOCK_LAYOUT_NAMES):
                self._request("PATCH", "/api/v1/settings", {"timeMode": index})
                Devices[Unit].Update(nValue=Level, sValue=str(Level))

        elif Unit == UNIT_SCROLL:
            index = int(Level / 10)
            if 0 <= index < len(SCROLL_NAMES):
                self._request("PATCH", "/api/v1/settings", {"scroll": SCROLL_NAMES[index].lower()})
                Devices[Unit].Update(nValue=Level, sValue=str(Level))

        elif Unit == UNIT_AUTOTRANSITION:
            state = Command.upper() == "ON"
            self._request("PATCH", "/api/v1/settings", {"autoTransition": state})
            Devices[Unit].Update(nValue=1 if state else 0, sValue="ON" if state else "OFF")

        else:
            Domoticz.Error("Unknown Unit in onCommand: {}".format(Unit))

    # ------------------------------------------------------------- heartbeat

    def onHeartbeat(self):
        # Each request blocks the plugin thread for up to the 5s timeout, so an
        # unplugged panel must not be retried at full cadence.
        if self.skipTicks > 0:
            self.skipTicks -= 1
            return

        stats = self._request("GET", "/api/v1/device")
        if not isinstance(stats, dict):
            self.failures += 1
            self.skipTicks = min(2 ** self.failures, 16) - 1
            if self.failures == 1:
                Domoticz.Log("AWTRIX NG is unreachable; backing off between retries")
            return

        if self.failures:
            Domoticz.Log("AWTRIX NG is reachable again")
            self.failures = 0

        self.syncDeviceState(stats)

        if self.slowTick <= 0:
            self.slowTick = SLOW_POLL_TICKS
            self.syncSettings()
            self.syncDisplay()
        self.slowTick -= 1

    def syncDeviceState(self, stats):
        battery = stats.get("batteryPercent", 255)

        if "temperature" in stats and "humidity" in stats:
            temp = stats["temperature"]
            hum = stats["humidity"]
            try:
                humValue = float(hum)
            except (TypeError, ValueError):
                humValue = 0.0
            if humValue < 40:
                comfort = "2"
            elif humValue <= 70:
                comfort = "1"
            else:
                comfort = "3"
            Devices[UNIT_TEMPHUM].Update(nValue=0, sValue="{};{};{}".format(temp, hum, comfort),
                                         BatteryLevel=battery)

        if "lightLevel" in stats:
            # NG reports ambient light as a 0-100 percentage rather than raw lux.
            light = stats["lightLevel"]
            Devices[UNIT_LUX].Update(nValue=int(float(light)), sValue=str(light))

        if "matrixPower" in stats:
            powerOn = 1 if stats["matrixPower"] else 0
            Devices[UNIT_POWER].Update(nValue=powerOn, sValue="ON" if powerOn else "OFF",
                                       BatteryLevel=battery)

        # The panel echoes indicator state here, so the switches follow changes
        # made over MQTT or from a script.
        indicators = stats.get("indicators")
        if isinstance(indicators, list):
            for unit, indicator in INDICATOR_UNITS.items():
                if indicator > len(indicators):
                    continue
                entry = indicators[indicator - 1]
                if not isinstance(entry, dict):
                    continue
                on = 1 if entry.get("on") else 0
                rgb = self.parseColor(entry.get("color"))
                Devices[unit].Update(nValue=on, sValue="100" if on else "0",
                                     Color=self.confirmColor(rgb) if rgb else Devices[unit].Color)

    def syncSettings(self):
        settings = self._request("GET", "/api/v1/settings")
        if not isinstance(settings, dict):
            return

        effect = settings.get("transitionEffect")
        if isinstance(effect, str):
            names = [n.lower() for n in TRANSITION_NAMES]
            if effect.lower() in names:
                level = names.index(effect.lower()) * 10
                Devices[UNIT_TRANSITION].Update(nValue=level, sValue=str(level))

        rgb = self.parseColor(settings.get("textColor"))
        if rgb:
            isWhite = rgb == COLOR_WHITE
            Devices[UNIT_TEXTCOLOR].Update(nValue=0 if isWhite else 1,
                                           sValue="0" if isWhite else "50",
                                           Color=self.confirmColor(rgb))
            if not isWhite:
                self.selectedTextColor.update(rgb)

        timeMode = settings.get("timeMode")
        if isinstance(timeMode, int) and 0 <= timeMode < len(CLOCK_LAYOUT_NAMES):
            level = timeMode * 10
            Devices[UNIT_CLOCKLAYOUT].Update(nValue=level, sValue=str(level))

        scroll = settings.get("scroll")
        mode = scroll.get("mode") if isinstance(scroll, dict) else scroll
        if isinstance(mode, str):
            names = [n.lower() for n in SCROLL_NAMES]
            if mode.lower() in names:
                level = names.index(mode.lower()) * 10
                Devices[UNIT_SCROLL].Update(nValue=level, sValue=str(level))

        if "autoTransition" in settings:
            auto = 1 if settings["autoTransition"] else 0
            Devices[UNIT_AUTOTRANSITION].Update(nValue=auto, sValue="ON" if auto else "OFF")

        if "brightness" in settings or "autoBrightness" in settings:
            autoBri = bool(settings.get("autoBrightness", False))
            try:
                percent = int(round(int(settings.get("brightness", 128)) * 100 / 255))
            except (TypeError, ValueError):
                percent = 50
            percent = max(1, min(percent, 100))
            Devices[UNIT_BRIGHTNESS].Update(nValue=0 if autoBri else 1,
                                            sValue="0" if autoBri else str(percent))

    def syncDisplay(self):
        display = self._request("GET", "/api/v1/display")
        if not isinstance(display, dict):
            return

        overlay = display.get("overlay")
        names = [n.lower() for n in OVERLAY_NAMES]
        if not overlay:
            level = 0
        elif str(overlay).lower() in names:
            level = names.index(str(overlay).lower()) * 10
        else:
            return
        Devices[UNIT_OVERLAY].Update(nValue=level, sValue=str(level))

    @staticmethod
    def parseColor(value):
        """AWTRIX NG returns '#RRGGBB'; older firmware returned a packed int."""
        if isinstance(value, str) and value.startswith("#") and len(value) == 7:
            try:
                packed = int(value[1:], 16)
            except ValueError:
                return None
        elif isinstance(value, int):
            packed = value
        elif isinstance(value, (list, tuple)) and len(value) == 3:
            return {"r": int(value[0]), "g": int(value[1]), "b": int(value[2])}
        else:
            return None
        return {"r": (packed >> 16) & 0xFF, "g": (packed >> 8) & 0xFF, "b": packed & 0xFF}


global _plugin
_plugin = BasePlugin()


def onStart():
    global _plugin
    _plugin.onStart()


def onStop():
    global _plugin
    _plugin.onStop()


def onConnect(connection, status, description):
    pass


def onMessage(connection, data):
    pass


def onCommand(Unit, Command, Level, Color):
    global _plugin
    _plugin.onCommand(Unit, Command, Level, Color)


def onHeartbeat():
    global _plugin
    _plugin.onHeartbeat()
