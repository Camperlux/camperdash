# AI setup prompt

You've got a blank hub, plugged into a laptop with a USB-C cable. Copy the
prompt below into an AI coding assistant that can run commands on your laptop,
such as Claude Code, and it will walk you through getting CamperDash running,
one step at a time.

Fill in the lines in **[brackets]** first. It helps to keep
[SETUP_GUIDE.md](SETUP_GUIDE.md) and [HARDWARE.md](HARDWARE.md) open too.

---

```text
I'm setting up CamperDash, an open campervan monitoring and control system, on
my own van. The repository is cloned at: [path to the CamperLux-Smart-Van folder]

Hardware I have:
- Hub: Waveshare RP2350-Relay-6CH-W, plugged into this laptop by USB-C, blank
  (factory firmware).
- [Cabin display: Freenove FNK0104S ESP32-S3 4" - or "no display yet"]
- [Sensors fitted: MPU-6050 accelerometer / u-blox NEO-7M GPS / none yet]
- [Devices in my van: e.g. Fogstar battery (JBD BMS), Renogy DC-DC with BT-2,
  Victron Blue Smart IP22, JP/SolGP diesel combi heater - list yours]
- [Relay loads I want to switch: e.g. water pump, lights - and how big they are]
- My computer: [Windows / macOS / Linux]

Please work through this with me, one step at a time:

1. Read README.md, docs/SETUP_GUIDE.md, docs/DEVICES.md, docs/HARDWARE.md,
   docs/ARCHITECTURE.md and docs/HUB_API.md in the repository first, and follow them. They describe
   what has been proven on real hardware; prefer them to general knowledge.
2. Check my tools: Python 3.10+, mpremote, and mpy-cross 1.29.0.post2 (the
   version must match the hub's MicroPython 1.29). Install anything missing.
3. Find the hub's serial port. Tell me how to put it into bootloader mode, then
   get the hub firmware (GitHub Actions artifact "hub-firmware-v1.29.0", or
   build it from firmware/) and help me flash it. Confirm MicroPython and Wi-Fi
   start.
4. Create pico/config.py from pico/config.example.py with me: my Wi-Fi
   networks, a hotspot name and a strong password, and only the sensors I
   actually have. Keep AP_IP at 192.168.4.1. Never commit config.py, and never
   print my passwords back to me.
5. Build and deploy the hub software (tools/build_mpy.py, then
   tools/deploy_mpy.py - dry run first, then --go --with-config), and confirm it
   starts and shows its address.
6. Guide me through the hub's Settings page: set a password first, pair my
   Bluetooth devices, name the switches, and calibrate the levelling on flat
   ground.
7. If I have the display: flash MicroPython for the ESP32-S3, create
   display/config.py, and install it with tools/deploy_display.py. Explain the
   update key (.ota_key): I must back it up and never commit it.
8. Explain the wiring for my relay loads and sensors from docs/HARDWARE.md,
   including a fuse per circuit and checking the relay ratings. Point out
   anything that's unsafe or too big for the relays.

Rules:
- Ask before any step that writes to a device, deletes anything, or changes
  something on a network.
- Always do the deploy tools' dry run first, and show me what it will change.
- Don't guess device protocols or pin numbers: use what the repository
  documents, and tell me if something I have isn't covered.
- If something I own isn't supported yet (another heater, battery or charger),
  tell me. Then, if I want it, help me add an integration following "Adding a
  device" in docs/ARCHITECTURE.md: capture the protocol first, then write a
  small decoder, and test it on the bench before the van.
- The heater can be started from these pages, so security settings come first.
```

---

**Tips for whoever runs it**

- **One step at a time.** Let the assistant run the commands, but read what it's about to do before saying yes to anything that writes to the hub or the display.
- **If a step fails,** paste the whole error back to it. The tools print clear reasons, such as an ABI mismatch, not enough room, or a wrong port.
- **Back up two files:** `pico/config.py` and `.ota_key`. They're not in the repository, on purpose.
