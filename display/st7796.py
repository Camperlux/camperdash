# ST7796 driver for the Freenove FNK0104S (4.0", 320x480, SPI).
#
# The whole screen lives in one RGB565 frame buffer in PSRAM (300 KB) and is
# sent in one go. At 80 MHz that is about 31 ms, which is quicker than working
# out which parts changed, and it means nothing on screen can be left stale.
#
# Byte order: the panel wants each pixel big-endian, framebuf stores RGB565
# little-endian. Rather than swapping 150,000 pixels on every frame, colours
# are created already swapped (see gfx.rgb), so the buffer can be sent as is.
#
# Pins are from Freenove's TFT_eSPI setup for this board
# (FNK0104S_4.0_320x480_ST7796.h). There is no reset line and no MISO.

import time
import framebuf
from machine import Pin, SPI, PWM

# MADCTL for each rotation, as TFT_eSPI sets them for this panel (BGR order).
_MADCTL = (0x48, 0x28, 0x88, 0xE8)

_INIT = (
    (0x01, b"", 120),                     # software reset
    (0x11, b"", 120),                     # sleep out
    (0xF0, b"\xC3", 0),                   # command set control: enable part 1
    (0xF0, b"\x96", 0),                   # ...and part 2
    (0x3A, b"\x55", 0),                   # 16 bits per pixel
    (0xB4, b"\x01", 0),                   # 1-dot inversion
    (0xB6, b"\x80\x02\x3B", 0),           # display function control
    (0xE8, b"\x40\x8A\x00\x00\x29\x19\xA5\x33", 0),
    (0xC1, b"\x06", 0),
    (0xC2, b"\xA7", 0),
    (0xC5, b"\x18", 120),                 # VCOM
    (0xE0, b"\xF0\x09\x0B\x06\x04\x15\x2F\x54\x42\x3C\x17\x14\x18\x1B", 0),
    (0xE1, b"\xE0\x09\x0B\x06\x04\x03\x2B\x43\x42\x3B\x16\x14\x17\x1B", 0),
    (0xF0, b"\x3C", 0),                   # command set control: lock again
    (0xF0, b"\x69", 120),
    (0x21, b"", 0),                       # inversion on - this panel needs it
    (0x29, b"", 0),                       # display on
)


class ST7796:
    def __init__(self, rotation=1, sck=12, mosi=11, cs=10, dc=46, bl=45,
                 baudrate=80_000_000, spi_id=1):
        self.spi = SPI(spi_id, baudrate=baudrate, polarity=0, phase=0,
                       sck=Pin(sck), mosi=Pin(mosi))
        self.cs = Pin(cs, Pin.OUT, value=1)
        self.dc = Pin(dc, Pin.OUT, value=1)
        # 20 kHz is above hearing, so the backlight never whines.
        self._bl = PWM(Pin(bl), freq=20000, duty_u16=0)
        self.rotation = rotation & 3
        if self.rotation & 1:
            self.width, self.height = 480, 320
        else:
            self.width, self.height = 320, 480
        self.buf = bytearray(self.width * self.height * 2)
        self.fb = framebuf.FrameBuffer(self.buf, self.width, self.height,
                                       framebuf.RGB565)
        self._c1 = bytearray(1)
        self._w4 = bytearray(4)
        self._init()

    def _cmd(self, c, data=b""):
        # the command byte goes out of a buffer kept for it: a new bytes
        # object per command was garbage that, many times a second in the
        # animations, added up to a stutter when it was cleared away
        self.cs(0)
        self.dc(0)
        self._c1[0] = c
        self.spi.write(self._c1)
        if data:
            self.dc(1)
            self.spi.write(data)
        self.cs(1)

    def _init(self):
        for c, data, wait in _INIT:
            self._cmd(c, data)
            if wait:
                time.sleep_ms(wait)
        self._cmd(0x36, bytes((_MADCTL[self.rotation],)))

    def _window(self, x0, y0, x1, y1):
        b = self._w4
        b[0], b[1], b[2], b[3] = x0 >> 8, x0 & 255, x1 >> 8, x1 & 255
        self._cmd(0x2A, b)
        b[0], b[1], b[2], b[3] = y0 >> 8, y0 & 255, y1 >> 8, y1 & 255
        self._cmd(0x2B, b)

    def show(self, y0=0, y1=None):
        """Send rows y0..y1-1 of the frame buffer (default: all of it).

        Whole rows only, because a run of whole rows is one contiguous slice
        of the buffer and needs no copying.
        """
        if y1 is None:
            y1 = self.height
        self._window(0, y0, self.width - 1, y1 - 1)
        row = self.width * 2
        self.cs(0)
        self.dc(0)
        self.spi.write(b"\x2C")               # memory write
        self.dc(1)
        self.spi.write(memoryview(self.buf)[y0 * row:y1 * row])
        self.cs(1)

    def show_box(self, x, y, w, h, data):
        """Send one box of the screen, already gathered into data."""
        self._window(x, y, x + w - 1, y + h - 1)
        self.cs(0)
        self.dc(0)
        self.spi.write(b"\x2C")
        self.dc(1)
        self.spi.write(data)
        self.cs(1)

    def backlight(self, level):
        """0.0 (off) to 1.0 (full)."""
        level = 0.0 if level < 0 else 1.0 if level > 1 else level
        self._bl.duty_u16(int(level * 65535))

    def sleep(self, on):
        """Put the panel itself to sleep as well as the backlight."""
        self._cmd(0x10 if on else 0x11)
        time.sleep_ms(120)
