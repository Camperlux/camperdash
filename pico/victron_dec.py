# Victron "Instant Readout" AC charger (Blue Smart IP22) decode — MicroPython port.
# Passive advert (manufacturer id 0x02E1), AES-128-CTR via ucryptolib ECB.

import ucryptolib

MANUFACTURER_ID = 0x02E1

_OP = {0: "Off", 1: "Low power", 2: "Fault", 3: "Bulk", 4: "Absorption",
       5: "Float", 6: "Storage", 7: "Equalise", 245: "Starting up",
       247: "Auto equalise", 252: "External control"}
_MODELS = {0xA330: "Blue Smart IP22 Charger 12/30"}


def _aes_ctr(key, ctr, data):
    out = bytearray(len(data))
    counter = bytearray(ctr)
    off = 0
    while off < len(data):
        ks = ucryptolib.aes(key, 1).encrypt(bytes(counter))  # 1 = ECB
        n = min(16, len(data) - off)
        for i in range(n):
            out[off + i] = data[off + i] ^ ks[i]
        off += 16
        # Step the counter LITTLE-endian, as Victron's own library does
        # (victron_ble: Counter.new(128, initial_value=iv, little_endian=True)).
        # It stepped the big end until 2026-10-02: harmless while only bytes
        # 0-4 are read, but anything past byte 16 would have come out wrong.
        j = 0
        while j < 16:
            counter[j] = (counter[j] + 1) & 0xFF
            if counter[j]:
                break
            j += 1
    return bytes(out)


def decode(raw, key_hex):
    if len(raw) < 10 or raw[0] != 0x10:
        return None
    key = bytes(int(key_hex[i:i + 2], 16) for i in range(0, len(key_hex), 2))
    if len(key) != 16:
        return None
    model = raw[2] | (raw[3] << 8)
    iv = raw[5] | (raw[6] << 8)
    ct = raw[8:]
    ctr = bytearray(16)
    ctr[0] = iv & 0xFF
    ctr[1] = (iv >> 8) & 0xFF
    try:
        pt = _aes_ctr(key, ctr, ct)
    except Exception:
        return {"connected": False, "error": "decrypt failed"}
    if len(pt) < 5:
        return {"connected": False, "error": "short packet"}
    state = pt[0]
    err = pt[1]
    v24 = pt[2] | (pt[3] << 8) | (pt[4] << 16)
    v_raw = v24 & 0x1FFF
    c_raw = (v24 >> 13) & 0x7FF
    v = None if v_raw == 0x1FFF else v_raw * 0.01
    c = None if c_raw == 0x7FF else c_raw * 0.1
    p = round(v * c, 1) if (v is not None and c is not None) else None
    return {
        "connected": True,
        "model": _MODELS.get(model, "Victron 0x%04x" % model),
        "state": _OP.get(state, "State %d" % state),
        "voltage": v, "current": c, "power_w": p,
        "error": "No Error" if err == 0 else "Error %d" % err,
    }
