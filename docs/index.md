---
hide:
  - navigation
---

# Melitta Barista & Nivona — Home Assistant Integration

A custom Home Assistant integration for controlling **Melitta Barista T/TS Smart** and **Nivona NICR 6xx / 7xx / 79x / 9xx / 1030 / 1040** plus **NIVO 8xxx** coffee machines over Bluetooth Low Energy (BLE). Both brands are built on the shared Eugster/Frismag OEM stack, so a single integration drives either.

!!! warning "Nivona testers wanted"
    Nivona support is shipped as **alpha** — cryptography and handshake are validated against upstream RE vectors, and **NICR 930** is now confirmed working on real hardware (firmware `0254A013A10`, [PR #7](https://github.com/dzerik/melitta-barista-ha/pull/7)). Other Nivona families have not yet been live-tested by the maintainer. If you own one, please [open an issue](https://github.com/dzerik/melitta-barista-ha/issues/new) with your results.

## Quick links

- :material-github: [Source on GitHub](https://github.com/dzerik/melitta-barista-ha) — full README, screenshots, automation examples, installation
- :material-package-variant: [HACS installation](https://github.com/dzerik/melitta-barista-ha#installation)
- :material-bug: [Issue tracker](https://github.com/dzerik/melitta-barista-ha/issues)
- :material-text-box: [Changelog](changelog.md)

## What's documented here

- **[BLE architecture](BLE_ARCHITECTURE.md)** — connection lifecycle, GATT layout, reconnect strategy, two transport modes (local BlueZ vs ESPHome BLE proxy)
- **[Wire protocol](PROTOCOL.md)** — frame format, opcodes (HU/HV/HR/HW/HX/HE/HZ/HY/HD/HI/HA/HB), AES + RC4 crypto details
- **[ADR-001: Brand profile abstraction](adr/001-brand-profile-abstraction.md)** — the design decision behind multi-brand support (Melitta + Nivona on a shared core)

## Supported

| Brand   | Models                                                            | Status                                |
|---------|-------------------------------------------------------------------|---------------------------------------|
| Melitta | Barista T, Barista T Smart, Barista TS Smart                      | stable                                |
| Nivona  | NICR 6xx / 7xx / 79x / 9xx / 1030 / 1040, NIVO 8xxx               | alpha (NICR 930 validated on hardware)|

For the full feature matrix, supported recipes table, screenshots, and HA automation examples, see the **[GitHub README](https://github.com/dzerik/melitta-barista-ha#readme)**.
