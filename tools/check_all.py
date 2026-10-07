"""Every check in one go: the tests, the games, the display's layout, the
compiles for both boards and the web pages' scripts. Exits non-zero if any
fails, so it can gate a deploy or run on GitHub.

  python tools/check_all.py

Needs: Pillow (the previews), mpy-cross matching the boards' MicroPython
(1.29), and optionally cryptography (the Victron test) and Node (the page
scripts) - a check whose tool is missing is reported as skipped, not passed.
"""
import glob
import os
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = tempfile.mkdtemp(prefix="camperdash_checks_")
results = []                 # (name, "pass" | "FAIL" | "skip", detail)


def run(args, timeout=600):
    r = subprocess.run(args, cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=timeout)
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def record(name, ok, detail="", skip=False):
    results.append((name, "skip" if skip else "pass" if ok else "FAIL", detail))
    print("%-5s %s%s" % (results[-1][1], name, ("  -  " + detail) if detail else ""), flush=True)


# ---- the tests ----
for t in sorted(glob.glob(os.path.join(ROOT, "tests", "test_*.py"))):
    name = "tests/" + os.path.basename(t)
    code, out = run([sys.executable, t])
    last = [ln for ln in out.strip().splitlines() if ln.strip()][-1:] or [""]
    if code == 0 and "skipped" in out.lower() and "ALL OK" not in out:
        record(name, True, last[0], skip=True)
    else:
        record(name, code == 0 and "ALL OK" in out, last[0] if code == 0 else out[-600:])

# ---- the games, and the display's layout ----
code, out = run([sys.executable, "tools/test_games.py", os.path.join(OUT, "games")])
games = len(re.findall(r"no errors", out))
record("games run on the PC", code == 0 and games >= 10, "%d games, no errors" % games
       if code == 0 else out[-600:])
code, out = run([sys.executable, "tools/preview_display.py", os.path.join(OUT, "pages")])
record("display layout (no text out of its box)",
       code == 0 and "no text runs out" in out, "" if code == 0 else out[-600:])

# ---- compiles for both boards ----
try:
    import mpy_cross                                             # noqa: F401
    have_mpy = True
except ImportError:
    have_mpy = False
for folder, extra, label in (("pico", [], "hub (RP2350)"), ("display", ["-march=xtensawin"], "display (ESP32-S3)")):
    if not have_mpy:
        record("compiles for the %s" % label, False, "mpy-cross not installed", skip=True)
        continue
    bad = []
    for f in sorted(glob.glob(os.path.join(ROOT, folder, "*.py"))):
        if os.path.basename(f).startswith("config"):
            continue
        code, out = run([sys.executable, "-m", "mpy_cross"] + extra +
                        ["-o", os.path.join(OUT, "x.mpy"), f])
        if code:
            bad.append("%s: %s" % (os.path.basename(f), out.strip()[-200:]))
    record("compiles for the %s" % label, not bad, "; ".join(bad))

# ---- the web pages' scripts ----
node = shutil.which("node")
pages = sorted(glob.glob(os.path.join(ROOT, "static", "*.html"))) + [os.path.join(ROOT, "static", "common.js")]
if not node:
    record("web page scripts", False, "Node not installed", skip=True)
else:
    bad = []
    for p in pages:
        src = open(p, encoding="utf-8").read()
        js = src if p.endswith(".js") else "\n".join(re.findall(r"<script>(.*?)</script>", src, re.S))
        if not js.strip():
            continue
        tmp = os.path.join(OUT, "check.js")
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(js)
        code, out = run([node, "--check", tmp])
        if code:
            bad.append("%s: %s" % (os.path.basename(p), out.strip()[-200:]))
    record("web page scripts", not bad, "; ".join(bad))

failed = [r for r in results if r[1] == "FAIL"]
skipped = [r for r in results if r[1] == "skip"]
print("\n%d passed, %d failed, %d skipped" % (len(results) - len(failed) - len(skipped),
                                             len(failed), len(skipped)))
shutil.rmtree(OUT, ignore_errors=True)
sys.exit(1 if failed else 0)
