# Touch sounds through the board's speaker: ES8311 codec -> amplifier -> speaker.
#
# Pins from Freenove's audio example for this board (Sketch_07.1_Music, the
# FNK0104S branch): I2S bit clock 5, word select 7, data out 8; amplifier
# enable on 1 (low = on). The codec is on the touch controller's I2C bus, at
# 0x18, so it is set up through the same I2C object.
#
# MicroPython's I2S cannot drive the codec's MCLK pin, so the codec is told to
# take its master clock from the bit clock instead. The register values follow
# Espressif's es8311 driver (as shipped in the Freenove example) for that case:
# 16-bit stereo at 32 kHz makes a 1.024 MHz bit clock, which is one of the
# combinations in its clock table (pre-divide 1, multiply 8, bit clock /4).
#
# The sounds are short, computed once at start-up, and written into the I2S
# DMA buffer, which is larger than any of them - so playing one never blocks.
# When the buffer empties the driver sends silence.

import math
import struct
import time
from machine import I2S, Pin

ADDR = 0x18
RATE = 32000


def _tone(parts, volume=0.5, square=False):
    """Stereo 16-bit samples for a sequence of (frequency Hz, milliseconds).
    A frequency of 0 is a gap. Clicks get a quick attack and an exponential
    decay; square=True makes a flat, hard-edged tone for the alarm, which is
    meant to be heard over an engine, not to be pleasant."""
    out = bytearray()
    attack = max(1, RATE // 2000)              # 0.5 ms
    for freq, ms in parts:
        n = RATE * ms // 1000
        if not freq:
            out += bytes(n * 4)
            continue
        for i in range(n):
            ph = math.sin(2 * math.pi * freq * i / RATE)
            if square:
                env = min(1.0, i / attack, (n - i) / attack)
                v = int(32767 * volume * env * (1 if ph >= 0 else -1))
            else:
                env = min(1.0, i / attack) * math.exp(-5.0 * i / n)
                v = int(32767 * volume * env * ph)
            out += struct.pack("<hh", v, v)
    return out


def _glide(f0, f1, ms, volume=0.5, wobble=0.0, wob_hz=0):
    """A sliding tone from f0 to f1 Hz, with a wobble (a fraction of the pitch,
    wob_hz times a second) for a cartoon boing. Built from a sine table and a
    running phase, which a glide needs and which is quicker than math.sin."""
    n = RATE * ms // 1000
    tbl = [math.sin(2 * math.pi * i / 256) for i in range(256)]
    out = bytearray(n * 4)
    ph = 0.0
    amp = 32767 * volume
    for i in range(n):
        t = i / n
        f = f0 + (f1 - f0) * t
        if wobble:
            f *= 1 + wobble * tbl[int(wob_hz * i * 256 / RATE) & 255]
        ph += f / RATE
        env = min(1.0, i / 64) * (1 - t)
        v = int(amp * env * tbl[int(ph * 256) & 255])
        struct.pack_into("<hh", out, i * 4, v, v)
    return out


# The logo's Easter egg: a boing for each of the first two taps, the second
# higher, and a slide whistle and ta-da on the third, as the games open.
_EGG = (lambda: _glide(140, 420, 170, 0.5, 0.18, 28),
        lambda: _glide(240, 720, 170, 0.5, 0.18, 34),
        lambda: _glide(330, 1500, 230, 0.45, 0.06, 12) +
        _tone(((0, 30), (1175, 80), (1568, 180)), 0.45))


def siren_steps(seconds=1.6, volume=0.55, step=2048):
    """A police siren's wail - up from 650 Hz to 1350 Hz and back - as stereo
    16-bit samples, built in steps: yields None every `step` samples so the
    caller can let the rest of the display run, and the finished bytes last.
    Built from a 256-entry sine table in fixed point: the straightforward
    per-sample math.sin takes several seconds on the display."""
    from array import array
    tbl = array("h", [int(32767 * math.sin(2 * math.pi * i / 256)) for i in range(256)])
    n = int(RATE * seconds)
    out = bytearray(n * 4)
    phase = 0                                    # 16.16 fixed point, in table steps
    amp = int(volume * 1.6 * 256)                # over-driven, then clipped: harsher
    f = 650
    for i in range(n):
        if i & 63 == 0:                          # the pitch changes smoothly enough
            f = 650 + int(700 * (0.5 - 0.5 * math.cos(2 * math.pi * i / n)))
        phase = (phase + (f * 256 * 65536) // RATE) & 0xFFFFFF
        v = (tbl[phase >> 16] * amp) >> 8
        v = 32767 if v > 32767 else -32767 if v < -32767 else v
        j = i * 4
        out[j] = v & 255
        out[j + 1] = (v >> 8) & 255
        out[j + 2] = v & 255
        out[j + 3] = (v >> 8) & 255
        if i % step == 0:
            yield None
    # End on an upward zero crossing, where the wave begins, so the sound
    # loops without a click: the pitch is back at 650 Hz there, as at the start.
    k = n - 1
    while k > n - 400:
        j = k * 4
        a = out[j] | (out[j + 1] << 8)
        b = out[j - 4] | (out[j - 3] << 8)
        if b >= 32768 and a < 32768:            # previous negative, this one not
            break
        k -= 1
    yield bytes(out[:k * 4]) if k > n - 400 else out


class Sound:
    def __init__(self, i2c, sck=5, ws=7, sd=8, amp=1, volume=90, click_level=0.6):
        """volume sets the codec, and so the alarm; click_level (0-1) makes the
        touch sounds that much quieter than the alarm."""
        self.i2c = i2c
        self.enabled = True
        self._amp = Pin(amp, Pin.OUT, value=1)      # amplifier off while setting up
        self._pins = (sck, ws, sd)
        self._i2s = self._open()
        self._codec_init()
        self.volume(volume)
        self._base_reg = self._reg             # what "100" means
        self._click_pct = self._alarm_pct = 100
        self._amp(0)                                 # and on
        k = 0 if click_level < 0 else 1 if click_level > 1 else click_level
        self._sounds = {
            "tap": _tone(((2600, 18),), 0.35 * k),
            "page": _tone(((1800, 22),), 0.35 * k),
            "open": _tone(((1500, 30), (2250, 40)), 0.4 * k),
            "ok": _tone(((1400, 45), (2100, 70)), 0.45 * k),
            "bad": _tone(((420, 90), (320, 140)), 0.5 * k),
            # the web pages' alarm, two rising notes, at full strength
            "alarm": _tone(((880, 200), (0, 60), (1180, 200)), 0.6, square=True),
            # the kitchen timer: three soft rising notes
            "chime": _tone(((880, 160), (1109, 160), (1319, 320)), 0.5),
            # the alarm clock: a brighter little tune, repeated while it rings
            "wake": _tone(((784, 140), (988, 140), (1175, 140), (1568, 260), (0, 120),
                           (1175, 140), (1568, 320)), 0.5),
        }

    def _open(self):
        sck, ws, sd = self._pins
        return I2S(0, sck=Pin(sck), ws=Pin(ws), sd=Pin(sd), mode=I2S.TX,
                   bits=16, format=I2S.STEREO, rate=RATE, ibuf=72000)

    def _w(self, reg, val):
        self.i2c.writeto_mem(ADDR, reg, bytes((val,)))

    def _r(self, reg):
        return self.i2c.readfrom_mem(ADDR, reg, 1)[0]

    def _codec_init(self):
        w, r = self._w, self._r
        w(0x00, 0x1F)                    # reset
        time.sleep_ms(20)
        w(0x00, 0x00)
        w(0x00, 0x80)                    # power on
        w(0x01, 0xBF)                    # all clocks on, MCLK taken from the bit clock
        w(0x06, r(0x06) & ~0x20)         # bit clock not inverted
        # clock dividers for a 1.024 MHz internal MCLK at 32 kHz
        w(0x02, (r(0x02) & 0x07) | (0 << 5) | (3 << 3))
        w(0x03, 0x10)
        w(0x04, 0x10)
        w(0x05, 0x00)
        w(0x06, (r(0x06) & 0xE0) | (4 - 1))
        w(0x07, r(0x07) & 0xC0)
        w(0x08, 0xFF)
        # slave, standard I2S, 16 bits in and out
        w(0x00, r(0x00) & 0xBF)
        w(0x09, 0x0C)
        w(0x0A, 0x0C)
        # power up the analogue side and the DAC, output to the driver
        w(0x0D, 0x01)
        w(0x0E, 0x02)
        w(0x12, 0x00)
        w(0x13, 0x10)
        w(0x1C, 0x6A)
        w(0x37, 0x08)

    def volume(self, percent):
        percent = 0 if percent < 0 else 100 if percent > 100 else int(percent)
        reg = 0 if percent == 0 else percent * 256 // 100 - 1
        if reg != getattr(self, "_reg", None):
            self._w(0x32, reg)
            self._reg = reg

    def levels(self, click, alarm):
        """Loudness of the touch sounds and of the alarm, 0-100 each, as set on
        the hub. 100 is the level the display was tuned to; each step down is
        half a decibel (the codec's own step). Click 0 is silent."""
        self._click_pct = click
        self._alarm_pct = alarm

    def _reg_for(self, pct):
        if pct <= 0:
            return 0
        # 100 -> the codec level the sounds were tuned at; each point below is
        # one register step, 0.5 dB
        return max(1, self._base_reg - (100 - int(pct)))

    def egg(self, n):
        """The n-th tap on the logo (1-3): made the first time it is wanted,
        and a tenth of a second is no wait for an Easter egg."""
        n = max(1, min(3, n))
        name = "egg%d" % n
        if name not in self._sounds:
            self._sounds[name] = _EGG[n - 1]()
        self.play(name)

    def siren_ready(self):
        return "siren" in self._sounds

    async def build_siren(self):
        """Make the siren in the background, a few milliseconds at a time."""
        import asyncio
        for part in siren_steps():
            if part is None:
                await asyncio.sleep_ms(0)
            else:
                self._sounds["siren"] = part

    LOOP_CHUNK = 16384                          # 128 ms of sound per refill

    def loop(self, name):
        """Play a sound round and round until stop_loop(), smoothly.

        The audio driver refills itself: with a callback set, I2S.write()
        stops blocking and the callback asks for the next piece as each one
        is taken. So the sound never waits on the rest of the display - a
        slow screen redraw no longer leaves the speaker waiting, which is
        what made the siren choppy when it was fed from the main loop."""
        s = self._sounds.get(name)
        if not s:
            return False
        reg = self._reg_for(self._alarm_pct)
        if reg != self._reg:
            self._w(0x32, reg)
            self._reg = reg
        self._loop_mv = memoryview(s)
        self._loop_pos = 0
        self._looping = True

        def more(i2s):
            if not self._looping:
                return
            mv, p = self._loop_mv, self._loop_pos
            end = p + self.LOOP_CHUNK
            if end >= len(mv):
                end = len(mv)
                self._loop_pos = 0
            else:
                self._loop_pos = end
            self.loop_pieces = getattr(self, "loop_pieces", 0) + 1
            i2s.write(mv[p:end])

        self._owner = name
        self._i2s.irq(more)
        more(self._i2s)
        return True

    def owns(self, name):
        """Is the self-fed audio still playing name (not stopped)?"""
        return getattr(self, "_looping", False) and getattr(self, "_owner", None) == name

    def stop_loop(self):
        """Stop the loop and give the audio interface back in a clean state.

        Switching the driver back from self-feeding to ordinary writes with
        irq(None) leaves it so the next ordinary write never completes - found
        on the display: the first click after a panic froze it. So the
        interface is closed and opened afresh instead. The codec keeps its
        settings; it just sees the bit clock stop and start again."""
        self._looping = False
        self._loop_mv = None
        self._owner = None
        try:
            self._i2s.deinit()
        except Exception:
            pass
        self._i2s = self._open()

    def play(self, name, force=False):
        """force plays it even with touch sounds switched off - for the alarm,
        which follows the hub's own alert-sound setting instead."""
        if not (self.enabled or force):
            return
        s = self._sounds.get(name)
        if s and getattr(self, "_looping", False):
            return                  # the siren is feeding the interface: leave it be
        if s:
            # the alarm and the kitchen timer are meant to be heard across
            # the van, so they follow the alarm level, not the clicks'
            pct = self._alarm_pct if name in ("alarm", "chime", "wake") else self._click_pct
            if pct <= 0:
                return
            reg = self._reg_for(pct)
            if reg != self._reg:
                self._w(0x32, reg)
                self._reg = reg
            try:
                self._i2s.write(s)
            except OSError:
                pass
