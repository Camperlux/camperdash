# External GPS: u-blox NEO-7M (and family) on a UART, speaking NMEA-0183.
#
# The NMEA parsing is ported from Rapid3/src/ext_gps.py, which was written and
# proven against this same receiver. The UART setup is the Pico's rather than
# QuecPython's, and the reading is non-blocking so it can share the event loop
# with the BLE poll instead of stalling it.
#
# Read-only. The receiver's RX line can be wired for UBX configuration later
# (raising the rate, disabling unused sentences), but nothing here writes to it.
#
# Why a separate receiver at all: the browser's Geolocation API needs a secure
# origin and the hub is served over plain http, so the Outside page has had no
# way to know where the van is. A receiver on a UART answers that without
# depending on the phone at all.

import time

from machine import UART, Pin

# A fix older than this is history, not a position.
FIX_STALE_S = 30

_uart = None
_err = ""
_buf = bytearray(96)          # one NMEA sentence is at most 82 characters
_n = 0
_fix = None
_fix_at = 0
_sentences = 0
_last_sentence = ""


def _coord(value, hemi):
    """NMEA DDMM.MMMM / DDDMM.MMMM to decimal degrees."""
    if not value or not hemi:
        return None
    try:
        dot = value.index(".")
        deg = int(value[:dot - 2])
        minutes = float(value[dot - 2:])
        c = deg + minutes / 60.0
        if hemi in ("S", "W"):
            c = -c
        return c
    except Exception:
        return None


def parse_rmc(sentence):
    """Position, speed and heading. Returns None if the sentence is not usable.

    The timestamp is taken from the sentence itself rather than from any clock
    on this board: NMEA mandates UTC for both the time and date fields, so it is
    right even when the hub's own clock has never been set.
    """
    try:
        parts = sentence.split(",")
        if len(parts) < 10 or not parts[0].endswith("RMC"):
            return None
        lat = _coord(parts[3], parts[4])
        lon = _coord(parts[5], parts[6])
        ts = None
        t, d = parts[1], parts[9]
        if t and d and len(t) >= 6 and len(d) >= 6:
            try:
                ts = (2000 + int(d[4:6]), int(d[2:4]), int(d[0:2]),
                      int(t[0:2]), int(t[2:4]), int(t[4:6]))
            except ValueError:
                ts = None
        return {"lat": lat, "lon": lon,
                "speed_kn": float(parts[7]) if parts[7] else 0.0,
                "heading_deg": float(parts[8]) if parts[8] else 0.0,
                "valid": parts[2] == "A" and lat is not None and lon is not None,
                "ts_utc": ts}
    except Exception:
        return None


def parse_gga(sentence):
    """Satellite count, HDOP and altitude - the quality RMC does not carry."""
    try:
        parts = sentence.split(",")
        if len(parts) < 10 or not parts[0].endswith("GGA"):
            return None
        return {"fix_quality": int(parts[6]) if parts[6] else 0,
                "satellites": int(parts[7]) if parts[7] else 0,
                "hdop": float(parts[8]) if parts[8] else 99.9,
                "altitude_m": float(parts[9]) if parts[9] else 0.0}
    except Exception:
        return None


def _checksum_ok(line):
    """NMEA carries an XOR checksum. A receiver with a marginal supply or a
    long cable produces occasional corrupt sentences, and a corrupt coordinate
    is far worse than a missing one."""
    star = line.rfind("*")
    if star < 1 or star + 3 > len(line):
        return False
    try:
        want = int(line[star + 1:star + 3], 16)
    except ValueError:
        return False
    got = 0
    for ch in line[1:star]:
        got ^= ord(ch)
    return got == want


def init(uart_id, tx_pin, rx_pin, baud=9600):
    global _uart, _err
    try:
        _uart = UART(uart_id, baudrate=baud, tx=Pin(tx_pin), rx=Pin(rx_pin),
                     timeout=0, timeout_char=0)
        _err = ""
        return True
    except Exception as e:
        _uart = None
        _err = "%s: %s" % (type(e).__name__, e)
        print("gps: could not open the UART:", _err)
        return False


def available():
    return _uart is not None


def poll():
    """Read whatever has arrived and parse any complete sentences.

    Non-blocking: it takes what is in the buffer and returns. The receiver
    sends about half a kilobyte a second at 9600 baud, so calling this a few
    times a second keeps up without ever waiting on the UART.
    """
    global _n, _fix, _fix_at, _sentences, _last_sentence
    if _uart is None:
        return False
    got = False
    try:
        data = _uart.read()
    except Exception:
        return False
    if not data:
        return False
    for b in data:
        if b == 10 or b == 13:            # end of sentence
            if _n > 6:
                try:
                    line = bytes(_buf[:_n]).decode()
                except Exception:
                    line = ""
                _n = 0
                if line.startswith("$") and _checksum_ok(line):
                    _sentences += 1
                    _last_sentence = line[:20]
                    body = line[1:line.rfind("*")]
                    r = parse_rmc(body)
                    if r is not None:
                        if r["valid"]:
                            f = dict(_fix) if _fix else {}
                            f.update(r)
                            _fix = f
                            _fix_at = time.time()
                            got = True
                        continue
                    g = parse_gga(body)
                    if g is not None and _fix:
                        _fix.update(g)
            else:
                _n = 0
        elif _n < len(_buf):
            _buf[_n] = b
            _n += 1
        else:
            _n = 0                        # overlong line: drop it and resync
    return got


def status():
    """What the Outside page and the API are told.

    A fix that has stopped arriving is reported as stale rather than quietly
    kept: a van that has been driven with the aerial unplugged should not show
    where it was parked yesterday as though it were current.
    """
    if _uart is None:
        return {"enabled": False, "error": _err, "fix": False}
    age = (time.time() - _fix_at) if _fix_at else None
    fresh = _fix is not None and age is not None and age < FIX_STALE_S
    st = {"enabled": True, "error": "", "fix": bool(fresh),
          "sentences": _sentences, "age_s": round(age) if age is not None else None}
    if _fix:
        st["lat"] = round(_fix.get("lat") or 0.0, 5)
        st["lon"] = round(_fix.get("lon") or 0.0, 5)
        st["speed_kn"] = round(_fix.get("speed_kn") or 0.0, 1)
        st["heading_deg"] = round(_fix.get("heading_deg") or 0.0)
        st["satellites"] = _fix.get("satellites")
        st["hdop"] = _fix.get("hdop")
        st["altitude_m"] = _fix.get("altitude_m")
        st["ts_utc"] = _fix.get("ts_utc")
        st["stale"] = not fresh
    return st
