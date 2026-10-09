"""The hub's own decisions: alerts, frost protection, Wi-Fi, the forecast's
location, the recorders.

These are the rules that run while nobody is watching - warning of a low or
unhealthy battery, of driving off still plugged in, starting the heater on a
freezing night (and, as important, stopping it) - so each is checked here
against the cases it was written for, with the hub's real code (pico/main.py,
loaded by hubsim without its start-up) and a stand-in heater.

Run:  python tests/test_hub_rules.py
"""
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hubsim                                                    # noqa: E402

work = tempfile.mkdtemp(prefix="hubtest_")
hub = hubsim.load_main(work)
cfg = sys.modules["config"]
passed = []


def ok(name):
    passed.append(name)
    print("ok  ", name)


# ---- battery alerts -------------------------------------------------------------
def batt(**kw):
    b = {"connected": True, "soc": 60, "cell_delta_mv": 20, "faults": [], "temps_c": [20.0],
         "current": 0.0}
    b.update(kw)
    return b


cfg.LOW_SOC = 20
hub._low_on = False
assert "low_battery" in hub._battery_alerts(batt(soc=19))
assert "low_battery" in hub._battery_alerts(batt(soc=21))        # still on: within 3% of the limit
assert "low_battery" not in hub._battery_alerts(batt(soc=23))    # off at the limit + 3
assert "low_battery" not in hub._battery_alerts(batt(soc=21))    # and stays off going down to 20
ok("low battery: on below the limit, off only 3% above it (no flicker)")

assert hub._battery_alerts(batt(connected=False, soc=5)) == {}
ok("no battery alerts while the battery monitor is out of range")

h = hub._battery_alerts(batt(cell_delta_mv=200, soc=50))
assert "cells 200 mV apart" in h["battery_health"]
assert "battery_health" not in hub._battery_alerts(batt(cell_delta_mv=200, soc=98))
assert "battery_health" not in hub._battery_alerts(batt(cell_delta_mv=200, soc=5))
ok("cell spread: a warning between 10% and 95% only (cells drift apart near full and empty)")

assert "battery_health" not in hub._battery_alerts(batt(faults=["MOSFET locked"]))
assert "Short circuit" in hub._battery_alerts(batt(faults=["Short circuit"]))["battery_health"]
ok("BMS faults alert, but 'MOSFET locked' (storage mode, on purpose) does not")

assert "battery at 52 C" in hub._battery_alerts(batt(temps_c=[52.0]))["battery_health"]
assert "below freezing" in hub._battery_alerts(batt(temps_c=[0.5], current=3.0))["battery_health"]
assert "battery_health" not in hub._battery_alerts(batt(temps_c=[0.5], current=0.0))
ok("hot battery alerts; charging below freezing alerts, cold but not charging does not")


# ---- driving off still plugged in -----------------------------------------------------
def alerts_with(alt_a=0.0, alt_v=12.6, mains=True, renogy=True):
    hub.state["renogy"] = {"connected": renogy, "alt_a": alt_a, "alt_v": alt_v}
    hub.state["victron"] = {"connected": mains}
    hub.state["victron_seen"] = time.ticks_ms()
    hub.state["battery"] = {"connected": False}
    hub._alert_hold = 0
    return [a["id"] for a in hub._alert_tick()]


cfg.ALERTS_OFF = []
assert "mains_while_running" in alerts_with(alt_a=2.0)              # charging from the alternator
assert "mains_while_running" in alerts_with(alt_v=13.9)             # or the line at 13.8 V+
assert "mains_while_running" not in alerts_with(alt_v=13.2)         # DC-DC trickle on hook-up
assert "mains_while_running" not in alerts_with(alt_a=2.0, mains=False)
assert "mains_while_running" not in alerts_with(alt_a=2.0, renogy=False)
ok("plugged in: engine (alternator current, or 13.8 V) + mains alerts; 13.2 V trickle does not")

hub.state["renogy"] = {"connected": True, "alt_a": 2.0, "alt_v": 14.0}
hub.state["victron"] = {"connected": True}
hub.state["victron_seen"] = time.ticks_ms() - cfg.VICTRON_STALE_MS - 1000
hub._alert_hold = 0
assert "mains_while_running" not in [a["id"] for a in hub._alert_tick()]
ok("plugged in: a stale mains reading does not count as plugged in")

cfg.ALERTS_OFF = ["mains_while_running"]
assert "mains_while_running" not in alerts_with(alt_a=2.0)
cfg.ALERTS_OFF = []
ok("an alert switched off in Settings stays quiet")


# ---- frost protection ------------------------------------------------------------------
sent = []


async def fake_heater(action, temp, water, level):
    sent.append(action)
    return {"ok": True}


hub.ble_hub.command_heater = fake_heater


def frost(temp_c, soc=80, minutes_running=None):
    hub.state["renogy"] = {"connected": True, "batt_temp_c": temp_c}
    hub.state["battery"] = {"connected": True, "soc": soc}
    if minutes_running is not None:
        hub._ah["started"] = time.time() - minutes_running * 60
    del sent[:]
    hubsim.run(hub._autoheat_check())
    return list(sent)


hub._ah.update({"armed": True, "active": False, "on_below": 5, "off_above": 9, "min_soc": 40,
                "max_run_min": 180, "started": 0})
assert frost(6) == [] and frost(5) == ["air"] and hub._ah["active"]
assert frost(8) == []                                           # in the gap: keeps going
assert frost(9) == ["off"] and not hub._ah["active"]
ok("frost protection: on at 5 C, still on at 8 C, off at 9 C")

hub._ah["active"] = False
assert frost(2, soc=30) == [] and "held off" in hub._ah["last"]
ok("frost protection: never starts with the battery below its minimum")

hub._ah["active"] = False
assert frost(2) == ["air"]
assert frost(2, soc=35) == ["off"]
ok("frost protection: stops if the battery falls below its minimum while heating")

hub._ah["active"] = False
assert frost(2) == ["air"]
assert frost(2, minutes_running=181) == ["off"] and "max run time" in hub._ah["last"]
ok("frost protection: stops after its maximum run time even if still cold")

hub._ah["active"] = False
hub.state["renogy"] = {"connected": False}
del sent[:]
hubsim.run(hub._autoheat_check())
assert sent == []
hub._ah["armed"] = False
assert frost(-10) == []
ok("frost protection: does nothing without a temperature reading, or disarmed")

hub._ah.update({"armed": True, "active": True, "started": time.time()})
del sent[:]
r = hubsim.run(hub.autoheat_set({"armed": False}))
assert sent == ["off"] and not r["autoheat"]["armed"] and not hub._ah["active"]
r = hubsim.run(hub.autoheat_set({"on_below": 8, "off_above": 6}))
assert r["autoheat"]["off_above"] > r["autoheat"]["on_below"]
r = hubsim.run(hub.autoheat_set({"on_below": -99, "max_run_min": 99999}))
assert r["autoheat"]["on_below"] == -20 and r["autoheat"]["max_run_min"] == 720
ok("frost protection settings: disarming turns the heater off; limits kept sane")


# ---- Wi-Fi decisions -------------------------------------------------------------------
waits = []
for fails in range(0, 30):
    hub._wifi_fails = fails
    waits.append(hub._retry_ms() // 1000)
assert waits[0] == 0 and set(waits[1:21]) == {15}
assert waits[21] == 30 and waits[22] == 60 and max(waits) == cfg.WIFI_RETRY_S
ok("Wi-Fi retries: every 15 s for the first 5 minutes, then backing off to WIFI_RETRY_S")

assert hub._overlaps("192.168.4.1", "192.168.5.20", "255.255.252.0")      # a /22 home network
assert hub._overlaps("192.168.4.1", "192.168.4.245", "255.255.255.0")
assert not hub._overlaps("192.168.44.1", "192.168.4.245", "255.255.252.0")
assert not hub._overlaps("192.168.4.1", "10.0.0.5", "255.0.0.0")
ok("hotspot clash: judged with the joined network's real mask (a /22 home network included)")

joined, clash = [False], [False]
hub._sta_joined = lambda: joined[0]
hub._net_clash = lambda: clash[0]
cfg.WIFI_MODE, cfg.WIFI_AP_ALWAYS = "sta", True
assert hub._ap_should_run()                                   # no network: the only way in
joined[0] = True
assert hub._ap_should_run()                                   # always on, no clash
clash[0] = True
assert not hub._ap_should_run()                               # always on, but it would clash
cfg.WIFI_AP_ALWAYS = False
clash[0] = False
assert not hub._ap_should_run()
cfg.WIFI_MODE = "ap"
assert hub._ap_should_run()
cfg.WIFI_MODE = "sta"
ok("hotspot: always up with no network; alongside one only if wanted and not clashing")


# ---- the trip recorder (level.py) and the stall recorder ---------------------------------
level = hub.level
level._trip_i = level._trip_n = 0
for k in range(level.TRIP_N + 50):                           # fills, then wraps
    level.trip_add({"ok": True, "roll": k * 0.01, "pitch": -1.5, "axes": {"x": 0, "y": 1.0, "z": 0}})
level.trip_add({"ok": False})                                # failed readings are not kept
rows = "".join(level.trip_chunks()).splitlines()
assert len(rows) == level.TRIP_N
first, last = rows[0].split(","), rows[-1].split(",")
assert first[2] == "0.50" and last[2] == "30.49" and first[3] == "-1.50" and first[4] == "1.000"
assert abs(float(last[0]) - time.time()) < 5
ok("trip recorder: keeps the last %d readings, oldest first, with correct clock times" % level.TRIP_N)

stalls = sys.modules["stalls"]
stalls._stalls[:] = []
stalls.mark("hotspot start")
stalls.mark("hotspot start")                                 # repeats collapse
assert stalls._marks == ["hotspot start"]
r = stalls.records()
assert r["threshold_ms"] == 500 and r["stalls"] == []
ok("stall recorder: labels collapse repeats; the record starts empty")


# ---- known networks switched off --------------------------------------------------------
settings = sys.modules["settings"]
cfg.WIFI_SSID = ""
cfg.WIFI_NETWORKS = [{"ssid": "Home", "password": "a", "on": True},
                     {"ssid": "Phone", "password": "b", "on": False},
                     {"ssid": "Site", "password": ""}]       # saved before the switch existed
assert [n["ssid"] for n in hub._known_networks()] == ["Home", "Site"]
cfg.WIFI_SSID = "Phone"                     # the old single network, switched off in the list
assert [n["ssid"] for n in hub._known_networks()] == ["Home", "Site"]
ok("Wi-Fi: a network switched off is never tried, even as the old single network")

r = hubsim.run(hub.settings_set({"wifi": {"ssid": "", "networks": [
    {"ssid": "Home", "password": "secret1", "on": True},
    {"ssid": "Phone", "password": "secret2", "on": False},
    {"ssid": "Site"}]}}))
assert r["ok"], r
assert [(n["ssid"], n["on"]) for n in r["settings"]["wifi"]["networks"]] == \
    [("Home", True), ("Phone", False), ("Site", True)]
assert "secret" not in str(r["settings"])
assert settings.load(refresh=True)["wifi"]["networks"][1] == {"ssid": "Phone", "password": "secret2", "on": False}
assert [n["ssid"] for n in hub._known_networks()] == ["Home", "Site"]
assert [n["ssid"] for n in hub.wifi_share()["networks"]] == ["Home", "Site"]
r = hubsim.run(hub.settings_set({"wifi": {"networks": [{"ssid": "Home", "on": False}]}}))
assert r["ok"] and r["restart_required"]                    # all off: the hub keeps to its hotspot
assert hub._known_networks() == [] and settings.load()["wifi"]["networks"][0]["password"] == "secret1"
ok("Wi-Fi: switched off is saved with its password kept, not joined, not shared with the display")

with open(settings.FILE, "w") as f:                          # a settings.json from before
    f.write('{"wifi": {"networks": [{"ssid": "Old", "password": "x"}]},'
            ' "location": {"lat": "51.5", "lon": "-0.1", "source": "nonsense"}}')
old = settings.load(refresh=True)
assert old["wifi"]["networks"] == [{"ssid": "Old", "password": "x", "on": True}]
assert old["location"]["source"] == "auto" and old["location"]["from"] == ""
ok("settings from before: networks come back switched on, the forecast automatic")


# ---- where the forecast is for ----------------------------------------------------------
hub._gps_fresh = lambda: (54.304, -2.198)                    # a fresh fix
cfg.WEATHER_LAT, cfg.WEATHER_LON = "51.5000", "-0.1200"
cfg.WEATHER_SOURCE = "auto"
assert hub._weather_where() == (54.304, -2.198, "gps")
cfg.WEATHER_SOURCE = "fixed"
assert hub._weather_where() == ("51.5000", "-0.1200", "settings")
hub._gps_fresh = lambda: None                                # no fix
cfg.WEATHER_SOURCE = "auto"
assert hub._weather_where() == ("51.5000", "-0.1200", "settings")
ok("forecast: the van's GPS while it has a fix, else the fixed place; always the fixed place when set so")

r = hubsim.run(hub.settings_set({"location": {"lat": "", "lon": "", "source": "fixed"}}))
assert not r["ok"] and "fixed" in r["error"]
r = hubsim.run(hub.settings_set({"location": {"source": "sometimes"}}))
assert not r["ok"]
hub._weather_due = 10 ** 9
r = hubsim.run(hub.settings_set({"location": {"lat": "51.5", "lon": "-0.12", "name": "London",
                                              "source": "fixed", "from": "gps"}}))
assert r["ok"], r
loc = r["settings"]["location"]
assert (loc["lat"], loc["source"], loc["from"]) == ("51.5000", "fixed", "gps")
assert cfg.WEATHER_SOURCE == "fixed" and hub._weather_due == 0
hub._weather_due = 10 ** 9
r = hubsim.run(hub.settings_set({"location": {"source": "auto", "from": "nonsense"}}))
assert r["ok"] and r["settings"]["location"]["from"] == "gps"     # an unknown origin is ignored
assert hub._weather_due == 0                                       # the choice alone refetches
m = hub.api_weather_meta()
assert (m["mode"], m["from"], m["fixed_place"], m["gps_fix"]) == ("auto", "gps", "London", False)
ok("forecast setting: fixed needs a place; a change of choice fetches again; the page is told why")



# ---- showing a stored password ------------------------------------------------------------
r = hubsim.run(hub.settings_set({"wifi": {"ap_password": "hotspot-pw-1", "networks": [
    {"ssid": "Home", "password": "home-pw-1"}]}}))
assert r["ok"], r
r = hubsim.run(hub.settings_reveal({"what": "ap"}))
assert not r["ok"] and "password" in r["error"]           # no hub password yet: never shown
r = hubsim.run(hub.settings_set({"auth": {"set_key": "ab" * 32}}))
assert r["ok"], r
assert hubsim.run(hub.settings_reveal({"what": "ap"})) == {"ok": True, "value": "hotspot-pw-1"}
assert hubsim.run(hub.settings_reveal({"what": "net", "ssid": "Home"}))["value"] == "home-pw-1"
assert not hubsim.run(hub.settings_reveal({"what": "net", "ssid": "Nope"}))["ok"]
assert not hubsim.run(hub.settings_reveal({"what": "hash"}))["ok"]
r = hubsim.run(hub.settings_set({"auth": {"web": False}}))
assert not hubsim.run(hub.settings_reveal({"what": "ap"}))["ok"]   # password not required: not shown
hubsim.run(hub.settings_set({"auth": {"clear": True, "web": True}}))
ok("stored passwords: shown only behind a hub password that is set and required")



# ---- factory reset ------------------------------------------------------------------------
os.makedirs("www", exist_ok=True); os.makedirs("dispfw", exist_ok=True)
for f in ("settings.json", "settings.json.bak", "hist.csv", "level_cal.json", "level_log.csv",
          "rc_last.txt", "weather.meta", "guard.json", "main_app.mpy", "main.py", "config.py",
          "www/app.html", "dispfw/manifest.json"):
    open(f, "w").write("x")
hub.factory_wipe()
left = sorted(os.listdir())
assert left == ["config.py", "dispfw", "main.py", "main_app.mpy", "www"], left
assert os.listdir("www") == ["app.html"] and os.listdir("dispfw") == ["manifest.json"]
ok("factory reset: every setting and log gone; the software, the pages and config.py kept")



# ---- two start-up faults found on the hardware --------------------------------------------
import types                                                      # noqa: E402
sys.modules.pop("indicate", None)
hub._reset_show(None)                       # no buzzer module: must not raise (it did: NameError)
sys.modules["indicate"] = types.SimpleNamespace(reset_hold=None)
hub._reset_show(2500)
assert sys.modules["indicate"].reset_hold == 2500
sys.modules.pop("indicate")
ok("factory reset countdown reaches the buzzer and light, and runs without them")

assert hub._dist_m(("54.3040", "-2.1980"), (54.304, -2.198)) < 1
assert 900 < hub._dist_m(("54.3040", "-2.1980"), ("54.3130", "-2.1980")) < 1100
ok("the forecast's moved-check takes the Settings location, stored as text")



# ---- the starter battery's charge from its resting voltage ----------------------------------
assert hub.starter_soc(12.89) == 100 and hub.starter_soc(13.4) == 100
assert hub.starter_soc(11.63) == 0 and hub.starter_soc(10.9) == 0
assert hub.starter_soc(12.23) == 50 and hub.starter_soc(12.65) == 80
assert hub.starter_soc(12.58) == 75                     # between the chart's 70% and 80% steps
assert all(hub.starter_soc(a[0]) <= hub.starter_soc(b[0]) for a, b in zip(hub.STARTER_CHART, hub.STARTER_CHART[1:]))
hub._engine_now, hub._engine_off_at = False, hub.ticks_ms() - 31 * 60000
assert hub._starter_status({"connected": True, "alt_v": 12.58}) == {"state": "rest", "v": 12.58, "soc": 75}
hub._engine_off_at = hub.ticks_ms() - 5 * 60000          # stopped five minutes ago
assert hub._starter_status({"connected": True, "alt_v": 12.7})["state"] == "settling"
assert hub._starter_status({"connected": True, "alt_v": 13.2}) == {"state": "charging", "v": 13.2, "soc": None}
hub._engine_now = True
assert hub._starter_status({"connected": True, "alt_v": 12.4})["state"] == "charging"
hub._engine_now = False
assert hub._starter_status({"connected": False, "alt_v": 12.6})["state"] == "offline"
ok("starter battery: charge from the resting voltage (sealed lead-acid chart); none while charging; settling after a drive")

# ---- the starter battery's type and its low warning ------------------------------------------
assert hub.starter_soc(12.41, "flooded") == 80 and hub.starter_soc(12.41, "agm") == 60
assert hub.starter_soc(12.07, "flooded") == 50 and hub.starter_soc(11.59, "flooded") == 0
hub.cfg.STARTER_TYPE = "flooded"
assert hub.starter_soc(12.41) == 80                       # the setting picks the chart
hub.cfg.STARTER_TYPE, hub.cfg.STARTER_LOW_SOC = "agm", 50
hub._engine_now, hub._engine_off_at = False, hub.ticks_ms() - 31 * 60000
assert hub._starter_alert({"connected": True, "alt_v": 12.6}) == {}
d = hub._starter_alert({"connected": True, "alt_v": 11.6})["starter_low"]
assert "about 0%" in d and "11.6 V" in d
assert "starter_low" in hub._starter_alert({"connected": True, "alt_v": 12.25})       # 50%: not 5% over yet
assert "starter_low" in hub._starter_alert({"connected": False, "alt_v": 12.6})       # out of range: kept
assert hub._starter_alert({"connected": True, "alt_v": 12.35}) == {}                  # 56%: cleared
hub._starter_alert({"connected": True, "alt_v": 11.6})
assert hub._starter_alert({"connected": True, "alt_v": 13.4}) == {}                   # charging clears it
hub._engine_off_at = hub.ticks_ms() - 5 * 60000
assert hub._starter_alert({"connected": True, "alt_v": 11.9}) == {}                   # settling: not judged
assert "starter_low" in [x["id"] for x in hub._alert_defs()]
r = hubsim.run(hub.settings_set({"alerts": {"starter_soc": 95, "starter_type": "flooded"}}))
assert r["ok"] and r["settings"]["alerts"]["starter_soc"] == 90 and r["settings"]["alerts"]["starter_type"] == "flooded"
assert hub.cfg.STARTER_TYPE == "flooded" and hub.cfg.STARTER_LOW_SOC == 90
assert not hubsim.run(hub.settings_set({"alerts": {"starter_type": "lithium"}}))["ok"]
assert settings.load(refresh=True)["alerts"]["starter_type"] == "flooded"
hubsim.run(hub.settings_set({"alerts": {"starter_soc": 50, "starter_type": "agm"}}))
ok("starter battery: AGM or flooded chart from Settings; a low warning at rest, held out of range, cleared 5% over or charging")

# ---- a mains charger on the starter battery ----------------------------------------------------
r = hubsim.run(hub.settings_set({"alerts": {"starter_charger": True}}))
assert r["ok"] and r["settings"]["alerts"]["starter_charger"] is True and cfg.STARTER_CHARGER
assert not hubsim.run(hub.settings_set({"alerts": {"starter_charger": "yes"}}))["ok"]
assert "mains_while_running" not in alerts_with(alt_a=0.82, alt_v=13.2)    # the CTEK, as measured
assert "mains_while_running" not in alerts_with(alt_a=5.0, alt_v=14.4)     # a charger in bulk
assert hub._engine_now is False
_gps = hub.gps_status
hub.gps_status = lambda: {"enabled": True, "fix": True, "speed_kn": 0.6}  # GPS jitter, parked
assert "mains_while_running" not in alerts_with(alt_a=2.0, alt_v=14.0)
hub.gps_status = lambda: {"enabled": True, "fix": True, "speed_kn": 12.0}  # driving off
assert "mains_while_running" in alerts_with(alt_a=2.0, alt_v=14.0)
assert cfg.STARTER_CHARGER is False
assert settings.load(refresh=True)["alerts"]["starter_charger"] is False
hub.gps_status = _gps
ok("starter on charge: the alternator line is not the engine; switched off (and saved) once the GPS sees the van move")

print("\nALL OK - %d checks" % len(passed))
