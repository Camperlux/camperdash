"""BLE scan for camper van devices.

Lists all nearby Bluetooth Low Energy devices with name, address, RSSI
and any advertised service UUIDs / manufacturer data. Run for ~15s.
"""
import asyncio
from bleak import BleakScanner


async def main(duration=15.0):
    print(f"Scanning for BLE devices for {duration:.0f}s...\n")
    found = {}

    def cb(device, adv):
        found[device.address] = (device, adv)

    scanner = BleakScanner(detection_callback=cb)
    await scanner.start()
    await asyncio.sleep(duration)
    await scanner.stop()

    print(f"Found {len(found)} device(s):\n")
    # Sort by RSSI (strongest first)
    for addr, (dev, adv) in sorted(
        found.items(), key=lambda kv: kv[1][1].rssi or -999, reverse=True
    ):
        name = adv.local_name or dev.name or "(no name)"
        print(f"  {name!r:30}  {addr}  RSSI={adv.rssi}")
        if adv.service_uuids:
            print(f"        services: {adv.service_uuids}")
        if adv.manufacturer_data:
            for cid, data in adv.manufacturer_data.items():
                print(f"        mfr 0x{cid:04x}: {data.hex()}")


if __name__ == "__main__":
    asyncio.run(main())
