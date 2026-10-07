# Raw BLE central for the Pico W hub.
#
# aioble keeps only the most recent notification, so a BMS frame that arrives as
# a burst of 20-byte packets loses everything but the tail. Here the IRQ handler
# appends EVERY notification to a buffer synchronously, so multi-packet frames
# reassemble reliably. One connection at a time (we poll sequentially).
#
# Connection state is tracked by handle: a connect that completes after we gave
# up on it is dropped rather than hijacking the active link, and a disconnect
# event only clears our state if it is for the link we are actually using.

import bluetooth
import asyncio
from micropython import const

_SCAN_RESULT = const(5)
_SCAN_DONE = const(6)
_PERIPH_CONNECT = const(7)
_PERIPH_DISCONNECT = const(8)
_CHAR_RESULT = const(11)
_CHAR_DONE = const(12)
_WRITE_DONE = const(17)
_NOTIFY = const(18)


def _addr_str(addr):
    return ":".join("%02x" % b for b in addr)


def _parse_adv(payload):
    i, name, mfr = 0, None, {}
    while i + 1 < len(payload):
        ln = payload[i]
        if ln == 0:
            break
        t = payload[i + 1]
        val = payload[i + 2:i + 1 + ln]
        if t in (0x08, 0x09):
            try:
                name = str(bytes(val), "utf-8")
            except Exception:
                pass
        elif t == 0xFF and len(val) >= 2:
            mfr[val[0] | (val[1] << 8)] = bytes(val[2:])
        i += 1 + ln
    return name, mfr


class Central:
    def __init__(self):
        self._ble = bluetooth.BLE()
        self._ble.active(True)
        self._ble.irq(self._irq)
        self._conn = None
        self._connecting = False
        self._found = {}
        self._chars = {}
        self.notify_buf = bytearray()
        self._f_scan = asyncio.ThreadSafeFlag()
        self._f_conn = asyncio.ThreadSafeFlag()
        self._f_disc = asyncio.ThreadSafeFlag()
        self._f_char = asyncio.ThreadSafeFlag()
        self._f_write = asyncio.ThreadSafeFlag()

    def _irq(self, event, data):
        if event == _SCAN_RESULT:
            addr_type, addr, adv_type, rssi, adv = data
            a = _addr_str(addr)
            name, mfr = _parse_adv(bytes(adv))
            prev = self._found.get(a)
            # keep first-seen addr_type/name, merge manufacturer data
            if prev:
                pn, pm = prev[1], prev[3]
                pm.update(mfr)
                self._found[a] = (addr_type, pn or name, rssi, pm)
            else:
                self._found[a] = (addr_type, name, rssi, mfr)
        elif event == _SCAN_DONE:
            self._f_scan.set()
        elif event == _PERIPH_CONNECT:
            conn_handle, _, _ = data
            if self._connecting:
                self._conn = conn_handle
                self._connecting = False
                self._f_conn.set()
            else:
                # a connect we already gave up on: drop it, don't adopt it
                try:
                    self._ble.gap_disconnect(conn_handle)
                except Exception:
                    pass
        elif event == _PERIPH_DISCONNECT:
            conn_handle, _, _ = data
            if conn_handle == self._conn:
                self._conn = None
                self._f_disc.set()
        elif event == _CHAR_RESULT:
            _, _def, vhandle, _props, uuid = data
            self._chars[str(uuid)] = vhandle
        elif event == _CHAR_DONE:
            self._f_char.set()
        elif event == _WRITE_DONE:
            self._f_write.set()
        elif event == _NOTIFY:
            _, _vh, ndata = data
            self.notify_buf.extend(ndata)

    async def scan(self, ms=6000):
        self._found = {}
        self._f_scan = asyncio.ThreadSafeFlag()
        started = False
        for _ in range(2):
            try:
                self._ble.gap_scan(ms, 30000, 30000, True)
                started = True
                break
            except OSError:
                # a previous scan is still active: stop it and retry once
                try:
                    self._ble.gap_scan(None)
                except OSError:
                    pass
                await asyncio.sleep_ms(200)
        if not started:
            return self._found
        try:
            await asyncio.wait_for_ms(self._f_scan.wait(), ms + 2000)
        except asyncio.TimeoutError:
            try:
                self._ble.gap_scan(None)
            except OSError:
                pass
        return self._found

    async def connect(self, addr_type, addr, timeout=10000):
        if self._conn is not None:          # never stack a second link
            await self.disconnect()
        self._f_conn = asyncio.ThreadSafeFlag()
        self._conn = None
        self.notify_buf = bytearray()
        self._connecting = True
        try:
            self._ble.gap_connect(addr_type, addr)
        except OSError:
            self._connecting = False
            return False
        try:
            await asyncio.wait_for_ms(self._f_conn.wait(), timeout)
        except asyncio.TimeoutError:
            self._connecting = False
            try:
                self._ble.gap_connect(None)   # cancel the pending attempt
            except OSError:
                pass
            return False
        return self._conn is not None

    async def discover(self):
        self._chars = {}
        self._f_char = asyncio.ThreadSafeFlag()
        if self._conn is None:
            return self._chars
        self._ble.gattc_discover_characteristics(self._conn, 1, 0xFFFF)
        try:
            await asyncio.wait_for_ms(self._f_char.wait(), 5000)
        except asyncio.TimeoutError:
            pass
        return self._chars

    def clear_buf(self):
        self.notify_buf = bytearray()

    def handle(self, uuid):
        return self._chars.get(str(bluetooth.UUID(uuid)))

    async def enable_notify(self, value_handle, timeout=2000):
        """Write the CCCD (value_handle+1) and wait for the write to complete, so
        the next GATT operation isn't rejected with EALREADY."""
        if self._conn is None:
            return False
        self._f_write = asyncio.ThreadSafeFlag()
        try:
            self._ble.gattc_write(self._conn, value_handle + 1, b"\x01\x00", 1)
        except OSError:
            return False
        try:
            await asyncio.wait_for_ms(self._f_write.wait(), timeout)
        except asyncio.TimeoutError:
            pass
        return True

    def write_nr(self, value_handle, data):
        if self._conn is None:
            return False
        try:
            self._ble.gattc_write(self._conn, value_handle, data, 0)
            return True
        except OSError:
            return False

    async def disconnect(self):
        if self._conn is None:
            return
        self._f_disc = asyncio.ThreadSafeFlag()
        try:
            self._ble.gap_disconnect(self._conn)
            await asyncio.wait_for_ms(self._f_disc.wait(), 3000)
        except Exception:
            pass
        self._conn = None
