# One-shot read-only probe of the diesel heater from the Pico.
import asyncio
from ble_raw import Central
import jp_heater as h

ADDR = "AA:BB:CC:DD:EE:FF"


def _ab(s):
    return bytes(int(x, 16) for x in s.split(":"))


async def _req(c, wh, cmd):
    for _ in range(4):
        c.clear_buf()
        if not c.write_nr(wh, h.query(cmd)):
            return None
        for _ in range(18):                 # ~1.8 s
            await asyncio.sleep_ms(100)
            f = h.find_frame(c.notify_buf, cmd)
            if f:
                return f
        await asyncio.sleep_ms(150)
    return None


async def main():
    c = Central()
    found = await c.scan(6000)
    info = found.get(ADDR)
    print("advert:", (info[0], info[1], info[2]) if info else None)
    if not info:
        print("heater not found"); return
    if not await c.connect(info[0], _ab(ADDR)):
        print("CONNECT FAILED"); return
    try:
        chars = await c.discover()
        nh, wh = c.handle(0xFFF1), c.handle(0xFFF2)
        print("notify=%s write=%s chars=%d" % (nh, wh, len(chars)))
        if nh is None or wh is None:
            print("chars missing:", list(chars.keys())); return
        c.enable_notify(nh)
        await asyncio.sleep_ms(300)
        for cmd in (3, 5, 6):
            f = await _req(c, wh, cmd)
            print("cmd %d ->" % cmd, f.hex() if f else "no reply")
            if f and cmd == 3:
                print("  handshake:", h.decode_handshake(f))
            elif f and cmd == 5:
                print("  status:", h.decode_status(f))
            elif f and cmd == 6:
                print("  sensors:", h.decode_sensors(f))
    finally:
        await c.disconnect()
        print("disconnected")


asyncio.run(main())
