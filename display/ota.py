# Updates over the air, from the hub.
#
# tools/publish_display.py builds this display's files, lists them in a
# manifest (a version, and each file's size and SHA-256), signs the manifest
# with the update key and puts it all on the hub, which serves it at
# /dispfw/<name>. The hub only passes it on: the signature is made on the PC,
# with a key that is never sent over the network - installed here once, over
# USB (ota_key.txt) - so a device on the WiFi pretending to be the hub cannot
# make one, even having seen the display's pairing key go by.
#
# check() fetches the manifest, checks its signature, and downloads the files
# that differ from what is installed into ota_new/, checking each one's SHA-256
# as it arrives. apply() swaps them in and restarts; otaboot.py (never updated
# over the air) takes it back if the new version does not start.

import asyncio
import binascii
import hashlib
import json
import os

KEY_FILE = "ota_key.txt"
INSTALLED = "ota_installed.json"
PENDING = "ota_pending.json"
BAD = "ota_bad.txt"
NEW = "ota_new"
PREV = "ota_prev"
MAX_MANIFEST = 16384


def _hex(b):
    return binascii.hexlify(b).decode()


def _hmac(key, msg):
    """HMAC-SHA256 (RFC 2104): MicroPython has hashlib but no hmac module."""
    if len(key) > 64:
        key = hashlib.sha256(key).digest()
    key = key + bytes(64 - len(key))
    inner = hashlib.sha256(bytes(b ^ 0x36 for b in key) + msg).digest()
    return hashlib.sha256(bytes(b ^ 0x5C for b in key) + inner).digest()


def _read(name, default=None):
    try:
        with open(name) as f:
            return f.read()
    except OSError:
        return default


def _json(name):
    try:
        return json.loads(_read(name, "") or "null")
    except ValueError:
        return None


def _exists(p):
    try:
        os.stat(p)
        return True
    except OSError:
        return False


def _mkdirs(path):
    d = ""
    for part in path.split("/")[:-1]:
        d = d + "/" + part if d else part
        try:
            os.mkdir(d)
        except OSError:
            pass


def installed():
    """What is on the display: {"version", "files": {path: sha256}}."""
    return _json(INSTALLED) or {"version": "", "files": {}}


def version():
    return installed().get("version") or "unknown"


async def _fetch(host, path, sink, limit):
    """GET path from the hub, feeding the body to sink(chunk). Bytes read."""
    port = 80
    if ":" in host:
        host, port = host.split(":", 1)
        port = int(port)
    r, w = await asyncio.wait_for(asyncio.open_connection(host, port), 10)
    total = 0
    try:
        w.write(("GET %s HTTP/1.0\r\nHost: %s\r\n\r\n" % (path, host)).encode())
        await w.drain()
        status = await asyncio.wait_for(r.readline(), 10)
        if b" 200" not in status:
            raise OSError("hub said %s" % status.strip().decode())
        while True:
            h = await asyncio.wait_for(r.readline(), 10)
            if h in (b"\r\n", b"\n", b""):
                break
        while True:
            chunk = await asyncio.wait_for(r.read(4096), 20)
            if not chunk:
                break
            total += len(chunk)
            if total > limit:
                raise OSError("too large")
            sink(chunk)
    finally:
        try:
            w.close()
            await w.wait_closed()
        except Exception:
            pass
    return total


async def check(host):
    """Look for an update on the hub. None if there is none (or it is one
    that failed here before); else (manifest, changed files), the changed
    files downloaded into ota_new/ and verified. Raises on anything wrong -
    a bad signature, a checksum that does not match - and keeps nothing."""
    key = (_read(KEY_FILE, "") or "").strip()
    if len(key) < 32:
        raise OSError("no update key on this display (install it over USB)")
    have = installed()
    buf = []
    await _fetch(host, "/dispfw/manifest.json?v=" + (have.get("version") or "none"),
                 buf.append, MAX_MANIFEST)
    m = json.loads(b"".join(buf))
    body = m["body"]
    if _hex(_hmac(binascii.unhexlify(key), body.encode())) != m.get("sig"):
        raise OSError("the update is not signed with this display's update key")
    man = json.loads(body)
    ver = man["version"]
    if ver == have.get("version") or ver == (_read(BAD, "") or "").strip():
        return None
    # Only forward. Versions begin with when they were built (20260930-0835-),
    # so they sort; one older than this display's - left on the hub after a
    # newer install over USB - would otherwise put old code back.
    if ver[:13] < (have.get("version") or "")[:13]:
        return None
    changed = [f for f in man["files"] if have["files"].get(f["path"]) != f["sha256"]]
    if not changed:
        # the same files under a new name (published after a USB install):
        # note the version, nothing to swap
        with open(INSTALLED, "w") as f:
            json.dump({"version": ver, "files": {x["path"]: x["sha256"] for x in man["files"]}}, f)
        return None
    try:
        os.mkdir(NEW)
    except OSError:
        pass
    for f in changed:
        dst = NEW + "/" + f["blob"]
        h = hashlib.sha256()
        with open(dst, "wb") as out:
            def sink(chunk):
                h.update(chunk)
                out.write(chunk)
            n = await _fetch(host, "/dispfw/" + f["blob"], sink, f["size"] + 1)
        if n != f["size"] or _hex(h.digest()) != f["sha256"]:
            clean()
            raise OSError("%s arrived damaged" % f["path"])
        await asyncio.sleep_ms(0)
    return man, changed


def apply(man, changed):
    """Swap the downloaded files in and restart. The old ones go to
    ota_prev/, and ota_pending.json - written before anything moves - lets
    otaboot.py put them back if this is interrupted or will not start."""
    import machine
    try:
        os.mkdir(PREV)
    except OSError:
        pass
    moved = [[f["path"], f["blob"], _exists(f["path"])] for f in changed]
    st = {"version": man["version"], "moved": moved, "applied": False, "boots": 0,
          "installed": {"version": man["version"],
                        "files": {f["path"]: f["sha256"] for f in man["files"]}}}
    with open(PENDING, "w") as f:
        json.dump(st, f)
    for path, blob, had in moved:
        if had:
            try:
                os.remove(PREV + "/" + blob)
            except OSError:
                pass
            os.rename(path, PREV + "/" + blob)
        _mkdirs(path)
        os.rename(NEW + "/" + blob, path)
        # a module installed as source by hand would hide the new bytecode
        if path.endswith(".mpy"):
            try:
                os.remove(path[:-4] + ".py")
            except OSError:
                pass
    st["applied"] = True
    with open(PENDING, "w") as f:
        json.dump(st, f)
    print("ota: version %s in place, restarting" % man["version"])
    machine.reset()


def clean():
    """Throw away a download that will not be used."""
    try:
        for n in os.listdir(NEW):
            os.remove(NEW + "/" + n)
    except OSError:
        pass
