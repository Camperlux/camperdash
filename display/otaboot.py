# The safety net for updates over the air (ota.py). Run from boot.py before
# main.py, and installed only over USB - never over the air - so a bad update
# can never take away the thing that undoes it.
#
# After ota.apply() the new version is on trial. Each start counts; main.py
# calls confirm() once it has been running normally for a minute. If it does
# not get that far - it crashes (main.py restarts the display on an error) or
# hangs (a timer restarts it after 150 s without a feed()) - the third start
# puts the previous files back, and that version is not taken again.

import json
import os
import time

PENDING = "ota_pending.json"
INSTALLED = "ota_installed.json"
BAD = "ota_bad.txt"
PREV = "ota_prev"
HANG_MS = 150000

_timer = None
_fed = 0


def _load():
    try:
        with open(PENDING) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _save(st):
    with open(PENDING, "w") as f:
        json.dump(st, f)


def _rm(p):
    try:
        os.remove(p)
    except OSError:
        pass


def rollback(st, why):
    print("ota: putting the previous version back (%s)" % why)
    for path, blob, had in st.get("moved") or []:
        prev = PREV + "/" + blob
        try:
            os.stat(prev)
            _rm(path)
            os.rename(prev, path)
        except OSError:
            if not had:
                _rm(path)          # new in this version: not there before
    try:
        with open(BAD, "w") as f:
            f.write(st.get("version") or "")
    except OSError:
        pass
    _rm(PENDING)


def boot():
    """From boot.py. Starts the watch on a version on trial, or rolls back."""
    global _timer, _fed
    st = _load()
    if not st:
        return
    if not st.get("applied"):
        rollback(st, "the update was interrupted")
        return
    st["boots"] = st.get("boots", 0) + 1
    if st["boots"] > 2:
        rollback(st, "it did not start")
        return
    _save(st)
    print("ota: version %s on trial, start %d" % (st.get("version"), st["boots"]))
    _fed = time.ticks_ms()
    try:
        import machine
        _timer = machine.Timer(3)
        _timer.init(period=10000, callback=_check)
    except Exception as e:
        print("ota: no hang timer:", e)


def _check(_t):
    if time.ticks_diff(time.ticks_ms(), _fed) > HANG_MS:
        import machine
        machine.reset()


def feed():
    global _fed
    _fed = time.ticks_ms()


def on_trial():
    return _timer is not None


def confirm():
    """main.py has run normally: the new version stays."""
    global _timer
    st = _load()
    if not st or not st.get("applied"):
        return False
    try:
        with open(INSTALLED, "w") as f:
            json.dump(st["installed"], f)
    except OSError:
        return False
    for _, blob, _had in st.get("moved") or []:
        _rm(PREV + "/" + blob)
    _rm(PENDING)
    if _timer is not None:
        _timer.deinit()
        _timer = None
    print("ota: version %s confirmed" % st.get("version"))
    return True
