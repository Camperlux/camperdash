"""Who may do what: the hub password and paired displays.

The heater lights a diesel burner and the battery switches can cut the van's
12 V, so these must stay behind the hub password. Requests are sent through
the hub's real web server (pico/httpd.py) with and without the password, and
the password check itself (main.auth_ok) is exercised.

Run:  python tests/test_security.py
"""
import asyncio
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hubsim                                                    # noqa: E402

hub = hubsim.load_main(tempfile.mkdtemp(prefix="hubsec_"))
httpd = hub.httpd
passed = []


def ok(name):
    passed.append(name)
    print("ok  ", name)


class _Writer:
    def __init__(self):
        self.out = b""

    def write(self, b):
        self.out += b.encode() if isinstance(b, str) else bytes(b)

    async def drain(self):
        pass

    async def aclose(self):
        pass

    def close(self):
        pass

    async def wait_closed(self):
        pass


handled = []


async def _ok(*a):
    handled.append(a)
    return {"ok": True}


def _sync_ok(*a):
    handled.append(a)
    return {"ok": True}


TOKEN = "the-right-key"
handler = httpd.make_handler(
    _sync_ok, _sync_ok, heater_cmd=_ok, autoheat_set=_ok, settings_get=_ok, settings_set=_ok,
    scan_ble=_ok, scan_wifi=_ok, reboot=_ok, weather_meta=_sync_ok, bms_fet=_ok, storage_set=_ok,
    level_cmd=_ok, level_get=_sync_ok, alerts_get=_sync_ok, alerts_ack=_ok, alerts_test=_ok,
    timers_set=_ok, panic_set=_ok, display_get=_sync_ok, display_set=_ok, guard_set=_ok,
    wifi_share=_ok, auth_ok=lambda t: t == TOKEN, pair_code=_ok, pair_claim=_ok,
    switches_set=_ok, dispfw_seen=_sync_ok, settings_reveal=_ok)


def request(method, path, body=None, token=None):
    """Send one request through the hub's web server; (status code, handled?)."""
    data = json.dumps(body or {}).encode() if method == "POST" else b""
    head = "%s %s HTTP/1.1\r\nHost: hub\r\nContent-Length: %d\r\n" % (method, path, len(data))
    if token:
        head += "X-Token: %s\r\n" % token
    raw = (head + "\r\n").encode() + data

    async def go():
        r = asyncio.StreamReader()
        r.feed_data(raw)
        r.feed_eof()
        w = _Writer()
        await handler(r, w)
        return w.out
    del handled[:]
    out = asyncio.new_event_loop().run_until_complete(go())
    return int(out.split(b" ", 2)[1]), bool(handled)


# ---- the web server's gate ------------------------------------------------------------
locked = list(httpd._PROTECTED) + ["/api/heater/cmd", "/api/autoheat"]
for p in locked:
    code, done = request("POST", p, {"action": "air"})
    assert code == 403 and not done, (p, code, done)
    code, done = request("POST", p, {"action": "air"}, token="wrong")
    assert code == 403 and not done, (p, code, done)
    code, done = request("POST", p, {"action": "air"}, token=TOKEN)
    assert code != 403 and done, (p, code, done)
ok("all %d protected actions refused without the key or with a wrong one, done with it" % len(locked))

for p in ("/api/heater/cmd", "/api/bms/fet", "/api/settings", "/api/reboot", "/api/switches",
          "/api/settings/reveal"):
    assert p in locked, p
ok("the heater, battery switches, settings, restart, relays and showing stored passwords are among them")

for p in ("/api/panic", "/api/alerts/ack", "/api/display"):
    code, done = request("POST", p, {"on": False})
    assert code == 200 and done, (p, code)
ok("panic, clearing an alarm and the display's own settings stay open (nobody locked out of the alarm)")

code, _ = request("GET", "/api/data")
assert code == 200
ok("reading the van's state needs no key")

big = {"action": "air", "pad": "x" * 2000}
code, done = request("POST", "/api/heater/cmd", big, token=TOKEN)
assert code == 413 and not done
ok("an oversized heater command is refused before it is read")

# ---- the password check itself ------------------------------------------------------------
auth = {"hash": "", "web": True, "devices": {}}
hub.settings.load = lambda refresh=False: {"auth": auth}
hub.cfg.API_TOKEN = ""
assert hub.auth_ok(None)
ok("no password set: everything allowed (a fresh hub must be reachable to set one)")

key = hub._sha("camperdash-hub:secret")                      # what the pages send
auth["hash"] = hub._sha(key)
assert hub.auth_ok(key) and not hub.auth_ok(None) and not hub.auth_ok("") and not hub.auth_ok("guess")
assert not hub.auth_ok(auth["hash"])                         # the stored hash is not itself a key
ok("password set: only the key worked out from it is accepted, not the stored hash")

display_key = "a1" * 32
auth["devices"] = {"display-1": hub._sha(display_key)}
assert hub.auth_ok(display_key)
auth["devices"] = {}
assert not hub.auth_ok(display_key)
ok("a paired display's key works, and stops working when the pairing is removed")

auth["web"] = False
assert hub.auth_ok(None)
auth["web"] = True
hub.cfg.API_TOKEN = "legacy"
assert hub.auth_ok("legacy") and not hub.auth_ok("nope")
hub.cfg.API_TOKEN = ""
ok("'require the password' off opens the pages; the old API token still works if set")


# ---- connections that never send anything ------------------------------------------------
# Four of them stopped the real hub answering at all, until it was restarted
# (Oct 2026): clients gone mid-request as the hub changed network never close.
import time                                                       # noqa: E402
httpd.READ_MS, httpd.CONN_MS = 300, 1500


async def _silent(feed=b""):
    r = asyncio.StreamReader()
    if feed:
        r.feed_data(feed)                 # a request line, then nothing more
    w = _Writer()
    t = time.monotonic()
    await handler(r, w)
    return time.monotonic() - t, w.out


took, out = asyncio.new_event_loop().run_until_complete(_silent())
assert took < 1.0 and out == b"", (took, out)
took, out = asyncio.new_event_loop().run_until_complete(_silent(b"GET /api/level HTTP/1.1\r\n"))
assert took < 1.0 and out == b"", (took, out)
ok("a connection that sends nothing, or stops halfway, is let go within the read limit")


async def _slow_action(obj):
    await asyncio.sleep(5)                # an action that never finishes in time
    return {"ok": True}

slow = httpd.make_handler(_sync_ok, _sync_ok, heater_cmd=_slow_action, auth_ok=lambda t: True)


async def _held():
    r = asyncio.StreamReader()
    body = b"{}"
    r.feed_data(b"POST /api/heater/cmd HTTP/1.1\r\nContent-Length: 2\r\n\r\n" + body)
    r.feed_eof()
    t = time.monotonic()
    await slow(r, _Writer())
    return time.monotonic() - t

took = asyncio.new_event_loop().run_until_complete(_held())
assert took < 2.5, took
httpd.READ_MS, httpd.CONN_MS = 10000, 120000
ok("no connection is held longer than the connection limit, whatever it is waiting for")

print("\nALL OK - %d checks" % len(passed))
