# Emulator Changelog

**Independent from the HA integration's `manifest.json` version.**
The emulator under `esp_emulator/` evolves at its own cadence — bug
fixes, new brand-emulation coverage, and protocol-fidelity work happen
here without touching the integration's release cycle, and vice versa.
Emulator releases are tagged `emu-v<MAJOR>.<MINOR>.<PATCH>`.

## [0.3.0] — 2026-04-14 — Phase C-lite: per-family brew recipes + multi-stage ramp

Second slice of the Nivona full-emulation roadmap (see
[`../docs/NIVONA_RE_NOTES.md`](../docs/NIVONA_RE_NOTES.md) §Phase
C-lite). HE brew is now family- and recipe-aware: unknown selectors
are rejected with NACK, and milk-capable recipes walk a proper
GRINDING → COFFEE → STEAM sequence via `sub_process` transitions
instead of a single flat ramp.

### Added

- `nivona_families.{h,c}` gained full per-family recipe tables
  (RECIPES_600 / 700 / 79X / 900 / 1030 / 1040 / 8000), mirroring
  `custom_components/melitta_barista/brands/nivona.py::_RECIPES_*`
  so the C and Python sides stay in sync.
- `nivona_recipe_category_t` enum (ESPRESSO / COFFEE / AMERICANO /
  MILK_DRINK / MILK_ONLY / WATER) and per-category brew ramps
  (stage list + total wall-clock).
- `nivona_family_recipe_by_selector()` — lookup helper used by both
  brew start (resolve category) and HE handler (validate selector).

### Changed

- **`nivona_brew_start(selector)`** now resolves the selector against
  the active family's recipe table. Unknown selector → returns `false`,
  which causes the HE dispatcher to reply NACK. Previously any
  selector byte was silently accepted.
- **`brew_task`** walks recipe-category stages, setting `sub_process`
  per stage:
  - ESPRESSO / COFFEE / milk-less: `GRINDING → COFFEE`
  - AMERICANO: `GRINDING → COFFEE → WATER`
  - MILK_DRINK: `GRINDING → COFFEE → STEAM`
  - MILK_ONLY: `STEAM` only
  - WATER: `WATER` only

  Progress percentage (0–100) is linear across the whole brew.
  Durations are heuristic (espresso 20s, milk drink 45s, etc.) and
  await verification against a real-machine BLE trace.
- **Dispatch `handle_he`** replies NACK when `nivona_brew_start`
  refuses the request (unknown selector / brew in progress).

### Requires

- HA integration `v0.46.0+` (brand-aware HX parsing for `sub_process`
  transitions to render correctly on non-8000 families).

### Binary

ESP32-C6 build clean, +1.8 KB over emu-v0.2.0 (still 13% partition
headroom).

---

## [0.2.0] — 2026-04-14 — Phase A: per-family FSM process codes

First slice of the full Nivona emulation roadmap (see
[`../docs/NIVONA_RE_NOTES.md`](../docs/NIVONA_RE_NOTES.md)).

### Added

- `main/nivona_families.{h,c}` — canonical per-family lookup table
  covering 8 known Nivona families (600/700/79x/900/900-light/1030/
  1040/8000) with `ble_name`, `model`, `process_ready`,
  `process_brewing`, `fluid_scale`, and `has_milk_system` fields.
- `nivona_fsm_reset_to_ready()` — retargets the FSM to the active
  family's READY code without requiring a reboot.

### Changed

- `nivona_fsm_init` reads from the active family entry instead of
  hardcoding `process = 3`.
- `nivona_brew_task` snapshots the current family at brew start and
  uses its `{process_brewing, process_ready}` codes for the ramp.
- CLI `family <key>` command now also calls `nivona_family_set` +
  `nivona_fsm_reset_to_ready` — previously only ADV / DIS were
  updated, FSM codes stayed locked to NIVO 8000 regardless of
  selected family.

### Fixed

- Emulator was functionally broken for any family other than `8000`.
  After `family 700` the ADV identified NICR 759 but HX kept
  returning `process = 3` (NIVO 8000 READY). With HA v0.46.0+
  brand-aware parsing expecting `8` for non-8000 Nivona families,
  status sensors would render as "unknown".

---

## [0.1.0] — 2026-04-13 — Initial emulator (bundled via HA integration v0.44.0)

First working Nivona BLE emulator:

- Byte-exact advertisement matching a real machine (company ID
  `0x0319`, Eugster customer payload, AD00 service, DIS in SR).
- GATT: AD00 (AD01 write, AD02 notify) + DIS (0x180A) with
  manufacturer/model/serial.
- Full Eugster/EFLibrary encrypted protocol: frame parser, RC4 stream
  cipher, AES customer-key bootstrap, HU handshake with per-brand
  verifier, HR/HW/HX/HE/HA/HB opcodes.
- HX FSM (hardcoded NIVO 8000 codes at this stage), HE brew ramp,
  unsolicited HX notifications during brew.
- CLI-driven family switch (ADV/DIS only — FSM codes fixed until
  0.2.0).
- WiFi CLI + telnet + OTA.
- Targets: ESP32-C6 (primary) and ESP32-S3.

Shipped alongside HA integration releases 0.44.0 → 0.48.1 as an
in-tree asset; from 0.2.0 onwards the emulator has its own tag series.
