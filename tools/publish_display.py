"""Put the van display's software on the hub, for the display to fetch itself.

The display looks on the hub two minutes after it starts and then hourly (or
at once, from the hub's Settings: Van display, Update the display now). It
downloads what changed, checks it, and swaps it in at a quiet moment; if the
new version will not start, it puts the old one back. See display/ota.py.

The manifest - version, and each file's size and SHA-256 - is signed here with
the update key (.ota_key, next to this repository's README, never committed).
The display has the same key, installed over USB by deploy_display.py, and
takes nothing that is not signed with it: the hub only stores and serves.
KEEP THE KEY SAFE AND BACKED UP - without it, updates need USB again.

Not sent this way, only over USB: boot.py and otaboot (the safety net that
undoes a bad update), and config.py (this display's own settings).

  python tools/publish_display.py --hub-port COM13          # dry run
  python tools/publish_display.py --hub-port COM13 --go
"""

import argparse
import hashlib
import hmac
import json
import os
import secrets
import shutil
import subprocess
import sys
import time

TOOLS = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(TOOLS)
sys.path.insert(0, TOOLS)
import deploy_display as dd                                      # noqa: E402

KEY_FILE = os.path.join(ROOT, ".ota_key")
STAGE = os.path.join(ROOT, "build", "dispfw")
USB_ONLY = ("boot.py", "otaboot.mpy", "config.py")


def key():
    """The update key, made the first time."""
    if not os.path.isfile(KEY_FILE):
        with open(KEY_FILE, "w") as f:
            f.write(secrets.token_hex(32))
        print("made a new update key:", KEY_FILE, "- back it up")
    with open(KEY_FILE) as f:
        return f.read().strip()


def version():
    """When, and from which commit: 20260929-1830-abc1234 (+ '-dirty' if the
    display's source has changes not yet committed)."""
    v = time.strftime("%Y%m%d-%H%M")
    try:
        h = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT,
                           capture_output=True, text=True).stdout.strip()
        dirty = subprocess.run(["git", "status", "--porcelain", "display"], cwd=ROOT,
                               capture_output=True, text=True).stdout.strip()
        v += "-" + h + ("-dirty" if dirty else "")
    except OSError:
        pass
    return v


def files():
    """(path on the display, file here) for everything the air may carry."""
    out = []
    for f in sorted(os.listdir(dd.OUT)):
        p = os.path.join(dd.OUT, f)
        if os.path.isfile(p) and f not in USB_ONLY:
            out.append((f, p))
    for f in sorted(os.listdir(os.path.join(dd.OUT, "fonts"))):
        out.append(("fonts/" + f, os.path.join(dd.OUT, "fonts", f)))
    return out


def manifest(ver):
    items = []
    for path, local in files():
        with open(local, "rb") as fh:
            data = fh.read()
        items.append({"path": path, "blob": path.replace("/", "-"), "size": len(data),
                      "sha256": hashlib.sha256(data).hexdigest(), "_local": local})
    return {"version": ver, "files": items}


def signed(man):
    """The manifest as the hub serves it: its text, and that text's HMAC."""
    body = json.dumps({"version": man["version"],
                       "files": [{k: v for k, v in f.items() if not k.startswith("_")}
                                 for f in man["files"]]}, separators=(",", ":"))
    sig = hmac.new(bytes.fromhex(key()), body.encode(), hashlib.sha256).hexdigest()
    return {"body": body, "sig": sig}


def installed_state(man):
    """What deploy_display.py records on the display after a USB install."""
    return {"version": man["version"], "files": {f["path"]: f["sha256"] for f in man["files"]}}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hub-port", required=True, help="the hub's USB port, e.g. COM13")
    ap.add_argument("--go", action="store_true")
    a = ap.parse_args()

    dd.build()
    man = manifest(version())
    total = sum(f["size"] for f in man["files"])
    print("version %s: %d file(s), %d KB" % (man["version"], len(man["files"]), total // 1024))
    if not a.go:
        print("dry run - nothing was written. Re-run with --go to put it on the hub.")
        return

    if os.path.isdir(STAGE):
        shutil.rmtree(STAGE)
    os.makedirs(STAGE)
    for f in man["files"]:
        shutil.copy2(f["_local"], os.path.join(STAGE, f["blob"]))
    with open(os.path.join(STAGE, "manifest.json"), "w") as fh:
        json.dump(signed(man), fh)

    port = a.hub_port
    dd.mp(port, "exec", "import os\ntry:\n os.mkdir('dispfw')\nexcept OSError:\n pass")
    # the files first and the manifest last: a display asking half way through
    # still sees the old manifest, whose files are all still there
    args = []
    for f in man["files"]:
        args += ["fs", "cp", os.path.join(STAGE, f["blob"]), ":dispfw/" + f["blob"], "+"]
    r = dd.mp(port, *args[:-1], timeout=600)
    if r.returncode:
        sys.exit("copy to the hub failed: %s" % (r.stderr or r.stdout))
    r = dd.mp(port, "fs", "cp", os.path.join(STAGE, "manifest.json"), ":dispfw/manifest.json")
    if r.returncode:
        sys.exit("manifest copy failed: %s" % (r.stderr or r.stdout))
    keep = [f["blob"] for f in man["files"]] + ["manifest.json"]
    r = dd.mp(port, "exec",
              "import os\nfor n in os.listdir('dispfw'):\n if n not in %r:\n  os.remove('dispfw/' + n)\n"
              "  print('removed', n)" % keep)
    if r.stdout.strip():
        print(r.stdout.strip())
    print("on the hub. The display takes it within the hour, or at once from the hub's")
    print("Settings: Van display, Update the display now.")
    # mpremote stopped the hub's program to copy: start it again
    dd.mp(port, "reset", timeout=30)


if __name__ == "__main__":
    main()
