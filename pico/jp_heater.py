# JP diesel heater (SolGP / 'Intrepid') protocol — MicroPython.
# Service 0xfff0, notify 0xfff1, write 0xfff2.
# Frame: 01 <cmd> 0C <12 payload> <crc lo> <crc hi>  (17 bytes, Modbus CRC-16 over first 15)
#   cmd 03 handshake -> reply[9]==0xAA, [3]=product code, [4]=firmware
#   cmd 05 status    -> [3]=mode (1 temp,2 power,3 fan, else off) [4]=set C [5]=level
#                       [7]==0xAB timer on  [9..11]=run time  [14]=run state
#   cmd 06 sensors   -> [3]=supply V*5  [4]=temp+50  [13]=error code  [14]=alarm/fault

# CR12 combi work modes (status byte f[3])
WORKMODE = {3: "Water + air", 4: "Air", 5: "Water", 6: "Ventilation",
            7: "Fuel priming", 8: "Shutting down", 10: "Off"}
WATER_PRESET = {0: "off", 1: "40 °C", 2: "60 °C", 3: "boost"}
# Energy source (status f[6], and the energy byte in a set command).  Confirmed
# from the app's own icons: a fuel canister plus two lightning bolts, each lit or
# greyed - 0 diesel only, 1/2 diesel plus electric stage 1/2, 3/4 electric only.
# The app refuses anything above 0 while AC 220 V is absent (dis[4] == 0, from
# sensor byte 12), so the element can never be asked for without mains present.
ENERGY = {0: "Diesel", 1: "Diesel + electric 1", 2: "Diesel + electric 2",
          3: "Electric 1", 4: "Electric 2"}
ENERGY_NEEDS_AC = (1, 2, 3, 4)

# Status f[14] is the host run state on this maker's air-only and water-only
# controllers.  Measured on this CR12 combi it sat at a constant 5 while the
# exchanger cooled from 95 to 83 C with the flame out, so it is NOT a burn
# indicator here - it is decoded for diagnosis only, and whether the burner is
# alight is judged from the exchanger trend instead.  Worth re-checking at the
# next ignition to see whether it ever moves.
RUN_STATE = {0: "Idle", 1: "Self-check", 2: "Ignition", 3: "Stable combustion",
             5: "Priming the fuel pump", 6: "Ventilating", 7: "Intermittent (flame out)",
             8: "Shutting down", 10: "Immediate stop"}


def crc16(data):
    crc = 0xFFFF
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc


def query(cmd, payload=b""):
    body = bytearray((0x01, cmd, 0x0C))
    body.extend(payload)
    while len(body) < 15:
        body.append(0)
    c = crc16(body)
    body.append(c & 0xFF)
    body.append((c >> 8) & 0xFF)
    return bytes(body)


def build_command(action, temp=20, water=2, level=1, energy=0):
    """Build a CR12 combi set frame (cmd 0x04).
      payload = workMode, waterTemp, airTemp, energy, windSpeed, tempOffset
      workMode 3=water+air 4=air 5=water 6=vent 10=off
      waterTemp preset 1=40C 2=60C 3=boost ; airTemp 5-35 C
      energy: the heater's own source setting (0 diesel .. 4 electric) - pass the
      value read back from status f[6] so a command doesn't change the source.
      Matches the app: vent and off zero the energy byte.
    """
    t = max(5, min(35, int(temp)))
    w = int(water) if int(water) in (1, 2, 3) else 2
    lvl = max(1, min(4, int(level)))
    en = int(energy) if int(energy) in (0, 1, 2, 3, 4) else 0
    ws = lvl - 1                       # heating modes: windSpeed = fanLevel-1 (0-3)
    if action == "off":
        return query(0x04, bytes((10, 0, 0, 0, 0, 0)))
    if action == "air":
        return query(0x04, bytes((4, 0, t, en, ws, 5)))
    if action == "water":
        return query(0x04, bytes((5, w, 0, en, ws, 5)))
    if action == "combi":
        return query(0x04, bytes((3, w, t, en, ws, 5)))
    if action == "vent":
        ws_vent = {1: 1, 2: 4, 3: 7, 4: 10}.get(lvl, 1)   # vent uses 1/4/7/10
        return query(0x04, bytes((6, 0, 0, 0, ws_vent, 5)))
    return None


def find_frame(buf, want):
    """Non-destructive scan for a valid 17-byte frame with cmd==want."""
    n = len(buf)
    i = 0
    while i + 17 <= n:
        if buf[i] == 0x01 and buf[i + 1] == want:
            f = bytes(buf[i:i + 17])
            if crc16(f[:15]) == (f[15] | (f[16] << 8)):
                return f
        i += 1
    return None


def decode_handshake(f):
    return {"product": f[3], "firmware": f[4], "marker": f[9]}


def decode_status(f):
    """CR12 combi status (cmd 5):
      f[3]=workMode f[4]=water preset f[5]=set air C f[6]=energy
      f[7]=fan level  f[9..11]=total run time  f[14]=burner run state
    """
    m = f[3]
    return {
        "mode_code": m,
        "mode": WORKMODE.get(m, "mode %d" % m),
        "on": m in (3, 4, 5, 6),
        "water_mode": WATER_PRESET.get(f[4], "?"),
        "water_code": f[4],
        "set_air_c": f[5],
        "energy": ENERGY.get(f[6], "src %d" % f[6]),
        "energy_code": f[6],
        "fan_level": f[7],
        "run_hours": (f[10] << 8) | f[9],
        "run_minutes": f[11],
        # raw only - see the note above; do not label it as a burner state
        "run_state_code": f[14],
    }


def decode_sensors(f):
    """CR12 combi sensors (cmd 6):
      f[3]=supply*5  f[4]=water/coolant temp+50  f[5]=measured air temp+50
      f[12]=AC220 element  f[13]=error  f[14]=alarm/fault
      f[4] is the water temperature: the app's water-only screens (ShuiNuan, CR14)
      decode that same byte with the same -50 offset and show it as the coolant
      temperature, and error codes 60/61/62 are water-probe open/short faults.
      The combi screen computes it and then throws it away, which is why the
      phone app never shows it.  f[6],f[7] are two more probes, placement TBC.
    """
    err = f[13]

    def temp(v):
        # A raw 0 is "no reading", not -50 C: the controller zeroes a channel it
        # has no probe value for, and a real -50 C is not a thing this van will
        # ever see.  Report it as missing so the UI keeps the last good figure.
        return None if v == 0 else v - 50

    return {
        "supply_v": round(f[3] / 5.0, 1) if f[3] else None,
        "air_temp_c": temp(f[5]),
        "water_temp_c": temp(f[4]),
        # f[6] is the burner heat exchanger: it climbs ~3.5 C/min while the
        # flame is lit (seen reaching 112 C) and goes flat or falls the moment
        # the burn stops, so its trend tells us whether it is actually firing.
        "hx_temp_c": temp(f[6]),
        "sensor4_c": temp(f[7]),
        # f[12] is what the phone app shows as "AC220: YES/NO", but this CR12
        # reports 1 with no 230 V hook-up connected at all (checked against the
        # Victron mains charger, which was reporting nothing). It is therefore
        # NOT trustworthy as a mains indicator and must not gate the electric
        # element - main.py uses the mains charger for that. Kept raw for
        # diagnosis only.
        "ac220_raw": f[12] > 0,
        "error_code": err,
        "fault": (err != 0 and err != 255),
        "alarm_flag": f[14],
    }
