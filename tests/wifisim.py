"""Wi-Fi scenario simulator: the hub's real Wi-Fi code and the display's real
reconnect code, run together against a model of the radios, in virtual time.

Real code used:
  hub     - pico/main.py: the Wi-Fi state machine (wifi_up, _wifi_check, _try_join,
            _settle_ap, _ap_set, _sta_reinit, _retry_ms)
  display - display/main.py: _networks, _hub_missing, wifi_task, poll_task
            display/hub.py:  Hub (its finding / retry / search logic)
Modelled: the radios, what is in range of what, how long joins and scans take,
the route out to the internet (the interface set up last carries it; stopping
the hotspot when it holds it leaves none - as measured on the hub),
the HTTP requests and the discovery broadcast (reachable or not), a phone joining.
"""
import ast
import heapq
import os
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, HERE)

# ---------------------------------------------------------------- virtual time
NOW = [0]


class _Sleep:
    def __init__(self, ms):
        self.ms = ms

    def __await__(self):
        yield ("sleep", self.ms)


fake_asyncio = types.SimpleNamespace(
    sleep_ms=lambda ms: _Sleep(ms),
    sleep=lambda s: _Sleep(int(s * 1000)),
    TimeoutError=TimeoutError)
fake_time = types.SimpleNamespace(
    ticks_ms=lambda: NOW[0], ticks_diff=lambda a, b: a - b,
    ticks_add=lambda a, b: a + b, time=lambda: 1790000000 + NOW[0] // 1000)


class Sched:
    def __init__(self):
        self.q = []
        self.n = 0

    def spawn(self, coro, at=0):
        self.n += 1
        heapq.heappush(self.q, (at, self.n, coro))

    def run(self, until_ms, stop=None):
        while self.q:
            at, n, coro = heapq.heappop(self.q)
            if at > until_ms:
                break
            NOW[0] = max(NOW[0], at)
            try:
                kind, ms = coro.send(None)
                heapq.heappush(self.q, (NOW[0] + max(1, ms), n, coro))
            except StopIteration:
                pass
            if stop and stop():
                return True
        return False


LOG = []


def log(who, *a):
    LOG.append((NOW[0], who, " ".join(str(x) for x in a)))


# ---------------------------------------------------------------- the world
SCAN_MS = 2200          # a cyw43 scan: the radio leaves the hotspot's channel
JOIN_MS = 3000          # association + WPA handshake + DHCP
HUB_PW = "hubpw"


class World:
    """What is in range of what, as functions of time (ms). Overridden per scenario."""
    def hub_sees(self, ssid, t): return False          # can the hub's radio hear it
    def hub_join_ok(self, ssid, t): return self.hub_sees(ssid, t)   # can it complete a join
    def disp_sees(self, ssid, t): return False         # home / phone networks, for the display
    def disp_near_hub(self, t): return True            # display within the hotspot's range
    def phone_near_hub(self, t): return True


W = World()


ROUTE = [None]           # which interface carries the route out: "sta", "ap" or None


class HubSTA:
    def __init__(self):
        self.target = None
        self.start = None
        self.up = False
        self.on = False

    def active(self, v=None):
        if v is None:
            return self.on
        if v and not self.on:
            self.on = True
            ROUTE[0] = "sta"                    # set up last: the route out
        elif not v and self.on:
            self.on = False
            self.target, self.up = None, False
        return None

    def scan(self):
        OFFCHAN.append((NOW[0], NOW[0] + SCAN_MS))
        log("hub", "radio: scanning (off the hotspot channel %.1f s)" % (SCAN_MS / 1000))
        names = [s for s in ("WiFi", "PhoneHotspot") if W.hub_sees(s, NOW[0])]
        if getattr(W, "busy", False):
            names.append("Neighbour")           # other people's networks on the air
        return [(s.encode(), b"", 1, -70, 3, 0) for s in names]

    def connect(self, ssid, pw):
        self.target, self.start, self.up = ssid, NOW[0], False

    def isconnected(self):
        if self.target is None:
            return False
        if not self.up and NOW[0] - self.start >= JOIN_MS and W.hub_join_ok(self.target, NOW[0]):
            self.up = True
        if self.up and not W.hub_sees(self.target, NOW[0]):
            log("hub", "radio: lost", self.target)
            self.target, self.up = None, False
        return self.up

    def disconnect(self):
        self.target, self.up = None, False

    def ifconfig(self):
        if self.up and self.target == "WiFi":
            return ("192.168.4.245", "255.255.252.0", "192.168.4.1", "192.168.4.1")
        return ("192.168.43.50", "255.255.255.0", "192.168.43.1", "192.168.43.1") if self.up \
            else ("0.0.0.0", "0.0.0.0", "0.0.0.0", "0.0.0.0")

    def config(self, *a, **k):
        return self.target if a else None


AP_HIST = []            # (on_ms, off_ms or None)
OFFCHAN = []            # (from_ms, to_ms): the radio away scanning


class HubAP:
    def __init__(self):
        self.on = False

    def active(self, v=None):
        if v is None:
            return self.on
        if v and not self.on:
            AP_HIST.append([NOW[0], None])
            ROUTE[0] = "ap"                     # the hotspot takes the route
            log("hub", "radio: HOTSPOT ON")
        elif not v and self.on:
            AP_HIST[-1][1] = NOW[0]
            if ROUTE[0] == "ap":
                ROUTE[0] = None                 # and takes it away with it
            log("hub", "radio: HOTSPOT OFF")
        self.on = bool(v)

    def config(self, *a, **k):
        return None

    def ifconfig(self, *a):
        return ("192.168.4.1", "255.255.255.0", "192.168.4.1", "192.168.4.1")


def ap_available(a, b):
    """The hotspot up and on its channel for the whole of [a, b]."""
    up = any(on <= a and (off is None or off >= b) for on, off in AP_HIST)
    return up and not any(f < b and t > a for f, t in OFFCHAN)


def ap_dropped_between(a, b):
    return any(off is not None and a < off <= b for on, off in AP_HIST)


STA, AP = HubSTA(), HubAP()
fake_network = types.SimpleNamespace(STA_IF=0, AP_IF=1, WLAN=lambda i: STA if i == 0 else AP)


class DispWLAN:
    def __init__(self):
        self.target = None
        self.start = None
        self.up = False
        self.since = None

    def active(self, v=None):
        return True

    def connect(self, ssid, pw):
        self.target, self.start, self.up = ssid, NOW[0], False
        log("dsp", "radio: joining", ssid)

    def disconnect(self):
        if self.target:
            log("dsp", "radio: left", self.target)
        self.target, self.up = None, False

    def _reachable_net(self, ssid, t):
        if ssid == "Camperlux":
            return W.disp_near_hub(t)
        return W.disp_sees(ssid, t)

    def isconnected(self):
        t = NOW[0]
        if self.target is None:
            return False
        if not self.up:
            if t - self.start < JOIN_MS:
                return False
            ok = self._reachable_net(self.target, t) and (
                self.target != "Camperlux" or (HUB_ON and ap_available(self.start, self.start + JOIN_MS)))
            if ok:
                self.up, self.since = True, t
                log("dsp", "radio: joined", self.target)
            else:
                if t - self.start >= 15000:     # the display gives up at 15 s anyway
                    pass
                return False
        if self.up:
            lost = not self._reachable_net(self.target, t)
            if self.target == "Camperlux" and (ap_dropped_between(self.since, t) or not AP.on):
                lost = True
            if lost:
                log("dsp", "radio: dropped from", self.target)
                self.target, self.up = None, False
                return False
        return self.up

    def config(self, k=None, **kw):
        return self.target if k == "essid" else None


DWLAN = DispWLAN()
HUB_ON = True


def hub_internet():
    return STA.up and ROUTE[0] == "sta"


def hub_addr_for_display():
    """The hub's address as the display could reach it right now, else None."""
    if not DWLAN.isconnected():
        return None
    if DWLAN.target == "Camperlux":
        return "192.168.4.1"
    if STA.up and STA.target == DWLAN.target:
        return STA.ifconfig()[0]
    return None


# ---------------------------------------------------------------- the hub
def load_hub():
    import hubsim
    import tempfile
    hub = hubsim.load_main(tempfile.mkdtemp(prefix="wifisim_"))
    cfg = sys.modules["config"]
    hub.asyncio = fake_asyncio
    hub.ticks_ms = fake_time.ticks_ms
    hub.ticks_diff = fake_time.ticks_diff
    hub.network = fake_network
    hub._try_ntp = lambda: None
    hub._internet = hub_internet
    hub.stalls = types.SimpleNamespace(mark=lambda *a: None)
    hub.print = lambda *a: log("hub", *a)
    return hub, cfg


async def hub_poll_loop(hub):
    await hub.wifi_up()
    while True:
        await fake_asyncio.sleep_ms(8000)       # the Bluetooth poll, about 8-10 s
        await hub._wifi_check()


# ---------------------------------------------------------------- the display
def load_display():
    src = open(os.path.join(REPO, "display", "hub.py"), encoding="utf-8").read()
    import re
    src = re.sub(r"^import (asyncio|time)\n", "", src, flags=re.M)
    hubmod = {"__name__": "dhub", "asyncio": fake_asyncio, "time": fake_time,
              "print": lambda *a: log("dsp", *a)}
    exec(compile(src, "display/hub.py", "exec"), hubmod)
    HubError = hubmod["HubError"]

    async def _request(host, method, path, body=None, token="", timeout_ms=8000):
        a = hub_addr_for_display()
        if a and host == a:
            await fake_asyncio.sleep_ms(300)
            return {}
        if host == "192.168.4.1" and DWLAN.isconnected() and DWLAN.target == "WiFi":
            await fake_asyncio.sleep_ms(100)    # the home router: answers, but isn't the hub
            raise HubError("not the hub (HTTP 200)")
        await fake_asyncio.sleep_ms(timeout_ms)
        raise HubError("timed out")

    async def discover(timeout_ms=1500):
        a = hub_addr_for_display()
        if a:
            await fake_asyncio.sleep_ms(150)
            return a
        await fake_asyncio.sleep_ms(timeout_ms)
        return None

    hubmod["_request"] = _request
    hubmod["discover"] = discover

    msrc = open(os.path.join(REPO, "display", "main.py"), encoding="utf-8").read()
    tree = ast.parse(msrc)
    want = {"_networks", "_hub_missing", "wifi_task", "poll_task"}
    funcs = [n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name in want]
    assert len(funcs) == 4, [f.name for f in funcs]
    # and the module-level state wifi_task keeps
    state = [n for n in tree.body if isinstance(n, ast.Assign) and any(
        isinstance(t, ast.Name) and t.id == "_left_for_hub" for t in n.targets)]
    mod = ast.Module(body=state + funcs, type_ignores=[])
    view = types.SimpleNamespace(link="", link_note="", dirty=False, game=False, saver=False)
    ns = {"asyncio": fake_asyncio, "time": fake_time, "wlan": DWLAN, "view": view,
          "HubError": HubError, "c": lambda k, d=None: d, "_take": lambda d: None,
          "_poll_misses": 0, "_dim": False, "_off": False,
          "_learned": [("WiFi", "homepw"), ("Camperlux", HUB_PW)],
          "_hub_net": ["WiFi", "Camperlux"],
          "print": lambda *a: log("dsp", *a)}
    # config.py's own list: home, the phone, the hotspot
    ns["c"] = lambda k, d=None: [("WiFi", "homepw"), ("PhoneHotspot", "phonepw"), ("Camperlux", HUB_PW)] \
        if k == "WIFI_NETWORKS" else d
    exec(compile(mod, "display/main.py", "exec"), ns)
    h = hubmod["Hub"](["192.168.4.1"])
    ns["hub"] = h
    return ns, h, view


# ---------------------------------------------------------------- the phone
PHONE = {"tries": 0, "ok": 0, "first_ok": None}


async def phone_task(start_ms, every_ms=15000, stop_ms=None):
    await fake_asyncio.sleep_ms(start_ms)
    while stop_ms is None or NOW[0] < stop_ms:
        if W.phone_near_hub(NOW[0]):
            PHONE["tries"] += 1
            t = NOW[0]
            await fake_asyncio.sleep_ms(JOIN_MS)
            if HUB_ON and ap_available(t, t + JOIN_MS):
                PHONE["ok"] += 1
                if PHONE["first_ok"] is None:
                    PHONE["first_ok"] = t
                    log("phone", "joined the hotspot")
            else:
                log("phone", "join REJECTED")
        await fake_asyncio.sleep_ms(every_ms)


# ---------------------------------------------------------------- running a scenario
def run(name, world, hub_nets, display_start, until_s, hub_start_joined=None,
        phone_from_s=None, watch_from_s=0, verbose=False):
    global W, STA, AP, DWLAN, HUB_ON, AP_HIST, OFFCHAN
    W = world
    NOW[0] = 0
    LOG.clear()
    AP_HIST.clear()
    OFFCHAN.clear()
    ROUTE[0] = None
    PHONE.update(tries=0, ok=0, first_ok=None)
    STA.__init__(); AP.__init__(); DWLAN.__init__()
    hub, cfg = load_hub()
    cfg.WIFI_MODE = "sta"
    cfg.WIFI_SSID = ""
    cfg.WIFI_NETWORKS = hub_nets
    cfg.WIFI_JOIN_TIMEOUT_S = 15
    cfg.WIFI_RETRY_S = 300
    cfg.WIFI_AP_ALWAYS = True
    cfg.AP_SSID, cfg.AP_PASSWORD, cfg.AP_IP = "Camperlux", HUB_PW, "192.168.4.1"
    ns, dhub, view = load_display()
    # where the display starts: already joined to a network
    if display_start:
        DWLAN.target, DWLAN.up, DWLAN.since = display_start, True, 0
        dhub.host = "192.168.4.245"             # where it last found the hub
    s = Sched()
    s.spawn(hub_poll_loop(hub))
    s.spawn(ns["wifi_task"]())
    s.spawn(ns["poll_task"]())
    if phone_from_s is not None:
        s.spawn(phone_task(phone_from_s * 1000))
    # sample: is the display talking to the hub?
    talk = []

    async def sampler():
        while True:
            talk.append((NOW[0], dhub.fail_since is None and dhub.host is not None
                         and hub_addr_for_display() == dhub.host, DWLAN.target if DWLAN.up else None,
                         AP.on, STA.isconnected(), hub_internet()))
            await fake_asyncio.sleep_ms(1000)
    s.spawn(sampler())
    s.run(until_s * 1000)
    return hub, talk, view


def measure(talk, watch_from_s):
    """What happened after the event at watch_from_s: how soon the display was
    talking to the hub again, how much of the time it was, how much of the time
    the hotspot was switched on, and how the phone fared."""
    w = watch_from_s * 1000
    after = [x for x in talk if x[0] >= w]
    first = next((x[0] for x in after if x[1]), None)
    return {"back_s": None if first is None else (first - w) / 1000,
            "talking": sum(1 for x in after if x[1]) / max(1, len(after)),
            "hotspot_on": sum(1 for x in after if x[3]) / max(1, len(after)),
            # of the time the hub was on a network, how much it had a route out
            "internet": (sum(1 for x in after if x[4] and x[5]) / sum(1 for x in after if x[4]))
                        if any(x[4] for x in after) else None,
            "phone_ok": PHONE["ok"], "phone_tries": PHONE["tries"]}


def dump(frm=0, to=10 ** 9, who=None):
    for t, w, m in LOG:
        if frm * 1000 <= t <= to * 1000 and (who is None or w == who):
            print("   %6.1f %-5s %s" % (t / 1000, w, m))
