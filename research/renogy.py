"""Renogy DC-DC charger interface via the BT-2 module (Modbus RTU over BLE).

Device: BT-TH-1A2B3C4D (AA:BB:CC:DD:EE:FF) -> RBC50D1S-G6, Modbus id 0xFF
  write  char 0xffd1, notify char 0xfff1
Live data is holding registers 0x0100..0x0122 (function 0x03).
"""
from __future__ import annotations
import asyncio
import struct
import time

from bleak import BleakClient

DEFAULT_ADDR = "AA:BB:CC:DD:EE:FF"
DEVICE_ID = 0xFF
WRITE = "0000ffd1-0000-1000-8000-00805f9b34fb"
NOTIFY = "0000fff1-0000-1000-8000-00805f9b34fb"

LIVE_REG, LIVE_COUNT = 0x0100, 35

CHARGE_STATES = {
    0: "Not charging", 1: "Activated", 2: "MPPT", 3: "Equalising",
    4: "Boost", 5: "Float", 6: "Current limiting",
}
# Bit index -> meaning for the 32-bit fault word (0x0121:0x0122)
FAULT_BITS = {
    16: "Charge MOSFET short", 17: "Anti-reverse MOSFET short",
    18: "Solar reversed", 19: "Solar over-voltage", 20: "Solar counter-current",
    21: "PV input over-voltage", 22: "PV input short", 23: "PV over-power",
    24: "Ambient over-temp", 25: "Controller over-temp", 26: "Load over-power",
    27: "Load short circuit", 28: "Battery under-voltage",
    29: "Battery over-voltage", 30: "Battery over-discharge",
}


def crc16(data: bytes) -> int:
    crc = 0xFFFF
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc


def read_cmd(reg: int, count: int, dev_id: int = DEVICE_ID) -> bytes:
    body = struct.pack(">BBHH", dev_id, 0x03, reg, count)
    return body + struct.pack("<H", crc16(body))


def _temp(b: int) -> int:
    return -(b & 0x7F) if b & 0x80 else b


def decode_live(r: list[int]) -> dict:
    """Decode the 0x0100.. register block into named fields."""
    faults_word = (r[33] << 16) | (r[34] if len(r) > 34 else 0)
    faults = [n for b, n in FAULT_BITS.items() if faults_word & (1 << b)]
    alt_a, sol_a = r[5] / 100.0, r[8] / 100.0
    source = ("Alternator + Solar" if alt_a > 0.05 and sol_a > 0.05
              else "Alternator" if alt_a > 0.05
              else "Solar" if sol_a > 0.05 else "None")
    return {
        "soc": r[0],
        "battery_v": r[1] / 10.0,
        "charge_a": r[2] / 100.0,
        "charge_w": round(r[1] / 10.0 * r[2] / 100.0, 1),
        "ctrl_temp_c": _temp(r[3] >> 8),
        "batt_temp_c": _temp(r[3] & 0xFF),
        # r[6]/r[9] are NOT per-source watts (r[9] is the charger's TOTAL output
        # power, which overstates solar and exceeds the panel rating). Compute
        # each source's power from its own volts x amps instead.
        "alt_v": r[4] / 10.0, "alt_a": alt_a, "alt_w": round(r[4] / 10.0 * alt_a, 1),
        "solar_v": r[7] / 10.0, "solar_a": sol_a, "solar_w": round(r[7] / 10.0 * sol_a, 1),
        "charge_out_w": r[9],   # controller's total charge-output power
        "today_min_v": r[11] / 10.0, "today_max_v": r[12] / 10.0,
        "today_max_chg_a": r[13] / 100.0, "today_max_chg_w": r[15],
        "today_chg_ah": r[17], "today_chg_wh": r[19],
        "run_days": r[21], "full_charges": r[23],
        "total_chg_ah": (r[24] << 16) | r[25],
        "total_gen_kwh": round(((r[28] << 16) | r[29]) / 1000.0, 1),
        "state_code": r[32] & 0xFF,
        "state": CHARGE_STATES.get(r[32] & 0xFF, f"Unknown ({r[32] & 0xFF})"),
        "source": source,
        "faults": faults,
    }


class RenogyPoller:
    """Maintains a BLE connection and continuously refreshes `self.state`."""

    def __init__(self, address: str = DEFAULT_ADDR):
        self.address = address
        self.state: dict = {"connected": False, "updated": None}
        self.on_update = None
        self._buf = bytearray()
        self._frame: bytes | None = None

    def _on_notify(self, _c, data: bytearray):
        self._buf.extend(data)
        if len(self._buf) >= 3 and self._buf[1] == 0x03:
            need = 3 + self._buf[2] + 2
            if len(self._buf) >= need:
                self._frame = bytes(self._buf[:need])
                self._buf.clear()
        elif len(self._buf) >= 5 and self._buf[1] & 0x80:
            self._frame = bytes(self._buf[:5])
            self._buf.clear()

    async def _query(self, client, reg, count, wait=2.5):
        self._frame = None
        self._buf.clear()
        await client.write_gatt_char(WRITE, read_cmd(reg, count), response=False)
        deadline = time.monotonic() + wait
        while time.monotonic() < deadline:
            if self._frame:
                f = self._frame
                if f[1] == 0x03 and struct.unpack("<H", f[-2:])[0] == crc16(f[:-2]):
                    return [struct.unpack_from(">H", f, 3 + 2 * i)[0]
                            for i in range(f[2] // 2)]
                return None
            await asyncio.sleep(0.05)
        return None

    async def _read_cycle(self, client):
        regs = await self._query(client, LIVE_REG, LIVE_COUNT)
        if regs and len(regs) >= 34:
            new = {"connected": True, "updated": time.time()}
            new.update(decode_live(regs))
            self.state = new
            if self.on_update:
                self.on_update(new)

    async def run(self, period: float = 3.0):
        while True:
            try:
                async with BleakClient(self.address, timeout=20.0) as client:
                    await client.start_notify(NOTIFY, self._on_notify)
                    while True:
                        await self._read_cycle(client)
                        await asyncio.sleep(period)
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001 - keep the loop alive
                self.state = {"connected": False, "updated": time.time(),
                              "error": str(e)}
                await asyncio.sleep(3.0)


async def read_once(address: str = DEFAULT_ADDR) -> dict:
    p = RenogyPoller(address)
    async with BleakClient(address, timeout=20.0) as client:
        await client.start_notify(NOTIFY, p._on_notify)
        await p._read_cycle(client)
        await client.stop_notify(NOTIFY)
    return p.state


if __name__ == "__main__":
    import json
    print(json.dumps(asyncio.run(read_once()), indent=2))
