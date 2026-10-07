"""Fogstar battery BMS interface (JBD / Xiaoxiang BLE protocol).

Device: SP04S060L4S300A  (AA:BB:CC:DD:EE:FF)
  service 0xff00, notify 0xff01, write 0xff02

Frame format:  DD <cmd> <status> <len> <payload...> <chk_hi> <chk_lo> 77
Commands:
  read basic info : DD A5 03 00 FF FD 77
  read cell volts : DD A5 04 00 FF FC 77
"""
from __future__ import annotations
import asyncio
import struct
import time

from bleak import BleakClient, BleakScanner

DEFAULT_ADDR = "AA:BB:CC:DD:EE:FF"

NOTIFY = "0000ff01-0000-1000-8000-00805f9b34fb"
WRITE = "0000ff02-0000-1000-8000-00805f9b34fb"

CMD_BASIC = bytes.fromhex("DDA50300FFFD77")
CMD_CELLS = bytes.fromhex("DDA50400FFFC77")

# JBD protection-status bit meanings (register 0x16..0x17)
PROTECTION_BITS = [
    "Cell overvoltage", "Cell undervoltage", "Pack overvoltage",
    "Pack undervoltage", "Charge over-temp", "Charge under-temp",
    "Discharge over-temp", "Discharge under-temp", "Charge overcurrent",
    "Discharge overcurrent", "Short circuit", "IC error", "MOSFET locked",
]


def _u16(b, i):
    return (b[i] << 8) | b[i + 1]


def _s16(b, i):
    return struct.unpack_from(">h", b, i)[0]


def decode_basic(payload: bytes) -> dict:
    """Decode a 0x03 'basic info' payload."""
    prot = _u16(payload, 16)
    faults = [name for n, name in enumerate(PROTECTION_BITS) if prot & (1 << n)]
    ntc = payload[22]
    temps = [
        round(_u16(payload, 23 + 2 * i) / 10.0 - 273.15, 1) for i in range(ntc)
    ]
    prod = _u16(payload, 10)
    fet = payload[20]
    return {
        "voltage": round(_u16(payload, 0) / 100.0, 2),
        "current": round(_s16(payload, 2) / 100.0, 2),
        "residual_ah": round(_u16(payload, 4) / 100.0, 2),
        "nominal_ah": round(_u16(payload, 6) / 100.0, 2),
        "cycles": _u16(payload, 8),
        "prod_date": f"{2000 + (prod >> 9)}-{(prod >> 5) & 0x0f:02d}-{prod & 0x1f:02d}",
        "soc": payload[19],
        "charge_fet": bool(fet & 0x01),
        "discharge_fet": bool(fet & 0x02),
        "cell_count": payload[21],
        "temps_c": temps,
        "protection_raw": prot,
        "faults": faults,
    }


def decode_cells(payload: bytes) -> dict:
    n = len(payload) // 2
    cells = [_u16(payload, 2 * i) for i in range(n)]  # mV
    if cells:
        return {
            "cells_mv": cells,
            "cell_min_mv": min(cells),
            "cell_max_mv": max(cells),
            "cell_delta_mv": max(cells) - min(cells),
        }
    return {"cells_mv": []}


def parse_frames(buf: bytearray) -> list[bytes]:
    """Pull complete DD...77 frames out of a rolling buffer (mutates buf)."""
    out = []
    while len(buf) >= 7 and buf[0] == 0xDD:
        plen = buf[3]
        total = 4 + plen + 3
        if len(buf) < total:
            break
        out.append(bytes(buf[:total]))
        del buf[:total]
    if buf and buf[0] != 0xDD:
        buf.clear()
    return out


async def read_once(address: str = DEFAULT_ADDR, timeout: float = 20.0) -> dict:
    """One-shot connect + read of the full battery state."""
    poller = BMSPoller(address)
    async with BleakClient(address, timeout=timeout) as client:
        await client.start_notify(NOTIFY, poller._on_notify)
        await poller._read_cycle(client)
        await client.stop_notify(NOTIFY)
    return poller.state


class BMSPoller:
    """Maintains a BLE connection and continuously refreshes `self.state`."""

    def __init__(self, address: str = DEFAULT_ADDR):
        self.address = address
        self.state: dict = {"connected": False, "updated": None}
        self.on_update = None  # optional callback(state) after each read
        self._buf = bytearray()
        self._frames: list[bytes] = []

    def _on_notify(self, _char, data: bytearray):
        self._buf.extend(data)
        self._frames.extend(parse_frames(self._buf))

    async def _collect(self, client, cmd, want_cmd, wait=1.5):
        self._frames.clear()
        await client.write_gatt_char(WRITE, cmd, response=False)
        deadline = time.monotonic() + wait
        while time.monotonic() < deadline:
            for f in self._frames:
                if f[1] == want_cmd and f[2] == 0x00:
                    return f
            await asyncio.sleep(0.05)
        return None

    async def _read_cycle(self, client):
        basic = await self._collect(client, CMD_BASIC, 0x03)
        cells = await self._collect(client, CMD_CELLS, 0x04)
        new = {"connected": True, "updated": time.time()}
        if basic:
            new.update(decode_basic(basic[4:4 + basic[3]]))
        if cells:
            new.update(decode_cells(cells[4:4 + cells[3]]))
        if basic and "voltage" in new and new.get("current") is not None:
            new["power_w"] = round(new["voltage"] * new["current"], 1)
        self.state = new
        if self.on_update:
            self.on_update(new)

    async def run(self, period: float = 3.0):
        """Reconnect-forever poll loop. Runs until cancelled."""
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
                self.state = {
                    "connected": False,
                    "updated": time.time(),
                    "error": str(e),
                }
                await asyncio.sleep(3.0)


if __name__ == "__main__":
    import json
    print(json.dumps(asyncio.run(read_once()), indent=2))
