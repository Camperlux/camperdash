# Camperlux Van Hub — API Reference

The van hub (CamperDash firmware, `pico/`) serves a small HTTP/JSON API. The hub's
own web app, the van display and the Android app all
use it. This document describes the API as implemented in `pico/httpd.py` and
`pico/main.py`.

> **Status: internal API.** It is not versioned and has no compatibility promise.
> It changes alongside its clients, so deploy the hub and the display together when
> it changes. Pin a client to a known firmware build if it must not break.

---

## 1. Basics

| | |
|---|---|
| Transport | Plain HTTP/1.1, port **80** (`HTTP_PORT` in `config.py`). No TLS. |
| Format | JSON request and response bodies (`Content-Type: application/json`), except where noted. |
| Connections | Every response is sent with `Connection: close`. Some large responses are streamed with **no `Content-Length`** and end when the socket closes. |
| Caching | API responses are `Cache-Control: no-store`. |
| Methods | Reads are `GET`. Writes are `POST` with a JSON object body. A body that isn't valid JSON is treated as `{}`. |
| Body limits | `/api/heater/cmd` and `/api/autoheat`: 1024 bytes. Other POSTs: 2048 bytes. Over the limit → `413`. |
| Header limit | Any request or header line over 512 bytes closes the connection without a reply. |

### Finding the hub

The hub's DHCP address changes, so don't hard-code it.

1. **UDP discovery:** broadcast the ASCII payload `camperdash?` to UDP port **50505**.
   The hub replies `camperdash hub` from its own address. A hub can have discovery
   turned off (`DISCOVERY_ENABLED = False`).
2. **Fallback:** on the van's own hotspot (`Camperlux`), the hub is always **192.168.4.1**.
   The hotspot is switched off while the hub is joined to a network on the same subnet.

The hub does **not** answer mDNS.

### Response conventions

- Write endpoints answer `{"ok": true, ...}` or `{"ok": false, "error": "<human-readable reason>"}`.
  Validation failures come back as **HTTP 200 with `ok: false`**, so check `ok` and not just the status code.
- `403 Forbidden` → `{"ok": false, "error": "the hub's password is needed", "auth": true}` (see §2).
- `404` → plain text `not found`.
- `500` → `{"ok": false, "error": "<ExceptionType>: <message>"}`.

### Load limits — read before writing a client

The hub is a microcontroller with a fragmented heap and a single BLE radio.

- **Poll `/api/data` no faster than every 2 s.** That's the rate the hub's own clients use.
  The hub reads its BLE devices on a fixed cycle anyway, so polling faster returns the same data.
- **Don't send several large requests at once.** Simultaneous `/api/data` + `/api/history` +
  `/api/weather` can return `500`. Stagger initial fetches and retry with a short back-off.
- For small, frequent checks use `/api/alerts` (a few dozen bytes) instead of `/api/data` (~3–5 KB).
- Commands that use BLE (heater, battery MOSFETs) **hold the radio for several seconds** and
  pause battery/charger polling while they run. Don't loop them.

---

## 2. Authentication

Reading is open. Most writes are protected by the **hub password**, if one is set.

**Sending the key:** add the header `X-Token: <key>` to the request.

**Deriving the key from the password** (as the web app does in `static/common.js`):

```
key = hex( SHA-256( "camperdash-hub:" + password ) )
```

The hub stores only `SHA-256(key)` and compares against that. These keys are also accepted:

- a **paired device key** issued by `/api/pair/claim` (§3.10), and
- the legacy `API_TOKEN` from `config.py`, if one is still set.

A request is authorised when **any** of these is true:

- no password is set,
- "require the password" (`auth.web`) is switched off, or
- `X-Token` matches one of the keys above.

`GET /api/data` reports whether a password exists (`auth_set`).

### Protected vs open

| Protected (need `X-Token` when a password is set) | Always open |
|---|---|
| `GET /api/settings`, `POST /api/settings` | all other `GET`s |
| `POST /api/settings/reveal` | `{"what": "ap" \| "net" \| "victron", "ssid": ...}` → `{value}`: one stored password, for Settings' eye button. Only while a hub password is set and required; the hub's own password is never stored, so can't be shown. |
| `GET /api/wifi/networks` | `POST /api/display` (brightness, ambient lights, kitchen timer…) |
| `POST /api/heater/cmd`, `POST /api/autoheat` | `POST /api/alerts/ack` |
| `POST /api/bms/fet`, `POST /api/storage` | `POST /api/panic` (on **and** off, so nobody is locked out of the alarm) |
| `POST /api/switches` | `POST /api/pair/claim` |
| `POST /api/timers`, `POST /api/guard` | |
| `POST /api/level`, `POST /api/alerts/test` | |
| `POST /api/scan/ble`, `POST /api/scan/wifi`, `POST /api/reboot` | |
| `POST /api/pair/code` | |

> The hub has no TLS, so the key travels in clear text on the LAN. Treat the van's
> WiFi as the trust boundary.

---

## 3. Endpoints

### 3.1 Live data

#### `GET /api/data`

The full live snapshot. Streamed (no `Content-Length`). Every client polls this.
Top-level fields, grouped:

**Leisure battery (Fogstar/JBD BMS)** — at the top level for historical reasons:

| Field | Meaning |
|---|---|
| `connected` | BMS reading is current (`false` once older than `BATTERY_STALE_MS`; then `stale: true`) |
| `voltage`, `current`, `power_w` | V, A (**+ = charging**), W |
| `soc` | State of charge, % |
| `residual_ah`, `nominal_ah` | Remaining / rated capacity, Ah |
| `cells_mv`, `cell_delta_mv` | Per-cell mV and the spread |
| `temps_c` | `[T1, T2, T3]` NTCs (T1 ≈ board, T2/T3 ≈ cells — labelling tentative) |
| `charge_fet`, `discharge_fet` | MOSFET states |
| `cycles`, `prod_date`, `faults` | Cycle count, pack production date, protection flags |
| `updated`, `age_s` | Unix time of this snapshot (`null` before the clock is set), seconds since the BMS answered |
| `stats` | Energy today (`wh_in`, `wh_out`, `ah_in`, `ah_out`), extremes (`ext`), `time_to_full_h`, `time_to_empty_h`, plus hub health: `uptime_s`, `mem_free`, `wifi_*`, `poll_error(s)`, `i2c` |

**Other devices** — each an object with its own `connected`:

| Field | Source | Notable keys |
|---|---|---|
| `renogy` | Renogy DC-DC/MPPT | `solar_v/a/w`, `alt_v/a/w` (alt_v = starter battery voltage), `charge_a`, `charge_w`, `charge_out_w` (total output), `battery_v`, `batt_temp_c` (external probe), `ctrl_temp_c`, `state`, `source`, `today_*`, `faults` |
| `starter` | The starter battery, from the DC-DC's input side | `state` (`offline`, `charging` - engine on or 13.0 V and over, `settling` - within 30 min of the engine stopping, `rest`), `v`, `soc` (%, from the resting-voltage chart for the type in Settings - `alerts.starter_type`, `agm` or `flooded`; `null` while charging) |
| `victron` | Victron IP22 mains charger | `connected` (= mains hook-up live), `current`, … |
| `heater` | Diesel/electric heater | `on`, `mode`, `mode_code`, `set_air_c`, `water_mode`, `energy`, `supply_v`, `air_temp_c`, `water_temp_c`, `run_state_code`, `error_code`, `fault`, `remembered` (last reading, not live), `at` |
| `derived` | Computed | `load_a`, `load_w` — the true load: charge in − battery net |
| `today` | Computed | Wh today by source: `solar`, `alt`, `mains`, `load`; `day` |

**State of hub features:** `storage` (§3.5), `autoheat` (§3.4), `timers` (§3.4), `guard` (§3.7),
`alerts` (§3.6), `display` (§3.8), `wake` (alarm-clock warm-up), `engine` (bool, engine judged running from the DC-DC's alternator side; always false while `alerts.starter_charger` is set - a mains charger on the starter - until the GPS sees the van move),
`mains_live` (bool), `level` (§3.9), `gps`.

**Configuration published for clients:** `switch_names`, `switch_icons`, `relays` (number of physical
relays; 0 = switches are on-screen only), `checklist`, `views`, `level_scale`, `level_refresh_ms`, `van`
(`wheelbase_mm`, `track_mm`), `panel`, `alert_sound`, `auth_set`, `wifi_rev`, `display_fw`.

Example (trimmed):

```json
{
  "connected": true, "voltage": 13.31, "current": 2.01, "power_w": 26.8, "soc": 80,
  "charge_fet": true, "discharge_fet": true, "cells_mv": [3329, 3332, 3332, 3324],
  "renogy": {"connected": true, "solar_w": 42.6, "alt_v": 11.6, "alt_a": 0.0,
             "charge_a": 2.31, "state": "MPPT", "batt_temp_c": 25},
  "victron": {"connected": false},
  "heater": {"connected": false, "remembered": true, "on": false, "mode": "Off"},
  "derived": {"load_a": 0.3, "load_w": 4.0},
  "today": {"solar": 43, "alt": 0, "mains": 0, "load": 33, "day": "2026-10-02"},
  "alerts": [], "engine": false, "mains_live": false, "relays": 6,
  "switch_names": ["Water pump", "Fridge", "Garage light", "Inverter", "Master Lights", "Fuel Pump"]
}
```

#### `GET /api/history`

Recent in-RAM history, oldest first. Streamed JSON array of rows:

```
[unix_time, voltage, current, soc|null, power_w]
```

#### `GET /api/history/long[?since=<unix_time>]`

The longer history from flash (`hist.csv`, up to about a week), in the same row format.
`since` skips older rows. Use it, because the full file is large.

### 3.2 Heater

#### `POST /api/heater/cmd` 🔒

Sends a command over BLE and **reads the heater back** to confirm it. This takes several seconds.

| Field | Values |
|---|---|
| `action` | `off`, `air`, `water`, `combi` (air + water), `vent`, `read` (status only) |
| `temp` | Air set-point °C, 5–35 (default 20) |
| `water` | Water preset: `1` = 40 °C, `2` = 60 °C, `3` = boost (default 2) |
| `level` | Fan level 1–4 (default 1) |
| `energy` | `0` diesel, `1`/`2` diesel + electric stage 1/2, `3`/`4` electric only, `"auto"` (electric stage 1 if the mains hook-up is live, else diesel), or omit to keep the heater's own setting |
| `bg` | `true` = background refresh: fewer connection retries |

The electric element is **refused unless the mains hook-up is live**, judged from the Victron charger.
Close the phone app first, because the heater accepts one BLE client at a time.

Response: `{"ok": true, "status": {...}, "sensors": {...}, "product": 28, "firmware": 187, "energy_auto": "..."}`,
or `{"ok": false, "error": "couldn't connect after retries - ..."}`.

### 3.3 Battery

#### `POST /api/bms/fet` 🔒

Switches the battery MOSFETs. Each change is read back from the pack.

```json
{"charge": false}
{"discharge": false, "confirm": "CUT POWER"}
```

- Omitted fields keep their current state. A fresh BMS reading is required, otherwise
  `ok: false, "no recent battery reading"`.
- ⚠️ **Turning discharge off cuts the van's 12 V supply, including the hub.** It can't be undone
  over the API. Without `"confirm": "CUT POWER"` the hub answers `{"ok": false, "needs_confirm": true}`.
- Setting `charge` by hand switches storage mode off.

#### `POST /api/storage` 🔒

Storage mode: holds SoC near a target by switching **charge only**. It never touches discharge.

```json
{"on": true, "target": 65}
```

`target` must be 30–95 %. Switching off re-enables charging if storage mode was holding it off.
Response: `{"ok": true, "storage": {...}, "charge_restored": bool}`.

### 3.4 Heating automation

#### `POST /api/autoheat` 🔒

Frost protection, driven by the DC-DC's battery temperature probe. All fields are optional:

| Field | Range |
|---|---|
| `armed` | bool (disarming turns off a heater it started) |
| `on_below` / `off_above` | −20…25 / −15…30 °C (`off_above` is forced at least 3 °C above `on_below`) |
| `target_c` | 5–35 |
| `min_soc` | 0–100 % |
| `max_run_min` | 10–720 |

Response: `{"ok": true, "autoheat": {...}}`.

#### `POST /api/timers` 🔒

The two fixed heater timers, `warm` and `water`. Each timer is updated by `id`:

```json
{"timers": [{"id": "warm", "on": true, "at": "07:15", "temp": 20, "run_min": 60}]}
```

`at` = `HH:MM`, `temp` 5–35, `water` 1–3, `run_min` 15–240. Response: `{"ok": true, "timers": [...]}`.

### 3.5 Switches (relays)

#### `POST /api/switches` 🔒

```json
{"i": 0, "on": true}
{"switches": [true, false, true, false, true, false]}
```

Index 0–5 matches `switch_names` in `/api/data`. The relay switches first, then the state is saved,
and it is restored after a reboot. Response: same as `GET /api/display`, including `display.switches`.

### 3.6 Alerts and panic

#### `GET /api/alerts`

Small and cheap. Use it for frequent polling.

```json
{"alerts": [{"id": "low_battery", "level": "warn", "title": "Battery low",
             "detail": "...", "acked": false}],
 "sound": true, "panic": false, "drev": 558}
```

Alert ids: `mains_while_running`, `low_battery`, `battery_health`, `panic`, `test`.
`level` is `danger` or `warn`. `drev` is the display-settings revision (§3.8).

#### `POST /api/alerts/ack`

`{"id": "<alert id>"}`, or `{}` for all. Clears the alarm on every screen. The alert stays listed
with `acked: true` until its condition ends. Acknowledging `panic` stops the panic.

#### `POST /api/alerts/test` 🔒

Raises a 60-second test alarm.

#### `POST /api/panic`

`{"on": true, "by": "phone", "why": "optional text"}` / `{"on": false}`. Stops itself after a time limit.

### 3.7 Guard (anti-theft)

#### `POST /api/guard` 🔒

`{"on": true}` / `{"on": false}`. Arms after 60 s, then watches for tilt, engine start and GPS movement.
Refused while the engine is running. Response: `{"ok": true, "guard": {"on", "state", "arming_s", "last", "tilt_deg", "gps"}}`.

### 3.8 Van display shared settings

#### `GET /api/display` · `POST /api/display`

Settings shared by every screen. `rev` increases on each change, and `drev` in `/api/alerts`
mirrors it so screens know when to re-fetch. POST any subset:

| Field | Values |
|---|---|
| `brightness` | 0.1–1.0 |
| `night` | `auto`, `on`, `off` |
| `dim`, `offmode`, `status_led` | bool |
| `ambient` | `{on, cycle, warm, h (0–359), s (0–100), bright (10–100)}` |
| `alarm_clock` | `{on, at: "HH:MM", days: every\|weekdays\|weekends, warm: 0–120 min}` |
| `kitchen` | `{left: seconds}` to start the timer, `{end: 0}` to cancel, `{paused}`, `{set}` |
| `ticks` | Checklist items ticked (≤ 20 strings) |

`switches` is ignored here. Use `/api/switches`.
Response: `{"ok": true, "display": {...}, "wake": {...}}`.

### 3.9 Levelling

| Endpoint | |
|---|---|
| `GET /api/level` | Small: `{level: {roll, pitch, ok, calibrated, temp_c, age_ms, ...}, level_scale, views, van, refresh_ms}`. For fast polling on the level page. |
| `POST /api/level` 🔒 | `{"action": "calibrate" \| "clear" \| "clear_log"}` |
| `GET /api/level/log` | CSV download: `t,temp_c,roll,pitch,raw_roll,raw_pitch,ax,ay,az` |
| `GET /api/stalls` | When the hub's program was held up for more than 0.5 s, and by what: `{threshold_ms, stalls: [{t, ms, during: ["wifi scan", "hotspot start", ...], wifi, hotspot}]}`, the last 50, in memory. For finding what interrupts the level readings and Bluetooth (pico/stalls.py). |
| `GET /api/level/trip` | CSV download, streamed: the last 3000 level readings, about 15 minutes at about three a second, kept in memory - `t,ms,roll,pitch,g_total`. For looking at a drive afterwards (G-force). Lost on a restart. |

### 3.10 Weather

#### `GET /api/weather`

The cached Open-Meteo forecast. The hub fetches it so phones on the van hotspot don't need internet.

```json
{"ok": true,
 "meta": {"configured": true, "online": true, "source": "gps", "place": "Near <town>",
          "mode": "auto", "gps_fix": true, "from": "typed", "fixed_place": "Home",
          "lat": "<lat>", "lon": "<lon>", "fetched": 1790933399, "age_s": 65,
          "stale": false, "error": ""},
 "data": { /* Open-Meteo response, unmodified */ }}
```

- **`source`:** what the stored forecast is for: `gps` (the van's position) or `settings` (the fixed location).
- **`mode`:** the choice in Settings → Weather. `auto` uses the van's GPS position while it has a fix, else the fixed location; `fixed` always uses the fixed location.
- **`gps_fix`:** whether the GPS has a fresh fix now.
- **`from`:** how the fixed location was filled in: `typed`, `gps` (the van's GPS) or `device` (a phone or laptop's location). Empty for one saved before this was recorded.
- **`fixed_place`:** the fixed location's name.

### 3.11 Settings and administration 🔒

| Endpoint | |
|---|---|
| `GET /api/settings` | `{settings: {...}, alert_defs: [...]}`. Passwords and keys are never returned, only `*_set` flags. |
| `POST /api/settings` | Any subset of sections: `wifi`, `devices`, `location`, `alerts`, `display`, `checklist`, `switches`, `switch_icons`, `panel`, `auth`. A blank password means "keep the current one". Response includes `restart_required`. |
| | `wifi.networks`: `[{ssid, password, on}]`, in the order tried. `on: false` keeps a network and its password, but the hub doesn't join it. |
| | `location`: `lat`, `lon`, `name`, `source` (`auto` or `fixed`; `fixed` needs `lat` and `lon`), `from` (`typed`, `gps` or `device`). |
| `GET /api/wifi/networks` | Known networks **with passwords**, plus the hotspot, so the display can follow the hub. Networks switched off are left out. |
| `POST /api/scan/wifi` | Nearby networks: `[{ssid, rssi, secure}]` |
| `POST /api/scan/ble` | `{"ms": 2000–12000}` → nearby BLE devices |
| `POST /api/reboot` | Replies first, then restarts about 0.6 s later |

### 3.12 Pairing a device

This gives a device (normally the van display) its own key without typing the password on it.

1. The device shows a 6-digit code and polls `POST /api/pair/claim` with `{"code": "123456", "name": "display"}`.
   Until the code is approved it gets `{"ok": false, "waiting": true}`.
2. An authorised user approves it: `POST /api/pair/code` 🔒 `{"code": "123456"}`. The approval lasts 3 minutes, and 5 wrong claims cancel it.
3. The next claim returns `{"ok": true, "key": "<32 hex>"}`. Use it as `X-Token`.

---

## 4. Non-API paths

| Path | |
|---|---|
| `/` | The web app (`www/app.html`) |
| `/details`, `/heater`, `/weather`, `/settings` | Pages behind the app's Settings |
| `/app.css`, `/common.js`, `/manifest.json`, icons | Shared assets |
| `/camperlux.apk`, `/apk.json` | The Android app and its version, if installed on the hub |
| `/dispfw/<file>` | Signed display firmware for OTA (`manifest.json?v=<current>`) |

## 5. Remote access

The API is **LAN-only**. Don't port-forward the hub to the internet: it has no TLS.
For access from away, use something secured itself that reads the hub on the local
network, such as Home Assistant (below).

## 6. Example: Home Assistant

```yaml
rest:
  - resource: http://192.168.4.1/api/data   # or the hub's LAN address
    scan_interval: 30
    sensor:
      - name: Van battery SoC
        value_template: "{{ value_json.soc }}"
        unit_of_measurement: "%"
      - name: Van solar power
        value_template: "{{ value_json.renogy.solar_w }}"
        unit_of_measurement: "W"
      - name: Van load
        value_template: "{{ value_json.derived.load_w }}"
        unit_of_measurement: "W"
```

The source of truth is `pico/httpd.py` (routing, auth, limits) and `pico/main.py` (handlers).
If this document disagrees with the code, the code wins. Please update this file.
