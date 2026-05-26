# Changelog

All notable changes to the Melitta Barista Smart & Nivona HA Integration.

## [0.73.0] — 2026-05-26

### Added (P10 — Nivona-safe Sommelier)
- **`LiveCapabilities` now carries `supports_recipe_writes: bool`** (schema v1 → v2). The flag is sourced from each family's `MachineCapabilities.supports_recipe_writes`. Surfaced through `melitta_barista/capabilities/get`. v1 cached blobs default to `True` on parse so existing Melitta installs see no change.
- **`ws_brew` and `ws_favorites_brew` refuse the BLE write** when the active machine reports `supports_recipe_writes=False`, surfacing `recipe_writes_unsupported` via `send_error` instead of failing silently in the BLE layer. The new `RecipeWritesUnsupportedError` carries the offending `family_key`.
- **Brew-related buttons are disabled in the Sommelier UI** when the active machine doesn't support recipe writes — applies to the per-card "Brew this" action, the brewing wizard's "Start brewing" button, and the brew / "Brew again" actions in the Favorites and History modals. The wizard still opens so the user can read the recipe + steps as a print-only card; an inline note in the pre-phase explains why brewing is unavailable. EN + RU i18n via `brewing.unsupported_tooltip` / `unsupported_note` / `unsupported_error`.

### Notes
- All Nivona families declare `supports_recipe_writes=False` because their recipe protocol differs from Melitta's freestyle slot — the integration cannot write a custom recipe to a Nivona machine. Adding Nivona freestyle support is a separate RE / protocol effort, out of scope here.
- The Sommelier data + UI surface (generation, favorites, history, ratings, presets, pantry) is brand-agnostic and remains fully functional on Nivona as a recipe notebook.

## [0.72.0] — 2026-05-26

### Fixed (TZ §10 B6 — preference-key write allowlist)
- **`use_weather`, `weather_entity`, and `use_presence` are now writable via `melitta_barista/sommelier/preferences/set`.** They have always been **read** by `ws_generate` (powering the weather and presence context blocks in the LLM prompt), but the write-side allowlist `VALID_PREFERENCE_KEYS` only covered the four `default_*` keys, so the WS API surface couldn't actually configure them. Frontends had to write into the DB out-of-band. Now they're first-class preferences.

### Notes
- This closes the only live backend bug from the TZ's §10 open-issues snapshot. The remaining items in that list are either documentation placeholders (`recipes/list` empty `base_recipes`), explicit deferrals (multi-machine routing, milk catalogue rewrite), or non-issues now (R9 audit, P3 ratings/history, rich-field syrups/toppings).

## [0.71.0] — 2026-05-26

### Added (P8b — R1 slice 2: autofill endpoints + UI modal extension)
- **`melitta_barista/syrups/autofill` and `melitta_barista/toppings/autofill` WS endpoints.** Mirror `/beans/autofill`: take `{brand, variant?, website?, agent_id?}`, call `_structured_call` against an HA conversation agent, return `{raw, parsed, validation_errors, via, schema_version}`. The `parsed` dict is validated by the new shared `AdditiveAutofillResult` pydantic model (`flavor_notes: list[str]`, `composition: str`, `attributes: dict[str, bool]`, `variant: str`). Backed by `DEFAULT_PROMPTS["syrups_autofill"]` / `toppings_autofill` with `{brand}` / `{variant_hint}` / `{website_hint}` placeholders.
- **Additives modal: rich-field block for syrups & toppings** in `melitta-additives.js`. Producer dropdown (loads `melitta_barista/producers/list`), Variant input, Flavor-notes chips (removable), Composition textarea, predefined Attribute chips (`vegan` / `sugar_free` / `lactose_free` / `gluten_free` / `nut_free`). Save sends only the populated fields — partial-patch semantics on the backend keep prior values intact for fields the user didn't touch.
- **"Fill from LLM" button** in the modal. Disabled until the user enters a brand. On success merges the parsed response into the editing state (variant only fills if empty; attributes filtered to `true` values; flavor_notes deduped). Errors stay scoped to a `.autofill-error` banner inside the modal.

### Notes
- Milk rows are intentionally unchanged — the milk catalogue still uses the flat-list `/milk/get|set` shape. Rewriting it to a CRUD catalogue with the same rich fields requires a legacy shim and stays out of scope.
- The new fields are persisted via the existing P8a `<table>/add` / `<table>/update` endpoints; no breaking changes to schemas.

## [0.70.0] — 2026-05-26

### Added (P8a — R1 slice 1: rich-field syrups & toppings catalogue)
- **`syrups` and `toppings` catalogue tables gain `producer_id`, `variant`, `flavor_notes`, `composition`, and `attributes` columns.** Legacy DBs migrated via idempotent `ALTER TABLE` guards inside `_ensure_panel_schema` (extends the P4a pattern). `flavor_notes` is a JSON-encoded list of strings; `attributes` is a JSON-encoded object; both are NULL by default. The existing `brand` column carries over unchanged.
- **`<table>/list` returns the new fields** with JSON values parsed back into Python lists/dicts. Bad JSON in a column returns `None` instead of crashing the handler (defensive fallback).
- **`<table>/add` and `<table>/update`** accept the new fields as optional parameters with voluptuous length/type constraints. Existing partial-patch semantics on `update` (`no_fields` error when nothing changes) are preserved; the new fields count toward the patch.

### Notes
- Mirrors the existing `coffee_beans` rich-metadata shape. The producers table already supports cross-category use, so no producer-table changes are needed.
- UI extensions to the Additives modal (producer dropdown, variant input, flavor-notes chips, attributes chips) and the LLM-backed `/syrups/autofill` / `/toppings/autofill` endpoints are deferred to **P8b**.
- Milk-config rewrite (turning the flat list into a CRUD-able catalogue with the same rich fields) is a separate, larger refactor with a legacy shim for the existing `/milk/get|set` endpoints — scoped out of P8.
- The Sommelier LLM prompt does not yet consume the new fields. Whether to enrich the prompt with brand / flavor_notes / composition is gated on observed recommendation quality — that's an optional future **P8c**.

## [0.69.0] — 2026-05-26

### Added (P7b — R8 slice 2: machine_profile UI)
- **"Profile N" badge in the Sommelier panel header** when the machine reports an active hardware profile. Sourced from `melitta_barista/status`'s `active_profile` field; falls back silently if the integration cannot reach the machine.
- **Preset / favorite / history lists auto-filter to the active profile** by passing `machine_profile_filter` on every list call. Shared entries (`machine_profile IS NULL`) always come through, so nothing disappears when a user switches profiles.
- **"Save as preset" gains an optional "Bind to profile N" checkbox** — visible only when a profile is active, default OFF (shared). Checked → preset is bound to the current machine profile via the P7a `machine_profile` parameter.
- **`sommelier/generate` is auto-tagged** with the active profile so the resulting `generation_sessions` row surfaces under the same filter in History.

### Notes
- R8 is now functionally closed: tagging + filtering work across presets, favorites, history, and new generate sessions. The favorites "Add" path inherits shared semantics (no per-machine binding UI yet — the Manage modal hides per-profile edit because R8 didn't request it for favorites).
- Profile-aware filtering is implicit (no toggle). The TZ §R8 mention of a "Show shared toggle" is deferred — every profile-aware list already includes shared rows, which addresses the common case.

## [0.68.0] — 2026-05-26

### Added (P7a — R8 slice 1: machine_profile tagging, data layer)
- **`machine_profile INTEGER` column on `sommelier_presets`, `favorites`, and `generation_sessions`.** NULL means "shared" (visible across all machine profiles); a specific integer binds the row to that profile slot. Existing rows are retro-flagged shared via the v8 → v9 migration's `ALTER TABLE ... ADD COLUMN` (SQLite leaves the new column NULL for existing rows by default). Row dicts now include `machine_profile`.
- **Optional `machine_profile` parameter** on `melitta_barista/sommelier/presets/add`, `favorites/add`, and `sommelier/generate` (tags the created `generation_sessions` row).
- **Optional `machine_profile_filter` parameter** on `melitta_barista/sommelier/presets/list`, `favorites/list`, and `history/list`. When set to an integer N, the response includes rows where `machine_profile = N` OR `machine_profile IS NULL`. Shared rows always come through.

### Notes
- This is the data-layer slice of TZ §R8. The FE work — surfacing the active profile from `client.active_profile`, choosing a profile on save, filtering the lists by current profile — is **P7b**.
- The `machine_profile` integer here refers to the machine's hardware profile (the same value `client.active_profile` exposes via `melitta_barista/status`), not the Sommelier user-profile concept tracked separately by `sommelier_profiles`.

## [0.67.0] — 2026-05-26

### Added (P6b — schema_version envelope, R10 slice 2)
- **Every `melitta_barista/*` WS response now carries a `schema_version: int` key** alongside its existing fields. `API_VERSION` (from 0.66.0) still tracks the integration-wide surface; `schema_version` is the per-endpoint discriminator that lets consumers pin against an individual endpoint's response shape. All endpoints ship at `schema_version = 1` in this release.
- **Centralised helper `_send_versioned(connection, msg_id, data, *, schema_version=1)`** in `panel_api.py`, imported by `sommelier_api.py` and `__init__.py`. Every `connection.send_result(...)` call in the integration now routes through this helper. Future endpoints should use it instead of `send_result` directly.

### Notes
- Purely additive — existing response keys are unchanged; clients that ignore uf nknown keys (the default for both Lit and HA companion app) keep working without changes. Tests asserting exact response shape were relaxed to ignore `schema_version` via a small `_assert_result` helper in the affected test files.
- TZ §R10's MUST for per-endpoint versioning is now satisfied. Slim list variants and a REST wrapper (§O10.1) stay out of scope until a real consumer asks for them.

## [0.66.0] — 2026-05-26

### Added (P6a — API contract foundation, R10 slice 1)
- **`docs/SOMMELIER_API.md`.** Exhaustive enumeration of every `melitta_barista/*` WebSocket endpoint (~55 entries), organised by namespace, with inputs / outputs / decorators / stability tag. This is now the canonical API contract — bumping `api_version` requires updating this doc. Each endpoint is marked `≤0.65.0` (pre-existing) or `0.66.0` (the new `api/info` itself).
- **`API_VERSION = "1.0"` constant** in `const.py`. Semver: bump **major** on a breaking change to any endpoint's input or output shape; bump **minor** on additive changes (new endpoint, new optional field, new optional response key).
- **`melitta_barista/api/info` WS endpoint.** Non-admin discovery handshake returning `{api_version, integration_version, schema_db_version, endpoints}`. `endpoints` is the domain-prefixed subset of HA's registered WS commands; consumers cross-reference `docs/SOMMELIER_API.md` for per-endpoint details. The integration-version lookup is wrapped in a broad try/except so the handshake never fails.

### Notes
- Per-response `schema_version` envelopes (TZ §R10's MUST) are deferred to **P6b** — a mechanical retrofit across ~55 handlers and their tests.
- Slim list variants and a REST wrapper (TZ §O10.1) stay out of scope until there's a concrete consumer asking for them; HA companion app and Lovelace both speak WS.

## [0.65.0] — 2026-05-26

### Added (P5b — Sommelier presets closing slice + §O7.1)
- **Four built-in presets** seeded on first setup: Morning, After lunch, Work, Guests. Names resolve through i18n (`presets.system.*`), so the select shows them in the user's language. All four ship with `dynamic_occasion=true` (see below), mirroring the existing `_suggestOccasionByTime()` time bands.
- **`dynamic_occasion` flag in the preset payload.** When `true`, `_applyPreset` recomputes `occasion` from the current local time instead of taking the snapshot value — applying "Morning" at 3pm fires the form with `occasion=after_lunch`. System presets default to `true`; user-created presets default to `false` (snapshot semantics).
- **System presets are read-only.** The DB layer raises `ValueError("system_preset_readonly")` for update/delete on `is_system=1` rows; the WS handlers translate that into `send_error("system_preset_readonly", ...)`. The Manage modal hides the rename + delete buttons for built-ins and shows a `(built-in)` badge instead.
- **DB schema v7 → v8.** Adds `is_system` and `dynamic_occasion` columns to `sommelier_presets`. Existing user presets retain `is_system=0` and `dynamic_occasion=0`. Seeding runs idempotently at the end of `async_setup`.

### Fixed (R9)
- `melitta-beans.js:619` rendered "Validation errors:" as a hardcoded literal — the last visible string outside `i18n.js`. It now lives in `common.validation_errors` with English and Russian entries.

## [0.64.0] — 2026-05-26

### Added (P5a — Sommelier presets, R7 slice 1)
- **Named presets for the Sommelier generate form.** Snapshot of `mode`, `preference`, `cup_size`, `moods`, `occasion`, `temperature`, `caffeine_pref`, and `dietary` — applied with one click from the new "Preset" select in the Sommelier header. Save the current form state via "Save as preset"; manage existing entries (rename, edit description, delete) via the new `<melitta-sommelier-presets>` modal.
- **`sommelier_presets` table (DB v6 → v7).** Backed by four `melitta_barista/sommelier/presets/{list,add,update,delete}` WS endpoints (`require_admin`, opaque JSON payload, `preset_id` to avoid the HA WS top-level `id` collision).

### Changed (incidental)
- The pre-existing `melitta_barista/sommelier/presets/list` handler that served the static `coffee_presets.json` bean catalogue was renamed to `melitta_barista/sommelier/bean_presets/list` to free the namespace. No in-tree caller used the old name.

### Notes
- Pantry constraints (`allow_syrups` / `allow_toppings` / `allow_milk`) and per-generation `count` are intentionally **not** snapshotted — pantry tracks catalogue availability and count is a knob, both better picked fresh per generation.
- System defaults, profile binding (R8), and the "dynamic occasion" toggle from §O7.1 are deferred.

## [0.63.0] — 2026-05-26

### Changed (P4b — Pantry, R6 closing slice)
- **The Sommelier AI now reads its pantry directly from the syrups / toppings catalogue** (`available=1`), not from `user_extras`. `ws_generate`, `melitta_barista/sommelier/extras/get`, and the `sommelier_intro` prompt-preview path are all backed by the new `async_get_pantry_extras` helper. Liqueurs and misc/ice still live in `user_extras` and are unaffected.
- **Sommelier UI chip list hides out-of-stock items.** `_loadAvailable` filters by the catalogue's `available` flag, so toggling a syrup off in the Additives panel removes it from the chip picker on the next Sommelier visit. The backend enforces the same filter.
- **`set_available` no longer mirrors into `user_extras`.** The mirror was a P4a bridge so the AI (which read `user_extras` then) would see catalogue toggles. With the AI reading the catalogue directly, the mirror is dead weight and is dropped.

### Migration note
- If your Sommelier suggestions used to draw on syrups/toppings that were stored only in `user_extras` (never added via the Additives panel), re-add them via the Additives panel. They will land in the catalogue with `available=1` and become visible to the AI again.

## [0.62.0] — 2026-05-26

### Added (P4a — Pantry, R6 slice 1)
- **`available` column on the `syrups` / `toppings` panel catalogue tables.** Schema lifted on fresh DBs via `CREATE TABLE`; legacy DBs get the column via an idempotent `PRAGMA table_info`-guarded `ALTER TABLE` inside `_ensure_panel_schema`. Existing rows are retro-flagged in stock (`DEFAULT 1`). The flag is exposed in `melitta_barista/syrups/list` / `toppings/list` responses and patchable via the existing `<table>/update` handler.
- **`melitta_barista/<table>/set_available` WS endpoints** for `syrups` and `toppings`. Resolves the catalogue row by `additive_id`, updates the flag, and mirrors the state into `user_extras` so the Sommelier AI prompt context (which still reads `user_extras` in P4a) reflects pantry state without a separate UI step. The mirror is one-way (catalogue → `user_extras`) and upserts on enable.
- **Inline "in stock" toggle on every syrup / topping row** in the Additives panel. Out-of-stock rows render at 50% opacity; the toggle reads `additives.in_stock` / `additives.out_of_stock` for its tooltip.

### Notes
- This is the first slice of TZ §R6. Beans availability, milk-row toggle, and the Sommelier-side "Show only in-stock items" UX are intentionally out of scope here. Full cutover (Sommelier reads the catalogue directly, `user_extras` retired) is **P4b/P4c**.

## [0.61.0] — 2026-05-25

### Added (frontend, finishes the P3 ratings/history surface)
- **`<melitta-sommelier-history>` modal.** Opened from the new "🕓 History" button in the Sommelier header. Paginated session list (loads `melitta_barista/sommelier/history/list`) with expandable rows: clicking a session inlines its recipes with per-recipe star rating, "Brew again" action (routes through the wizard with `source="generated"` so the user can re-tune extras / cup before brewing), and a tasting note when one is recorded.
- **"Clear history" footer action.** Confirms via `<melitta-confirm>` and calls the P3a `melitta_barista/sommelier/history/clear` endpoint with `keep_favorited=true`. Sessions referenced by favorites are preserved (server-enforced); the returned `{cleared}` count drives the success toast.

### Notes
- Star rating uses the shared `<melitta-star-rating>` component introduced in 0.60.0; ratings written from the history view are immediately reflected because the modal re-loads `history/list` on every open.
- This release closes the P3 surface. Further Sommelier ergonomics (e.g. cross-session recipe search, comparative ratings across capability snapshots) are out of scope.

## [0.60.0] — 2026-05-25

### Added (frontend, consumes P3a ratings backend)
- **`<melitta-star-rating>` shared component** (`www/components/ui/`). Five clickable stars; emits `rate` with the chosen value and `unrate` when the user clicks the currently-active star. Supports a `readonly` mode for view-only contexts. Used in both Generate result cards and the Favorites modal.
- **`<melitta-sommelier-favorites>` modal.** Opened from the new "★ Favorites" button in the Sommelier header. Lists saved favorites with inline rating, optional tasting note (gated by an existing rating, matching the P3a `favorites/update` contract), rename / edit description (inline form), brew (routes through the wizard with `source="favorite"`), and delete (with confirm). Loads `favorites/list` on every open.
- **Star rating inline in Generate result cards.** A rated recipe persists across history reads — `_ratings` is a local optimistic cache layered on top of the server-supplied `rating` field.

### Changed
- **`<melitta-brew-wizard>` accepts `source` ("generated" | "favorite") + `sourceId`.** When `source === "favorite"`, the brew call goes through `melitta_barista/sommelier/favorites/brew` (which increments `brew_count`) instead of `sommelier/brew`. Backward-compat: when unset, defaults to "generated" / `recipe.id`.

### Notes
- History view (`<melitta-sommelier-history>`) and full Sommelier sub-tabs refactor are deferred to **P3c**. The Favorites modal is a pragmatic shortcut that exposes the P3a backend without restructuring the Sommelier tab.
- Tasting notes still require a rating to exist first (P3a constraint). The Favorites modal surfaces this with `favorites.note_needs_rating` hint when the favorite has no rating yet.

## [0.59.0] — 2026-05-25

### Added (backend, foundation for History/Favorites/Ratings UI in P3b)
- **`recipe_ratings` table (DB v5 → v6).** Stores 1..5 star ratings + optional tasting notes, keyed by `(target_id, target_type)` where `target_type ∈ {"generated", "favorite"}`. Lets the same recipe carry a separate "first impression" (on the generated row) and an "after saving" rating (on the favorite copy). Validation: range check via CHECK constraint + Python `ValueError` for defensive depth.
- **`melitta_barista/sommelier/recipe/rate`** and **`recipe/unrate`** WS endpoints. Upsert / delete a rating for any recipe. Voluptuous validation: rating in 1..5; target_type in the two enum values. `require_admin`.
- **`melitta_barista/sommelier/favorites/update`** WS endpoint. Patch a favorite's name, description, or note. Notes route through the unified `recipe_ratings` table (a rating must exist first; the UI is expected to combine the two operations in P3b).
- **`melitta_barista/sommelier/history/clear`** WS endpoint with `keep_favorited` (default `true`) — protects sessions whose recipes are referenced by `favorites.source_recipe_id`. Relies on the existing `ON DELETE CASCADE` between `generation_sessions` and `generated_recipes` (`PRAGMA foreign_keys=ON` already set in `async_setup`). Returns `{cleared: <count>}`.
- **`async_list_favorites`, `async_get_favorite`, `async_list_history`, and `async_get_recipe` now expose `rating` + `note`** via a `LEFT JOIN recipe_ratings`. Both `target_type='favorite'` and `target_type='generated'` JOINs supported. Recipes without ratings return `null` for both fields.

### Notes
- Frontend components (favorites view, history view, `<melitta-star-rating>`) are out of scope here — P3b will consume these endpoints.
- The `brew_count` regression (wizard path calls `/sommelier/brew` instead of `/favorites/brew`, so the favorite's `brew_count` never increments after the initial add) is also deferred to P3b — it requires wizard-level wiring that depends on the favorites view UX.

## [0.58.0] — 2026-05-25

### Added
- **`<melitta-brew-wizard>` component (R3).** Pre/during/post-brew wizard opened from "Brew this" in the Sommelier panel. The pre phase lists user prep steps (cup choice, ice, additive measurement) and the chosen cup type. The during phase fires the BLE brew, animates a progress bar driven by an estimated duration, and polls `melitta_barista/status` every 2 s to auto-advance when the machine returns to READY. The post phase lists finishing-touch steps and the recipe's `extras.instruction` text. Cancel / "I'm done" buttons keep the wizard usable in offline / no-poll modes.
- **`RecipeStep.phase` field** (`Literal["pre", "during", "post"]`, default `"during"`). LLM is now explicitly instructed to phase-tag each step; the wizard splits steps by this field. Recipes without phase fields (legacy or LLM oversight) all render as during-brew steps, matching prior behaviour.
- **Estimated brew duration heuristic** (`estimateBrewSeconds(recipe)` in `melitta-brew-wizard.js`). Frontend-side formula: `8 s warmup + portion_ml / 50 × 5 s` per phase. Conservative; drives the progress bar saturation at 95 % and the manual-finish-button timeout (`estimated + 30 s`).

### Changed
- **Sommelier "Brew this" no longer fires `melitta_barista/sommelier/brew` directly.** Instead it opens the wizard, which orchestrates the brew call when the user clicks "Start brewing" in the pre phase. The user-facing latency between click and machine starting is roughly the same; the wizard adds explicit phases and the option to back out before any BLE write fires.
- **`ws_favorites_add` now stores `machine_phases`** alongside legacy `component1`/`component2` (closes a pre-existing bug where `ws_favorites_brew` read `machine_phases` but `ws_favorites_add` only stored the old shape).

### Notes
- WS status pushing is NOT in scope — the wizard polls `melitta_barista/status` instead. A future P2c / P3+ may introduce a proper push subscription if poll-latency becomes a UX bottleneck.
- Sequential per-phase brewing with explicit user-action pauses between machine phases is deferred to **P2c**. For multi-phase recipes today, the BLE protocol still fires both components in a single `brew_freestyle` call (the machine sequences them internally without exposing the gap to the host).
- TTS / voice-prompted wizard steps are explicitly out of scope.

## [0.57.0] — 2026-05-25

### Changed (data-model migration)
- **`GeneratedRecipe.component1` + `component2` replaced by `machine_phases: list[MachinePhase]`** (length 1..2). Each `MachinePhase` carries a `component: RecipeComponent` and a `user_action_before: list[RecipeStep]` (always empty in P2a; populated by the Brewing Wizard in P2b). The pydantic model change automatically propagates into the LLM-prompt JSON Schema and the validation/retry loop.
- **DB schema v4 → v5.** New `machine_phases TEXT` column on `generated_recipes` and `favorites`. Migration populates the new column from existing `component1`/`component2` via SQLite JSON1 (`json_array`/`json_object`). Old columns stay NOT NULL for cross-version readability; new rows write synthesized placeholders. Physical column drop is a P3+ housekeeping task.
- **`_brew_recipe_components`** now takes `phases: list[dict]` instead of `comp1`/`comp2` kwargs. The BLE call (`client.brew_freestyle(component1=..., component2=...)`) is unchanged — the helper unpacks the first two phases and synthesizes a `"none"`-process component2 for single-phase recipes. The `portion // 5` conversion and `blend`-alternation between components are preserved.
- **LLM prompt:** example JSON, rules block, and capability-instruction text reference `machine_phases` instead of `component1/2`. Single-phase brews are encouraged by default; a second phase is added only when a single-phase brew can't achieve the result.

### UI
- **`melitta-sommelier.js`** iterates `r.machine_phases` to render per-phase chips. A fallback to `r.component1`/`r.component2` for legacy WS responses is kept until P2b.

### Notes
- BLE-protocol layer (`client.brew_freestyle` signature) is untouched in P2a. Sequential brewing with explicit user-action pauses between phases is the Brewing Wizard's job (P2b), not the BLE layer's.
- Read path for favorites / history synthesizes `machine_phases` from `component1`/`component2` for legacy rows; it still returns `component1`/`component2` for backwards-compat readers (the frontend keeps its fallback).
- 19 new tests: `test_machine_phases.py` (7 on the pydantic model) + updates to `test_ai_recipes.py` / `test_sommelier_db.py` / `test_capabilities_db.py`.

## [0.56.0] — 2026-05-25

### Added
- **Capability-driven LLM prompt (R4).** `_build_prompt` now accepts a `LiveCapabilities` object (from P1a) and emits the `## Machine Capabilities` section from the connected machine's actual supported set. Explicit instruction tells the LLM to ignore JSON-schema values not listed in this section. `ws_generate` fetches caps from the DB cache, falls back to live-derive, then to `None` (legacy universal block).
- **Per-request `agent_id` override (B7).** `melitta_barista/sommelier/generate` WS now accepts an optional `agent_id` field; `_resolve_agent_id` is the single source of truth (msg.override > settings > HA default).
- **Optional `entry_id` for `prompts/preview`.** Capability-aware prompt preview for development.

### Changed
- **Pydantic is now a mandatory dependency (B8).** `pydantic>=2.0` added to `manifest.json`. The `try/except ImportError` soft-degrade path is removed; `_PYDANTIC_OK` flag retired; `_schema_for` and `_validate_parsed` no longer guard against missing pydantic. In HA's runtime, pydantic v2 is always available — the degrade path was dead code.
- **Eager `sommelier_db` initialization in `async_setup_entry`.** Fixes the P1a probe-on-connect caveat: capabilities cache is now populated on the very first handshake rather than only after a panel open.

### UI
- **`melitta-sommelier.js` capability-aware temperature chips.** Chips for `hot` / `iced` dim and disable when not supported by the connected machine. Tooltip ("Not supported by this machine" / "Не поддерживается этой машиной") explains why. New state field `_capabilities` populated via `melitta_barista/capabilities/get` at mount.

### Notes
- Dynamic-per-request pydantic models (`Literal[*supported_processes]`) explicitly deferred: the JSON schema still enumerates the universal set, but the Machine Capabilities section explicitly instructs the LLM to ignore schema values not listed. Pragmatic — re-evaluate if LLM compliance turns out poor.
- Cup-size / mood / occasion / caffeine / dietary chips are UI-only conventions (no direct machine mapping) — not gated against capabilities.

## [0.55.0] — 2026-05-25

### Added
- **`LiveCapabilities` data model** (`custom_components/melitta_barista/capabilities.py`) — typed view of machine capabilities (supported processes / intensities / aromas / temperatures / shots, per-process portion limits, forbidden combinations). Frozen dataclass with `to_json()` / `from_json()` round-trip; rejects unsupported `schema_version` early.
- **`machine_capabilities` SQLite table** (sommelier DB schema v3 → v4). Cached per `entry_id` with `probed_at` UTC timestamp. New `async_get_capabilities(entry_id)` / `async_save_capabilities(entry_id, json_payload)` methods on `SommelierDB`.
- **`derive_capabilities(client)` builder** — produces `LiveCapabilities` from a client's static brand profile + `const.py` enum maps. `strength_levels=3` → center three intensities (`mild/medium/strong`); `strength_levels=5` → all five. `has_aroma_balance=False` → only `standard` aroma. Per-process portion limits use a global default (`{min: 0, max: 250, step: 5}`) for P1a.
- **Probe-on-connect hook** — `_make_capabilities_probe_callback` factory in `__init__.py` wired via `client.add_connection_callback`; on a successful handshake it derives + saves capabilities. Errors during derive / save are swallowed and logged.
- **`melitta_barista/capabilities/get` WebSocket endpoint** — returns cached blob with `source: "cache"`, or falls back to on-the-fly derive with `source: "derive"`, or `send_error` if neither is possible. Corrupt or future-schema cached payloads are gracefully detected and fall through to the live-derive path (regression test included).

### Notes (P1a scope)
- No new BLE round-trips. `forbidden_combinations` is always `[]`; real values arrive when protocol observation produces them.
- **Known caveat:** `sommelier_db` is initialized lazily on the first WS call — at `async_setup_entry` time it is usually `None`, so the probe-on-connect callback silently no-ops on a fresh setup and the cache is populated only after the panel opens. The fallback-to-derive path in the WS endpoint covers this transparently. Eager DB initialization is deferred to P1b (or a follow-up hotfix).
- LLM-prompt rewrite (R4 prompt section), B7 (agent_id override in `/generate`), B8 (pydantic mandatory dep), UI gating, and per-process portion limits are explicitly out of scope — see `docs/SOMMELIER_TZ_DRAFT.md` §13 / §14.

## [0.54.1] — 2026-05-25

### Changed
- **HACS download counter enabled.** `hacs.json` now declares `zip_release: true` + `filename: melitta_barista.zip`. New `.github/workflows/release.yml` builds a release zip and uploads it as an asset on every published GitHub Release. Previously HACS fell back to the GitHub Contents API (which doesn't increment any download counter), so the badge in HACS UI always showed 0 / "unknown". Existing installs are unaffected — HACS will simply pull the zip on next update instead of fetching files individually. Old releases (pre-0.54.1) still have no asset and will not retroactively gain a counter; only v0.54.1+ will count.

## [0.54.0] — 2026-05-25

### Added
- **`<melitta-confirm>` shared dialog component** — promise-based replacement for native `window.confirm()`. All deletion dialogs in Beans / Add-ins now use it with destructive-styling.
- **`design-tokens.js` + `sharedStyles` export from `lit-base.js`** — spacing scale, radius, focus-ring tokens for uniform component styling. Foundation for the wider design-system work in upcoming P1–P5 plans.
- **`<melitta-system>` container** with sub-tabs **Status / Settings / Diagnostics / Machine recipes** — collapses what used to be four top-level navigation buttons into one.

### Changed
- **Top tabs reduced to four** in new order: **Sommelier / Beans / Add-ins / System**. Sommelier becomes the primary (first) tab; everything machine-side / configuration / diagnostic lives behind a single "System" tab in last position.
- **Hopper-assignment flash messages** are now localized through `i18n.js` (en + ru) — previously the strings were hardcoded in Russian regardless of the user's HA language.
- **LLM autofill `via:` debug info** moved into a collapsed `<details>` block — production UI no longer leaks the agent backend name as a top-level label on the bean modal.

### Removed
- Native `window.confirm()` from `melitta-beans.js` (2 sites) and `melitta-additives.js` (1 site).
- `tabs.status`, `tabs.diagnostics`, `tabs.recipes`, `tabs.settings` i18n keys (replaced by `tabs.system` + `system.subtabs.*`).

### Notes
- No backend, BLE, LLM, or schema changes — pure frontend refactor. First plan in the P0–P5 series.

## [0.53.4] — 2026-05-24

### Changed
- **Migrated AES from `pycryptodome` to `cryptography` (pyca).** Both AES-CBC call sites (`protocol._derive_rc4_key`, `brands/melitta._derive_rc4_key`) now use `cryptography.hazmat.primitives.ciphers`. Ciphertext bytes are bit-identical to the previous implementation (verified against the pycryptodome reference). Dependency change: `pycryptodome>=3.0.0` → `cryptography>=41.0.0`. `cryptography` is already a transitive dependency of Home Assistant core, so installed footprint shrinks slightly.

### Fixed
- **Bandit `B413` and 5×`B608` warnings.** B413 (PyCrypto deprecation) is now moot after the cryptography migration. B608 (SQL injection) findings on `panel_api.py` were false positives — column names come from a literal whitelist in the same function, and `{table}` is a closure-captured string literal (`"producers"`, `"syrups"`, `"toppings"`). Annotated with `# nosec B608` plus a comment explaining the invariant. The lint job now passes end-to-end.

## [0.53.3] — 2026-05-24

### Fixed
- **CI green again.** Three classes of breakage that piled up since 0.53.0:
  - **Hassfest translations rejected `<machine>` placeholders** in `clock_entity_migration.description` (parsed as HTML) — replaced with plain `MACHINE` across `strings.json` and 29 translation files.
  - **Hassfest rejected `target.device` filter** on the `sync_clock` service (HA no longer supports device filters on `target` since the validator started calling `raise_on_target_device_filter`). Moved to an optional `fields.device_id` with a proper `device` selector; behavior is unchanged (omit the field to sync all configured machines, same as `repair_connection`).
  - **Tests workflow missed `aiosqlite`** — `tests/test_sommelier_db.py` and `tests/test_review_fixes.py` failed at collection. Added it to the test job's pip install line. `aiosqlite` was already declared in `manifest.json` requirements; only the CI runner needed it.
- **Ruff lint cleanup:** dropped unused `datetime.time` import in `__init__.py`, unused `async_generate_recipes` import in `sommelier_api.py`, and split two `;`-joined statements in `button.py` (E702).

## [0.53.2] — 2026-05-22

### Fixed
- **Recorder warning + DB bloat from oversized select attributes** (#13). The Profile select exposes the full DirectKey recipe table (`directkey_recipes`) and the Recipe select the full base-recipe table (`recipes`) live for the companion card/app — but these exceed the recorder's 16 KB per-state attribute cap, triggering `State attributes ... exceed maximum size` warnings and DB performance degradation. Both bulk attributes are now marked `_unrecorded_attributes`, so the recorder skips them while the live state attribute is preserved — the card and app keep working unchanged, and the small per-recipe / `active_profile` fields stay in history.

## [0.53.1] — 2026-05-22

### Fixed
- **False `pairing_wedged` repair when the machine is powered off** (issue #12). The reconnect loop counted every failed connect toward the wedge threshold regardless of whether the device was actually advertising. Turning the machine off between uses (a common pattern) racked up 5 consecutive failures and raised a bogus repair, plus spammed the log with hundreds of "Connection failed" errors. The loop now consults `bluetooth.async_address_present`: a device that is not advertising is treated as powered off / out of range — the connect attempt is skipped, the wedge counter is cleared, and the loop waits quietly for the next advertisement. A genuinely wedged device keeps advertising, so real wedges are still detected and recovered.

## [0.53.0] — 2026-05-21

### Added
- **Serial number sensor** (`sensor.<machine>_serial`) — read once on connect via the `HL` command (20-byte ASCII response). Confidence 0.95 from the Nivona protocol spec; size matches Melitta's frame registration, so the same opcode is expected to work across both brands.
- **BLE frame log in diagnostics** — `diagnostics.py` now exposes two new sections:
  - `ble_trace.recent_frames_raw` — last 100 raw notifications (pre-decryption hex preview, captured on every BLE event by `ble_client._on_notification`).
  - `ble_trace.frame_log_decoded` — last 200 decoded frames (post-RC4 payloads), captured by `protocol._dispatch_frame`. Useful for inspecting `HF` (16 B) / `HQ` (15 B) / `HP` (14 B) — three opcodes whose payload semantics remain unresolved across all public sources.
- DEBUG log line `[FRAME-UNH] cmd=X len=N hex=...` for any decoded frame the integration does not actively handle (i.e. not `HX`, not `HU`, not a response to a pending request). Activated via the standard HA "Enable debug logging" toggle on the integration page.

### Changed
- `device.serial` is now included in the diagnostics download.

## [0.52.0] — 2026-05-21

### Added
- New `time.<machine>_clock` entity — display and set the machine RTC directly from HA.
- New service `melitta_barista.sync_clock` — push HA local time to the machine on demand.
- Auto-sync coordinator: writes the machine clock on BLE reconnect (with 12 h throttle) and once per day at a configurable time (default 03:17).
- Options Flow → Advanced: `auto_sync_clock` (toggle), `auto_sync_drift_minutes` (skip threshold), `auto_sync_daily_time` (HH:MM).

### Changed
- `docs/PROTOCOL.md` clock setting notes filled in with verified semantics.

### Removed (BREAKING)
- `number.<machine>_clock` and `number.<machine>_clock_send` (introduced in 0.20.0). They were two writable sliders for what is really a read-only / write-only pair. Migration: use the new `time` entity for manual changes, or the `sync_clock` service for automations. A Repair card surfaces on upgrade.

## [0.51.0] — 2026-05-15 — Pairing recovery — GA

Stable release of the pairing-recovery work that ran through beta.1
through beta.7. Real-device validated: the Force re-pair (hard) flow
now resolves the issue #10 wedge end-to-end as long as the user
puts the coffee machine into pairing mode before pressing Submit.

> **Required ESPHome action**: pull the latest `esphome/ble-proxy-xiao-*.yaml`
> from this repo and flash via the ESPHome dashboard. The recovery
> flow depends on three actions that ship in the YAML:
> `clear_ble_bonds`, `disconnect_ble_peer`, and the `factory_reset`
> button. Without a reflash, the integration falls back to a partial
> recovery path.

### Summary of what made it into 0.51.0

- **Layered recovery** for the issue #10 wedge:
  - Settle delay between `pair=False` fail and `pair=True` so the ESP
    has time to release the BLE socket (beta.1).
  - `disconnect()` resets `_paired` (beta.1).
  - Counter for `_consecutive_connect_failures`; auto-trigger of the
    repair routine after 5 consecutive failures (beta.1).
  - Soft repair: reload the ESPHome ConfigEntry to evict the cached
    `BLEDevice` from `habluetooth._previous_service_info` (beta.1).
  - `melitta_barista.repair_connection` service for manual trigger
    (beta.1).
  - Options Flow → **Repair connection** menu entry (beta.2).
  - `clear_ble_bonds` action in `esphome/ble-proxy-xiao-*.yaml` to
    wipe the ESP NVS bond table (beta.3).
  - Robust proxy-entry matcher: tries `entry.unique_id`,
    `entry.data["bluetooth_mac_address"]`, `device_info.bluetooth_mac_address`,
    `device_info.mac_address`, and `device_info.name` (beta.4 + beta.5).
    The critical fix in beta.5: ESP32 chips have separate WiFi and
    BLE MACs (BT = WiFi + 2); we were comparing the wrong one.
  - Options Flow → **Force re-pair (hard)** menu entry — wipes ESP
    bond, surgical GAP disconnect of the peer, reloads the proxy
    entry, and re-arms the reconnect loop (beta.4).
  - `disconnect_ble_peer` action in the proxy YAML to drop a stuck
    `state: ESTABLISHED` connection slot (beta.6).
  - UI strings now spell out the manual pairing-mode step
    everywhere recovery is referenced (beta.7).
- **Pairing-wedged Repair Issue** in HA UI with a learn-more link
  to issue #10 and a 3-step recovery list. Auto-cleared on the next
  successful connect.
- **Best-practice ESPHome YAML extras** carried over from the
  official `esphome/bluetooth-proxies` reference: `factory_reset`
  button (nuke the whole NVS), `safe_mode` button (recovery boot),
  `BLE bonds` text sensor (live count from
  `esp_ble_get_bond_device_num`), `min_version: 2025.8.0`,
  `esp32_ble.max_connections` raised to match `connection_slots+1`.
- **Tests**: `tests/test_pairing_recovery.py` covers the settle
  delay, disconnect hygiene, counter increment/reset, repair
  callback firing at threshold, threshold=0 off switch, missing
  callback safety, public callback API, and all four Options Flow
  abort paths (repair done / partial / no-action / failed). **759
  total tests passing**.
- **Documentation**: README now has an `ESPHome BLE proxy
  (recommended transport)` section listing the YAML reference and
  the four buttons it provides, plus a `BLE pairing recovery`
  section walking through the three escalating recovery paths.
  Pairing instructions in `Step 3` are updated to spell out that
  pairing mode is a one-time step per central.

### Migration / upgrade notes

If you're coming from any `0.51.0-beta.*`: nothing to change beyond
the manifest version bump. If you're coming from 0.50.x: pull the
latest `esphome/ble-proxy-xiao-*.yaml` and reflash your proxy as
described above. Without it, Force re-pair (hard) and the
`melitta_barista.repair_connection` service will fall back to the
soft path (reload ESPHome entry only) — that's enough for cache
eviction but does not wipe the ESP NVS bond.

## [0.51.0-beta.7] — 2026-05-14 — Pairing-mode requirement documented

Real-device beta.6 testing surfaced the remaining piece: Melitta
firmware **requires the machine to be in explicit pairing mode**
(menu-activated) before it accepts SMP from a new BLE central. Even
with the ESP completely clean (no bond, no stuck slot, fresh BLEDevice
cache), the machine answers every SMP exchange with `auth fail
reason=82` until you put it into pairing mode via its UI. After a
single successful pair the bond persists on both sides — pairing
mode is not needed for subsequent reconnects.

This isn't a code bug, but the integration was silent about it.
beta.7 surfaces the requirement in every recovery message and in the
repair issue's UI.

### Changed

- `repair`, `full_pair`, and the issue `pairing_wedged` descriptions
  now spell out the pairing-mode step (en + ru, plus the other 27
  locales carrying the en text as placeholder).
- `full_pair_done` abort text now says "IMPORTANT: now put the
  machine into pairing mode within 1 minute" so the user knows the
  next move.
- `pairing_wedged` issue body lists the full 3-step recovery:
  pairing mode on the machine → call repair_connection → bond
  persists, done.

## [0.51.0-beta.6] — 2026-05-14 — Surgical GAP disconnect on stuck slot

Production log on beta.5 showed `auth fail reason=82` (SMP rejection
from the machine) **continuing** even after `clear_ble_bonds` wiped
both ESP NVS bonds. The bluetooth_proxy connection slot then sits in
`state: ESTABLISHED` and the next line in the log is
`Connection request ignored, state: ESTABLISHED` — every subsequent
client-side connect is dropped on the floor.

Wiping the bond table is necessary but not sufficient. We also need
to drop the half-closed GAP link so the next pair=True actually
opens a new SMP exchange.

### Added

- New ESPHome action `disconnect_ble_peer` in both
  `esphome/ble-proxy-xiao-c6.yaml` and `esphome/ble-proxy-xiao-s3.yaml`.
  Takes a `peer_mac` string variable and calls
  `esp_ble_gap_disconnect(bd_addr)` on it. Logs the API return code.
- `_async_force_repair` now calls
  `esphome.<proxy>_disconnect_ble_peer` with the machine's MAC
  immediately after `clear_ble_bonds`. New result key
  `peer_disconnected` indicates whether the GAP-disconnect ran.

### What this fixes in the user-visible flow

Before beta.6, after issuing "Force re-pair" you would see in the ESP
log:
```
[ble_bonds] Removed 2 bonded device(s)
... auth fail reason=82
[bluetooth_proxy] Connection request ignored, state: ESTABLISHED
```
… in a tight loop. After beta.6 (with the new YAML action flashed):
```
[ble_bonds] Removed 2 bonded device(s)
[ble_disconnect] GAP disconnect F1:2C:72:3F:75:ED -> 0
... clean reconnect with fresh SMP
```

### Note for users hit by issue #10

If the machine ALSO holds a stale bond (which the log above suggests),
clearing the ESP side alone isn't enough — the machine refuses every
new SMP request because its remembered LTK doesn't match what we
present. Look in the machine menu for **Settings → Bluetooth →
Disconnect / Reset connection** to forget its side. Some Melitta TS
firmwares require a power-cycle of the machine after that for the
state to actually persist.

## [0.51.0-beta.5] — 2026-05-14 — Proxy matcher: use BT MAC (not WiFi MAC)

### Fixed

beta.4's robust matcher still missed every proxy in the wild because
of a subtle ESP32 hardware detail: **every ESP32 has separate WiFi
and Bluetooth MAC addresses (BT = base + 2)**. The scanner's
`source` field is the BT MAC. ESPHome's `device_info.mac_address`
and `entry.unique_id`, however, are the WiFi MAC. So the matcher
compared two MACs that were always different by a fixed offset and
never lined up.

The matcher now also checks:
- `entry.runtime_data.device_info.bluetooth_mac_address` — the
  ESPHome-reported BT MAC at runtime.
- `entry.data["bluetooth_mac_address"]` — the value ESPHome
  persists at discovery/reconfigure time (manager.py:563-567 in
  HA core), covers the case where the proxy entry is mid-setup
  and runtime_data isn't populated yet.

Verified against Home Assistant developer docs (`scanner.source`
is documented as "source MAC address") via context7. Confirmed
no public API exposes a scanner → config_entry_id reverse lookup;
matching by MAC keys is the supported pattern.

## [0.51.0-beta.4] — 2026-05-14 — Force re-pair option + robust proxy matcher

### Added — Options Flow "Force re-pair (hard)"

A second menu entry under Configure that does, in order:

1. Disconnect the Melitta client.
2. Find the ESPHome proxy ConfigEntry that owns the scanner for this peer.
3. Call the `esphome.<proxy_name>_clear_ble_bonds` service if it exists
   (i.e. the user has wired the `clear_ble_bonds` action from
   `esphome/ble-proxy-xiao-c6.yaml` — beta.3 introduced this).
4. Reload the proxy ConfigEntry (evicts HA-side cached BLEDevice).
5. Re-arm the reconnect loop.

5 localised abort outcomes: `full_pair_done`, `full_pair_partial`,
`full_pair_no_action`, `full_pair_local_only`, `full_pair_failed`.

### Fixed — Proxy-entry matcher kept missing valid proxies

`_find_proxy_entry_for_address` used to compare `scanner.source` only
against `entry.unique_id`. When the ESPHome entry was added via zeroconf
discovery (or reconfigured later) the `unique_id` could drift from the
proxy's actual MAC, and the integration would return None even though
the ESPHome proxy clearly was advertising the machine. Result: every
Repair / Force re-pair call fell back to the local-adapter path with the
"No ESPHome proxy found" abort, even though one existed.

The matcher now compares the (normalised) scanner source against three
keys per ESPHome entry: `entry.unique_id`,
`entry.runtime_data.device_info.mac_address`, and
`entry.runtime_data.device_info.name`. Also logs the source UUID and the
candidate count at DEBUG so a future mismatch can be diagnosed without
a code change.

### Tests

5 new tests in `tests/test_pairing_recovery.py` for the Force re-pair
abort paths (done / partial / no-action / local-only / failed). 759
total passing.

## [0.51.0-beta.3] — 2026-05-14 — ESP-side bond clearing recipe

beta.1 and beta.2 reload the ESPHome scanner to evict the **HA-side**
cached `BLEDevice`. That fixes the wedge when the cache points at a
dead `source` UUID — but it does NOT touch the **ESP-side bond
table** (LTK stored in NVS flash on the proxy). If the ESP firmware
holds a stale bond key and the machine reset its internal SMP state,
the proxy keeps presenting an LTK the machine refuses to acknowledge,
and every `pair=True` lands on the same rejection.

`client.unpair()` only fixes that when the ESP firmware was built
with the unpair feature flag. Many community-built proxies don't
have it, so the call returns `BluetoothConnectionDroppedError` and
the bond stays forever.

### Added

- `esphome/ble-proxy-xiao-c6.yaml` now ships an `api.actions:` block
  with a `clear_ble_bonds` action that calls
  `esp_ble_remove_bond_device` for every entry in the ESP bond
  table. After flashing the proxy, HA exposes the action as the
  service `esphome.<proxy_name>_clear_ble_bonds`. Calling it once
  resets the NVS bond table; the next `pair=True` triggers a fresh
  SMP exchange and the machine creates a new bond from scratch.

### Changed

- `_try_unpair` now logs a WARNING (with concrete service name) when
  `client.unpair()` fails on the ESPHome path, pointing users at the
  `clear_ble_bonds` workaround instead of just hiding the failure in
  DEBUG.

### Recovery workflow when handshake stays wedged

If the integration's auto-recovery / `repair_connection` does not
fix the red-indicator state, the bond on the ESP is the next thing
to clear:

1. Update your ESPHome proxy YAML with the `clear_ble_bonds` action
   from `esphome/ble-proxy-xiao-c6.yaml` (api → actions block).
2. Flash the proxy (OTA from the ESPHome dashboard is fine).
3. Developer Tools → Services →
   `esphome.<your_proxy_name>_clear_ble_bonds` → Call.
4. Settings → Devices & Services → Melitta entry → Configure →
   Repair connection → Submit.

Step 3 wipes the NVS bond on the ESP; step 4 evicts the cached
BLEDevice and forces our `_connect_impl` to start from `pair=False`
(which now fails fast because there is no bond) then escalate to
`pair=True`, this time provoking a fresh SMP exchange.

## [0.51.0-beta.2] — 2026-05-14 — Repair step in Options Flow

Builds on beta.1. Same recovery routine, now also exposed as a UI
button — no need to remember the service name.

### Added

- "Repair connection" entry in the integration's Options Flow menu
  (Configure → Repair connection → Submit). Calls the same
  `_async_repair_pairing` routine that the service and the auto-
  trigger use. Three abort outcomes with localised messages:
  `repair_proxy_reloaded` (an ESPHome entry was reloaded),
  `repair_local_reconnect` (no proxy found — fell back to a local
  disconnect + advertisement wait), `repair_failed` (the routine
  raised — see HA logs).
- 4 new tests in `tests/test_pairing_recovery.py` covering the three
  abort paths and the initial form display.

### Translations

`step.repair` and `abort.repair_*` blocks added to all 29 locale
files (en + ru real translations; 27 placeholders carry the EN text).

## [0.51.0-beta.1] — 2026-05-14 — Pairing recovery (PRE-RELEASE)

Pre-release. Must be installed explicitly from HACS by toggling
"Show beta versions" on the integration page. Targets issue #10:
after long BLE silence the encrypted HU handshake gets stuck, the
machine displays a red Bluetooth indicator, and the only known
recovery used to be removing and re-adding the integration via UI.

This release adds a 3-layer fix; each layer is independent so partial
backports work.

### Root cause (full write-up: see `docs/PAIRING.md` after this commit)

habluetooth's `BaseHaRemoteScanner._previous_service_info` caches the
`BLEDevice` instance per peer address with a frozen
`details["source"]` / `details["address_type"]`. After long quiet
periods the cached `source` can point at a dead scanner UUID (e.g.
the ESP proxy reconnected to HA between sessions), or the address
type can drift after the machine resets its bond. Every reconnect
attempt then hands `establish_connection` that stale BLEDevice, the
HU response never finds its way back, and the machine displays red.

`hass.config_entries.async_reload(melitta_entry)` does NOT clear that
cache (the scanner lives on the **ESPHome** entry, not ours).
Deleting the integration entry happens to work because by the time
the user finishes the config flow, the long pause has caused
`_previous_service_info[address]` to expire on its own — the next
advertisement then builds a fresh BLEDevice with current `source` /
`address_type`.

### Added — Layer 3 (root-cause recovery)

- `melitta_barista.repair_connection` service. Walks every melitta
  config entry, finds the ESPHome config entry that owns the proxy
  scanner for that peer (via `bluetooth.async_scanner_devices_by_address`
  and matching `scanner.source` against `ConfigEntry.unique_id`), and
  reloads the ESPHome entry. The reload unregisters the scanner,
  which is the only HA-public path that evicts the cached BLEDevice.
  The next advertisement builds a fresh one and the next `pair=True`
  succeeds in ~1 s.
- Automatic recovery: after `DEFAULT_REPAIR_AFTER_FAILURES = 5`
  consecutive failed `connect()` calls the reconnect loop calls the
  same routine without user action. Counter resets on every successful
  connect; threshold can be set to 0 to disable (useful for debugging
  the underlying transport).
- Repair issue (`pairing_wedged_<address>`) raised at the same
  threshold so the user gets a UI card explaining what happened and
  pointing at issue #10 for logs. Auto-cleared on the next successful
  connect.
- New constants in `const.py`: `DEFAULT_PAIR_SETTLE_DELAY = 2.0`,
  `DEFAULT_REPAIR_AFTER_FAILURES = 5`.

### Added — Layer 1 (paint-the-bike-shed timing fix)

- 2-second settle delay in `_connect_impl` between a failed
  `pair=False` handshake and the next `pair=True` attempt, and again
  between `_try_unpair()` and the final `pair=True`. Without this gap
  the ESP proxy / BlueZ does not always release the previous BLE
  socket before we re-pair, manifesting as the 60-second
  `TimeoutAPIError waiting for BluetoothDevicePairingResponse` users
  see in logs. Configurable via the new `pair_settle_delay` ctor arg.

### Added — Layer 2 (hygiene)

- `disconnect()` now resets `self._paired = False`. Field was tracked
  on connect but never cleared — kept the bond-state mental model
  inconsistent across reconnects.

### Fixed

- Reconnect-loop now tracks `_consecutive_connect_failures` and
  resets it on every successful `connect()`. Surfaces as a public
  property `consecutive_connect_failures` for diagnostics.

### Tests

- `tests/test_pairing_recovery.py` — 10 new tests covering the settle
  delay invariant (no sleep on success, sleep between attempts on
  failure), disconnect hygiene, counter increment / reset, callback
  firing exactly at threshold, threshold=0 off switch, missing
  callback not crashing the loop, and the public callback API.

### Known limitations

- The recovery does not auto-tune itself: if the ESPHome proxy keeps
  re-acquiring stale state quickly (e.g. flaky power), the user will
  see repeated reload spikes. The `repair_after_failures` knob is
  exposed for diagnostics but not yet wired through Options Flow.
- The repair routine assumes one ESPHome entry per peer address. A
  setup with multiple proxies all hearing the same machine will only
  reload the first matching entry per cycle.
- No `melitta_barista.repair_connection` target selector in the
  services dialog yet — the service applies to every melitta entry
  it finds.

## [0.50.2] — 2026-05-14 — hassfest validation fixes

Closes the three findings raised by the nightly `validate-hassfest`
CI job (these were broken before 0.50.1 — the audit didn't surface
them because the agents reviewed source, not CI logs).

### Fixed

- **`strings.json` + 29 translation files**: removed the top-level
  `"brand": {melitta, nivona}` block. HA's translations schema does
  not allow a top-level `brand` key, so every nightly hassfest run
  was failing. The block was dead code anyway — no
  `translation_key="brand"` lookup exists in the integration.
- **`manifest.json`**: declared `http` in `dependencies` since
  `__init__.py:131` calls `hass.http.async_register_static_paths()`
  to mount the panel SPA assets.

## [0.50.1] — 2026-05-14 — Code review: critical security + crash fixes

Closes the full Critical list, all Important findings (including
the BLE test gaps and the i18n / enum-ID rendering), and the
Minor pack from the v0.50.0 code review. 740 tests passing
(was 721).

### Security / authorization

- Admin guard on every mutating WebSocket command in panel_api.py and
  sommelier_api.py (31 handlers in total) plus sensitive reads
  (diagnostics, diagnostics/llm_calls, prompts/list, prompts/preview).
  `require_admin=True` on the panel registration only hides the
  sidebar entry — without these guards, any authenticated household
  user could change the system prompt template, the LLM agent
  setting, or physically start a brew via `sommelier/brew`.
- `safeHttpUrl()` for the producer website link in melitta-beans.js so
  a `javascript:`-scheme URL stored in the producers table cannot
  execute when the row is rendered. `rel="noopener noreferrer"`
  added on the `target="_blank"` link.
- Allowlist for `sommelier/settings/set` and `sommelier/preferences/set`
  keys via `vol.In(...)`. Previously a caller could overwrite the
  shared `settings.schema_version` row and break future migrations.
- WS error responses now log full exception traces server-side and
  return a static message to the panel — SQLite paths and
  conversation-integration error text no longer leak via WS errors
  for /producers/add, /beans/autofill, /sommelier/generate,
  /sommelier/brew, /sommelier/favorites/brew, /sommelier/presets/list.

### Correctness

- `sommelier/profiles/activate` no longer crashes. The handler called
  `db.async_activate_profile()` which never existed; renamed to the
  actual `async_set_active_profile()`. The DB method now returns
  `bool` so the not-found path is reported correctly to the panel.
- `ws_profiles_add`: the old code called the DB method with kwargs
  `name=..., preferences=...` that the method never accepted — every
  add-profile call raised TypeError. The handler now builds a single
  data dict so the DB row + the nested preferences both land in
  storage.
- Schema migration: SCHEMA_VERSION 2 → 3 with a new MIGRATE_V2_TO_V3
  block that adds a `steps TEXT` column to `generated_recipes` and
  `favorites`. The headline new feature of 0.50.0 — numbered
  preparation steps with dosages — used to be dropped on reload and
  on brew-from-favorite. Migration runner now applies each version
  step in sequence instead of a hardcoded v1→v2 jump.
- `ws_favorites_add` forwards `extras`, `steps`, and `cup_type` from
  the source recipe (they used to be dropped).
- `_find_client` resolves through entity registry's
  `config_entry_id` instead of substring-matching the BLE address
  into the entity unique_id — eliminates a multi-machine false
  match path.
- `async_unload_entry` removes all six services (`brew_freestyle`,
  `brew_directkey`, `save_directkey`, `reset_recipe`,
  `confirm_prompt`, `nivona_write_recipe_param`,
  `nivona_write_mycoffee_param`) on last-entry removal so they
  don't leak in HA's service registry until restart. Gated on
  `unload_ok` so we don't tear down shared state while platform
  entities are still live.
- `MelittaTotalCupsSensor.available` now gates on the connection
  state. It used to return `True` forever after the first read,
  masking BLE drops from automations.
- `services.yaml`: `enabled` and `icon` added to the
  `nivona_write_recipe_param` param_key selector (the voluptuous
  schema already accepted them).
- `config_flow`: explicit WARNING before the BleakScanner.discover
  fallback so ESPHome-proxy-only setups can see why discovery
  returned empty results.
- `_auto_confirm_task` is now tracked with a done callback so any
  unexpected exception surfaces in HA logs instead of being
  swallowed by asyncio's "task exception was never retrieved".

### Frontend i18n + a11y

- `melitta-sommelier.js`: every user-visible label, toast, and
  diagnostics message now resolves through `_t()` (was a mix of
  hardcoded Russian and English). Constraints block, Add-ins
  section, favorite toast, machine-line label, Why?-summary, plus
  the 26 enum values for cup-size / mood / occasion / temperature /
  caffeine / dietary preferences. en + ru keys added.
- `melitta-beans.js`: the autofill-brewing-recommendation note that
  gets appended into the bean composition field goes through
  `_t(beans.brewing_label)` — earlier it wrote literal
  "Заваривание: …" into the row regardless of locale.
- `melitta-additives.js` / `melitta-beans.js`: `confirm("Delete?")`
  now uses a localized prompt.
- `melitta-modal.js`: bind `aria-labelledby` on the dialog role to
  the `<h3>` id so screen readers announce the modal title.
- `melitta-diagnostics.js`: null out `this._timer` after
  `clearInterval`, same pattern as melitta-status.js.

### Tests / tooling

- `tests/test_review_fixes.py` — regression coverage for steps
  round-trip, `async_set_active_profile` return contract, settings
  / preferences allowlist schemas, `safeHttpUrl` text contract,
  and the service-removal block in __init__.py.
- `tests/test_protocol_full.py` — split-BLE-notification parser
  tests (frame cut mid-payload + byte-by-byte feed).
- `tests/test_ble_client.py` — `_auto_confirm_task` error-path
  coverage (BleakError caught; unexpected exception routed to the
  done-callback).
- `pyproject.toml` — pytest `timeout = 10` default per the project
  memory rule.

### Internals

- Removed dead `_CRC_TABLE` / `_compute_handshake_crc` from
  protocol.py; live code routes through `MelittaProfile.hu_verifier`.
- `_BleClientProtocol` typing stub now declares `_brand`,
  `_capabilities`, `_profile_callbacks`,
  `_recipe_refresh_callbacks`, and `record_error` so mypy sees the
  full mixin contract.
- `ai_recipes._validate_extras` drops the hardcoded English
  VALID_SYRUPS / VALID_TOPPINGS / VALID_LIQUEURS allowlists — the
  LLM is informed of the user's actual extras via the prompt and
  Pydantic accepts any string. Kept normalisation (strip, lowercase,
  64-char cap).
- Docstrings added on the 13 WS handlers in panel_api.py that were
  missing them.

### Known limitations carried over

- WS handlers' end-to-end coverage with a full HA harness is still
  out of scope; the new regression tests target DB invariants and
  text contracts of the critical fixes.
- Some recipe metadata (`estimated_caffeine`, badges) still
  formats English-only on the recipe card.

## [0.50.0] — 2026-04-27 — Admin SPA panel + AI Coffee Sommelier (alpha)

### Fixed

- **`sommelier/profiles/activate` no longer crashes.** Handler called
  `db.async_activate_profile()` which never existed; renamed to the actual
  `async_set_active_profile()`. The DB method now returns `bool` so the
  not-found path surfaces correctly to the panel.
- **Admin guard on all mutating WebSocket commands.** `require_admin=True`
  on the panel registration only hides the sidebar; the WS endpoints
  themselves had no authorization check, so any authenticated household
  user could change prompt templates, modify the LLM agent setting, or
  physically start a brew via `sommelier/brew`. Added
  `@websocket_api.require_admin` to all CRUD endpoints (producers, beans,
  syrups, toppings, tags, prompts, milk, hoppers, favorites, history
  config, profiles, sommelier preferences/extras/settings, generate,
  brew, autofill) plus sensitive read endpoints (diagnostics,
  diagnostics/llm_calls, prompts/list, prompts/preview). Read-only data
  endpoints (status, list, get) remain open.
- **`javascript:` URI XSS on producer website link.** Producer rows
  rendered `<a href=${p.website} target="_blank">` with a user-stored
  value. A `javascript:` website would execute in the HA frontend
  origin (with access to the WS token). Added `safeHttpUrl()` that
  only returns the URL if it parses as http/https, plus
  `rel="noopener noreferrer"` on the link.
- **Services no longer leak after the last entry is removed.** Six
  services (`brew_freestyle`, `brew_directkey`, `save_directkey`,
  `reset_recipe`, `confirm_prompt`, `nivona_write_recipe_param`,
  `nivona_write_mycoffee_param`) were registered with a
  `has_service` guard but never deregistered; when the last config
  entry was removed they stayed in HA's service registry until restart
  and would report `device_not_found` on every call.
- **Domain-wide teardown now gated on `unload_ok`.** Panel
  unregistration, Sommelier DB close, and service removal are now
  only executed when the platform unload actually succeeded — they
  used to run unconditionally even if entities were still live.

## [0.50.0] — 2026-04-27 — Admin SPA panel + AI Coffee Sommelier (alpha)

Big release. The integration now ships an in-HA admin panel with a full
Sommelier workflow that goes from "I have these beans + this milk +
this mood" to a one-tap brew on the machine.

### Added

- **Admin SPA panel** in the HA sidebar (`/melitta-barista`). Built on
  vendored Lit 3.x (no HACS-card side effects required), localised
  en + ru, panel module URL is cache-busted by the integration version.
- **Tabs**: Status (live BLE + machine snapshot), Diagnostics
  (ring-buffered errors + frames + recent LLM calls with full prompt /
  raw response / validation errors), Recipes (DirectKey viewer), Beans
  (producers + beans CRUD with dynamic flavour tags + LLM autofill +
  hopper assignment), Add-ins (syrups / toppings / milk via a unified
  modal), Sommelier, Settings.
- **AI Coffee Sommelier (alpha)** — end-to-end:
  - Rich form: allowed syrups / toppings / milk multi-selects, mood
    multi-select, cup size, occasion (auto-suggested from the local
    clock), temperature, caffeine, dietary multi-select.
  - Hybrid structured-output pipeline. SmartChain agents go through
    that integration's native JSON Schema mode (OpenAI Structured
    Outputs / Gemini responseSchema / Anthropic tool-use / Ollama 0.5+
    format=schema). All other agents go through a Pydantic-validated
    text-with-retry path with the JSON Schema appended to the prompt.
  - Locale-aware prompt: `hass.config.language` is forwarded so names /
    descriptions / step instructions come back in the user's language;
    enum values stay English so validation works regardless.
  - Recipes carry a complete numbered preparation sequence with
    explicit dosages (`1. Brew espresso — 30 ml`, `2. Add Vanilla
    syrup — 15 ml`, …) on top of the machine portion. ★ to favourite,
    "Brew this" to send the freestyle payload.
  - Diagnostic transparency: every LLM round-trip is recorded and
    visible in the Diagnostics tab (full prompt, response, validation
    errors, the path that handled it).
- **Beans LLM autofill**: brand + product + producer URL → strict
  Pydantic-validated bean fields (roast / bean_type / origin /
  origin_country / flavor_notes / composition / brewing recommendation).
  Any agent works; the URL is passed as a hint that browsing-capable
  agents follow on their own.
- **Settings tab**: LLM model picker, prompt template editor with
  inline placeholders documentation and "Preview assembled prompt"
  showing the exact text that will be sent.
- **Diagnostics tab**: ring-buffered BLE errors + notification frames
  with consecutive-duplicate collapsing, full LLM call log,
  configuration snapshot.

### Changed

- `sommelier_api` flavor_notes / milk_types schemas relaxed from
  hardcoded English vocabularies to free-form string lists. Russian /
  brand-specific names work everywhere now.
- Status tab uses a compact single-line label-value layout.
- Bean / producer / additive saves use explicit writable-field
  allowlists (no more spreading the whole record back, which tripped
  voluptuous extra-keys validation on `created_at` / `updated_at`).

### Fixed (along the way)

- HA WS payload key collision: `id` is the framework's message id,
  not a row pk. Renamed to `producer_id` / `additive_id` / `bean_id`
  in all panel-side schemas.
- Hopper dropdown lost selection on tab switch — Lit's `.value=` on
  `<select>` races with option rendering; switched to per-option
  `?selected`.
- `customElements.define()` registration race on panel re-import:
  every component file now guards with `if (!customElements.get(...))`.

### For users

- Backwards compatible. Existing config entries continue to work as
  before; the panel adds a sidebar entry and a couple of new
  per-domain WS commands.
- HACS will pick up 0.50.0 as a normal upgrade — the alpha label is
  scoped to the Sommelier feature, not the integration as a whole.
  Reload the browser hard (Ctrl+Shift+R) once after the update so the
  panel module URL refreshes its cache.

## [0.49.7] — 2026-04-27 — Fix HA startup blocking + bleak-retry-connector warning

Bug fix release addressing
[issue #9](https://github.com/dzerik/melitta-barista-ha/issues/9).

### Fixed

- **HA startup no longer blocked by our reconnect loop.**
  `async_setup_entry` was scheduling `_async_connect_and_poll` (an
  infinite `while True:` reconnect loop) via `hass.async_create_task`,
  which puts the task into `hass._tasks` and makes HA's bootstrap
  *wait* for it during the `EVENT_HOMEASSISTANT_START` → `running`
  transition. With an unreachable / slow machine this surfaced as the
  `Setup of domain melitta_barista is taking over 10 minutes` warning
  and a delayed "started" state for HA itself. Switched to
  `hass.async_create_background_task` which is exactly the right
  primitive for never-returning monitor loops.
- **`habluetooth.wrappers` warning eliminated.** The connect path used
  to fall back to raw `BleakClient.connect()` whenever the cached
  `BLEDevice` was `None` (typical on a cold boot via ESPHome BLE proxy)
  or whenever `establish_connection()` raised — which triggered:

  > `BleakClient.connect() called without bleak-retry-connector. For reliable connection establishment, use bleak_retry_connector.establish_connection().`

  Inside HA we now **always** route through
  `bleak_retry_connector.establish_connection()`. Without a cached
  `BLEDevice` the call raises `BleakError` so the reconnect loop waits
  for the next advertisement (via `set_ble_device` /
  `_reconnect_event`) instead of burning a 30 s timeout per attempt
  on a raw connect. Without `bleak_retry_connector` (tests / CLI
  scripts) the raw fallback still works.

### Why it matters

The two bugs reinforced each other: the raw `BleakClient.connect()`
fallback grabbed BlueZ slots without coordination, and the
`async_create_task` choice made the resulting slow connect cycles
visible as a startup hang. After this release a cold boot with the
machine off / out of range completes the integration setup
immediately and the reconnect loop runs quietly in the background
until the first advertisement arrives.

### Changed

- `tests/test_ble_client.py::TestEstablishConnection`: rewrote two
  tests that previously locked in the raw-fallback antipattern. The
  new tests assert that `BleakError` propagates and that a missing
  `BLEDevice` raises instead of silently falling back.

## [0.49.6] — 2026-04-15 — Documentation site (MkDocs Material)

Documentation infrastructure.

### Added

- **MkDocs Material site** at <https://dzerik.github.io/melitta-barista-ha/>
  serving the committed docs (`docs/BLE_ARCHITECTURE.md`,
  `docs/PROTOCOL.md`, `docs/adr/001-...md`) plus an auto-included
  changelog. Mermaid diagrams render natively, full-text search, dark
  / light toggle, edit-on-GitHub links.
- GitHub Actions workflow (`.github/workflows/docs.yml`) auto-deploys
  on push to `main` whenever README, CHANGELOG, `docs/`, or
  `mkdocs.yml` change.
- `mkdocs.yml` `exclude_docs:` whitelist guards local-only RE / audit
  notes from being published if a contributor runs `mkdocs build`
  locally with those files present.

### Changed

- README header gains a docs-site link near the top.
- `.gitignore` now excludes `/site/` and `.cache/`.

## [0.49.5] — 2026-04-15 — Docs: BLE name formats + companion app/card scope

Documentation-only patch.

### Changed

- README and docs (`multi-brand-architecture.md`, `adr/001-...md`,
  `NIVONA_HA_INTEGRATION_AUDIT.md`) now describe **all three**
  observed Nivona advertisement formats: legacy `NIVONA-NNN-----`,
  bare `NNN-----`, and the 15-digit no-dash serial form (e.g.
  `930254000000000`) seen on real NICR 930 / firmware `0254A013A10`.
  Previously docs only mentioned the legacy form, even though the
  regex was broadened in 0.49.1.
- README marks the **Custom Lovelace card** and **Standalone PWA**
  as **Melitta only** — both companion projects assume Melitta-shaped
  entities (HC/HJ extensions, named cup counters, profile selects)
  and don't yet render Nivona's per-family stats / brew override
  layout.

## [0.49.4] — 2026-04-15 — README + repo description: full Nivona scope

Documentation-only patch.

### Changed

- README intro and Features bullet now spell out the full Nivona
  family list — **NICR 6xx / 7xx / 79x / 9xx / 1030 / 1040** plus
  **NIVO 8xxx** — instead of the misleading "Nivona NICR/NIVO 8xxx".
  The supported-families table further down was already correct;
  only the headline copy understated coverage.
- GitHub repo description updated to match. Also dropped "AI Coffee
  Sommelier" from the repo blurb pending the recipe → brew handoff
  (see 0.49.3 README clarification).
- Mention that NICR 930 is now validated on real hardware (PR #7,
  Cyrill).

## [0.49.3] — 2026-04-15 — README: clarify AI Sommelier WIP status

Documentation-only patch.

### Changed

- README marks **AI Coffee Sommelier** as work-in-progress: the
  WebSocket API, persistence, bean catalog, and conversation-agent
  prompt building are functional, but the **recipe → Freestyle brew
  handoff is not yet wired up** — generated recipes can be inspected
  but not brewed with one tap. Section reorganized into "Currently
  working" / "Not yet working" / "Planned end-to-end flow".
- Project tagline no longer lists "generate AI recipes" as a
  shipped feature; references the Sommelier section for current
  scope instead.

## [0.49.2] — 2026-04-15 — NICR 930 follow-up: cleanup of PR #7

Quality follow-up to 0.49.1 — same external behavior on NICR 930
plus regressions fixed.

### Fixed

- **Brew button respects override sliders again.** PR #7 removed the
  override-collection loop because `payload[5]=0x01` brewed with
  zeros when no temp-recipe HW writes happened. The new implementation
  flags each `NivonaBrewOverrideNumber` as `user_set` only when the
  user actually moves the slider; the brew button forwards just those
  fields, so unchanged sliders fall through to the machine's saved
  recipe defaults.
- **`is_ready` strict again** — the global relaxation in 0.49.1
  could let Melitta brews fire in states they shouldn't. The
  `MOVE_CUP_TO_FROTHER` tolerance is now declared per-family on
  `MachineCapabilities.tolerated_brew_manipulations` and applied via
  a new `is_ready_for_brew(tolerated)` helper. Only Nivona 9xx /
  9xx-light opt in.

### Changed

- `EugsterProtocol.start_process_nivona` gained an explicit
  `use_temp_recipe: bool` parameter — replaces the overloaded
  `chilled` flag that PR #7 was reusing to mean "use saved
  defaults". `payload[5]` is now `0x00` when either `chilled` or
  `not use_temp_recipe`. Docstring updated.

### Tests

+18 unit tests covering the override pipeline, the new readiness
helper, the per-family tolerated-flag declaration, and the byte
layout of `start_process_nivona`. 721 total (+18 from 0.49.1's 703).

## [0.49.1] — 2026-04-15 — Nivona NICR 930 support (PR #7 by @Cyrill)

Fixes for NICR 930 (family 900), validated on real hardware
(firmware 0254A013A10).

### Fixed

- **BLE name regex** now accepts the 15-digit no-dash advertisement
  form (`930254000000000`) observed on real NICR 930. Previously the
  regex required `\d{10}-----` and brand detection fell through to
  Melitta, breaking the HU handshake.
- **Stats enabled** for families `900` / `900-light` — the
  `_STATS_900` table was already populated but gated behind
  `supports_stats=False`.
- **Capabilities resolved at setup time** via the HA bluetooth
  scanner cache + BLEDevice advertisement-name fallback. Previously
  `client.capabilities` was `None` when entity platforms ran
  `async_setup_entry` (BLE not connected yet), so no stat / setting
  entities were created.
- **Brew defaults** now use `payload[5]=0x00` (saved recipe
  defaults) when no overrides are pre-written via HW; previously
  `0x01` caused the machine to brew with zeros.
- **`is_ready`** no longer rejects `MOVE_CUP_TO_FROTHER` — this flag
  was observed to persist after a completed brew on some Nivona
  models and blocked subsequent brews.

### Known limitations (addressed in 0.49.2)

- Nivona brew button currently ignores user-facing override number
  entities (strength / coffee_amount / temperature / milk_amount).
- `is_ready` relaxation is global (also affects Melitta).

## [0.49.0] — 2026-04-14 — Nivona accuracy pass + new entities for every family

Closes the prioritised findings of `docs/NIVONA_HA_INTEGRATION_AUDIT.md`.
The biggest item is a critical fix: `NivonaBrewOverrideNumber` no
longer corrupts the persistent standard-recipe slots on real
hardware. Most of the rest is bringing per-family stats / settings
into line with what the machines actually expose.

### Fixed (CRITICAL)

- **Temp-recipe HW writes go to the dedicated 9001-based register**
  instead of the persistent `10000 + selector*100 + offset` slot. The
  previous code permanently rewrote the standard recipe definition
  on the machine whenever a user adjusted a Nivona override number
  entity. New flow announces the recipe class at register 9001 first,
  then writes per-field offsets into the same temp slot, then issues
  HE — matching how the official app builds a temporary recipe.

### Fixed (HIGH)

- **Stat tables now exist for `600`, `900`, `900-light`, `1030`,
  `1040`** — these previously rendered zero stat sensors on any
  Nivona of that family. New tables expose 7–24 recipe / cumulative
  counters plus the universal 600/610/620/640 maintenance gauges per
  family.
- **`_STATS_700` no longer over-includes IDs 213-221** that don't
  exist on 700-family hardware (would have shown 7 broken sensors).
- **`_STATS_79X` rebuilt** — adds 202 Lungo, drops the spurious 213,
  adds the universal maintenance gauges; selector 4 (Cappuccino)
  remains absent, matching real 79X hardware.
- **`strength_levels` corrected for 8 NICR models** (660 / 670 / 675
  / 680 / 768 / 769 / 778 / 779) from 3 to 5 — previously truncated
  the strength dropdown for these owners.
- **HX parser is `>hhhh`** on the Nivona side — bytes 4-5 are a
  single 16-bit Message field, not info(U8)+manip(U8). Old shape
  worked for currently observed Message values 0/11/20 but would
  have silently lost any future ≥256 value.
- **NICR 8107 chilled recipes** — selectors 8/9/10 are now exposed
  via the recipe select on NICR 8107 entries; HE flag byte switches
  to 0x00 (chilled) for those selectors.

### Added — settings

- **`_SETTINGS_900`** expanded from 3 → 11 entries (tank lighting
  accents, save_energy, touch lock, AutoOn deactivated +
  hours/minutes pair).
- **`_SETTINGS_900_LIGHT`** expanded from 2 → 6 entries
  (save_energy, AutoOn-deactivated + pair).
- **`_SETTINGS_1030` and `_SETTINGS_1040`** expanded from 7/10 to
  14/17 entries (cup heater, milk-products toggle,
  direct-start-deactivated, touch lock, AutoOn pair).
- **`_SETTINGS_79X`** is now its own table (not the 700 alias) —
  drops id 103 (off-rinse) which 79X hardware does not expose.
- **NICR 758 drops setting 106** (profile) via a new per-model
  filter — that specific model omits the aroma-balance feature and
  HR-reading id 106 would NACK on real hardware.

### Added — entity wiring

- New `BrandSettingNumber` in `number.py` for options-less
  capability settings (auto_on_hours / auto_on_minutes; future
  numeric settings). `BrandSettingSelect` in `select.py` is now
  gated to descriptors that carry an options list — previously it
  would have crashed on options-less entries.

### Changed — docs / hygiene

- `Manipulation` enum docstring flags values 1-6 as Melitta-derived
  (only 0 / 11 / 20 are observed identical on Nivona); future
  per-brand overrides may rebind 1-6.
- `confirm_prompt` docstring spells out the fire-and-forget contract
  — a False return is "the write didn't ACK", not "the prompt is
  still showing"; callers should poll HX for authoritative state.
- `reset_recipe_default` swallows `FeatureNotSupported` on the HC
  re-read step so calling HD on a Nivona machine no longer raises.
- `fluid_write_scale_10` reverted to False on 900 / 900-Light —
  the ×10 scaling assumption was unverified by observed behaviour.

### Translations

- All entity translation keys (sensor / select / number) added to
  `strings.json`; the same keys mirrored into all 29 translation
  files with English fallback. Native translations for the new
  strings will land as community contributions.

## [0.48.1] — 2026-04-14 — Decouple emulator versioning

**Policy change:** the ESP32 BLE emulator under `esp_emulator/` now
has its own independent version (`esp_emulator/VERSION`) and its own
changelog (`esp_emulator/CHANGELOG.md`), and will be tagged separately
as `emu-v<MAJOR>.<MINOR>.<PATCH>`. The HA-integration version in
`manifest.json` no longer bumps for emulator-only changes and vice
versa. This commit itself contains the emulator Phase A work, released
as `emu-v0.2.0`; see the emulator changelog for details. From
`emu-v0.2.0` onwards the two projects move independently.

### Added

- `docs/NIVONA_RE_NOTES.md` — living scratch-pad for per-family
  Nivona reverse-engineering findings (Phases A→H of the emulator
  roadmap). Sources every fact to a specific line of the decompiled
  `EugsterMobileApp` (v3.8.6).
- `esp_emulator/VERSION` + `esp_emulator/CHANGELOG.md` — independent
  versioning channel for the emulator.

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
