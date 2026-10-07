# stalls.py - notices when the hub's program is held up, and by what.
#
# Everything on the hub takes turns in one asyncio loop: the level sensor, the
# Bluetooth polls, the web server, Wi-Fi. Anything that waits without handing
# over its turn - a Wi-Fi scan, starting the hotspot, a DNS lookup - stops all
# of them. On a drive round the block the level readings had a 9.6 s hole
# just as the hub left the home Wi-Fi, and this is how to see what caused one.
#
# watch() wakes every 100 ms; when it wakes much later than that, the loop was
# held, and the gap is recorded with the labels mark() was given in the
# meantime ("wifi scan", "hotspot start", ...) and the Wi-Fi state. The last 50
# are kept in memory and served at GET /api/stalls. A label is only a clue -
# it says what started during the gap, which is usually what held it.

import time

try:
    import asyncio
except ImportError:                 # the PC tests
    asyncio = None

THRESHOLD_MS = 500                  # a gap longer than this is recorded
KEEP = 50

_stalls = []                        # dicts, oldest first
_marks = []                         # labels given since the watcher last woke
_wifi = None                        # () -> (joined, hotspot on), set by main


def mark(label):
    """Note what the hub is about to do; seen only if a stall follows."""
    if len(_marks) < 8 and (not _marks or _marks[-1] != label):
        _marks.append(label)


def set_wifi_probe(fn):
    global _wifi
    _wifi = fn


async def watch():
    last = time.ticks_ms()
    while True:
        await asyncio.sleep_ms(100)
        now = time.ticks_ms()
        gap = time.ticks_diff(now, last) - 100
        if gap > THRESHOLD_MS:
            joined = ap = None
            if _wifi:
                try:
                    joined, ap = _wifi()
                except Exception:
                    pass
            _stalls.append({"t": time.time(), "ms": gap, "during": list(_marks),
                            "wifi": joined, "hotspot": ap})
            if len(_stalls) > KEEP:
                _stalls.pop(0)
            print("stall: %d ms during %s" % (gap, ", ".join(_marks) or "?"))
        del _marks[:]
        last = now


def records():
    """For GET /api/stalls."""
    return {"threshold_ms": THRESHOLD_MS, "stalls": list(_stalls)}
