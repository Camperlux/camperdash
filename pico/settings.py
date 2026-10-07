# Runtime settings for the hub.
#
# config.py holds the factory defaults.  Anything the user changes on the
# Settings page is written to settings.json and applied over the top of those
# defaults at boot, so a unit can be set up entirely from the web UI without
# anyone editing code.  If settings.json is missing or corrupt we fall back to
# the defaults rather than refusing to start - a hub that won't boot is worse
# than one running on factory settings.

import json
import config as cfg

FILE = "settings.json"

# setting name -> the config.py attribute it overrides
_WIFI = {"mode": "WIFI_MODE", "ssid": "WIFI_SSID", "password": "WIFI_PASSWORD",
         "ap_ssid": "AP_SSID", "ap_password": "AP_PASSWORD", "ap_ip": "AP_IP"}
_DEVICES = {"bms": "BMS_ADDR", "renogy": "RENOGY_ADDR", "victron": "VICTRON_ADDR",
            "victron_key": "VICTRON_KEY", "heater": "HEATER_ADDR"}
_LOCATION = {"lat": "WEATHER_LAT", "lon": "WEATHER_LON", "name": "WEATHER_PLACE"}
# Where the forecast is for. "auto": the van's GPS position while it has a fix,
# else the location above. "fixed": always the location above, GPS or not.
# "from" records how that location was filled in ("typed", "gps" - the van's
# own GPS - or "device", the phone or laptop's location), so the page can say.
# Not in an older config.py, hence the defaults.
_LOCATION_OPT = {"source": ("WEATHER_SOURCE", "auto"), "from": ("WEATHER_FROM", "")}
WEATHER_SOURCES = ("auto", "fixed")
WEATHER_FROM = ("", "typed", "gps", "device")
# Numbers rather than strings, so they need their own handling on load/save.
_WIFI_NUM = {"join_timeout_s": "WIFI_JOIN_TIMEOUT_S", "retry_s": "WIFI_RETRY_S"}
_WIFI_BOOL = {"ap_always": "WIFI_AP_ALWAYS"}
# The OLED. Which way up it reads and how bright it is depend on where it ends
# up mounted, so they belong to the user rather than to config.py.
_DISPLAY_NUM = {"brightness": "OLED_BRIGHTNESS", "refresh_ms": "OLED_REFRESH_MS",
                "level_ok": "LEVEL_OK_DEG", "level_warn": "LEVEL_WARN_DEG",
                "level_max": "LEVEL_MAX_DEG",
                "level_refresh_ms": "LEVEL_REFRESH_MS"}
_DISPLAY_BOOL = {"on": "OLED_ON", "invert": "OLED_INVERT", "rotate": "OLED_ROTATE",
                 "level_swap_xy": "LEVEL_SWAP_XY",
                 "level_invert_roll": "LEVEL_INVERT_ROLL",
                 "level_invert_pitch": "LEVEL_INVERT_PITCH",
                 "gps_enabled": "GPS_ENABLED",
                 # The drift log is a diagnostic that gets switched on for
                 # a day or two and off again, so it belongs with the
                 # settings rather than in a file that needs a redeploy.
                 "level_log": "LEVEL_LOG_ENABLED"}
# Page layouts. Kept on the hub rather than in each browser so a van is set up
# once and every phone, tablet and laptop that opens it agrees.
_DISPLAY_STR = {"overview": "OVERVIEW_VIEW", "level": "LEVEL_VIEW"}

# The Drive page's tick-off list, shown on the van display. Used when config.py
# has no CHECKLIST, so an older config.py still boots.
DEFAULT_CHECKLIST = ["Roof vents shut", "Gas off", "Cupboards latched", "Windows shut",
                     "Step and awning in", "Ramps removed"]
# The van display's sound levels, 0-100. 100 is as loud as it was designed to
# be; each step down is half a decibel. Touch sounds may go to 0 (off); the
# alarm may not go below ALARM_MIN - the Alerts sound switch silences alarms.
DEFAULT_PANEL = {"click_volume": 100, "alarm_volume": 100}
ALARM_MIN = 30
# The Switches page's six buttons, in order: named here so the van can be set
# up for what is actually wired. Up to 14 characters, what fits on a button
# on the van display.
DEFAULT_SWITCHES = ["Water pump", "Fridge", "Garage light", "Inverter", "Lights", "USB"]
SWITCH_NAME_MAX = 14
# Their icons, chosen from this list in Settings. Every one must be drawn both
# by the display (display/icons.py) and by the web pages (the i-* symbols).
SWITCH_ICONS = ("bulb", "sun", "drop", "snow", "flame", "fan", "bolt", "plug", "power",
                "usb", "battery", "tv", "music", "cup", "wifi", "home", "engine",
                "siren", "switch", "clock")
DEFAULT_SWITCH_ICONS = ["drop", "snow", "home", "bolt", "bulb", "power"]
CHECKLIST_MAX = 10          # items
CHECKLIST_ITEM_MAX = 32     # characters each - what fits on the display


def clean_panel(p, base):
    """The van display's settings from p, over base; None if p is unusable."""
    if not isinstance(p, dict):
        return None
    out = dict(base)
    for k, lo in (("click_volume", 0), ("alarm_volume", ALARM_MIN)):
        if k in p:
            try:
                v = int(p[k])
            except (TypeError, ValueError):
                return None
            out[k] = lo if v < lo else 100 if v > 100 else v
    return out


def clean_switches(names):
    """Six button names from whatever was sent or stored, or None if it is not
    a list. A blank name keeps that button's default."""
    if not isinstance(names, list):
        return None
    out = []
    for i, d in enumerate(DEFAULT_SWITCHES):
        x = names[i] if i < len(names) else ""
        x = x.strip()[:SWITCH_NAME_MAX] if isinstance(x, str) else ""
        out.append(x or d)
    return out


def clean_switch_icons(icons):
    """Six icon names, each from SWITCH_ICONS, or None if it is not a list.
    An unknown or missing one keeps that button's default."""
    if not isinstance(icons, list):
        return None
    return [icons[i] if i < len(icons) and icons[i] in SWITCH_ICONS else d
            for i, d in enumerate(DEFAULT_SWITCH_ICONS)]


def clean_checklist(items):
    """A usable checklist from whatever was sent or stored, or None if it is
    not a list at all. Blank lines go; overlong items are cut."""
    if not isinstance(items, list):
        return None
    out = []
    for x in items:
        if isinstance(x, str):
            x = x.strip()[:CHECKLIST_ITEM_MAX]
            if x and x not in out:
                out.append(x)
    return out[:CHECKLIST_MAX]


_cache = None


def _display_defaults():
    d = {k: getattr(cfg, a) for k, a in _DISPLAY_NUM.items()}
    d.update({k: getattr(cfg, a) for k, a in _DISPLAY_BOOL.items()})
    d.update({k: getattr(cfg, a) for k, a in _DISPLAY_STR.items()})
    return d


def defaults():
    w = {k: getattr(cfg, a) for k, a in _WIFI.items()}
    w.update({k: getattr(cfg, a) for k, a in _WIFI_NUM.items()})
    w.update({k: getattr(cfg, a) for k, a in _WIFI_BOOL.items()})
    # Known networks, tried in order: home first, phone hotspot second, and so
    # on.  The old single ssid/password is kept as the first entry so an
    # existing unit carries on working after an update.
    w["networks"] = []
    if getattr(cfg, "WIFI_SSID", ""):
        w["networks"].append({"ssid": cfg.WIFI_SSID, "password": cfg.WIFI_PASSWORD, "on": True})
    loc = {k: getattr(cfg, a) for k, a in _LOCATION.items()}
    loc.update({k: getattr(cfg, a, d) for k, (a, d) in _LOCATION_OPT.items()})
    return {
        "wifi": w,
        "devices": {k: getattr(cfg, a) for k, a in _DEVICES.items()},
        "location": loc,
        "api_token": cfg.API_TOKEN,
        "alerts": {"off": list(cfg.ALERTS_OFF), "sound": cfg.ALERT_SOUND,
                   "low_soc": getattr(cfg, "LOW_SOC", 20)},
        "display": _display_defaults(),
        "checklist": list(getattr(cfg, "CHECKLIST", DEFAULT_CHECKLIST)),
        "switches": list(getattr(cfg, "SWITCH_NAMES", DEFAULT_SWITCHES)),
        "switch_icons": list(getattr(cfg, "SWITCH_ICONS", DEFAULT_SWITCH_ICONS)),
        "panel": dict(getattr(cfg, "PANEL", DEFAULT_PANEL)),
        # The hub password. Only a hash of the key a browser works out from
        # it is kept (never the password), and one per paired display.
        # web: require it at all - off leaves the hub open, as it was.
        "auth": {"hash": "", "web": True, "devices": {}},
    }


def load(refresh=False):
    global _cache
    if _cache is not None and not refresh:
        return _cache
    s = defaults()
    try:
        try:
            with open(FILE) as f:
                saved = json.load(f)
        except (OSError, ValueError):
            # interrupted mid-save: the previous copy is still there
            with open(FILE + ".bak") as f:
                saved = json.load(f)
            print("settings: recovered from .bak")
        for section in ("wifi", "devices", "location", "display"):
            for k, v in (saved.get(section) or {}).items():
                if k not in s[section]:
                    continue
                if isinstance(v, str):
                    s[section][k] = v
                elif isinstance(v, bool):
                    s[section][k] = v
                elif isinstance(v, int):
                    s[section][k] = v
                elif isinstance(v, float):
                    s[section][k] = v
        nets = (saved.get("wifi") or {}).get("networks")
        if isinstance(nets, list):
            clean = []
            for n in nets:
                if isinstance(n, dict) and isinstance(n.get("ssid"), str) and n["ssid"]:
                    clean.append({"ssid": n["ssid"],
                                  "password": n.get("password") if isinstance(n.get("password"), str) else "",
                                  # switched off: kept, but not joined
                                  "on": n.get("on") is not False})
            s["wifi"]["networks"] = clean[:8]      # a sane ceiling
        if s["location"]["source"] not in WEATHER_SOURCES:
            s["location"]["source"] = "auto"
        if s["location"]["from"] not in WEATHER_FROM:
            s["location"]["from"] = ""
        if isinstance(saved.get("api_token"), str):
            s["api_token"] = saved["api_token"]
        al = saved.get("alerts")
        if isinstance(al, dict):
            if isinstance(al.get("off"), list):
                s["alerts"]["off"] = [x for x in al["off"] if isinstance(x, str)]
            if isinstance(al.get("sound"), bool):
                s["alerts"]["sound"] = al["sound"]
            if isinstance(al.get("low_soc"), int):
                s["alerts"]["low_soc"] = max(5, min(80, al["low_soc"]))
        cl = clean_checklist(saved.get("checklist"))
        if cl is not None:
            s["checklist"] = cl
        sw = clean_switches(saved.get("switches"))
        if sw is not None:
            s["switches"] = sw
        si = clean_switch_icons(saved.get("switch_icons"))
        if si is not None:
            s["switch_icons"] = si
        pn = clean_panel(saved.get("panel"), s["panel"])
        if pn is not None:
            s["panel"] = pn
        au = saved.get("auth")
        if isinstance(au, dict):
            if isinstance(au.get("hash"), str) and len(au["hash"]) in (0, 64):
                s["auth"]["hash"] = au["hash"]
            if isinstance(au.get("web"), bool):
                s["auth"]["web"] = au["web"]
            dv = au.get("devices")
            if isinstance(dv, dict):
                s["auth"]["devices"] = {str(k)[:20]: v for k, v in dv.items()
                                        if isinstance(v, str) and len(v) == 64}
    except (OSError, ValueError):
        pass                     # no file yet, or it's unreadable - use defaults
    _cache = s
    return s


def save(s):
    """Write settings.json so that a power cut always leaves a readable copy.

    The filesystem cannot rename onto an existing file, so there is no single
    atomic step available.  The previous version deleted settings.json and then
    renamed the new one in, which left a window with no settings at all - losing
    power there dropped the user's networks, location, alert choices and every
    device pairing, and the unit came back on factory defaults.  Keeping the old
    file as .bak until the new one is in place means load() always has something
    to fall back on.
    """
    import os
    tmp = FILE + ".tmp"
    bak = FILE + ".bak"
    with open(tmp, "w") as f:
        json.dump(s, f)
    try:
        try:
            os.remove(bak)
        except OSError:
            pass
        try:
            os.rename(FILE, bak)       # keep the old copy until the new one lands
        except OSError:
            pass                       # no existing file: nothing to preserve
        os.rename(tmp, FILE)
        try:
            os.remove(bak)
        except OSError:
            pass
    except OSError as e:
        print("settings save failed:", e)
    global _cache
    _cache = s


def apply():
    """Push the saved settings onto config, so every cfg.* reader sees them."""
    s = load()
    for k, a in _WIFI.items():
        setattr(cfg, a, s["wifi"][k])
    for k, a in _WIFI_NUM.items():
        setattr(cfg, a, s["wifi"][k])
    for k, a in _WIFI_BOOL.items():
        setattr(cfg, a, s["wifi"][k])
    cfg.WIFI_NETWORKS = list(s["wifi"]["networks"])
    for k, a in _DEVICES.items():
        setattr(cfg, a, (s["devices"][k] or "").lower())
    for k, a in _LOCATION.items():
        setattr(cfg, a, s["location"][k])
    for k, (a, _d) in _LOCATION_OPT.items():
        setattr(cfg, a, s["location"][k])
    cfg.API_TOKEN = s["api_token"]
    cfg.ALERTS_OFF = list(s["alerts"]["off"])
    cfg.ALERT_SOUND = s["alerts"]["sound"]
    cfg.LOW_SOC = s["alerts"]["low_soc"]
    cfg.CHECKLIST = list(s["checklist"])
    cfg.SWITCH_NAMES = list(s["switches"])
    cfg.SWITCH_ICONS = list(s["switch_icons"])
    cfg.PANEL = dict(s["panel"])
    for k, a in _DISPLAY_NUM.items():
        setattr(cfg, a, s["display"][k])
    for k, a in _DISPLAY_BOOL.items():
        setattr(cfg, a, s["display"][k])
    for k, a in _DISPLAY_STR.items():
        setattr(cfg, a, s["display"][k])
    return s


def public():
    """Settings for the UI: never hand a password back out over HTTP."""
    s = load()
    w = dict(s["wifi"])
    w["password"] = ""
    w["password_set"] = bool(s["wifi"]["password"])
    w["ap_password"] = ""
    w["ap_password_set"] = bool(s["wifi"]["ap_password"])
    # never hand the network passwords back out, only whether each has one
    w["networks"] = [{"ssid": n["ssid"], "password_set": bool(n.get("password")),
                      "on": n.get("on") is not False}
                     for n in s["wifi"]["networks"]]
    d = dict(s["devices"])
    d["victron_key"] = ""
    d["victron_key_set"] = bool(s["devices"]["victron_key"])
    return {"wifi": w, "devices": d, "location": dict(s["location"]),
            "alerts": {"off": list(s["alerts"]["off"]), "sound": s["alerts"]["sound"],
                       "low_soc": s["alerts"]["low_soc"]},
            "display": dict(s["display"]),
            "checklist": list(s["checklist"]),
            "switches": list(s["switches"]),
            "switch_icons": list(s["switch_icons"]),
            "panel": dict(s["panel"]),
            "api_token_set": bool(s["api_token"]),
            # whether a password is set, never the hash
            "auth": {"set": bool(s["auth"]["hash"]), "web": s["auth"]["web"],
                     "devices": sorted(s["auth"]["devices"])}}
