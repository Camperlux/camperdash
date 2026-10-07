# Fogstar / JBD (Xiaoxiang) BMS decode — MicroPython port of bms.py.
# Frame: DD <cmd> <status> <len> <payload...> <chk_hi> <chk_lo> 77

CMD_BASIC = bytes((0xDD, 0xA5, 0x03, 0x00, 0xFF, 0xFD, 0x77))
CMD_CELLS = bytes((0xDD, 0xA5, 0x04, 0x00, 0xFF, 0xFC, 0x77))

_PROT = ("Cell overvoltage", "Cell undervoltage", "Pack overvoltage",
         "Pack undervoltage", "Charge over-temp", "Charge under-temp",
         "Discharge over-temp", "Discharge under-temp", "Charge overcurrent",
         "Discharge overcurrent", "Short circuit", "IC error", "MOSFET locked")


# ---- MOSFET control (write) ----------------------------------------------
# Register 0xE1, two bytes: bit0 turns the CHARGE MOSFET off, bit1 the
# DISCHARGE MOSFET off (so 0x0000 = both conducting).  The checksum is the same
# scheme as the read commands - 0x10000 minus the sum of register, length and
# data - which is verified against CMD_BASIC/CMD_CELLS above.
MOSFET_REG = 0xE1


def mosfet_frame(charge_on, discharge_on):
    bits = (0 if charge_on else 1) | (0 if discharge_on else 2)
    body = bytes((MOSFET_REG, 2, 0x00, bits))
    chk = (0x10000 - sum(body)) & 0xFFFF
    return bytes((0xDD, 0x5A)) + body + bytes((chk >> 8, chk & 0xFF, 0x77))


def write_result(buf):
    """Find the BMS's reply to a register write.  Returns True (accepted),
    False (refused) or None (nothing came back yet)."""
    n = len(buf)
    for i in range(n - 1):
        if buf[i] == 0xDD and buf[i + 1] == MOSFET_REG and i + 3 < n:
            return buf[i + 2] == 0x00        # status byte: 0 = OK, 0x80 = error
    return None


def _u16(b, i):
    return (b[i] << 8) | b[i + 1]


def _s16(b, i):
    v = _u16(b, i)
    return v - 0x10000 if v >= 0x8000 else v


def find_frame(buf, want):
    """Non-destructively scan buf for a complete DD..77 frame with cmd==want,
    status 0. (MicroPython bytearrays don't support slice deletion, so we scan
    rather than consume; the buffer holds only the current response.)"""
    n = len(buf)
    i = 0
    while i + 7 <= n:
        if buf[i] != 0xDD:
            i += 1
            continue
        total = 4 + buf[i + 3] + 3
        if i + total > n:
            break
        if buf[i + 1] == want and buf[i + 2] == 0x00:
            return bytes(buf[i:i + total])
        i += total
    return None


def payload_of(frame):
    return frame[4:4 + frame[3]]


def decode(basic, cells):
    st = {"connected": True}
    if basic and len(basic) >= 23:
        ntc = basic[22]
        temps = []
        for i in range(ntc):
            idx = 23 + 2 * i
            if idx + 1 < len(basic):
                temps.append(round(_u16(basic, idx) / 10.0 - 273.15, 1))
        prot = _u16(basic, 16)
        faults = [n for i, n in enumerate(_PROT) if prot & (1 << i)]
        fet = basic[20]
        v = _u16(basic, 0) / 100.0
        c = _s16(basic, 2) / 100.0
        prod = _u16(basic, 10)
        st.update({
            "voltage": round(v, 2),
            "current": round(c, 2),
            "power_w": round(v * c, 1),
            "residual_ah": round(_u16(basic, 4) / 100.0, 2),
            "nominal_ah": round(_u16(basic, 6) / 100.0, 2),
            "cycles": _u16(basic, 8),
            "soc": basic[19],
            "charge_fet": bool(fet & 1),
            "discharge_fet": bool(fet & 2),
            "temps_c": temps,
            "faults": faults,
            "prod_date": "%04d-%02d-%02d" % (2000 + (prod >> 9), (prod >> 5) & 0x0F, prod & 0x1F),
        })
    if cells and len(cells) >= 2:
        n = len(cells) // 2
        mv = [_u16(cells, 2 * i) for i in range(n)]
        if mv:
            st["cells_mv"] = mv
            st["cell_delta_mv"] = max(mv) - min(mv)
    return st
