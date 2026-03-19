# Changelog

All notable changes to the Melitta Barista Smart HA Integration.

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
- HJ write_recipe: omit `recipe_key` byte for DirectKey slots (``recipeKey=null` skips the byte, components start at offset 3)
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
