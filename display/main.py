# Van display: shows the hub's data on the Freenove FNK0104S and sends heater
# commands to it. See ui.py for the pages and hub.py for the network side.
#
# Four jobs run side by side under asyncio: keep WiFi up, poll the hub, watch
# the touch panel, and redraw when something changed. None of them blocks, so
# a slow hub never freezes the screen.

import asyncio
import gc
import json
import time

import machine
import network

import config as cfg
import gfx
import ui
from ft6336 import FT6336
from hub import Hub, HubError
from st7796 import ST7796

try:
    import otaboot                       # installed over USB; see boot.py
except ImportError:
    otaboot = None
_boot_ms = time.ticks_ms()

CAL_FILE = "touch_cal.json"
PREFS_FILE = "display_prefs.json"    # brightness and sound, as set on the screen
LEARNED_FILE = "wifi_learned.json"   # the hub's networks, as it last told us
HUB_NET_FILE = "wifi_hubnet.json"    # where the hub was last seen: its network and its hotspot
KEY_FILE = "hub_key.txt"             # this display's key, from pairing with the hub


def _load_key():
    try:
        with open(KEY_FILE) as f:
            return f.read().strip()
    except OSError:
        return ""


def c(name, default):
    return getattr(cfg, name, default)


# ---- hardware -----------------------------------------------------------------

disp = ST7796(rotation=c("ROTATION", 1))
fb = disp.fb
fonts = gfx.Fonts("fonts")
touch = FT6336()
boot_btn = machine.Pin(0, machine.Pin.IN, machine.Pin.PULL_UP)
try:
    from sound import Sound
    sound = Sound(touch.i2c, volume=c("ALARM_VOLUME", 90),
                  click_level=c("SOUND_VOLUME", 60) / 100)
except Exception as e:                   # a silent display is still a display
    print("sound unavailable:", e)
    sound = None


def beep(name):
    if sound:
        sound.play(name)


# The RGB LED on the back of the board (a WS2812 on GPIO 42, from Freenove's
# sketches for this model), used as a status light you can see across the van.
try:
    import neopixel
    led = neopixel.NeoPixel(machine.Pin(42), 1)
except Exception as e:
    print("status LED unavailable:", e)
    led = None
_led_last = None


def led_set(rgb):
    global _led_last
    if led is None or rgb == _led_last:
        return
    led[0] = rgb
    led.write()
    _led_last = rgb


def splash(line1, line2=""):
    fb.fill(gfx.BG)
    fonts.lg.text(fb, line1, disp.width // 2, 120, gfx.TXT, gfx.BG, 1)
    if line2:
        fonts.md.text(fb, line2, disp.width // 2, 168, gfx.MUTED, gfx.BG, 1)
    disp.show()


# ---- touch calibration ---------------------------------------------------------

def _wait_tap():
    """Block until a finger goes down and comes up again; return the average
    raw position while it was down, which is steadier than any one sample."""
    while touch.raw():                          # wait for a clear panel first
        time.sleep_ms(20)
    pts = []
    while not pts:
        p = touch.raw()
        while p:
            pts.append(p)
            time.sleep_ms(20)
            p = touch.raw()
        time.sleep_ms(20)
    n = len(pts)
    return (sum(p[0] for p in pts) // n, sum(p[1] for p in pts) // n)


def _target(tx, ty, step, note=""):
    w = disp.width
    fb.fill(gfx.BG)
    fonts.lg.text(fb, "Touch setup", w // 2, 100, gfx.TXT, gfx.BG, 1)
    fonts.md.text(fb, "Tap the centre of the target (%d of 3)" % step,
                  w // 2, 146, gfx.MUTED, gfx.BG, 1)
    if note:
        fonts.sm.text(fb, note, w // 2, 176, gfx.AMBER, gfx.BG, 1)
    fb.ellipse(tx, ty, 16, 16, gfx.BRAND)
    fb.ellipse(tx, ty, 4, 4, gfx.BRAND, True)
    fb.hline(tx - 24, ty, 48, gfx.BRAND)
    fb.vline(tx, ty - 24, 48, gfx.BRAND)
    disp.show()


# How far the check tap may land from its target. A finger is about 10 mm,
# which on this 4" panel is roughly 60 px; half of that still hits every button.
CAL_TOLERANCE_PX = 25


def calibrate(note=""):
    """Two taps to work out the mapping, and a third to check it. A setup done
    carelessly - or a tap that slipped - is caught here and repeated, rather
    than saved and left to make every button a near miss."""
    w, h = disp.width, disp.height
    targets = ((30, 30), (w - 30, h - 30))
    raws = []
    for i, (tx, ty) in enumerate(targets):
        _target(tx, ty, i + 1, note if i == 0 else "")
        raws.append(_wait_tap())
    cal = FT6336.solve(raws[0], raws[1], targets[0], targets[1], w, h)
    if cal is None:
        return calibrate("Those taps did not register properly - once more")
    # the check: a target the fit has not seen, in the middle of the screen
    cx, cy = w // 2 + 40, h // 2 + 50
    _target(cx, cy, 3)
    touch.cal = cal
    mx, my = touch.map(_wait_tap())
    miss = int(((mx - cx) ** 2 + (my - cy) ** 2) ** 0.5)
    if miss > CAL_TOLERANCE_PX:
        touch.cal = None
        return calibrate("The check tap was %d px out - let's do it again" % miss)
    with open(CAL_FILE, "w") as f:
        json.dump(cal, f)
    splash("Touch is set up", "Hold BOOT while powering up to redo it")
    time.sleep_ms(1500)
    return cal


def load_cal():
    try:
        with open(CAL_FILE) as f:
            cal = json.load(f)
        if cal[5] == disp.width and cal[6] == disp.height:
            return tuple(cal)
    except (OSError, ValueError, IndexError, KeyError):
        pass
    return None


def load_prefs():
    try:
        with open(PREFS_FILE) as f:
            p = json.load(f)
        return p if isinstance(p, dict) else {}
    except (OSError, ValueError):
        return {}


def save_prefs():
    try:
        with open(PREFS_FILE, "w") as f:
            json.dump({"brightness": view.brightness, "sound": view.sound_on,
                       "night": view.night_mode, "ambient": view.ambient,
                       "alarm_clock": view.alarm_clock, "dim": view.dim_on,
                       "offmode": view.offmode, "unsent": sorted(_unsent)}, f)
    except OSError as e:
        print("could not save preferences:", e)


_prefs = load_prefs()
# Shared settings changed here that the hub has not yet taken (share_task).
# Until it has, its older copy must not overwrite them here, and they are sent
# again whenever it can be reached - see share() for why.
_unsent = set(p for p in (_prefs.get("unsent") or []) if isinstance(p, str))
_bright = _prefs.get("brightness", c("BRIGHTNESS", 1.0))
_bright = 0.1 if _bright < 0.1 else 1.0 if _bright > 1.0 else _bright
disp.backlight(_bright)
if sound:
    sound.enabled = _prefs.get("sound", c("SOUND", True))
# Hold the BOOT button while powering up to redo the touch setup.
touch.cal = None if boot_btn.value() == 0 else load_cal()
if touch.cal is None:
    touch.cal = calibrate()


# ---- clock, from the hub -------------------------------------------------------

_clock_base = None           # (hub unix time, ticks_ms when it was received)


def now():
    """Unix time, carried forward from the hub's last report, or None."""
    if _clock_base is None:
        return None
    return _clock_base[0] + time.ticks_diff(time.ticks_ms(), _clock_base[1]) // 1000


# ---- the app -------------------------------------------------------------------

view = ui.UI(fb, fonts, pages=c("PAGES", None), now=now,
            tz=(c("UTC_OFFSET_MIN", 0), c("DST_EU", True)),
            mono=lambda: time.time(), buf=disp.buf)
view.brightness = _bright
view.van = (c("VAN_WHEELBASE_MM", 3640), c("VAN_TRACK_MM", 1777))
view.checklist = list(c("CHECKLIST", view.checklist))
view.night_mode = _prefs.get("night", c("NIGHT_MODE", "auto"))
if isinstance(_prefs.get("ambient"), dict):
    view.ambient.update({k: v for k, v in _prefs["ambient"].items() if k in view.ambient})
if isinstance(_prefs.get("alarm_clock"), dict):
    view.alarm_clock.update(_prefs["alarm_clock"])
view.dim_on = bool(_prefs.get("dim", True))
view.offmode = bool(_prefs.get("offmode", False))
view._logo_img()        # load the screensaver logo now, not in the middle of a redraw
view.sound_on = sound.enabled if sound else False
view.game = False           # Camp Crossing is running: it has the screen
view.dragging = False       # a page is being dragged: the drag draws the screen
hub = Hub(c("HUB_HOSTS", ["192.168.4.23"]), _load_key() or c("API_TOKEN", ""))
wlan = network.WLAN(network.STA_IF)

_last_touch = time.ticks_ms()
_resume = None              # the page to go back to on waking: (page, sub, logo)
_dim = False
_off = False
_last_ok = None
_result_at = None


def activity():
    """A touch. Returns True if the screen was dimmed, in which case the touch
    only wakes it and must do nothing else."""
    global _last_touch, _dim, _off, _resume
    _last_touch = time.ticks_ms()
    _radio_fast()
    woke = _dim or _off or view.saver
    if woke:
        _set_rest(False)
        if _off:
            disp.sleep(False)
        view.saver = False
        # back to the page that was showing before the screen went idle
        if _resume is not None:
            view.page, view.sub, view.logo_page = _resume
            _resume = None
        _dim = _off = False
        view.seconds = True
        disp.backlight(min(view.brightness, c("NIGHT_BRIGHTNESS", 0.3))
                       if view.night else view.brightness)
        view.dirty = True
    return woke


# ---- low power while resting ----------------------------------------------------------
# Dimmed or off, nobody is looking: the backlight is down, and the processor
# and the radio need not run flat out either. 80 MHz is plenty for polling the
# hub and redrawing a dim clock once a minute, and Wi-Fi power saving lets the
# radio sleep between the hub's answers. Waking (a touch, an alarm) puts both
# back before anything is drawn. The peripherals keep their own clock, so the
# screen, touch and sound are unaffected.
_rest = False
# The radio's power saving has it doze between the access point's beacons, so
# an answer from the hub waits for it to wake: measured on this display, a
# request took 0.63 s awake and 0.84 s resting, against 0.12 s with power
# saving off. For the half minute after a touch it is off, so a tap - a relay,
# the heater - is answered at once; then back to saving, as the screen only
# polls. The cost is a few tens of mA for those seconds.
_fast_until = None           # ticks_ms: no radio power saving until then
_pm_now = None


def _pm_apply():
    global _pm_now
    name = ("PM_NONE" if _fast_until is not None else
            "PM_POWERSAVE" if _rest else "PM_PERFORMANCE")
    if name == _pm_now:
        return
    try:
        pm = getattr(network.WLAN, name, None)
        if pm is not None:
            wlan.config(pm=pm)
            _pm_now = name
    except Exception as e:
        print("wifi power saving:", e)


def _radio_fast():
    """A touch: the radio fully awake for the next TOUCH_RADIO_S seconds."""
    global _fast_until
    _fast_until = time.ticks_add(time.ticks_ms(), c("TOUCH_RADIO_S", 30) * 1000)
    _pm_apply()


def _set_rest(on):
    global _rest
    if on == _rest:
        return
    _rest = on
    print("power:", "resting" if on else "awake")
    try:
        machine.freq(c("REST_CPU_HZ", 80000000) if on else c("CPU_HZ", 240000000))
    except Exception as e:
        print("cpu speed:", e)
    _pm_apply()


async def power_task():
    global _fast_until
    _pm_apply()                          # as it should be from the start, not the chip's default
    while True:
        # an update on trial: still alive (otaboot's hang timer), and after a
        # minute of running normally, kept
        if otaboot:
            otaboot.feed()
            if otaboot.on_trial() and time.ticks_diff(time.ticks_ms(), _boot_ms) > 60000:
                otaboot.confirm()
        if c("REST_POWER_SAVE", True):
            _set_rest(_dim or _off)
        if _fast_until is not None and time.ticks_diff(time.ticks_ms(), _fast_until) > 0:
            _fast_until = None
            _pm_apply()
        await asyncio.sleep(2)


# ---- WiFi -----------------------------------------------------------------------------
# Networks come from config.py and from the hub itself: while connected, the
# display keeps a copy of the networks the hub knows and of its hotspot
# (learn_task), so when the hub is moved to one of them the display can
# follow. And if the hub stops answering on the network the display is on, it
# is somewhere else: after HUB_MISSING_S the display moves on to the next
# network - the hub's own hotspot among them, which reaches it wherever it is.
# The network it left is then passed over for HUB_AVOID_S, so the display keeps
# trying the hotspot rather than going straight back to sit two minutes on a
# network the hub is not on (which could miss a hotspot that was briefly down,
# round after round: tests/test_wifi_scenarios.py).
def _load_learned():
    try:
        with open(LEARNED_FILE) as f:
            n = json.load(f)
        return [(s, p) for s, p in n if isinstance(s, str) and isinstance(p, str)]
    except (OSError, ValueError, TypeError):
        return []


_learned = _load_learned()


def _load_hub_net():
    try:
        with open(HUB_NET_FILE) as f:
            n = json.load(f)
        return [n[0] if isinstance(n[0], str) else "", n[1] if isinstance(n[1], str) else ""]
    except (OSError, ValueError, TypeError, IndexError):
        return ["", ""]


# The network the hub said it was on, and its hotspot's name, from its own
# status (/api/data "stats"). Looking for the hub, these come first: where it
# was, then its hotspot - which, with the hub's "hotspot always on", is there
# wherever else the hub has gone. Only then the rest, in order.
_hub_net = _load_hub_net()


def _note_hub_net(st):
    """Keep what the hub reports about its WiFi; written only on a change."""
    global _hub_net
    now = [st.get("wifi_ssid") or "", st.get("wifi_ap_ssid") or ""]
    if not now[0] and not now[1]:
        return
    now = [now[0] or _hub_net[0], now[1] or _hub_net[1]]
    if now != _hub_net:
        _hub_net = now
        try:
            with open(HUB_NET_FILE, "w") as f:
                json.dump(now, f)
        except OSError:
            pass


def _networks():
    """(ssid, password) to try, in order: the hub's own network and then its
    hotspot (as it last reported them), then config.py's, then the rest
    learned from the hub."""
    out, seen = [], set()
    known = list(c("WIFI_NETWORKS", [])) + _learned
    pw = {s: p for s, p in known}
    for ssid in _hub_net:
        if ssid and ssid in pw and ssid not in seen:
            seen.add(ssid)
            out.append((ssid, pw[ssid]))
    for ssid, p in known:
        if ssid and ssid not in seen:
            seen.add(ssid)
            out.append((ssid, p))
    return out


def _hub_missing():
    """True when every request to the hub has failed for HUB_MISSING_S."""
    f = hub.fail_since
    return f is not None and time.ticks_diff(time.ticks_ms(), f) > c("HUB_MISSING_S", 60) * 1000


_left_for_hub = {}       # ssid -> ticks_ms the display left it, the hub not being there


async def wifi_task():
    wlan.active(True)
    start = 0               # where in the list the next search begins
    while True:
        nets = _networks()
        if wlan.isconnected() and len(nets) > 1 and _hub_missing():
            try:
                cur = wlan.config("essid")
            except Exception:
                cur = None
            names = [n[0] for n in nets]
            start = (names.index(cur) + 1) if cur in names else 0
            if cur:
                _left_for_hub[cur] = time.ticks_ms()
            print("wifi: no hub on", cur, "- trying the next network")
            try:
                wlan.disconnect()
            except OSError:
                pass
            await asyncio.sleep_ms(500)
        if not wlan.isconnected():
            now = time.ticks_ms()
            for s in list(_left_for_hub):
                if time.ticks_diff(now, _left_for_hub[s]) > c("HUB_AVOID_S", 180) * 1000:
                    del _left_for_hub[s]
            order = [nets[(start + k) % len(nets)] for k in range(len(nets))]
            order = [n for n in order if n[0] not in _left_for_hub]
            for ssid, pw in order:
                view.link, view.link_note = "wifi", "Joining " + ssid
                view.dirty = True
                try:
                    wlan.disconnect()
                    wlan.connect(ssid, pw)
                except OSError:
                    pass
                for _ in range(30):
                    if wlan.isconnected():
                        break
                    await asyncio.sleep_ms(500)
                if wlan.isconnected():
                    print("wifi: joined", ssid)
                    hub.fail_since = None       # a fresh start on this network:
                    hub.host = None             # search for the hub straight away
                    hub._search_at = None       # rather than first retrying where it was
                    view.link, view.link_note = "stale", "Finding the hub"
                    view.dirty = True
                    break
                print("wifi: could not join", ssid)
            else:
                # Nothing joined. Passing a network over means the hub's hotspot
                # is being looked for, so look again soon.
                view.link, view.link_note = "wifi", "Looking for the hub" if _left_for_hub else "No WiFi"
                view.dirty = True
                start = 0
                await asyncio.sleep(5 if _left_for_hub else 20)
                continue
            start = 0
        await asyncio.sleep(5)


# ---- pairing with the hub -------------------------------------------------------------
# With a password set on the hub, this display pairs rather than typing one:
# it shows a 6-digit code, and asks the hub for a key every few seconds; the
# hub hands one over once the code has been typed into its Settings. Offered
# when the hub first refuses something; "Not now" holds it off for half an
# hour unless a button here is pressed that needs it.
_pair_running = False
_pair_quiet_until = None
_learn_now = False           # set by _take: fetch the hub's networks now


def start_pairing(asked):
    global _pair_running
    if _pair_running:
        return
    if not asked and _pair_quiet_until is not None and time.ticks_diff(_pair_quiet_until, time.ticks_ms()) > 0:
        return
    _pair_running = True
    asyncio.create_task(pair_task(asked))


def pair_cancel():
    global _pair_quiet_until
    _pair_quiet_until = time.ticks_add(time.ticks_ms(), 1800000)


async def pair_task(asked):
    global _pair_running
    import os
    code = "%06d" % (int.from_bytes(os.urandom(4), "big") % 1000000)
    view.pair = {"code": code}
    if asked:
        activity()
    view.dirty = True
    t0 = time.ticks_ms()
    try:
        while view.pair and time.ticks_diff(time.ticks_ms(), t0) < 180000:
            try:
                r = await hub.post("/api/pair/claim", {"code": code, "name": "display"})
                if r.get("ok") and r.get("key"):
                    with open(KEY_FILE, "w") as f:
                        f.write(r["key"])
                    hub.token = r["key"]
                    print("paired with the hub")
                    global _learn_now
                    _learn_now = True             # and fetch the networks with the new key
                    view.pair = {"code": code, "done": True}
                    view.dirty = True
                    beep("ok")
                    await asyncio.sleep(3)
                    break
            except (HubError, OSError) as e:
                print("pairing:", e)
            await asyncio.sleep(3)
    finally:
        if view.pair:
            view.pair = None
            view.dirty = True
        _pair_running = False


async def learn_task():
    """Keep a copy of the hub's networks: soon after finding it, then every
    half hour. Saved only when something changed. Needs the hub's API token in
    config.py if one is set on the hub."""
    global _learned
    await asyncio.sleep(30)
    while True:
        wait = 60
        if wlan.isconnected() and hub.host:
            try:
                r = await hub.get_private("/api/wifi/networks")
                nets = [(n.get("ssid"), n.get("password") or "") for n in r.get("networks") or []
                        if isinstance(n, dict) and n.get("ssid")]
                ap = r.get("ap") or {}
                if ap.get("ssid"):
                    nets.append((ap["ssid"], ap.get("password") or ""))
                if nets and nets != _learned:
                    _learned = nets
                    with open(LEARNED_FILE, "w") as f:
                        json.dump(nets, f)
                    print("wifi: learned", len(nets), "networks from the hub")
                wait = 1800
            except HubError as e:
                print("wifi: could not learn the hub's networks:", e)
                wait = 600
                if "password" in str(e):
                    start_pairing(False)          # not paired: offer it, quietly
                    wait = 60
            except OSError as e:
                print("wifi: could not save the hub's networks:", e)
                wait = 1800
        global _learn_now
        _learn_now = False
        for _ in range(wait):
            await asyncio.sleep(1)
            if _learn_now:
                break


def _take(d):
    """A fresh /api/data from the hub."""
    global _clock_base, _last_ok, _take_sig
    old = view.data or {}
    view.data = d
    _last_ok = time.ticks_ms()
    _note_hub_net(d.get("stats") or {})
    if d.get("updated"):
        _clock_base = (int(d["updated"]), time.ticks_ms())
    h = d.get("heater") or {}
    if h.get("at") != (old.get("heater") or {}).get("at"):
        view.adopt_heater(h)
    if d.get("alerts") and not old.get("alerts") and c("WAKE_ON_ALERT", True):
        activity()
    # A password just set on the hub, or its WiFi settings changed: fetch the
    # networks now, not at the next half-hourly look - the first means this
    # display should pair, the second that a hotspot password may be changing.
    global _learn_now
    if old and ((d.get("auth_set") and not old.get("auth_set"))
                or d.get("wifi_rev") != old.get("wifi_rev")):
        _learn_now = True
    _engine_watch()
    pn = d.get("panel")
    if sound and isinstance(pn, dict):
        sound.levels(pn.get("click_volume", 100), pn.get("alarm_volume", 100))
    was = view.link
    view.link, view.link_note = "ok", ""
    # redraw only if something on the page may have changed
    sig = view.page_sig()
    if sig is None or sig != _take_sig or was != "ok":
        view.dirty = True
    _take_sig = sig


_take_sig = None
_engine_on = False
_engine_since = None        # ticks_ms the engine started
_park_at = None             # ticks_ms to bring up the Level page, after a drive


def _engine_watch():
    """When the engine starts and the Drive checklist is not complete, put it
    on screen. When it stops after a drive, and stays stopped for 20 s, the
    Level page, for parking level. (The hub empties the list once the van has
    been parked a while.)"""
    global _engine_on, _engine_since, _park_at
    running = view.engine_running()
    ms = time.ticks_ms()
    if running and not _engine_on:
        _engine_since, _park_at = ms, None
        if "drive" in view.pages and not view.drive_ready() and not view.alarm():
            _show("drive")
    elif not running and _engine_on:
        # a drive, not a quick start and stop in the yard
        if _engine_since is not None and time.ticks_diff(ms, _engine_since) > 120000:
            _park_at = time.ticks_add(ms, 20000)
    if _park_at is not None and not running and time.ticks_diff(ms, _park_at) >= 0:
        _park_at = None
        if "level" in view.pages and not view.alarm() and not view.panic:
            _show("level")
    _engine_on = running


def _show(page):
    global _resume
    _resume = None
    activity()
    view.saver = view.logo_page = False
    view.sub = None
    view.page = view.pages.index(page)
    view.dirty = True


_drev = None                # the hub's settings revision this screen has
_share_at = None            # ticks_ms of the last change made on this screen
_fetching = False


def share(*parts):
    """A setting the hub keeps for every screen changed here: send it.

    It stays on the waiting list (_unsent) until the hub has taken it. A send
    used to be tried three times in two seconds and then dropped without a
    word: the hub, unreachable for a moment (restarting, changing network),
    kept its old copy, and the next change to any shared setting - a switch
    from a phone, the Drive list clearing itself after a stop - brought that
    old copy back here. "Dim off" turned itself back on that way."""
    global _share_at
    _share_at = time.ticks_ms()
    if not set(parts) <= _unsent:
        _unsent.update(parts)
        save_prefs()
    asyncio.create_task(share_task(parts))


async def share_task(parts):
    global _drev
    for _ in range(3):
        try:
            # the values as they are now: a later change may have moved them
            body = {p: view.share(p) for p in parts}
            r = await hub.post("/api/display", body)
            if r.get("ok"):
                _drev = r["display"]["rev"]
                if view.data is not None:
                    view.data["wake"] = r.get("wake")
                view.dirty = True
            else:
                print("share: the hub refused", parts, r.get("error"))
            # taken (or refused - sending it again would not help): off the
            # list, unless it changed again here while this was on its way
            done = [p for p in parts if p in _unsent and view.share(p) == body[p]]
            if done:
                _unsent.difference_update(done)
                save_prefs()
            return
        except HubError:
            await asyncio.sleep_ms(700)
    print("share: the hub did not take", parts, "- sent again when it answers")


def _sharing():
    """Changed here moments ago: the hub's copy may not have it yet."""
    return _share_at is not None and time.ticks_diff(time.ticks_ms(), _share_at) < 3000


async def fetch_shared():
    global _fetching
    _fetching = True
    try:
        r = await hub.get("/api/display", timeout_ms=4000)
        if not _sharing():
            adopt_shared(r.get("display") or {})
            if view.data is not None:
                view.data["wake"] = r.get("wake")
    except HubError:
        pass
    _fetching = False


def adopt_shared(d):
    """The hub's copy, changed on a phone perhaps: show it here - all but the
    settings changed here that the hub has not taken yet (_unsent)."""
    global _drev, _ota_req
    if _unsent:
        d = {k: v for k, v in d.items() if k not in _unsent}
    if isinstance(d.get("ambient"), dict):
        view.ambient.update(d["ambient"])
    if isinstance(d.get("alarm_clock"), dict):
        view.alarm_clock.update(d["alarm_clock"])
    k = d.get("kitchen")
    if isinstance(k, dict):
        view.kt_end = k.get("end") or None
        view.kt_paused = k.get("paused") or None
        view.kt_set = k.get("set") or view.kt_set
    if isinstance(d.get("ticks"), list):
        view.ticks = set(d["ticks"])
    b = d.get("brightness")
    if isinstance(b, (int, float)) and abs(b - view.brightness) > 0.001:
        view.brightness = b
        backlight_now()
    if d.get("night") in ("auto", "on", "off") and d["night"] != view.night_mode:
        view.night_mode = d["night"]
        apply_night()
    if "dim" in d:
        view.dim_on = bool(d["dim"])
    if "status_led" in d:
        view.status_led = bool(d["status_led"])
    # "Update the display now" in the hub's settings
    r = d.get("ota_req")
    if r is not None:
        if _ota_req is not None and r != _ota_req:
            _ota_now.set()
        _ota_req = r
    if isinstance(d.get("switches"), list) and len(d["switches"]) == len(view.switches):
        view.switches = [bool(x) for x in d["switches"]]
    if "offmode" in d and bool(d["offmode"]) != view.offmode:
        view.offmode = bool(d["offmode"])
        if view.offmode and not _off:
            screen_off(sticky=False)             # set from a phone: dark now
        elif not view.offmode and _off:
            activity()
    _drev = d.get("rev")
    save_prefs()
    view.dirty = True


def backlight_now():
    if not (_dim or _off or view.panic):
        disp.backlight(min(view.brightness, c("NIGHT_BRIGHTNESS", 0.3))
                       if view.night else view.brightness)


_poll_misses = 0


async def poll_task():
    global _poll_misses
    while True:
        if view.game:
            await asyncio.sleep_ms(500)
            continue
        if wlan.isconnected():
            try:
                _take(await hub.get("/api/data"))
                _poll_misses = 0
            except HubError as e:
                print("hub:", e)
                # the figures on screen are seconds old: say so only when the
                # hub has really gone, not on one slow answer
                _poll_misses += 1
                if _poll_misses >= 3:
                    view.link, view.link_note = "down", "Hub not answering"
                    view.dirty = True
            # No gc.collect() here. It took 50 ms, every 5 s, which stalled the
            # animations; with megabytes of PSRAM free, MicroPython collects
            # on its own far less often.
        # Nobody is reading the figures behind the screensaver or a dimmed
        # screen, so ask the hub less often then (alarms have their own poll).
        resting = _dim or _off or view.saver
        wait = c("POLL_DIM_S", 30) if resting else c("POLL_S", 5)
        # sleep in short steps so waking the screen brings fresh data promptly
        t0 = time.ticks_ms()
        while time.ticks_diff(time.ticks_ms(), t0) < wait * 1000:
            await asyncio.sleep_ms(250)
            if resting and not (_dim or _off or view.saver):
                break


async def level_task():
    """Faster readings while the Level page is on screen, from the hub's small
    dedicated endpoint rather than the whole dashboard."""
    while True:
        if (view.pages[view.page] == "level" and not (_dim or _off or view.saver)
                and wlan.isconnected()):
            try:
                r = await hub.get("/api/level", timeout_ms=3000)
                view.level = r.get("level")
                if view.sub == "gforce":
                    view.gforce_sample(view.level)
                view.dirty = True
            except HubError:
                pass
        else:
            view.level = None
        # the G-force view wants it brisk; the hub measures every few hundred ms
        await asyncio.sleep_ms(c("GFORCE_POLL_MS", 300) if view.sub == "gforce"
                               else c("LEVEL_POLL_MS", 1000))


_ack_pending = {}            # alert id -> ticks_ms we cleared it, until the hub agrees


async def alert_task():
    """Ask the hub for alerts every couple of seconds - dimmed or not, on any
    page - from its small /api/alerts endpoint rather than the whole dashboard.
    A new alarm wakes the screen."""
    while True:
        if wlan.isconnected():
            try:
                r = await hub.get("/api/alerts", timeout_ms=4500)
                before = view.alarm()
                was = (repr(view.alerts), view.alert_sound)
                alerts = r.get("alerts") or []
                ms = time.ticks_ms()
                for a in alerts:
                    # cleared here a moment ago and the hub has not caught up:
                    # do not let one stale answer start the alarm again
                    t = _ack_pending.get(a.get("id"))
                    if t is not None and time.ticks_diff(ms, t) < 6000:
                        a["acked"] = True
                view.alerts = alerts
                view.alert_sound = bool(r.get("sound", True))
                # changes the hub has not taken yet: it answers now, so send them
                if _unsent and not _sharing():
                    share(*sorted(_unsent))
                # the shared settings moved: fetch them, unless the move is ours
                dr = r.get("drev")
                if dr is not None and dr != _drev and not _sharing() and not _fetching:
                    if dr == 0 and _drev is None:
                        # a hub that has never had them: this screen's are the ones
                        share("ambient", "alarm_clock", "kitchen", "ticks",
                              "brightness", "night", "dim", "offmode")
                    else:
                        asyncio.create_task(fetch_shared())
                # Panic follows the hub - unless we changed it moments ago and
                # the hub has not caught up, when its old answer is ignored.
                hp = r.get("panic")
                if hp is not None:
                    fresh = (_panic_sent is not None
                             and time.ticks_diff(time.ticks_ms(), _panic_sent[1]) < 8000
                             and _panic_sent[0] != hp)
                    if not fresh:
                        if hp and not view.panic:
                            start_panic(local=False)
                        elif not hp and view.panic:
                            stop_panic(local=False)
                now_on = view.alarm()
                if now_on and (not before or before.get("id") != now_on.get("id")):
                    print("alarm: sounding -", now_on.get("id"))
                    activity()
                elif before and not now_on:
                    print("alarm: stopped -", before.get("id"))
                # Redraw only when the alerts changed. This ran every 2 s and
                # redrew the whole page each time, which is what kept pausing
                # the animated pages.
                if (repr(view.alerts), view.alert_sound) != was:
                    view.dirty = True
            except HubError:
                pass
        # every couple of seconds; a little less often while the screen is dim
        # or off, to let the radio sleep - an alarm still wakes it within 5 s
        await asyncio.sleep_ms(c("ALERT_POLL_REST_MS", 5000) if (_dim or _off)
                               else c("ALERT_POLL_MS", 2000))


async def alarm_task():
    """Sound the alarm while an alert is uncleared. It follows the hub's alert
    sound setting, not the display's touch-sound switch: turning the clicks
    off must never silence a safety alarm."""
    while True:
        if sound and view.alert_sound and view.alarm() and not view.panic:
            sound.play("alarm", force=True)
            await asyncio.sleep_ms(2600)
        else:
            await asyncio.sleep_ms(250)


async def ack_task(alert_id):
    """Tell the hub, so every screen stops - the web pages too. Tried a few
    times: if the hub never hears, the next poll brings the alarm back, which
    is the safe way for that to fail."""
    _ack_pending[alert_id] = time.ticks_ms()
    for _ in range(3):
        try:
            await hub.post("/api/alerts/ack", {"id": alert_id})
            return
        except HubError:
            await asyncio.sleep_ms(700)


# The alarm's pattern, like a police light bar: two quick red flashes, two quick
# blue, repeating. (colour, milliseconds) steps; None is dark.
_BEACON = ((255, 0, 0), 60), (None, 50), ((255, 0, 0), 60), (None, 140),           ((0, 0, 255), 60), (None, 50), ((0, 0, 255), 60), (None, 140)


async def led_task():
    """Gold when all is well, amber when the battery is low, a slow white blink
    when the hub cannot be reached, and a red-and-blue beacon for an alarm
    nobody has cleared. Dimmed with the screen and off with it - except the
    beacon, which always runs at full brightness."""
    on = True
    while True:
        if (view.alarm() or view.panic) and c("LED", True):
            for rgb, ms in _BEACON:
                led_set(rgb or (0, 0, 0))
                await asyncio.sleep_ms(ms)
            continue
        if view.ac_ringing:
            # a sunrise: warm light fading up over the first minute
            k = min(1.0, (time.time() - (_ac_rang or time.time())) / 60) or 0.05
            led_set((int(255 * k), int(120 * k), int(30 * k)))
            await asyncio.sleep_ms(500)
            continue
        amb = view.ambient_rgb(time.ticks_ms() / 1000)
        if amb is not None and view.night:
            k = c("NIGHT_AMBIENT", 0.4)
            amb = (int(amb[0] * k), int(amb[1] * k), int(amb[2] * k))
        if amb is not None:
            led_set(amb)
            # the colour cycle needs small steps to look smooth
            await asyncio.sleep_ms(100 if view.ambient.get("cycle") else 500)
            continue
        on = not on
        # status_led: switched off in the shared settings (the Lights page)
        if not c("LED", True) or _off or not getattr(view, "status_led", True):
            rgb = (0, 0, 0)
        else:
            k = c("LED_BRIGHTNESS", 0.25) * (0.15 if (_dim or view.night) else 1.0)
            d = view.data or {}
            soc = d.get("soc") if d.get("connected") else None
            if view.link in ("down", "wifi"):
                base = (200, 200, 200) if on else (0, 0, 0)      # slow white blink
            elif soc is not None and soc < c("LED_LOW_SOC", 20):
                base = (255, 110, 0)                             # amber
            else:
                base = (255, 170, 40)                            # Camperlux gold
            rgb = tuple(int(v * k) for v in base)
        led_set(rgb)
        await asyncio.sleep_ms(1000)


def _location():
    """The van's position: the hub's weather location (set on its Settings
    page, or from the GPS), else config LAT/LON."""
    meta = (view.weather or {}).get("meta") or {}
    try:
        return float(meta["lat"]), float(meta["lon"])
    except (KeyError, TypeError, ValueError):
        lat, lon = c("LAT", None), c("LON", None)
        return (lat, lon) if lat is not None and lon is not None else None


def _sun_internet(t):
    """Today's sunrise and sunset from the forecast the hub fetched."""
    w = (view.weather or {}).get("data") or {}
    dl = w.get("daily") or {}
    days, rise, sset = dl.get("time"), dl.get("sunrise"), dl.get("sunset")
    off = w.get("utc_offset_seconds")
    if not (days and rise and sset) or off is None:
        return None
    y, m, d = ui._civil(t + off)[:3]
    try:
        i = days.index("%04d-%02d-%02d" % (y, m, d))
    except ValueError:
        return None

    def ep(v):                  # "2026-09-26T07:01", local time, to unix time
        try:
            return (ui._days_from_civil(int(v[0:4]), int(v[5:7]), int(v[8:10])) * 86400
                    + int(v[11:13]) * 3600 + int(v[14:16]) * 60 - off)
        except (TypeError, ValueError, IndexError):
            return None

    r, s_ = ep(rise[i]), ep(sset[i])
    return (r, s_) if r and s_ else None


def _sun(t):
    """(sunrise, sunset, where from) for today: worked out from the GPS
    position when there is a fix; else the forecast service's own times, from
    the internet; else worked out for the hub's saved location."""
    g = (view.data or {}).get("gps") or {}
    if g.get("fix") and g.get("lat") is not None:
        st = ui.sun_times(t, g["lat"], g["lon"])
        if st:
            return st[0], st[1], "GPS"
    st = _sun_internet(t)
    if st:
        return st[0], st[1], "internet"
    loc = _location()
    if loc:
        st = ui.sun_times(t, loc[0], loc[1])
        if st:
            return st[0], st[1], "location"
    return None


def night_now():
    mode = view.night_mode
    if mode in ("on", "off"):
        return mode == "on"
    t = now()
    if t is None:
        return False
    s = _sun(t)
    if s:
        view.night_src = (s[1], s[0], s[2])
        return not (s[0] <= t < s[1])
    view.night_src = (None, None, None)
    h = ui.local_parts(t, c("UTC_OFFSET_MIN", 0), c("DST_EU", True))[3]
    return h >= 20 or h < 7                 # no location at all: a plain evening rule


def apply_night():
    n = night_now()
    if n != view.night:
        view.night = n
        view.dirty = True
        if not (_dim or _off):
            disp.backlight(min(view.brightness, c("NIGHT_BRIGHTNESS", 0.3))
                           if n else view.brightness)


async def night_task():
    was_day = None
    while True:
        apply_night()
        t = now()
        if t is not None:
            day = _daylight(t)
            if was_day is False and day:
                end_offmode("sunrise")
            was_day = day
        await asyncio.sleep(60)


def screen_off(sticky=True):
    """Backlight and panel off until a touch, the BOOT button, or something
    that needs attention - an alarm, panic, the alarm clock, the kitchen
    timer - all of which call activity(), which wakes it.

    sticky: off mode - after each wake it goes dark again once left alone,
    until switched back or the morning comes. Idle at night is not sticky."""
    global _dim, _off
    if sticky and not view.offmode:
        view.offmode = True
        save_prefs()
        share("offmode")
    view.confirm = None
    view.bright_open = False
    _remember_page()
    view.saver = False
    _dim = _off = True
    disp.backlight(0)
    disp.sleep(True)


def _remember_page():
    """Note the page on show, for waking to - once, so a later idle step
    (the dim clock is Home) does not overwrite it."""
    global _resume
    if _resume is None:
        _resume = (view.page, view.sub, view.logo_page)


def end_offmode(why):
    if view.offmode:
        view.offmode = False
        print("off mode ended:", why)
        save_prefs()
        share("offmode")
        view.dirty = True


def _daylight(t):
    """Sun up, whatever night mode is set to."""
    s = _sun(t)
    if s:
        return s[0] <= t < s[1]
    h = ui.local_parts(t, c("UTC_OFFSET_MIN", 0), c("DST_EU", True))[3]
    return 7 <= h < 20


# Factory reset: hold the BOOT button for RESET_HOLD_S with the display running
# (not while powering it up: that starts the chip's own download mode). It
# clears what this display has learned - its pairing with the hub, the hub's
# networks, brightness and sound, game scores - and keeps what it needs to
# run: its software and update key (ota_*), the touch calibration, config.py.
RESET_HOLD_S = 10
RESET_FILES = (PREFS_FILE, LEARNED_FILE, HUB_NET_FILE, KEY_FILE, "game_hi.json", "games_hi.json")
_reset_hold = False          # counting down: the screen is the countdown's


def factory_wipe():
    import os
    for f in RESET_FILES:
        try:
            os.remove(f)
            print("factory reset: removed", f)
        except OSError:
            pass


async def boot_button_task():
    """The BOOT button on the back of the board. A press turns the screen off
    and on (on letting go). Held, a factory reset: counted down on the screen
    from 2 s - so a press is never taken for one - and cancelled by letting go."""
    global _reset_hold
    while True:
        await asyncio.sleep_ms(40)
        if boot_btn.value() or view.game:
            continue
        t0 = time.ticks_ms()
        shown = None
        while not boot_btn.value():
            held = time.ticks_diff(time.ticks_ms(), t0)
            if held >= 2000:
                if not _reset_hold:
                    _reset_hold = True
                    activity()
                    print("factory reset: counting down")
                left = RESET_HOLD_S - held // 1000
                if left != shown:
                    shown = left
                    if left <= 0:
                        splash("Factory reset", "Clearing, then restarting")
                        beep("ok")
                        factory_wipe()
                        await asyncio.sleep_ms(1500)
                        machine.reset()
                    splash("Factory reset in %d" % left, "Let go of BOOT to cancel")
                    beep("tap")
            await asyncio.sleep_ms(40)
        if _reset_hold:
            _reset_hold = False
            print("factory reset: cancelled")
            beep("bad")
            view.dirty = True                  # the page back
        elif _off:
            activity()
        else:
            screen_off()


_logo_taps = []


def logo_tap():
    """A tap on the logo page. Three within a second and a half: the games."""
    global _logo_taps
    ms = time.ticks_ms()
    _logo_taps = [t for t in _logo_taps if time.ticks_diff(ms, t) < 1500] + [ms]
    if sound:
        try:
            sound.egg(len(_logo_taps))       # boing, BOING, ta-da: something is happening
        except Exception as e:
            print("egg sound:", e)
    if len(_logo_taps) >= 3:
        _logo_taps = []
        asyncio.create_task(game_task())


# ---- updates over the air ---------------------------------------------------------
# The hub carries this display's software (tools/publish_display.py). Looked
# for two minutes after start and then hourly - or at once from the hub's
# settings - downloaded in the background, and put in place only at a quiet
# moment: never in a game, an alarm, panic, the kitchen timer or a dialog, and
# only once nobody has touched the screen for a minute.
_ota_req = None
_ota_now = asyncio.Event()


def _ota_quiet():
    if view.game or view.alarm() or view.panic or view.ac_ringing or view.kt_done:
        return False
    if view.confirm or view.bright_open or view.pair:
        return False
    return _off or _dim or time.ticks_diff(time.ticks_ms(), _last_touch) > 60000


async def ota_task():
    try:
        import ota
    except ImportError:
        return
    try:
        await asyncio.wait_for(_ota_now.wait(), 120)
    except asyncio.TimeoutError:
        pass
    while True:
        _ota_now.clear()
        found = None
        if hub.host:
            try:
                found = await ota.check(hub.host)
                if found is None:
                    print("ota: up to date (%s)" % ota.version())
            except Exception as e:
                print("ota: check failed:", e)
                ota.clean()
        if found:
            man, changed = found
            print("ota: version %s ready, %d file(s)" % (man["version"], len(changed)))
            while not _ota_quiet():
                await asyncio.sleep(15)
            if not _off:
                splash("Updating the display", "Back in a moment")
            ota.apply(man, changed)          # restarts
        try:
            await asyncio.wait_for(_ota_now.wait(), 3600)
        except asyncio.TimeoutError:
            pass


_paused_game = None        # (index, game) paused by the swipe from the right edge


async def game_task():
    """The games, from three taps on the logo: the menu (arcade.py) and
    whichever game is chosen from it. While they run the pages are not
    redrawn, the hub is not polled for data, and the flow page's copy of the
    screen is let go - but the alarm, panic, the alarm clock and the kitchen
    timer are still watched, and any of them ends the games so it can be
    seen. The menu's X goes back to the logo; a paused game, to Home."""
    global _flow_bg, _paused_game
    if view.game:
        return
    view.game = True
    _flow_bg = None
    _flow_boxes.clear()
    global _frame_boxes
    _frame_boxes = []
    gc.collect()
    try:
        import arcade
        paused, _paused_game = _paused_game, None
        out = await arcade.run(disp, fonts, touch, sound,
                               lambda: bool(view.alarm() or view.panic or view.ac_ringing
                                            or view.kt_done), paused)
        _paused_game = out if isinstance(out, tuple) else None
        if _paused_game or view.alarm() or view.panic:
            # a game paused, or something needing attention: Home (where "Back
            # to the game" waits); X alone leaves the logo where it was
            view.logo_page = False
            view.page = view.pages.index("home") if "home" in view.pages else 0
            view.sub = None
    except Exception as e:
        import sys
        sys.print_exception(e)
        # The games would not start (short of memory, say): back to Home.
        view.logo_page = False
        view.sub = None
        view.page = view.pages.index("home") if "home" in view.pages else 0
    finally:
        try:
            view.paused_game = arcade.GAMES[_paused_game[0]][1] if _paused_game else None
        except Exception:
            view.paused_game = None
        import sys
        for m in ("arcade", "game", "g_pack", "g_snake", "g_memory", "g_2048", "g_midge",
                  "g_pac", "g_ttt", "g_invaders", "g_dash",
                  "g_pong", "g_breakout", "g_blocks", "g_four", "g_mines",
                  "g_chess", "g_draughts", "g_solitaire", "g_reversi", "g_sudoku"):
            sys.modules.pop(m, None)             # their code and pictures, freed
        gc.collect()
        view.game = False
        activity()
        view.dirty = True


# The display's own battery. The FNK0104 brings it, halved by a divider, to
# GPIO 9; there is no charging-status line, so "charging" is read from the
# voltage climbing over the last few minutes, and "full" from it sitting at the
# top. The percentage is a lithium cell's usual resting curve - only a guide
# while it is charging, when the voltage reads high.
_LIION = ((4.20, 100), (4.10, 92), (4.00, 82), (3.90, 70), (3.80, 56), (3.70, 40),
          (3.60, 22), (3.50, 8), (3.40, 3), (3.30, 0))


def _batt_pct(v):
    if v >= _LIION[0][0]:
        return 100
    for (v1, p1), (v0, p0) in zip(_LIION, _LIION[1:]):
        if v >= v0:
            return int(p0 + (p1 - p0) * (v - v0) / (v1 - v0))
    return 0


async def batt_task():
    try:
        adc = machine.ADC(machine.Pin(c("BATTERY_PIN", 9)), atten=machine.ADC.ATTN_11DB)
    except Exception as e:
        print("display battery sense unavailable:", e)
        return
    hist = []
    flat = 0
    while True:
        s = 0
        for i in range(32):
            s += adc.read_uv()
            if i % 8 == 7:
                await asyncio.sleep_ms(1)
        v = s / 32 * 2 / 1e6
        hist = (hist + [v])[-8:]                         # four minutes of readings
        if v < 3.0 or v > 4.45:
            new = None                                   # no battery fitted, or not a cell
        else:
            rise = v - min(hist[:-1]) if len(hist) >= 4 else 0
            state = "charging" if rise > 0.015 else "full" if v >= 4.15 else "battery"
            new = {"v": v, "pct": _batt_pct(v), "state": state}
            # nearly flat: shut down while the cell still has something left,
            # rather than browning out - and plugging in starts it again
            flat = flat + 1 if v < c("SHUTDOWN_V", 3.40) and state == "battery" else 0
            if flat >= 2 and not (view.alarm() or view.panic):
                shut_down("Battery flat")
        old = view.dbatt
        if (old is None) != (new is None) or (new and (old["pct"] != new["pct"]
                                                       or old["state"] != new["state"])):
            view.dirty = True
        view.dbatt = new
        await asyncio.sleep(30)


def shut_down(why):
    """Off until plugged in to charge: a moment to read that, then deep sleep
    (shutdown.py; boot.py starts it again). Does not return."""
    beep("bad")
    splash(why, "Plug in to charge to start again")
    disp.backlight(0.5)
    time.sleep_ms(2500)
    import shutdown
    shutdown.shut_down(disp, led, touch.i2c)


async def post_task(path, body):
    """A setting changed on the screen: send it, and show what the hub says."""
    try:
        r = await hub.post(path, body)
        if r.get("ok") is False:
            raise HubError(r.get("error") or "refused")
        d = view.data or {}
        for k in ("storage", "autoheat", "timers", "guard"):
            if k in r:
                d[k] = r[k]
        view.data = d
        if isinstance(r.get("display"), dict):
            adopt_shared(r["display"])       # a switch: the hub's word on all six
    except HubError as e:
        print("post %s: %s" % (path, e))
        view._pending.clear()                # show the hub's real state again
        if path == "/api/switches":
            asyncio.create_task(fetch_shared())
        beep("bad")
        if "password" in str(e):
            start_pairing(True)
    view.dirty = True


async def history_task():
    """The last day of battery history, for the Power page's 24-hour view -
    from the hub's long record (a row every ten minutes)."""
    while True:
        wait = 60
        if wlan.isconnected() and hub.host and now() and not view.game:
            try:
                cut = now() - 86400
                rows = await hub.get("/api/history/long?since=%d" % cut, timeout_ms=20000)
                view.history = [r for r in rows if r and r[0] >= cut]
                view.dirty = True
                wait = 600
            except (HubError, ValueError, TypeError) as e:
                print("history:", e)
            gc.collect()
        await asyncio.sleep(wait)


async def chime_task():
    """The kitchen timer: when it runs out, wake the screen and chime every
    couple of seconds for a minute, or until OK is pressed."""
    rang = None
    while True:
        if view.kt_end is not None and view.kclock() >= view.kt_end:
            late = view.kclock() - view.kt_end
            view.kt_end = None
            share("kitchen")                     # so a phone shows it finished
            # one that ran out long ago, with this screen off, is not news
            if late < 90:
                view.kt_done = True
                view.dirty = True
                activity()
                rang = time.time()
        if view.kt_done and sound and rang is not None and time.time() - rang < 60:
            sound.play("chime", force=True)
            await asyncio.sleep_ms(2000)
        else:
            await asyncio.sleep_ms(250)


_panic_sent = None          # (state, ticks_ms) we told the hub, until it agrees


def start_panic(local):
    """Beacon and siren on. local: set off here, so tell the hub, which sets
    off every phone and web page too."""
    global _panic_sent
    if not view.panic:
        view.panic = True
        view.dirty = True
        asyncio.create_task(panic_task())
    if local:
        _panic_sent = (True, time.ticks_ms())
        asyncio.create_task(panic_post(True))


def stop_panic(local):
    global _panic_sent
    view.panic = False
    view.dirty = True
    if local:
        _panic_sent = (False, time.ticks_ms())
        asyncio.create_task(panic_post(False))


async def panic_post(on):
    """Tell the hub. If it cannot be reached the display's own panic carries
    on regardless - it does not depend on the hub."""
    for _ in range(3):
        try:
            await hub.post("/api/panic", {"on": on, "by": "the van display"})
            return
        except HubError:
            await asyncio.sleep_ms(700)


async def panic_task():
    """Beacon (led_task) and siren until STOP. Keeps the screen awake and bright."""
    disp.backlight(1.0)
    looping = False
    while view.panic:
        activity()
        if sound and not looping and sound.siren_ready():
            looping = sound.loop("siren")       # feeds itself from here on
        if looping:
            await asyncio.sleep_ms(200)
        else:                                   # siren still being built: the alarm
            if sound:
                sound.play("alarm", force=True)
            await asyncio.sleep_ms(2600)
    if looping:
        sound.stop_loop()
    disp.backlight(view.brightness)
    view.dirty = True


_ac_rang = None             # time.time() the alarm clock started ringing
_ac_last = None             # the day and time it last rang, so it rings once


def _ac_due():
    """Is it the alarm clock's time (or a snooze's end) right now?"""
    ac = view.alarm_clock
    t = now()
    if t is None:
        return False
    if view.ac_snooze and t >= view.ac_snooze:
        view.ac_snooze = None
        return True
    if not ac.get("on"):
        return False
    p = view.parts(t)
    if "%02d:%02d" % (p[3], p[4]) != ac.get("at"):
        return False
    days = ac.get("days", "every")
    if days == "weekdays" and p[6] >= 5 or days == "weekends" and p[6] < 5:
        return False
    global _ac_last
    key = (p[0], p[1], p[2], ac.get("at"))
    if key == _ac_last:
        return False
    _ac_last = key
    return True


async def alarm_clock_task():
    global _ac_rang
    while True:
        if not view.ac_ringing and _ac_due():
            view.ac_ringing = True
            _ac_rang = time.time()
            end_offmode("the alarm clock")
            activity()
            view.dirty = True
        if view.ac_ringing:
            if time.time() - _ac_rang > 600:    # ten minutes unanswered: stop
                view.ac_ringing = False
                view.dirty = True
            else:
                if sound:
                    sound.play("wake", force=True)
                await asyncio.sleep_ms(4000)
                continue
        await asyncio.sleep_ms(2000)


async def wheel_task():
    """The colour wheel takes about a second to draw: do it in slices after
    start-up, so it is ready by the time anyone opens the Lights page."""
    await asyncio.sleep(8)                     # let everything else start first
    for _ in view.wheel_steps():
        await asyncio.sleep_ms(0)


async def weather_task():
    """The hub keeps the forecast; fetch its copy now and then. The hub only
    refreshes it every half hour or so, so asking more often gains nothing."""
    while True:
        wait = 60                            # try again soon if it failed
        if wlan.isconnected() and hub.host and not view.game:
            try:
                view.weather = await hub.get("/api/weather", timeout_ms=10000)
                view.dirty = True
                wait = c("WEATHER_POLL_S", 600)
            except HubError:
                pass
        await asyncio.sleep(wait)


async def heater_task(action):
    global _result_at
    label = ui.ACTION_LABEL.get(action, action)
    view.busy = "Reading the heater..." if action == "read" else label + " - sending..."
    view.result = None
    view.dirty = True
    try:
        res = await hub.heater(view.heater_body(action))
        if res.get("ok"):
            # show the heater's reply now rather than on the next poll
            d = view.data or {}
            h = dict(d.get("heater") or {})
            h["connected"] = True
            for part in ("status", "sensors"):
                for k, v in (res.get(part) or {}).items():
                    if v is not None:
                        h[k] = v
            if _clock_base:
                h["at"] = now()
            d["heater"] = h
            view.data = d
            view.adopt_heater(h)
            view.result = (("Heater read" if action == "read" else label + " confirmed"),
                           gfx.GREEN)
            beep("ok")
        else:
            view.result = ("Failed: %s" % res.get("error", "no reply"), gfx.RED)
            beep("bad")
    except HubError as e:
        view.result = ("Failed: %s" % e, gfx.RED)
        beep("bad")
        if "password" in str(e):
            start_pairing(True)
    view.busy = None
    _result_at = time.ticks_ms()
    view.dirty = True


# ---- dragging between pages ------------------------------------------------------
# The page follows the finger: sideways movement drags the whole page, with
# the next one coming in beside it. Let go past a quarter of the way, or with a
# flick, and it carries on to the next page; otherwise it springs back.

class Drag:
    # How much it takes to turn the page. Dragged past COMMIT_PX (a sixth of
    # the screen) it goes; short of that, a flick does it - FLICK px per ms
    # over the last FLICK_MS, at least FLICK_MIN_PX in all. Each frame of a
    # drag takes a while to draw, so the touch is only read every few tens of
    # ms: the speed is measured over the last moment of the swipe, not the
    # last few readings, which could reach back to before the flick began.
    COMMIT_PX = 80
    FLICK = 0.3                              # px per ms that counts as a flick
    FLICK_MS = 120
    FLICK_MIN_PX = 30

    _a = _b = None                           # two spare screens, kept once made

    def __init__(self):
        from array import array
        import slide
        self.compose = slide.compose
        if Drag._a is None:
            Drag._a = bytearray(len(disp.buf))
            Drag._b = bytearray(len(disp.buf))
        view.dragging = True
        Drag._a[:] = disp.buf                # this page, as it is on screen
        self.state = ((view.page, view.logo_page), view.sub, view._hits,
                      (view.flow_live, view.heat_live), view.dirty)
        self.dir = 0
        self.dx = 0
        self.p = array("i", [0, ui.BODY_Y, ui.TAB_Y, disp.width])
        self.trail = []                          # (ticks_ms, x) for the flick speed

    def _neighbour(self, d):
        """Draw the page a drag in direction d reveals, into the second
        spare screen, and put everything back as it was."""
        where, sub, hits, flow, dirty = self.state
        logo = where[1]
        view.swipe(d)
        # The logo fills the whole screen, bars and all: to or from it, the
        # whole screen slides, or the header and tab bar would clip it.
        full = logo or view.logo_page
        self.p[1], self.p[2] = (0, disp.height) if full else (ui.BODY_Y, ui.TAB_Y)
        view.render()
        Drag._b[:] = disp.buf
        self.next = (view._hits, (view.flow_live, view.heat_live))
        view.page, view.logo_page = where
        view.sub = sub
        view._hits, view.dirty = hits, dirty
        view.flow_live, view.heat_live = flow
        disp.buf[:] = Drag._a
        self.dir = d

    def _frame(self, dx):
        dx = max(-disp.width, min(disp.width, int(dx))) & ~1
        self.p[0] = dx
        self.compose(disp.buf, Drag._a, Drag._b, self.p)
        disp.show(self.p[1], self.p[2])

    def move(self, dx, x):
        d = 1 if dx < 0 else -1
        if d != self.dir:
            self._neighbour(d)                   # the finger went the other way
        self.dx = dx
        now = time.ticks_ms()
        self.trail = [p for p in self.trail if time.ticks_diff(now, p[0]) <= self.FLICK_MS][-8:]
        self.trail.append((now, x))
        self._frame(dx)

    def release(self):
        v = 0.0
        if len(self.trail) >= 2:
            (t0, x0), (t1, x1) = self.trail[0], self.trail[-1]
            v = (x1 - x0) / max(1, time.ticks_diff(t1, t0))
        dx = self.dx
        go = abs(dx) > self.COMMIT_PX or (abs(v) > self.FLICK and v * dx > 0
                                          and abs(dx) >= self.FLICK_MIN_PX)
        target = (-disp.width if dx < 0 else disp.width) if go else 0
        for k in range(1, 7):                    # glide the rest, easing out
            f = k / 6
            self._frame(dx + (target - dx) * (1 - (1 - f) * (1 - f)))
        if go:
            beep("page")
            view.swipe(self.dir)
            disp.buf[:] = Drag._b
            view._hits, (view.flow_live, view.heat_live) = self.next
            disp.show()
            view.dirty = True                    # redrawn fresh, and the flow restarts
        else:
            disp.buf[:] = Drag._a
            disp.show(self.p[1], self.p[2])
        view.dragging = False


def can_drag():
    return not (_dim or _off or view.saver or view.game or view.confirm or view.bright_open
                or view.alarm() or view.kt_done or view.panic or view.ac_ringing)


def slide_apply(r):
    """A slider moved under the finger: show it at once. The backlight
    follows the brightness slider live; the hub hears at the end."""
    if not r:
        return
    view.dirty = True
    if r[0] == "brightness":
        disp.backlight(min(r[1], c("NIGHT_BRIGHTNESS", 0.3)) if view.night else r[1])


def slide_done(what):
    beep("tap")
    if what[0] == "dial":
        return                  # the heater's target goes with its next command
    save_prefs()
    if what[0] == "wheel" or what[1] == "amb":
        share("ambient")
    elif what[1] == "bright":
        share("brightness")


async def touch_task():
    down = None
    last = None
    held = None             # ticks_ms the Panic button went down, while held
    swallow = False         # the finger that set panic off: ignore its release
    drag = None             # a page being dragged sideways
    sliding = None          # the slider the finger is on (ui.slide), while down
    while True:
        if view.game:
            down = held = sliding = None
            await asyncio.sleep_ms(100)
            continue
        p = touch.read()
        if p:
            if down is None:
                down = p
                what = view.hit_at(p[0], p[1]) if not (_dim or _off or view.saver) else None
                if what and what[0] == "panic_hold":
                    held = time.ticks_ms()
                    view.panic_arming = view.mono_ms()
                elif what and what[0] in view.SLIDES:
                    sliding = what
                    activity()
                    slide_apply(view.slide(what, p[0], p[1]))      # a tap sets it too
            if sliding is not None and p != last:
                slide_apply(view.slide(sliding, p[0], p[1]))
            last = p
            # sideways, and clearly so: the page follows the finger
            ddx, ddy = p[0] - down[0], p[1] - down[1]
            if (drag is None and held is None and sliding is None and not swallow and abs(ddx) > 14
                    and abs(ddx) > abs(ddy) and can_drag()):
                activity()
                drag = Drag()
            if drag is not None:
                drag.move(ddx, p[0])
            if held is not None:
                view.dirty = True                # the ring filling round the button
                if time.ticks_diff(time.ticks_ms(), held) >= view.PANIC_HOLD_MS:
                    held = None
                    view.panic_arming = None
                    swallow = True
                    start_panic(local=True)
        elif down is not None:
            start, end = down, last
            down = None
            if sliding is not None:              # let go of a slider: now tell the hub
                slide_done(sliding)
                sliding = None
                continue
            if drag is not None:
                drag.release()
                drag = None
                continue
            if held is not None:                 # let go too soon: nothing happens
                held = None
                view.panic_arming = None
                view.dirty = True
                continue
            if swallow:
                swallow = False
                continue
            if not activity():               # a waking tap is silent and does nothing
                dx, dy = end[0] - start[0], end[1] - start[1]
                if abs(dx) > 70 and abs(dx) > 2 * abs(dy):
                    if not (view.confirm or view.bright_open):
                        beep("page")
                    view.swipe(1 if dx < 0 else -1)
                elif view.logo_page:
                    logo_tap()
                    continue
                else:
                    r = view.tap(start[0], start[1])
                    hit = view.hit
                    if r and r[0] == "sound" and sound:
                        sound.enabled = r[1]
                        save_prefs()
                    if hit:
                        # the switch that just turned sound on says so audibly
                        beep("page" if hit[0] == "tab" else
                             "open" if view.confirm and hit[0] == "heat" else "tap")
                    if r and r[0] == "panic" and r[1] is False:
                        stop_panic(local=True)
                    if r and r[0] == "prefs":
                        save_prefs()
                        if len(r) > 1:
                            share(r[1])
                    if r and r[0] == "ack":
                        asyncio.create_task(ack_task(r[1]))
                    if r and r[0] == "brightness":
                        disp.backlight(min(r[1], c("NIGHT_BRIGHTNESS", 0.3))
                                       if view.night else r[1])
                        save_prefs()
                        share("brightness")
                    if r and r[0] == "screen_off":
                        screen_off()
                    if r and r[0] == "shutdown":
                        shut_down("Shutting down")
                    if r and r[0] == "restart":
                        print("restart: from the screen")
                        splash("Restarting", "Back in about half a minute")
                        time.sleep_ms(800)
                        machine.reset()
                    if r and r[0] == "resume":
                        asyncio.create_task(game_task())
                    if r and r[0] == "pair_cancel":
                        pair_cancel()
                    if r and r[0] == "night":
                        save_prefs()
                        apply_night()
                        share("night")
                    elif r and r[0] == "heater" and not view.busy:
                        asyncio.create_task(heater_task(r[1]))
                    elif r and r[0] == "post":
                        asyncio.create_task(post_task(r[1], r[2]))
                        await asyncio.sleep_ms(0)    # the request on its way before the redraw
        await asyncio.sleep_ms(5 if drag is not None else 20)


_logo_lit = False           # the backlight is up at full for the logo page
_flow_bg = None             # the flow page as drawn, under its moving dots
_flow_at = 0


def _flow_rows():
    row = disp.width * 2
    return ui.BODY_Y * row, ui.TAB_Y * row


_flow_boxes = {}            # connection -> its box, and views onto it
_anim_ms = 0                # the moving parts' clock, ms: a whole number, so no garbage
_anim_at = None             # ticks_ms of the last frame
_frame_boxes = []           # the boxes a frame redraws, made when the page is drawn


def flow_snapshot():
    """Keep the page as just drawn, to put the dots on afresh each frame."""
    global _flow_bg
    a, b = _flow_rows()
    if _flow_bg is None:
        _flow_bg = bytearray(b - a)
        _flow_boxes.clear()                      # their views were onto the old copy
    _flow_bg[:] = memoryview(disp.buf)[a:b]
    global _frame_boxes
    _frame_boxes = [_flow_box(k, r) for k, r in view.anim_boxes()]


def _flow_box(key, rect):
    """A connection's box, with a view onto it in the kept copy, a view onto
    it on screen, and a small buffer to gather it into for sending. Copying
    a box row by row in Python took most of a second on this board; these
    let framebuf's own code do it, in about a millisecond."""
    bx = _flow_boxes.get(key)
    if bx is None:
        import framebuf
        x, y, w, h = rect
        RGB, sw = framebuf.RGB565, disp.width
        kept = framebuf.FrameBuffer(memoryview(_flow_bg)[((y - ui.BODY_Y) * sw + x) * 2:],
                                    w, h, RGB, sw)
        shown = framebuf.FrameBuffer(memoryview(disp.buf)[(y * sw + x) * 2:], w, h, RGB, sw)
        data = bytearray(w * h * 2)
        bx = (x, y, w, h, kept, shown, data, framebuf.FrameBuffer(data, w, h, RGB))
        _flow_boxes[key] = bx
    return bx


def flow_frame():
    """Put back the page under each live connection, draw the dots, and send
    only those boxes: a few kilobytes, not the whole middle of the screen."""
    for x, y, w, h, kept, shown, data, gather in _frame_boxes:
        fb.blit(kept, x, y)
    # The animation keeps its own time, stepped by at most a frame or two: a
    # moment's pause (a redraw, a fetch) then shows as the dots slowing for an
    # instant, not jumping ahead to where the wall clock says they should be.
    global _anim_ms, _anim_at
    ms = time.ticks_ms()
    step = 60 if _anim_at is None else time.ticks_diff(ms, _anim_at)
    # wrapped well before the sums in the animations outgrow a small integer
    _anim_ms = (_anim_ms + (step if step < 90 else 90)) % 1000000
    _anim_at = ms
    view.anim_draw(_anim_ms)
    for x, y, w, h, kept, shown, data, gather in _frame_boxes:
        gather.blit(shown, 0, 0)
        disp.show_box(x, y, w, h, data)


async def screen_task():
    global _dim, _off, _result_at, _logo_lit, _flow_at
    minute = None
    confirm_since = None
    while True:
        if view.game or view.dragging or _reset_hold:
            await asyncio.sleep_ms(20 if view.dragging else 100)
            continue
        ms = time.ticks_ms()
        idle = time.ticks_diff(ms, _last_touch) // 1000
        # dim, and later maybe switch off; never with a question on screen
        if view.alarm() or view.panic or view.ac_ringing or view.logo_page or view.sub == "gforce":
            idle = 0                        # these keep the screen up (G-force: you're driving)
        # The logo page is chosen to be looked at: full brightness, day or
        # night, and it never dims or goes off while it is showing.
        if view.logo_page and not (_logo_lit or _off):
            _logo_lit = True
            disp.backlight(1.0)
        elif _logo_lit and not view.logo_page:
            _logo_lit = False
            backlight_now()
        # the screensaver, after a minute untouched - but not over a heater
        # command still waiting for its answer
        timer_up = view.kt_done or (view.sub == "timer" and view.kt_end is not None)
        hold = view.busy or view.alarm() or timer_up
        if view.offmode or view.night:
            # no screensaver and no dimming: dark, once left alone
            after = c("OFFMODE_AFTER_S", 30) if view.offmode else c("SAVER_AFTER_S", 60)
            if not _off and not hold and idle >= after:
                screen_off(sticky=False)
        elif (not view.saver and not view.busy and not view.alarm() and not timer_up
                and idle >= c("SAVER_AFTER_S", 60)):
            view.saver = True
            view.confirm = None
            view.bright_open = False
            view.dirty = True
        if (view.dim_on and not (view.offmode or view.night) and not _dim and not _off
                and idle >= c("DIM_AFTER_S", 120)):
            view.confirm = None
            view.bright_open = False
            _dim = True
            # Back to the Home page, which makes a good night clock; the second
            # hand stops so the dimmed screen redraws once a minute, not a second.
            view.seconds = False
            if c("HOME_WHEN_IDLE", True) and "home" in view.pages:
                _remember_page()
                view.page = view.pages.index("home")
                view.logo_page = False
            disp.backlight(min(c("DIM_BRIGHTNESS", 0.06), view.brightness))
            view.dirty = True
        off_after = c("OFF_AFTER_S", 0)
        if off_after and not _off and idle >= off_after:
            _off = True
            disp.backlight(0)
            disp.sleep(True)
        # an unanswered question goes away on its own
        if view.confirm:
            confirm_since = confirm_since or ms
            if time.ticks_diff(ms, confirm_since) > 20000:
                view.confirm = None
                view.dirty = True
        else:
            confirm_since = None
        if _result_at and time.ticks_diff(ms, _result_at) > 60000:
            view.result = None
            _result_at = None
            view.dirty = True
        # data that has stopped arriving is shown as such
        if _last_ok and view.link == "ok":
            age = time.ticks_diff(ms, _last_ok) // 1000
            if age > 3 * max(c("POLL_S", 5), c("POLL_DIM_S", 30)):
                view.link, view.link_note = "stale", "No data for %d s" % age
                view.dirty = True
        # redraw as the clock moves: every second for the Home page's second
        # hand while awake, otherwise every minute for the header clock
        t = now()
        if view.panic:
            tick = ms // 500                # the panic screen flashes with the beacon
        elif view.saver:
            tick = "saver"                  # the logo does not change: draw it once
        elif view.kt_end is not None:
            tick = t                        # the countdown, in the header and on its page
        else:
            tick = None if t is None else (t if (view.seconds and view.pages[view.page] == "home")
                                           else t // 60)
        if tick != minute:
            minute = tick
            view.dirty = True
        if view.dirty and not _off:
            view.render()
            disp.show()
            if view.animating():
                flow_snapshot()
        elif (view.animating() and not (_dim or _off or view.saver or view.logo_page)
              and time.ticks_diff(ms, _flow_at) >= c("FLOW_FRAME_MS", 60)):
            _flow_at = ms
            flow_frame()
        await asyncio.sleep_ms(40)


async def main():
    asyncio.create_task(wifi_task())
    asyncio.create_task(learn_task())
    asyncio.create_task(power_task())
    asyncio.create_task(poll_task())
    asyncio.create_task(level_task())
    asyncio.create_task(weather_task())
    asyncio.create_task(alert_task())
    asyncio.create_task(alarm_task())
    asyncio.create_task(led_task())
    asyncio.create_task(night_task())
    asyncio.create_task(history_task())
    asyncio.create_task(chime_task())
    asyncio.create_task(alarm_clock_task())
    if sound:
        asyncio.create_task(sound.build_siren())
    asyncio.create_task(wheel_task())
    asyncio.create_task(touch_task())
    asyncio.create_task(boot_button_task())
    asyncio.create_task(batt_task())
    asyncio.create_task(ota_task())
    await screen_task()


try:
    asyncio.run(main())
except KeyboardInterrupt:
    raise                                   # mpremote stopping us: stay stopped
except Exception as e:
    # Show what went wrong rather than freezing on the last frame, then start
    # again: a display in a van should recover on its own.
    import sys
    sys.print_exception(e)
    try:
        splash("Display error", ("%s: %s" % (type(e).__name__, e))[:48])
        fonts.sm.text(fb, "Restarting in 15 s", disp.width // 2, 210,
                      gfx.MUTED, gfx.BG, 1)
        disp.show()
    except Exception:
        pass
    time.sleep(15)
    machine.reset()
