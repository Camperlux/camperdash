# Licences and credits

## CamperDash

Copyright (c) 2026 Camperlux (https://camperlux.co.uk).

- **The software**, meaning everything in this repository except the documentation, photos, screenshots and brochure, is under the [PolyForm Noncommercial License 1.0.0](LICENSE.md).
  - You may use, change and share it for any **non-commercial** purpose: your own van, hobby builds, research, teaching, and charities.
  - **Commercial use is not allowed**, including selling the system, kits, installations or products built on it, or using it in a business. For a commercial licence, contact Camperlux through [camperlux.co.uk](https://camperlux.co.uk).
- **The documentation, photos, screenshots, schematics and brochure** (`docs/`, and the `README.md` files) are under [Creative Commons Attribution-NonCommercial-ShareAlike 4.0](LICENSE-docs.txt): share and adapt them non-commercially, credit Camperlux, and share changes under the same licence.

## Parts from others

Their own licences apply to these, not the ones above.

| Part | Where | Licence |
|---|---|---|
| Inter typeface (rendered into the display's fonts) | `tools/fonts/`, `display/fonts/` | SIL Open Font License 1.1 (`tools/fonts/Inter-LICENSE.txt`) |
| MicroPython (the hub and display firmware the code runs on) | built from [micropython/micropython](https://github.com/micropython/micropython); `firmware/` holds only our board definition | MIT |
| Leaflet (the map on the weather page, loaded from a CDN) | `static/weather.html` | BSD 2-Clause |
| Map tiles and place names | OpenStreetMap, Nominatim | Data © OpenStreetMap contributors, ODbL |
| Weather forecasts | Open-Meteo | Data CC BY 4.0 |
| Android libraries (AndroidX, Jetpack Compose and others) | `android_hub/`, `android/` | Apache 2.0 |

## Device protocols

The battery, charger and heater protocols were worked out, for interoperability, from live Bluetooth captures and, for the heater, from its maker's app. The decoders in `pico/` describe each byte and how it was confirmed. Fogstar, JBD, Renogy, Victron, JP and SolGP are trademarks of their owners. This project is not affiliated with them.
