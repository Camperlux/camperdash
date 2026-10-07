"""Connect to the Fogstar battery BMS (JBD/Xiaoxiang protocol) and read data.

Device: SP04S060L4S300A  (AA:BB:CC:DD:EE:FF), service 0xff00.
JBD BLE protocol:
  - notify char 0xff01, write char 0xff02
  - read basic info : DD A5 03 00 FF FD 77
  - read cell volts  : DD A5 04 00 FF FC 77
"""
import asyncio
from bleak import BleakClient

ADDR = "AA:BB:CC:DD:EE:FF"

CMD_BASIC = bytes.fromhex("DDA50300FFFD77")
CMD_CELLS = bytes.fromhex("DDA50400FFFC77")

NOTIFY = "0000ff01-0000-1000-8000-00805f9b34fb"
WRITE  = "0000ff02-0000-1000-8000-00805f9b34fb"

buf = bytearray()
frames = []


def handle(_, data: bytearray):
    buf.extend(data)
    # JBD frame: DD <cmd> <status> <len> <payload...> <chk hi> <chk lo> 77
    while len(buf) >= 7 and buf[0] == 0xDD:
        plen = buf[3]
        total = 4 + plen + 3
        if len(buf) < total:
            break
        frame = bytes(buf[:total])
        del buf[:total]
        frames.append(frame)
        print(f"  <- frame: {frame.hex()}")
    if buf and buf[0] != 0xDD:
        buf.clear()


async def main():
    print(f"Connecting to {ADDR} ...")
    async with BleakClient(ADDR, timeout=20.0) as c:
        print(f"Connected: {c.is_connected}\n")
        print("Services / characteristics:")
        for s in c.services:
            print(f"  service {s.uuid}")
            for ch in s.characteristics:
                print(f"    char {ch.uuid}  props={ch.properties}")
        print()

        await c.start_notify(NOTIFY, handle)

        print("-> read basic info")
        await c.write_gatt_char(WRITE, CMD_BASIC, response=False)
        await asyncio.sleep(1.5)

        print("-> read cell voltages")
        await c.write_gatt_char(WRITE, CMD_CELLS, response=False)
        await asyncio.sleep(1.5)

        await c.stop_notify(NOTIFY)
    print(f"\nCaptured {len(frames)} frame(s).")


if __name__ == "__main__":
    asyncio.run(main())
