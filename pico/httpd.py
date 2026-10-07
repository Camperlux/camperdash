# Minimal async HTTP server for the Pico W hub.
import asyncio
import json
import uos

_STATIC = {
    "/": ("www/app.html", "text/html"),       # the app: the van display's pages
    "/details": ("www/index.html", "text/html"),
    "/heater": ("www/heater.html", "text/html"),
    "/settings": ("www/settings.html", "text/html"),
    "/weather": ("www/weather.html", "text/html"),
    "/logo.jpg": ("www/logo.jpg", "image/jpeg"),
    "/badge.jpg": ("www/badge.jpg", "image/jpeg"),   # the app's logo page
    # Shared stylesheet and script: stored once instead of inside every page,
    # and cached by the browser so switching pages does not re-download them.
    "/app.css": ("www/app.css", "text/css"),
    "/common.js": ("www/common.js", "application/javascript"),
    # Add to Home Screen: the name, and the icons (small: the hub's flash is tight)
    "/manifest.json": ("www/manifest.json", "application/manifest+json"),
    "/icon-192.png": ("www/icon-192.png", "image/png"),
    "/icon-180.jpg": ("www/icon-180.jpg", "image/jpeg"),
    # the Android app, on a hub with the room for it (see tools/build_mpy.py)
    "/camperlux.apk": ("www/camperlux.apk", "application/vnd.android.package-archive"),
    "/apk.json": ("www/apk.json", "application/json"),
}
_POST_API = ("/api/settings", "/api/scan/ble", "/api/scan/wifi", "/api/reboot",
             "/api/bms/fet", "/api/storage", "/api/level", "/api/alerts/ack",
             "/api/alerts/test", "/api/timers", "/api/panic", "/api/display",
             "/api/guard", "/api/pair/code", "/api/pair/claim", "/api/switches",
             "/api/settings/reveal")
# Behind the hub password (when one is set). Left open: reading everything,
# the display's shared settings (brightness, lights), clearing an alarm, panic
# - on and off, so nobody is ever locked out of the alarm - and the display's
# half of pairing.
_PROTECTED = ("/api/settings", "/api/scan/ble", "/api/scan/wifi", "/api/reboot",
              "/api/bms/fet", "/api/storage", "/api/level", "/api/alerts/test",
              "/api/timers", "/api/guard", "/api/pair/code", "/api/switches",
              "/api/settings/reveal")
_FW_CHARS = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.-"
_MAX_BODY = 1024          # heater commands are ~60 bytes; anything bigger is bogus
_MAX_SETTINGS_BODY = 2048 # settings carry SSIDs, keys and five MAC addresses
_MAX_HEADER_LINE = 512    # keep a misbehaving LAN client from eating the heap
# Time limits on a connection. The hub has only a handful of connection slots -
# four silent connections were enough to stop it answering at all (measured, Oct
# 2026). Clients that vanish mid-request - the display or a phone as the hub
# moves between home Wi-Fi and its hotspot - never close their end, so with no
# limit each held a slot for good, and after a drive away and back the web
# server stopped answering until the hub was restarted.
READ_MS = 10000           # the request must arrive within this, each line and body
CONN_MS = 120000          # and no connection lasts longer (the display's update files fit)


async def _line(reader):
    return await asyncio.wait_for_ms(reader.readline(), READ_MS)


async def _read(reader, n):
    return await asyncio.wait_for_ms(reader.read(n), READ_MS)


async def _send_headers(w, status, ctype, length=None, extra="", cache="no-store"):
    w.write("HTTP/1.1 %s\r\n" % status)
    w.write("Content-Type: %s\r\n" % ctype)
    if length is not None:
        w.write("Content-Length: %d\r\n" % length)
    w.write("Cache-Control: %s\r\n" % cache)
    w.write("Connection: close\r\n%s\r\n" % extra)
    await w.drain()


async def _send_json(w, obj, status="200 OK"):
    body = json.dumps(obj).encode()
    await _send_headers(w, status, "application/json", len(body))
    w.write(body)
    await w.drain()


async def _send_json_stream(w, obj, status="200 OK", chunk=6):
    """Stream a dict as JSON, one key at a time.

    _send_json builds the whole response as a string and then encodes it, which
    is two contiguous allocations of the finished size. For /api/data that is
    about 3 KB each, on a heap with well under 70 KB free that has been
    fragmenting since boot - and two browsers polling at once makes it four.
    That showed up as intermittent 500s and dropped connections on /api/data
    while the small /api/level never missed a beat.

    Serialising per key means the largest single allocation is the biggest
    value, a few hundred bytes, instead of the entire payload. There is no
    Content-Length, the same as the history endpoint: the response is delimited
    by the connection close that every reply here already does.
    """
    await _send_headers(w, status, "application/json")
    w.write("{")
    first = True
    n = 0
    for k, v in obj.items():
        w.write('%s%s:%s' % ("" if first else ",", json.dumps(k), json.dumps(v)))
        first = False
        n += 1
        if n % chunk == 0:
            await w.drain()
    w.write("}")
    await w.drain()


async def _send_text(w, status, text):
    body = text.encode()
    await _send_headers(w, status, "text/plain", len(body))
    w.write(body)
    await w.drain()


async def _send_file(w, fn, ctype, inm=""):
    # Pages must never be served from cache without asking - they have to be able
    # to change on a deploy. But "no-store" meant every page change re-downloaded
    # 20-35 KB of HTML over WiFi from flash, which is most of why moving between
    # pages felt slow. They now carry a validator instead: the browser asks, and
    # an unchanged page comes back as a 304 with no body at all.
    #
    # The readings do not come from the HTML - they come from /api/data - so a
    # cached page is never a stale reading, only a stale layout, and the
    # validator catches that on the next request after a deploy.
    # A short max-age, not no-cache: with no-cache the browser still asks on
    # every page change, and the wait is the hub scheduling the reply rather
    # than the bytes moving - so revalidating saved the download but not the
    # delay. Thirty seconds lets hopping between pages skip the request
    # entirely, while a deploy is still picked up within half a minute. The
    # ETag below then handles the revalidation after that.
    cache = "max-age=30" if ctype == "text/html" else "max-age=600"
    try:
        size = 0
        with open(fn, "rb") as f:
            f.seek(0, 2); size = f.tell()
        # Size and mtime are enough to tell one deploy from another, and both are
        # already to hand - no need to read the file to hash it.
        try:
            st = uos.stat(fn)
            tag = '"%d-%d"' % (st[6], st[8])
        except Exception:
            tag = '"%d"' % size
        if inm and tag in inm:
            await _send_headers(w, "304 Not Modified", ctype, 0,
                                extra='ETag: %s\r\n' % tag, cache=cache)
            return
        big = size > 65536                # the Android app: a download, in bigger pieces
        await _send_headers(w, "200 OK", ctype, size, cache=cache,
                            extra='ETag: %s\r\n' % tag +
                            ('Content-Disposition: attachment; filename="camperlux.apk"\r\n'
                             if ctype.endswith("archive") else ""))
        with open(fn, "rb") as f:
            while True:
                chunk = f.read(8192 if big else 1024)
                if not chunk:
                    break
                w.write(chunk)
                await w.drain()
    except OSError:
        await _send_text(w, "404 Not Found", "not found")


async def _send_csv(w, fn, header=""):
    """Serve a log file exactly as it stands, and never from cache.

    Unlike the pages, this file is appended to continuously, so a cached copy is
    always the wrong one. The header row is sent rather than stored, so trimming
    the file cannot quietly discard it.
    """
    try:
        with open(fn, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
        head = header.encode() if header else b""
        await _send_headers(
            w, "200 OK", "text/csv", size + len(head),
            extra='Content-Disposition: attachment; filename="%s"\r\n' % fn)
        if head:
            w.write(head)
        with open(fn, "rb") as f:
            while True:
                chunk = f.read(1024)
                if not chunk:
                    break
                w.write(chunk)
                await w.drain()
        await w.drain()
    except OSError:
        await _send_text(w, "404 Not Found", "no log yet")


async def _send_weather(w, meta, fn="weather.json"):
    """Stream the cached forecast back with its age wrapped around it.

    The file is the forecast service's own reply, untouched, so this never
    parses it - it just writes a prefix, pipes the bytes and closes the object.
    """
    try:
        # One handle for both the size and the body: measuring with a separate
        # open let the forecast refresh replace the file in between, so the
        # Content-Length could disagree with the bytes actually sent and leave
        # the browser waiting on a response that never completes.
        f = open(fn, "rb")
        f.seek(0, 2)
        size = f.tell()
        f.seek(0)
        if not size:
            f.close()
            raise OSError("empty")
    except OSError:
        await _send_json(w, {"ok": False, "meta": meta, "error": "no forecast stored yet"})
        return
    try:
        head = '{"ok":true,"meta":' + json.dumps(meta) + ',"data":'
        await _send_headers(w, "200 OK", "application/json", len(head) + size + 1)
        w.write(head)
        await w.drain()
        while True:
            b = f.read(256)
            if not b:
                break
            w.write(b)
            await w.drain()
        w.write("}")
        await w.drain()
    finally:
        f.close()


async def _send_history_rows(w, rows, chunk=40):
    """Stream the in-RAM history as JSON a few rows at a time.

    It used to go out through _send_json, which builds the whole array as one
    string: at 340 rows that is a ~14 KB contiguous allocation on a heap that has
    been fragmenting all day, and it was failing with MemoryError while the long
    tier - which already streamed - was fine.
    """
    await _send_headers(w, "200 OK", "application/json")
    w.write("[")
    buf = []
    first = True
    for r in rows:
        soc = r[3] if r[3] is not None else "null"
        buf.append("%s[%s,%s,%s,%s,%s]" % ("" if first else ",",
                                           r[0], r[1], r[2], soc, r[4] if r[4] is not None else 0))
        first = False
        if len(buf) >= chunk:
            w.write("".join(buf)); buf = []
            await w.drain()
    if buf:
        w.write("".join(buf))
    w.write("]")
    await w.drain()


async def _send_history_file(w, fn="hist.csv", chunk=40, since=0):
    """Stream the CSV history out as a JSON array, a few rows at a time, so a
    week of data never has to exist as one big string in RAM. since (unix
    time) skips older rows: the van display wants one day, not the week."""
    try:
        f = open(fn)
    except OSError:
        await _send_json(w, [])
        return
    try:
        await _send_headers(w, "200 OK", "application/json")
        w.write("[")
        buf = []
        first = True
        for line in f:
            line = line.strip()
            if not line:
                continue
            c = line.split(",")
            if len(c) < 5:
                continue
            if since:
                try:
                    if int(c[0]) < since:
                        continue
                except ValueError:
                    continue
            soc = c[3] if c[3] != "" else "null"
            buf.append("%s[%s,%s,%s,%s,%s]" % ("" if first else ",", c[0], c[1], c[2], soc, c[4]))
            first = False
            if len(buf) >= chunk:
                w.write("".join(buf)); buf = []
                await w.drain()
        if buf:
            w.write("".join(buf))
        w.write("]")
        await w.drain()
    finally:
        f.close()


def make_handler(get_data, get_history, heater_cmd=None, api_token="", autoheat_set=None,
                 settings_get=None, settings_set=None, scan_ble=None, scan_wifi=None,
                 reboot=None, weather_meta=None, bms_fet=None, storage_set=None,
                 level_cmd=None, level_get=None, alerts_get=None, alerts_ack=None,
                 alerts_test=None, timers_set=None, panic_set=None, display_get=None,
                 display_set=None, guard_set=None, wifi_share=None, auth_ok=None,
                 pair_code=None, pair_claim=None, switches_set=None, dispfw_seen=None,
                 settings_reveal=None):
    async def refuse(writer, reader, clen):
        # Read what was sent first. Closing with the request body still unread
        # makes the stack reset the connection, and a reset throws away the
        # answer on its way: the browser got a bare status line and reported
        # "failed to fetch" instead of asking for the password. The Pico W was
        # slow enough to get away with it; the RP2350 is not.
        n = min(clen, _MAX_SETTINGS_BODY)
        while n > 0:
            chunk = await _read(reader, n)
            if not chunk:
                break
            n -= len(chunk)
        await _send_json(writer, {"ok": False, "error": "the hub's password is needed",
                                  "auth": True}, "403 Forbidden")

    async def handle(reader, writer):
        try:
            await asyncio.wait_for_ms(_serve(reader, writer), CONN_MS)
        except asyncio.TimeoutError:
            print("http: a connection held on for %d s - dropped" % (CONN_MS // 1000))
        except Exception:
            pass
        finally:
            try:
                await writer.aclose()
            except Exception:
                pass

    async def _serve(reader, writer):
        path = "?"
        got = False
        try:
            line = await _line(reader)
            if len(line) > _MAX_HEADER_LINE:
                return
            parts = line.split(b" ")
            if len(parts) < 2:
                return
            method = parts[0].decode()
            path = parts[1].decode()
            clen = 0
            token = ""
            inm = ""
            while True:                       # read request headers
                h = await _line(reader)
                if h == b"\r\n" or not h:
                    break
                if len(h) > _MAX_HEADER_LINE:
                    return
                hl = h.decode()
                key = hl.split(":", 1)[0].strip().lower()
                if key == "content-length":
                    try:
                        clen = int(hl.split(":", 1)[1].strip())
                    except Exception:
                        clen = 0
                elif key == "x-token":
                    token = hl.split(":", 1)[1].strip()
                elif key == "if-none-match":
                    inm = hl.split(":", 1)[1].strip()
            p = path.split("?")[0]
            got = True                        # the request is in; from here a timeout is an error
            ok = auth_ok(token) if auth_ok else (not api_token or token == api_token)
            if method == "POST" and p == "/api/heater/cmd" and heater_cmd:
                if clen > _MAX_BODY:
                    await _send_text(writer, "413 Payload Too Large", "too large")
                    return
                if not ok:
                    await refuse(writer, reader, clen)
                    return
                body = b""
                while len(body) < clen:
                    chunk = await _read(reader, clen - len(body))
                    if not chunk:
                        break
                    body += chunk
                try:
                    obj = json.loads(body) if body else {}
                except Exception:
                    obj = {}
                if not isinstance(obj, dict):
                    obj = {}
                await _send_json(writer, await heater_cmd(obj))
            elif method == "POST" and p == "/api/autoheat" and autoheat_set and not ok:
                await refuse(writer, reader, clen)
            elif method == "POST" and p in _PROTECTED and not ok:
                await refuse(writer, reader, clen)
            elif method == "POST" and p == "/api/autoheat" and autoheat_set:
                body = b""
                while len(body) < min(clen, _MAX_BODY):
                    chunk = await _read(reader, min(clen, _MAX_BODY) - len(body))
                    if not chunk:
                        break
                    body += chunk
                try:
                    obj = json.loads(body) if body else {}
                except Exception:
                    obj = {}
                await _send_json(writer, await autoheat_set(obj if isinstance(obj, dict) else {}))
            elif method == "POST" and p in _POST_API:
                fn = {"/api/settings": settings_set, "/api/scan/ble": scan_ble,
                      "/api/scan/wifi": scan_wifi, "/api/reboot": reboot,
                      "/api/bms/fet": bms_fet, "/api/storage": storage_set,
                      "/api/level": level_cmd, "/api/alerts/ack": alerts_ack,
                      "/api/alerts/test": alerts_test, "/api/timers": timers_set,
                      "/api/panic": panic_set, "/api/display": display_set,
                      "/api/guard": guard_set, "/api/pair/code": pair_code,
                      "/api/pair/claim": pair_claim, "/api/switches": switches_set,
                      "/api/settings/reveal": settings_reveal}[p]
                if fn is None:
                    await _send_text(writer, "404 Not Found", "not found")
                elif clen > _MAX_SETTINGS_BODY:
                    await _send_text(writer, "413 Payload Too Large", "too large")
                else:
                    body = b""
                    while len(body) < min(clen, _MAX_SETTINGS_BODY):
                        chunk = await _read(reader, min(clen, _MAX_SETTINGS_BODY) - len(body))
                        if not chunk:
                            break
                        body += chunk
                    try:
                        obj = json.loads(body) if body else {}
                    except Exception:
                        obj = {}
                    await _send_json(writer, await fn(obj if isinstance(obj, dict) else {}))
            elif p == "/api/wifi/networks" and wifi_share:
                # passwords: behind the hub password, when one is set
                if not ok:
                    await refuse(writer, reader, clen)
                else:
                    await _send_json(writer, wifi_share())
            elif p == "/api/display" and display_get:
                await _send_json(writer, display_get())
            elif p == "/api/alerts" and alerts_get:
                await _send_json(writer, alerts_get())
            elif p == "/api/weather" and weather_meta:
                await _send_weather(writer, weather_meta())
            elif p == "/api/settings" and settings_get:
                if not ok:
                    await refuse(writer, reader, clen)
                else:
                    await _send_json(writer, await settings_get())
            elif p == "/api/stalls":
                import stalls                   # when the hub was held up, and by what
                await _send_json(writer, stalls.records())
            elif p == "/api/level/trip":
                # the last 15 minutes or so of readings (level.py), streamed: its
                # length is not known until it is written
                import level
                await _send_headers(writer, "200 OK", "text/csv", None,
                                    extra='Content-Disposition: attachment; filename="trip.csv"\r\n')
                writer.write(level.TRIP_HEADER)
                for chunk in level.trip_chunks():
                    writer.write(chunk)
                    await writer.drain()
            elif p == "/api/level/log":
                await _send_csv(writer, "level_log.csv",
                                "t,temp_c,roll,pitch,raw_roll,raw_pitch,ax,ay,az\n")
            elif p == "/api/level" and method == "GET" and level_get:
                await _send_json(writer, level_get())
            elif p == "/api/data":
                # the largest payload the hub serves, and the one that was
                # failing under memory pressure - stream it
                await _send_json_stream(writer, get_data())
            elif p == "/api/history/long":
                since = 0
                q = path.split("?", 1)
                if len(q) > 1:
                    for kv in q[1].split("&"):
                        if kv.startswith("since="):
                            try:
                                since = int(kv[6:])
                            except ValueError:
                                pass
                await _send_history_file(writer, since=since)
            elif p == "/api/history":
                await _send_history_rows(writer, get_history())
            elif p.startswith("/dispfw/"):
                # the van display's software, for it to update itself
                # (tools/publish_display.py, display/ota.py). Signed on the PC,
                # so open to read; the name is checked, never a path.
                name = p[8:]
                if not name or any(ch not in _FW_CHARS for ch in name) or name.startswith("."):
                    await _send_text(writer, "404 Not Found", "not found")
                else:
                    if name == "manifest.json" and dispfw_seen:
                        q = path.split("?", 1)
                        for kv in (q[1].split("&") if len(q) > 1 else ()):
                            if kv.startswith("v="):
                                dispfw_seen(kv[2:][:40])
                    await _send_file(writer, "dispfw/" + name,
                                     "application/json" if name.endswith(".json")
                                     else "application/octet-stream")
            elif p in _STATIC:
                fn, ct = _STATIC[p]
                await _send_file(writer, fn, ct, inm)
            else:
                await _send_text(writer, "404 Not Found", "not found")
        except Exception as e:
            if isinstance(e, asyncio.TimeoutError) and not got:
                # no request arrived: a client gone mid-request. Nobody is
                # there to read an answer, so the connection is just let go.
                print("http: no request within %d s - dropped" % (READ_MS // 1000))
                return
            # A handler that throws used to close the socket with no reply at
            # all, which looks like a dead hub from the browser.  Say what broke
            # on the console and give the caller an error it can show.
            print("http error on", path, "->", type(e).__name__, e)
            try:
                await _send_json(writer, {"ok": False,
                                          "error": "%s: %s" % (type(e).__name__, e)},
                                 "500 Internal Server Error")
            except Exception:
                pass
        finally:
            try:
                await writer.aclose()
            except Exception:
                pass
    return handle
