# Shutting the display down, and starting it again by plugging it in to charge.
#
# The buttons are on the back, out of reach once it is mounted, so "off" is a
# deep sleep instead: nearly everything powered down, and a timer waking the
# processor for a moment every WAKE_S seconds to read the battery. The board
# has no charger-status line and its USB power reaches no pin, so the battery
# voltage is all there is to go on - but that is enough: a charger plugged in
# lifts it at once, by more than a slow charge or a resting cell ever moves it
# in a minute. Seeing that jump, boot.py lets main.py start as usual; otherwise
# it is straight back to sleep. The check takes a fraction of a second.

import machine
import time

BATT_PIN = 9
WAKE_S = 60                 # how often to look; at most this long to start once plugged in
JUMP_V = 0.03               # a rise this big since the last look: a charger
CLIMB_V = 0.08              # or this far above the lowest since shutting down
MAGIC = b"CLXOFF1 "
_QUIET = ((1, 1), (18, 0))  # held through the sleep: amplifier off (high), touch in reset


def batt_v():
    """The battery, in volts (the board halves it on the way to the pin)."""
    adc = machine.ADC(machine.Pin(BATT_PIN), atten=machine.ADC.ATTN_11DB)
    s = 0
    for _ in range(64):
        s += adc.read_uv()
    return s / 64 * 2 / 1e6


def _state():
    m = machine.RTC().memory()
    if not m.startswith(MAGIC):
        return None
    try:
        last, low, left = m[len(MAGIC):].decode().split()
        return float(last), float(low), int(left)
    except (ValueError, UnicodeError):
        return None


def _sleep(last, low, left=-1):
    machine.RTC().memory(MAGIC + ("%.3f %.3f %d" % (last, low, left)).encode())
    for pin, v in _QUIET:
        machine.Pin(pin, machine.Pin.OUT, value=v, hold=True)
    machine.deepsleep(WAKE_S * 1000)


def _wake():
    machine.RTC().memory(b"")
    for pin, _ in _QUIET:
        machine.Pin(pin, hold=False)


def check():
    """From boot.py, on waking from deep sleep: back to sleep unless charging
    has started (or the test count has run out). Returns only to start up."""
    st = _state()
    if st is None:
        return                                   # not our sleep: start as usual
    last, low, left = st
    v = batt_v()
    print("shut down: battery %.3f V (last %.3f, lowest %.3f)" % (v, last, low))
    # no sensible reading - no battery, running from USB alone - start too
    if v - last > JUMP_V or v - low > CLIMB_V or not 3.0 < v < 4.45 or left == 0:
        _wake()
        return
    _sleep(v, min(low, v), left - 1 if left > 0 else -1)


def shut_down(disp=None, led=None, i2c=None, test_cycles=-1):
    """Everything off, and sleep until a charger is plugged in. test_cycles:
    start again after that many looks regardless (for trying it on the bench,
    where the charger is always in). Does not return."""
    try:
        if led is not None:
            led[0] = (0, 0, 0)
            led.write()
    except Exception:
        pass
    if disp is not None:
        try:
            disp.backlight(0)
            disp.sleep(True)
        except Exception:
            pass
    if i2c is not None:
        # the codec: DAC and analogue side down, clocks off (Espressif's
        # es8311 suspend, as far as this board uses the codec)
        try:
            for reg, val in ((0x12, 0x02), (0x0E, 0xFF), (0x0D, 0xFC), (0x01, 0x00)):
                i2c.writeto_mem(0x18, reg, bytes((val,)))
        except OSError:
            pass
    time.sleep_ms(50)
    v = batt_v()
    _sleep(v, v, test_cycles)
