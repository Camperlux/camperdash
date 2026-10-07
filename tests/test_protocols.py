"""The device protocols: what the hub sends and how it reads what comes back.

Each of the hub's decoders (pico/) is a MicroPython port of a PC version in
research/ that was worked out against the real devices. Here the two are fed
the same messages and must agree, so a slip in a port - a byte off by one, a
sign lost - shows up without the van. The heater's commands, which light a
diesel burner, are checked byte for byte.

Run:  python tests/test_protocols.py
Needs the "cryptography" package for the Victron test (skipped without it).
"""
import importlib.util
import os
import random
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hubsim                                                    # noqa: E402

hubsim.hardware_stubs()
hubsim.load_config()
sys.path.insert(0, hubsim.PICO)
# the research scripts talk to Bluetooth through these; only their decoders run
for name in ("bleak", "winrt", "winrt.windows", "winrt.windows.devices",
             "winrt.windows.devices.bluetooth",
             "winrt.windows.devices.bluetooth.genericattributeprofile",
             "winrt.windows.storage", "winrt.windows.storage.streams",
             "victron_ble", "victron_ble.devices"):
    hubsim._stub(name)

import jbd                                                       # noqa: E402
import jp_heater                                                 # noqa: E402
import renogy_dec                                                # noqa: E402

RESEARCH = os.path.join(hubsim.REPO, "research")


def research(name):
    spec = importlib.util.spec_from_file_location("research_" + name, os.path.join(RESEARCH, name + ".py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


rng = random.Random(7)                 # the same "random" messages every run
passed = []


def ok(name):
    passed.append(name)
    print("ok  ", name)


# ---- CRC -------------------------------------------------------------------
# The standard Modbus CRC-16 check value: "123456789" gives 0x4B37.
assert jp_heater.crc16(b"123456789") == 0x4B37
assert renogy_dec.crc16(b"123456789") == 0x4B37
ok("CRC-16 (heater and DC-DC) matches the Modbus check value")

# ---- the heater: commands ------------------------------------------------------
rh = research("heater")
n = 0
for action in ("off", "air", "water", "combi", "vent"):
    for temp in (5, 12, 20, 35):
        for water in (1, 2, 3):
            for level in (1, 2, 3, 4):
                for energy in (0, 1, 2, 3, 4):
                    a = jp_heater.build_command(action, temp, water, level, energy)
                    b = rh.build(action, temp, water, level, energy)
                    assert a == b, (action, temp, water, level, energy, a.hex(), b.hex())
                    n += 1
ok("heater: all %d commands match the research version byte for byte" % n)

off = jp_heater.build_command("off")
assert off[:9] == bytes((0x01, 0x04, 0x0C, 10, 0, 0, 0, 0, 0)) and len(off) == 17
assert jp_heater.crc16(off[:15]) == off[15] | (off[16] << 8)
ok("heater: Off is mode 10 with a valid CRC")

# Out-of-range values never reach the heater: the hub clamps them (the research
# version refuses them instead).
for t, want in ((0, 5), (-20, 5), (99, 35)):
    assert jp_heater.build_command("air", temp=t)[5] == want
for lvl, want in ((0, 0), (9, 3)):                      # fan level 1-4 is sent as 0-3
    assert jp_heater.build_command("air", level=lvl)[7] == want
assert jp_heater.build_command("air", energy=7)[6] == 0     # unknown source: diesel
assert jp_heater.build_command("water", water=9)[4] == 2    # unknown preset: 60 C
assert jp_heater.build_command("bogus") is None
ok("heater: out-of-range temperature, fan, source and water are clamped, unknown actions refused")

vent = {lvl: jp_heater.build_command("vent", level=lvl)[7] for lvl in (1, 2, 3, 4)}
assert vent == {1: 1, 2: 4, 3: 7, 4: 10}
assert all(jp_heater.build_command("vent", level=l)[6] == 0 for l in (1, 2, 3, 4))
ok("heater: fan only uses the 1/4/7/10 speeds and never the electric element")

# ---- the heater: reading -------------------------------------------------------
st = jp_heater.query(0x05, bytes((3, 2, 21, 0, 2, 0, 0x34, 0x01, 45, 0, 0, 5)))
noise = bytes((0x99, 0x01, 0x05, 0x0C)) + st + bytes((0x01, 0x05))
assert jp_heater.find_frame(noise, 0x05) == st
bad = bytearray(st)
bad[16] ^= 0xFF
assert jp_heater.find_frame(bytes(bad), 0x05) is None
d = jp_heater.decode_status(st)
assert d["mode"] == "Water + air" and d["on"] and d["set_air_c"] == 21
assert d["water_mode"] == "60 °C" and d["fan_level"] == 2 and d["run_hours"] == 0x0134
assert d["run_minutes"] == 45
off_st = jp_heater.decode_status(jp_heater.query(0x05, bytes((10,))))
assert not off_st["on"] and off_st["mode"] == "Off"
ok("heater: status frames found among noise, bad CRCs rejected, fields decoded")

s = jp_heater.decode_sensors(jp_heater.query(0x06, bytes((66, 110, 70, 0, 0, 0, 0, 0, 1, 0, 0, 0))))
assert s["supply_v"] == 13.2 and s["water_temp_c"] == 60 and s["air_temp_c"] == 20
assert s["hx_temp_c"] is None                     # a raw 0 is "no reading", not -50 C
assert not s["fault"]
f = jp_heater.decode_sensors(jp_heater.query(0x06, bytes((66, 110, 70, 0, 0, 0, 0, 0, 0, 0, 60, 1))))
assert f["fault"] and f["error_code"] == 60
assert not jp_heater.decode_sensors(jp_heater.query(0x06, bytes((66,) + (0,) * 9 + (255, 0))))["fault"]
ok("heater: sensors decoded; 0 means no reading; error 255 is not a fault")

# ---- the battery (JBD BMS) ----------------------------------------------------
rb = research("bms")


def bms_basic(volts, amps, residual, nominal, cycles, soc, fet, temps_c, prot=0, cells=4):
    p = struct.pack(">HhHHHH", round(volts * 100), round(amps * 100), round(residual * 100),
                    round(nominal * 100), cycles, (26 << 9) | (3 << 5) | 14)
    p += struct.pack(">HHH", 0, 0, prot) + bytes((0x10, soc, fet, cells, len(temps_c)))
    for t in temps_c:
        p += struct.pack(">H", round((t + 273.15) * 10))
    return p


def frame(cmd, payload):
    chk = (0x10000 - sum(bytes((len(payload),)) + payload)) & 0xFFFF
    return bytes((0xDD, cmd, 0x00, len(payload))) + payload + struct.pack(">H", chk) + b"\x77"


for _ in range(200):
    temps = [round(rng.uniform(-20, 60), 1) for _ in range(rng.randint(1, 3))]
    basic = bms_basic(rng.uniform(10, 14.6), rng.uniform(-150, 150), rng.uniform(0, 100), 100,
                      rng.randint(0, 3000), rng.randint(0, 100), rng.randint(0, 3), temps,
                      prot=rng.choice((0, 1, 1 << 9, 1 << 12, 0x1FFF)))
    cells = b"".join(struct.pack(">H", rng.randint(2900, 3650)) for _ in range(4))
    fb = jbd.find_frame(frame(0x03, basic) + frame(0x04, cells), 0x03)
    fc = jbd.find_frame(frame(0x03, basic) + frame(0x04, cells), 0x04)
    hub = jbd.decode(jbd.payload_of(fb), jbd.payload_of(fc))
    ref = rb.decode_basic(basic)
    ref.update(rb.decode_cells(cells))
    for k in ("voltage", "current", "residual_ah", "nominal_ah", "cycles", "soc", "charge_fet",
              "discharge_fet", "temps_c", "faults", "prod_date", "cells_mv", "cell_delta_mv"):
        assert hub[k] == ref[k], (k, hub[k], ref[k])
ok("battery: 200 random readings decode the same as the research version")

neg = jbd.decode(bms_basic(13.1, -42.5, 50, 100, 1, 50, 3, [20.0]), None)
assert neg["current"] == -42.5 and neg["power_w"] == round(13.1 * -42.5, 1)
ok("battery: discharge current is negative")

m = jbd.mosfet_frame(charge_on=False, discharge_on=True)
assert m[:6] == bytes((0xDD, 0x5A, 0xE1, 0x02, 0x00, 0x01)) and m[-1] == 0x77
assert (sum(m[2:6]) + ((m[6] << 8) | m[7])) & 0xFFFF == 0
assert jbd.mosfet_frame(True, True)[5] == 0 and jbd.mosfet_frame(False, False)[5] == 3
assert jbd.write_result(bytes((0xDD, 0xE1, 0x00, 0x00))) is True
assert jbd.write_result(bytes((0xDD, 0xE1, 0x80, 0x00))) is False
assert jbd.write_result(b"") is None
ok("battery: switch commands carry the right bits and checksum; replies read")

# ---- the DC-DC charger (Renogy) ------------------------------------------------
rr = research("renogy")
assert renogy_dec.read_cmd(0x0100, 35) == rr.read_cmd(0x0100, 35)
for _ in range(200):
    regs = [rng.randint(0, 0xFFFF) for _ in range(35)]
    regs[3] = (rng.randint(0, 255) << 8) | rng.randint(0, 255)
    hub, ref = renogy_dec.decode(regs), rr.decode_live(regs)
    for k in ("soc", "battery_v", "charge_a", "charge_w", "ctrl_temp_c", "batt_temp_c", "alt_v",
              "alt_a", "alt_w", "solar_v", "solar_a", "solar_w", "charge_out_w", "today_min_v",
              "today_max_v", "today_max_chg_a", "today_max_chg_w", "today_chg_ah",
              "today_chg_wh", "run_days", "full_charges", "total_gen_kwh", "source"):
        assert hub[k] == ref[k], (k, hub[k], ref[k])
ok("DC-DC: the read command and 200 random register blocks match the research version")

r = [0] * 35
r[3] = (0x80 | 5) << 8 | 25           # controller -5 C (sign bit), battery 25 C
r[4], r[5], r[7], r[8] = 136, 3065, 186, 607
d = renogy_dec.decode(r)
assert d["ctrl_temp_c"] == -5 and d["batt_temp_c"] == 25
assert d["alt_w"] == 416.8 and d["solar_w"] == 112.9 and d["source"] == "Alternator + Solar"
ok("DC-DC: negative temperatures, per-source watts and the source name")

# ---- the mains charger (Victron, encrypted adverts) ------------------------------
try:
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
except ImportError:
    print("skip  Victron (needs the cryptography package)")
else:
    class _AES:
        def __init__(self, key, mode):
            self.c = Cipher(algorithms.AES(key), modes.ECB())

        def encrypt(self, block):
            e = self.c.encryptor()
            return e.update(block) + e.finalize()
    sys.modules["ucryptolib"].aes = _AES
    import victron_dec

    key = bytes(range(16))

    def victron_ctr(data, iv):
        """AES-CTR as Victron's own library (victron_ble) does it: the counter
        is a 128-bit LITTLE-endian number starting at the advert's IV, so block
        n is encrypted from (iv + n) little-endian. (The cryptography package's
        CTR mode steps a big-endian counter instead - not the same past 16
        bytes - so the keystream is made here block by block.)"""
        ecb = Cipher(algorithms.AES(key), modes.ECB()).encryptor()
        out = bytearray()
        for n in range(0, len(data), 16):
            ks = ecb.update(((iv + n // 16) % (1 << 128)).to_bytes(16, "little"))
            out += bytes(a ^ b for a, b in zip(data[n:n + 16], ks))
        return bytes(out)

    def advert(state, err, volts, amps, iv, extra=b""):
        v24 = round(volts * 100) | (round(amps * 10) << 13)
        plain = bytes((state, err)) + v24.to_bytes(3, "little") + extra
        ct = victron_ctr(plain, iv)
        return bytes((0x10, 0x00, 0x30, 0xA3, 0x08, iv & 0xFF, iv >> 8, key[0])) + ct

    d = victron_dec.decode(advert(3, 0, 14.4, 12.3, 0x1234), key.hex())
    assert d["state"] == "Bulk" and d["voltage"] == 14.4 and d["current"] == 12.3
    assert d["model"] == "Blue Smart IP22 Charger 12/30" and d["error"] == "No Error"
    d = victron_dec.decode(advert(5, 0, 13.6, 0.0, 0xFFFF), key.hex())
    assert d["state"] == "Float" and d["current"] == 0.0
    assert victron_dec.decode(b"\x10\x00", key.hex()) is None
    ok("Victron: adverts decrypted and decoded (state, volts, amps, model)")

    # Past the first 16 bytes the counter steps on, and must step as Victron's
    # does, or anything after byte 16 of a record decodes wrong. (Today the hub
    # reads only bytes 0-4, so this has never mattered in practice.)
    pt = bytes(range(40))
    iv = 0x00FF                                    # stepping it carries into byte 1
    ct = victron_ctr(pt, iv)
    assert victron_dec._aes_ctr(key, bytearray(iv.to_bytes(16, "little")), ct) == pt, \
        "the counter steps the wrong end"
    ok("Victron: decryption beyond 16 bytes matches Victron's counter")

print("\nALL OK - %d checks" % len(passed))
