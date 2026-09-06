"""
<plugin key="AWTRIXNG" name="AWTRIX NG" author="galadril" version="2.0.0" wikilink="https://github.com/galadril/Domoticz-AWTRIXNG-Plugin/wiki" externallink="https://github.com/galadril/Domoticz-AWTRIXNG-Plugin">
    <description>
        <h2>AWTRIX NG Plugin</h2><br/>
        Integrates an AWTRIX NG Smart Pixel Clock with Domoticz.
    </description>
    <params>
        <param field="Address" label="IP Address" width="200px" required="true"/>
        <param field="Username" label="Username" width="200px"/>
        <param field="Password" label="Password" width="200px" password="true"/>
        <param field="Mode1" label="Default Icon ID" width="100px" default="39762"/>
        <param field="Mode6" label="Debug Level" width="200px">
            <options>
                <option label="None" value="0" default="true"/>
                <option label="Basic Debugging" value="62"/>
                <option label="All" value="-1"/>
            </options>
        </param>
    </params>
</plugin>
"""

import Domoticz
import json
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

TRANSITION_OPTIONS = "Off|Slide|Zoom|Fade|Random"
OVERLAY_OPTIONS = "None|Snow|Rain|Frost|Drizzle|Storm|Thunder|Wind|Clouds"


class BasePlugin:
    def __init__(self):
        self.baseUrl = ""
        self.auth = None
        self.iconId = "39762"

    def onStart(self):
        Domoticz.Debugging(int(Parameters["Mode6"]))
        Domoticz.Log("AWTRIX NG plugin started")

        self.iconId = Parameters.get("Mode1", "39762") or "39762"
        self.baseUrl = "http://{}".format(Parameters["Address"])

        if Parameters.get("Username"):
            self.auth = HTTPBasicAuth(Parameters["Username"], Parameters.get("Password", ""))

        if "AWTRIXNG" not in Images:
            Domoticz.Image("AWTRIXNG-Icons.zip").Create()

        if UNIT_POWER not in Devices:
            Domoticz.Device(Name="Power", Unit=UNIT_POWER, TypeName="Switch", Image=Images["AWTRIXNG"].ID).Create()
        if UNIT_LUX not in Devices:
            Domoticz.Device(Name="Lux", Unit=UNIT_LUX, TypeName="Lux", Image=Images["AWTRIXNG"].ID).Create()
        if UNIT_TEMPHUM not in Devices:
            Domoticz.Device(Name="Temp+Hum", Unit=UNIT_TEMPHUM, TypeName="Temp+Hum", Image=Images["AWTRIXNG"].ID).Create()
        if UNIT_NOTIFICATION not in Devices:
            Domoticz.Device(Name="Send Notification", Unit=UNIT_NOTIFICATION, TypeName="Text", Image=Images["AWTRIXNG"].ID).Create()
        if UNIT_CUSTOMAPP not in Devices:
            Domoticz.Device(Name="Send Custom App", Unit=UNIT_CUSTOMAPP, TypeName="Text", Image=Images["AWTRIXNG"].ID).Create()
        if UNIT_SETTINGS not in Devices:
            Domoticz.Device(Name="Send Settings", Unit=UNIT_SETTINGS, TypeName="Text", Image=Images["AWTRIXNG"].ID).Create()
        if UNIT_NEXTAPP not in Devices:
            Domoticz.Device(Name="Next App", Unit=UNIT_NEXTAPP, TypeName="Switch", Switchtype=9, Image=Images["AWTRIXNG"].ID).Create()
        if UNIT_PREVAPP not in Devices:
            Domoticz.Device(Name="Previous App", Unit=UNIT_PREVAPP, TypeName="Switch", Switchtype=9, Image=Images["AWTRIXNG"].ID).Create()
        if UNIT_DISMISS not in Devices:
            Domoticz.Device(Name="Dismiss Notification", Unit=UNIT_DISMISS, TypeName="Switch", Switchtype=9, Image=Images["AWTRIXNG"].ID).Create()
        if UNIT_RTTTL not in Devices:
            Domoticz.Device(Name="RTTTL", Unit=UNIT_RTTTL, TypeName="Text", Image=Images["AWTRIXNG"].ID).Create()
        if UNIT_TRANSITION not in Devices:
            options = {"LevelActions": "|" * (len(TRANSITION_OPTIONS.split("|")) - 1), "LevelNames": TRANSITION_OPTIONS, "LevelOffHidden": "false", "SelectorStyle": "0"}
            Domoticz.Device(Name="Transition effect", Unit=UNIT_TRANSITION, TypeName="Selector Switch", Options=options, Image=Images["AWTRIXNG"].ID).Create()
        if UNIT_OVERLAY not in Devices:
            options = {"LevelActions": "|" * (len(OVERLAY_OPTIONS.split("|")) - 1), "LevelNames": OVERLAY_OPTIONS, "LevelOffHidden": "false", "SelectorStyle": "0"}
            Domoticz.Device(Name="Overlay", Unit=UNIT_OVERLAY, TypeName="Selector Switch", Options=options, Image=Images["AWTRIXNG"].ID).Create()
        if UNIT_TEXTCOLOR not in Devices:
            Domoticz.Device(Name="Text color", Unit=UNIT_TEXTCOLOR, TypeName="Color Switch", Switchtype=7, Image=Images["AWTRIXNG"].ID).Create()
        if UNIT_BRIGHTNESS not in Devices:
            Domoticz.Device(Name="Brightness", Unit=UNIT_BRIGHTNESS, TypeName="Dimmer", Image=Images["AWTRIXNG"].ID).Create()
        if UNIT_SLEEP not in Devices:
            Domoticz.Device(Name="Sleep Mode", Unit=UNIT_SLEEP, TypeName="Switch", Image=Images["AWTRIXNG"].ID).Create()

        Domoticz.Heartbeat(30)

    def onStop(self):
        Domoticz.Log("AWTRIX NG plugin stopped")

    def _request(self, method, path, jsonBody=None):
        url = self.baseUrl + path
        try:
            response = requests.request(method, url, json=jsonBody, auth=self.auth, timeout=5)
            Domoticz.Debug("{} {} -> {}".format(method, url, response.status_code))
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

    def parsePushMessage(self, message):
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

    def sendNotification(self, message):
        payload = self.parsePushMessage(message)
        if payload is None:
            return
        self._request("POST", "/api/v1/notifications", payload)

    def sendCustomApp(self, message):
        payload = self.parsePushMessage(message)
        if payload is None:
            return
        appname = "custom"
        if isinstance(payload, dict) and "appname" in payload:
            appname = payload.pop("appname")
        self._request("PUT", "/api/v1/apps/pushed/{}".format(appname), payload)

    def sendSettings(self, message):
        try:
            payload = json.loads(message)
        except ValueError:
            Domoticz.Error("Invalid settings JSON: {}".format(message))
            return
        self._request("PATCH", "/api/v1/settings", payload)

    def onCommand(self, Unit, Command, Level, Color):
        Domoticz.Debug("onCommand called for Unit {}: Command '{}', Level: {}".format(Unit, Command, Level))

        if Unit == UNIT_POWER:
            state = Command.upper() == "ON"
            self._request("PATCH", "/api/v1/display", {"power": state})
            Devices[Unit].Update(nValue=1 if state else 0, sValue="")

        elif Unit == UNIT_NOTIFICATION:
            self.sendNotification(Command)
            Devices[Unit].Update(nValue=0, sValue=Command)

        elif Unit == UNIT_CUSTOMAPP:
            self.sendCustomApp(Command)
            Devices[Unit].Update(nValue=0, sValue=Command)

        elif Unit == UNIT_SETTINGS:
            self.sendSettings(Command)
            Devices[Unit].Update(nValue=0, sValue=Command)

        elif Unit == UNIT_NEXTAPP:
            self._request("POST", "/api/v1/apps/next")
            Devices[Unit].Update(nValue=0, sValue="")

        elif Unit == UNIT_PREVAPP:
            self._request("POST", "/api/v1/apps/previous")
            Devices[Unit].Update(nValue=0, sValue="")

        elif Unit == UNIT_DISMISS:
            self._request("DELETE", "/api/v1/notifications/active")
            Devices[Unit].Update(nValue=0, sValue="")

        elif Unit == UNIT_RTTTL:
            self._request("POST", "/api/v1/audio/play", {"rtttl": Command})
            Devices[Unit].Update(nValue=0, sValue=Command)

        elif Unit == UNIT_TRANSITION:
            index = int(Level / 10)
            self._request("PATCH", "/api/v1/settings", {"TEFF": index})
            Devices[Unit].Update(nValue=1, sValue=str(Level))

        elif Unit == UNIT_OVERLAY:
            index = int(Level / 10)
            names = OVERLAY_OPTIONS.split("|")
            overlay = names[index] if 0 <= index < len(names) else "None"
            self._request("PATCH", "/api/v1/settings", {"OVERLAY": overlay.lower()})
            Devices[Unit].Update(nValue=1, sValue=str(Level))

        elif Unit == UNIT_TEXTCOLOR:
            r = (Color.get("r", 0) if isinstance(Color, dict) else 0)
            g = (Color.get("g", 0) if isinstance(Color, dict) else 0)
            b = (Color.get("b", 0) if isinstance(Color, dict) else 0)
            self._request("PATCH", "/api/v1/settings", {"TCOL": [r, g, b]})
            Devices[Unit].Update(nValue=1, sValue=str(Level), Color=json.dumps(Color) if isinstance(Color, dict) else "")

        elif Unit == UNIT_BRIGHTNESS:
            if Command.upper() == "OFF":
                self._request("PATCH", "/api/v1/settings", {"ABRI": False})
                Devices[Unit].Update(nValue=0, sValue="0")
            else:
                brightness = int(Level * 255 / 100)
                self._request("PATCH", "/api/v1/settings", {"ABRI": False, "BRI": brightness})
                Devices[Unit].Update(nValue=1, sValue=str(Level))

        elif Unit == UNIT_SLEEP:
            state = Command.upper() == "ON"
            if state:
                try:
                    seconds = int(Devices[Unit].sValue) if Devices[Unit].sValue else 60
                except ValueError:
                    seconds = 60
                self._request("POST", "/api/v1/device/sleep", {"sleep": seconds})
            Devices[Unit].Update(nValue=1 if state else 0, sValue=Devices[Unit].sValue)

    def onHeartbeat(self):
        stats = self._request("GET", "/api/v1/device")
        if not isinstance(stats, dict):
            return

        if "lux" in stats:
            Devices[UNIT_LUX].Update(nValue=0, sValue=str(stats["lux"]))

        if "temp" in stats and "hum" in stats:
            temp = stats["temp"]
            hum = stats["hum"]
            Devices[UNIT_TEMPHUM].Update(nValue=0, sValue="{};{};0".format(temp, hum))

        if "matrix" in stats:
            powerOn = 1 if stats["matrix"] else 0
            if Devices[UNIT_POWER].nValue != powerOn:
                Devices[UNIT_POWER].Update(nValue=powerOn, sValue="")


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
