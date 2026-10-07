# Camperlux Pico W hub — polls BMS + Renogy + Victron over BLE and serves the
# dashboard over WiFi. Reuses the exact web pages in www/ (they fetch /api/data).

import asyncio
import machine
import network
import gc
import sys
import time
from time import ticks_ms, ticks_diff

import config as cfg
import settings
import ble_hub
import httpd
import weather
import stalls
import level
import gps

# Fine tier, RAM only, feeding the 1-hour trend. Measured at 11.8 s per row, so
# an hour needs about 305; 340 leaves headroom if the cycle shortens. Each row is
# a 5-tuple of small numbers, roughly 110 bytes, so this costs about 37 KB of the
# ~125 KB free.
HISTORY_MAX = 340
HIST_LONG_EVERY_S = 600      # long tier: one row per 10 minutes...
HIST_LONG_MAX = 1008         # ...kept for 7 days, on flash (survives reboots)
HIST_FILE = "hist.csv"
HIST_TRIM_AT = 1200          # rewrite the file once it grows past this
STATS_GAP_S = 180        # integrate across gaps up to this (a poll cycle is ~40 s)
RTC_VALID_AFTER = 1_700_000_000   # time.time() below this = clock never set
_boot_ticks = ticks_ms()
# MicroPython only collects when an allocation fails, so a long-running hub can
# fragment its heap and slow down over hours - the classic "fine after a reboot,
# worse by evening" pattern.  Collect once per poll cycle and keep the low-water
# mark so we can actually tell whether memory is trending down.
_wifi_fallback = False      # no known network joined, running on the hotspot
_wifi_ssid = ""             # the network we are actually on, for the UI
_wifi_next_try = 0          # ticks_ms deadline for the next join attempt
_wifi_fails = 0             # join attempts failed in a row, for the back-off
_mem_free = None
_mem_low = None
_poll_error = ""            # last exception from the poll cycle, for the UI
_poll_errors = 0

state = {
    "battery": {"connected": False},
    "renogy": {"connected": False},
    "victron": {"connected": False},
    "heater": {"connected": False},
    "victron_seen": 0,
    "heater_seen": 0,
    "battery_seen": 0,
    "renogy_seen": 0,
    "derived": {},
    "stats": {},
}

# Fine tier as fixed-size arrays rather than a list of tuples.
#
# It used to append a tuple and pop(0) every cycle. That reallocated the list,
# shifted every element and left a dead tuple behind each time - some 300 an
# hour, interleaved with the short-lived strings each HTTP request makes. The
# heap ends up free but fragmented, and an allocation of a couple of kilobytes
# fails while gc.mem_free() still reports plenty: exactly the "fine after a
# reboot, unresponsive by evening" failure. These are allocated once at boot and
# only ever overwritten, so the steady state costs nothing.
from array import array
_h_t = array("i", [0] * HISTORY_MAX)          # epoch seconds
_h_v = array("f", [0.0] * HISTORY_MAX)        # pack volts
_h_a = array("f", [0.0] * HISTORY_MAX)        # amps, signed
_h_s = array("h", [-1] * HISTORY_MAX)         # SOC %, -1 = unknown
_h_w = array("f", [0.0] * HISTORY_MAX)        # watts
_h_n = 0                 # how many slots hold real readings
_h_i = 0                 # next slot to write
_long_rows = 0
_last_long_t = 0


def _hist_boot():
    """Count rows already on flash and recover the last timestamp."""
    global _long_rows, _last_long_t
    n = last = 0
    try:
        with open(HIST_FILE) as f:
            for line in f:
                if line.strip():
                    n += 1
                    try:
                        last = int(line.split(",")[0])
                    except ValueError:
                        pass
    except OSError:
        pass
    _long_rows, _last_long_t = n, last
    print("history: %d rows on flash" % n)


def _hist_trim():
    """Keep only the newest HIST_LONG_MAX rows."""
    global _long_rows
    try:
        import os
        with open(HIST_FILE) as f:
            rows = [l for l in f if l.strip()][-HIST_LONG_MAX:]
        with open(HIST_FILE + ".tmp", "w") as f:
            for l in rows:
                f.write(l)
        try:
            os.remove(HIST_FILE)
        except OSError:
            pass
        os.rename(HIST_FILE + ".tmp", HIST_FILE)
        _long_rows = len(rows)
    except (OSError, MemoryError) as e:
        print("history trim failed:", e)


def _record_history(b):
    global _long_rows, _last_long_t, _h_n, _h_i
    if not b.get("connected") or b.get("voltage") is None:
        return
    t = int(time.time())
    row = (t, b["voltage"], b["current"], b.get("soc"), b.get("power_w"))
    _h_t[_h_i] = t
    _h_v[_h_i] = row[1]
    _h_a[_h_i] = row[2]
    _h_s[_h_i] = -1 if row[3] is None else int(row[3])
    _h_w[_h_i] = row[4] or 0
    _h_i = (_h_i + 1) % HISTORY_MAX
    if _h_n < HISTORY_MAX:
        _h_n += 1
    # long tier: needs a real clock, else the timeline would be nonsense
    if _rtc_ok() and t - _last_long_t >= HIST_LONG_EVERY_S:
        _last_long_t = t
        try:
            with open(HIST_FILE, "a") as f:
                f.write("%d,%.2f,%.2f,%s,%.1f\n" % (
                    t, row[1], row[2],
                    row[3] if row[3] is not None else "", row[4] or 0))
            _long_rows += 1
            if _long_rows > HIST_TRIM_AT:
                _hist_trim()
        except OSError as e:
            print("history write failed:", e)


# ---- auto-heat: frost protection driven by the DC-DC external temp probe ----
# The probe sits on the leisure battery (inside the van), so this works with no
# internet - unlike a forecast. Disarmed by default; it can never fire unless
# the user explicitly arms it.
AH_FILE = "autoheat.json"
_ah = {"armed": False,        # master switch - OFF until the user arms it
       "on_below": 5,         # start heating at/below this (C)
       "off_above": 9,        # stop once back up here (hysteresis)
       "target_c": 18,        # air temperature to ask the heater for
       "min_soc": 40,         # never start (and stop) below this battery %
       "max_run_min": 180,    # hard stop after this long
       "active": False,       # did WE turn it on?
       "started": 0,
       "last": ""}


def _ah_load():
    try:
        import json as _j
        with open(AH_FILE) as f:
            saved = _j.load(f)
        for k in ("armed", "on_below", "off_above", "target_c", "min_soc", "max_run_min"):
            if k in saved:
                _ah[k] = saved[k]
        print("autoheat: loaded (armed=%s)" % _ah["armed"])
    except (OSError, ValueError):
        pass


def _ah_save():
    try:
        import json as _j
        with open(AH_FILE, "w") as f:
            _j.dump({k: _ah[k] for k in
                     ("armed", "on_below", "off_above", "target_c", "min_soc", "max_run_min")}, f)
    except OSError as e:
        print("autoheat save failed:", e)


async def _ah_do(action, why):
    """Issue a heater command on behalf of the automation and record why."""
    res = await ble_hub.command_heater(action, _ah["target_c"], 2, 1)
    ok = res.get("ok")
    if ok:
        _ah["active"] = (action != "off")
        _ah["started"] = time.time() if _ah["active"] else 0
    _ah["last"] = "%s %s: %s" % (action, "ok" if ok else "FAILED", why)
    print("autoheat:", _ah["last"])
    return ok


async def _autoheat_check():
    if not _ah["armed"]:
        return
    r, b = state["renogy"], state["battery"]
    t = r.get("batt_temp_c") if r.get("connected") else None
    if t is None:
        return                                   # no probe reading - do nothing
    soc = b.get("soc") if b.get("connected") else None
    now = time.time()
    # --- safety stops first ---
    if _ah["active"] and _ah["started"] and (now - _ah["started"]) > _ah["max_run_min"] * 60:
        await _ah_do("off", "max run time (%d min) reached" % _ah["max_run_min"])
        return
    if _ah["active"] and soc is not None and soc < _ah["min_soc"]:
        await _ah_do("off", "battery %d%% below minimum %d%%" % (soc, _ah["min_soc"]))
        return
    # --- normal hysteresis ---
    if not _ah["active"] and t <= _ah["on_below"]:
        if soc is not None and soc < _ah["min_soc"]:
            _ah["last"] = "held off: battery %d%% below minimum %d%%" % (soc, _ah["min_soc"])
            return
        await _ah_do("air", "%dC at or below %dC" % (t, _ah["on_below"]))
    elif _ah["active"] and t >= _ah["off_above"]:
        await _ah_do("off", "%dC back above %dC" % (t, _ah["off_above"]))


async def autoheat_set(obj):
    """Apply settings from the dashboard; disarming also turns the heater off."""
    was = _ah["armed"]
    for k, lo, hi in (("on_below", -20, 25), ("off_above", -15, 30),
                      ("target_c", 5, 35), ("min_soc", 0, 100), ("max_run_min", 10, 720)):
        if k in obj:
            try:
                _ah[k] = max(lo, min(hi, int(obj[k])))
            except (TypeError, ValueError):
                pass
    if _ah["off_above"] <= _ah["on_below"]:
        _ah["off_above"] = _ah["on_below"] + 3      # keep a sane hysteresis gap
    if "armed" in obj:
        _ah["armed"] = bool(obj["armed"])
    _ah_save()
    if was and not _ah["armed"] and _ah["active"]:
        await _ah_do("off", "automation disarmed")
    if not _ah["armed"]:
        _ah["active"] = False
    return {"ok": True, "autoheat": _ah_status()}


def _ah_status():
    r = state["renogy"]
    return {"armed": _ah["armed"], "active": _ah["active"],
            "on_below": _ah["on_below"], "off_above": _ah["off_above"],
            "target_c": _ah["target_c"], "min_soc": _ah["min_soc"],
            "max_run_min": _ah["max_run_min"], "last": _ah["last"],
            "temp_c": r.get("batt_temp_c") if r.get("connected") else None,
            "run_min": int((time.time() - _ah["started"]) / 60) if _ah["active"] and _ah["started"] else 0}


# ---- lightweight stats (energy today, extremes, time estimates) ----------
_stats = {"wh_in": 0.0, "wh_out": 0.0, "ah_in": 0.0, "ah_out": 0.0,
          "ext": {"v": [None, None], "a": [None, None], "t": [None, None], "soc": [None, None]},
          "ema": None, "last_ms": None}


def _rtc_ok():
    return time.time() > RTC_VALID_AFTER


def _uptime_s():
    return ticks_diff(ticks_ms(), _boot_ticks) / 1000.0


def _track(key, val):
    lo, hi = _stats["ext"][key]
    _stats["ext"][key] = [val if lo is None else min(lo, val),
                          val if hi is None else max(hi, val)]


def _update_stats(b):
    if not b.get("connected") or b.get("voltage") is None:
        return
    now = ticks_ms()
    v, a = b["voltage"], b["current"]
    last = _stats["last_ms"]
    if last is not None:
        dt = ticks_diff(now, last) / 1000.0
        if 0 < dt <= STATS_GAP_S:
            h = dt / 3600.0
            w = v * a
            if a > 0:
                _stats["wh_in"] += w * h; _stats["ah_in"] += a * h
            else:
                _stats["wh_out"] += -w * h; _stats["ah_out"] += -a * h
            alpha = min(1.0, dt / 60.0)
            _stats["ema"] = a if _stats["ema"] is None else _stats["ema"] + alpha * (a - _stats["ema"])
        else:
            _stats["ema"] = a
    else:
        _stats["ema"] = a
    _stats["last_ms"] = now
    _track("v", v); _track("a", a)
    if b.get("soc") is not None:
        _track("soc", b["soc"])
    for t in b.get("temps_c") or []:
        _track("t", t)


def _stats_summary(b):
    tte = ttf = None
    ema = _stats["ema"]
    if ema is not None and b.get("residual_ah") is not None:
        if ema < -0.1:
            tte = b["residual_ah"] / -ema
        elif ema > 0.1 and b.get("nominal_ah"):
            ttf = (b["nominal_ah"] - b["residual_ah"]) / ema
    return {
        # absolute boot time only if the clock is real; the page shows "since boot" otherwise
        "since": (time.time() - _uptime_s()) if _rtc_ok() else None,
        "uptime_s": round(_uptime_s()),
        "wifi_mode": "ap" if (cfg.WIFI_MODE == "ap" or _wifi_fallback) else "sta",
        "wifi_fallback": _wifi_fallback,
        "wifi_ssid": _wifi_ssid,
        # ask the interface, don't just report the setting - the two can differ
        "wifi_ap_on": _ap_active(),
        "wifi_ap_ip": _ap_ip(),
        "wifi_ap_ssid": cfg.AP_SSID,
        "wifi_ap_clash": _net_clash(),
        "wifi_ap_suppressed": bool(_net_clash() and not _ap_active()),
        "i2c": [hex(a) for a in _i2c_devices],
        "poll_error": _poll_error,
        "poll_errors": _poll_errors,
        "mem_free": _mem_free,
        "mem_free_low": _mem_low,
        "wh_in": round(_stats["wh_in"], 1), "wh_out": round(_stats["wh_out"], 1),
        "ah_in": round(_stats["ah_in"], 2), "ah_out": round(_stats["ah_out"], 2),
        "ext": _stats["ext"],
        "avg_current": None if ema is None else round(ema, 2),
        "time_to_empty_h": None if tte is None else round(tte, 2),
        "time_to_full_h": None if ttf is None else round(ttf, 2),
    }



# ---- local time ------------------------------------------------------------
# The RTC keeps UTC. Midnight for "energy today" and 07:00 for a heater timer
# are local times, so add the configured offset and the EU summer-time hour.
def _local_now():
    t = time.time()
    off = getattr(cfg, "UTC_OFFSET_MIN", 0) * 60
    if getattr(cfg, "DST_EU", True):
        y = time.gmtime(t)[0]

        def last_sunday(m):             # 01:00 UTC on the last Sunday of month m
            d = time.mktime((y, m, 31, 1, 0, 0, 0, 0))
            return d - ((time.gmtime(d)[6] + 1) % 7) * 86400

        if last_sunday(3) <= t < last_sunday(10):
            off += 3600
    return time.gmtime(t + off)


def _local_day(lt):
    return "%04d-%02d-%02d" % (lt[0], lt[1], lt[2])


# ---- energy today ----------------------------------------------------------
# Watt-hours from each source since local midnight: solar and alternator are
# the DC-DC's input sides, mains is the mains charger's output, load is the
# derived house load. Saved every ten minutes so a reboot does not lose the day.
TODAY_FILE = "today.json"
_today = {"day": "", "solar": 0.0, "alt": 0.0, "mains": 0.0, "load": 0.0}
_today_ms = None
_today_saved = None      # ticks_ms of the last save; None until the first tick


def _today_load():
    try:
        import json as _j
        with open(TODAY_FILE) as f:
            d = _j.load(f)
        if isinstance(d, dict) and d.get("day"):
            for k in _today:
                if k in d:
                    _today[k] = d[k]
    except (OSError, ValueError):
        pass


def _today_tick():
    global _today_ms, _today_saved
    if not _rtc_ok():
        return
    day = _local_day(_local_now())
    if _today["day"] != day:
        _today.update({"day": day, "solar": 0.0, "alt": 0.0, "mains": 0.0, "load": 0.0})
    now = ticks_ms()
    if _today_ms is not None:
        # a long gap (a reboot, a stuck poll) is not counted as a minute of it
        h = min(ticks_diff(now, _today_ms), 60000) / 3600000.0
        r = state["renogy"]
        v = _snapshot("victron", "victron_seen", cfg.VICTRON_STALE_MS)
        if r.get("connected"):
            _today["solar"] += (r.get("solar_w") or 0) * h
            _today["alt"] += (r.get("alt_w") or 0) * h
        if v.get("connected"):
            _today["mains"] += (v.get("power_w") or 0) * h
        _today["load"] += ((state["derived"] or {}).get("load_w") or 0) * h
    _today_ms = now
    if _today_saved is None:
        _today_saved = now
    elif ticks_diff(now, _today_saved) > 600000:
        _today_saved = now
        try:
            import json as _j
            with open(TODAY_FILE, "w") as f:
                _j.dump(_today, f)
        except OSError:
            pass


def _today_status():
    return {"day": _today["day"], "solar": round(_today["solar"]),
            "alt": round(_today["alt"]), "mains": round(_today["mains"]),
            "load": round(_today["load"])}


# ---- heater timers ---------------------------------------------------------
# Two fixed timers, set from the van display: a morning warm-up (air) and
# morning hot water. Each starts the heater at its time with the auto fuel
# choice, then turns it off after its run time, so nothing is left running all
# day. The hub talks to the heater only at a time the user chose. A timer is
# skipped if the battery is below frost protection's minimum.
TIMER_FILE = "timers.json"
_timers = [
    {"id": "warm", "on": False, "at": "07:00", "action": "air", "temp": 20, "water": 2,
     "run_min": 60},
    {"id": "water", "on": False, "at": "06:45", "action": "water", "temp": 20, "water": 2,
     "run_min": 45},
]
_tm = {}                 # id -> {"fired": unix time, "last": text}
_tm_session = {"until": 0, "action": None, "ids": []}   # the heater run the timers own


def _tm_state(tid):
    return _tm.setdefault(tid, {"fired": 0, "last": ""})


def _timers_load():
    try:
        import json as _j
        with open(TIMER_FILE) as f:
            saved = _j.load(f)
        for t in _timers:
            for s in saved if isinstance(saved, list) else []:
                if isinstance(s, dict) and s.get("id") == t["id"]:
                    ok = _timer_clean(s, t)
                    if ok:
                        t.update(ok)
    except (OSError, ValueError):
        pass


def _timer_clean(s, cur):
    """A validated copy of timer cur with s applied, or None if s is bad."""
    t = dict(cur)
    try:
        if "on" in s:
            t["on"] = bool(s["on"])
        if "at" in s:
            hh, mm = str(s["at"]).split(":")
            hh, mm = int(hh), int(mm)
            if not (0 <= hh < 24 and 0 <= mm < 60):
                return None
            t["at"] = "%02d:%02d" % (hh, mm)
        for k, lo, hi in (("temp", 5, 35), ("water", 1, 3), ("run_min", 15, 240)):
            if k in s:
                t[k] = max(lo, min(hi, int(s[k])))
    except (TypeError, ValueError):
        return None
    return t


def _timers_status():
    out = []
    for t in _timers:
        d = dict(t)
        d["running"] = t["id"] in _tm_session["ids"]
        d["last"] = _tm_state(t["id"])["last"]
        out.append(d)
    return out


async def timers_set(obj):
    new = []
    for t in _timers:
        for s in obj.get("timers") or []:
            if isinstance(s, dict) and s.get("id") == t["id"]:
                c = _timer_clean(s, t)
                if c is None:
                    return {"ok": False, "error": "bad timer setting for %s" % t["id"]}
                t = c
        new.append(t)
    lt = _local_now() if _rtc_ok() else None
    for i, t in enumerate(new):
        old = _timers[i]
        # Switched on, or moved, to a time only just gone: that is for
        # tomorrow, not "start now" - so count it as done for today.
        if lt and t["on"] and (not old["on"] or old["at"] != t["at"]):
            hh, mm = t["at"].split(":")
            if (lt[3] * 60 + lt[4] - (int(hh) * 60 + int(mm))) % 1440 < 5:
                _tm_state(t["id"])["fired"] = time.time()
        _timers[i] = t
    try:
        import json as _j
        with open(TIMER_FILE, "w") as f:
            _j.dump(_timers, f)
    except OSError as e:
        return {"ok": False, "error": "could not save: %s" % e}
    return {"ok": True, "timers": _timers_status()}


async def _timers_check():
    """Run the timers as ONE heater session. The heater is a single device, so
    two timers that overlap share it: the later start adds its heating (air on
    top of water becomes water + air) and the session ends at the later of the
    two finish times. Previously each timer switched the heater off at its own
    end, so the hot-water timer cut the morning warm-up short."""
    if not _rtc_ok():
        return
    lt = _local_now()
    now, now_min = time.time(), lt[3] * 60 + lt[4]
    s = _tm_session
    if s["until"] and now >= s["until"]:
        res = await heater_cmd({"action": "off"})
        for tid in s["ids"]:
            _tm_state(tid)["last"] = "Finished" if res.get("ok") else "FAILED to stop"
        s.update({"until": 0, "action": None, "ids": []})
    wake = _wake_timer()
    for t in _timers + ([wake] if wake else []):
        st = _tm_state(t["id"])
        if not t["on"] or t["id"] in s["ids"]:
            continue
        days = t.get("days", "every")
        if days != "every" and (days == "weekdays") != ((lt[6] + t.get("shift", 0)) % 7 < 5):
            continue
        hh, mm = t["at"].split(":")
        # a five-minute window (modulo a day, so 23:58 still fires after
        # midnight), so a poll cycle or a reboot at 07:00 is not a miss; never
        # twice within ten minutes, whatever the day
        if (now_min - (int(hh) * 60 + int(mm))) % 1440 >= 5 or now - st["fired"] < 600:
            continue
        st["fired"] = now
        b = state["battery"]
        soc = b.get("soc") if b.get("connected") else None
        if soc is not None and soc < _ah["min_soc"]:
            st["last"] = "Skipped %s: battery %d%%" % (t["at"], soc)
            continue
        action = t["action"]
        if s["action"] and s["action"] != action:
            action = "combi"                    # air and water together
        fan = (state["heater"] or {}).get("fan_level") or 1
        res = await heater_cmd({"action": action, "temp": t["temp"], "water": t["water"],
                                "level": fan if 1 <= fan <= 4 else 1, "energy": "auto"})
        if res.get("ok"):
            s["action"] = action
            s["until"] = max(s["until"], now + t["run_min"] * 60)
            s["ids"].append(t["id"])
            st["last"] = ("Warming for the %s alarm" % _disp["alarm_clock"]["at"]
                          if t["id"] == "wake" else "Started at %s" % t["at"])
        else:
            st["last"] = "FAILED at %s: %s" % (t["at"], res.get("error", "no reply"))
        print("timer %s: %s" % (t["id"], st["last"]))


# ---- the van display's own settings, kept here so every screen shares them --
# The ambient light, the alarm clock, the kitchen timer, the display's
# brightness and night mode, and the Drive list's ticks. The display used to
# keep these to itself; kept here, a phone can set them too and both stay in
# step. "rev" goes up on every change: the display sees it in /api/alerts, which
# it asks every 2 s, and fetches the rest only when it moves.
DISP_FILE = "display.json"
_disp = {"rev": 0,
         "ambient": {"on": False, "h": 30, "s": 80, "bright": 50, "cycle": False,
                     "warm": False},
         # warm: minutes before the alarm to start warming the van (0 = no)
         "alarm_clock": {"on": False, "at": "07:00", "days": "every", "warm": 0},
         # end: unix time it runs out while running; paused: seconds left
         "kitchen": {"end": 0, "paused": 0, "set": 300},
         "brightness": 1.0, "night": "auto", "ticks": [],
         # dim: dim when idle by day; offmode: dark whenever idle, until morning
         "dim": True, "offmode": False,
         # status_led: the display's gold "all is well" light (alarms flash it regardless)
         "status_led": True,
         # the Switches page: on screen only until relays are wired, but kept
         # here so the display and every phone show the same
         "switches": [False] * 6,
         # "Update the display now": the display looks for an update when this changes
         "ota_req": 0}


def _hhmm(v):
    hh, mm = str(v).split(":")
    hh, mm = int(hh), int(mm)
    if not (0 <= hh < 24 and 0 <= mm < 60):
        raise ValueError("time out of range")
    return "%02d:%02d" % (hh, mm)


def _clip(v, lo, hi):
    return max(lo, min(hi, int(v)))


def _disp_clean(obj):
    """A copy of the shared settings with the fields in obj applied. Raises
    ValueError or TypeError on a bad one, so a bad request changes nothing."""
    d = dict(_disp)
    a = obj.get("ambient")
    if isinstance(a, dict):
        n = dict(d["ambient"])
        for k in ("on", "cycle", "warm"):
            if k in a:
                n[k] = bool(a[k])
        if "h" in a:
            n["h"] = int(a["h"]) % 360
        if "s" in a:
            n["s"] = _clip(a["s"], 0, 100)
        if "bright" in a:
            n["bright"] = _clip(a["bright"], 10, 100)
        d["ambient"] = n
    ac = obj.get("alarm_clock")
    if isinstance(ac, dict):
        n = dict(d["alarm_clock"])
        if "on" in ac:
            n["on"] = bool(ac["on"])
        if "at" in ac:
            n["at"] = _hhmm(ac["at"])
        if ac.get("days") in ("every", "weekdays", "weekends"):
            n["days"] = ac["days"]
        if "warm" in ac:
            n["warm"] = _clip(ac["warm"], 0, 120)
        d["alarm_clock"] = n
    k = obj.get("kitchen")
    if isinstance(k, dict):
        n = dict(d["kitchen"])
        if "left" in k:                      # start: seconds from now, on the hub's clock
            n["end"] = int(time.time()) + _clip(k["left"], 1, 5999)
            n["paused"] = 0
        if "end" in k:
            n["end"] = max(0, int(k["end"] or 0))
        if "paused" in k:
            n["paused"] = _clip(k["paused"] or 0, 0, 5999)
        if "set" in k:
            n["set"] = _clip(k["set"], 10, 5999)
        d["kitchen"] = n
    if "brightness" in obj:
        d["brightness"] = max(0.1, min(1.0, float(obj["brightness"])))
    if obj.get("night") in ("auto", "on", "off"):
        d["night"] = obj["night"]
    for k in ("dim", "offmode", "status_led"):
        if k in obj:
            d[k] = bool(obj[k])
    if isinstance(obj.get("ticks"), list):
        d["ticks"] = [str(t)[:48] for t in obj["ticks"][:20]]
    if "ota_req" in obj:
        d["ota_req"] = int(obj["ota_req"] or 0)
    sw = obj.get("switches")
    if isinstance(sw, list):
        d["switches"] = ([bool(x) for x in sw] + [False] * 6)[:6]
    return d


def _disp_save():
    try:
        import json as _j
        with open(DISP_FILE, "w") as f:
            _j.dump(_disp, f)
    except OSError as e:
        print("display settings not saved:", e)


def _disp_load():
    global _disp
    try:
        import json as _j
        with open(DISP_FILE) as f:
            saved = _j.load(f)
        d = _disp_clean(saved)
        d["rev"] = int(saved.get("rev") or 0)
        _disp = d
    except (OSError, ValueError, TypeError, AttributeError):
        pass


def display_get(_obj=None):
    return {"ok": True, "display": _disp, "wake": _wake_status()}


async def display_set(obj):
    global _disp
    # the switches drive relays: they change through /api/switches, behind the
    # password, not through this open path
    obj.pop("switches", None)
    try:
        d = _disp_clean(obj)
    except (TypeError, ValueError, KeyError) as e:
        return {"ok": False, "error": "bad setting: %s" % e}
    d["rev"] = _disp["rev"] + 1
    _disp = d
    _disp_save()
    return display_get()


# ---- the relays -------------------------------------------------------------------
# The Switches page's six switches, on the Waveshare RP2350-Relay-6CH-W's relays
# (GPIO 26-31, high = on). Found by the board's name; RELAY_PINS in config.py
# sets them for other wiring, or () for none. A board without relays - the
# Pico W - touches no pins: the switches stay on screen only.
_relays = []


def _relays_init():
    global _relays
    pins = getattr(cfg, "RELAY_PINS", None)
    if pins is None:
        import sys
        pins = range(26, 32) if "Relay-6CH" in getattr(sys.implementation, "_machine", "") else ()
    from machine import Pin
    # as they were: a restart must not switch the fridge off
    _relays = [Pin(n, Pin.OUT, value=1 if on else 0)
               for n, on in zip(pins, _disp["switches"])]
    if _relays:
        print("relays: %d, on: %s" % (len(_relays), [i + 1 for i, r in enumerate(_relays) if r.value()]))


def _relays_apply():
    for r, on in zip(_relays, _disp["switches"]):
        r.value(1 if on else 0)


async def switches_set(obj):
    """{"i": 0-5, "on": true/false} for one switch, or {"switches": [six]}."""
    global _disp
    sw = list(_disp["switches"])
    try:
        if isinstance(obj.get("switches"), list):
            sw = obj["switches"]
        else:
            sw[int(obj["i"])] = bool(obj["on"])
        d = _disp_clean({"switches": sw})
    except (TypeError, ValueError, KeyError, IndexError) as e:
        return {"ok": False, "error": "bad switch: %s" % e}
    d["rev"] = _disp["rev"] + 1
    _disp = d
    _relays_apply()                        # the relay first, then the flash write
    _disp_save()
    return display_get()


# ---- the van display's updates over the air ------------------------------------------
# tools/publish_display.py puts the display's software in dispfw/; the display
# fetches it (display/ota.py) and says which version it has when it asks.
_dispfw = {"has": None, "at": None}


def dispfw_seen(v):
    _dispfw["has"] = v
    _dispfw["at"] = time.time()


def dispfw_status():
    """{"hub": version the hub carries, "has": the display's, "seen_s": ago}."""
    out = {"hub": None, "has": _dispfw["has"],
           "seen_s": int(time.time() - _dispfw["at"]) if _dispfw["at"] else None}
    try:
        import os
        st = os.stat("dispfw/manifest.json")
        key = (st[6], st[8])
        if _dispfw.get("key") != key:          # read it again only once it changes
            import json as _j
            with open("dispfw/manifest.json") as f:
                _dispfw["hub"] = _j.loads(_j.load(f)["body"])["version"]
            _dispfw["key"] = key
        out["hub"] = _dispfw.get("hub")
    except (OSError, ValueError, KeyError):
        pass
    return out


def _indicator_state():
    """For the hub's own buzzer and light (indicate.py)."""
    un = [a for a in _alert_active if not a.get("acked")]
    return {"panic": _panic["on"],
            "danger": any(a["level"] == "danger" and a["id"] != "panic" for a in un),
            "warn": any(a["level"] == "warn" for a in un),
            "cleared": bool(_alert_active) and not un,
            "fallback": _wifi_fallback,
            "status_led": _disp.get("status_led", True),
            "sound": bool(cfg.ALERT_SOUND)}


def _wake_timer():
    """The alarm clock's warm-up, as a timer like the two fixed ones: the heater
    on (air) its "warm" minutes before the alarm, at the morning warm-up's
    temperature, running until half an hour after the alarm. None when the
    alarm clock is off or not linked."""
    ac = _disp["alarm_clock"]
    lead = ac.get("warm") or 0
    if not (ac.get("on") and lead):
        return None
    hh, mm = ac["at"].split(":")
    am = int(hh) * 60 + int(mm)
    sm = (am - lead) % 1440
    w = _timers[0]
    for t in _timers:
        if t["id"] == "warm":
            w = t
    # "shift": a warm-up starting before midnight is for tomorrow's alarm, so
    # its weekdays/weekends test uses tomorrow's day
    return {"id": "wake", "on": True, "at": "%02d:%02d" % (sm // 60, sm % 60),
            "action": "air", "temp": w["temp"], "water": w["water"],
            "run_min": lead + 30, "days": ac.get("days", "every"),
            "shift": 1 if sm > am else 0}


def _wake_status():
    t = _wake_timer()
    return {"at": t["at"] if t else None, "running": "wake" in _tm_session["ids"],
            "last": _tm_state("wake")["last"]}


# ---- the engine, as the alerts judge it ------------------------------------
_engine_now = False
_engine_off_at = None       # ticks_ms the engine stopped (or the hub started)
_ticks_reset = False        # the Drive list has been cleared for this stop


def _engine_note(engine):
    """Remember the engine's state, and empty the Drive list once the van has
    been parked a while, so each departure starts from nothing ticked. Once per
    stop: ticks made while parked, after that, are kept for the next drive."""
    global _engine_now, _engine_off_at, _ticks_reset
    if engine:
        _engine_off_at = None
        _ticks_reset = False
    elif _engine_now or _engine_off_at is None:
        _engine_off_at = ticks_ms()
    _engine_now = engine
    if (not engine and not _ticks_reset and _engine_off_at is not None
            and ticks_diff(ticks_ms(), _engine_off_at)
            > getattr(cfg, "CHECKLIST_RESET_MIN", 30) * 60000):
        _ticks_reset = True
        if _disp["ticks"]:
            _disp["ticks"] = []
            _disp["rev"] += 1
            _disp_save()


# ---- the starter battery's charge, from its voltage at rest -------------------
# A 12 V sealed (AGM) lead-acid battery - what a stop-start Crafter has - read
# against its resting voltage, from Nature's Generator's chart (sealed column):
# https://naturesgenerator.com/blogs/news/lead-acid-battery-voltage-chart
# Only a battery at rest tells its charge: the alternator holds it near 14 V,
# the DC-DC's trickle from the hook-up near 13.2 V, and after a drive its
# surface charge reads high for a while. So: 13.0 V or more, or the engine on,
# is "charging" with no figure; within STARTER_SETTLE_MIN of the engine
# stopping (or of the hub starting) the figure is marked as settling.
STARTER_CHART = ((11.63, 0), (11.70, 10), (11.81, 20), (11.96, 30), (12.11, 40), (12.23, 50),
                 (12.41, 60), (12.51, 70), (12.65, 80), (12.78, 90), (12.89, 100))
STARTER_CHARGING_V = 13.0
STARTER_SETTLE_MIN = 30


def starter_soc(v):
    """Percent from a resting voltage, straight lines between the chart's steps."""
    if v is None:
        return None
    lo, hi = STARTER_CHART[0], STARTER_CHART[-1]
    if v <= lo[0]:
        return 0
    if v >= hi[0]:
        return 100
    for (v0, p0), (v1, p1) in zip(STARTER_CHART, STARTER_CHART[1:]):
        if v <= v1:
            return round(p0 + (p1 - p0) * (v - v0) / (v1 - v0))
    return 100


def _starter_status(r):
    """{"state": "offline" | "charging" | "settling" | "rest", "v", "soc"} -
    the voltage is the DC-DC's input side, which is the starter battery."""
    v = r.get("alt_v") if r.get("connected") else None
    if v is None:
        return {"state": "offline", "v": None, "soc": None}
    if _engine_now or v >= STARTER_CHARGING_V:
        return {"state": "charging", "v": round(v, 2), "soc": None}
    rested = (_engine_off_at is not None and ticks_diff(ticks_ms(), _engine_off_at)
              >= STARTER_SETTLE_MIN * 60000)
    return {"state": "rest" if rested else "settling", "v": round(v, 2), "soc": starter_soc(v)}


# ---- guard -----------------------------------------------------------------
# Armed when the van is left. After a minute to get out and lock up, it takes
# the van's tilt (and position, if the GPS has a fix) as normal; the van being
# jacked, lifted or towed, the engine starting, or the van moving then sets
# off panic - the display's beacon and siren, and the alarm on every screen.
# Disarming stops a panic it set off. After a panic is stopped it looks again
# from wherever the van then sits.
GUARD_FILE = "guard.json"
GUARD_ARM_S = 60
GUARD_MOVE_M = 60           # GPS wanders 10-20 m; a van driven away does not
_guard = {"on": False, "state": "off", "since": 0, "base": None, "pos": None,
          "n": 0, "last": ""}


def _guard_status():
    g = _guard
    left = 0
    if g["state"] == "arming":
        left = max(0, GUARD_ARM_S - ticks_diff(ticks_ms(), g["since"]) // 1000)
    return {"on": g["on"], "state": g["state"], "arming_s": left, "last": g["last"],
            "tilt_deg": getattr(cfg, "GUARD_TILT_DEG", 1.5), "gps": bool(g["pos"])}


def _guard_save():
    try:
        with open(GUARD_FILE, "w") as f:
            f.write("1" if _guard["on"] else "0")
    except OSError:
        pass


def _guard_load():
    """A hub that restarts while guarding - a power cut - guards again."""
    try:
        with open(GUARD_FILE) as f:
            if f.read().strip() == "1":
                _guard.update({"on": True, "state": "arming", "since": ticks_ms()})
    except OSError:
        pass


def _guard_arm():
    _guard.update({"state": "arming", "since": ticks_ms(), "base": None, "pos": None,
                   "n": 0})


async def guard_set(obj):
    g = _guard
    on = bool(obj.get("on"))
    if on and not g["on"]:
        if _engine_now:
            return {"ok": False, "error": "the engine is running",
                    "guard": _guard_status()}
        g["on"] = True
        g["last"] = ""
        _guard_arm()
    elif not on and g["on"]:
        g.update({"on": False, "state": "off", "base": None, "pos": None})
        if _panic["on"] and _panic["by"] == "guard":
            await panic_set({"on": False})
    _guard_save()
    return {"ok": True, "guard": _guard_status()}


def _dist_m(a, b):
    """Metres between two (lat, lon). Either may be strings: the Settings
    location is stored as text, and once broke the forecast's moved-check."""
    import math
    a = (float(a[0]), float(a[1]))
    b = (float(b[0]), float(b[1]))
    dy = (a[0] - b[0]) * 111320
    dx = (a[1] - b[1]) * 111320 * math.cos(math.radians(a[0]))
    return (dx * dx + dy * dy) ** 0.5


async def _guard_check(lv=None):
    """lv: a fresh tilt reading, from the level loop twice a second; None from
    the poll loop, which checks the engine and the GPS."""
    g = _guard
    if not g["on"]:
        return
    if g["state"] == "tripped":
        if not _panic["on"]:
            _guard_arm()                 # stopped: settle again, then keep watching
        return
    if g["state"] == "arming":
        if ticks_diff(ticks_ms(), g["since"]) < GUARD_ARM_S * 1000:
            return
        if cfg.LEVEL_ENABLED and lv is None:
            return                       # the level loop takes the tilt to compare with
        if lv is not None and lv.get("ok") and lv.get("roll") is not None:
            g["base"] = [lv["roll"], lv["pitch"]]
        fx = gps_status()
        if fx.get("fix"):
            g["pos"] = (fx["lat"], fx["lon"])
        g["state"], g["n"] = "armed", 0
        print("guard: armed", g["base"], g["pos"])
        return
    why = None
    if lv is not None:
        if g["base"] and lv.get("ok") and lv.get("roll") is not None:
            tilt = getattr(cfg, "GUARD_TILT_DEG", 1.5)
            b = g["base"]
            d = max(abs(lv["roll"] - b[0]), abs(lv["pitch"] - b[1]))
            if d >= tilt:
                g["n"] += 1
                if g["n"] >= 4:          # two seconds of it: not a gust of wind
                    why = "the van tilted %.1f degrees" % d
            else:
                g["n"] = 0
                if d < tilt / 2:
                    # follow the sensor's slow drift with temperature - about a
                    # degree over a day - but not a van being jacked in seconds
                    b[0] += (lv["roll"] - b[0]) * 0.002
                    b[1] += (lv["pitch"] - b[1]) * 0.002
    else:
        if _engine_now:
            why = "the engine started"
        elif g["pos"]:
            fx = gps_status()
            if fx.get("fix"):
                m = _dist_m(g["pos"], (fx["lat"], fx["lon"]))
                if m > GUARD_MOVE_M:
                    why = "the van moved %d m" % m
    if why:
        g["state"] = "tripped"
        at = (" at %02d:%02d" % tuple(_local_now()[3:5])) if _rtc_ok() else ""
        g["last"] = "Set off%s: %s" % (at, why)
        print("guard:", g["last"])
        await panic_set({"on": True, "by": "guard",
                         "why": "Guard: %s. Stop it on any screen." % why})


# ---- battery alerts ----------------------------------------------------------
_low_on = False          # low-battery latch, so it does not flicker at the limit


def _battery_alerts(b):
    """Low battery and battery health, as {alert id: detail text}."""
    global _low_on
    out = {}
    if not b.get("connected"):
        return out
    soc = b.get("soc")
    lim = int(getattr(cfg, "LOW_SOC", 20))
    if soc is not None:
        if soc < lim:
            _low_on = True
        elif soc >= lim + 3:
            _low_on = False
        if _low_on:
            out["low_battery"] = ("The leisure battery is down to %d%%. Charge it soon - plug "
                                  "in, run the engine or cut the loads." % soc)
    probs = []
    spread = b.get("cell_delta_mv")
    # Cells drift apart near full and near empty as a matter of course; in the
    # middle they should agree, so only there is a big spread a warning sign.
    if (spread is not None and soc is not None and 10 <= soc <= 95
            and spread > getattr(cfg, "CELL_SPREAD_MV", 150)):
        probs.append("cells %d mV apart" % spread)
    # "MOSFET locked" is reported whenever the charge switch is off - storage
    # mode does that on purpose - so it is not a fault.
    faults = [f for f in (b.get("faults") or []) if "locked" not in f]
    if faults:
        probs.append("BMS protection: " + ", ".join(faults))
    temps = b.get("temps_c") or []
    if temps:
        if max(temps) >= getattr(cfg, "BATT_HOT_C", 50):
            probs.append("battery at %d C" % max(temps))
        if min(temps) <= 1 and (b.get("current") or 0) > 0.5:
            probs.append("charging below freezing (%d C)" % min(temps))
    if probs:
        out["battery_health"] = "Battery needs attention: " + "; ".join(probs) + "."
    return out


# ---- safety alerts --------------------------------------------------------
# Driving off still plugged into the mains rips the hook-up lead out, and at
# worst takes the bollard or the van's inlet with it.  The alternator supplying
# current means the engine is running; the Victron reporting means the lead is
# still connected.  We cannot detect movement (there is no accelerometer on the
# hub), so "engine running while plugged in" is the earliest warning available -
# it fires while you are still sitting there, before you pull away.
_alert_hold = 0          # consecutive cycles the condition has been true
_alert_active = []       # what _alert_tick() last decided, served to callers
# Raised on the first reading. It was held for two cycles (~20 s) against a
# single odd reading, but a warning that arrives after the van has moved off is
# no warning at all; the 13.8 V bar below is what keeps it honest instead.
_ALERT_CYCLES = 1
# Alerts someone has cleared. Cleared from any screen, cleared on all of them:
# the display, phones and the web pages all ask the hub. Forgotten as soon as
# the condition goes away, so the next occurrence sounds again.
_alert_ack = set()
# A test alarm, raised from the Settings page to check that every screen and
# phone sounds without having to start the engine on a hook-up. Clears itself.
_alert_test_until = None
# Panic: set off from the van display, a phone or a web page, and stopped from
# any of them. It is an alert like the others, so every screen shows and sounds
# it; the display adds its beacon and siren. Stops by itself after
# PANIC_MAX_S, so an empty van does not wail all night.
_panic = {"on": False, "since": 0, "by": "", "why": ""}
PANIC_MAX_S = 1800


def _panic_alert():
    return {"id": "panic", "level": "danger", "title": "PANIC",
            "detail": _panic["why"] or ("Panic alarm set off from %s. Stop it on any "
                                        "screen." % (_panic["by"] or "the van"))}


async def panic_set(obj):
    """{"on": true/false, "by": "display" | "phone" | ...}"""
    global _alert_active
    on = bool(obj.get("on"))
    if on and not _panic["on"]:
        _panic.update({"on": True, "since": time.time(),
                       "by": str(obj.get("by") or "")[:20],
                       "why": str(obj.get("why") or "")[:100]})
        print("PANIC set off by", _panic["by"] or "?")
    elif not on and _panic["on"]:
        _panic["on"] = False
        print("panic stopped")
    # straight into the alert list, not on the next poll cycle
    _alert_active = [a for a in _alert_active if a["id"] != "panic"]
    if _panic["on"]:
        _alert_active = [_panic_alert()] + _alert_active
    _alert_mark()
    return {"ok": True, "panic": _panic["on"], "alerts": _alert_active}


_ALERT_TEST = {"id": "test", "level": "danger", "title": "Test alarm",
               "detail": "This is a test of the van's alarm. Clear it here, or it "
                         "stops by itself within a minute."}
# Alternator current is the certain sign of a running engine, but it only flows
# while the DC-DC is actually drawing - with the leisure battery full it sits at
# zero with the engine running, which is exactly when someone drives off.
# Voltage is the fallback, and it has to clear a high bar: measured on this van,
# the DC-DC trickle-charges the starter from the hook-up and held the line at
# 13.2 V with the engine OFF, so the display threshold of 13.6 V is nowhere near
# safe enough for an alarm.
_ALERT_ENGINE_V = 13.8

# Every alert is described here rather than in the web page, so the Settings
# page documents exactly what the hub actually checks and the two can never drift
# apart. "why" is shown to the user; keep it plain and specific.
_ALERT_DEFS = [
    {
        "id": "mains_while_running",
        "name": "Driving off still plugged in",
        "level": "danger",
        "title": "Still plugged in",
        "detail": "The engine is running and the mains hook-up is still connected. "
                  "Unplug the lead before moving the van.",
        "why": "Raised when the engine appears to be running while the mains hook-up is "
               "still connected, from the first reading that shows it - there is no "
               "hold-off. The engine is judged from the alternator: any charging "
               "current, or the alternator line at %.1f V or above. Parked on hook-up "
               "your DC-DC trickle-charges the starter and holds that line near 13.2 V, "
               "which is why the bar is set well above it." % _ALERT_ENGINE_V,
        "limits": "It cannot detect movement, so it fires while you are still stationary "
                  "with the engine on - deliberately early. It only warns someone with a "
                  "page open; a buzzer wired to the hub would not have that limitation.",
    },
    {
        "id": "low_battery",
        "name": "Low battery",
        "level": "warn",
        "title": "Battery low",
        "detail": "The leisure battery is low.",
        "why": "Raised when the battery's charge falls below the limit set below, and cleared "
               "once it is 3% above it again, so it does not flicker on and off at the limit.",
        "limits": "Only while the battery monitor is in range.",
    },
    {
        "id": "battery_health",
        "name": "Battery health",
        "level": "warn",
        "title": "Battery needs attention",
        "detail": "The battery monitor reports a problem.",
        "why": "Raised when the battery monitor reports a protection trip, a cell more than "
               "150 mV from the others while the battery is between 10% and 95% (cells "
               "spread apart near full and near empty anyway), the battery at 50 C or more, "
               "or charging with the battery at 1 C or below, which damages lithium cells.",
        "limits": "Only while the battery monitor is in range.",
    },
]


def _alert_defs():
    off = cfg.ALERTS_OFF
    return [{"id": a["id"], "name": a["name"], "why": a["why"],
             "limits": a.get("limits", ""), "enabled": a["id"] not in off}
            for a in _ALERT_DEFS]


def _alert_tick():
    """Re-evaluate the alerts. Called once per poll cycle, never from a request.

    The debounce counts poll cycles, so it has to advance on the poll loop's
    clock. Driving it from api_data() instead made it count HTTP requests: with
    a dashboard open it cleared a "20 second" hold in about four, faster with
    several devices watching, and never advanced at all with no page open.
    """
    global _alert_hold, _alert_active
    r = state["renogy"]
    v = _snapshot("victron", "victron_seen", cfg.VICTRON_STALE_MS)
    ren_ok = bool(r.get("connected"))
    engine = ren_ok and ((r.get("alt_a") or 0) > 0.05
                         or (r.get("alt_v") or 0) >= _ALERT_ENGINE_V)
    mains = bool(v.get("connected"))
    _engine_note(engine)
    if engine and mains:
        _alert_hold += 1
    else:
        _alert_hold = 0
    # Each definition carries its own condition, so adding one is a matter of
    # adding an entry - the dispatch no longer names a single id.
    conditions = {"mains_while_running": engine and mains}
    batt = _battery_alerts(state["battery"])
    for k in batt:
        conditions[k] = True
    out = []
    for a in _ALERT_DEFS:
        if a["id"] in cfg.ALERTS_OFF:
            continue
        if not conditions.get(a["id"]):
            continue
        if a["id"] == "mains_while_running" and _alert_hold < _ALERT_CYCLES:
            continue
        out.append({"id": a["id"], "level": a["level"],
                    "title": a["title"], "detail": batt.get(a["id"], a["detail"])})
    if _alert_test_on():
        out.append(dict(_ALERT_TEST))
    if _panic["on"] and time.time() - _panic["since"] > PANIC_MAX_S:
        _panic["on"] = False
        print("panic stopped after %d minutes" % (PANIC_MAX_S // 60))
    if _panic["on"]:
        out.insert(0, _panic_alert())
    _alert_active = out
    _alert_mark()
    return out


def _alert_test_on():
    global _alert_test_until
    if _alert_test_until is None:
        return False
    if ticks_diff(_alert_test_until, ticks_ms()) <= 0:
        _alert_test_until = None
        return False
    return True


async def alerts_test(obj):
    """Raise the test alarm for a minute - straight away, not on the next poll
    cycle, so pressing the button is answered at once."""
    global _alert_test_until, _alert_active
    _alert_test_until = ticks_ms() + 60000
    _alert_ack.discard("test")
    if not any(a["id"] == "test" for a in _alert_active):
        _alert_active = _alert_active + [dict(_ALERT_TEST)]
    _alert_mark()
    return {"ok": True, "alerts": _alert_active}


def _alert_mark():
    """Forget clearances for alerts that have gone, and flag the rest."""
    global _alert_ack
    ids = [a["id"] for a in _alert_active]
    _alert_ack = set(i for i in _alert_ack if i in ids)
    for a in _alert_active:
        a["acked"] = a["id"] in _alert_ack


def api_alerts():
    """Just the alerts: small enough for screens to ask every couple of seconds."""
    return {"alerts": _alert_active, "sound": bool(cfg.ALERT_SOUND),
            "panic": _panic["on"], "drev": _disp["rev"]}


async def alerts_ack(obj):
    """Clear an alert (obj["id"]), or every active one when no id is given.
    It stays listed, flagged "acked", until the condition itself goes away."""
    want = obj.get("id")
    if _panic["on"] and want in (None, "panic"):
        # clearing a panic stops it, everywhere
        return await panic_set({"on": False})
    for a in _alert_active:
        if want is None or a["id"] == want:
            _alert_ack.add(a["id"])
    _alert_mark()
    return {"ok": True, "alerts": _alert_active}


def _derive():
    b, r, v = state["battery"], state["renogy"], state["victron"]
    d = {}
    if b.get("connected") and b.get("current") is not None:
        charge_in = 0.0; have = False
        if r.get("connected") and r.get("charge_a") is not None:
            charge_in += r["charge_a"]; have = True
        if v.get("connected") and v.get("current") is not None:
            charge_in += v["current"]; have = True
        if have:
            load_a = max(0.0, charge_in - b["current"])
            d["load_a"] = round(load_a, 2)
            d["load_w"] = round(load_a * b["voltage"], 1) if b.get("voltage") else None
    state["derived"] = d


HEATER_FILE = "heater_last.json"


def _heater_save():
    """Keep the last good heater reading across a restart.

    The heater is only read when someone asks for it - it is a BLE connection
    that takes several seconds and interrupts the battery poll while it runs.
    So after a reboot the hub knew nothing about the heater until the user
    opened the page and waited, and the page had nothing to show but dashes.

    The reading itself is small and changes rarely, and reads are user-driven
    rather than continuous, so writing it each time costs very little flash.
    """
    h = state["heater"]
    if not h.get("connected"):
        return
    import json               # imported here, as everywhere else in this file
    try:
        d = dict(h)
        d["at"] = time.time() if _rtc_ok() else 0
        with open(HEATER_FILE, "w") as f:
            json.dump(d, f)
    except (OSError, ValueError) as e:
        print("heater: could not save the last reading:", e)


def _heater_load():
    """Restore it at boot, marked as remembered rather than live."""
    import json
    try:
        with open(HEATER_FILE) as f:
            d = json.load(f)
        if not isinstance(d, dict):
            return
        # connected stays False: this is what the heater said last time, not
        # what it is saying now. The page shows it as remembered and says when.
        d["connected"] = False
        d["remembered"] = True
        state["heater"] = d
        print("heater: restored the last known reading")
    except (OSError, ValueError):
        pass


def _snapshot(key, seen_key, stale_ms):
    s = state[key]
    if s.get("connected") and ticks_diff(ticks_ms(), state[seen_key]) > stale_ms:
        s = dict(s); s["connected"] = False; s["stale"] = True
    return s


def api_data():
    b = dict(_snapshot("battery", "battery_seen", cfg.BATTERY_STALE_MS))
    b["updated"] = time.time() if _rtc_ok() else None
    b["age_s"] = (round(ticks_diff(ticks_ms(), state["battery_seen"]) / 1000.0)
                  if b.get("connected") else None)
    b["stats"] = _stats_summary(b)
    b["renogy"] = _snapshot("renogy", "renogy_seen", cfg.RENOGY_STALE_MS)
    b["victron"] = _snapshot("victron", "victron_seen", cfg.VICTRON_STALE_MS)
    b["heater"] = _snapshot("heater", "heater_seen", cfg.HEATER_STALE_MS)
    b["derived"] = state["derived"]
    b["storage"] = dict(_storage)
    b["alerts"] = list(_alert_active)
    # A second-old attitude is fine here - this feeds the OLED and the other
    # pages, none of which are tracking the bubble. The Level page has its own
    # endpoint. Before this, every dashboard poll from every page forced a
    # fresh 80 ms I2C measurement.
    b["level"] = api_level(600000) if cfg.LEVEL_ENABLED else None
    # Which layout each page should draw. Published here rather than behind its
    # own endpoint: every page already polls this, so a change takes effect on
    # the next tick with no extra request and no reload.
    b["views"] = {"overview": getattr(cfg, "OVERVIEW_VIEW", "auto"),
                  "level": getattr(cfg, "LEVEL_VIEW", "bubble")}
    # The angles the level page draws its scale and its colours from.
    b["level_scale"] = {"ok": getattr(cfg, "LEVEL_OK_DEG", 1.0),
                        "warn": getattr(cfg, "LEVEL_WARN_DEG", 20.0),
                        "max": getattr(cfg, "LEVEL_MAX_DEG", 30.0)}
    b["level_refresh_ms"] = max(200, int(getattr(cfg, "LEVEL_REFRESH_MS", 500)))
    b["gps"] = gps_status()
    b["alert_sound"] = bool(cfg.ALERT_SOUND)
    # one place decides whether the electric element may be offered
    b["mains_live"] = bool(_snapshot("victron", "victron_seen",
                                     cfg.VICTRON_STALE_MS).get("connected"))
    b["autoheat"] = _ah_status()
    # the van display's Drive page list, kept here so it is set in one place
    b["checklist"] = list(getattr(cfg, "CHECKLIST", settings.DEFAULT_CHECKLIST))
    b["relays"] = len(_relays)             # 0: the switches are on screen only
    b["display_fw"] = dispfw_status()
    # and the Switches page's button names
    b["switch_names"] = list(getattr(cfg, "SWITCH_NAMES", settings.DEFAULT_SWITCHES))
    b["switch_icons"] = list(getattr(cfg, "SWITCH_ICONS", settings.DEFAULT_SWITCH_ICONS))
    # whether the hub has a password yet - the pages ask for one until it has
    b["auth_set"] = bool(settings.load()["auth"]["hash"])
    # goes up whenever the WiFi settings are saved: the display fetches them
    # again at once, so a changed hotspot password reaches it before the hub
    # restarts onto it
    b["wifi_rev"] = _wifi_rev
    b["panel"] = dict(getattr(cfg, "PANEL", settings.DEFAULT_PANEL))
    b["today"] = _today_status()
    b["timers"] = _timers_status()
    b["display"] = _disp
    b["wake"] = _wake_status()
    b["guard"] = _guard_status()
    b["engine"] = _engine_now
    b["starter"] = _starter_status(b["renogy"])
    b["van"] = _van_size()
    return b


def _van_size():
    """Wheelbase and track in mm, for the wheel heights (a 2025 MWB Crafter)."""
    return {"wheelbase_mm": getattr(cfg, "VAN_WHEELBASE_MM", 3640),
            "track_mm": getattr(cfg, "VAN_TRACK_MM", 1777)}


def api_history():
    """Oldest-first readings, yielded one at a time.

    A generator so nothing large is built: httpd streams these straight out, and
    the only allocation is the single row being written.
    """
    start = 0 if _h_n < HISTORY_MAX else _h_i
    for k in range(_h_n):
        j = (start + k) % HISTORY_MAX
        yield (_h_t[j], round(_h_v[j], 2), round(_h_a[j], 2),
               None if _h_s[j] < 0 else _h_s[j], round(_h_w[j], 1))


# ---- WiFi ----------------------------------------------------------------
_wlan = None


def _internet():
    """Is there a route out to the internet? A send to an internet address fails
    at once without one, where a name lookup waits: with no route, setting the
    clock held the whole hub for 13 s at a time (Oct 2026)."""
    import socket
    u = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        u.sendto(b"\0", ("1.1.1.1", 53))
        return True
    except OSError:
        return False
    finally:
        u.close()


def _try_ntp():
    """Set the RTC from NTP if there's internet; harmless if not (van hotspot).
    From a fixed address (Cloudflare's time service), so no name lookup."""
    if not _internet():
        print("RTC: no route out to the internet - clock not set from NTP")
        return
    try:
        stalls.mark("internet clock")
        import ntptime
        ntptime.host = "162.159.200.123"
        ntptime.settime()
        print("RTC set from NTP")
    except Exception:
        pass


def _ip_int(a):
    p = [int(x) for x in a.split(".")]
    return (p[0] << 24) | (p[1] << 16) | (p[2] << 8) | p[3]


def _overlaps(ap_ip, sta_ip, sta_mask):
    """Do the hotspot's /24 and the joined network share any address?

    Uses the joined network's real mask, not a guess: this van's home network is
    a /22 spanning 192.168.4-7, so comparing three octets would have missed a
    router on 192.168.5.x while the two ranges genuinely overlap.
    """
    try:
        m = _ip_int(sta_mask) & 0xFFFFFF00      # never looser than the AP's /24
        return (_ip_int(ap_ip) & m) == (_ip_int(sta_ip) & m)
    except Exception:
        return False


def _sta_joined():
    return _wlan is not None and _wlan.isconnected()


def _net_clash():
    """True when the hotspot's subnet overlaps the network we have joined."""
    try:
        if _wlan is None or not _wlan.isconnected():
            return False
        sta = _wlan.ifconfig()
        return _overlaps(getattr(cfg, "AP_IP", "192.168.4.1"), sta[0], sta[1])
    except Exception:
        return False


def _ap_should_run():
    """Whether the hotspot belongs up right now.

    It always runs when we have no network of our own, since it is the only way
    back in.  Alongside a joined network it runs only if the two do not overlap:
    a hotspot whose clients are handed unroutable addresses is worse than no
    hotspot at all.
    """
    if cfg.WIFI_MODE == "ap":
        return True
    if not _sta_joined():
        return True
    return bool(cfg.WIFI_AP_ALWAYS) and not _net_clash()


def _ap_active():
    try:
        return bool(network.WLAN(network.AP_IF).active())
    except Exception:
        return False


def _ap_ip():
    try:
        ap = network.WLAN(network.AP_IF)
        return ap.ifconfig()[0] if ap.active() else None
    except Exception:
        return None


# ---- the Wi-Fi state machine ------------------------------------------------
# One radio, two interfaces: the hub's own connection to a network (station)
# and its hotspot. The hub is always in one of two states, and _wifi_check()
# is the only thing that moves it between them:
#   "joined"   on a known network. The hotspot alongside it only if wanted
#              (WIFI_AP_ALWAYS) and its subnet does not clash with the network's.
#   "hotspot"  no network: the hotspot up, the known networks tried again on
#              the retry schedule (_retry_ms). A join attempt takes the hotspot
#              down for at most the join timeout per network (_try_join).
# The rule everything here keeps: the interface set up last is the route out
# to the internet. Starting the hotspot takes that route over; stopping it
# then leaves no route at all. Measured on the hub (Oct 2026): back from a
# drive, it rejoined the home network - reachable from it, pages and all - with
# no internet (no clock or forecast) until restarted. So whenever the
# hotspot has started, the connection is set up again afterwards
# (_sta_reinit) before it is relied on; _sta_fresh records whether it has been.
# Tested with the real code in tests/test_wifi_scenarios.py, the route included.
_wifi_state = "start"
_sta_fresh = True             # the station was set up after the hotspot last started


async def _ap_set(on):
    """Switch the hotspot on or off. The only place that does."""
    global _sta_fresh
    ap = network.WLAN(network.AP_IF)
    if on and not ap.active():
        stalls.mark("hotspot start")
        # Activate before configuring. ESP32 refuses config() on an interface it
        # has not started yet - "Wifi Invalid Mode", which is fatal at boot -
        # while the Pico W accepts it either way round. This order suits both.
        ap.active(True)
        _sta_fresh = False                 # the hotspot has the route out now
        try:
            # ESP32 leaves the AP open unless the auth mode is given explicitly;
            # the Pico W's driver does not accept the keyword, hence the fallback.
            ap.config(essid=cfg.AP_SSID, password=cfg.AP_PASSWORD, authmode=3)
        except Exception:
            ap.config(essid=cfg.AP_SSID, password=cfg.AP_PASSWORD)
        ip = getattr(cfg, "AP_IP", "") or "192.168.44.1"
        try:
            if ap.ifconfig()[0] != ip:
                # Order matters, measured on this board: setting the address
                # BEFORE active(True) is overwritten back to 192.168.4.1 by
                # activation, and bouncing the interface afterwards resets it the
                # same way. Setting it once, after activation, is what sticks.
                ap.ifconfig((ip, "255.255.255.0", ip, ip))
                print("AP address set to", ip)
        except Exception as e:
            print("could not set the AP address:", e)
        # Waiting for it to come up hands over the turn: with time.sleep_ms here
        # the whole hub - the level readings, Bluetooth, the pages - stopped for up
        # to five seconds every time it left the home network.
        for _ in range(25):
            if ap.active():
                break
            await asyncio.sleep_ms(200)
        print("AP up:", cfg.AP_SSID, ap.ifconfig()[0])
    elif not on and ap.active():
        ap.active(False)
        print("AP down" + (": its subnet overlaps " + _wlan.ifconfig()[0] if _sta_joined() else ""))


def _sta_reinit():
    """Set the station up again, so that it is the route out (see above). Done
    while the hotspot is still up, so the radio never has neither interface."""
    global _wlan, _sta_fresh
    if _wlan is None:
        _wlan = network.WLAN(network.STA_IF)
    try:
        _wlan.active(False)
    except OSError:
        pass
    _wlan.active(True)
    _sta_fresh = True
    print("wifi: connection set up afresh, to keep the route out")


def _known_networks():
    """Known networks in priority order, the legacy single SSID first. One
    switched off in Settings is kept there but left out here, so never joined."""
    nets = []
    saved = getattr(cfg, "WIFI_NETWORKS", []) or []
    seen = [n["ssid"] for n in saved if n.get("ssid") and n.get("on") is False]
    if cfg.WIFI_SSID and cfg.WIFI_SSID not in seen:
        nets.append({"ssid": cfg.WIFI_SSID, "password": cfg.WIFI_PASSWORD})
        seen.append(cfg.WIFI_SSID)
    for n in saved:
        if n.get("ssid") and n["ssid"] not in seen:
            nets.append({"ssid": n["ssid"], "password": n.get("password", "")})
            seen.append(n["ssid"])
    return nets


# A network that is heard but will not take us - parked at the edge of its range,
# or a changed password - is not tried again on every check. Each try takes the
# hotspot down for up to the join timeout, so retrying every 15 s kept the
# hotspot off for half the time and turned phones and the display away
# (simulated in tests/test_wifi_scenarios.py). ssid -> [failures, ticks_ms
# before which it is not tried again]; cleared when it joins.
_join_fail = {}
JOIN_BACKOFF_S = (60, 120, 300, 600)


def _join_held(ssid):
    f = _join_fail.get(ssid)
    return f is not None and ticks_diff(f[1], ticks_ms()) > 0


def _join_failed(ssid):
    f = _join_fail.get(ssid) or [0, 0]
    f[0] += 1
    wait = JOIN_BACKOFF_S[min(f[0], len(JOIN_BACKOFF_S)) - 1]
    f[1] = ticks_ms() + wait * 1000
    _join_fail[ssid] = f
    print("wifi: could not join %s - not trying it again for %d s" % (ssid, wait))


async def _connect(n):
    """Join one network and wait for it. True when joined."""
    global _wifi_ssid
    print("wifi: trying", n["ssid"])
    stalls.mark("wifi join")
    try:
        _wlan.connect(n["ssid"], n["password"])
    except OSError as e:
        print("wifi: connect failed:", e)
        return False
    for _ in range(max(1, int(cfg.WIFI_JOIN_TIMEOUT_S * 2))):
        if _wlan.isconnected():
            _wifi_ssid = n["ssid"]
            print("WiFi joined:", n["ssid"], "->", _wlan.ifconfig()[0])
            return True
        await asyncio.sleep_ms(500)
    try:
        _wlan.disconnect()
    except OSError:
        pass
    return False


async def _try_join():
    """Try each known network that is on the air. Returns the joined SSID, or None.

    Only networks actually on the air are attempted: scanning first avoids
    spending the whole join timeout on the home network while parked in a field.
    A scan that worked and found nothing we know means nothing to join - it is
    not a failed scan. Treating the two alike once meant trying every network
    blind, hotspot down, wherever no other Wi-Fi was about.

    A coroutine, and it must stay one: waiting for a join with time.sleep_ms
    blocks the whole event loop, so a network in range that refuses us used to
    freeze the dashboard and the BLE polling for the full join timeout on every
    retry - minutes of it with several networks saved.
    """
    global _wlan
    nets = _known_networks()
    if not nets:
        return None
    if _wlan is None:
        _wlan = network.WLAN(network.STA_IF)
    _wlan.active(True)
    around = []
    scanned = False
    stalls.mark("wifi scan")
    try:
        around = [n[0].decode() if isinstance(n[0], bytes) else str(n[0])
                  for n in _wlan.scan()]
        scanned = True
    except Exception as e:
        print("wifi scan failed:", e)      # fall through and just try them all
    nets = [n for n in nets if (not scanned or n["ssid"] in around)
            and not _join_held(n["ssid"])]
    if not nets:
        return None                        # nothing we know on the air, or not yet due
    if not _sta_fresh:
        _sta_reinit()                      # the route out, before the hotspot goes
    if _ap_active():
        # One radio serves both, and the hotspot sits on 192.168.4.1 - an
        # address the home router here also uses. The join is quicker and surer
        # with it out of the way; _settle_ap() brings it back afterwards if it
        # is still wanted.
        print("wifi: hotspot off while trying", ", ".join(n["ssid"] for n in nets))
        await _ap_set(False)
    for n in nets:
        if await _connect(n):
            _join_fail.pop(n["ssid"], None)
            return n["ssid"]
        _join_failed(n["ssid"])
    return None


WIFI_QUICK_TRIES = 20           # every 15 s for the first five minutes


def _retry_ms():
    """How long to wait before the next join attempt: every 15 s for the first
    five minutes after the network is lost, then doubling, up to WIFI_RETRY_S.
    Driving round the block and back onto the drive is picked up within about
    15 s of being back in range (it took a minute or more when the wait doubled
    from the start), while a network that is simply not there - the van away
    for the week - is not hammered."""
    if not _wifi_fails:
        return 0
    if _wifi_fails <= WIFI_QUICK_TRIES:
        return 15000
    return min(cfg.WIFI_RETRY_S, 15 * (1 << min(_wifi_fails - WIFI_QUICK_TRIES, 8))) * 1000


async def _settle_ap():
    """Joined: the hotspot alongside only if wanted, and the route out kept on
    the network. Starting the hotspot takes the route, so the connection is
    set up again behind it and the same network rejoined."""
    if _ap_should_run():
        if not _ap_active():
            ssid = _wifi_ssid
            await _ap_set(True)
            _sta_reinit()
            n = [x for x in _known_networks() if x["ssid"] == ssid]
            if not (n and await _connect(n[0])):
                print("wifi: could not rejoin", ssid, "after starting the hotspot")
    elif _ap_active():
        await _ap_set(False)               # the route stays: set up after it
    if _sta_joined() and not _sta_fresh:
        # Joined with the route still on the hotspot's side - after a restart
        # that left the hotspot running, say. Set it up again and rejoin.
        ssid = _wifi_ssid
        _sta_reinit()
        n = [x for x in _known_networks() if x["ssid"] == ssid]
        if n:
            await _connect(n[0])


async def _wifi_check():
    """One step of the state machine: at start-up and on every poll.

    Not joined: try the known networks when the retry schedule says, and
    otherwise keep the hotspot up - the way in when there is no network, so a
    wrong password can never lock the unit out. Joined: keep the hotspot as
    wanted, and the route out on the network."""
    global _wifi_state, _wifi_fallback, _wifi_ssid, _wifi_next_try, _wifi_fails
    try:
        if cfg.WIFI_MODE == "ap":
            _wifi_state, _wifi_fallback = "hotspot", False
            await _ap_set(True)
            return
        if _sta_joined():
            if _wifi_state != "joined":
                print("WiFi: on", _wifi_ssid or "a network")
            _wifi_state, _wifi_fallback, _wifi_fails = "joined", False, 0
            await _settle_ap()
            return
        if _wifi_state == "joined":
            print("WiFi: lost", _wifi_ssid)
            _wifi_next_try = 0                 # look again straight away
        _wifi_state, _wifi_fallback, _wifi_ssid = "hotspot", True, ""
        if ticks_diff(ticks_ms(), _wifi_next_try) >= 0:
            print("WiFi: looking for a known network")
            if await _try_join():
                _wifi_state, _wifi_fallback, _wifi_fails = "joined", False, 0
                await _settle_ap()
                _try_ntp()
                return
            _wifi_fails += 1
            _wifi_next_try = ticks_ms() + _retry_ms()
        await _ap_set(True)
    except Exception as e:
        print("wifi check error:", e)


async def wifi_up():
    """At start-up: join first, and only then decide about the hotspot. The Pico
    W's firmware pins the hotspot to 192.168.4.1, the home router's own address
    here, and starting it first meant joining with a second interface already
    claiming that address (the boot log of 25 Sep 2026). If no network is found
    the hotspot comes up. Returns the hub's address."""
    global _wlan, _sta_fresh
    if _wlan is None:
        _wlan = network.WLAN(network.STA_IF)
    _wlan.active(True)
    # A soft restart leaves the hotspot running, and with it the route out:
    # the connection must be set up again before it is relied on.
    _sta_fresh = not _ap_active()
    await _wifi_check()
    if _sta_joined():
        return _wlan.ifconfig()[0]
    if not _wifi_fallback and cfg.WIFI_MODE != "ap":
        print("WiFi: no known network joined - running on the hotspot")
    return _ap_ip() or getattr(cfg, "AP_IP", "192.168.4.1")


# ---- discovery -------------------------------------------------------------
# The router hands the hub whatever address it likes (192.168.4.23 one day,
# .187 the next). It answers to camperlux.local (the firmware's hostname,
# firmware/boards/...), but not every phone looks .local names up - Android often
# does not - and the Pico W did not answer at all (tested). So, as well, they
# can broadcast
# "camperdash?" to this port and the hub replies; the reply's source address is
# the hub. Works the same on the home network, a phone hotspot or our own.
DISCOVERY_PORT = 50505


def wifi_share():
    """The networks this hub knows, passwords included, and its own hotspot -
    for the van display, which keeps a copy so that when the hub moves to one
    of them the display can follow. Served behind the API token if one is set."""
    w = settings.load()["wifi"]
    # not the ones switched off: the hub will not be found on those
    saved = [n for n in (w.get("networks") or []) if n.get("ssid")]
    nets = [{"ssid": n["ssid"], "password": n.get("password", "")}
            for n in saved if n.get("on") is not False]
    if w.get("ssid") and all(n["ssid"] != w["ssid"] for n in saved):
        nets.insert(0, {"ssid": w["ssid"], "password": w.get("password", "")})
    return {"ok": True, "networks": nets,
            "ap": {"ssid": w.get("ap_ssid", ""), "password": w.get("ap_password", "")}}


async def discovery_loop():
    # off on a second hub being tried out beside the real one, so the display
    # does not find it (DISCOVERY_ENABLED = False in its config.py)
    if not getattr(cfg, "DISCOVERY_ENABLED", True):
        return
    import socket
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.bind(("0.0.0.0", DISCOVERY_PORT))
        s.setblocking(False)
    except Exception as e:
        print("discovery unavailable:", e)
        return
    while True:
        try:
            data, addr = s.recvfrom(64)
        except OSError:
            await asyncio.sleep_ms(300)
            continue
        if data.startswith(b"camperdash?"):
            try:
                s.sendto(b"camperdash hub", addr)
            except OSError:
                pass


# ---- battery MOSFET control ----------------------------------------------
# "holding" means the keeper is the reason charging is off, so switching
# storage mode off can put it back.  If the user turned charge off by hand we
# leave it alone - restoring it would override a deliberate choice.
_storage = {"on": False, "target": 80, "hysteresis": 5, "last": "", "holding": False}
# The pack sometimes acknowledges a MOSFET command and then does not apply it -
# seen intermittently, cause not yet established. Retrying every poll cycle would
# hammer the BMS for hours, so back off after a few tries and say so rather than
# repeating a message that looks like a hung loop.
_storage_fails = 0
_storage_retry_at = 0
_STORAGE_MAX_FAILS = 3
_STORAGE_BACKOFF_MS = 300000        # 5 minutes
_STORAGE_FILE = "storage.json"


def _storage_load():
    try:
        import json
        with open(_STORAGE_FILE) as f:
            d = json.load(f)
        if isinstance(d, dict):
            _storage["on"] = bool(d.get("on"))
            _storage["holding"] = bool(d.get("holding"))
            t = int(d.get("target", 80))
            _storage["target"] = max(30, min(95, t))
    except (OSError, ValueError, TypeError):
        pass


def _storage_save():
    try:
        import json
        with open(_STORAGE_FILE, "w") as f:
            json.dump({"on": _storage["on"], "target": _storage["target"],
                       "holding": _storage["holding"]}, f)
    except OSError as e:
        print("storage save failed:", e)


async def bms_fet(obj):
    """Switch the pack's MOSFETs.

    Turning discharge off cuts power to everything running from the leisure
    battery, which on most installs includes this hub - so it cannot be undone
    from here afterwards.  The caller has to say confirm=\"CUT POWER\" to do it,
    which the UI only sends after the user types it.
    """
    want_c = obj.get("charge")
    want_d = obj.get("discharge")
    # Both MOSFETs go out in one register, so whichever was not asked about is
    # carried over from the last reading. That reading is retained through failed
    # polls, so insist it is recent: acting on a minutes-old copy could re-close a
    # MOSFET the BMS had just opened for protection. _snapshot marks it stale past
    # BATTERY_STALE_MS. The pack cannot simply be re-read here instead - measured
    # on this unit, any traffic between connecting and the write makes the BMS
    # acknowledge the command and then not apply it.
    b = _snapshot("battery", "battery_seen", cfg.BATTERY_STALE_MS)
    if not b.get("connected"):
        return {"ok": False,
                "error": "no recent battery reading - not sending a change"}
    charge_on = b.get("charge_fet") if want_c is None else bool(want_c)
    discharge_on = b.get("discharge_fet") if want_d is None else bool(want_d)
    if charge_on is None or discharge_on is None:
        return {"ok": False, "error": "current MOSFET state unknown"}
    # Only when discharge was explicitly asked to go off. A charge-only command
    # leaves discharge_on as None, which must not be read as "turn it off" -
    # that would demand the CUT POWER confirmation just to switch charging.
    if want_d is not None and not bool(want_d) and b.get("discharge_fet"):
        if obj.get("confirm") != "CUT POWER":
            return {"ok": False, "needs_confirm": True,
                    "error": "turning discharge off cuts the van's 12 V supply, including this hub"}
    if want_c is not None and _storage["on"]:
        # otherwise the keeper would silently undo what the user just asked for
        _storage["on"] = False
        _storage["holding"] = False
        _storage["last"] = "switched off - charge was set by hand"
        _storage_save()
    res = await ble_hub.set_mosfet(charge_on, discharge_on)
    if res.get("ok") and res.get("state"):
        for k in ("charge_fet", "discharge_fet"):
            if res["state"].get(k) is not None:
                state["battery"][k] = res["state"][k]
    return res


async def storage_set(obj):
    restored = False
    if "on" in obj:
        want = bool(obj["on"])
        # Switching storage mode off must not quietly leave the van unable to
        # charge: if the keeper is what stopped charging, start it again.
        if not want and _storage["on"] and _storage["holding"]:
            b = state["battery"]
            if b.get("connected") and b.get("charge_fet") is False:
                res = await ble_hub.set_mosfet(True, b.get("discharge_fet", True))
                if res.get("ok"):
                    restored = True
                    state["battery"]["charge_fet"] = True
                else:
                    return {"ok": False, "storage": dict(_storage),
                            "error": "storage mode left on - couldn't switch charging back on: %s"
                                     % res.get("error")}
        _storage["on"] = want
        if not want:
            _storage["holding"] = False
        _storage["last"] = ("armed" if want else
                            ("off - charging switched back on" if restored else "off"))
    if "target" in obj:
        try:
            t = int(obj["target"])
        except (TypeError, ValueError):
            return {"ok": False, "error": "target must be a number"}
        if t < 30 or t > 95:
            return {"ok": False, "error": "target must be between 30 and 95 %"}
        _storage["target"] = t
    _storage_save()
    return {"ok": True, "storage": dict(_storage), "charge_restored": restored}


async def _storage_check():
    """Hold the pack near the storage target by switching CHARGE only.

    This never touches the discharge MOSFET - the van keeps its 12 V supply
    whatever happens here.
    """
    if not _storage["on"]:
        return
    b = state["battery"]
    if not b.get("connected") or b.get("soc") is None:
        return
    global _storage_fails, _storage_retry_at
    soc, target = b["soc"], _storage["target"]
    charge_on = b.get("charge_fet")
    if charge_on is None:
        return
    # The pack does not always reflect a MOSFET change within the few seconds the
    # write waits - it has been seen settling the better part of a minute later.
    # So confirmation is deferred to the polls that follow rather than demanded
    # on the spot: whatever the write reported, if charging is off while we are
    # armed and at or above target, the keeper is holding and says so.
    if soc >= target and not charge_on:
        if not _storage["holding"]:
            _storage["holding"] = True
            _storage["last"] = "charging stopped at %d%%" % soc
            _storage_fails = 0
            _storage_save()
            print("storage:", _storage["last"])
        return
    if soc <= target - _storage["hysteresis"] and charge_on and _storage["holding"]:
        _storage["holding"] = False
        _storage["last"] = "charging allowed again at %d%%" % soc
        _storage_fails = 0
        _storage_save()
        print("storage:", _storage["last"])
        return
    # Backing off only ever stops us SENDING again; the observation above still
    # runs, so a change that lands late is still noticed while we are waiting.
    if _storage_fails >= _STORAGE_MAX_FAILS and ticks_diff(ticks_ms(), _storage_retry_at) < 0:
        return
    if soc >= target and charge_on:
        res = await ble_hub.set_mosfet(False, b.get("discharge_fet", True))
        if res.get("ok"):
            _storage["holding"] = True
            _storage["last"] = "charging stopped at %d%%" % soc
            state["battery"]["charge_fet"] = False   # don't wait for the next poll
            _storage_fails = 0
            _storage_save()
        else:
            _storage_fails += 1
            if _storage_fails >= _STORAGE_MAX_FAILS:
                _storage_retry_at = ticks_ms() + _STORAGE_BACKOFF_MS
                _storage["last"] = ("the battery would not stop charging after %d tries "
                                    "- waiting 5 minutes before trying again (%s)"
                                    % (_storage_fails, res.get("error")))
            else:
                _storage["last"] = ("asked the battery to stop charging (try %d) - "
                                    "waiting for it to take effect" % _storage_fails)
        print("storage:", _storage["last"])
    elif soc <= target - _storage["hysteresis"] and not charge_on:
        res = await ble_hub.set_mosfet(True, b.get("discharge_fet", True))
        if res.get("ok"):
            _storage["holding"] = False
            _storage["last"] = "charging allowed again at %d%%" % soc
            state["battery"]["charge_fet"] = True
            _storage_fails = 0
            _storage_save()
        else:
            _storage["last"] = "couldn't resume charging: %s" % res.get("error")
        print("storage:", _storage["last"])


# ---- settings API --------------------------------------------------------
def _is_mac(v):
    parts = v.split(":")
    if len(parts) != 6:
        return False
    for p in parts:
        if len(p) != 2:
            return False
        for ch in p.lower():
            if ch not in "0123456789abcdef":
                return False
    return True


_wifi_rev = 0               # see api_data: bumped when the WiFi settings change


def _sha(s):
    import hashlib
    import binascii
    return binascii.hexlify(hashlib.sha256(s.encode()).digest()).decode()


def auth_ok(token):
    """May this request do the things behind the hub password? Yes when no
    password is set, when "require the password" is off, or when it carries
    the key worked out from the password or a paired display's key. The old
    API_TOKEN, if one is still set in config.py, is accepted too.

    Read from the settings on every request, so a change needs no restart."""
    a = settings.load()["auth"]
    legacy = getattr(cfg, "API_TOKEN", "")
    if not ((a["hash"] and a["web"]) or legacy):
        return True
    if not token:
        return False
    if legacy and token == legacy:
        return True
    if a["hash"]:
        h = _sha(token)
        return h == a["hash"] or h in a["devices"].values()
    return False


# ---- pairing the van display ---------------------------------------------------------
# The display shows a 6-digit code and asks for its key; typing the code into
# the hub's Settings (behind the password) lets that one request through.
# Codes last three minutes, and five wrong guesses cancel one.
_pair = None


async def pair_code(obj):
    global _pair
    code = str(obj.get("code", "")).strip()
    if len(code) != 6 or not code.isdigit():
        return {"ok": False, "error": "the code on the display is 6 digits"}
    _pair = {"code": code, "until": time.ticks_add(ticks_ms(), 180000), "tries": 0}
    return {"ok": True}


async def pair_claim(obj):
    global _pair
    p = _pair
    if p is None or ticks_diff(p["until"], ticks_ms()) <= 0:
        _pair = None
        return {"ok": False, "waiting": True}
    if str(obj.get("code", "")) != p["code"]:
        p["tries"] += 1
        if p["tries"] >= 5:
            _pair = None
        return {"ok": False, "waiting": True}
    _pair = None
    import os
    import binascii
    key = binascii.hexlify(os.urandom(16)).decode()
    s = settings.load()
    s["auth"]["devices"][str(obj.get("name") or "display")[:20]] = _sha(key)
    settings.save(s)
    return {"ok": True, "key": key}


def _is_hex(v, n):
    if len(v) != n:
        return False
    for ch in v.lower():
        if ch not in "0123456789abcdef":
            return False
    return True


async def settings_get(_obj=None):
    # the definitions ride along so the Settings page can document each alert
    # without keeping its own copy of the wording
    return {"ok": True, "settings": settings.public(), "alert_defs": _alert_defs()}


async def settings_set(obj):
    """Validate and save. A blank password means 'keep the one already stored',
    so the UI never has to hold the real password to save an unrelated change."""
    cur = settings.load()
    new = {"wifi": dict(cur["wifi"]), "devices": dict(cur["devices"]),
           "location": dict(cur["location"]), "api_token": cur["api_token"],
           "alerts": {"off": list(cur["alerts"]["off"]),
                      "sound": cur["alerts"]["sound"],
                      "low_soc": cur["alerts"].get("low_soc", 20)},
           "display": dict(cur["display"]),
           "checklist": list(cur["checklist"]),
           "switches": list(cur["switches"]),
           "switch_icons": list(cur["switch_icons"]),
           "panel": dict(cur["panel"]),
           "auth": {"hash": cur["auth"]["hash"], "web": cur["auth"]["web"],
                    "devices": dict(cur["auth"]["devices"])}}
    w = obj.get("wifi") or {}
    if "mode" in w:
        if w["mode"] not in ("sta", "ap"):
            return {"ok": False, "error": "mode must be sta or ap"}
        new["wifi"]["mode"] = w["mode"]
    for k in ("ssid", "ap_ssid"):
        if k in w:
            v = str(w[k]).strip()
            if len(v) > 32:
                return {"ok": False, "error": k + " is too long (max 32)"}
            new["wifi"][k] = v
    for k in ("password", "ap_password"):
        if w.get(k):                       # blank = keep existing
            new["wifi"][k] = str(w[k])
    # Known networks: a list of {ssid, password, on}. A blank password on an
    # entry that already has one means "keep it", same as the single fields;
    # on: false keeps the network but stops the hub joining it.
    if "networks" in w:
        if not isinstance(w["networks"], list):
            return {"ok": False, "error": "networks must be a list"}
        if len(w["networks"]) > 8:
            return {"ok": False, "error": "at most 8 saved networks"}
        old = {n["ssid"]: n.get("password", "") for n in cur["wifi"]["networks"]}
        out = []
        for n in w["networks"]:
            if not isinstance(n, dict):
                return {"ok": False, "error": "bad network entry"}
            ssid = str(n.get("ssid", "")).strip()
            if not ssid:
                continue
            if len(ssid) > 32:
                return {"ok": False, "error": "network name too long: " + ssid[:20]}
            pw = n.get("password")
            pw = str(pw) if pw else old.get(ssid, "")
            out.append({"ssid": ssid, "password": pw, "on": n.get("on") is not False})
        new["wifi"]["networks"] = out
    if "ap_always" in w:
        new["wifi"]["ap_always"] = bool(w["ap_always"])
    for k, lo, hi in (("join_timeout_s", 5, 60), ("retry_s", 30, 3600)):
        if k in w:
            try:
                v = int(w[k])
            except (TypeError, ValueError):
                return {"ok": False, "error": k + " must be a number"}
            if v < lo or v > hi:
                return {"ok": False,
                        "error": "%s must be between %d and %d seconds" % (k, lo, hi)}
            new["wifi"][k] = v
    if new["wifi"]["mode"] == "sta" and not new["wifi"]["ssid"] and not new["wifi"]["networks"]:
        return {"ok": False, "error": "add at least one network to join"}
    if not new["wifi"]["ap_ssid"]:
        return {"ok": False, "error": "the hotspot needs a name"}
    if "ap_ip" in w and str(w["ap_ip"]).strip() != new["wifi"]["ap_ip"]:
        # Moving the hotspot address does not move its DHCP pool: MicroPython
        # fixes the pool when the interface activates, so a client would be
        # handed an address it cannot route from and the hotspot - the way back
        # in when the WiFi details are wrong - would be unusable. Where the
        # range collides with a joined network the AP stands down instead; see
        # _ap_should_run().
        return {"ok": False,
                "error": "the hotspot address is fixed at %s - its DHCP pool cannot "
                         "follow a change, so clients would be left unroutable"
                         % new["wifi"]["ap_ip"]}
    d = obj.get("devices") or {}
    for k in ("bms", "renogy", "victron", "heater"):
        if k in d:
            v = str(d[k]).strip().lower()
            if v and not _is_mac(v):
                return {"ok": False, "error": "%s: not a Bluetooth address" % k}
            new["devices"][k] = v
    if d.get("victron_key"):
        v = str(d["victron_key"]).strip().lower()
        if not _is_hex(v, 32):
            return {"ok": False, "error": "the Victron key must be 32 hex characters"}
        new["devices"]["victron_key"] = v
    loc = obj.get("location") or {}
    for k in ("lat", "lon"):
        if k in loc:
            v = str(loc[k]).strip()
            if v:
                try:
                    f = float(v)
                except ValueError:
                    return {"ok": False, "error": "%s must be a number" % k}
                limit = 90 if k == "lat" else 180
                if f < -limit or f > limit:
                    return {"ok": False, "error": "%s is out of range" % k}
                v = "%.4f" % f
            new["location"][k] = v
    if "name" in loc:
        new["location"]["name"] = str(loc["name"]).strip()[:40]
    if "source" in loc:
        if loc["source"] not in settings.WEATHER_SOURCES:
            return {"ok": False, "error": "the forecast location must be auto or fixed"}
        new["location"]["source"] = loc["source"]
    if "from" in loc and loc["from"] in settings.WEATHER_FROM:
        new["location"]["from"] = loc["from"]
    if new["location"]["source"] == "fixed" and not (new["location"]["lat"] and new["location"]["lon"]):
        return {"ok": False, "error": "a fixed forecast location needs a latitude and longitude"}
    al = obj.get("alerts")
    if isinstance(al, dict):
        if "sound" in al:
            new["alerts"]["sound"] = bool(al["sound"])
        if "low_soc" in al:
            try:
                new["alerts"]["low_soc"] = max(5, min(80, int(al["low_soc"])))
            except (TypeError, ValueError):
                return {"ok": False, "error": "the low battery limit must be a number"}
        if "off" in al:
            if not isinstance(al["off"], list):
                return {"ok": False, "error": "alerts.off must be a list"}
            known = [a["id"] for a in _ALERT_DEFS]
            off = [x for x in al["off"] if x in known]
            new["alerts"]["off"] = off
    if "checklist" in obj:
        cl = settings.clean_checklist(obj["checklist"])
        if cl is None:
            return {"ok": False, "error": "the checklist must be a list of items"}
        new["checklist"] = cl
    if "switches" in obj:
        sw = settings.clean_switches(obj["switches"])
        if sw is None:
            return {"ok": False, "error": "the switch names must be a list"}
        new["switches"] = sw
    if "switch_icons" in obj:
        si = settings.clean_switch_icons(obj["switch_icons"])
        if si is None:
            return {"ok": False, "error": "the switch icons must be a list"}
        new["switch_icons"] = si
    if "panel" in obj:
        pn = settings.clean_panel(obj["panel"], new["panel"])
        if pn is None:
            return {"ok": False, "error": "the van display volumes must be numbers"}
        new["panel"] = pn
    dp = obj.get("display")
    if isinstance(dp, dict):
        for k in ("on", "invert", "rotate",
                  "level_swap_xy", "level_invert_roll", "level_invert_pitch",
                  "gps_enabled", "level_log"):
            if k in dp:
                new["display"][k] = bool(dp[k])
        # Brightness stops at 1, not 0: zero is a black screen that looks
        # identical to a broken one, and there is already a switch for off.
        # Layout choices are a fixed set: anything else would leave a page with
        # no view it knows how to draw.
        for k, allowed in (("overview", ("auto", "diagram", "cards")),
                           ("level", ("bubble", "van"))):
            if k in dp:
                v = str(dp[k])
                if v not in allowed:
                    return {"ok": False,
                            "error": "%s must be one of %s" % (k, ", ".join(allowed))}
                new["display"][k] = v
        for k, lo, hi in (("brightness", 1, 255), ("refresh_ms", 500, 60000)):
            if k in dp:
                try:
                    v = int(dp[k])
                except (TypeError, ValueError):
                    return {"ok": False, "error": k + " must be a number"}
                if v < lo or v > hi:
                    return {"ok": False,
                            "error": "%s must be between %d and %d" % (k, lo, hi)}
                new["display"][k] = v
        # Levelling thresholds. Degrees, so these are floats - half a degree is
        # a meaningful difference when deciding whether a van is sleepable.
        if "level_refresh_ms" in dp:
            try:
                v = int(dp["level_refresh_ms"])
            except (TypeError, ValueError):
                return {"ok": False, "error": "level_refresh_ms must be a number"}
            # 200 ms is the floor: a reading is ~80 ms of blocking I2C, so
            # asking much faster would spend most of the hub's time measuring
            # tilt and starve the BLE poll that fetches the actual readings.
            if v < 200 or v > 10000:
                return {"ok": False,
                        "error": "level_refresh_ms must be between 200 and 10000"}
            new["display"]["level_refresh_ms"] = v
        for k, lo, hi in (("level_ok", 0.1, 15.0), ("level_warn", 1.0, 45.0),
                          ("level_max", 2.0, 60.0)):
            if k in dp:
                try:
                    v = float(dp[k])
                except (TypeError, ValueError):
                    return {"ok": False, "error": k + " must be a number"}
                if v < lo or v > hi:
                    return {"ok": False,
                            "error": "%s must be between %g and %g degrees" % (k, lo, hi)}
                new["display"][k] = v
        # The three have to stay in order or the vial cannot be drawn: the warn
        # ring would fall outside the rim, or the level ring outside the warning.
        if not (new["display"]["level_ok"] < new["display"]["level_warn"]
                < new["display"]["level_max"]):
            return {"ok": False,
                    "error": "levelling thresholds must increase: level (%g) < "
                             "warning (%g) < end of scale (%g)"
                             % (new["display"]["level_ok"], new["display"]["level_warn"],
                                new["display"]["level_max"])}
    if "api_token" in obj:
        new["api_token"] = str(obj["api_token"])
    au = obj.get("auth")
    if isinstance(au, dict):
        a = new["auth"]
        if au.get("clear"):
            a["hash"], a["devices"] = "", {}
        if au.get("set_key"):
            k = str(au["set_key"]).strip().lower()
            if not _is_hex(k, 64):
                return {"ok": False, "error": "bad password key"}
            a["hash"] = _sha(k)
        if "web" in au:
            a["web"] = bool(au["web"])
        if au.get("unpair") in a["devices"]:
            del a["devices"][au["unpair"]]
    try:
        settings.save(new)
    except OSError as e:
        return {"ok": False, "error": "could not save settings: %s" % e}
    global _wifi_rev
    if new["wifi"] != cur["wifi"]:
        _wifi_rev += 1
    # Device addresses are read per poll so they take effect immediately; the
    # WiFi interface and the API token are both bound at startup.
    restart = (any(new["wifi"][k] != cur["wifi"][k] for k in new["wifi"])
               or new["api_token"] != cur["api_token"]
               # same for the GPS: its loop opens the UART at start-up and
               # returns immediately if the receiver is switched off
               or new["display"]["gps_enabled"] != cur["display"]["gps_enabled"])
    moved = any(new["location"][k] != cur["location"][k] for k in ("lat", "lon", "source"))
    settings.apply()
    # The display is driven straight from cfg, so a change shows on the glass
    # immediately; nothing here needs a restart.
    if any(new["display"][k] != cur["display"][k]
           for k in ("level_swap_xy", "level_invert_roll", "level_invert_pitch")):
        # The stored zero belongs to the old axis mapping. level.py works that
        # out when it loads the file; it has to be told when the mapping changes
        # underneath it, or it keeps applying a zero that no longer means
        # anything - which is how an 8 degree error slipped through before.
        stale = level.orientation_changed()
        print("level: mounting changed, stored zero %s"
              % ("is now stale - recalibrate" if stale else "still applies"))
    if any(new["display"][k] != cur["display"][k] for k in
           ("level_swap_xy", "level_invert_roll", "level_invert_pitch")):
        # The axis mapping changed: re-read the calibration so the stored zero is
        # re-checked against the new orientation and flagged stale if it no
        # longer applies, and throw away the cached reading.
        level.load_cal()
        level.invalidate()
    if moved:
        global _weather_due
        _weather_due = 0          # fetch for the new place on the next loop
    return {"ok": True, "restart_required": restart,
            "settings": settings.public()}


async def settings_reveal(obj):
    """One stored password, for the Settings page's Show button, named by
    "what" - the hotspot's ("ap"), a saved network's ("net", with its ssid),
    the Victron key ("victron") and so on, as below.

    Behind the hub password (httpd), and only while one is set and required:
    without it, anyone on the van's Wi-Fi could read the home network's
    password off the hub. The hub's own password is never stored, so it is not
    here to show."""
    s = settings.load()
    if not (s["auth"]["hash"] and s["auth"]["web"]):
        return {"ok": False, "error": "stored passwords are only shown once the hub has a password "
                                      "(Settings, Security)"}
    what = obj.get("what")
    if what == "ap":
        v = s["wifi"]["ap_password"]
    elif what == "net":
        v = None
        for n in s["wifi"]["networks"]:
            if n["ssid"] == obj.get("ssid"):
                v = n.get("password", "")
        if v is None:
            return {"ok": False, "error": "no saved network of that name - save it first"}
    elif what == "victron":
        v = s["devices"]["victron_key"]
    else:
        return {"ok": False, "error": "nothing of that name to show"}
    print("settings: a stored password was shown (%s)" % what)
    return {"ok": True, "value": v or ""}


async def scan_ble(obj):
    try:
        ms = int(obj.get("ms", 6000))
    except (TypeError, ValueError):
        ms = 6000
    ms = max(2000, min(12000, ms))
    try:
        return {"ok": True, "devices": await ble_hub.scan_devices(ms)}
    except Exception as e:
        return {"ok": False, "error": "%s: %s" % (type(e).__name__, e)}


async def scan_wifi(_obj=None):
    """List nearby networks so the installer picks rather than types the SSID."""
    try:
        w = _wlan or network.WLAN(network.STA_IF)
        w.active(True)
        seen = {}
        for net in w.scan():
            ssid = net[0].decode() if isinstance(net[0], bytes) else str(net[0])
            if not ssid:
                continue
            rssi = net[3]
            if ssid not in seen or rssi > seen[ssid]["rssi"]:
                seen[ssid] = {"ssid": ssid, "rssi": rssi, "secure": net[4] > 0}
        nets = list(seen.values())
        nets.sort(key=lambda n: -n["rssi"])
        return {"ok": True, "networks": nets}
    except Exception as e:
        return {"ok": False, "error": "%s: %s" % (type(e).__name__, e)}


async def reboot(_obj=None):
    async def _go():
        await asyncio.sleep_ms(600)      # let the reply reach the browser first
        machine.reset()
    asyncio.create_task(_go())
    return {"ok": True, "rebooting": True}


_weather_due = 0          # ticks_ms deadline for the next forecast fetch
_weather_last = ""        # why the last attempt failed, for the UI
_weather_pos = None       # (lat, lon, "gps" | "settings") the forecast was fetched for
_weather_at = None        # ticks_ms of the last fetch
_place = None             # the name of where the van is (place.py), for a GPS forecast
WEATHER_MOVED_M = 10000   # a forecast follows the van once it is this far away
WEATHER_MIN_GAP_MS = 600000   # and not more than every 10 minutes while driving


def _gps_fresh():
    """(lat, lon) while the GPS has a fresh fix, else None."""
    g = gps_status()
    if g.get("fix") and not g.get("stale") and g.get("lat") is not None:
        return round(g["lat"], 3), round(g["lon"], 3)
    return None


def _weather_fixed():
    return getattr(cfg, "WEATHER_SOURCE", "auto") == "fixed"


def _weather_where():
    """Where to forecast for: the van's own position while the GPS has a
    fresh fix (a van moves; the Settings location is only where it lives),
    else the location in Settings - or always the Settings location, if it is
    set to fixed. (lat, lon, source) or None."""
    fix = None if _weather_fixed() else _gps_fresh()
    if fix:
        return fix[0], fix[1], "gps"
    if cfg.WEATHER_LAT and cfg.WEATHER_LON:
        return cfg.WEATHER_LAT, cfg.WEATHER_LON, "settings"
    return None


async def weather_loop():
    """Keep a forecast on flash while we have a route out.

    Runs on its own task rather than inside the poll loop: a slow or dead
    forecast service must never delay a battery reading.
    """
    global _weather_due, _weather_last, _weather_pos, _weather_at, _place
    while True:
        try:
            where = _weather_where()
            if (where and where[2] == "settings" and _weather_pos and _weather_pos[2] == "gps"
                    and not _weather_fixed()):
                # the fix is lost (a tunnel, a garage): the van's last known
                # position still beats where it lives
                where = _weather_pos
            online = _wlan is not None and _wlan.isconnected()
            due = ticks_diff(ticks_ms(), _weather_due) >= 0
            if where and _weather_pos and not due:
                if where[2] == "gps" and _weather_pos[2] != "gps":
                    due = True               # the first fix: the van's own forecast now
                elif (_dist_m(where[:2], _weather_pos[:2]) > WEATHER_MOVED_M and
                      (_weather_at is None or
                       ticks_diff(ticks_ms(), _weather_at) > WEATHER_MIN_GAP_MS)):
                    due = True               # moved well away: within reason
            # and a route out: without one the name lookup holds the hub for seconds
            if where and online and due and _internet():
                lat, lon = where[0], where[1]
                try:
                    stalls.mark("forecast")
                    n = await weather.fetch(lat, lon)
                    _weather_last = ""
                    _weather_pos, _weather_at = where, ticks_ms()
                    print("weather: cached %d bytes for %s,%s (%s)" % (n, lat, lon, where[2]))
                    # and what the place is called - only now, with the forecast,
                    # so no more often than the forecast itself (place.py)
                    _place = None
                    if where[2] == "gps":
                        try:
                            import place
                            stalls.mark("place name")
                            _place = await place.lookup(lat, lon)
                            print("place:", _place)
                        except Exception as e:
                            print("place lookup failed:", type(e).__name__, e)
                    _weather_due = ticks_ms() + cfg.WEATHER_REFRESH_MIN * 60000
                except Exception as e:
                    _weather_last = "%s: %s" % (type(e).__name__, e)
                    print("weather fetch failed:", _weather_last)
                    _weather_due = ticks_ms() + 300000     # try again in 5 min
        except Exception as e:
            print("weather loop error:", e)
        await asyncio.sleep_ms(20000)


def api_weather_meta():
    m = weather.cached_meta()
    online = _wlan is not None and _wlan.isconnected()
    gpsd = bool(_weather_pos and _weather_pos[2] == "gps")
    out = {"configured": bool(_weather_where()),
           "online": online, "error": _weather_last,
           # so Settings can say plainly where the forecast is for, and why:
           # mode is the choice (auto/fixed), gps_fix whether there is one now,
           # from how the fixed location was filled in
           "mode": getattr(cfg, "WEATHER_SOURCE", "auto"),
           "from": getattr(cfg, "WEATHER_FROM", ""),
           "gps_fix": _gps_fresh() is not None,
           "fixed_place": cfg.WEATHER_PLACE,
           # a forecast for the van's GPS position is named as such: the
           # Settings place name is where the van lives, not where it is
           "place": (("Near " + _place) if _place else "Where the van is (GPS)")
                    if gpsd else cfg.WEATHER_PLACE,
           "source": "gps" if gpsd else "settings"}
    if m:
        out["fetched"] = m[0]
        out["lat"], out["lon"] = m[1], m[2]
        if _rtc_ok():
            age = int(time.time()) - m[0]
            out["age_s"] = age if age >= 0 else 0
            out["stale"] = out["age_s"] > cfg.WEATHER_STALE_H * 3600
    return out


# ---- I2C peripherals: display and levelling sensor -------------------------
# Both share one bus. Neither is essential, so a missing or faulty device is
# reported and stepped over rather than being allowed to stop the hub.
_i2c = None
_i2c_devices = []


def _board_power_up():
    """Release any peripherals the board holds off at reset.

    The Pico W powers its headers straight from 3V3 and has nothing to do here.
    Other boards are not so simple: the Heltec V3's display sits on a switched
    Vext rail and is held in reset until a GPIO releases it, so an I2C scan
    finds an empty bus until both are dealt with.

    Both are optional config, so this does nothing at all on a board that does
    not declare them - which keeps one firmware working on both.
    """
    vext = getattr(cfg, "BOARD_VEXT_PIN", None)
    rst = getattr(cfg, "BOARD_OLED_RESET_PIN", None)
    if vext is None and rst is None:
        return
    try:
        from machine import Pin
        if vext is not None:
            on = 0 if getattr(cfg, "BOARD_VEXT_ACTIVE_LOW", True) else 1
            Pin(vext, Pin.OUT).value(on)
            time.sleep_ms(50)
        if rst is not None:
            p = Pin(rst, Pin.OUT)
            p.value(0)
            time.sleep_ms(20)
            p.value(1)
            time.sleep_ms(50)
        print("board: peripheral power and reset released")
    except Exception as e:
        print("board power-up failed:", e)


def i2c_up():
    global _i2c, _i2c_devices
    _board_power_up()
    try:
        from machine import Pin, I2C
        _i2c = I2C(0, scl=Pin(cfg.I2C_SCL_PIN), sda=Pin(cfg.I2C_SDA_PIN),
                   freq=cfg.I2C_FREQ)
        _i2c_devices = _i2c.scan()
        print("i2c: found", [hex(a) for a in _i2c_devices])
    except Exception as e:
        print("i2c bus failed:", e)
        return
    # The OLED is retired (the van display took its place), but its controller
    # keeps showing its last picture for as long as it has power: tell it to
    # switch off. One command, no driver.
    oled = getattr(cfg, "OLED_ADDR", 0x3C)
    if oled in _i2c_devices:
        try:
            _i2c.writeto(oled, bytes((0x00, 0xAE)))   # control byte: command; 0xAE: display off
            print("i2c: retired OLED switched off")
        except OSError as e:
            print("i2c: could not switch the OLED off:", e)
    if cfg.LEVEL_ENABLED and level.find(_i2c_devices) is not None:
        print("i2c: levelling sensor %s at 0x%02x" % (level.CHIP, level.ADDR),
              "ready" if level.init(_i2c) else "failed")
    elif cfg.LEVEL_ENABLED:
        print("i2c: no levelling sensor (MPU-6050 at 0x68, LIS3DH at 0x18/0x19)")


# ---- external GPS ----------------------------------------------------------
def gps_status():
    st = gps.status() if gps.available() else {"enabled": False, "fix": False,
                                               "error": gps._err}
    st["configured"] = bool(getattr(cfg, "GPS_ENABLED", False))
    st["uart"] = getattr(cfg, "GPS_UART", 1)
    st["tx_pin"] = getattr(cfg, "GPS_TX_PIN", 4)
    st["rx_pin"] = getattr(cfg, "GPS_RX_PIN", 5)
    st["baud"] = getattr(cfg, "GPS_BAUD", 9600)
    return st


async def gps_loop():
    """Drain the receiver's UART and keep the latest fix.

    The NEO-7M sends a burst of sentences every second at 9600 baud. Reading a
    few times a second keeps the buffer clear; reading less often risks the
    UART's FIFO overflowing and sentences arriving in pieces.
    """
    if not getattr(cfg, "GPS_ENABLED", False):
        return
    # Two peripherals on one pin do not fail loudly - they corrupt each other,
    # and the symptom turns up somewhere else entirely. GP4/GP5 carry the
    # display and the levelling sensor on this board, and they are also a valid
    # UART1 pair, so this is an easy and expensive mistake to make.
    clash = {cfg.GPS_TX_PIN, cfg.GPS_RX_PIN} & {cfg.I2C_SCL_PIN, cfg.I2C_SDA_PIN}
    if clash:
        gps._err = ("GP%s is the I2C bus (display and levelling sensor) - "
                    "move the GPS to another UART pin"
                    % ", GP".join(str(p) for p in sorted(clash)))
        print("gps:", gps._err)
        return
    if not gps.init(cfg.GPS_UART, cfg.GPS_TX_PIN, cfg.GPS_RX_PIN, cfg.GPS_BAUD):
        return
    print("gps: listening on UART%d (rx GP%d)" % (cfg.GPS_UART, cfg.GPS_RX_PIN))
    period = max(100, int(getattr(cfg, "GPS_POLL_MS", 250)))
    clock_at = None
    while True:
        try:
            gps.poll()
            # The clock from the satellites: at once if it was never set (no
            # internet, so no NTP - on the van's own hotspot, say), then hourly
            # to keep it true. The display takes its time from here.
            if clock_at is None or not _rtc_ok() or ticks_diff(ticks_ms(), clock_at) > 3600000:
                if _clock_from_gps():
                    clock_at = ticks_ms()
        except Exception as e:
            print("gps loop error:", e)
        await asyncio.sleep_ms(period)


def _clock_from_gps():
    """Set the RTC (UTC, as NTP sets it) from a fresh fix. True if it was."""
    st = gps.status()
    ts = st.get("ts_utc")
    if not (st.get("fix") and not st.get("stale") and ts and len(ts) >= 6 and ts[0] >= 2024):
        return False
    t = time.mktime((ts[0], ts[1], ts[2], ts[3], ts[4], ts[5], 0, 0))
    if _rtc_ok() and abs(time.time() - t) <= 2:
        return True                          # right already
    wd = time.localtime(t)[6]
    machine.RTC().datetime((ts[0], ts[1], ts[2], wd, ts[3], ts[4], ts[5], 0))
    print("RTC set from GPS: %04d-%02d-%02d %02d:%02d:%02d UTC" % tuple(ts[:6]))
    return True


# ---- levelling API ---------------------------------------------------------
def api_level(max_age_ms=0):
    st = level.read(max_age_ms)
    st["enabled"] = bool(cfg.LEVEL_ENABLED)
    st["present"] = level.ADDR in _i2c_devices
    st["sensor"] = level.CHIP
    return st


def api_level_only():
    """Just the levelling state, for the Level page to poll.

    /api/data is about 2.9 KB and carries the battery, charger, heater, weather
    and history summary with it. The Level page needs none of that, and sending
    it several times a second to drive one bubble is most of the cost of a fast
    refresh. This is a few hundred bytes.

    The small cache age lets two phones watching the vial share measurements
    without either seeing a stale figure.
    """
    # A very generous cache age: the background sampler keeps this fresh, and a
    # request must never be the thing that triggers a measurement.
    st = api_level(600000) if cfg.LEVEL_ENABLED else None
    # If the sampler has stopped, say so rather than serving its last reading
    # indefinitely: a bubble that has quietly stopped moving is worse than one
    # that admits it is not reading.
    if st is not None:
        age = level.age_ms()
        st["age_ms"] = age
        period = max(200, int(getattr(cfg, "LEVEL_REFRESH_MS", 500)))
        if age is not None and age > period * 5 + 2000:
            st["ok"] = False
            st["error"] = "the sensor has stopped being read (%.0f s ago)" % (age / 1000.0)
    return {"level": st,
            "level_scale": {"ok": getattr(cfg, "LEVEL_OK_DEG", 1.0),
                            "warn": getattr(cfg, "LEVEL_WARN_DEG", 20.0),
                            "max": getattr(cfg, "LEVEL_MAX_DEG", 30.0)},
            "views": {"level": getattr(cfg, "LEVEL_VIEW", "bubble")},
            "van": _van_size(),
            "refresh_ms": max(200, int(getattr(cfg, "LEVEL_REFRESH_MS", 500)))}


async def level_cmd(obj):
    action = obj.get("action")
    if action == "calibrate":
        return level.calibrate()
    if action == "clear":
        return level.clear_cal()
    if action == "clear_log":
        return level.clear_log()
    return {"ok": False, "error": "unknown action"}


# ---- factory reset: hold the board's BOOT button ---------------------------
# Held for RESET_HOLD_S with the hub running - not while powering it up, which
# starts the chip's own firmware-loading mode instead - the hub forgets
# everything it has been told and restarts as first installed: settings.json
# (networks, devices, the password, the display's pairing, names, alerts and
# the rest), the levelling calibration, relays, timers, guard, frost
# protection, storage mode and every log. Kept: its software (*.py, *.mpy),
# the pages and the display's update (folders), and config.py, whose values it
# falls back to. A countdown on the buzzer and light from 2 s, so a press is
# never mistaken for it; letting go cancels. No password: having a hand on the
# board is the permission, as with a router's reset button.
RESET_HOLD_S = 10


def factory_wipe():
    import os
    for name in os.listdir():
        if name.endswith(".py") or name.endswith(".mpy"):
            continue                        # the software, and config.py
        try:
            if os.stat(name)[0] & 0x4000:
                continue                    # a folder: www/, dispfw/
            os.remove(name)
            print("factory reset: removed", name)
        except OSError as e:
            print("factory reset: could not remove", name, e)


def _reset_show(v):
    # indicate is imported by the start-up code, not here: look it up, as
    # naming it directly failed on the hub (NameError), stopping this loop
    ind = sys.modules.get("indicate")
    if ind is not None:                 # a board without buzzer and light: counted silently
        ind.reset_hold = v


async def reset_button_loop():
    try:
        import rp2
        rp2.bootsel_button()
    except Exception as e:
        print("factory reset button unavailable:", e)
        return
    held = 0
    while True:
        await asyncio.sleep_ms(100)
        if rp2.bootsel_button():
            held += 100
            if held >= 2000:
                if held == 2000:
                    print("factory reset: counting down - let go of BOOT to cancel")
                _reset_show(held)
            if held >= RESET_HOLD_S * 1000:
                _reset_show("go")
                print("factory reset: clearing")
                factory_wipe()
                await asyncio.sleep_ms(1500)
                machine.reset()
        else:
            if held >= 2000:
                print("factory reset: cancelled")
            held = 0
            _reset_show(None)


# ---- main loops ----------------------------------------------------------
async def poll_loop():
    global _mem_free, _mem_low
    while True:
        try:
            stalls.mark("bluetooth")
            fresh = await ble_hub.poll_all(state) or {}
            # state now holds the last good reading even if this cycle failed -
            # only fold a genuinely fresh one into stats, history and automation
            if fresh.get("battery"):
                _update_stats(state["battery"])
                _record_history(state["battery"])
            _derive()
            _today_tick()
            _alert_tick()          # once per cycle, so the debounce means seconds
            await _timers_check()
            await _guard_check()
            if fresh.get("battery"):
                await _autoheat_check()
                await _storage_check()
            stalls.mark("wifi check")
            await _wifi_check()
            b = state["battery"]; r = state["renogy"]; v = state["victron"]
            print("poll: batt=%s ren=%s vic=%s" % (
                ("%.2fV %d%%" % (b.get("voltage", 0), b.get("soc", 0))) if b.get("connected") else "off",
                ("%.1fA" % r.get("charge_a", 0)) if r.get("connected") else "off",
                ("%.0fW" % (v.get("power_w") or 0)) if v.get("connected") else "off"))
        except Exception as e:
            # Surfaced in /api/data: a swallowed error here silently stops
            # history, the storage keeper and the alert tick while the battery
            # still looks fresh, which is very hard to spot from outside.
            global _poll_error, _poll_errors
            _poll_error = "%s: %s" % (type(e).__name__, e)
            _poll_errors += 1
            print("poll error:", _poll_error)
        gc.collect()
        _mem_free = gc.mem_free()
        _mem_low = _mem_free if _mem_low is None else min(_mem_low, _mem_free)
        await asyncio.sleep_ms(cfg.POLL_PERIOD_MS)


def _auto_energy(mains):
    """The fuel "auto" means: the element on a live hook-up, diesel otherwise.

    Which element setting is HEATER_AUTO_ELECTRIC (default 3, Electric 1 - the
    lower stage, least likely to trip a small campsite hook-up that the battery
    charger is also drawing from). Diesel when the hook-up is absent or simply
    unknown, so this fails towards the fuel that always works."""
    e = getattr(cfg, "HEATER_AUTO_ELECTRIC", 3)
    if e not in (1, 2, 3, 4):
        e = 3
    return e if mains else 0


async def heater_cmd(obj):
    action = obj.get("action")
    if action not in ("off", "air", "water", "combi", "vent", "read"):
        return {"ok": False, "error": "unknown action"}
    # The mains charger reporting is the evidence that a 230 V hook-up is
    # live. The heater's own AC flag claims mains with nothing plugged in,
    # so it must not be used to decide whether the element may run.
    v = _snapshot("victron", "victron_seen", cfg.VICTRON_STALE_MS)
    mains = bool(v.get("connected"))
    energy = obj.get("energy")
    # "auto" is decided here, once, when a heat command is sent - never in the
    # background: connecting to the heater unprompted makes it drop its burn
    # (see HEATER_POLL). Off and ventilate carry the heater's own setting.
    auto = energy == "auto"
    if auto:
        energy = _auto_energy(mains) if action in ("air", "water", "combi") else None
    if energy is not None:
        try:
            energy = int(energy)
        except (TypeError, ValueError):
            return {"ok": False, "error": "bad energy source"}
        if energy not in (0, 1, 2, 3, 4):
            return {"ok": False, "error": "bad energy source"}
    # a page auto-refresh sets bg - don't let it hog the radio on a bad link
    budget = 2 if obj.get("bg") else 6
    # Connecting to the heater needs a contiguous block of about a kilobyte,
    # and with a large module loaded the heap can be fragmented enough that there is
    # none even with 30 KB free. Tidying first, and once more on a
    # MemoryError, gets it; a command lost to that is a heater left running.
    for attempt in (0, 1):
        try:
            gc.collect()
            res = await ble_hub.command_heater(
                action, obj.get("temp", 20), obj.get("water", 2), obj.get("level", 1), energy,
                budget, mains)
            break
        except MemoryError:
            if attempt:
                return {"ok": False, "error": "the hub is short of memory - please try again"}
            await asyncio.sleep_ms(200)
        except Exception as e:              # surface to the UI instead of a dropped socket
            return {"ok": False, "error": "%s: %s" % (type(e).__name__, e)}
    if auto and energy is not None:
        res["energy_auto"] = ("hook-up live - electric" if energy else
                              "no hook-up - diesel")
    if res.get("ok"):                       # reflect the fresh reading on the dashboard
        # Reads often come back with the status frame but no sensor frame, and a
        # channel with no probe value reports None - neither should wipe out a
        # good reading we already have.
        h = dict(state["heater"]) if state["heater"].get("connected") else {}
        h["connected"] = True
        for k in ("product", "firmware"):
            if k in res:
                h[k] = res[k]
        for part in ("status", "sensors"):
            for k, v in (res.get(part) or {}).items():
                if v is not None:
                    h[k] = v
        state["heater"] = h
        state["heater_seen"] = ticks_ms()
        _heater_save()
    return res


async def main():
    settings.apply()             # saved settings override the factory defaults
    _storage_load()
    _heater_load()
    i2c_up()
    ip = await wifi_up()
    _hist_boot()
    _ah_load()
    _today_load()
    _timers_load()
    _disp_load()
    _relays_init()
    try:
        import indicate
        print("indicators:", indicate.init(cfg))
    except Exception as e:                 # a missing buzzer is no reason to stop
        indicate = None
        print("indicators unavailable:", e)
    _guard_load()
    handler = httpd.make_handler(api_data, api_history, heater_cmd, cfg.API_TOKEN, autoheat_set,
                                 settings_get, settings_set, scan_ble, scan_wifi, reboot,
                                 api_weather_meta, bms_fet, storage_set, level_cmd,
                                 api_level_only, api_alerts, alerts_ack, alerts_test,
                                 timers_set, panic_set, display_get, display_set,
                                 guard_set, wifi_share, auth_ok, pair_code, pair_claim,
                                 switches_set, dispfw_seen, settings_reveal)
    await asyncio.start_server(handler, "0.0.0.0", cfg.HTTP_PORT)
    print("Dashboard: http://%s:%d/" % (ip or "0.0.0.0", cfg.HTTP_PORT))
    asyncio.create_task(weather_loop())
    asyncio.create_task(gps_loop())
    asyncio.create_task(level_loop())
    asyncio.create_task(discovery_loop())
    stalls.set_wifi_probe(lambda: (_sta_joined(), _ap_active()))
    asyncio.create_task(stalls.watch())
    if indicate is not None and indicate.present():
        asyncio.create_task(indicate.chirp())
        asyncio.create_task(indicate.loop(_indicator_state))
    asyncio.create_task(reset_button_loop())
    await poll_loop()


# Self-heal: an unexpected crash restarts the board rather than parking it at
# the REPL until someone power-cycles it. Ctrl-C from mpremote still works
# (KeyboardInterrupt is not an Exception).
try:
    asyncio.run(main())
except Exception as e:
    print("FATAL:", e, "- restarting in 5 s")
    time.sleep(5)
    import machine
    machine.reset()
finally:
    asyncio.new_event_loop()
