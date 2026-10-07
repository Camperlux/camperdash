# FT6336U capacitive touch controller, on I2C at 0x38.
#
# Pins from Freenove's sketches for this board: SDA 16, SCL 15, reset 18,
# interrupt 17. The interrupt line is not used - polling every 20 ms is
# simpler and costs nothing noticeable.
#
# The controller reports in the panel's native portrait coordinates, which do
# not match the screen once it is rotated. Rather than deriving the mapping from
# datasheets, main.py has the user tap two targets once, and stores the result
# in touch_cal.json. See solve().

import time
from machine import Pin, I2C

ADDR = 0x38


class FT6336:
    def __init__(self, sda=16, scl=15, rst=18, i2c_id=0, freq=400000):
        r = Pin(rst, Pin.OUT, value=1)
        r(0)
        time.sleep_ms(10)
        r(1)
        time.sleep_ms(300)                   # the controller needs a moment
        self.i2c = I2C(i2c_id, sda=Pin(sda), scl=Pin(scl), freq=freq)
        self._buf = bytearray(5)
        self._buf2 = bytearray(11)
        self.cal = None                      # see solve()

    def present(self):
        try:
            return ADDR in self.i2c.scan()
        except OSError:
            return False

    def raw(self):
        """(x, y) of the first touch in panel coordinates, or None."""
        try:
            self.i2c.readfrom_mem_into(ADDR, 0x02, self._buf)
        except OSError:
            return None
        b = self._buf
        if not (b[0] & 0x0F):
            return None
        return ((b[1] & 0x0F) << 8) | b[2], ((b[3] & 0x0F) << 8) | b[4]

    def map(self, p):
        """Panel coordinates to screen coordinates, using the calibration."""
        if p is None or self.cal is None:
            return p
        swap, ax, bx, ay, by, w, h = self.cal
        x, y = p
        if swap:
            x, y = y, x
        x = int(ax * x + bx)
        y = int(ay * y + by)
        return (0 if x < 0 else w - 1 if x >= w else x,
                0 if y < 0 else h - 1 if y >= h else y)

    def read(self):
        return self.map(self.raw())

    def raw_all(self):
        """Every finger on the panel - it tracks two - in panel coordinates.
        Registers 0x02 (how many), 0x03-0x06 the first, 0x09-0x0C the second."""
        try:
            self.i2c.readfrom_mem_into(ADDR, 0x02, self._buf2)
        except OSError:
            return []
        b = self._buf2
        n = b[0] & 0x0F
        out = []
        if n >= 1:
            out.append((((b[1] & 0x0F) << 8) | b[2], ((b[3] & 0x0F) << 8) | b[4]))
        if n >= 2:
            out.append((((b[7] & 0x0F) << 8) | b[8], ((b[9] & 0x0F) << 8) | b[10]))
        return out

    def read_all(self):
        """Every finger, in screen coordinates: a list of (x, y), up to two."""
        return [self.map(p) for p in self.raw_all()]

    @staticmethod
    def solve(raw0, raw1, tgt0, tgt1, w, h):
        """Fit the mapping from two taps on targets at opposite corners.

        Which raw axis is the screen's x is decided by the targets: the screen
        axis the targets are further apart along must come from the raw axis
        that moved further. Each axis is then a straight line through the two
        points, which takes care of flipping and of any difference in scale
        between the controller's numbers and the screen's pixels.
        """
        rdx = abs(raw1[0] - raw0[0])
        rdy = abs(raw1[1] - raw0[1])
        tdx = abs(tgt1[0] - tgt0[0])
        tdy = abs(tgt1[1] - tgt0[1])
        swap = (rdy > rdx) == (tdx > tdy)
        if swap:
            raw0 = (raw0[1], raw0[0])
            raw1 = (raw1[1], raw1[0])
        if raw1[0] == raw0[0] or raw1[1] == raw0[1]:
            return None                      # a tap did not register properly
        ax = (tgt1[0] - tgt0[0]) / (raw1[0] - raw0[0])
        ay = (tgt1[1] - tgt0[1]) / (raw1[1] - raw0[1])
        return (swap, ax, tgt0[0] - ax * raw0[0], ay, tgt0[1] - ay * raw0[1], w, h)
