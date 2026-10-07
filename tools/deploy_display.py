"""Install the van display's software onto the Freenove FNK0104S, over USB.

Dry run unless you pass --go. Modules are compiled to .mpy first, with the
same mpy-cross as the hub build (the ABI check is repeated on the device, as
for the hub). Two files stay as source: main.py, which MicroPython runs by
name, and config.py, which holds this unit's WiFi passwords and is left alone
unless --with-config is given.

  python tools/make_fonts.py                           # only when fonts change
  python tools/deploy_display.py --port COM11          # dry run
  python tools/deploy_display.py --port COM11 --go --with-config
"""

import argparse
import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "display")
OUT = os.path.join(ROOT, "build", "display")
KEEP_SOURCE = ("main.py", "boot.py", "config.py")
SKIP = ("config.example.py",)
# run on the display after a USB install: no update left half-done
OTA_CLEAN = "\n".join((
    "import os",
    "for d in ('ota_new', 'ota_prev'):",
    " try:",
    "  [os.remove(d + '/' + n) for n in os.listdir(d)]",
    " except OSError:",
    "  pass",
    "for n in ('ota_pending.json', 'ota_bad.txt'):",
    " try:",
    "  os.remove(n)",
    " except OSError:",
    "  pass"))


def mp(port, *args, timeout=120):
    r = subprocess.run(["mpremote", "connect", port] + list(args), capture_output=True,
                       text=True, encoding="utf-8", errors="replace", timeout=timeout)
    return r


def build():
    if os.path.isdir(OUT):
        shutil.rmtree(OUT)
    os.makedirs(os.path.join(OUT, "fonts"))
    for fn in sorted(os.listdir(SRC)):
        p = os.path.join(SRC, fn)
        if not fn.endswith(".py") or fn in SKIP:
            continue
        if fn in KEEP_SOURCE:
            shutil.copy2(p, os.path.join(OUT, fn))
            continue
        # -march: aa.py's @micropython.viper loops are machine code, which
        # mpy-cross can only produce for a named processor - the ESP32-S3's
        r = subprocess.run([sys.executable, "-m", "mpy_cross", "-march=xtensawin", "-o",
                            os.path.join(OUT, fn[:-3] + ".mpy"), p],
                           capture_output=True, text=True)
        if r.returncode:
            sys.exit("mpy-cross failed on %s\n%s" % (fn, r.stderr))
    # images (the screensaver logo, from tools/make_logo.py) go as they are
    for fn in os.listdir(SRC):
        if fn.endswith(".bin"):
            shutil.copy2(os.path.join(SRC, fn), os.path.join(OUT, fn))
    for fn in os.listdir(os.path.join(SRC, "fonts")):
        shutil.copy2(os.path.join(SRC, "fonts", fn), os.path.join(OUT, "fonts", fn))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", required=True)
    ap.add_argument("--go", action="store_true")
    ap.add_argument("--with-config", action="store_true")
    a = ap.parse_args()

    if a.with_config and not os.path.isfile(os.path.join(SRC, "config.py")):
        sys.exit("no display/config.py - copy config.example.py and fill it in")
    build()
    files = sorted(f for f in os.listdir(OUT) if os.path.isfile(os.path.join(OUT, f)))
    if not a.with_config and "config.py" in files:
        files.remove("config.py")
    fonts = sorted(os.listdir(os.path.join(OUT, "fonts")))
    total = sum(os.path.getsize(os.path.join(OUT, f)) for f in files)
    print("would copy %d file(s), %d bytes, and %d font(s)" % (len(files), total, len(fonts)))
    for f in files:
        print("   %-18s %7d" % (f, os.path.getsize(os.path.join(OUT, f))))
    if not a.with_config:
        print("config.py: left alone (pass --with-config to overwrite it)")
    if not a.go:
        print("dry run - nothing was written. Re-run with --go to apply.")
        return

    probe = next(f for f in files if f.endswith(".mpy"))
    r = mp(a.port, "fs", "cp", os.path.join(OUT, probe), ":_abi_probe.mpy",
           "+", "exec", "import _abi_probe; print('abi ok')")
    mp(a.port, "exec", "import os\ntry:\n os.remove('_abi_probe.mpy')\nexcept OSError:\n pass")
    if "abi ok" not in (r.stdout or ""):
        sys.exit("the device rejected the bytecode - nothing changed.\n%s%s"
                 % (r.stdout, r.stderr))
    print("ABI ok")

    # main.py last, so an interrupted copy still boots the previous version
    order = [f for f in files if f != "main.py"] + [f for f in files if f == "main.py"]
    mp(a.port, "exec", "import os\ntry:\n os.mkdir('fonts')\nexcept OSError:\n pass")
    args = []
    for f in fonts:
        args += ["fs", "cp", os.path.join(OUT, "fonts", f), ":fonts/" + f, "+"]
    for f in order:
        args += ["fs", "cp", os.path.join(OUT, f), ":" + f, "+"]
    r = mp(a.port, *args[:-1], timeout=300)
    if r.returncode:
        sys.exit("copy failed: %s" % (r.stderr or r.stdout))
    print(r.stdout.strip())
    # MicroPython imports name.py in preference to name.mpy, so a source file
    # left behind by hand would quietly shadow the module just installed.
    built = [f[:-4] for f in files if f.endswith(".mpy")]
    r = mp(a.port, "exec",
           "import os\nfor n in %r:\n try:\n  os.remove(n + '.py')\n"
           "  print('removed stale', n + '.py')\n except OSError:\n  pass" % built)
    if r.stdout.strip():
        print(r.stdout.strip())
    # Updates over the air (display/ota.py): the update key, and a record of
    # exactly what is now installed, so the first update fetches only what
    # changes. A USB install replaces any update part-way through.
    import json
    import publish_display as pd
    man = pd.manifest(pd.version())
    with open(os.path.join(OUT, "ota_key.txt"), "w") as f:
        f.write(pd.key())
    with open(os.path.join(OUT, "ota_installed.json"), "w") as f:
        json.dump(pd.installed_state(man), f)
    r = mp(a.port, "fs", "cp", os.path.join(OUT, "ota_key.txt"), ":ota_key.txt", "+",
           "fs", "cp", os.path.join(OUT, "ota_installed.json"), ":ota_installed.json", "+",
           "exec", OTA_CLEAN)
    if r.returncode:
        print("could not set up updates over the air:", (r.stderr or r.stdout).strip())
    else:
        print("updates over the air: key installed, version", man["version"])
    mp(a.port, "reset", timeout=30)
    print("done - the display restarts now")


if __name__ == "__main__":
    main()
