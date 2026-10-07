# Requirements: CamperDash

**Status:** Draft 0.3, 3 October 2026. Written from the system as built, for the owner to review.

This document combines a **user requirements specification** (what the people
using the van need, in their terms) and a **system requirements specification**
(what the system must do, in terms that can be tested). Each system
requirement traces to the user need it serves and to how it is verified.

For a project of this size, one combined document is enough. Separate formal
URS and SRS documents, in the IEEE 29148 style, earn their keep when:
- the system is being **sold** or installed for others, so customers need to know what they're getting;
- **someone else builds or maintains** it, and needs a contract for what "done" means;
- it takes on a **safety function** (it controls a diesel burner and 12 V loads), where the requirements become the basis for a hazard analysis.

If any of those becomes true, split this document in two and add a hazard log.
Until then, keep this one current when features change.

**Verification methods:**
- **T:** Test, on the bench or in the van.
- **A:** Analysis or measurement, from logs or recorded data.
- **I:** Inspection of the design or code.
- **D:** Demonstration by use.

---

## 1. User requirements

| ID | The user needs to… |
|---|---|
| UR-1 | See the leisure battery's charge and how fast it is charging or discharging, at a glance |
| UR-2 | See where power is coming from (solar, alternator, mains) and going to |
| UR-3 | Turn the heater and hot water on and off, set the temperature, and schedule them |
| UR-4 | Switch the van's 12 V circuits (pump, lights, fridge…) from inside or outside the van |
| UR-5 | Know how to level the van on uneven ground |
| UR-6 | Be warned of problems: low battery, faults, driving off still plugged in, intrusion |
| UR-7 | Use it from a fixed screen, their phone (iPhone or Android), and the van's own dashboard screen |
| UR-9 | Have it work with no mobile signal and no internet |
| UR-10 | Know the weather and where the van is |
| UR-11 | Keep strangers from controlling the van |
| UR-12 | Not have it drain the battery or need regular attention |
| UR-13 | Small conveniences: clock, alarm, kitchen timer, a pre-drive checklist, games |
| UR-14 | Update it without taking it apart |

## 2. System requirements

### 2.1 Power and battery

| ID | Requirement | Traces to | Verify |
|---|---|---|---|
| SR-1 | The hub shall read the BMS's state of charge, pack voltage, current, cell voltages, temperatures and faults at least every 10 s while connected. | UR-1 | T |
| SR-2 | The power-flow view shall show solar, alternator (DC-DC), mains (Victron) and load power, with the direction of flow. | UR-2 | D |
| SR-3 | Storage mode shall hold the pack near a set charge for winter lay-up. | UR-12 | T |
| SR-4 | Switching a BMS MOSFET off shall need an explicit typed confirmation, and shall never be possible remotely. | UR-11 | I, T |

### 2.2 Heating

| ID | Requirement | Traces to | Verify |
|---|---|---|---|
| SR-10 | The hub shall start the heater in air, water, air + water, or fan-only mode, set an air target of 5–35 °C, a hot-water setting and a fan speed of 1–4, and stop it. | UR-3 | T |
| SR-11 | Every heater start or stop from a screen shall ask for confirmation first. | UR-3, UR-11 | D |
| SR-12 | The hub shall not communicate with the heater except to carry out a command a person or a timer asked for (`HEATER_POLL` off). | UR-12 | I |
| SR-13 | Timers shall start the heater for a morning warm-up and hot water, and frost protection shall start it below a set temperature. | UR-3 | T |
| SR-14 | "Auto" fuel shall select electric when mains hook-up is live, and diesel otherwise, decided at command time. | UR-3 | T |

### 2.3 Switches

| ID | Requirement | Traces to | Verify |
|---|---|---|---|
| SR-20 | The hub shall drive six relays; switch *n* drives relay *n*, with a name and icon set in Settings. | UR-4 | T |
| SR-21 | Relay states shall survive a restart of the hub. | UR-4 | T |
| SR-22 | A switch change shall show on every awake screen within 5 s (30 s on a dimmed display). | UR-4 | T |

### 2.4 Levelling and position

| ID | Requirement | Traces to | Verify |
|---|---|---|---|
| SR-30 | The hub shall report roll and pitch from the accelerometer to 0.1°, against a calibrated zero. | UR-5 | T |
| SR-31 | The Level page shall show, for each wheel, the height in cm to raise it to level the van. | UR-5 | D |
| SR-32 | Within the level tolerance (default 1°), the Level drawings shall show the van level. | UR-5 | D |
| SR-33 | A reading shall be marked "still" only when the reads behind it agree to within 0.03 g. | UR-5 | I |
| SR-34 | With a GPS fix, the hub shall set its clock, use the van's position for the forecast, and show the place name and a map. | UR-10 | T |
| SR-38 | The forecast location shall be either automatic (the van's GPS position while it has a fix, else a fixed location) or always the fixed location. Settings shall say which location the forecast is for and why, and how the fixed location was filled in. | UR-10 | T |
| SR-35 | A G-force view shall show forward, cornering and bump forces in g, with peaks, from the levelling calibration. Directions shall read correctly: accel/brake, left/right. | UR-5 | T (drive tests, 2 Oct 2026) |
| SR-36 | The hub shall keep at least the last 10 minutes of level readings and serve them for download, including readings taken out of Wi-Fi range. | UR-5 | T |
| SR-37 | A Tanks page shall show fresh and grey water levels, warning when fresh is low and grey is high. Sample levels shall be marked as such until sensors report. | UR-1 | D |

### 2.5 Alerts and security

| ID | Requirement | Traces to | Verify |
|---|---|---|---|
| SR-40 | Alerts (low battery, BMS faults, mains connected while the engine runs, guard, panic) shall show and sound on every screen, and on the hub's buzzer and LED. Acknowledging one on any screen shall silence it on all of them. | UR-6 | T |
| SR-41 | Guard mode shall raise an alarm on tilt, engine start, or GPS movement over 60 m, after a 60 s arming delay. | UR-6 | T |
| SR-42 | With a hub password set, every change shall require it, or a paired display, or an API token. | UR-11 | T |
| SR-44 | The hub's web server shall not be exposed to the internet. | UR-11 | I |

### 2.6 Screens and access

| ID | Requirement | Traces to | Verify |
|---|---|---|---|
| SR-50 | The same pages (Home, Power, Heater, Level, Switches, Drive, Settings) shall be served to any browser, including iPhone Safari, with no app needed. | UR-7 | D |
| SR-51 | The cabin display shall find the hub with no address configured, and follow it between networks. | UR-7, UR-9 | T |
| SR-52 | The Android app shall find the hub on the phone's Wi-Fi network or on the phone's own hotspot, remember its address per network, and update itself from the hub. | UR-7 | T |
| SR-53 | An Android Auto view shall show key state on the van's head unit. | UR-7 | D |
| SR-54 | Controls that set a value along a range (brightness, colour, heater dial) shall follow a finger dragged along them. | UR-7 | D |
| SR-55 | The cabin display and the web app shall offer the same pages and features. Only the games are display-only. | UR-7 | I |

### 2.7 Operation

| ID | Requirement | Traces to | Verify |
|---|---|---|---|
| SR-60 | Everything except the forecast, the map, place names shall work with no internet. The hub shall run its own hotspot when no known network is in range. | UR-9 | T |
| SR-65 | Each known network shall have an on/off switch. A network switched off shall keep its password, and shall not be joined by the hub or given to the cabin display. | UR-9 | T |
| SR-61 | The cabin display shall shut down when running on its own battery, and wake when power returns. | UR-12 | T |
| SR-62 | The display shall update over Wi-Fi from a signed build held on the hub, only when idle, and restore the previous version if the new one fails to start. | UR-14 | T (rollback proven 29 Sep 2026) |
| SR-63 | The hub shall be updated over USB, with a bytecode-compatibility and free-space check before anything is written. | UR-14 | I |

## 3. Open items

- **Hotspot address clash:** the hub's hotspot address clashes with home networks on 192.168.4.x. A firmware build setting the driver's default hotspot address would remove this ([HARDWARE.md](HARDWARE.md#networking)).
- **DS18B20 probe:** wired in the design, not yet implemented.
- **Tank level sensor:** researched, with hardware not yet chosen. A resistive sender to an ADC is the likely route with top-only access.
- **Relay ratings:** check them against each load as installed, and record them in [HARDWARE.md](HARDWARE.md).
- **Fan-only (vent) mode:** not yet tested on this heater.
- **G-force left readings:** two unexplained left sections in drive 2 ([gforce_tests/](gforce_tests/README.md)) still to be confirmed against the route.
- **Hold-ups when leaving Wi-Fi range:** the hotspot start-up wait no longer stops the hub, and hold-ups are now recorded (`/api/stalls`). To confirm on the next drive: no gap longer than about a second in the trip record. If one remains, the level sensor could move to the RP2350's second core.
- **Alternator figures:** the screens show the current drawn from the alternator (input), which at the charger's 50 A output limit is about 55–60 A. Label it "from alternator" and show the charger's "into battery" figure as well.
