# Melitta Barista Smart — Full BLE Integration + PWA + Lovelace Card

Hi everyone!

I'd like to share my project for controlling **Melitta Barista T Smart** and **Melitta Barista TS Smart** coffee machines from Home Assistant over Bluetooth Low Energy.

The project consists of three components:

## 1. Custom Integration (HACS)

**[melitta-barista-ha](https://github.com/dzerik/melitta-barista-ha)**

Full BLE integration with encrypted protocol support (AES/RC4), just like the official Melitta app.

**What it can do:**
- Real-time machine status, brewing progress, and required actions via BLE push notifications
- Brew any of the 24 built-in recipes with one tap
- Freestyle recipe builder — create custom drinks with two components, adjustable intensity, temperature, aroma, shots, and portion sizes
- DirectKey profiles — brew from personalized profile slots
- Machine settings — water hardness, brew temperature, auto-off, energy saving, rinsing, auto bean select
- Maintenance — easy clean, intensive clean, descaling, evaporating, water filter management
- Cup counters per recipe
- User profile names (read/write)
- Auto-discovery via BLE
- Works with local Bluetooth adapter or ESPHome BLE proxy (ESP32)
- 29 language translations
- 350+ tests, 89% coverage

## 2. Standalone PWA (Tablet / Kiosk)

**[melitta-barista-app](https://github.com/dzerik/melitta-barista-app)** — [Live Demo](https://dzerik.github.io/melitta-barista-app/)

A full-screen React progressive web app designed for wall-mounted tablets and kiosk displays. Connects to HA via WebSocket API.

Five tabs:

**Brew** — recipe grid with profile switching and quick-access buttons

![Brew](https://raw.githubusercontent.com/dzerik/melitta-barista-app/main/screenshots/brew.png)

**Freestyle** — custom drink builder with live glass visualization

![Freestyle](https://raw.githubusercontent.com/dzerik/melitta-barista-app/main/screenshots/freestyle.png)

**Stats** — cup counter dashboard with per-recipe statistics

![Stats](https://raw.githubusercontent.com/dzerik/melitta-barista-app/main/screenshots/stats.png)

**Service** — cleaning, descaling, water filter management

![Service](https://raw.githubusercontent.com/dzerik/melitta-barista-app/main/screenshots/service.png)

**Settings** — toggles and sliders for machine configuration

![Settings](https://raw.githubusercontent.com/dzerik/melitta-barista-app/main/screenshots/settings.png)

- Installable as PWA on any device
- Dark coffee-themed UI optimized for touch
- 3 languages (EN, RU, DE)
- No data sent to external servers — credentials stored only in browser localStorage

## 3. Custom Lovelace Card

**[melitta-barista-card](https://github.com/dzerik/melitta-barista-card)**

A full-featured Lovelace card with auto-detection, theme-aware styling, and all the same capabilities as the PWA — right inside your HA dashboard.

**Recipes** — DirectKey quick-access, user profiles, full recipe grid with SVG icons
![Recipes](https://raw.githubusercontent.com/dzerik/melitta-barista-card/main/images/recipes.png)

**Freestyle** — custom drink builder with two components
![Freestyle](https://raw.githubusercontent.com/dzerik/melitta-barista-card/main/images/freestyle.png)

**Stats** — cup counter and per-recipe statistics
![Stats](https://raw.githubusercontent.com/dzerik/melitta-barista-card/main/images/stats.png)

**Maintenance** — cleaning, descaling, water filter, power off
![Maintenance](https://raw.githubusercontent.com/dzerik/melitta-barista-card/main/images/maintenance.png)

**Settings** — toggles and sliders for machine configuration
![Settings](https://raw.githubusercontent.com/dzerik/melitta-barista-card/main/images/settings.png)

## Supported Models

| Model | Recipes | Bean Hoppers |
|-------|---------|--------------|
| Barista T Smart (F50/2-102) | 21 | 1 (single) |
| Barista TS Smart (F75/0-102) | 24 | 2 (dual) |

## Installation

All three components are installable via HACS as custom repositories:

1. **Integration**: HACS → Integrations → Custom repositories → `https://github.com/dzerik/melitta-barista-ha`
2. **Card**: HACS → Frontend → Custom repositories → `https://github.com/dzerik/melitta-barista-card`
3. **PWA**: Self-host the build or use the [hosted version](https://dzerik.github.io/melitta-barista-app/)

## How the BLE Protocol Works

The Melitta BLE protocol uses a custom GATT service (`0000ad00-b35c-11e4-9813-0002a5d5c51b`) with AES-encrypted frames. The integration handles pairing, authentication, command framing, and real-time status parsing — all locally, no cloud involved.

Works with:
- **Local Bluetooth adapter** (BlueZ + D-Bus Agent1 pairing)
- **ESPHome BLE proxy** (ESP32-C6/S3) — place it near the machine if HA host is too far

## Tech Stack

- **Integration**: Python, bleak, pycryptodome
- **PWA**: React 19, TypeScript, Vite 7, Tailwind CSS 4, home-assistant-js-websocket
- **Card**: Lit, TypeScript, Rollup

---

If you have a Melitta Barista Smart, give it a try! Feedback, bug reports, and contributions are welcome.

GitHub: [melitta-barista-ha](https://github.com/dzerik/melitta-barista-ha) | [melitta-barista-app](https://github.com/dzerik/melitta-barista-app) | [melitta-barista-card](https://github.com/dzerik/melitta-barista-card)
