# indicate.py - the hub's own buzzer and RGB light, for alerts.
#
# The Waveshare RP2350-Relay-6CH-W has a buzzer (GPIO 23, driven at about
# 2 kHz) and one WS2812 RGB LED (GPIO 36). Its other three LEDs are wired to
# the power supply and the RS485 lines, out of software's reach. A board
# without them - the Pico W - gets neither, and nothing here touches a pin.
# BUZZER_PIN and RGB_PIN in config.py override the board's own.
#
# What they say, most urgent first:
#   panic                 red/blue, fast       a siren of rising beeps
#   alarm (danger)        red/blue, two and two  two beeps every 2.5 s
#   warning               amber, flashing      one short beep every 10 s
#   cleared, still there  amber, slow pulse    silent
#   hotspot fallback      white, slow blink    silent (no known network)
#   all well              dim gold             silent (off with the status light)
# Above all of them, while the BOOT button is held for a factory reset (main.py):
#   counting down         red, flashing        a beep each second
#   resetting             white                one long beep
# The buzzer sounds only with the hub's alert sound on (ALERT_SOUND), and stops
# as soon as the alert is cleared on any screen.

import asyncio
import sys
import time

_buz = None
_led = None
_last = None
# Set by main.py while BOOT is held: ms held so far, or "go" as it resets.
# Sounds whatever the alert sound setting: it answers a hand on the button.
reset_hold = None


def init(cfg):
    """Set up whatever this board has. Returns a note for the log."""
    global _buz, _led
    relay_board = "Relay-6CH" in getattr(sys.implementation, "_machine", "")
    bp = getattr(cfg, "BUZZER_PIN", 23 if relay_board else None)
    lp = getattr(cfg, "RGB_PIN", 36 if relay_board else None)
    from machine import Pin, PWM
    if bp is not None:
        _buz = PWM(Pin(bp))
        _buz.freq(2000)
        _buz.duty_u16(0)
    if lp is not None:
        import neopixel
        _led = neopixel.NeoPixel(Pin(lp), 1)
        _set((0, 0, 0))
    return "buzzer %s, light %s" % ("GPIO %d" % bp if _buz else "none",
                                    "GPIO %d" % lp if _led else "none")


def present():
    return _buz is not None or _led is not None


def _set(rgb):
    global _last
    if _led is None or rgb == _last:
        return
    _led[0] = rgb
    _led.write()
    _last = rgb


_hz = 0


def _tone(hz):
    global _hz
    if _buz is None or hz == _hz:
        return
    _hz = hz
    if hz:
        _buz.freq(hz)
        _buz.duty_u16(32768)
    else:
        _buz.duty_u16(0)


async def chirp():
    """Two quick rising notes: the hub has started."""
    for hz in (1800, 2600):
        _tone(hz)
        await asyncio.sleep_ms(70)
        _tone(0)
        await asyncio.sleep_ms(40)


def _pattern(st, t):
    """(colour, buzzer Hz or 0) at t ms into the pattern."""
    RED, BLUE = (90, 0, 0), (0, 0, 110)
    AMBER, OFF = (90, 40, 0), (0, 0, 0)
    sound = st["sound"]
    if st["panic"]:
        p = t % 400
        c = RED if p < 200 else BLUE
        hz = (1800 + (t % 1000) * 12 // 10) if sound else 0     # rising, over and over
        return c, hz
    if st["danger"]:
        p = t % 2500                    # two red, two blue, as the display's alarm
        c = (RED if p < 100 or 200 <= p < 300 else
             BLUE if 500 <= p < 600 or 700 <= p < 800 else OFF)
        hz = 2400 if sound and (p < 120 or 250 <= p < 370) else 0
        return c, hz
    if st["warn"]:
        c = AMBER if t % 1000 < 500 else OFF
        hz = 2000 if sound and t % 10000 < 150 else 0
        return c, hz
    if st["cleared"]:
        p = t % 3000                    # a slow pulse up and down
        k = p if p < 1500 else 3000 - p
        return (60 * k // 1500, 25 * k // 1500, 0), 0
    if st["fallback"]:
        return ((40, 40, 40) if t % 2000 < 150 else OFF), 0
    return ((12, 7, 0) if st["status_led"] else OFF), 0


async def loop(get_state):
    """Runs for ever: get_state() -> {"panic", "danger", "warn", "cleared",
    "fallback", "status_led", "sound"}, asked a few times a second."""
    if not present():
        return
    st = None
    kind = None
    start = time.ticks_ms()
    n = 0
    while True:
        if n % 5 == 0:                  # the state every 250 ms, the pattern every 50
            try:
                st = get_state()
            except Exception as e:
                print("indicate:", e)
            k = [x for x in ("panic", "danger", "warn", "cleared", "fallback") if st and st.get(x)]
            k = k[0] if k else None
            if k != kind:               # a new state starts its pattern from the top
                kind = k
                start = time.ticks_ms()
        n += 1
        if reset_hold is not None:
            if reset_hold == "go":
                _set((80, 80, 80))
                _tone(1500)
            else:
                p = reset_hold % 1000
                _set((110, 0, 0) if p < 500 else (0, 0, 0))
                _tone(2600 if p < 120 else 0)
        elif st is not None:
            c, hz = _pattern(st, time.ticks_diff(time.ticks_ms(), start))
            _set(c)
            _tone(hz)
        await asyncio.sleep_ms(50)
