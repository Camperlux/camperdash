# rs485.py - the RS485 port on the Waveshare RP2350-Relay-6CH-W, for later use
# (extra relay boards, sensors, a Modbus battery or charger).
#
# UART1, TX on GPIO 24 and RX on GPIO 25, as Waveshare's demo. The board's
# transceiver switches between sending and listening by itself, so there is no
# direction pin to drive - but it does not listen while it sends, so a device
# only ever hears the other end, never its own words. Most RS485 kit speaks
# Modbus RTU: a frame is address, function, data, then a CRC-16 (low byte
# first), and a gap of 3.5 characters ends it.
#
#   bus = RS485()
#   bus.send(bytes((6, 5, 0, 1, 0x55, 0)))          # adds the CRC
#   reply = await bus.request(bytes((1, 3, 0, 0, 0, 2)))   # read 2 registers

import asyncio
import time

from machine import UART, Pin


def modbus_crc(buf):
    """CRC-16/MODBUS: polynomial 0xA001 (reversed 0x8005), from 0xFFFF."""
    crc = 0xFFFF
    for b in buf:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc


def with_crc(frame):
    c = modbus_crc(frame)
    return bytes(frame) + bytes((c & 0xFF, c >> 8))


def crc_ok(frame):
    return len(frame) >= 4 and modbus_crc(frame[:-2]) == frame[-2] | frame[-1] << 8


class RS485:
    def __init__(self, baud=9600, uart_id=1, tx=24, rx=25):
        self.u = UART(uart_id, baudrate=baud, tx=Pin(tx), rx=Pin(rx),
                      rxbuf=512, timeout=0)
        # the silence that ends a Modbus frame: 3.5 characters of 11 bits,
        # and never under 2 ms (the spec's floor above 19200 baud is 1.75 ms)
        self.gap_ms = max(2, (35 * 11 * 1000) // (10 * baud) + 1)

    def send(self, frame, crc=True):
        """Put a frame on the bus (with its CRC added, unless crc=False)."""
        while self.u.any():                      # drop anything stale first
            self.u.read()
        self.u.write(with_crc(frame) if crc else frame)
        self.u.flush()

    async def receive(self, timeout_ms=500):
        """The next frame from the bus, or None if nothing came in time. A
        frame ends when the line has been quiet for gap_ms."""
        start = time.ticks_ms()
        while not self.u.any():
            if time.ticks_diff(time.ticks_ms(), start) > timeout_ms:
                return None
            await asyncio.sleep_ms(2)
        buf = bytearray()
        last = time.ticks_ms()
        while True:
            n = self.u.any()
            if n:
                buf += self.u.read(n)
                last = time.ticks_ms()
            elif time.ticks_diff(time.ticks_ms(), last) >= self.gap_ms:
                return bytes(buf)
            await asyncio.sleep_ms(1)

    async def request(self, frame, timeout_ms=500):
        """Send a Modbus request and return the reply if its CRC checks out,
        else None."""
        self.send(frame)
        r = await self.receive(timeout_ms)
        return r if r and crc_ok(r) else None
