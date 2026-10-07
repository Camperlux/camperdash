"""Probe the Renogy BT-2 module on the DC-DC charger (Modbus RTU over BLE).

Device: BT-TH-1A2B3C4D  (AA:BB:CC:DD:EE:FF)
  write  char 0xffd1 (service 0xffd0)
  notify char 0xfff1 (service 0xfff0)
Frames: <dev_id> 03 <reg_hi> <reg_lo> <cnt_hi> <cnt_lo> <crc_lo> <crc_hi>
Reply : <dev_id> 03 <nbytes> <data...> <crc_lo> <crc_hi>
"""
import asyncio
import struct

from bleak import BleakClient

ADDR = "AA:BB:CC:DD:EE:FF"
WRITE = "0000ffd1-0000-1000-8000-00805f9b34fb"
NOTIFY = "0000fff1-0000-1000-8000-00805f9b34fb"

DEVICE_IDS = [0xFF, 0x01, 0x60, 0x10, 0x30]


def crc16(data: bytes) -> int:
    crc = 0xFFFF
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc


def read_cmd(dev_id, reg, count):
    body = struct.pack(">BBHH", dev_id, 0x03, reg, count)
    return body + struct.pack("<H", crc16(body))


class Rx:
    def __init__(self):
        self.buf = bytearray()
        self.frame = None

    def on_notify(self, _c, data):
        self.buf.extend(data)
        if len(self.buf) >= 3 and self.buf[1] == 0x03:
            need = 3 + self.buf[2] + 2
            if len(self.buf) >= need:
                self.frame = bytes(self.buf[:need])
                self.buf.clear()
        elif len(self.buf) >= 5 and self.buf[1] & 0x80:  # modbus exception
            self.frame = bytes(self.buf[:5])
            self.buf.clear()


async def query(client, rx, dev_id, reg, count, wait=2.0):
    rx.frame = None
    rx.buf.clear()
    await client.write_gatt_char(WRITE, read_cmd(dev_id, reg, count), response=False)
    for _ in range(int(wait / 0.05)):
        if rx.frame:
            return rx.frame
        await asyncio.sleep(0.05)
    return None


def regs(frame):
    n = frame[2]
    return [struct.unpack_from(">H", frame, 3 + 2 * i)[0] for i in range(n // 2)]


async def main():
    print(f"Connecting to {ADDR} ...")
    async with BleakClient(ADDR, timeout=20.0) as c:
        print("Connected.\nServices:")
        for s in c.services:
            print(f"  {s.uuid}")
            for ch in s.characteristics:
                print(f"    {ch.uuid}  {ch.properties}")
        rx = Rx()
        await c.start_notify(NOTIFY, rx.on_notify)

        dev_id = None
        for did in DEVICE_IDS:
            print(f"\n-> try device id 0x{did:02X}: read model (0x000C x8)")
            f = await query(c, rx, did, 0x000C, 8)
            if f:
                print(f"  <- {f.hex()}")
                if f[1] == 0x03:
                    print(f"  model: {f[3:3+f[2]].decode('ascii','replace').strip()!r}")
                    dev_id = f[0]
                    break
        if dev_id is None:
            print("No reply on any device id.")
            return
        print(f"\nUsing device id 0x{dev_id:02X} (charger replied as 0x{dev_id:02X})")

        print("\n-> serial / sw / hw (0x0014 x6)")
        f = await query(c, rx, dev_id, 0x0014, 6)
        if f: print("  regs:", [f"{r:04x}" for r in regs(f)])

        print("\n-> live block 0x0100 x34")
        f = await query(c, rx, dev_id, 0x0100, 34)
        if not f:
            print("  no reply"); return
        r = regs(f)
        for i, v in enumerate(r):
            print(f"  0x{0x100+i:04X}  {v:5d}  0x{v:04x}")

        print("\nTentative decode (Rover/DCC register map):")
        print(f"  SOC              {r[0]} %")
        print(f"  Battery V        {r[1]/10:.1f} V")
        print(f"  Charge current   {r[2]/100:.2f} A")
        ct, bt = r[3] >> 8, r[3] & 0xFF
        print(f"  Controller temp  {(-(ct&0x7f) if ct&0x80 else ct)} C   Battery temp {(-(bt&0x7f) if bt&0x80 else bt)} C")
        print(f"  Alternator       {r[4]/10:.1f} V  {r[5]/100:.2f} A  {r[6]} W")
        print(f"  Solar            {r[7]/10:.1f} V  {r[8]/100:.2f} A  {r[9]} W")
        print(f"  Today min/max V  {r[11]/10:.1f} / {r[12]/10:.1f}")
        print(f"  Today max chg A  {r[13]/100:.2f}   max chg W {r[15]}")
        print(f"  Today chg Ah/Wh  {r[17]} / {r[19]}")
        print(f"  Run days {r[21]}  full charges {r[23]}")
        print(f"  Charging state   {r[32] & 0xff}  (0 off,1 on,2 MPPT,3 eq,4 boost,5 float,6 limit)")
        print(f"  Fault bits       0x{r[33]:04x}")

        await c.stop_notify(NOTIFY)


if __name__ == "__main__":
    asyncio.run(main())
