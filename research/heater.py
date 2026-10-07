"""JP diesel heater (CR12 water+air combi) — fire-and-forget commands over BLE
on Windows / WinRT.

Windows' BLE stack cannot hold a working session with this module (descriptor
discovery and later requests are never answered), but the first write after a
fresh connection reliably reaches the heater. So every command is:
connect -> locate 0xfff2 -> one write -> disconnect. There is NO read-back on
this link; the Pico hub (pico/) is the path that verifies commands.

Frame (from the app's BleDataSendService.setWorkParameter / CR12Activity.Open):
  01 04 0C <workMode> <waterTemp> <airTemp> <energy> <windSpeed> <tempOffset> 00x6 <crc16>
  workMode 3=water+air 4=air 5=water 6=vent 10=off
  waterTemp preset 1=40C 2=60C 3=boost ; airTemp 5-35 C ; energy 0=diesel..4
  cmd 0x0B sync time : yr//100, yr%100, month, day, hour, min, sec
"""
from __future__ import annotations
import asyncio
from datetime import datetime
from uuid import UUID

from winrt.windows.devices.bluetooth import BluetoothCacheMode, BluetoothLEDevice
from winrt.windows.devices.bluetooth.genericattributeprofile import (
    GattCommunicationStatus, GattSession, GattWriteOption)
from winrt.windows.storage.streams import DataWriter

ADDR = 0x5C5310BC585E
SVC = UUID("0000fff0-0000-1000-8000-00805f9b34fb")
WRITE = UUID("0000fff2-0000-1000-8000-00805f9b34fb")

ACTIONS = ("off", "air", "water", "combi", "vent", "time")


def crc16(data: bytes) -> int:
    crc = 0xFFFF
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc


def frame(cmd: int, payload: bytes = b"") -> bytes:
    body = bytes([0x01, cmd, 0x0C]) + payload.ljust(12, b"\x00")
    c = crc16(body)
    return body + bytes([c & 0xFF, c >> 8])


def build(action: str, temp=20, water=2, level=1, energy=0) -> bytes:
    """Build the CR12 frame for a named action; raises ValueError on bad input.
    `energy` should be the heater's current source setting when known — this
    link can't read it back, so the caller must supply it (default diesel)."""
    if action == "time":
        n = datetime.now()
        return frame(0x0B, bytes([n.year // 100, n.year % 100, n.month, n.day,
                                  n.hour, n.minute, n.second]))
    if action not in ACTIONS:
        raise ValueError(f"unknown action {action!r}")
    if action == "off":
        return frame(0x04, bytes([10, 0, 0, 0, 0, 0]))
    # validate only what the chosen mode actually uses
    lvl = int(1 if level is None else level)
    if not 1 <= lvl <= 4:
        raise ValueError("fan level must be 1-4")
    t = w = 0
    if action in ("air", "combi"):
        t = int(20 if temp is None else temp)
        if not 5 <= t <= 35:
            raise ValueError("air temperature must be 5-35 °C")
    if action in ("water", "combi"):
        w = int(2 if water is None else water)
        if w not in (1, 2, 3):
            raise ValueError("water preset must be 1 (40°C), 2 (60°C) or 3 (boost)")
    en = int(0 if energy is None else energy)
    if en not in (0, 1, 2, 3, 4):
        raise ValueError("energy must be 0-4")
    ws = lvl - 1
    if action == "air":
        return frame(0x04, bytes([4, 0, t, en, ws, 5]))
    if action == "water":
        return frame(0x04, bytes([5, w, 0, en, ws, 5]))
    if action == "combi":
        return frame(0x04, bytes([3, w, t, en, ws, 5]))
    ws_vent = {1: 1, 2: 4, 3: 7, 4: 10}[lvl]
    return frame(0x04, bytes([6, 0, 0, 0, ws_vent, 5]))


def _buf(b: bytes):
    w = DataWriter()
    w.write_bytes(b)
    return w.detach_buffer()


async def send_frame(f: bytes, attempts: int = 6) -> dict:
    """Connect, write one frame without response, disconnect."""
    dev = await BluetoothLEDevice.from_bluetooth_address_async(ADDR)
    if dev is None:
        return {"ok": False, "error": "heater not found"}
    session = None
    try:
        session = await GattSession.from_device_id_async(dev.bluetooth_device_id)
        session.maintain_connection = True
        wchar, last = None, "unreachable"
        for _ in range(attempts):
            try:
                r = await dev.get_gatt_services_for_uuid_with_cache_mode_async(SVC, BluetoothCacheMode.UNCACHED)
                if r.status == GattCommunicationStatus.SUCCESS and r.services.size:
                    cr = await r.services.get_at(0).get_characteristics_with_cache_mode_async(BluetoothCacheMode.UNCACHED)
                    if cr.status == GattCommunicationStatus.SUCCESS:
                        wchar = next((c for c in cr.characteristics if str(c.uuid) == str(WRITE)), None)
                        if wchar:
                            break
                last = f"gatt status {int(r.status)}"
            except OSError as e:
                last = str(e)[:60]
            await asyncio.sleep(2.5)
        if not wchar:
            return {"ok": False, "error": f"could not reach heater ({last}) — is the phone app connected?"}
        st = await wchar.write_value_with_option_async(_buf(f), GattWriteOption.WRITE_WITHOUT_RESPONSE)
        if st != GattCommunicationStatus.SUCCESS:
            return {"ok": False, "error": f"write failed (status {int(st)})"}
        await asyncio.sleep(0.6)  # let the packet leave before we drop the link
        return {"ok": True, "frame": f.hex(" ")}
    except OSError as e:
        return {"ok": False, "error": str(e)[:80]}
    finally:
        try:
            if session:
                session.close()
            dev.close()
        except OSError:
            pass


class HeaterCommander:
    """Serialises fire-and-forget commands; keeps the last result for the UI."""

    def __init__(self):
        self.lock = asyncio.Lock()
        self.last: dict = {}

    async def run(self, action: str, temp=None, water=None, level=None, energy=None) -> dict:
        try:
            f = build(action, temp, water, level, energy)
        except (ValueError, TypeError) as e:
            return {"ok": False, "error": str(e), "action": action}
        async with self.lock:
            res = await send_frame(f)
        res.update({"action": action, "at": datetime.now().strftime("%H:%M:%S")})
        self.last = res
        return res
