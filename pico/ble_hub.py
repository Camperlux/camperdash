# BLE polling for the Pico W hub, on the raw-BLE Central (reliable notifications).
# One scan per cycle discovers who's present (and reads the Victron advert), then
# we connect to the BMS and Renogy in turn. The heater is only touched on demand.

import asyncio
from time import ticks_ms

import jbd
import renogy_dec as ren
import victron_dec as vic
import jp_heater as jph
import config as cfg
from ble_raw import Central

_central = None
_lock = asyncio.Lock()          # serialise all BLE use (poll vs command)
_addr_types = {}                # cache addr_type from the last scan


def _c():
    global _central
    if _central is None:
        _central = Central()
    return _central


def _addr_bytes(s):
    return bytes(int(x, 16) for x in s.split(":"))


async def _connect_retry(c, addr, tries=3, timeout=10000, addr_type=None):
    """Keep trying to connect (the heater's BLE link drops often)."""
    at = addr_type if addr_type is not None else _addr_types.get(addr)
    for _ in range(tries):
        if at is None:
            info = (await c.scan(3500)).get(addr)
            if info:
                at = info[0]
                _addr_types[addr] = at
        if at is not None and await c.connect(at, _addr_bytes(addr), timeout=timeout):
            return True
        await asyncio.sleep_ms(400)
    return False


async def _jbd_req(c, wh, cmd, want):
    for _ in range(3):
        c.clear_buf()
        if not c.write_nr(wh, cmd):
            return None
        for _ in range(20):                     # up to ~2 s
            await asyncio.sleep_ms(100)
            f = jbd.find_frame(c.notify_buf, want)
            if f:
                return f
        await asyncio.sleep_ms(150)             # resend
    return None


async def poll_bms(found):
    # Adverts are easy to miss in one scan window - if we know the address type
    # from an earlier scan, connect anyway rather than calling it disconnected.
    info = found.get(cfg.BMS_ADDR)
    at = info[0] if info else _addr_types.get(cfg.BMS_ADDR)
    if at is None:
        return {"connected": False}
    c = _c()
    if not await _connect_retry(c, cfg.BMS_ADDR, tries=2, addr_type=at):
        return {"connected": False}
    try:
        await c.discover()
        nh, wh = c.handle(0xFF01), c.handle(0xFF02)
        if nh is None or wh is None:
            return {"connected": False}
        await c.enable_notify(nh)
        await asyncio.sleep_ms(200)
        basic = await _jbd_req(c, wh, jbd.CMD_BASIC, 0x03)
        cells = await _jbd_req(c, wh, jbd.CMD_CELLS, 0x04)
        if basic or cells:
            return jbd.decode(jbd.payload_of(basic) if basic else None,
                              jbd.payload_of(cells) if cells else None)
    finally:
        await c.disconnect()
    return {"connected": False}


async def poll_renogy(found):
    info = found.get(cfg.RENOGY_ADDR)
    at = info[0] if info else _addr_types.get(cfg.RENOGY_ADDR)
    if at is None:
        return {"connected": False}
    c = _c()
    if not await _connect_retry(c, cfg.RENOGY_ADDR, tries=2, addr_type=at):
        return {"connected": False}
    try:
        await c.discover()
        nh, wh = c.handle(0xFFF1), c.handle(0xFFD1)
        if nh is None or wh is None:
            return {"connected": False}
        await c.enable_notify(nh)
        await asyncio.sleep_ms(200)
        c.clear_buf()
        if not c.write_nr(wh, ren.read_cmd(ren.LIVE_REG, ren.LIVE_COUNT)):
            return {"connected": False}
        for _ in range(30):                     # up to ~3 s
            await asyncio.sleep_ms(100)
            buf = c.notify_buf
            if len(buf) >= 3 and buf[1] == 0x03:
                need = 3 + buf[2] + 2
                if len(buf) >= need:
                    regs = ren.regs_of(bytes(buf[:need]))
                    # 35, not 34: the alarm words are r[33] and r[34], so a
                    # short frame would index past the end.
                    if len(regs) >= ren.LIVE_COUNT:
                        return ren.decode(regs, getattr(cfg, "RENOGY_DEBUG_REGS", False))
                    break
    finally:
        await c.disconnect()
    return {"connected": False}


async def _htr_req(c, wh, cmd):
    for _ in range(4):
        c.clear_buf()
        if not c.write_nr(wh, jph.query(cmd)):
            return None
        for _ in range(18):
            await asyncio.sleep_ms(100)
            f = jph.find_frame(c.notify_buf, cmd)
            if f:
                return f
        await asyncio.sleep_ms(150)
    return None


def _heater_readback(hs, st, se):
    """Decoded readback, plus the raw status/sensor frames as hex.

    Several bytes in both frames are still unidentified - the fuel dosing pump
    rate would be a direct "is it burning" signal - so the raw frames ride along
    for diagnosis.  They are small and read-only.
    """
    out = {"raw": {}}
    if st:
        out["raw"]["status"] = bytes(st).hex()
    if se:
        out["raw"]["sensors"] = bytes(se).hex()
    if hs:
        d = jph.decode_handshake(hs)
        out["product"] = d["product"]
        out["firmware"] = d["firmware"]
    if st:
        out["status"] = jph.decode_status(st)
    if se:
        out["sensors"] = jph.decode_sensors(se)
    return out


async def poll_heater(found):
    info = found.get(cfg.HEATER_ADDR)
    if not info:
        return {"connected": False}
    c = _c()
    if not await _connect_retry(c, cfg.HEATER_ADDR, tries=2, addr_type=info[0]):
        return {"connected": False}
    try:
        await c.discover()
        nh, wh = c.handle(0xFFF1), c.handle(0xFFF2)
        if nh is None or wh is None:
            return {"connected": False}
        await c.enable_notify(nh)
        await asyncio.sleep_ms(200)
        hs = await _htr_req(c, wh, 3)
        st = await _htr_req(c, wh, 5)
        se = await _htr_req(c, wh, 6)
        if not (hs or st or se):
            return {"connected": False}
        rb = _heater_readback(hs, st, se)
        out = {"connected": True}
        for k in ("product", "firmware"):
            if k in rb:
                out[k] = rb[k]
        out.update(rb.get("status", {}))
        out.update(rb.get("sensors", {}))
        return out
    finally:
        await c.disconnect()


def _victron(found, state):
    info = found.get(cfg.VICTRON_ADDR)
    if not info:
        return
    raw = info[3].get(vic.MANUFACTURER_ID)
    if not raw:
        return
    d = vic.decode(raw, cfg.VICTRON_KEY)
    if d and d.get("connected"):
        state["victron"] = d
        state["victron_seen"] = ticks_ms()


async def _guarded(fn, *args):
    """Run one device poll; a failure marks that device offline instead of
    aborting the whole cycle and leaving stale data marked live."""
    try:
        return await fn(*args)
    except Exception as e:
        print("poll", fn.__name__, "error:", e)
        return {"connected": False}


async def poll_all(state):
    c = _c()
    async with _lock:
        found = await c.scan(cfg.VICTRON_SCAN_MS)
    for a in (cfg.BMS_ADDR, cfg.RENOGY_ADDR, cfg.VICTRON_ADDR, cfg.HEATER_ADDR):
        if a in found:
            _addr_types[a] = found[a][0]
    _victron(found, state)
    fresh = {}
    async with _lock:
        b = await _guarded(poll_bms, found)
    fresh["battery"] = b.get("connected")
    # A single failed cycle used to blank the panel outright.  Hold the last
    # good reading and let api_data age it out instead.
    if b.get("connected"):
        state["battery"] = b
        state["battery_seen"] = ticks_ms()
    elif not state["battery"].get("connected"):
        state["battery"] = b
    async with _lock:
        r = await _guarded(poll_renogy, found)
    fresh["renogy"] = r.get("connected")
    if r.get("connected"):
        state["renogy"] = r
        state["renogy_seen"] = ticks_ms()
    elif not state["renogy"].get("connected"):
        state["renogy"] = r
    if cfg.HEATER_POLL:
        async with _lock:
            h = await _guarded(poll_heater, found)
        if h.get("connected"):
            state["heater"] = h
            state["heater_seen"] = ticks_ms()
        # else keep the last good reading; main.py marks it stale if too old
    return fresh


async def set_mosfet(charge_on, discharge_on):
    """Switch the BMS charge/discharge MOSFETs and read the pack back to prove it.

    Never reports success on the write alone: the BMS is asked for its basic
    frame afterwards and the answer must match what was requested.  Turning the
    DISCHARGE MOSFET off cuts the pack's output, so anything powered from the
    leisure battery - this hub included - loses power immediately and cannot
    switch it back on.  main.py guards that; this function just does as it is
    told.
    """
    async with _lock:
        c = _c()
        info_type = _addr_types.get(cfg.BMS_ADDR)
        if info_type is None:
            found = await c.scan(3500)
            if cfg.BMS_ADDR not in found:
                return {"ok": False, "error": "couldn't find the battery over Bluetooth"}
            info_type = found[cfg.BMS_ADDR][0]
            _addr_types[cfg.BMS_ADDR] = info_type
        if not await _connect_retry(c, cfg.BMS_ADDR, tries=3, addr_type=info_type):
            return {"ok": False, "error": "couldn't connect to the battery"}
        try:
            # GATT discovery misses often enough to matter here - roughly one
            # attempt in six on this pack.  A failed poll is harmless because the
            # last reading is kept, but a failed switch is a button that did
            # nothing, so drop the link and ask again rather than giving up.
            nh = wh = None
            for attempt in range(2):
                await c.discover()
                nh, wh = c.handle(0xFF01), c.handle(0xFF02)
                if nh is not None and wh is not None:
                    break
                if attempt == 0:
                    print("bms: discovery incomplete, reconnecting")
                    await c.disconnect()
                    await asyncio.sleep_ms(500)
                    if not await _connect_retry(c, cfg.BMS_ADDR, tries=3,
                                                addr_type=info_type):
                        return {"ok": False,
                                "error": "lost the battery while reading its services"}
            if nh is None or wh is None:
                return {"ok": False,
                        "error": "the battery didn't list its services - try again"}
            await c.enable_notify(nh)
            await asyncio.sleep_ms(200)
            # Both MOSFETs live in one register, so the caller must supply both
            # bits.  Do NOT read the pack here first: measured on this unit, a
            # CMD_BASIC request between enabling notifications and the write
            # makes the BMS acknowledge the write and then not apply it - the
            # MOSFET command has to be the first traffic on the connection.
            # main.py guarantees freshness of the carried-over bit instead.
            if charge_on is None or discharge_on is None:
                return {"ok": False, "error": "both MOSFET states must be given"}
            c.clear_buf()
            if not c.write_nr(wh, jbd.mosfet_frame(charge_on, discharge_on)):
                return {"ok": False, "error": "write failed"}
            accepted = None
            for _ in range(15):
                await asyncio.sleep_ms(100)
                accepted = jbd.write_result(c.notify_buf)
                if accepted is not None:
                    break
            if accepted is False:
                return {"ok": False, "error": "the battery refused the change"}
            # The pack does not always report the new state on the first read -
            # it can still be showing the old FET byte a few hundred ms after
            # acknowledging the write. A single early read made a command that
            # had actually worked look like a failure, which invites the user to
            # send it again. Ask a few times before giving up.
            d = None
            for attempt in range(4):
                await asyncio.sleep_ms(400 if attempt == 0 else 600)
                basic = await _jbd_req(c, wh, jbd.CMD_BASIC, 0x03)
                if not basic:
                    continue
                d = jbd.decode(jbd.payload_of(basic), None)
                if (d.get("charge_fet") == bool(charge_on)
                        and d.get("discharge_fet") == bool(discharge_on)):
                    return {"ok": True, "acked": accepted, "state": d,
                            "settled_after": attempt + 1, "error": None}
            if d is None:
                return {"ok": False, "acked": accepted,
                        "error": "changed, but the battery didn't read back - check the app"}
            # Reached only when every read-back disagreed with what was asked.
            return {"ok": False, "acked": accepted, "state": d,
                    "error": "the battery did not apply the change"}
        finally:
            await c.disconnect()


async def scan_devices(ms=6000):
    """Scan for nearby BLE devices, for the Settings page's pairing list.

    Takes the same lock as the pollers so a scan can't run on top of a
    connection, and guesses what each device is from its advertised name or
    manufacturer data - the installer still confirms each assignment.
    """
    c = _c()
    async with _lock:
        found = await c.scan(ms)
    for a, info in found.items():
        _addr_types[a] = info[0]
    out = []
    for addr, (at, name, rssi, mfr) in found.items():
        name = name or ""
        low = name.lower()
        if vic.MANUFACTURER_ID in mfr:
            guess = "victron"
        elif low.startswith("bt-th") or low.startswith("bt-tr"):
            guess = "renogy"
        elif ("jbd" in low or "xiaoxiang" in low
              # JBD-based packs advertise as e.g. SP04S060L4S300A or DP04S...
              or (len(low) > 4 and low[0] in "sd" and low[1] == "p"
                  and low[2:4].isdigit() and low[4] == "s")):
            guess = "bms"
        elif "intrepid" in low or "solgp" in low:
            guess = "heater"
        else:
            guess = ""
        out.append({"addr": addr, "name": name, "rssi": rssi,
                    "addr_type": at, "guess": guess})
    out.sort(key=lambda d: -d["rssi"])
    return out


async def command_heater(action, temp=20, water=2, level=1, energy=None, budget=6,
                         mains_present=None):
    """Send a control command (or 'read') and read the heater back to verify.

    mains_present says whether a 230 V hook-up is actually live, judged from the
    mains charger rather than the heater (whose own AC flag is wrong on this
    unit).  None or False both refuse the electric element.

    budget caps the connect retries.  Every retry holds the BLE lock and so
    stalls the battery and DC-DC polling, which is fine when you pressed the
    button and are waiting, but not for the page's own background refresh -
    that passes a small budget and gives up quickly instead.

    energy is the source: 0 diesel, 1/2 diesel + electric stage 1/2, 3/4 electric
    only.  Pass None to keep whatever the heater is already set to, so a command
    never silently forces diesel-only.  Anything that uses the electric element
    is refused unless the heater reports AC 220 V present on this same
    connection - the element must never be asked for without mains.
    """
    if action not in ("read", "off", "air", "water", "combi", "vent"):
        return {"ok": False, "error": "unknown action"}
    async with _lock:
        c = _c()
        if not await _connect_retry(c, cfg.HEATER_ADDR, tries=budget):
            return {"ok": False, "error": "couldn't connect after retries - close the phone app so the heater is free"}
        try:
            nh = wh = None
            for attempt in range(2):
                await c.discover()
                nh, wh = c.handle(0xFFF1), c.handle(0xFFF2)
                if nh is not None and wh is not None:
                    break
                if attempt == 0:      # GATT discovery can time out - drop and ask once more
                    print("heater: discovery incomplete, reconnecting")
                    await c.disconnect()
                    await asyncio.sleep_ms(500)
                    if not await _connect_retry(c, cfg.HEATER_ADDR, tries=max(1, budget - 2)):
                        return {"ok": False, "error": "lost the heater while reading its services"}
            if nh is None or wh is None:
                return {"ok": False, "error": "the heater didn't list its services - try again in a moment"}
            await c.enable_notify(nh)
            await asyncio.sleep_ms(200)
            out = {"ok": True, "action": action}
            if action != "read":
                want = energy
                cur = 0
                pre = await _htr_req(c, wh, 5)
                if pre:
                    cur = pre[6]
                energy = cur if want is None else want
                out["energy_carried"] = cur if pre else None
                # The element must never be asked for without mains, whether it
                # was requested or merely carried over from the heater's last
                # setting.  The heater's own AC flag is not usable for this - it
                # reads "present" with nothing plugged in - so the caller passes
                # what the mains charger reports instead.  Unknown counts as
                # absent: this fails closed.
                if energy in jph.ENERGY_NEEDS_AC:
                    if mains_present is not True:
                        if want is None:                  # carried over, not asked for
                            energy = 0
                            out["energy_note"] = "no 230 V hook-up - switched to diesel"
                        else:
                            return {"ok": False, "no_ac": True,
                                    "error": "no 230 V hook-up - the electric element needs mains power"}
                out["energy_sent"] = energy
                try:
                    frame = jph.build_command(action, temp, water, level, energy)
                except (ValueError, TypeError) as e:
                    return {"ok": False, "error": "bad parameters: %s" % e}
                if frame is None:
                    return {"ok": False, "error": "unknown action"}
                c.clear_buf()
                if not c.write_nr(wh, frame):
                    return {"ok": False, "error": "write failed"}
                out["sent"] = frame.hex()
                await asyncio.sleep_ms(800)
            hs = await _htr_req(c, wh, 3)     # handshake -> product/firmware
            st = await _htr_req(c, wh, 5)     # status (read-back)
            se = await _htr_req(c, wh, 6)     # sensors
            out.update(_heater_readback(hs, st, se))
            return out
        finally:
            await c.disconnect()
