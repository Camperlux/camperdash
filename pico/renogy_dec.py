# Renogy DC-DC charger (BT-2, Modbus RTU over BLE) decode — MicroPython port.
# Live registers 0x0100.. read with function 0x03, device id 0xFF.

DEVICE_ID = 0xFF
LIVE_REG = 0x0100
LIVE_COUNT = 35

# State 8 comes from the community register maps (cyrils/renogy-bt): the charger
# passing the alternator straight through rather than running a charge stage.
# Without it the dashboard showed "Unknown" for a perfectly normal state.
_STATES = {0: "Not charging", 1: "Activated", 2: "MPPT", 3: "Equalising",
           4: "Boost", 5: "Float", 6: "Current limiting",
           8: "Alternator direct"}

# Public alias: van_status.py inverts this to put the state on the wire. Keeping
# one table means the hub, the wire and the server cannot drift apart.
STATES = _STATES


def crc16(data):
    crc = 0xFFFF
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc


def read_cmd(reg, count):
    body = bytes((DEVICE_ID, 0x03, (reg >> 8) & 0xFF, reg & 0xFF,
                  (count >> 8) & 0xFF, count & 0xFF))
    c = crc16(body)
    return body + bytes((c & 0xFF, (c >> 8) & 0xFF))


def regs_of(frame):
    n = frame[2]
    return [(frame[3 + 2 * i] << 8) | frame[4 + 2 * i] for i in range(n // 2)]


def _t(b):
    return -(b & 0x7F) if b & 0x80 else b


def decode(r, debug_regs=False):
    alt_a = r[5] / 100.0
    sol_a = r[8] / 100.0
    src = ("Alternator + Solar" if alt_a > 0.05 and sol_a > 0.05
           else "Alternator" if alt_a > 0.05
           else "Solar" if sol_a > 0.05 else "None")
    d = {
        "connected": True,
        "soc": r[0],
        "battery_v": r[1] / 10.0,
        "charge_a": r[2] / 100.0,
        "charge_w": round(r[1] / 10.0 * r[2] / 100.0, 1),
        "ctrl_temp_c": _t(r[3] >> 8), "batt_temp_c": _t(r[3] & 0xFF),
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
        "total_gen_kwh": round(((r[28] << 16) | r[29]) / 1000.0, 1),
        "state": _STATES.get(r[32] & 0xFF, "Unknown"),
        "source": src,
        # Registers 289/290 carry the charger's alarm bits. They are NOT decoded
        # yet: the one published mapping (cyrils/renogy-bt parse_state) reads a
        # single byte and then tests bits 8-12 of it, which can never be set, so
        # porting it would produce alarms that never fire or fire on the wrong
        # condition. Inventing a fault indicator is worse than admitting we have
        # none, so this stays empty until the mapping is confirmed against the
        # live device using the raw words below.
        "faults": [],
        "alarm_regs": [r[33], r[34]],
    }
    if debug_regs:
        # Every word as received, so the registers we do not decode can be
        # watched as the charger changes state. This is how the alarm bits and
        # the starter-battery behaviour get pinned down from the real device
        # instead of from a guess.
        d["regs"] = list(r)
    return d
