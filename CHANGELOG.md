# Changelog

All notable changes to the Melitta Barista Smart & Nivona HA Integration.

## [0.48.1] — 2026-04-14 — Emulator Phase A: per-family FSM process codes

Emulator-only release — no changes to the HA integration itself.

### Added

- **`esp_emulator/main/nivona_families.{h,c}`** — canonical per-family
  lookup table (`600`/`700`/`79x`/`900`/`900-light`/`1030`/`1040`/`8000`)
  centralising the values the FSM needs in order to emulate different
  Nivona machines convincingly. Fields: `ble_name`, `model`,
  `process_ready`, `process_brewing`, `fluid_scale`, `has_milk_system`.
- **`docs/NIVONA_RE_NOTES.md`** — living scratch-pad for per-family
  reverse-engineering findings (Phases A→H). Sources every fact to a
  specific line of the decompiled `EugsterMobileApp` (v3.8.6).

### Changed (emulator)

- **`nivona_fsm_init`** now reads `process_ready` from the active
  family entry instead of hardcoding `3`. New
  `nivona_fsm_reset_to_ready()` retargets the FSM live when the CLI
  switches family — no reboot needed for the status codes.
- **`nivona_brew_task`** snapshots the current family at brew start
  and uses `{process_brewing, process_ready}` from the table — so
  on a `family 700` emulator, HX reads now return `8` (READY) / `11`
  (PRODUCT) as expected by the HA integration (v0.46.0+ brand-aware
  HX parsing) and by the official Android app's `MakeCoffee` switch.
- **CLI `family <key>`** now also calls `nivona_family_set()` +
  `nivona_fsm_reset_to_ready()` so the very next HX read reflects
  the switch. Previously only ADV / DIS were updated.

### Fixed

- **Emulator was broken for any family other than `8000`.** After
  `family 700` the ADV said NICR 759 but the FSM kept emitting
  `process=3` (NIVO 8000 READY code) — HA would see the status as
  "unknown" because the brand-aware parser expects `8` for
  non-8000 Nivona families.

## [0.48.0] — 2026-04-14 — Show brand & model at discovery time

### Added

- **Discovery picker now shows brand + model**, not just the raw
  advertisement local_name. Instead of `"8107000001----- (MAC)"` you
  see `"Nivona NICR 8107 · 8107000001----- · MAC"` — resolved at
  advertisement time (no BLE connect required) via the new
  `_describe_advertisement()` helper.
- **Bluetooth-confirm + pair forms** list the resolved brand, model,
  raw advertisement name, and MAC before you commit to pairing, so
  a misdetection is caught *before* the config entry is created.
- **Config-entry title and ``CONF_NAME`` default to the
  brand + model** (e.g. ``"Melitta Barista TS Smart"`` /
  ``"Nivona NICR 8107"``) instead of the raw advertisement name.
  The device shows up in Home Assistant's device registry under the
  friendly name straight away — no manual rename required.

### Changed

- **strings.json** bluetooth_confirm / pair descriptions gained
  ``{brand}`` / ``{model}`` / ``{address}`` placeholders alongside
  ``{name}``; all **29 translation files** updated with native-language
  labels (Marke/Modell, Marque/Modèle, Марка/Модель, Μάρκα/Μοντέλο,
  Zīmols/Modelis, …) — `tr`, `sv`, `el`, etc. all localised.
- **Direct-scan fallback in `_async_discover_devices`** also matches
  `"nivona"` substring and delegates to `detect_from_advertisement` so
  discovery picks up both brands uniformly.

## [0.47.2] — 2026-04-14 — Fix Nivona brand detection for bare-serial advertisements

### Fixed

- **Emulator and real Nivona machines were being misdetected as
  Melitta.** The Nivona `ble_name_regex` still required the legacy
  `"NIVONA-"` prefix (`^NIVONA-\d{10}-----$`), but real machines (and
  therefore the emulator, as of v0.45.0) advertise the bare serial
  form `"8107000001-----"` so the official Nivona Android app can
  derive the model code via `Substring(0, 4)`. The regex never
  matched, `detect_from_advertisement` returned None, and
  `MelittaProfile` (the default) was picked — entities appeared
  under "Melitta" manufacturer and process-code parsing fell back to
  Melitta's 2/4 table.
- Regex now accepts both forms: `^(?:NIVONA-)?\d{10}-----$`. Trailing
  5-dash suffix remains the distinguisher from Melitta's
  `8xxx + hex` advertisement.
- Direct-scan fallback in `config_flow._async_discover_devices` now
  also delegates to `detect_from_advertisement` (in addition to the
  legacy Melitta substring checks) and matches `"nivona"` in the
  BLE name.

## [0.47.1] — 2026-04-14 — Highlight the ESP32 BLE emulator in README

- Added a Features bullet and a dedicated `## ESP32 BLE Emulator
  (unique)` section in README.md describing the bundled ESP-IDF
  firmware (`esp_emulator/`) that impersonates a real Nivona machine
  at the BLE layer — byte-exact ADV, AD00 GATT, full Eugster/EFLibrary
  encrypted protocol, HU handshake, HX FSM, HE brew ramp. Discovered
  and controlled by HA **and** the official Nivona Android app, so
  the whole pair → discover → brew flow works without physical
  hardware.

## [0.47.0] — 2026-04-14 — Brand-neutral UI, docs, and legal notices

Comprehensive de-branding sweep — no more "Melitta Barista" strings shown
to Nivona users, no legal disclaimers that forget Nivona / Eugster, and
no stale module docstrings that claim Melitta-only scope when the code
handles both brands.

### Changed — user-facing strings

- **Config-flow titles, descriptions, and placeholders** are now
  brand-neutral ("Coffee Machine Setup", "Select your coffee
  machine…") across `strings.json` and all **29 translation files**
  (`bg/bs/cs/da/de/el/en/es/et/fi/fr/hr/hu/it/lt/lv/mk/nb/nl/pl/pt/ro/
  ru/sk/sl/sr/sv/tr/uk`). Each translation uses its native term for
  "coffee machine" (Kaffeemaschine, Machine à café, Кофемашина, …)
  rather than the English literal.
- **Entity-name fallbacks** in `config_flow.py`, `button.py`,
  `sensor.py`, `switch.py`, `text.py`, `select.py`, `number.py`,
  `binary_sensor.py` now derive the default from the active
  `BrandProfile.brand_name` (e.g. `"Melitta Coffee Machine"`,
  `"Nivona Coffee Machine"`) instead of the hardcoded
  `"Melitta Barista"` literal.
- **`model_name`** (used by `DeviceInfo.model`) falls back to
  `f"{brand_name} Coffee Machine"` when no DIS / legacy model-table
  hit is available, rather than `"Melitta Barista"`.
- **AI sommelier prompt** ("You are an expert barista…") describes
  the target as "a bean-to-cup smart coffee machine" rather than
  "a Melitta Barista Smart".
- **`conversation`-facing error messages** ("No coffee machine
  available") and WebSocket sommelier API errors no longer mention
  a specific brand.
- **Log lines** — `"Connecting to Melitta at …"` →
  `"Connecting to {brand_name} machine at …"`.

### Changed — docstrings and module headers

- Module-level docstrings in `__init__.py`, `ble_client.py`,
  `protocol.py`, `config_flow.py`, `diagnostics.py`, `entity.py`,
  `sensor.py`, `switch.py`, `number.py`, `binary_sensor.py`,
  `button.py`, `select.py`, `text.py`, `_ble_commands.py`,
  `_ble_recipes.py`, `_ble_settings.py` now describe their actual
  scope (coffee-machine entities / Eugster protocol / multi-brand)
  rather than claiming Melitta Barista only.

### Changed — documentation, metadata, legal

- **`NOTICE`** now carries full trademark disclaimers for
  **Melitta Group Management GmbH & Co. KG**, **Nivona Apparate
  GmbH**, and **Eugster/Frismag AG** (OEM). Previously only Melitta
  was disclaimed.
- **`README.md` Disclaimer** mirrors the NOTICE file and names all
  three trademark holders.
- **`README.md` Requirements** section lists both Melitta Barista
  T/TS Smart (stable) and Nivona NICR/NIVO 8xxx (alpha) as
  supported machines.
- **`README.md` installation / UI paths** reference
  `"Melitta Barista Smart & Nivona"` (matching the manifest `name`)
  instead of the legacy `"Melitta Barista Smart"`.
- **`README.md` Known Limitations** single-BLE-connection note
  covers both the Melitta Connect and Nivona App.
- **`hacs.json`** `name` synced with `manifest.json` (adds `& Nivona`).
- **`CHANGELOG.md`** header updated to multi-brand scope.
- **`docs/PROTOCOL.md`** retitled to reflect the shared
  Eugster/EFLibrary OEM protocol rather than Melitta-only.
- **`docs/BLE_ARCHITECTURE.md`** subtitle updated for multi-brand
  scope.

### Unchanged

- On-device entity identity, unique IDs, storage keys, and service
  payloads are untouched — this release is purely cosmetic /
  descriptive. Existing installations see new labels after restart;
  no reconfiguration required.

## [0.46.0] — 2026-04-14 — Brand-aware HX status parsing

### Fixed

- **Nivona machines no longer report "unknown" state.** `MachineStatus.
  from_payload` used a hardcoded Melitta `MachineProcess` enum (READY=2,
  PRODUCT=4), so raw process codes from Nivona firmware (NIVO 8000 uses
  3/4, other Nivona families use 8/11) fell through to `process=None`
  and the whole integration looked idle / never-ready. Surfaced while
  the official Nivona Android app refused to start brewing against the
  emulator with "machine not ready" — app-side tables
  (`EugsterMobileApp.MakeCoffee`) expected family-specific codes.

### Changed

- `BrandProfile` Protocol gained a `parse_status(family_key, data)`
  method. `MelittaProfile` delegates to the canonical
  `MachineStatus.from_payload` (Melitta-native codes); `NivonaProfile`
  overrides with per-family tables — `8000 → {3:READY, 4:PRODUCT}`,
  other Nivona families → `{8:READY, 11:PRODUCT}`.
- `EugsterProtocol` now tracks the detected family (`set_family`) and
  routes every HX parse through `brand.parse_status(family, payload)`.
  `MelittaBleClient` pushes the family key immediately after
  `_resolve_capabilities()`.

## [0.45.0] — 2026-04-14 — Nivona emulator app compatibility

Completes the BLE emulator so the official Nivona Android app discovers,
connects to, and operates it exactly like a real machine.

### Fixed (emulator)

- **Advertisement format now byte-exact to a real machine.** Company ID
  switched from the wrong 7425 to **0x0319 (Melitta)**, manufacturer
  payload is `ff ff 00 00 00 00` (customerId=65535 LE + vendor tail),
  and DIS (0x180A) is advertised in the scan response so the app can
  see the device class during scan.
- **BLE name no longer prefixed with `NIVONA-`.** The official app
  treats `Peripheral.Name` as the serial number — it strips trailing
  dashes and takes `Substring(0, 4)` to derive the model code
  ("8107" → NICR 8107). A `NIVONA-` prefix made the substring resolve
  to `"NIVO"`, no family matched, and the app silently skipped us
  (EugsterMobileApp:7381 + Droid:28319).
- **Primary-ADV 31-byte budget respected.** Moved the 16-bit DIS UUID
  from primary to scan response; primary keeps flags + AD00 + mfr data
  = 31 bytes exact.
- **NimBLE stack overflow on HE brew.** `nivona_frame` local buffers
  (`plain`/`cs_in`/`frame`) promoted to `static` and NimBLE host task
  stack raised to 8 KB — previously the emulator silently reset on
  valid HE frames because 1.5 KB of stack buffers collided with the
  4 KB default host task size.
- **Per-cmd size gating in the frame parser.** A spurious `0x45`
  byte in an encrypted HE payload was triggering a premature
  `FRAME_END`. The parser now looks up the expected request size
  (HE=25, HU=11, HX=7, …) per cmd and only completes frames at the
  exact byte count.

## [0.44.0] — 2026-04-14 — Nivona brew + BLE emulator

Adds brewing UI for Nivona (no HC/HJ needed — uses HE with per-family
recipe layouts) and introduces a standalone ESP32 firmware that
impersonates a Nivona machine for offline integration development.

### Added

- **Nivona brew button + recipe select** — `select.<name>_recipe`
  exposes the per-family `_RECIPES_*` drink list (Espresso, Coffee,
  Americano, Cappuccino, Caffè Latte, Latte Macchiato, Milk, Hot
  Water on 8xxx); `button.<name>_brew` submits the choice via HE
  with the family-correct `brew_command_mode` (0x04 for NIVO 8000,
  0x0B for NICR).
- **Nivona brew overrides as persistent `number` entities** —
  `<name>_brew_strength` (1–5), `<name>_brew_coffee_amount` (20–240 mL),
  `<name>_brew_temperature_preset` (0/1/2), `<name>_brew_milk_amount`
  (0–240 mL). Values survive restarts via `RestoreEntity` and are
  written via HW into per-family temporary-recipe registers
  (`10000 + recipe_id * 100 + field_offset`) right before HE —
  mirrors the `SendTemporaryRecipe()` flow in the Android app.
- **`BrandProfile.temp_recipe_register(family, recipe_id, field)`** helper
  and `fluid_write_scale()` accessor on `NivonaProfile`, reading from the
  existing `_STANDARD_RECIPE_LAYOUTS` tables.
- **`EugsterProtocol.start_process_nivona(selector, mode)`** — Nivona-
  specific 18-byte HE payload (`byte[1]=mode, byte[3]=selector,
  byte[5]=0x01`) distinct from the Melitta `start_process()` layout.
- **`esp_emulator/`** — ESP32 firmware that acts as a Nivona BLE
  peripheral for development. Implements HU handshake, RC4 framing,
  all documented H* commands, per-family recipe layouts, and a brew
  FSM. Exposes HTTP OTA, telnet CLI, mDNS, and diagnostic counters.
  Tested against a Seeed XIAO ESP32-C6 + BlueZ and Seeed XIAO ESP32-S3
  ESPHome BLE proxy + Home Assistant. Python test suite in
  `esp_emulator/tests/`.

### Fixed

- **`ble_client.brew_nivona()`** accepts an optional `overrides` dict
  to apply HW writes before HE — previously only the bare
  HE-with-defaults path existed.

## [0.43.0] — 2026-04-14 — Nivona gaps 1-6 closed

Closes the six remaining Nivona-support gaps from the upstream RE port:
entity wiring for settings/stats descriptors, DIS service reads at
connect, family-override in Options Flow, experimental recipe-write
path, and experimental MyCoffee-slot write path. The
manufacturer_data advertisement matcher (gap 4) is documented as
deferred pending real Nivona adv captures.

### Added

- **Generic `BrandSettingSelect`** driven by `SettingDescriptor` tuples
  from the active brand's `MachineCapabilities`. Reads via HR, writes
  via HW. For Nivona, instantiated for every setting in the per-family
  table (up to 10 entries on 1040). Melitta continues to use its
  hand-tailored setting entities.
- **Generic `BrandStatSensor`** driven by `StatDescriptor` tuples.
  Per-recipe cup counters, maintenance counters, and
  percentage/flag gauges for Nivona 700/79x/8000 families. Up to 27
  new diagnostic sensors on NIVO 8xxx.
- **Device Information Service (0x180A) read at connect**: Manufacturer
  / Model / Serial / HW / FW / SW revision strings. Used to refine
  capability detection via serial-prefix cascade AND to populate HA
  Device Registry with precise model information (no longer generic
  "Nivona Barista").
- **`BleCoffeeClient.capabilities` property** exposes the resolved
  `MachineCapabilities` (family-level + per-model overrides).
- **`BleCoffeeClient.dis_info` property** exposes the DIS snapshot.
- **Options Flow family override** (`family_override`, Basic Settings):
  dropdown of the active brand's family keys. Empty = auto-detect.
  Unblocks future / misdetected models without waiting for a release.
- **Experimental write-path services** for Nivona:
  - `melitta_barista.nivona_write_recipe_param` — write a single byte
    of a standard recipe slot via HW. 14 supported param keys:
    strength / profile / two_cups / temperature (+ per-fluid temps on
    900 family) / overall_temperature / coffee_amount /
    water_amount / milk_amount / milk_foam_amount / preparation.
  - `melitta_barista.nivona_write_mycoffee_param` — write a single
    byte of a MyCoffee user slot. Additional param keys: enabled, icon.
  - Both services marked EXPERIMENTAL in description — offsets are
    ported from upstream RE but have NOT been validated on real Nivona
    hardware; writes persist. Use at your own risk.
- **`RecipeFieldLayout` dataclass** in `brands/base.py` with all 14
  per-family byte offsets.
- **Per-family standard-recipe and MyCoffee layouts** in
  `brands/nivona.py` covering all 8 Nivona families. Fluid writes on
  900-family families multiplied ×10 per upstream quirk.
- **`NivonaProfile.standard_recipe_layout`, `.mycoffee_layout`,
  `.standard_recipe_register`, `.mycoffee_register`** helper methods.
- **`write_standard_recipe_param` / `write_mycoffee_param`** client
  mixin methods (brand-gated, graceful False on missing layout).

### Changed

- `BleCoffeeClient.model_name` now prefers resolved
  `capabilities.model_name`, falling back to DIS model string, then to
  legacy `MACHINE_MODEL_NAMES`.
- `MelittaDeviceMixin.device_info.model` now reflects the precise
  per-model name for Nivona entries (e.g. "NICR 756", "NICR 1040",
  "NIVO 8101" instead of generic "Nivona NICR 7xx").

### Deferred (documented)

- **Gap #4 — manufacturer_data advertisement matcher**: upstream's
  `CheckDiscovered` inspects a non-standard adv structure `0x0D` with
  Eugster-proprietary `customerId=65535`. That structure has no clean
  mapping to HA's `BluetoothMatcher` schema, and reconstructing the
  exact byte layout without real Nivona adv captures is unreliable.
  `local_name` regex continues to cover all standard advertisements.
  A manufacturer_data-based secondary matcher can be added once a
  real capture is available.

### Tests

- 692 → 703 (+11).
- New: recipe layout validation per-family (8 families × 14 offsets),
  MyCoffee layout validation, register calculation (10000+ and
  20000+), write_standard_recipe_param / write_mycoffee_param happy
  path + slot-bounds / family-gating edge cases.

## [0.42.0] — 2026-04-14 — Nivona data-completeness

Completes the port of Nivona-specific data from upstream
[mpapierski/esp-coffee-bridge](https://github.com/mpapierski/esp-coffee-bridge)
`src/nivona.cpp`. Crypto + recipe lists landed in 0.40.0/0.41.0;
this release ports per-family **settings register descriptors** and
**stats register descriptors**, plus per-model capability overrides
that are needed for correct MyCoffee slot counts.

### Added

- **Per-family settings tables** (`SettingDescriptor` tuples) with
  4–10 entries per family covering water hardness, off-rinse, auto-off,
  temperature, profile, and per-fluid temperatures (1030/1040). All
  option enums (HARDNESS / AUTO_OFF / TEMPERATURE / PROFILE /
  MILK_TEMPERATURE / MILK_FOAM_TEMPERATURE / POWER_ON_FROTHER_TIME) are
  ported verbatim from upstream with value-code → label mapping.
- **Per-family stats tables** for families with `supports_stats=True`:
  27 counters on 8000, 25 on 700, 10 on 79x. Includes per-recipe cup
  counters, maintenance counters (clean/descale/rinse/filter), and
  percentage/flag registers for descale/brew-unit-clean/frother-clean/
  filter progress + warnings.
- **`NivonaProfile.capabilities_for_model(ble_name, dis)`** — per-model
  refinement using upstream `MODEL_RULES`. Returns a
  `MachineCapabilities` with correct `my_coffee_slots` and
  `strength_levels` per individual model code (e.g. NICR 788 = 5 slots
  vs 756 = 1 slot; NICR 1040 = 18 slots vs 920 = 9 slots).
- Recipe/MyCoffee register base constants for future recipe-write
  support: `RECIPE_BASE_REGISTER = 10000`, `MY_COFFEE_BASE_REGISTER =
  20000`, both with `stride = 100`.
- Fixed `_PREFIX_TO_FAMILY` mapping for NICR 1030/1040: serial prefix
  is actually `"030"` / `"040"` per upstream, not `"1030"` / `"1040"`.

### Tests

- 688 → 692 (+6 Nivona coverage tests).
- New tests: per-family settings count, per-family stats count, per-
  model capability overrides (10 model codes covering all 8 families),
  unknown model returns `None`.

### Gaps deliberately not closed

The following items remain `TODO` for future Nivona work:

- **HN Flying Picture** — upstream itself does not implement it; only
  the HI feature bit is known.
- **Standard-recipe layout offsets** (per-family byte positions for
  strength/profile/temperature in the HE payload) — data ported as
  register-base constants, but the full `resolveStandardRecipeLayout`
  write path is not wired through BleCoffeeClient yet. Requires live
  Nivona hardware to validate HW byte-by-byte writes.
- **Advertisement manufacturer_data customerId** — optional secondary
  discovery matcher; local_name regex already works for standard
  Nivona advertisements.
- **DIS-service reads (0x180A)** — would populate device registry
  with precise Manufacturer/Model/Serial/FW at connect time. Currently
  we rely on BLE advertisement local_name only.
- **HE factory-reset opcodes (0x0032/0x0033)** — destructive, user
  explicitly deferred.
- **Chilled add-ons (NICR 8xxx)** — upstream itself does not
  implement; requires fresh APK RE.

These are documented in the project's internal roadmap and remain
parity with upstream esp-coffee-bridge as of 2026-04-14.

## [0.41.0] — 2026-04-13 — Nivona support (alpha)

First public release with **Nivona NICR / NIVO 8xxx** machines as a
supported brand alongside Melitta. Ships the Nivona profile that has
been in the codebase since 0.40.0 but inactive, plus polish for proper
multi-brand device-registry rendering.

### Added (Nivona-specific)

- `NivonaProfile` is now active in the BrandRegistry and advertised via
  `bluetooth: local_name: "NIVONA-*"` in `manifest.json`. Home Assistant
  will auto-discover Nivona machines and offer to set them up.
- Seven family capability entries (`600`, `700`, `79x`, `900`,
  `900-light`, `1030`, `1040`, `8000`) with per-family brew command
  mode, MyCoffee slot count, strength levels, and aroma-balance flag.
- Nivona-specific HU verifier with the upstream 256-byte S-box and
  `+0x5D`/`+0xA7` fold offsets — independently validated against the
  published `seed FA 48 D1 7B → verifier 7E 6E` vector.
- Runtime RC4 stream key `NIV_060616_V10_1*9#3!4$6+4res-?3` (recovered
  from `de.nivona.mobileapp` 3.8.6 in upstream RE).

### Changed

- `MelittaDeviceMixin` now renders `manufacturer` from the active brand
  profile instead of hard-coded `"Melitta"` — Nivona entries show up
  correctly as `Nivona` in the HA Device Registry.

### Known limitations / not in this release

- **Alpha status**: this release has not yet been validated on real
  Nivona hardware by the maintainer. The crypto + handshake
  implementations match the upstream reference against published test
  vectors, but live BLE interop (pair, handshake, brew) is unverified.
  Please report via GitHub issue if you own a NICR / NIVO machine.
- **No recipe editing**: Nivona firmware does not expose `HC`/`HJ`
  recipe read/write opcodes, so the Recipe Select, Freestyle builder,
  and Profile Activity switches do not appear on Nivona entries. Only
  maintenance actions, HY prompt confirmation, HD reset, and settings
  (HR/HW) are available.
- **Cup counters**: Nivona 700+ families expose stats via different
  register IDs than Melitta. Currently the `Total Cups` sensor shows
  `unknown` on Nivona; family-specific stats entities are planned for
  a future release.

## [0.40.0] — 2026-04-13 — Multi-brand refactor

Internal architecture refactor introducing pluggable **BrandProfile**
abstraction, preparing the integration for adding Nivona (0.41.0) and
potentially other OEM Eugster/EFLibrary-family brands later. **No
user-visible changes for existing Melitta Barista users.**

### Added
- `custom_components/melitta_barista/brands/` package:
  - `base.py` — `BrandProfile` Protocol + `MachineCapabilities` /
    `RecipeDescriptor` / `SettingDescriptor` / `StatDescriptor` PODs
    + `FeatureNotSupported` exception.
  - `melitta.py` — `MelittaProfile` hosting Melitta-specific crypto
    (RC4 key, HU CRC table, verifier algorithm), advertisement regex
    (`8301/8311/8401/8501/8601/8604`), 2 family capability entries
    (`barista_t`, `barista_ts`), supported extensions `{"HC", "HJ"}`.
  - `nivona.py` — `NivonaProfile` (alpha — code-complete, untested on
    real hardware; see 0.41.0).
  - `__init__.py` — `BrandRegistry` with `get_profile`,
    `all_profiles`, `detect_from_advertisement`.
- `docs/adr/001-brand-profile-abstraction.md` — architectural decision
  record (4 alternatives considered).
- 21 new brand-profile unit tests (including a Nivona HU verifier
  vector guaranteed to match upstream RE).

### Changed
- `MelittaProtocol` → `EugsterProtocol(brand=...)` (brand-agnostic
  Eugster/EFLibrary core). `MelittaProtocol` retained as backward-compat
  alias — all existing imports continue to work.
- `MelittaBleClient` accepts `brand: BrandProfile | None` kwarg; all
  crypto is delegated to the active profile.
- `HC` / `HJ` opcodes (recipe read/write) now gated on
  `brand.supported_extensions` — future Nivona clients will not try to
  issue commands the firmware doesn't understand.
- Entity registration (`button.py`, `select.py`, `text.py`,
  `number.py`, `switch.py`) filters Melitta-only entities (recipe
  select, freestyle builder, profile activity switches, cup counters
  via HC) when `"HC"` / `"HJ"` is not in the brand's supported set.
- `bluetooth` matchers in `manifest.json` now include `local_name:
  "NIVONA-*"` in addition to the shared service UUID.

### Migration
- Config entries automatically upgrade from v1 → v2 via
  `async_migrate_entry`: all pre-existing entries receive
  `data["brand"] = "melitta"`. No action required from users.
- Entity unique IDs are stable — all existing automations continue to
  work.

### Tests
- 665 → 686 (+21 brand-profile tests).

## [0.34.1] — 2026-04-13

### Fixed
- **Stale recipe cache after HD reset**: after `reset_recipe_default`
  received an ACK, the Recipe select entity's cached `recipes`
  attribute kept showing pre-reset values until a reconnect. Now the
  client re-reads the recipe via HC and notifies subscribers through
  a new `add_recipe_refresh_callback` hook; `MelittaRecipeSelect`
  subscribes and refreshes its cached attributes immediately.

## [0.34.0] — 2026-04-13

### Added
- **HY confirm-prompt** protocol command (`CMD_CONFIRM_PROMPT`) +
  `protocol.confirm_prompt()` + client mixin `confirm_prompt()`.
- **`Awaiting Confirmation` binary_sensor** (PROBLEM device class) that
  turns on whenever `MachineStatus.manipulation` reports any active
  prompt (codes 1–6, 11, 20).
- **`Confirm Prompt` button** — manual acknowledgement, available only
  when a prompt is active.
- **`melitta_barista.confirm_prompt` service** for automation use.
- **Global `Auto-confirm soft prompts` Options Flow toggle** — when
  enabled, the integration automatically sends HY for soft prompts
  (`MOVE_CUP_TO_FROTHER`, `FLUSH_REQUIRED`) so brew flow proceeds
  without user intervention. Hardware-blocking prompts (fill water,
  empty trays, etc.) intentionally still require manual confirmation.
- Auto-confirm uses per-code debounce — each prompt is auto-confirmed
  only once per "appearance" to avoid loops if the machine reasserts.
- Two new `Manipulation` enum members: `MOVE_CUP_TO_FROTHER = 11`,
  `FLUSH_REQUIRED = 20`.
- New platform: `Platform.BINARY_SENSOR`.
- Translations (29 languages) for new entities, options, errors.

## [0.33.0] — 2026-04-13

### Added
- **HD reset-to-default** protocol command (`CMD_RESET_DEFAULT`) +
  `protocol.reset_default(value_id)` + client mixin method
  `reset_recipe_default(recipe_id)`.
- **`Reset Recipe` button** — config-category entity that sends HD for
  the currently selected recipe. Available only when the machine is
  ready and a recipe is selected. NACK/timeout logged as warning,
  does not crash the entity.
- **`melitta_barista.reset_recipe` service** with optional `recipe_id`
  (defaults to currently selected). Raises `ServiceValidationError` if
  no machine matched the entity or no recipe selected;
  `HomeAssistantError` on NACK/timeout.
- Translations (29 languages) for the new button and error messages.

### Fixed
- **Blocking file I/O in event loop**: `ws_presets_list` was reading
  `coffee_presets.json` synchronously inside the event loop, triggering
  HA warnings. Now cached in-memory after a single executor-thread load.

## [0.32.0] — 2026-04-13

### Added
- **HI feature capability read** on connect — machine reports supported
  capability bits (currently known: bit 0 = `IMAGE_TRANSFER`). Graceful
  degradation via 3s timeout — some firmwares do not answer HI.
- **`Features` diagnostic sensor** (disabled by default) exposing parsed
  flags + raw byte in `extra_state_attributes`.
- `features` field in diagnostics output.
- `FeatureFlags` IntFlag enum in `const.py`.
- `send_and_wait_response()` now accepts optional `timeout` override
  (backwards-compatible).

## [0.29.0] — 2026-03-20

### Added
- Recipe select entity: `recipes` attribute with all preloaded recipe details
- Profile select entity: `directkey_recipes` attribute with per-profile DK data
- Info-level logging for recipe preload progress

## [0.28.0] — 2026-03-20

### Added
- Dark theme brand icons (dark_icon.png, dark_logo.png + @2x variants)
- GitHub community files: CODE_OF_CONDUCT, CONTRIBUTING, SECURITY, issue/PR templates
- Milk category in brew_directkey service schema

### Changed
- Git history cleaned: removed scripts/, audit/, docs/QUALITY_SCALE_PLAN.md
- Removed all decompilation/APK references from code, docs, and git history

## [0.27.0] — 2026-03-19

### Added
- **Repair Issues**: BLE connection instability warning in Settings → Repairs (Gold: `repair-issues`)
- **GitHub Actions CI**: automated tests, coverage, HACS validation, hassfest, ruff, bandit
- README badges updated: 497 tests, 97% coverage

### Stats
- **497 tests**, **97% coverage**, 12 modules at 99-100%
- Bronze 18/18 ✅, Silver 10/10 ✅, Gold ~18/22

## [0.26.0] — 2026-03-19

### Added
- **HA Quality Scale compliance**:
  - `PARALLEL_UPDATES = 0` in all 6 entity platform files (Silver: `parallel-updates`)
  - Service actions now raise `HomeAssistantError` / `ServiceValidationError` (Silver: `action-exceptions`)
  - Exception translations in `strings.json` (Gold: `exception-translations`)
  - `ConfigFlowResult` return types (HA best practice)
- **Quality Scale Plan**: `docs/QUALITY_SCALE_PLAN.md` — detailed roadmap to Platinum

### Changed
- Service handlers (`brew_freestyle`, `brew_directkey`, `save_directkey`) raise exceptions on failure instead of silently returning

## [0.25.0] — 2026-03-19

### Added
- **Diagnostics support** (`diagnostics.py`) — HA diagnostics panel with redacted BLE address
- **Reconfigure flow** (`async_step_reconfigure`) — change BLE address/name without re-adding
- **Type safety** (`_ble_typing.py`) — Protocol class for mypy mixin type checking

### Changed
- `manifest.json`: added `integration_type: "device"`, `loggers: ["melitta_barista"]`
- `config_flow.py`: migrated `FlowResult` → `ConfigFlowResult` (HA best practice)
- Mixin classes now use conditional `_MixinBase` for mypy compatibility

### Improved
- Test coverage: 89% → 89% (371 tests, was 349)
- `button.py`: 78% → **100%** (22 new tests)
- `config_flow.py`: maintained 90% (reconfigure flow added)
- HA Quality Scale: 10/14 → **13/14** (diagnostics, loggers, integration_type added)

## [0.24.0] — 2026-03-19

### Changed
- **Refactor**: `ble_client.py` split from 1386 lines into 4 modules using mixins:
  - `ble_client.py` — connection, reconnect, polling (684 lines)
  - `_ble_commands.py` — brew, cancel, maintenance (262 lines)
  - `_ble_recipes.py` — recipe/profile CRUD, cup counters (447 lines)
  - `_ble_settings.py` — settings, alpha read/write (62 lines)
- All external imports unchanged — fully backward-compatible

## [0.23.4] — 2026-03-19

### Fixed
- `_SHOTS_NAMES` mapped to integers instead of strings — shots entity attributes rendered as `0/1/2/3` instead of `"none"/"one"/"two"/"three"`
- Brew/recipe methods used hardcoded `DEFAULT_POLL_INTERVAL` instead of `self._poll_interval`, silently overriding user Options Flow configuration

## [0.23.3] — 2026-03-19

### Fixed
- `_load_post_connect_data` task now tracked and cancelled on disconnect (was fire-and-forget, could write to closed BLE)
- `set_ble_device()` no longer spawns duplicate `_reconnect_loop` when `_async_connect_and_poll` is still active (shared `_reconnect_event` race condition)
- `MelittaProtocol()` in `_try_connect_and_handshake` now passes `frame_timeout` from Options Flow (was using hardcoded default)
- `write_alpha()` now checks `was_polling` before restarting poll loop in `finally` (was unconditionally starting polling)
- `send_and_wait_response()` now cleans up stale future via `finally` block (was leaking future when `write_func` raised)
- Cup counter refresh now checks `_brew_lock.locked()` before launching (could interleave with brew sequence)

## [0.23.2] — 2026-03-19

### Fixed
- **Critical**: reconnect loop silently cancelled itself — `_connect_impl` called `_reconnect_task.cancel()` on the currently running task, preventing any reconnection after BLE disconnect
- Poll-loop forced disconnect now calls `_safe_disconnect()` to properly close the BLE connection on ESPHome proxy before scheduling reconnect

### Added
- New test verifying reconnect loop does not cancel itself

## [0.23.0] — 2026-03-14

### Added
- **Options Flow UI**: configurable integration parameters via Settings → Integrations → Melitta Barista → Configure
  - **Basic settings**: poll interval, reconnect initial delay, reconnect max backoff, poll errors before disconnect, BLE frame timeout
  - **Advanced settings**: BLE connection timeout, pairing timeout, recipe read/write retries, initial connect delay
- All 9 parameters have sensible defaults matching previous hardcoded values — no changes needed after upgrade
- 4 new tests for Options Flow (init menu, basic form, basic submit, advanced submit)

### Changed
- `MelittaProtocol` accepts `frame_timeout` parameter instead of using module-level constant
- `MelittaBleClient` accepts all configurable parameters via constructor kwargs
- `_async_connect_and_poll` accepts `poll_interval`, `initial_delay`, `reconnect_delay`, `reconnect_max_delay` parameters
- Integration reloads automatically when options are changed

## [0.22.2] — 2026-03-14

### Changed
- Settings switches and number entities no longer poll via BLE every 30s; values are read once on connect (`should_poll=False`)
- Parameter mappings (`PROCESS_MAP`, `INTENSITY_MAP`, etc.) consolidated into `const.py`, eliminating duplication across `button.py` and `__init__.py`
- Profile data and cup counters now load in background after connect, not blocking the connection phase
- All 11 `device_info` properties replaced with shared `MelittaDeviceMixin` (new `entity.py`)
- Hardcoded `interval=5.0` replaced with `DEFAULT_POLL_INTERVAL` constant

## [0.22.1] — 2026-03-14

### Fixed
- **Graceful shutdown**: background connect task is now cancelled on integration unload, preventing "task still running after shutdown" warnings
- **Callback cleanup**: all entity callbacks are unsubscribed in `async_will_remove_from_hass`, preventing duplicate state updates and stale references after integration reload
- **Poll loop disconnect detection**: 3 consecutive poll errors now force disconnect and trigger reconnect, fixing silent "zombie" connections where BLE link is dead but no disconnect callback fires (e.g. ESP32 reboot without clean disconnect)

## [0.22.0] — 2026-03-14

### Added
- Instant reconnect on BLE advertisement: when machine powers on after being offline, reconnect triggers immediately instead of waiting up to 5 minutes for next backoff retry
- Reconnect event mechanism (`_reconnect_event`) wakes up both initial connect and reconnect loops when BLE advertisement is received
- Backoff delay resets to 5s when advertisement arrives (machine is likely available)
- Catch-all exception handler in reconnect loops prevents silent reconnect death

### Fixed
- Machine not reconnecting after long power-off without HA restart
- Profile and Recipe select entities no longer store all DirectKey/recipe data in state attributes, preventing Recorder "exceeds maximum size of 16384 bytes" warnings
- Config flow test `test_step_pair_success_creates_entry` no longer times out

### Removed
- `directkey_recipes` attribute from Profile select (was causing >16KB state attributes)
- `recipes` attribute from Recipe select (redundant bulk data; selected recipe details still available)

## [0.21.1] — 2026-03-10

### Fixed
- BLE connection: 3-phase pairing strategy to handle ESPHome proxy bond issues
  - Phase 1: `pair=False` (reuse existing bond — fast reconnect)
  - Phase 2: `pair=True` (create new bond — first-ever connection)
  - Phase 3: `unpair` + `pair=True` (clear stale bond on ESP32, then fresh pair)
- Fixes `TimeoutAPIError`, `BluetoothConnectionDroppedError`, and pairing error 82 on ESPHome BLE proxy after ESP32 reboot
- Refactored connect logic into `_try_connect_and_handshake` / `_try_unpair` for clean retry

### Added
- ESPHome config for Seeed XIAO ESP32-S3 BLE proxy (`esphome/ble-proxy-xiao-s3.yaml`)

## [0.21.0] — 2026-03-09

### Added
- Aroma parameter (standard/intense) for freestyle entities, services, and recipe attributes
- Freestyle Aroma select entities (aroma_1, aroma_2)
- Aroma fields in brew_freestyle and save_directkey services

### Fixed
- Profile Activity switches no longer poll periodically (was causing "update taking over 10 seconds" warnings); values are now read once on connect

## [0.20.0] — 2026-03-09

### Fixed
- `select.py`: Temperature.COLD now correctly maps to "cold" instead of "normal" (was losing COLD value)
- `ble_client.py`: `reset_profile_recipe`, `update_profile_recipe`, `copy_profile_recipe` now use `_brew_lock` and stop/resume polling to prevent BLE contention
- `ble_client.py`: `write_profile_recipe` only restarts polling if it was active before the operation
- `ble_client.py`: Race condition between disconnect callback and manual disconnect prevented with `_disconnecting` guard
- `ble_client.py`: Replaced deprecated `asyncio.ensure_future` with `asyncio.create_task`
- `sensor.py`: `MelittaActivitySensor` now has `available` property based on connection state

### Added
- Number entities: Language, Clock, Clock Send, Filter machine settings
- Button entities: Filter Insert, Filter Replace, Filter Remove, Evaporating maintenance operations
- Switch entities: Profile Activity (enable/disable user profiles 1-8)

## [0.19.0] — 2026-03-09

### Fixed
- HJ write_recipe: omit `recipe_key` byte for DirectKey slots (`recipeKey=null` skips the byte, components start at offset 3)
- Only TEMP_RECIPE writes (for brewing) include `recipe_key`; DK slot writes (save, reset, copy, update) do not
- This fixes "ACK timeout" errors when saving DirectKey recipes

## [0.18.2] — 2026-03-09

### Fixed
- `write_profile_recipe` now retries write_recipe up to 3 times on ACK timeout
- Added detailed debug logging for DirectKey recipe write (recipe_id, type, key)

## [0.18.1] — 2026-03-09

### Fixed
- `write_profile_recipe` no longer fails when `read_recipe` returns None — falls back to default `recipe_type` per DirectKey category
- Added `DIRECTKEY_DEFAULT_RECIPE_TYPE` mapping for all 7 categories

## [0.18.0] — 2026-03-09

### Added
- Two cups (2x) mode: `two_cups` flag in HE startProcess payload at offset 6
- `two_cups` parameter in `brew_recipe`, `brew_directkey`, `brew_freestyle` methods
- `two_cups` field in `brew_directkey` and `brew_freestyle` service schemas

## [0.17.1] — 2026-03-09

### Fixed
- HC response parsing: remove incorrect recipe_key byte skip — HC payload is `id(2)+type(1)+comp1(8)+comp2(8)`, no recipe_key
- HJ write payload: pass correct `recipe_key` per RecipeType→RecipeKey mapping
- Fix `RECIPE_KEY_MAP`: Espresso Macchiato → CAPPUCCINO(2), not MACCHIATO(3)
- Add `RECIPE_TYPE_TO_KEY` mapping and `get_recipe_key()` helper for all 25 recipe types
- All `write_recipe` call sites now pass correct `recipe_key` (brew, DirectKey, freestyle, profile edit, copy, reset)

## [0.17.0] — 2026-03-09

### Added
- DirectKey brewing: read DK recipe → write to temp slot → start brew
- Profile data caching for faster recipe access

### Fixed
- BLE protocol: rewrite frame parser to match original Melitta app algorithm
- A/N (ACK/NACK) frames are plaintext — no longer RC4-decrypted
- Frame timeout to prevent stale buffer corruption
- Drop corrupted BLE frames, retry read_recipe on checksum mismatch
- Stop polling during BLE writes to prevent command conflicts
- Eliminate BLE reads from text entity polling, retry ACK on timeout

## [0.11.5] — 2026-03-08

### Added
- 75 new tests for ble_client.py (26→101), covering connect/disconnect, reconnect, BLE write, notifications, brew, maintenance, cup counters, discovery
- Total: 249 tests, 89% coverage (was 174 tests, 82%)
- `ble_client.py` coverage: 62% → 100%

## [0.11.4] — 2026-03-08

### Fixed
- Fix 14 ruff errors in tests (unused imports, unused variables)
- Suppress Bandit B413 false positive for pycryptodome (`# nosec B413`)
- Update audit report with fresh results (ruff 0→0, 174 tests, 82% coverage)

## [0.11.3] — 2026-03-08

### Changed
- Narrow 30 broad `except Exception` to specific types (`BleakError`, `OSError`, `asyncio.TimeoutError`) across ble_client, __init__, config_flow, select, protocol
- Refactor `async_pair_device` (154→6 helper functions, CC 17→5) in ble_agent.py
- Extract `_async_discover_devices()` from `async_step_user` (CC 18→8) in config_flow.py

### Added
- 51 new tests: config_flow (100% coverage), ble_agent (93% coverage)
- Total: 174 tests, 82% coverage (was 123 tests, 71%)

## [0.11.2] — 2026-03-08

### Fixed
- Fix failing test `test_recipe_select_option` — mock missing `active_profile` and `read_recipe`
- Remove unused imports (`asyncio` in config_flow, `TYPE_CHECKING`/`HomeAssistant` in ble_client)
- Fix undefined `RecipeComponent` type annotation — add proper import
- Add `# noqa: F821` to D-Bus type signature annotations in ble_agent.py
- Sync `strings.json` accent characters with `translations/en.json` (Café, Crème, Caffè)
- Add `_write_lock` to BLE client for serialized GATT writes (prevents concurrent write races)
- Ruff: 18 errors → 0 errors

## [0.11.1] — 2026-03-08

### Added
- ESPHome `.gitignore` and `secrets.yaml.example` for easy proxy setup
- Test scripts for BLE connection verification (local adapter and ESPHome proxy)

## [0.11.0] — 2026-03-08

### Added
- Automatic BLE pairing via Bleak's `pair=True` — works with both local BlueZ adapter and ESPHome BLE proxy
- Config flow gracefully skips D-Bus pairing when unavailable (pairing handled on connect)

### Changed
- ESPHome proxy config: removed aggressive scan parameters (1100ms/1100ms) — use defaults for stable single-core ESP32-C6 operation
- `establish_connection()` and fallback `BleakClient` now pass `pair=True` for cross-platform bonding

## [0.10.2] — 2026-03-08

### Fixed
- Check `Adapter1` interface existence (not just D-Bus path) when detecting local BlueZ adapter
- Added CHANGELOG.md

## [0.10.1] — 2026-03-08

### Fixed
- Skip D-Bus pairing when no local BlueZ adapter — enables ESPHome BLE proxy support
- `ble_agent.py` now checks for `hci0` existence before attempting D-Bus pairing; returns "ok" if missing (proxy handles bonding at ESP32 level)

## [0.10.0] — 2026-03-08

### Added
- Preload all recipe details on BLE connect — cached in `recipes` attribute
- Web app shows recipe details instantly without per-click BLE reads

## [0.9.3] — 2026-03-08

### Fixed
- Harden BLE code against race conditions and short payloads
- Capture `self._client` to local var in `connected`, `_write_ble`, `disconnect()` to prevent race with `_on_disconnect` callback
- Add length guards to `NumericalValue`, `AlphanumericValue`, `MachineRecipe`, `RecipeComponent` `from_payload`/`from_bytes` — return `None` on short data
- Handle fire-and-forget cup counter refresh task errors via `done_callback`

## [0.9.2] — 2026-03-08

### Fixed
- Wrap handshake-failure disconnect in `try/except` — prevents `EOFError` noise when D-Bus connection is already dead

## [0.9.1] — 2026-03-08

### Fixed
- Handle short HR payloads in cup counters (IDs 111, 122 return < 6 bytes)
- Fix race condition in `_connect_impl` error handler where `_on_disconnect` could null `self._client` between check and `disconnect()` call

## [0.9.0] — 2026-03-08

### Added
- Cup counter sensor (`total_cups`) with per-recipe statistics as attributes
- Counters auto-refresh after each brew completes (PRODUCT → READY transition)
- Counter IDs discovered via BLE scan: HR 100–123 (per recipe) + HR 150 (total)

## [0.8.1] — 2026-03-07

### Fixed
- Map temperature `0` to "normal" (standard brew temperature) instead of "low"

## [0.8.0] — 2026-03-07

### Added
- Expose recipe details (intensity, temperature, shots, portion) as `extra_state_attributes` on recipe select entity
- Reads recipe via HC command on selection, respects active profile (DirectKey)

### Changed
- Documentation: added freestyle entities reference and PWA app section to README

## [0.7.1] — 2026-03-06

### Added
- Freestyle recipe entities for standard HA UI:
  - Select entities for process, intensity, temperature, shots (both components)
  - Number entities for portion sizes (ml)
  - Text entity for recipe name
  - "Brew Freestyle" button that reads entity values and brews

### Fixed
- Rename "steam" to "milk" in freestyle UI for clarity (protocol value unchanged)
- Legacy cleanup now preserves `brew_freestyle` button

## [0.7.0] — 2026-03-06

### Added
- Profile select entity with DirectKey-based per-profile brewing
- `brew_freestyle` service for custom drink recipes via TEMP_RECIPE
- `DirectKeyCategory` enum and `get_directkey_id()` calculation
- `services.yaml` for HA service UI
- 15 new tests for profiles, DirectKey, freestyle

## [0.6.6] — 2026-03-05

### Fixed
- Instant entity availability update on BLE reconnect — all entities (buttons, select, sensors, switches, numbers, text) now register connection callbacks

## [0.6.5] — 2026-03-05

### Fixed
- Expand legacy cleanup to handle named recipe button entities (`{address}_brew_espresso`, etc.) in addition to numeric IDs

## [0.6.4] — 2026-03-05

### Added
- Comprehensive test suite: 107 tests covering protocol, BLE client, entities, and integration lifecycle

### Fixed
- Code quality improvements from audit:
  - Fix firmware sensor race with background connect
  - Fix maintenance buttons accessing private client members
  - Fix incorrect `Callable` type annotations in `protocol.py`
  - Fix state sensor returning "unavailable" string instead of `None`
  - Move connection callback registration to `async_added_to_hass`
  - Remove dead code
- Additional audit fixes:
  - Add `pycryptodome` and `bleak-retry-connector` to manifest requirements
  - Fix duplicate device entries in `discover_melitta_devices`
  - Type `async_step_bluetooth` with `BluetoothServiceInfoBleak`

## [0.6.2] — 2026-03-04

### Added
- Initial release of Melitta Barista Smart integration for Home Assistant
- BLE communication via `bleak` with D-Bus Agent1 pairing
- Recipe select entity + brew button pattern (replaces 24 individual buttons)
- 29 language translations
- HACS-compatible structure with GitHub Actions validation
