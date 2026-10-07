"""Install the bytecode build onto the Pico W hub, over USB.

Dry run unless you pass --go.

Why USB and not the network: a .mpy file carries an ABI version, and a file
built by the wrong mpy-cross does not fail politely. It raises ValueError at
import, main.py dies, and the hub never brings up WiFi - so the only way back
in is the USB port. Deploying bytecode to a board you can only reach over the
air is a one-way trip. This script therefore refuses to run without a serial
port, and checks the ABI on the device before it writes anything.

The check is empirical rather than a version-number comparison: it copies one
small compiled module across and imports it. If that works, every other file
built by the same mpy-cross will work too, and if it does not, nothing else has
been touched yet.

  python tools/build_mpy.py
  python tools/deploy_mpy.py --port COM9              # dry run, changes nothing
  python tools/deploy_mpy.py --port COM9 --go
"""

import argparse
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BUILD = os.path.join(ROOT, "build")
PROBE = "crc.mpy"          # small, no imports of its own
# config.py holds this unit's WiFi passwords and BLE addresses. The copy in the
# repository is not necessarily the copy that belongs to the van in front of
# you, so it is left alone unless it is asked for by name.
CONFIG = "config.py"


def run(port, args, timeout=120):
    cmd = ["mpremote", "connect", port] + args
    return subprocess.run(cmd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=timeout)


def device_files(port):
    r = run(port, ["fs", "ls"])
    if r.returncode != 0:
        return None
    out = []
    for line in (r.stdout or "").splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 2:
            out.append(parts[-1])
    return out


def ordered_names(files):
    return [f for f in files if f != "main.py"] + [f for f in files if f == "main.py"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", required=True, help="e.g. COM9")
    ap.add_argument("--go", action="store_true",
                    help="actually write to the device (default: dry run)")
    ap.add_argument("--with-config", action="store_true",
                    help="also overwrite config.py - this unit's WiFi passwords "
                         "and BLE addresses live in it, so think first")
    a = ap.parse_args()

    if not os.path.isdir(BUILD):
        sys.exit("no build/ - run tools/build_mpy.py first")

    files = sorted(f for f in os.listdir(BUILD)
                   if os.path.isfile(os.path.join(BUILD, f)))
    if not a.with_config and CONFIG in files:
        files.remove(CONFIG)
    pages = sorted(os.listdir(os.path.join(BUILD, "www")))

    # --- what is on the device now -----------------------------------------
    print("connecting to %s ..." % a.port)
    have = device_files(a.port)
    if have is None:
        sys.exit("no answer from %s - is the hub plugged in and no other "
                 "program holding the port?" % a.port)
    print("device has %d file(s) in /" % len(have))

    # Stale sources: every .py that a .mpy now replaces. Left in place they
    # waste flash and, worse, leave two copies of a module on the device for
    # the next person to wonder about.
    built = {f[:-4] for f in files if f.endswith(".mpy")}
    stale = sorted(f for f in have
                   if f.endswith(".py") and f[:-3] in built and f != CONFIG)

    print()
    print("would copy %d module(s) and %d page(s):" % (len(files), len(pages)))
    for f in files:
        print("   %-22s %7d" % (f, os.path.getsize(os.path.join(BUILD, f))))
    print("   www/  %d file(s)" % len(pages))
    if stale:
        print()
        print("would DELETE %d stale source file(s) now replaced by bytecode:"
              % len(stale))
        print("   " + "  ".join(stale))
    if not a.with_config:
        print()
        print("config.py: left alone (pass --with-config to overwrite it)")

    if not a.go:
        print()
        print("dry run - nothing was written. Re-run with --go to apply.")
        return

    # --- ABI check, before anything is written ------------------------------
    print()
    print("checking the bytecode ABI against this firmware ...")
    r = run(a.port, ["fs", "cp", os.path.join(BUILD, PROBE), ":_abi_probe.mpy"])
    if r.returncode != 0:
        sys.exit("could not copy the probe: %s" % (r.stderr or r.stdout))
    r = run(a.port, ["exec", "import _abi_probe; print('abi ok')"])
    ok = "abi ok" in (r.stdout or "")
    run(a.port, ["exec",
                 "import os\ntry:\n os.remove('_abi_probe.mpy')\nexcept OSError:\n pass"])
    if not ok:
        print(r.stdout or "")
        print(r.stderr or "")
        sys.exit("the device rejected a .mpy built by this mpy-cross.\n"
                 "Nothing has been changed. Find the firmware version with\n"
                 "  mpremote connect %s exec \"import sys; print(sys.version)\"\n"
                 "and install the matching mpy-cross:\n"
                 "  pip install mpy-cross==<version>\n"
                 "then rebuild." % a.port)
    print("  ABI ok")

    # --- room, before anything is written ------------------------------------
    # The flash is small (848 KB) and nothing here removes what the build no
    # longer has. Running out part-way left a hub without its main page, so
    # check first: what arrives, less what it replaces, must fit with a margin.
    probe = "\n".join((
        "import os, json",
        "d = {}",
        "for p in ('', 'www/'):",
        " try:",
        "  names = os.listdir(p or '/')",
        " except OSError:",
        "  names = []",
        " for n in names:",
        "  try:",
        "   d[p + n] = os.stat(p + n)[6]",
        "  except OSError:",
        "   pass",
        "s = os.statvfs('/')",
        "print(json.dumps({'free': s[0] * s[3], 'total': s[0] * s[2], 'files': d}))"))
    r = run(a.port, ["exec", probe])
    try:
        import json
        info = json.loads((r.stdout or "").strip().splitlines()[-1])
        # The Android app (1.4 MB) is for the RP2350 hub's 14 MB; the Pico W's
        # 848 KB cannot hold it, so a small board gets the pages without it.
        if info.get("total", 0) < 3 * 1024 * 1024:
            skipped = [f for f in pages if f in ("camperlux.apk", "apk.json")]
            pages = [f for f in pages if f not in skipped]
            if skipped:
                print("  (the Android app is left off: this board's flash is too small)")
        incoming = ([(f, os.path.getsize(os.path.join(BUILD, f))) for f in ordered_names(files)] +
                    [("www/" + f, os.path.getsize(os.path.join(BUILD, "www", f))) for f in pages])
        grow = sum(n - info["files"].get(f, 0) for f, n in incoming)
        margin = 24 * 1024
        print("room: %d KB free, the deploy adds %+d KB" % (info["free"] // 1024, grow // 1024))
        if grow + margin > info["free"]:
            sys.exit(("not enough room on the device: it needs %d KB more than it has (with a %d KB "
                      "margin). Nothing has been changed. Remove files the build no longer has "
                      "(an old page in www/, say) and try again.")
                     % ((grow + margin - info["free"]) // 1024, margin // 1024))
    except (ValueError, KeyError, IndexError):
        print("  (could not measure the room left - going ahead)")

    # --- modules, then pages, then the shim last ----------------------------
    # main.py goes last on purpose: until it lands the device still boots the
    # old application, so an interrupted deploy leaves something that runs.
    ordered = [f for f in files if f != "main.py"] + \
              [f for f in files if f == "main.py"]

    for f in ordered:
        print("  -> %s" % f)
        r = run(a.port, ["fs", "cp", os.path.join(BUILD, f), ":" + f])
        if r.returncode != 0:
            sys.exit("failed on %s: %s" % (f, r.stderr or r.stdout))

    run(a.port, ["exec",
                 "import os\ntry:\n os.mkdir('www')\nexcept OSError:\n pass"])
    for f in pages:
        print("  -> www/%s" % f)
        src = os.path.join(BUILD, "www", f)
        size = os.path.getsize(src)
        # USB copies run at about 20 KB/s: a big file (the Android app, 3 MB)
        # needs far longer than the usual limit - allow for its size.
        limit = max(120, size // 10000 + 60)
        if size > 200000:
            # and it goes in under another name, renamed only once complete,
            # so a copy cut short never leaves a broken file in its place
            r = run(a.port, ["fs", "cp", src, ":www/" + f + ".part"], timeout=limit)
            if r.returncode == 0:
                r = run(a.port, ["exec",
                                 "import os\ntry:\n os.remove('www/%s')\nexcept OSError:\n pass\n"
                                 "os.rename('www/%s.part', 'www/%s')" % (f, f, f)])
        else:
            r = run(a.port, ["fs", "cp", src, ":www/" + f], timeout=limit)
        if r.returncode != 0:
            sys.exit("failed on www/%s: %s" % (f, r.stderr or r.stdout))

    for f in stale:
        print("  rm %s" % f)
        run(a.port, ["exec",
                     "import os\ntry:\n os.remove(%r)\nexcept OSError:\n pass" % f])

    print()
    print("resetting ...")
    run(a.port, ["reset"], timeout=30)
    print("done. Watch it come up with:")
    print("  mpremote connect %s" % a.port)


if __name__ == "__main__":
    main()
