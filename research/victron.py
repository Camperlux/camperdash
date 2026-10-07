"""Victron Blue Smart IP22 mains charger via BLE Instant Readout (passive).

Device: BSC IP22 12/30 (AA:BB:CC:DD:EE:FF). Victron broadcasts encrypted live
data in its BLE advertisement (manufacturer id 0x02E1); we decrypt it with the
per-device key from VictronConnect. No connection is made — this just listens,
so it runs happily alongside the BMS and Renogy GATT connections.
"""
from __future__ import annotations
import asyncio
import os
import time

from bleak import BleakScanner
from victron_ble.devices import detect_device_type

# The Victron advert key is a secret: it decrypts this charger's broadcasts, so
# it is kept out of the repo.  Set VICTRON_ADDR / VICTRON_KEY in the
# environment, or drop them in an untracked local_secrets.py next to this file.
try:
    from local_secrets import VICTRON_ADDR as _ADDR, VICTRON_KEY as _KEY
except ImportError:
    _ADDR = _KEY = ""
DEFAULT_ADDR = os.environ.get("VICTRON_ADDR", _ADDR)
DEFAULT_KEY = os.environ.get("VICTRON_KEY", _KEY)
VICTRON_MFR = 0x02E1
STALE_S = 30.0  # no advert for this long -> treat as off/unplugged


def _pretty(mode) -> str:
    return mode.name.replace("_", " ").title() if mode is not None else "Unknown"


class VictronMonitor:
    def __init__(self, address: str = DEFAULT_ADDR, key: str = DEFAULT_KEY):
        self.address = address.upper()
        self.key = key
        self.state: dict = {"connected": False, "updated": None}
        self._device = None
        self._scanner: BleakScanner | None = None

    def _cb(self, d, adv):
        if d.address.upper() != self.address:
            return
        raw = adv.manufacturer_data.get(VICTRON_MFR)
        if not raw:
            return
        raw = bytes(raw)
        try:
            if self._device is None:
                cls = detect_device_type(raw)
                if cls is None:
                    return
                self._device = cls(self.key)
            p = self._device.parse(raw)
            v = p.get_output_voltage1()
            a = p.get_output_current1()
            self.state = {
                "connected": True,
                "updated": time.time(),
                "model": p.get_model_name(),
                "state": _pretty(p.get_charge_state()),
                "voltage": v,
                "current": a,
                "power_w": round(v * a, 1) if v is not None and a is not None else None,
                "error": _pretty(p.get_charger_error()),
                "rssi": adv.rssi,
            }
        except Exception as e:  # noqa: BLE001 - bad/rotated key or malformed advert
            self.state = {"connected": False, "updated": time.time(),
                          "error": f"decode failed: {str(e)[:60]}"}

    def fresh(self) -> bool:
        return bool(self.state.get("connected") and self.state.get("updated")
                    and time.time() - self.state["updated"] < STALE_S)

    def snapshot(self) -> dict:
        """State with a stale flag folded in (charger stops advertising when off)."""
        s = dict(self.state)
        if s.get("connected") and not self.fresh():
            s["connected"] = False
            s["stale"] = True
        return s

    async def run(self, restart_period: float = 90.0):
        """Passive scanner, cycled every `restart_period` seconds.

        On Windows a BleakScanner running alongside active GATT connections can be
        starved and silently stop delivering advertisements (no error raised), so
        we periodically stop and restart it to self-heal rather than trusting it
        to run forever.
        """
        while True:
            try:
                self._scanner = BleakScanner(detection_callback=self._cb)
                await self._scanner.start()
                await asyncio.sleep(restart_period)
                await self._scanner.stop()
                self._scanner = None
                await asyncio.sleep(0.5)  # let the radio settle before restarting
            except asyncio.CancelledError:
                try:
                    if self._scanner:
                        await self._scanner.stop()
                except Exception:
                    pass
                raise
            except Exception as e:  # noqa: BLE001
                self.state = {"connected": False, "updated": time.time(),
                              "error": f"scanner: {str(e)[:60]}"}
                await asyncio.sleep(3.0)


async def read_once(address: str = DEFAULT_ADDR, key: str = DEFAULT_KEY,
                    listen: float = 8.0) -> dict:
    m = VictronMonitor(address, key)
    s = BleakScanner(detection_callback=m._cb)
    await s.start()
    await asyncio.sleep(listen)
    await s.stop()
    return m.snapshot()


if __name__ == "__main__":
    import json
    print(json.dumps(asyncio.run(read_once()), indent=2, default=str))
