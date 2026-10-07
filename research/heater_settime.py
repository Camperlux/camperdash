"""Set the JP diesel heater's clock from this PC's local time (cmd 0x0B).

Fire-and-forget: the first write after connecting reliably reaches the heater
on Windows even though later requests stall. Frame layout (from the app):
  01 0B 0C  <yr hi=20> <yr lo=26> <month> <day> <hour> <min> <sec> 00 00 00 00 00 <crc>
"""
import asyncio, time
from datetime import datetime
from uuid import UUID
from winrt.windows.devices.bluetooth import BluetoothCacheMode, BluetoothConnectionStatus, BluetoothLEDevice
from winrt.windows.devices.bluetooth.genericattributeprofile import GattCommunicationStatus, GattSession, GattWriteOption
from winrt.windows.storage.streams import DataWriter
from probe_heater import crc16, frame, ADDR, SVC, WRITE

def to_buffer(b):
    w = DataWriter(); w.write_bytes(b); return w.detach_buffer()

async def main():
    dev = await BluetoothLEDevice.from_bluetooth_address_async(ADDR)
    session = await GattSession.from_device_id_async(dev.bluetooth_device_id)
    session.maintain_connection = True
    wchar = None
    for attempt in range(1, 9):
        try:
            r = await dev.get_gatt_services_for_uuid_with_cache_mode_async(SVC, BluetoothCacheMode.UNCACHED)
            if r.status == GattCommunicationStatus.SUCCESS and r.services.size:
                cr = await r.services.get_at(0).get_characteristics_with_cache_mode_async(BluetoothCacheMode.UNCACHED)
                if cr.status == GattCommunicationStatus.SUCCESS:
                    wchar = next((c for c in cr.characteristics if str(c.uuid) == str(WRITE)), None)
                    if wchar: break
            print(f"  try {attempt}: not ready (status {int(r.status)})")
        except OSError as e:
            print(f"  try {attempt}: {str(e)[:50]}")
        await asyncio.sleep(3)
    if not wchar:
        print("Could not reach the heater's write characteristic."); return
    now = datetime.now()
    payload = bytes([now.year // 100, now.year % 100, now.month, now.day, now.hour, now.minute, now.second])
    f = frame(0x0B, payload)
    st = await wchar.write_value_with_option_async(to_buffer(f), GattWriteOption.WRITE_WITHOUT_RESPONSE)
    print(f"sent time-sync {now:%Y-%m-%d %H:%M:%S}: {f.hex(' ')}  ->", "write OK" if st == GattCommunicationStatus.SUCCESS else f"status {int(st)}")
    await asyncio.sleep(1.0)
    dev.close()

asyncio.run(main())
