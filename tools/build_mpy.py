"""Build a bytecode deployment for the Pico W hub.

Every module is precompiled to .mpy with mpy-cross. Two things come out of that,
and the second matters more than the first:

  Size. The .mpy files are roughly a third of the source, which on a 2 MB flash
  is worth having but was never the problem.

  Peak RAM. Importing a .py file makes MicroPython compile it on the device: it
  builds a parse tree and a bytecode buffer in RAM, on top of whatever is
  already allocated, and then frees most of it again. That transient spike is
  what produced MemoryError at 5,000 bytes on a heap reporting far more free
  than that - the free space was there, but not in one contiguous piece. A .mpy
  is loaded, not compiled, so the spike does not happen at all.

Two files are deliberately left as source:

  config.py    It has to be editable on the device. It is also the one file that
               differs between units, and a unit whose settings can only be
               changed by rebuilding on a PC is a unit nobody can fix in a
               layby.

  main.py      MicroPython's start-up looks for main.py by name and will not
               run a main.mpy. The application is shipped as main_app.mpy and
               main.py becomes a two-line shim that imports it.

Run:  python tools/build_mpy.py
Then: python tools/deploy_mpy.py --port COM<n>        (dry run by default)
"""

import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "pico")
STATIC = os.path.join(ROOT, "static")
OUT = os.path.join(ROOT, "build")

# Left as readable source on the device, for the reasons in the docstring.
KEEP_SOURCE = ("config.py",)
# The entry point, which needs the shim treatment.
ENTRY = "main.py"
ENTRY_MODULE = "main_app"
# Never deployed: a template, and a bench tool.
SKIP = ("config.example.py", "heater_probe.py")

SHIM = '''# Boot shim - do not put anything else in here.
#
# MicroPython's start-up runs main.py by name and does not look for a main.mpy,
# so the application is compiled to %s.mpy and this hands over to it. The
# import runs it: %s.py executes at module level and never returns until the
# board resets.
import %s
''' % (ENTRY_MODULE, ENTRY_MODULE, ENTRY_MODULE)


def mpy_version():
    out = subprocess.run([sys.executable, "-m", "mpy_cross", "--version"],
                         capture_output=True, text=True, encoding="utf-8")
    return (out.stdout or out.stderr).strip()


def compile_one(src, dst):
    r = subprocess.run([sys.executable, "-m", "mpy_cross", "-o", dst, src],
                       capture_output=True, text=True, encoding="utf-8")
    if r.returncode != 0:
        sys.stderr.write("mpy-cross failed on %s\n%s\n%s\n"
                         % (src, r.stdout, r.stderr))
        sys.exit(1)


def main():
    if os.path.isdir(OUT):
        shutil.rmtree(OUT)
    os.makedirs(os.path.join(OUT, "www"))

    print(mpy_version())
    print()

    src_total = 0
    out_total = 0
    rows = []

    for fn in sorted(os.listdir(SRC)):
        path = os.path.join(SRC, fn)
        if not os.path.isfile(path) or not fn.endswith(".py"):
            continue
        if fn in SKIP:
            rows.append((fn, "-", "-", "not deployed"))
            continue
        size = os.path.getsize(path)
        src_total += size

        if fn in KEEP_SOURCE:
            shutil.copy2(path, os.path.join(OUT, fn))
            out_total += size
            rows.append((fn, size, size, "source (editable on device)"))
            continue

        if fn == ENTRY:
            # compile under its new name, then write the shim as main.py
            tmp = os.path.join(OUT, ENTRY_MODULE + ".py")
            shutil.copy2(path, tmp)
            dst = os.path.join(OUT, ENTRY_MODULE + ".mpy")
            compile_one(tmp, dst)
            os.remove(tmp)
            with open(os.path.join(OUT, "main.py"), "w", newline="\n") as f:
                f.write(SHIM)
            n = os.path.getsize(dst) + len(SHIM)
            out_total += n
            rows.append((fn, size, n, "-> %s.mpy + shim" % ENTRY_MODULE))
            continue

        dst = os.path.join(OUT, fn[:-3] + ".mpy")
        compile_one(path, dst)
        n = os.path.getsize(dst)
        out_total += n
        rows.append((fn, size, n, ""))

    # --- pages ---------------------------------------------------------------
    # static/ only. pico/www is a stale snapshot of an older deploy - its logo
    # is the 255 KB original rather than the optimised one the hub actually
    # serves - and mixing the two just raises the question of which wins.
    # Checked against the running hub: every page it serves matches static/.
    pages = 0
    for fn in sorted(os.listdir(STATIC)):
        p = os.path.join(STATIC, fn)
        if os.path.isfile(p):
            shutil.copy2(p, os.path.join(OUT, "www", fn))
    # The Android app, when it has been built (android_hub: gradlew
    # assembleDebug), for Settings to offer as a download. Signed with the
    # development key only - see android_hub. apk.json says which version.
    apk = os.path.join(ROOT, "android_hub", "app", "build", "outputs", "apk", "debug",
                       "app-debug.apk")
    if os.path.isfile(apk):
        import json
        import re
        ver = "?"
        try:
            with open(os.path.join(ROOT, "android_hub", "app", "build.gradle.kts")) as f:
                m = re.search(r'versionName\s*=\s*"([^"]+)"', f.read())
                ver = m.group(1) if m else "?"
        except OSError:
            pass
        shutil.copy2(apk, os.path.join(OUT, "www", "camperlux.apk"))
        with open(os.path.join(OUT, "www", "apk.json"), "w") as f:
            json.dump({"version": ver, "size": os.path.getsize(apk)}, f)
        print("android app: version %s, %d bytes" % (ver, os.path.getsize(apk)))
    for fn in sorted(os.listdir(os.path.join(OUT, "www"))):
        pages += os.path.getsize(os.path.join(OUT, "www", fn))

    # --- report --------------------------------------------------------------
    w = max(len(r[0]) for r in rows)
    print("%-*s %9s %9s  %s" % (w, "module", "source", "built", ""))
    print("-" * (w + 34))
    for fn, a, b, note in rows:
        if a == "-":
            print("%-*s %9s %9s  %s" % (w, fn, "-", "-", note))
        else:
            print("%-*s %9d %9d  %s" % (w, fn, a, b, note))
    print("-" * (w + 34))
    print("%-*s %9d %9d  %.0f%% smaller"
          % (w, "total", src_total, out_total,
             100.0 * (src_total - out_total) / src_total))
    print()
    print("pages: %d file(s), %d bytes" % (len(os.listdir(os.path.join(OUT, "www"))), pages))
    print("built into %s" % OUT)


if __name__ == "__main__":
    main()
