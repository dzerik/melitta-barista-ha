# UI Contract Specification (v1)

Status: **Design accepted for 0.91**. The v2 feature set — parameter catalogs,
action catalog and i18n-over-WS — is **normative as of 0.92** (§6, shipped
additively within `contract_version: 1`; implementation plan §8; Appendix A.1
amendment 6). The v3 feature set — settings descriptors, the sommelier
vocabulary endpoint and the DirectKey/profile model — is **normative as of
0.93** (§9, shipped additively within `contract_version: 1`; implementation
plan §10; Appendix A.1 amendment 7).

This document is the canonical contract between the `melitta_barista` integration
(server) and its thin clients: the Lovelace card (`melitta-barista-card`) and the
future PWA. Where this document and code disagree, the document wins until a
versioned amendment lands here. This revision incorporates the adversarial review
of 2026-09-02; resolved choice points and rejected suggestions are listed in
Appendix A.

*(Canonical file: `/home/dzerik/Development/melitta-ha-integration/docs/UI_CONTRACT.md`)*

---

## 1. Motivation & principles

### 1.1 The problem

Today the card carries hardcoded machine knowledge in five clusters:

1. `machine-state.ts` string-matches **exact English display text** produced by
   `sensor.py` (`"Connected"`, `"Brewing"`, `"Ready"`, `"Idle"`). Any label change,
   any HA translation, any new process value silently breaks state detection.
2. `const.ts` duplicates the freestyle enum lists, `PORTION_LIMITS`, the DirectKey
   category list — including an English-display-name→token **reverse map**
   (`"Café Crème"` → `cafe_creme`) that must track integration display names
   accent-for-accent.
3. `icons.ts` keys a 27-entry drink table on exact English recipe names, patched
   with accent aliases, plus a name-based steam heuristic
   (`recipe !== "Milk" && recipe !== "Milk Froth" && ...`).
4. Maintenance/settings sections hardcode entity-suffix lists.
5. `recipe.ts` hardcodes the 12-field service payload.

Every new brand family (Nivona 600/700/79x/900/1030/8000…) multiplies this drift.

### 1.2 Principles

* **Server decides semantics, client renders pixels.** The integration knows what a
  machine can do, what its status means, and what a drink is made of. Clients receive
  that as *data* — stable tokens, numeric ranges, and procedural icon descriptions —
  and only turn data into UI. A client must be able to render a machine family it has
  never heard of.
* **Tokens, never display strings, as machine-readable identity.** Display strings are
  presentation; they may be localized, renamed, or accented freely. Anything a client
  branches on is a token from a vocabulary published by the server.
* **Versioned envelope with graceful degradation.** Every contract payload carries
  `contract_version`. Old card + new integration and new card + old integration must
  both keep working (§5). New-client behaviour against an old server is *fallback to
  the current string-matching code paths*, which are retained, not removed.
* **Additive-only within a version.** Within `contract_version: 1` fields may be
  added, never removed, renamed or re-typed. Clients MUST ignore unknown fields and
  unknown tokens (rendering a sane default), so additive server evolution never
  requires a client release.
* **Server always re-validates.** Ranges and clamp rules travel to the client as data
  (so the client can build good UI), but enforcement remains server-side on every
  brew/write path (`_resolve_enum`, `_resolve_portion` in `sommelier_api.py` stay
  authoritative). The contract is descriptive, never a security or safety boundary.
* **No probing cost.** The contract is derived from data the integration already
  holds after handshake (`MachineCapabilities`, `LiveCapabilities`, const maps,
  the client-side recipe cache — §7.1 Zone I-A0). Building it must never trigger
  BLE traffic.

---

## 2. Delivery mechanism

### 2.1 Decision: WebSocket for the contract document, entity attributes for bridge/live data

We deliver on **both channels**, with a strict split of responsibilities:

| Data | Channel | Why |
| --- | --- | --- |
| Contract document (capabilities, token vocabularies, limits, recipe catalog with icon specs) | WS `melitta_barista/ui_contract/get` | Large, quasi-static (changes only on handshake/family re-detection/recipe-preload completion — tracked by `contract_fingerprint`). Entity attributes would pollute recorder and re-render tracking; WS is fetch-and-cache keyed by fingerprint. Plumbing already exists on both sides (`_send_versioned` / `api.ts` `hass.callWS`). |
| **Bridge attributes** (`entry_id`, `contract_version`, `contract_fingerprint`, `connected`) | Attributes on `sensor.<prefix>_connection` — **always available**, so detection never flickers | `MelittaConnectionSensor` has no `available` override, is already in the card's tracked-ids whitelist, and semantically owns "connected". These attributes change rarely (recorder-cheap). |
| Live status tokens (`process_token`, `sub_process_token`, `manipulation_token`, …) | Attributes on `sensor.<prefix>_state` (additive) + existing panel WS `/status` | The card is entity-driven; HA already pushes entity updates. The state sensor keeps its availability gate — **its unavailability IS the offline signal**, exactly as in legacy mode (§3.4). Tiny payload — recorder-safe. |
| Per-recipe icon spec | Embedded in the existing `recipes` attribute of `select.<prefix>_recipe` (already recorder-excluded) + in WS `recipes/list` + in sommelier recipe payloads | Icon data must live *next to* the recipe data it describes, on every surface that serves recipes, so no client ever joins by display name again. |

This follows the proven server-driven pattern already in the codebase: the DirectKey
feature publishes structured data as attributes of `select.<prefix>_profile` and the
card's pure `parseDirectKeyData` renders it. The contract generalizes that pattern
and fixes its one wart (display-name keys) by keying everything on tokens/ids.

**Availability is deliberate.** `sensor.<prefix>_state` keeps
`available = connected and status is not None` — the legacy card depends on its
`unavailable` state for the offline body, and HA drops `extra_state_attributes`
from unavailable entities. That is why detection and WS-scoping data live on the
connection sensor, never on the state sensor.

### 2.2 WS command

* **Name:** `melitta_barista/ui_contract/get`
* **Location:** `panel_api.py`, registered inside `async_register_panel_websocket`
  (existing once-guard `hass.data[DOMAIN]["panel_api_registered"]`).
* **Schema:** `{"type": "melitta_barista/ui_contract/get", "entry_id": <str, Required>}`
* **Scoping:** `entry_id` is **required** (multi-entry installs exist — see the
  Tepliuk duplicate-entry case). Resolution via existing `_resolve_entry` /
  `_resolve_client`.
* **Errors** (all client-retriable rules in §2.3):
  * `entry_not_found` — no such entry (mirrors `ws_capabilities_get`).
  * `client_not_ready` — entry exists but `runtime_data` has no client.
  * `contract_not_ready` — **new, defined here**: the client object exists but
    `client.capabilities` is `None` (no successful handshake yet — fresh install
    with the machine off, HA restart before reconnect). The server never invents
    placeholder capability values; it returns this error instead. Clients MUST
    treat it as transient (§2.3 step 5), never as "server has no contract support".
* **Auth:** **not admin-gated.** Read-only informational, same class as
  `melitta_barista/status`, `recipes/list`, and `api/info`. It exposes nothing that
  isn't already visible through entity states/attributes.
* **Handler style:** sync `@callback` wrapped by `_wrap_sync_with_schema(handler,
  schema, admin=False)` — the `_ws_status` shape. Pure in-memory read of
  `entry.runtime_data` and the client-side recipe cache; no DB, no BLE.
* **Envelope:** response wrapped by `_send_versioned(..., schema_version=1)` like
  every other endpoint; the *contract* additionally carries its own
  `contract_version` and `contract_fingerprint` (§5.1) — the WS `schema_version`
  versions the transport shape, `contract_version` versions the contract
  semantics, `contract_fingerprint` versions the contract *content* for this
  machine. `melitta_barista/api/info` automatically lists the new command in
  `endpoints`, which is the coarse capability-discovery signal.

### 2.3 Client-side detection, fetch, and retry lifecycle (normative)

The card bridges from the entity world to the WS world without configuration:

1. Card resolves its entity prefix as today (`button.<prefix>_brew` regex).
2. Card reads `sensor.<prefix>_connection` attributes (always available):
   * `attributes.contract_version` present **and** in the client's
     `SUPPORTED_CONTRACT_VERSIONS` → **token mode** eligible. The version gate
     covers the *attribute* surface too, not just the WS document: if
     `contract_version` is absent or unsupported, `readStatusTokens` returns
     `null` and the card runs full legacy string-matching (§5.3.3).
   * `attributes.entry_id` → the id to scope WS calls.
   * `attributes.contract_fingerprint` → cache key component and refetch trigger.
3. Live status: in token mode the card reads `sensor.<prefix>_state` attributes
   (`process_token`, …). If the state sensor is `unavailable`, that **means
   offline** — identical to legacy semantics; the card renders its offline body.
   Token-mode eligibility itself never flickers, because it hangs off the
   connection sensor. Detection is re-evaluated on every `willUpdate` (not
   sticky), so an integration upgraded mid-session is picked up.
4. Contract fetch: in token mode the card fires `hass.callWS({type:
   "melitta_barista/ui_contract/get", entry_id})` once, caches the result for the
   session keyed by `entry_id + contract_fingerprint`, and refetches whenever the
   `contract_fingerprint` attribute changes (covers reconnect with a different
   detected family, post-handshake `machine_type` refinement, options-flow family
   override, and Melitta recipe-preload completion).
5. **Failure classification** (replaces the old "any failure → legacy" rule):
   * **Durable** — WS `unknown command`, or a response whose `contract_version`
     is not in the supported set: contract features stay in legacy fallback for
     the rest of the session (no re-probing).
   * **Transient** — `client_not_ready`, `contract_not_ready`, `entry_not_found`,
     network/auth errors, and a malformed payload whose `contract_version` IS
     supported (structural validation failure — assumed to be a server-side
     transient, e.g. a partially built document): the card auto-retries on the next `false→true`
     transition of the connection sensor's `connected` attribute (or when
     `contract_fingerprint` first appears/changes) — bounded to one retry per
     transition, no polling.
   * In **both** cases, degradation is **per-feature**: only contract-derived
     features (icon specs, enum lists, limits, capability gating) fall back to
     the hardcoded legacy consts. Attribute-token status handling stays active
     whenever step 2 succeeded — a failed contract fetch never throws away
     working token status.

Legacy mode is a permanent fixture of the card, not a transition shim: it is the
"new card + old integration" cell of the compatibility matrix (§5.4).

---

## 3. Contract schema v1

### 3.1 Casing conventions (normative)

* **Status tokens** (process / sub-process / manipulation / info-message /
  machine-type): `UPPER_SNAKE_CASE`, byte-for-byte equal to the Python enum member
  names in `coffee_platform/domain.py` and `const.py`. The panel `/status` WS payload
  already emits these; the contract canonizes them.
* **Value tokens** (freestyle process/intensity/aroma/temperature/shots/blend, glass
  types, layer roles, recipe categories): `lower_snake_case`, byte-for-byte equal to
  the keys of the const-map string→int tables (`PROCESS_MAP`, `INTENSITY_MAP`, …).
* Tokens are **frozen identifiers**. They are never localized, never displayed
  directly (except as last-resort fallback), and never change spelling within a
  contract version.

### 3.2 Token vocabularies (normative; known lists for v1, open for additive growth)

Derived from the real enums; the server sends these lists in the contract so clients
can build UI, but the *meaning* of each token below is fixed by this spec. All
token-typed fields are **open string types**: new tokens may appear additively
(§5.2.2) and clients MUST tolerate unknown values (§5.3.2). A client-side
`validateContract` MUST reject only unsupported `contract_version` and structural
mismatches — never unknown token values.

**`status.process`** (from `MachineProcess`, brand-normalized by `parse_status`):

```
READY  PRODUCT  CLEANING  DESCALING  FILTER_INSERT  FILTER_REPLACE  FILTER_REMOVE
SWITCH_OFF  EASY_CLEAN  INTENSIVE_CLEAN  EVAPORATING  BUSY
```

`process_token` may also be `null` (no status yet / raw code unmapped). Clients MUST
render unknown non-null tokens as a neutral "busy-like" state (never crash, never
hide the card).

**`status.sub_process`** (from `SubProcess`):

```
GRINDING  COFFEE  STEAM  WATER  PREPARE
```

`sub_process_token` is `null` when idle.

**`status.manipulation`** (from `Manipulation`):

```
NONE  BU_REMOVED  TRAYS_MISSING  EMPTY_TRAYS  FILL_WATER  CLOSE_POWDER_LID
FILL_POWDER  MOVE_CUP_TO_FROTHER  FLUSH_REQUIRED
```

**`status.info_message`** (from `InfoMessage` IntFlag, sent as a list of set flags):

```
FILL_BEANS_1  FILL_BEANS_2  EASY_CLEAN  POWDER_FILLED  PREPARATION_CANCELLED
```

**`machine_type`**: known tokens `BARISTA_T` | `BARISTA_TS`; `null` for Nivona and
undetected machines.

**Freestyle value tokens** (from const maps; wire values in parentheses are
server-internal and do **not** travel):

* `process`: `none`(0) `coffee`(1) `milk`(2) `water`(3)
* `intensity`: `very_mild`(0) `mild`(1) `medium`(2) `strong`(3) `very_strong`(4)
* `aroma`: `standard`(0) `intense`(1)
* `temperature`: `cold`(0) `normal`(1) `high`(2)
* `shots`: `none`(0) `one`(1) `two`(2) `three`(3)
* `blend`: `hopper_1`(1) `hopper_2`(2). **Wire byte 0 (`Blend.BARISTA_T`, the
  single-hopper/machine-default value) has no token: `component_to_tokens` OMITS
  the `blend` key for byte 0 or any unknown byte, and clients MUST treat an
  absent/null `blend` as "machine default hopper".**

The per-machine contract sends *subsets* of these (e.g. 3-level Nivona intensity,
single-hopper blend list); the client renders exactly what it receives.

### 3.3 Contract document shape (WS `ui_contract/get` response)

TypeScript-flavoured schema; all fields required unless marked `?`. Union types
below list the **known v1 tokens** — normatively every token-typed field is an
open `string` (§3.2 growth rule). `IconSpec` is defined in §3.6.

```ts
interface UiContractResponse {
  schema_version: 1;              // transport envelope (from _send_versioned)
  contract_version: 1;            // THE contract compatibility version (§5)
  contract_fingerprint: string;   // content revision for THIS machine (§5.1);
                                  // clients cache per entry_id + fingerprint
  entry_id: string;
  generated_at: string;           // ISO 8601 UTC
  source: "live";                 // reserved: "cache" if a cached variant is added later

  machine: {
    brand: string;                // BrandProfile.brand_slug; known: "melitta" | "nivona"
    brand_name: string;           // display, e.g. "Melitta"
    model_name: string | null;    // e.g. "Barista TS Smart", "NICR 769"
    family_key: string | null;    // e.g. "barista_ts", "700", "79x"
    machine_type: string | null;  // known: "BARISTA_T" | "BARISTA_TS"; null = Nivona/undetected
    connected: boolean;
  };

  brand_theme: BrandTheme;        // §3.10 — brand badge DATA (never a logo asset)

  capabilities: {
    supports_recipe_writes: boolean;   // MachineCapabilities.supports_recipe_writes
    supports_stats: boolean;
    supports_factory_reset: boolean;
    supports_brew_overrides: boolean;
    supports_freestyle: boolean;       // derived: "HJ" in profile.supported_extensions
    my_coffee_slots: number;           // 0 = no MyCoffee
    strength_levels: number;           // 3 or 5
    has_aroma_balance: boolean;
    hopper_count: 1 | 2;               // derived, §3.5 (unknown Melitta type => 2)
    has_milk_system: boolean;          // derived, §3.5
    tolerated_brew_manipulations: string[]; // Manipulation tokens, §3.5 serialization rule
  };

  vocabularies: {
    status: {
      process: string[];         // full §3.2 list
      sub_process: string[];
      manipulation: string[];
      info_message: string[];
    };
    freestyle: {                  // machine-filtered subsets of §3.2
      process: string[];          // always ["none","coffee","milk","water"] in v1
      intensity: string[];        // 5-level: all; 3-level: ["mild","medium","strong"]
      aroma: string[];            // ["standard","intense"] or ["standard"]
      temperature: string[];
      shots: string[];
      blend: string[];            // ["hopper_1","hopper_2"] iff hopper_count==2 else ["hopper_1"]
    };
  };

  limits: {
    portion_ml: {
      c1: { min: number; max: number; step: number };  // {5, 250, 5}
      c2: { min: number; max: number; step: number };  // {0, 250, 5}
    };
  };

  recipes: Recipe[];              // machine-appropriate catalog (TS-gating applied server-side)

  status_attribute_entity: string;     // suffix "state": live tokens (§3.4)
  bridge_attribute_entity: string;     // suffix "connection": detection/bridge block (§3.4)
}

interface Recipe {
  recipe_id: number;              // stable id: RecipeId value (Melitta) or
                                  // RecipeDescriptor.recipe_id (Nivona)
  name: string;                   // English display name (i18n deferred to v2, §6.3)
  category: string;               // lower_snake token: "espresso" | "coffee" |
                                  // "milk_drink" | "water" | "my_coffee" | "" (unknown)
  icon: IconSpec | null;          // null → client renders its generic default
  components?: {                  // present when composition is known (Melitta recipe cache)
    c1: RecipeComponentData | null;
    c2: RecipeComponentData | null;
  };
}

interface RecipeComponentData {
  process: string;                // freestyle process token
  intensity: string;
  aroma: string;
  temperature: string;
  shots: string;
  portion_ml: number;
  blend?: string;                 // OMITTED when wire byte is 0 (Blend.BARISTA_T)
                                  // or unknown; present only for hopper_1/hopper_2
}
```

Notes:

* `recipes` for Melitta comes from the **client-side base-recipe cache**
  (§7.1 Zone I-A0: raw `MachineRecipe`/`RecipeComponent` objects keyed by
  `RecipeId` int, living next to `_directkey_recipes`/`_profile_names`) with
  `RECIPE_NAMES` + `get_available_recipes` TS-gating; for Nivona from
  `MachineCapabilities.recipes` descriptor tables. Composition-less recipes
  (Nivona) get icons derived from category defaults (§4.8).
* **Melitta completeness caveat:** the base-recipe preload runs asynchronously
  after connect. A contract fetched before preload completion returns the recipe
  catalog **without** `components`/composition-derived icons (category-default or
  `null` icons instead). Preload completion bumps `contract_fingerprint`, which
  triggers the client refetch (§2.3 step 4) — no client polling needed.
* `forbidden_combinations` from `LiveCapabilities` is intentionally **not** carried
  into v1 (always empty today); it arrives with the v2 enum catalogs.
* The existing `melitta_barista/capabilities/get` (sommelier) endpoint is unchanged
  and remains the LLM-facing surface; `ui_contract/get` is the renderer-facing
  surface. They may share builder code but version independently.

### 3.4 Entity attribute surfaces (additive)

Two entities carry contract data, with distinct availability semantics:

**A. Bridge block — `sensor.<prefix>_connection` (`MelittaConnectionSensor`).**
Always available (no `available` override); `native_value` stays the frozen
`"Connected"`/`"Disconnected"` strings for legacy cards. Gains:

```jsonc
{
  "entry_id": "0123abcd...",        // NEW: WS-scoping bridge
  "contract_version": 1,            // NEW: token-mode detection + compatibility gate
  "contract_fingerprint": "9f3ac1d24b07", // NEW: content revision; refetch trigger (§5.1)
  "connected": true                 // NEW: boolean twin of native_value; retry trigger (§2.3.5)
}
```

These keys are **always present** — presence never flickers with machine state.
They change rarely (recorder-cheap). `contract_fingerprint` may be absent only on
a pre-handshake entry where no contract exists yet (matching `contract_not_ready`).

**B. Live token block — `sensor.<prefix>_state` (`MelittaStateSensor`).**
Availability gate (`connected and status is not None`) is **unchanged**;
`native_value` — the English display string — is **frozen** (§5.2.3), so old
cards keep working. When available, `extra_state_attributes` gains:

```jsonc
{
  "process_id": 4,                       // existing
  "info_messages": ["FILL_BEANS_1"],     // existing — hereby PROMOTED to a frozen
                                         // token list (InfoMessage member names);
                                         // no duplicate alias key is added

  "process_token": "PRODUCT",            // NEW: MachineProcess token or null (unmapped raw code)
  "sub_process_token": "GRINDING",       // NEW: SubProcess token or null (idle)
  "manipulation_token": "NONE",          // NEW: null iff status is None; "NONE" for
                                         // no-manipulation AND for parsed-unknown codes
  "is_brewing": true,                    // NEW: process == PRODUCT (server-derived)
  "awaiting_confirmation": false         // NEW: manipulation in PROMPT_MANIPULATIONS
}
```

Rules:

* **Entity unavailable ⇒ offline.** When the machine is disconnected or has no
  status, the state sensor is `unavailable` and HA strips its attributes — that
  absence is the offline signal, byte-identical to legacy-card semantics. Clients
  MUST NOT infer "no token support" from it (token-mode detection lives on the
  connection sensor, block A).
* *Server-side clarification:* the token builder
  (`build_status_tokens(status, connected)`) emits all-null tokens both when
  `status is None` **and** while disconnected — the "null iff status is None"
  rule above describes the only state observable on the wire, because the
  frozen availability gate strips the attributes in the disconnected-with-
  stale-status case anyway. No client-visible behaviour differs.
* The card derives everything `computeMachineStatus` needs from these two
  entities: `isConnected` from the bridge `connected`; `isBrewing = is_brewing`;
  `isReady = process_token === "READY" && manipulation_token === "NONE"` (both
  non-null by construction when the entity is available and status parsed);
  `isBusy = process_token === "BUSY"`; activity from `sub_process_token`
  (`null` → the client's own localized "idle"); color from `process_token`.
* The panel WS `/status` payload already carries the same tokens
  (enum-name strings); v1 formalizes those names as frozen (they may no longer be
  treated as an implementation detail of `panel_api.py`).

### 3.5 Derived capability fields (server derivation rules)

* `hopper_count`: brand `melitta` → `1` **only on a confirmed `BARISTA_T`**, else
  `2` (unknown/unrefined `machine_type` is treated as dual-hopper — mirroring
  `select.py`'s existing gating, which registers blend selects on confirmed TS
  AND unknown type, and `get_available_recipes`, which treats unknown as TS).
  Brand `nivona` → `1` for all currently supported families.
  `hopper_count` MAY change mid-session when the post-handshake HR machine-type
  read refines the type; the change bumps `contract_fingerprint` and propagates
  via the client refetch (§2.3). This replaces the card's `BLEND_OPTIONS`
  hardcode as the *client-visible* truth. `vocabularies.freestyle.blend` follows
  the same rule.
* `has_milk_system`: `true` iff any of:
  (a) brand profile's family layout defines `milk_amount_offset` (Nivona families
  with milk parameters), (b) any recipe in the catalog has `category ==
  "milk_drink"`, (c) brand is `melitta` (all supported Barista models have milk
  systems). Clients use it to gate milk-ish UI (e.g. frother hints), never to block
  commands — server re-validates anyway.
* `supports_freestyle`: `"HJ" in profile.supported_extensions` — Melitta `true`,
  Nivona `false` today. Gates the freestyle section without entity-probing.
* `tolerated_brew_manipulations`: the static field is `tuple[int, ...]`
  (`domain.py`). Serialization rule: each int is mapped via
  `Manipulation(value).name`; ints without an enum member are **omitted** from
  the client-facing list (server-side `is_ready_for_brew` keeps using the raw
  ints). Today only the Nivona 900 family sets a value — `(11,)` →
  `["MOVE_CUP_TO_FROTHER"]`; all other families serialize to `[]`.

### 3.6 IconSpec — procedurally derived drink icon description

The icon spec is a *description of a drink*, not a picture: glass shape, fill
level, stacked layers bottom→top, optional foam cap, steam. **The client owns
geometry, colors and style; the server owns composition** — `color_hint` (below)
is the sole, advisory exception. Derivation rules in §4.

```ts
interface IconSpec {
  spec_version: 1;
  glass: string;                  // known: "espresso_cup" | "cup" | "tall_glass"
  total_ml: number;               // sum of all layers + foam, >= 1
  fill_level: number;             // 0.01–1.00: how full the glass is drawn (§4.2);
                                  // min(1.0, total_ml / nominal_volume(glass))
  layers: Layer[];                // bottom → top, 1..6 entries
  foam: Foam | null;              // always rendered topmost when present
  steam: boolean;                 // server-derived; replaces the name heuristic (§4.7)
}

interface Layer {
  role: string;                   // known: "coffee" | "milk" | "water" | "additive"
  ml: number;                     // integer, >= 0
  fraction: number;               // ml / total_ml, rounded to 2 decimals
  intensity: number;              // 0.00–1.00; darkness/opacity hint.
                                  // coffee: brew darkness (§4.3); milk: 0.0;
                                  // water: 0.0 (render translucent); additive: 0.5
  crema?: true;                   // only on a coffee layer that is topmost overall
  color_hint?: string | null;     // additive layers only; "#RRGGBB" or null (see below)
  label?: string;                 // additive layers only; additive display name
}

interface Foam {
  role: "milk_foam";
  ml: number;
  fraction: number;
}
```

Nominal glass volumes (normative for `spec_version: 1`):
`espresso_cup` = 60 ml, `cup` = 220 ml, `tall_glass` = 320 ml. The server
computes `fill_level = round(min(1.0, total_ml / nominal), 2)`; unknown glass
tokens use the `cup` nominal client-side if `fill_level` is ever missing.

Client rendering contract:

* Draw liquid up to `fill_level` of the glass interior; within that filled
  region, stack layers (and foam) bottom-up by `fraction`; fractions sum to
  1.0 ± 0.02 — the client normalizes remainder into the last layer. This
  preserves the ristretto/espresso/lungo/café-crème fill-height distinction the
  current card renders.
* Unknown `role` values → render as a neutral layer of `intensity` grey.
  Unknown `glass` → render as `cup`.
* `icon: null`, unparsable spec, or **unknown `spec_version`** → the client's
  existing generic default drink (today's `DRINKS` `DEFAULT`). The card's icon
  geometry primitives are reused; only the lookup-by-English-name layer is
  bypassed.
* `color_hint` is `#RRGGBB` only — the server validates/normalizes and emits
  `null` for anything else. It is advisory: the client MAY ignore it or adjust
  lightness/contrast for the active theme, and MUST treat it as data (an escaped
  fill value), never as markup.

### 3.7 Example payload — Melitta Barista TS Smart

```json
{
  "schema_version": 1,
  "contract_version": 1,
  "contract_fingerprint": "9f3ac1d24b07",
  "entry_id": "a1b2c3d4e5f6",
  "generated_at": "2026-09-02T10:15:00Z",
  "source": "live",
  "machine": {
    "brand": "melitta",
    "brand_name": "Melitta",
    "model_name": "Barista TS Smart",
    "family_key": "barista_ts",
    "machine_type": "BARISTA_TS",
    "connected": true
  },
  "brand_theme": {
    "brand": "melitta",
    "wordmark": "MELITTA",
    "accent": "#c8102e",
    "accent_soft": "#f6e3e6",
    "logo_url": null
  },
  "capabilities": {
    "supports_recipe_writes": true,
    "supports_stats": true,
    "supports_factory_reset": false,
    "supports_brew_overrides": false,
    "supports_freestyle": true,
    "my_coffee_slots": 8,
    "strength_levels": 5,
    "has_aroma_balance": true,
    "hopper_count": 2,
    "has_milk_system": true,
    "tolerated_brew_manipulations": []
  },
  "vocabularies": {
    "status": {
      "process": ["READY", "PRODUCT", "CLEANING", "DESCALING", "FILTER_INSERT",
                  "FILTER_REPLACE", "FILTER_REMOVE", "SWITCH_OFF", "EASY_CLEAN",
                  "INTENSIVE_CLEAN", "EVAPORATING", "BUSY"],
      "sub_process": ["GRINDING", "COFFEE", "STEAM", "WATER", "PREPARE"],
      "manipulation": ["NONE", "BU_REMOVED", "TRAYS_MISSING", "EMPTY_TRAYS",
                       "FILL_WATER", "CLOSE_POWDER_LID", "FILL_POWDER",
                       "MOVE_CUP_TO_FROTHER", "FLUSH_REQUIRED"],
      "info_message": ["FILL_BEANS_1", "FILL_BEANS_2", "EASY_CLEAN",
                       "POWDER_FILLED", "PREPARATION_CANCELLED"]
    },
    "freestyle": {
      "process": ["none", "coffee", "milk", "water"],
      "intensity": ["very_mild", "mild", "medium", "strong", "very_strong"],
      "aroma": ["standard", "intense"],
      "temperature": ["cold", "normal", "high"],
      "shots": ["none", "one", "two", "three"],
      "blend": ["hopper_1", "hopper_2"]
    }
  },
  "limits": {
    "portion_ml": {
      "c1": { "min": 5, "max": 250, "step": 5 },
      "c2": { "min": 0, "max": 250, "step": 5 }
    }
  },
  "recipes": [
    {
      "recipe_id": 200,
      "name": "Espresso",
      "category": "espresso",
      "components": {
        "c1": { "process": "coffee", "intensity": "strong", "aroma": "standard",
                "temperature": "normal", "shots": "one", "portion_ml": 40,
                "blend": "hopper_1" },
        "c2": null
      },
      "icon": {
        "spec_version": 1,
        "glass": "espresso_cup",
        "total_ml": 40,
        "fill_level": 0.67,
        "layers": [
          { "role": "coffee", "ml": 40, "fraction": 1.0, "intensity": 0.68, "crema": true }
        ],
        "foam": null,
        "steam": true
      }
    },
    {
      "recipe_id": 214,
      "name": "Latte Macchiato",
      "category": "milk_drink",
      "components": {
        "c1": { "process": "milk", "intensity": "medium", "aroma": "standard",
                "temperature": "normal", "shots": "none", "portion_ml": 160,
                "blend": "hopper_1" },
        "c2": { "process": "coffee", "intensity": "strong", "aroma": "standard",
                "temperature": "normal", "shots": "one", "portion_ml": 40,
                "blend": "hopper_1" }
      },
      "icon": {
        "spec_version": 1,
        "glass": "tall_glass",
        "total_ml": 200,
        "fill_level": 0.63,
        "layers": [
          { "role": "milk", "ml": 130, "fraction": 0.65, "intensity": 0.0 },
          { "role": "coffee", "ml": 40, "fraction": 0.20, "intensity": 0.68 }
        ],
        "foam": { "role": "milk_foam", "ml": 30, "fraction": 0.15 },
        "steam": true
      }
    }
  ],
  "status_attribute_entity": "state",
  "bridge_attribute_entity": "connection"
}
```

### 3.8 Example payload — Nivona (700 family, NICR 769)

Recipe names/ids below are illustrative of the descriptor-table path; the real
values come from `CAPABILITIES_700.recipes`. Both icons below are generated
strictly by §4.8 synthetic compositions + §4.1–4.7 (the Cappuccino is the §4.11
worked example).

```json
{
  "schema_version": 1,
  "contract_version": 1,
  "contract_fingerprint": "41c09be77a20",
  "entry_id": "f6e5d4c3b2a1",
  "generated_at": "2026-09-02T10:15:00Z",
  "source": "live",
  "machine": {
    "brand": "nivona",
    "brand_name": "Nivona",
    "model_name": "NICR 769",
    "family_key": "700",
    "machine_type": null,
    "connected": true
  },
  "brand_theme": {
    "brand": "nivona",
    "wordmark": "NIVONA",
    "accent": "#00646b",
    "accent_soft": "#e0eeef",
    "logo_url": "/local/melitta_barista/nivona.png"
  },
  "capabilities": {
    "supports_recipe_writes": false,
    "supports_stats": true,
    "supports_factory_reset": true,
    "supports_brew_overrides": true,
    "supports_freestyle": false,
    "my_coffee_slots": 4,
    "strength_levels": 3,
    "has_aroma_balance": true,
    "hopper_count": 1,
    "has_milk_system": true,
    "tolerated_brew_manipulations": []
  },
  "vocabularies": {
    "status": {
      "process": ["READY", "PRODUCT", "CLEANING", "DESCALING", "FILTER_INSERT",
                  "FILTER_REPLACE", "FILTER_REMOVE", "SWITCH_OFF", "EASY_CLEAN",
                  "INTENSIVE_CLEAN", "EVAPORATING", "BUSY"],
      "sub_process": ["GRINDING", "COFFEE", "STEAM", "WATER", "PREPARE"],
      "manipulation": ["NONE", "BU_REMOVED", "TRAYS_MISSING", "EMPTY_TRAYS",
                       "FILL_WATER", "CLOSE_POWDER_LID", "FILL_POWDER",
                       "MOVE_CUP_TO_FROTHER", "FLUSH_REQUIRED"],
      "info_message": ["FILL_BEANS_1", "FILL_BEANS_2", "EASY_CLEAN",
                       "POWDER_FILLED", "PREPARATION_CANCELLED"]
    },
    "freestyle": {
      "process": ["none", "coffee", "milk", "water"],
      "intensity": ["mild", "medium", "strong"],
      "aroma": ["standard", "intense"],
      "temperature": ["cold", "normal", "high"],
      "shots": ["none", "one", "two", "three"],
      "blend": ["hopper_1"]
    }
  },
  "limits": {
    "portion_ml": {
      "c1": { "min": 5, "max": 250, "step": 5 },
      "c2": { "min": 0, "max": 250, "step": 5 }
    }
  },
  "recipes": [
    {
      "recipe_id": 1,
      "name": "Espresso",
      "category": "espresso",
      "icon": {
        "spec_version": 1,
        "glass": "espresso_cup",
        "total_ml": 40,
        "fill_level": 0.67,
        "layers": [
          { "role": "coffee", "ml": 40, "fraction": 1.0, "intensity": 0.68, "crema": true }
        ],
        "foam": null,
        "steam": true
      }
    },
    {
      "recipe_id": 4,
      "name": "Cappuccino",
      "category": "milk_drink",
      "icon": {
        "spec_version": 1,
        "glass": "cup",
        "total_ml": 180,
        "fill_level": 0.82,
        "layers": [
          { "role": "coffee", "ml": 40, "fraction": 0.22, "intensity": 0.68 },
          { "role": "milk", "ml": 110, "fraction": 0.61, "intensity": 0.0 }
        ],
        "foam": { "role": "milk_foam", "ml": 30, "fraction": 0.17 },
        "steam": true
      }
    }
  ],
  "status_attribute_entity": "state",
  "bridge_attribute_entity": "connection"
}
```

(Note: no `components` blocks — the 700 family does not expose per-recipe
composition; icons come from category defaults, §4.8. `brand_theme.logo_url`
is shown non-null purely to illustrate the URL form — it is emitted only when
this user placed `<config>/www/melitta_barista/nivona.png` themselves, §3.10;
the §3.7 example shows the default `null` case.)

### 3.9 Sommelier recipes — icon next to the recipe

Sommelier surfaces (`sommelier/generate` results, `saved_recipes` listings,
`favorites`) attach the same `icon: IconSpec` object to each recipe payload,
computed by the same builder from the recipe's `machine_phases` components plus
**additive slots**: each additive becomes an `additive` layer (§4.6) with `label`
set to the additive's display name and `color_hint` when the DB has one
(normalized to `#RRGGBB` or `null`, §3.6). This is purely additive to the
existing sommelier schemas.

### 3.10 brand_theme — brand badge data (additive within `contract_version: 1`)

A top-level `brand_theme` block (placed in the §3.3 shape directly after the
`machine` block) lets clients render a brand badge without hardcoding brand
knowledge. **Legal constraint (non-negotiable): the integration never ships or
distributes brand logos — they are trademarks.** `brand_theme` is *data only*:
a slug, a wordmark display string, and accent colors. Any logo pixels come
exclusively from a file the **user** placed in their own HA configuration.

```ts
interface BrandTheme {
  brand: string;            // BrandProfile.brand_slug; same value as machine.brand
  wordmark: string;         // display string, e.g. "MELITTA" | "NIVONA" — text,
                            // rendered as text, never an image
  accent: string;           // "#rrggbb" — primary brand accent
  accent_soft: string;      // "#rrggbb" — muted companion usable as a background tint
  logo_url: string | null;  // "/local/melitta_barista/<brand>.png" iff the
                            // user-supplied file exists (below); null otherwise
}
```

**Per-brand values (normative for v1; server-owned — clients MUST NOT hardcode
them):**

| brand | wordmark | accent | accent_soft |
| --- | --- | --- | --- |
| `melitta` | `MELITTA` | `#c8102e` | `#f6e3e6` |
| `nivona` | `NIVONA` | `#00646b` | `#e0eeef` |

**Client contrast responsibilities** (same precedent as `color_hint`, §3.6):
the colors are advisory data. Clients MAY adjust lightness/contrast for the
active theme (`accent_soft` in particular is a *light*-theme tint and will
usually need darkening on dark themes), MUST keep text rendered on either
color legible, and MUST treat all values as escaped color data, never as
markup. An absent `brand_theme` (older server) or an unknown `brand` slug ⇒
neutral, unbranded rendering — never an error.

**`logo_url` semantics:**

* `null` unless the user placed their own file at
  `<config>/www/melitta_barista/<brand>.png` (e.g.
  `<config>/www/melitta_barista/melitta.png`), which HA serves as
  `/local/melitta_barista/<brand>.png` — the exact string emitted.
* File existence is checked **once at entry setup** with async executor I/O
  (never blocking the event loop in a `@callback`); the boolean result is
  cached in the entry runtime for the sync contract builder. Adding or
  removing the file therefore takes effect on the next entry reload.
* The integration never validates, reads, or redistributes the file's
  contents; it only reports that the user's own file exists. Clients render
  the image with the wordmark as fallback (`alt`/error path → wordmark text).
* Logo presence is a `contract_fingerprint` input (§5.1) — its presence
  changes rendering.

---

## 4. Icon-spec derivation rules

One deterministic pure function, `build_icon_spec(components, additives=()) ->
IconSpec | None`, in `ui_contract.py`. Same inputs → byte-identical output
(dict-equal), guaranteed by tests. All constants below are normative for
`spec_version: 1`.

Inputs: an ordered sequence of up to two components (c1, c2), each with token fields
`process`, `intensity`, `aroma`, `temperature`, `shots` and integer `portion_ml`;
plus an ordered sequence of additives `{name, ml | null, color_hint | null}`.

### 4.1 Component filtering & ordering

* Drop components with `process == "none"` or `portion_ml <= 0`.
* If nothing remains and there are no additives → return `None` (client default icon).
* Layer stacking order is **dispense order**: c1 bottom, c2 above it. This alone
  reproduces drink physiognomy: `c1=milk, c2=coffee` → latte-macchiato banding;
  `c1=coffee, c2=milk` → cappuccino banding. Additive layers go above component
  layers; foam is always topmost (§4.4).

### 4.2 Totals, glass selection, fill level

```
total_ml   = Σ layer.ml + foam.ml (foam carved out of the milk portion, §4.4;
             additive ml included — but see §4.6 for glass selection)
milk_ml    = Σ portion_ml of components with process == "milk"
has_coffee = any component with process == "coffee"
milk_first = a surviving milk component precedes the first surviving coffee
             component in dispense order (c1 before c2)
```

Glass (first matching rule wins; computed from **component** ml only, §4.6):

1. `tall_glass` if `total_ml > 200`, **or** (`has_coffee` and `milk_first`) —
   layered milk-under-coffee drinks read as tall glasses even at 180–200 ml
   (Latte Macchiato). A coffee-first milk drink (Cappuccino: c1 coffee, c2 milk)
   falls through to rule 3 and renders as a `cup` — cappuccino physiognomy stays
   reachable.
2. `espresso_cup` if `total_ml <= 60`.
3. `cup` otherwise.

Fill level (nominal volumes from §3.6: espresso_cup 60, cup 220, tall_glass 320):

```
fill_level = round(min(1.0, total_ml / nominal_volume(glass)), 2)   # >= 0.01
```

This preserves the partial-fill distinction (ristretto 25 ml → 0.42 of an
espresso cup; lungo 110 ml → 0.50 of a cup) that the legacy `DRINKS` table
encoded as per-name heights.

### 4.3 Coffee layers — darkness from intensity + shots

For each `coffee` component:

```
intensity_idx = index in [very_mild, mild, medium, strong, very_strong]  # 0..4
shot_count    = index in [none, one, two, three]                          # 0..3
extra_shots   = max(shot_count - 1, 0)
darkness      = clamp(0.30 + 0.125 * intensity_idx + 0.10 * extra_shots, 0.30, 1.00)
```

Layer: `{role: "coffee", ml: portion_ml, intensity: round(darkness, 2)}`.
Examples: very_mild/1 shot → 0.30; medium/1 → 0.55; strong/1 → 0.68 (canonical
espresso); very_strong/3 → 1.00. `aroma == "intense"` adds +0.05 before clamping.

**Crema:** set `crema: true` on a coffee layer iff it is the topmost element of the
whole spec (no layer, additive, or foam above it).

### 4.4 Milk layers and foam

Each `milk` component (`process == "milk"`, the wire STEAM process) splits into a
body layer and a foam contribution:

```
if it is the only component and temperature == "high":   # froth-dominant drink
    foam_ratio = 0.50
else:
    foam_ratio = 0.20
foam_ml  = max(round5(portion_ml * foam_ratio), 10)   # round5 = round to nearest 5
body_ml  = portion_ml - foam_ml                        # may be 0, then no body layer
```

* Body layer: `{role: "milk", ml: body_ml, intensity: 0.0}` at its dispense position.
* Foam contributions from all milk components merge into one `foam` object,
  rendered topmost. If no milk component exists, `foam: null`.
* `temperature == "cold"` milk still foams (machine dispenses cold froth); steam
  is governed solely by §4.7.

### 4.5 Water handling

`water` components become `{role: "water", ml: portion_ml, intensity: 0.0}`.
Clients render `water` translucent/blue-tinted per their style. A pure hot-water
composition (single water component) yields `glass: "cup"` (or `tall_glass` above
200 ml), no foam, and **no steam** (§4.7 — parity with the card's deliberate
suppression for Hot Water/Milk drinks); the English-name check itself is still
replaced by data. Water in mixed drinks (e.g. Americano as coffee + water) stacks
by dispense order like any layer.

### 4.6 Additive layers (sommelier)

Each additive → `{role: "additive", ml: additive.ml ?? 10, intensity: 0.5,
color_hint: additive.color_hint, label: additive.name}`, stacked in listed order
directly above the component layers, below foam. `color_hint` is normalized to
`#RRGGBB` or `null` before it enters the spec (§3.6). Additive ml counts toward
`total_ml` and `fill_level` *after* glass selection has been computed from
component ml only (a 10 ml syrup must not flip an espresso into a cup).

### 4.7 Steam flag

`steam = true` iff at least one **coffee** component has `temperature != "cold"`.
Milk-only, water-only, and additive-only specs get `steam = false`. This
reproduces the current card's rendering exactly (no steam on Milk, Milk Froth,
Hot Water) while replacing the hardcoded English-name list with data — and a
cold-brew-style future recipe automatically loses its steam wisps.

### 4.8 Composition-less recipes (Nivona descriptor tables)

When per-recipe composition is unavailable, derive from `RecipeDescriptor.category`
using fixed synthetic compositions, then run §4.1–4.7 unchanged:

| category | synthetic composition |
| --- | --- |
| `espresso` | coffee 40 ml, strong, one shot |
| `coffee` (café crème/lungo class) | coffee 120 ml, medium, one shot |
| `milk_drink` | c1 coffee 40 ml strong/one + c2 milk 140 ml medium |
| `water` | water 200 ml, high |
| `my_coffee` / `""` / unknown | return `None` → client default icon |

### 4.9 Fractions and rounding (determinism)

* `fraction = round(ml / total_ml, 2)` for every layer and foam.
* All `ml` values are integers; `intensity` and `fill_level` rounded to 2 decimals.
* The server does not force fractions to sum to exactly 1.0; the client normalizes
  (§3.6). Tests assert `abs(1.0 - Σfractions) <= 0.02` for every generated spec.

### 4.10 Worked example A — Latte Macchiato (c1 milk 160 medium, c2 coffee 40 strong/one)

1. Both components survive filtering; order: milk bottom, coffee above.
2. Milk splits: foam_ratio 0.20 (not sole component) → foam round5(32)=30 ml,
   body 130 ml.
3. Coffee darkness: 0.30 + 0.125·3 + 0 = 0.675 → 0.68. Not topmost (foam above) →
   no crema.
4. total_ml = 130 + 40 + 30 = 200; milk_first ∧ has_coffee → `tall_glass`;
   fill_level = round(min(1, 200/320), 2) = 0.63.
5. steam: coffee temp `normal` → true.

Result: exactly the Latte Macchiato icon in §3.7.

### 4.11 Worked example B — Cappuccino (§4.8 milk_drink synthetic: c1 coffee 40 strong/one, c2 milk 140 medium)

1. Both survive; order: coffee bottom, milk above.
2. Milk splits: foam_ratio 0.20 (not sole component) → foam round5(28)=30 ml,
   body 110 ml.
3. Coffee darkness 0.68; not topmost → no crema.
4. total_ml = 40 + 110 + 30 = 180; not milk_first, not > 200, > 60 → `cup`;
   fill_level = round(min(1, 180/220), 2) = 0.82.
5. steam: coffee temp `normal` → true.

Result: exactly the Cappuccino icon in §3.8. Tests pin both orderings (A and B)
byte-exact.

---

## 5. Compatibility & versioning rules

### 5.1 Version fields

* `contract_version` — **integer**, currently `1`. The **compatibility gate**.
  Lives in the `ui_contract/get` response and mirrored as a bridge attribute on
  the connection sensor. Bumped **only** on a breaking change to shapes or token
  semantics defined here. Constant `CONTRACT_VERSION = 1` in `ui_contract.py`.
* `contract_fingerprint` — **short opaque string** (e.g. 12 hex chars of a
  sha256), the **content revision** for one machine. Computed over
  `(family_key, model_name, machine_type, capability-relevant profile fields,
  recipe-cache generation counter, brand-logo presence flag)`. The logo flag is
  the cached setup-time result of the §3.10 `logo_url` file check — brand_theme
  participates in the fingerprint because logo presence changes rendering; the
  flag is fixed for the life of the entry runtime and can only differ across
  entry reloads. It changes on: handshake completion, family
  or model re-detection, post-handshake machine-type refinement (HR read),
  options-flow family override, and Melitta base-recipe preload completion.
  Clients cache the contract per `entry_id + contract_fingerprint` and refetch on
  change (§2.3.4). It carries no semantics beyond equality comparison.
* `spec_version` inside `IconSpec` — versions the icon sub-schema independently
  (an icon-only overhaul must not force a full contract bump). Client rule for an
  unknown value: §5.3.2.
* These are orthogonal to the existing axes: `API_VERSION` (`const.py`, "1.0"),
  per-endpoint `schema_version` (`_send_versioned`), `LiveCapabilities` blob
  versions, and sommelier DB `SCHEMA_VERSION`. None of them are reused for the UI
  contract.

*Appended by the 0.92 amendment (Appendix A.1, entry 6):*

* `strings_version` — **string**, the integration `manifest.json` version.
  Carried in every `i18n/get` response and, additively, in the contract
  document. The cache axis for server-served display strings
  (`locale + strings_version`, §6.3.2). Orthogonal to `contract_version`;
  carries no semantics beyond equality.
* **Fingerprint inputs, 0.92 delta:** the `parameters` and `actions` blocks add
  no new machine-state inputs — every value they derive from (`family_key`,
  `machine_type`, `strength_levels`, `has_aroma_balance`,
  `supported_extensions`, `supports_factory_reset`, `supports_brew_overrides`,
  `verified_maintenance_processes`, `tolerated_brew_manipulations`, …) is
  already, or hereby becomes, a §5.1 input. **One new input: the integration
  version string**, so catalog-content changes shipped in a release (e.g. the
  #36 audit flipping an `available` flag) refresh long-lived client sessions
  whose fingerprint would otherwise be unchanged. Normative rule going forward:
  any value that feeds the served catalogs MUST be (directly or transitively) a
  fingerprint input.
* **Single-source rule for the version input (normative):** the fingerprint has
  two independent call paths — `build_bridge_attributes` (connection sensor)
  and `build_ui_contract` (WS handler). They MUST be byte-identical by
  construction: `async_setup_entry` resolves the manifest version **once** via
  the existing `async_get_integration` pattern (async — no event-loop-blocking
  manifest read) and stashes it on the client
  (`client.integration_version`), like every other fingerprint input;
  `compute_contract_fingerprint` reads it from `client` at both call sites
  (which are sync — the cached string is their only valid source). Threading
  the version through per-call-site arguments is forbidden: divergent values
  would make the card's fingerprint cache check permanently fail and re-arm a
  WS refetch every ~2 s status poll, for every client, indefinitely. A pytest
  pins `build_bridge_attributes(...)['contract_fingerprint'] ==
  build_ui_contract(...)['contract_fingerprint']`.
* **On i18n and the fingerprint:** server i18n strings are not a *direct*
  fingerprint input — but because `strings_version` equals the integration
  version, which *is* an input, any release that changes served strings churns
  every entry's fingerprint as a side effect. This one-contract-refetch-per-
  entry-per-upgrade cost is accepted and intended: it is precisely the
  mechanism that delivers the new `strings_version` to clients (§6.3.2) with
  zero extra round-trips. (The draft's "deliberately excluded from the
  fingerprint" phrasing is retracted as misleading.)

*Appended by the 0.93 amendment (Appendix A.1, entry 7):*

* **Fingerprint inputs, 0.93 delta: none.** The v3 blocks add **no new machine-state
  inputs** — every value they derive from is already a §5.1 input:
  * `settings` derives from `family_key`, `machine_type` (TS-only gating),
    `model_name` (the NICR 758 per-model descriptor exclusion),
    `unsupported_generic_setting_ids` / `capabilities.settings` (both pure
    functions of family + model), and the integration version (table edits
    shipped in a release).
  * `directkey` derives from `supported_extensions` (HC), `machine_type`
    (physical-button map, slot count), `my_coffee_slots`, and the integration
    version.
  * The sommelier vocabulary is **deliberately outside the contract document**
    (§9.2.1) and therefore outside the fingerprint; its cache axis is
    `strings_version`, which is fingerprint-transitive via the integration
    version. The §5.1 normative rule ("any value that feeds the served
    catalogs MUST be a fingerprint input") is satisfied for all three features
    with zero new inputs.
* `strings_version` gains two new key domains (`settings`, `sommelier`,
  §9.1.5/§9.2.5) on the same axis; no new version field is introduced by v3.
  `strings_version` is resolved **once at setup** and stashed domain-wide
  (§9.2.2) — the lazy per-request resolution inside `i18n/get` is removed in
  the same release, applying the §5.1 single-source precedent.

### 5.2 Server rules

1. Within `contract_version: 1`: additive-only. New fields, new *optional* tokens in
   vocabularies, new recipe fields — allowed. Removing/renaming fields, changing a
   token's spelling or meaning, changing casing conventions — forbidden (requires
   `contract_version: 2`).
2. Token vocabularies may **grow** (a new `MachineProcess` member ships as a new
   token in the same release); clients are required to tolerate unknown tokens
   (§5.3), so growth is additive. This is why token-typed fields are open string
   types (§3.2/§3.3).
3. English `native_value` strings of existing sensors are frozen for the lifetime of
   contract v1 support — they are the legacy card's de-facto API. New display
   behaviour goes through tokens. The state sensor's availability gate is equally
   frozen (§2.1).
4. If `contract_version: 2` ever ships, the server serves v2 only (single shape);
   old clients detect the unknown version — on the WS response AND on the bridge
   attribute (§5.3.3) — and fall back to legacy mode, which rule 3 keeps
   functional for the entire surface including status. No dual-shape serving.
5. Server always re-validates every command regardless of what limits/vocabularies
   it advertised (existing `_resolve_enum` / `_resolve_portion` / capability gates
   stay authoritative).
6. Whenever any input of the fingerprint changes, the server MUST expose the
   updated `contract_fingerprint` bridge attribute no later than the next
   connection-sensor state write — in practice within one status-poll cycle
   (~2 s), which is the effective granularity of the §2.3.4 refetch trigger.
   (Amended from "in the same state write" — see Appendix A amendment log.)
7. *(0.92 amendment)* The v1 `vocabularies.freestyle` and `limits.portion_ml` blocks are closed
   sets mirrored by `parameters` (§6.1.2): the server MUST keep them present,
   MUST keep them element-wise consistent with `parameters`, and MUST NOT add
   new keys inside them. All catalog evolution happens in `parameters` /
   `actions` / `forbidden_combinations`.

8. *(0.93 amendment)* **Legacy DirectKey identity surfaces are frozen (mirror-and-freeze, same
   pattern as rule 7).** The profile select's `directkey_recipes` attribute
   (keyed by **English display names** — `"Espresso"`, `"Café Crème"`, …) and
   the Title-case `name` labels in the `recipes/list` `directkey` rows are
   closed sets: no new category keys or label spellings will ever be added
   inside them, and the existing spellings are frozen (they are the shipped
   card's and PWA's de-facto reverse-map API). All category identity evolution
   happens in the token surfaces: the additive `category` field on
   `recipes/list` rows and the contract `directkey` block (§9.3.4).
9. *(0.93 amendment)* The Melitta settings entities' **behaviour** is unchanged by v3, but their
   defining data moves: the switch/number setting tables migrate from
   `switch.py`/`number.py` into `const.py` as pure data
   (`MELITTA_SETTING_TABLES`: setting id, control kind, min/max/step, mode,
   icon, English name, TS-only flag), consumed by **both** the entity
   platforms and the contract builder — one source of truth, satisfying the
   never-hand-copy rule without `ui_contract.py` importing
   `homeassistant.components.*`. Entity ids, names, icons, and behaviour are
   byte-identical before and after the move. The Nivona descriptor entities
   are untouched. The `settings` block **describes** the entity surface, it
   does not replace it; the builder and entity registration evaluate identical
   predicates over identical tables, pinned by pytest as **predicate
   equality** (§9.1.2 rule 5).
10. *(0.93 amendment)* New `ui_strings/` key domains are additive. The `i18n/get` domain set
    grows to `{status, values, recipes, actions, settings, sommelier}`;
    unknown requested domains remain ignored (§6.3.1, unchanged). **Note the
    real 2.x-compat mechanism:** shipped clients fetch i18n **without a
    domain filter** (the card's `fetchServerStrings` passes no `domains`
    parameter, and an omitted filter serves ALL domains), so their responses
    against a ≥0.93 server DO grow with every `settings.*`/`sommelier.*` key.
    Safety rests on §6.0.3 unknown-key tolerance (`setServerStrings` stores
    keys nothing consumes; contract consumers never enumerate) — not on
    domain filtering. Only a client that requests an explicit old-four-domain
    filter sees byte-identical responses.

### 5.3 Client rules

1. Ignore unknown fields everywhere.
2. Unknown status token → neutral "active/busy" rendering + raw token via
   `localizeOptional` fallback; unknown layer role → neutral grey layer; unknown
   glass → `cup` geometry and nominal volume; `icon: null`, structurally invalid
   spec, or **unknown `spec_version`** → default drink icon. Never throw.
3. `contract_version` not in the client's supported set → discard the contract
   AND ignore the token attribute surface entirely (`readStatusTokens` returns
   `null` unless the bridge `contract_version` is supported) → full legacy mode.
   The version gate covers both halves of the contract, so a future v2 server
   cannot leak v2 token semantics into a v1 client.
4. Cache the contract per `entry_id + contract_fingerprint` for the session;
   refetch when the bridge `contract_fingerprint` attribute changes or after HA
   reconnect. Transient fetch failures retry per §2.3.5; failure degrades only
   contract-derived features, never attribute-token status.
5. Keep the legacy code paths (string matching, `DRINKS`, hardcoded consts) alive
   and covered by tests for as long as integrations older than 0.91 are supported.

6. *(0.93 amendment)* The three-tier fallback pattern (§6.1.5) extends to every v3 feature, per
   feature independently: contract/WS-served data → the client's existing
   hardcoded tables (settings tables, category arrays, sommelier option
   lists) → hide/degrade. The hardcoded tables become permanent fallback
   fixtures, never deleted while pre-0.93 integrations are supported.
   **Within tier 1, entity existence remains a required gate** — contract
   presence never overrides entity absence (§9.1.6 rule 2, §9.3.6 rule 6).

### 5.4 Compatibility matrix

| | **Old integration (<0.91)** | **New integration (≥0.91)** |
| --- | --- | --- |
| **Old card (≤2.3.x)** | Status quo. | Works unchanged: sensor `native_value` strings and availability gates frozen (§5.2.3); new attributes and WS command are invisible additive surface; `recipes` attribute gains an `icon` key old cards never read. |
| **New card (≥2.4)** | Detection finds no `contract_version` on the connection sensor → legacy mode; `ui_contract/get` never called. Behaviour identical to old card. | Token mode: status from attribute tokens, sections gated by capabilities, icons from specs, enum lists/limits from contract. Hardcoded consts used only as legacy fallback. Machine off at load → offline body via unavailable state sensor, contract fetched (or retried) per §2.3. |

The same matrix applies to the panel frontend. The **PWA** has no legacy mode:
it requires `contract_version` **in its supported set** and distinguishes the two
mismatch directions — server version below the PWA's minimum → "update the
integration" screen; server version above the PWA's maximum → "update the app"
screen. The PWA additionally persists the last-good contract per `entry_id`
(unlike the card's session cache) and MAY render from it when the fetch fails or
the device is offline, marking the data as possibly stale and refetching on the
next successful connection.

*Appended by the 0.92 amendment:*

**0.92 feature-level matrix** (all cells inside `contract_version: 1`; the §5.4
base matrix continues to govern the <0.91 column):

| | **Integration 0.91.x** | **Integration ≥0.92** |
| --- | --- | --- |
| **Card 2.4–2.6.x (v1-only)** | Status quo (v1 token mode). | Works unchanged: `parameters`/`actions`/`forbidden_combinations`/`strings_version`/`name_key` are unknown fields (`validateContract` passes and ignores them, §3.2); `i18n/get` is never called. Zero behaviour change — the reason §6.0 chose the additive path. |
| **Card 2.7 (v2-aware)** | Token mode as today; `parameters` absent → **v1 `vocabularies.freestyle`/`limits` (today's behaviour; `const.ts` only without any contract, §6.1.5)**; `actions` absent → legacy hardcoded action arrays; `i18n/get` → `unknown_command` → durable fallback to card bundles for the session. | Full v2: catalog-driven pickers/sliders and action groups, server display strings with bundle fallback. Per-feature degradation throughout (§6.0.4). |

Panel: ships inside the integration package, so panel and server are always
version-matched; the panel adopts `parameters` and i18n-over-WS as an ordinary
consumer (§6.3.5) with its existing per-key bundle fallback. PWA: still no
legacy mode (v1 rule); it treats `parameters`/`actions` as optional exactly
like the card and persists i18n per `locale + strings_version` with the §6.3.2
revalidation rule.

*Appended by the 0.93 amendment:*

**0.93 feature-level matrix** (all cells inside `contract_version: 1`; the §5.4
base matrix and the 0.92 matrix continue to govern older columns):

| | **Integration 0.92.x** | **Integration ≥0.93** |
| --- | --- | --- |
| **Card 2.7.0 (v2-aware)** | Status quo (full v2). | Works unchanged: `settings`/`directkey` are unknown fields (§5.3.1); the `save_directkey` action entry is in the `control` group, which the card renders with bespoke UI (informational per §6.2.5.2); `vocab/get` is never called. Its unfiltered `i18n/get` responses **grow** with the new `settings.*`/`sommelier.*` keys — safe via unknown-key tolerance (§5.2 rule 10), and an expected observation in the §10.4 beta checklist, not a regression. Zero behaviour change — the reason §9.0 chose the additive path again. |
| **Card 2.8 (v3-aware)** | `settings`/`directkey` absent → tier-2/3 fallback to `SWITCH_KEYS`/`NUMBER_KEYS`/`DIRECTKEY_CATEGORIES` + entity existence (today's 2.7 behaviour). | Contract-driven settings section (numbers become writable level controls; Nivona selects render), catalog-driven DirectKey category set with per-machine button truth. |
| **PWA ≤1.8.3 (contract-unaware)** | Status quo. | Works unchanged: the entity surface it reads (states, attributes, services) is untouched by v3. The sole server-visible change is the cup-size token normalization (§9.2.6.4): a migrated profile's stored `espresso_cup` is not in 1.8.3's picker, so its profile dialog may show no preselected cup size until re-saved — cosmetic; re-saves of the legacy token are re-normalized on write. |
| **PWA 2.0 (full v1+v2+v3 port)** | v1+v2 features active; v3 features absent → per-feature fallback to its ported hardcoded tables. Against <0.91 servers: no `contract_version` on the bridge → "update the integration" screen (§5.4 PWA rule, unchanged). | Full contract client: token status, icon specs, parameters, action catalog, settings descriptors, DirectKey/profile model, sommelier vocabulary, server i18n, brew-phase wizard. |

Panel: ships inside the integration (always version-matched); adopts the
`recipes/list` `category` token, the sommelier vocabulary, and the new
`sommelier.*` labels as an ordinary consumer. The panel has **no
machine-settings tab** (its tabs are sommelier/beans/additives/producers/
system), so it consumes nothing from §9.1.
---

## 6. The v2 feature set (0.92, normative)

### 6.0 Versioning decision (normative)

**All three v2 features ship additively within `contract_version: 1`.** No bump
to `contract_version: 2`.

Rationale, per the §5 rules:

* §5.2.1 permits new fields and new commands within v1; nothing in 0.92 removes,
  renames, or re-types a v1 field, changes a token spelling, or changes casing
  conventions — the §5.2.1 bump triggers are all absent.
* A bump would be strictly *worse* for compatibility: §5.3.3 makes the version
  gate cover the attribute surface too, so a `contract_version: 2` server would
  push every shipped 2.4–2.6.x card — whose `SUPPORTED_CONTRACT_VERSIONS` is
  `[1]` — into **full legacy string-matching mode**, discarding working token
  status for zero benefit. The additive path leaves them byte-for-byte
  unaffected (v2 fields are unknown fields; §5.3.1).
* Precedent: `brand_theme` (Appendix A.1 amendment 5) already shipped a whole
  feature block additively within v1.

Consequent normative rules:

1. **Per-feature presence gating.** Clients detect each v2 feature by field/
   command presence, never by version: `parameters` present → catalog-driven
   pickers; `actions` present → catalog-driven action UI; `i18n/get` answered →
   server strings. `validateContract` MUST NOT require any v2 field — a v1
   document without them remains valid.
2. `SUPPORTED_CONTRACT_VERSIONS` stays `[1]` in all clients. No client story for
   a version bump is needed because no bump occurs.
3. Unknown values inside v2 structures follow §5.3.2: unknown parameter `kind`
   or `scope` → that parameter falls back (§6.1.5); unknown action `kind` →
   that entry is dropped; unknown `requires` token → condition treated as
   satisfied (server re-validates anyway); unknown i18n keys → ignored.
4. **Per-feature degradation.** Failure or absence of any one v2 feature never
   degrades another, and never degrades v1 behaviour (token status, icon specs,
   v1 vocabularies). This extends §2.3.5 unchanged.

### 6.1 Parameter catalogs (enum catalogs with ranges)

#### 6.1.1 Shape

Additive top-level contract field `parameters`, a map from parameter family to a
self-describing descriptor:

```ts
// Additive to UiContractResponse (§3.3):
parameters?: Record<string, ParameterDescriptor>;
forbidden_combinations?: ForbiddenCombination[];   // §6.1.6
strings_version?: string;                          // §6.3.2

interface ParameterDescriptor {
  kind: string;              // known: "enum" | "range" (open, §5.3.2)
  scope: string[];           // known: "freestyle" | "brew_override" (open)
  applies_to?: string[];     // freestyle process tokens the parameter attaches
                             // to (e.g. ["coffee"]); absent = all processes
  // kind == "enum":
  tokens?: string[];         // lower_snake value tokens, §3.1 casing
  // kind == "range":
  unit?: string;             // known: "ml"
  per_component?: true;      // when set, c1/c2 sub-ranges below; else min/max/step
  c1?: { min: number; max: number; step: number };
  c2?: { min: number; max: number; step: number };
  min?: number; max?: number; step?: number;
}
```

Rules: a descriptor with unknown `kind` is ignored (per-parameter fallback,
§6.1.5); a descriptor whose `scope` contains no token the client understands is
not rendered; enum `tokens` are byte-equal to the const-map keys (§3.1) and are
the exact set the server's `_resolve_enum` accepts for this machine. Clamp
rules travel as data; enforcement stays server-side (§1.2 — unchanged).

#### 6.1.2 Relationship to v1 `vocabularies.freestyle` / `limits.portion_ml`: mirror-and-freeze

The v1 blocks are neither removed (forbidden by §5.2.1) nor left to drift.
Normatively (also appended as §5.2 rule 7):

* `vocabularies.freestyle` and `limits.portion_ml` remain present forever within
  v1, but become **closed sets**: no new keys will ever be added inside them.
* `parameters` **mirrors** them: for every freestyle family,
  `parameters.<family>.tokens` is byte-equal to
  `vocabularies.freestyle.<family>`, and `parameters.portion_ml.c1/.c2` are
  element-wise equal to `limits.portion_ml.c1/.c2`. A pytest pins this
  invariant.
* All catalog evolution (new families, ranges, scopes, `applies_to`,
  `forbidden_combinations`) happens exclusively in the v2 blocks.

#### 6.1.3 Families and scopes served in 0.92

| family | kind | Melitta (TS example) | Nivona (700 example) | scope |
| --- | --- | --- | --- | --- |
| `process` | enum | `none coffee milk water` | same | `freestyle` |
| `intensity` | enum | 5 tokens | `mild medium strong` | Melitta: `freestyle`; Nivona: `brew_override` |
| `aroma` | enum | `standard intense` | per `has_aroma_balance` | as intensity |
| `temperature` | enum | `cold normal high` | same | `freestyle` (Melitta only) |
| `shots` | enum | `none one two three` | same | `freestyle` (Melitta only) |
| `blend` | enum, `applies_to: ["coffee"]` | `hopper_1 hopper_2` (T: 1) | `hopper_1` | `freestyle` |
| `portion_ml` | range, `per_component`, `unit: "ml"` | c1 {5,250,5}, c2 {0,250,5} | same | Melitta: `freestyle`; Nivona: `brew_override` |

`brew_override`-scoped descriptors are emitted **iff**
`capabilities.supports_brew_overrides`; `freestyle`-scoped descriptors iff
`capabilities.supports_freestyle`. A machine with neither gets an empty-scope
family omitted entirely. Token subsets follow the exact same server filters as
the v1 vocabularies (3-level intensity, single-hopper blend, §3.5) — guaranteed
by the §6.1.2 mirror.

#### 6.1.4 Example payloads (pinned verbatim in tests)

Melitta Barista TS (extends the §3.7 document):

```json
"parameters": {
  "process":     { "kind": "enum", "scope": ["freestyle"],
                   "tokens": ["none", "coffee", "milk", "water"] },
  "intensity":   { "kind": "enum", "scope": ["freestyle"], "applies_to": ["coffee"],
                   "tokens": ["very_mild", "mild", "medium", "strong", "very_strong"] },
  "aroma":       { "kind": "enum", "scope": ["freestyle"], "applies_to": ["coffee"],
                   "tokens": ["standard", "intense"] },
  "temperature": { "kind": "enum", "scope": ["freestyle"],
                   "tokens": ["cold", "normal", "high"] },
  "shots":       { "kind": "enum", "scope": ["freestyle"], "applies_to": ["coffee"],
                   "tokens": ["none", "one", "two", "three"] },
  "blend":       { "kind": "enum", "scope": ["freestyle"], "applies_to": ["coffee"],
                   "tokens": ["hopper_1", "hopper_2"] },
  "portion_ml":  { "kind": "range", "scope": ["freestyle"], "unit": "ml",
                   "per_component": true,
                   "c1": { "min": 5, "max": 250, "step": 5 },
                   "c2": { "min": 0, "max": 250, "step": 5 } }
},
"forbidden_combinations": []
```

Nivona 700 (extends §3.8): only

```json
"parameters": {
  "intensity":  { "kind": "enum", "scope": ["brew_override"], "applies_to": ["coffee"],
                  "tokens": ["mild", "medium", "strong"] },
  "aroma":      { "kind": "enum", "scope": ["brew_override"], "applies_to": ["coffee"],
                  "tokens": ["standard", "intense"] },
  "portion_ml": { "kind": "range", "scope": ["brew_override"], "unit": "ml",
                  "per_component": true,
                  "c1": { "min": 5, "max": 250, "step": 5 },
                  "c2": { "min": 0, "max": 250, "step": 5 } }
},
"forbidden_combinations": []
```

(The v1 `vocabularies.freestyle` block in the same Nivona document still carries
the full mirrored token lists — the mirror invariant applies to the families
both surfaces carry; families the v1 block lists but `parameters` scopes away
from this machine are simply not rendered as freestyle UI, which matches
`supports_freestyle: false` gating that v1 clients already apply.)

#### 6.1.5 Client fallback chain (normative — three tiers)

Per parameter, independently:

**`parameters.<family>` → v1 `vocabularies.freestyle.<family>` /
`limits.portion_ml` → hardcoded client consts.**

A v2-aware client talking to a 0.91 server (no `parameters`) MUST land on tier 2
— the server-filtered v1 blocks — which is exactly today's 2.6.x behaviour
(3-level Nivona intensity, single-hopper blend). Tier 3 (client consts) is
reached only with no contract at all. Skipping tier 2 is forbidden: it would
regress a v2 card below the card it replaces. The §6.1.2 mirror guarantees tiers
1 and 2 agree on ≥0.92 servers, so `resolveParameters` is a cheap generalization
of the existing `resolveFreestyleVocab`.

#### 6.1.6 `forbidden_combinations`

Defined shape, empty content in 0.92 (`LiveCapabilities` carries none today):

```ts
interface ForbiddenCombination {
  params: Record<string, string>;   // e.g. { "process": "milk", "temperature": "cold" }
  reason_token?: string;            // optional i18n-able token
}
```

Clients MAY grey out matching combinations; the server re-validates regardless.
Always emitted (as `[]`) so clients need no presence special-case once they
support it.

### 6.2 Action catalog

#### 6.2.1 Shape

Additive top-level contract field `actions: ActionEntry[]`:

```ts
actions?: ActionEntry[];

interface ActionEntry {
  action: string;              // lower_snake action token (§6.2.2)
  group: string;               // known: "brew" | "control" | "cleaning" |
                               // "filter" | "power" | "danger" (open set)
  process: string | null;      // MachineProcess token the action starts, if any
  icon?: string;               // "mdi:<name>" — an mdi identifier is data, not a
                               // brand asset; absent/malformed → client default
                               // (normatively: mdi:cog)
  confirm: boolean;            // client shows a confirm step (two-tap or dialog)
  destructive?: true;          // implies confirm regardless of the confirm flag,
                               // plus danger styling
  requires: string[];          // condition tokens, §6.2.4; [] = always
  available: boolean;          // per-family truth, §6.2.6
  invocation: ActionInvocation;
}

type ActionInvocation =
  | { kind: "button";
      entity_suffix: string }                 // press button.<prefix>_<suffix>
  | { kind: "service";
      service: string;                        // melitta_barista.<service>
      entity_suffix: string;                  // REQUIRED: the button entity whose
                                              // entity_id is passed as the
                                              // service's entity_id
      params: ActionParam[] };

interface ActionParam {
  name: string;
  kind: string;                // known: "enum" | "bool" | "int" | "params_ref"
  required: boolean;
  tokens?: string[];           // kind enum
  default?: string | number | boolean;
  ranges?: [number, number][]; // kind int; disjoint inclusive ranges
  ref?: string;                // kind params_ref; known: "freestyle" — the full
                               // freestyle form per the `parameters` catalog
}
```

**Multi-machine targeting is normative:** every `melitta_barista` service
requires `entity_id` and resolves the target machine through it (`_find_client`
via the entity registry). Therefore service-kind entries MUST carry
`entity_suffix`; clients assemble `entity_id = "button.<prefix>_<entity_suffix>"`
exactly as button-kind does (today's `button.<prefix>_brew` anchor generalized).
An entry with unknown `invocation.kind` is dropped by the client (§6.0.3).

#### 6.2.2 Catalog contents (0.92)

Sixteen action tokens. Icons for the twelve existing buttons are the mdi names
the server already owns in `button.py`; the four new entries get authored icons.

Errata: three catalog icons (cancel mdi:stop, brew_freestyle mdi:coffee-maker,
confirm_prompt mdi:check-circle) intentionally differ from the entity icons
button.py owns (mdi:stop-circle, mdi:coffee-maker-outline,
mdi:check-circle-outline); the table is the normative catalog surface.

| action | group | process | invocation | confirm | destructive | requires | icon |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `brew` | brew | `PRODUCT` | button `brew` | no | | `["ready"]` | mdi:coffee |
| `brew_freestyle` | brew | `PRODUCT` | service `brew_freestyle`, params `[{name:"params", kind:"params_ref", ref:"freestyle", required:true}]` | no | | `["ready"]` | mdi:coffee-maker |
| `brew_directkey` | brew | `PRODUCT` | service `brew_directkey`, params `[{name:"category", kind:"enum", tokens:[7 DirectKey tokens], required:true}, {name:"two_cups", kind:"bool", default:false, required:false}]` | no | | `["ready"]` | mdi:gesture-tap-button |
| `cancel` | control | null | button `cancel` | no | | `["connected"]` | mdi:stop |
| `confirm_prompt` | control | null | button `confirm_prompt` | no | | `["awaiting_confirmation"]` | mdi:check-circle |
| `reset_recipe` | control | null | service `reset_recipe`, params `[{name:"recipe_id", kind:"int", ranges:[[200,223],[302,388]], required:false}]` | yes | | `["ready"]` | mdi:restore |
| `easy_clean` | cleaning | `EASY_CLEAN` | button `easy_clean` | yes | | `["ready"]` | mdi:shimmer |
| `intensive_clean` | cleaning | `INTENSIVE_CLEAN` | button `intensive_clean` | yes | | `["ready"]` | mdi:dishwasher |
| `descaling` | cleaning | `DESCALING` | button `descaling` | yes | | `["ready"]` | mdi:water-sync |
| `filter_insert` | filter | `FILTER_INSERT` | button `filter_insert` | no | | `["ready"]` | mdi:filter-plus |
| `filter_replace` | filter | `FILTER_REPLACE` | button `filter_replace` | no | | `["ready"]` | mdi:filter-cog |
| `filter_remove` | filter | `FILTER_REMOVE` | button `filter_remove` | no | | `["ready"]` | mdi:filter-remove |
| `evaporating` | power | `EVAPORATING` | button `evaporating` | yes | | `["ready"]` | mdi:air-humidifier |
| `switch_off` | power | `SWITCH_OFF` | button `switch_off` | yes | | `["connected"]` | mdi:power |
| `factory_reset_settings` | danger | null | button `factory_reset_settings` | yes | yes | `["ready"]` | mdi:cog-refresh |
| `factory_reset_recipes` | danger | null | button `factory_reset_recipes` | yes | yes | `["ready"]` | mdi:book-refresh |

Availability gating (server-side, encoded into `available`):
`brew_freestyle`/`reset_recipe` require the HJ/HD write paths
(`supports_freestyle` / `supports_recipe_writes`); `brew_directkey` requires the
HC extension; `factory_reset_*` require `supports_factory_reset` (Nivona 8000 →
`available: false`); process-starting maintenance actions follow §6.2.6.
`switch_off.requires == ["connected"]` — not `ready` — encodes the PR #42
precedent (Switch Off stays usable while connected-not-ready) as data.

Parameter shapes are **byte-equal to the service schemas** in `__init__.py`
(`BREW_FREESTYLE_SCHEMA`, `BREW_DIRECTKEY_SCHEMA`, `RESET_RECIPE_SCHEMA`): the
DirectKey token list is the 7-member `_DIRECTKEY_CATEGORIES`, the `params_ref:
"freestyle"` form is the 15 freestyle fields with the exact voluptuous defaults
(`name` defaults to `"Custom"` and is omittable), `reset_recipe.recipe_id` is
optional with the split 200–223 / 302–388 range. A pytest diffs the emitted
`ActionParam`s against the live schemas so they can never drift.

#### 6.2.3 Group labels and ordering

Known group render order: `brew, control, cleaning, filter, power, danger`,
then unknown groups in served order. Group headers are localized via
`actions._groups.<group>` i18n keys (§6.3.4); an unknown group id with no
served string falls back to the humanized token (normative).

#### 6.2.4 `requires` evaluation (client-side, advisory)

Known condition tokens, all derived from surfaces clients already read:

* `connected` — bridge attribute `connected == true` (§3.4 block A).
* `ready` — `process_token == "READY" && manipulation_token == "NONE"` (§3.4).
* `awaiting_confirmation` — state attribute `awaiting_confirmation == true`.

All listed tokens must hold (AND). An **unknown token is treated as satisfied**
(fail-open) — the catalog is descriptive; the server re-validates every command
and the machine NACKs what it can't do. `requires` gates enablement styling,
never correctness.

#### 6.2.5 Client consumption rules

1. **Fallback:** `actions` absent (0.91 server) → the client's existing
   hardcoded action lists (`CLEANING_ACTIONS`/`FILTER_ACTIONS`/`OTHER_ACTIONS`)
   render exactly as today. The legacy arrays are a permanent fixture, not a
   shim (§5.4 logic).
2. **Partial adoption is allowed:** clients MAY catalog-drive any subset of
   groups. Entries in groups a client renders with bespoke UI (the card's
   existing brew/freestyle/DirectKey sections) are **informational** for that
   client — catalog completeness exists for the PWA and future clients, and a
   client never builds two invocation paths for the same brew. Card 2.7
   catalog-drives `cleaning`/`filter`/`power`/`danger` plus unknown groups.
3. `available: false` entries are hidden by default (clients MAY render them
   disabled-with-explanation).
4. `destructive` forces the confirm step and danger styling regardless of
   `confirm`.
5. Labels/descriptions resolve per §6.3.5 (server string → client bundle →
   humanized token); icons per §6.2.1 (default `mdi:cog`).

#### 6.2.6 Maintenance availability and the per-family verification audit

The catalog MUST NOT promote unverified assumptions into served truth. Issue
#36 (NICR 779) proved that `MachineProcess` start codes are shifted on at least
the Nivona 700 family (a "power off" press started a cleaning cycle);
`button.py` registering all 8 maintenance buttons unconditionally is a known
bug, and this catalog is where it gets fixed as data:

* New field `MachineCapabilities.verified_maintenance_processes:
  tuple[int, ...] | None = None`. `None` = all process codes verified for this
  family (Melitta families — hardware-verified). A tuple = only the listed
  process ids are verified; everything else is unverified.
* `build_action_catalog` emits `available: false` for every process-starting
  entry whose process id is not verified for the family. **All Nivona families
  ship 0.92.0b1 with `verified_maintenance_processes = ()`** — maintenance and
  `switch_off`/`evaporating` entries served `available: false` — until the #36
  TX-byte button matrix lands and populates per-family tuples. This is
  explicit, intended b1 behaviour, and flipping a flag later is a
  catalog-content change delivered by the fingerprint (§5.1 amendment).
* Button *entities* are unchanged in 0.92 (fixing their unconditional
  registration is tracked separately); only the catalog carries the truth.
  Clients that catalog-drive these groups therefore stop exposing the broken
  buttons — the #36 hazard disappears for catalog-aware clients first.

### 6.3 Machine-domain i18n over WS

#### 6.3.1 Endpoint

* **Name:** `melitta_barista/i18n/get`
* **Schema:** `{"type": ..., vol.Required("locale"): str, vol.Optional("domains"): [str]}`
* **Not entry-scoped** — served strings are machine-independent (no `entry_id`).
* **Auth:** `admin=False` (informational, same class as `ui_contract/get`).
* **Handler:** async (file I/O on first use per locale), registered inside
  `async_register_panel_websocket`; auto-listed by `api/info`.
* **Locale resolution chain (normative):** requested locale → exact match →
  base language (`de-DE` → `de`) → `en`. `resolved_locale` reports the file
  that won; per-key fallback to `en` applies on top (§6.3.3).
* **Domains:** `status`, `values`, `recipes`, `actions`. Unknown requested
  domains are ignored; omitted `domains` = all.

```jsonc
// request
{ "type": "melitta_barista/i18n/get", "locale": "de-DE",
  "domains": ["status", "values", "recipes", "actions"] }
// response (via _send_versioned)
{ "schema_version": 1,
  "locale": "de-DE", "resolved_locale": "de",
  "strings_version": "0.92.0",
  "strings": {
    "status.process.READY": "Bereit",
    "status.manipulation.FILL_WATER": "Wassertank füllen",
    "values.intensity.very_mild": "Sehr mild",
    "recipes.name.espresso": "Espresso",
    "actions.easy_clean.label": "Easy Clean",
    "actions.easy_clean.description": "Milchsystem spülen",
    "actions._groups.cleaning": "Reinigung"
  } }
```

Keys are flat, dot-joined, and **byte-equal to the tokens they describe**
(§3.1 casing preserved): `status.*` embeds `UPPER_SNAKE`, `values.*` /
`recipes.*` / `actions.*` embed `lower_snake`. No client may case-fold keys.
`values.*` keys are family-scoped (`values.intensity.very_mild`) because bare
tokens collide across families (`none`, `standard`).

#### 6.3.2 Versioning and caching

* New version field **`strings_version`** (string) — the integration
  `manifest.json` version. Carried in every `i18n/get` response **and**, as an
  additive top-level field, in the contract document itself
  (`UiContractResponse.strings_version?`). It is the cache/equality axis for
  server strings; it carries no semantics beyond equality. Orthogonal to
  `contract_version`; related to `contract_fingerprint` only as described in
  the §5.1 amendment.
* **Refetch triggers (normative):** clients (re)fetch i18n on (a) session
  start / first token-mode activation, (b) HA locale change, and (c)
  `contract_fingerprint` change — the trigger the card already observes via
  `noteBridgeUpdate`, and which now transitively encodes the integration
  version (§5.1 amendment), so an integration upgrade arms exactly one
  re-fetch. `strings_version` is the **storage key, never the trigger**: if a
  re-fetch returns the cached `strings_version`, the cached strings stand.
* **Persisted caches** (PWA): a persisted `locale + strings_version` entry MUST
  be revalidated against the `strings_version` in the current session's
  contract document (free — it rides the contract fetch), or by one `i18n/get`
  per session when no contract is available. This closes the
  stale-across-upgrades hole a fetch-suppressing persisted cache would
  otherwise have.
* i18n failure (durable `unknown_command` on a 0.91 server, or transient)
  degrades **only display strings** — never token semantics, catalogs, or
  status handling.

#### 6.3.3 Where the strings live: `ui_strings/` asset files (NOT `translations/`)

The draft's "new `ui_contract` category in `strings.json`/`translations/*.json`
served via `async_get_translations`" is **rejected as unimplementable**: this
repo runs hassfest in CI (validate.yml and tests.yml), and hassfest validates
both `strings.json` and `translations/en.json` for custom integrations against
a closed voluptuous schema of known categories — an unknown top-level
`ui_contract` key fails with `extra keys not allowed`, and hassfest's slug key
rules would reject `UPPER_SNAKE`-embedding keys even inside an allowed
category. CI reality wins over the loader convenience.

Normative replacement:

* Strings ship as **`custom_components/melitta_barista/ui_strings/<locale>.json`**
  — `en.json` plus the same 28 additional locales as `translations/` (29
  total). Flat `{key: string}` maps in exactly the §6.3.1 key format, served
  verbatim after domain filtering. The directory is invisible to hassfest and
  is included in the HACS zip automatically (release packaging zips the whole
  component directory).
* The WS handler uses a small dedicated loader (~40 lines): resolve the locale
  per §6.3.1, `hass.async_add_executor_job(json-read)` for the locale file and
  `en.json`, **merge en-first** so every missing key falls back to English per
  key, cache the merged map in `hass.data[DOMAIN]` per resolved locale (files
  are immutable for the life of the install). No `async_get_translations`, no
  prefix stripping, no runtime mapping tables — the files are the wire format.
* **Completeness rules (asymmetric, replacing the draft's hard 29-way parity):**
  `en.json` MUST be complete — every token the contract builders can emit has a
  key, and no orphan keys exist (both enforced by pytest). The other 28 locales
  MAY be sparse; missing keys are served via the en overlay. The 0.92 seeding
  produces full 29-locale coverage for the initial content, but a future token
  may ship with English only, un-gated by 29 hand translations. (This mirrors
  HA core's own "only English needs to be always complete" rule and is what
  the per-key fallback chain exists for.)
* The dormant `entity.*` blocks in `translations/*.json` are **left untouched**
  — they belong to the entity-translation system (wiring entity
  `translation_key`s stays out of scope for 0.92). Duplication between
  `entity.*` and `ui_strings/` is accepted and deliberate: different key
  schemas, different freeze rules; only `ui_strings/` is normative for this
  endpoint.
* All strings are our own authored translations — never brand marketing copy or
  extracted brand assets (legal rule unchanged).
* Zone I-F definition-of-done includes a local hassfest run as a regression
  check that `translations/` remained untouched-valid.

#### 6.3.4 Token families served in 0.92, with the true source of each

Seeding plan per family — 29-language material that already exists in this
project's own repos is **ported** into `ui_strings/`; gaps are **newly
authored** (English first, 29-way translation as part of Zone I-F; nothing is
served that has no home):

| domain | keyspace | tokens | 29-language source today | 0.92 action |
| --- | --- | --- | --- | --- |
| `status` | `status.process.<TOKEN>` | 12 `MachineProcess` tokens | integration `entity.sensor.state.state.*` (dormant, complete; `READY`→`ready`, `PRODUCT`→`brewing`, `SWITCH_OFF`→`off`, rest mechanical lowercase) | copy into `ui_strings/` under token keys |
| `status` | `status.sub_process.<TOKEN>` | 5 `SubProcess` tokens | integration `entity.sensor.activity.state.*` (dormant; `GRINDING`→`grinding`, `COFFEE`→`extracting`, `STEAM`→`steaming`, `WATER`→`dispensing_water`, `PREPARE`→`preparing`) | copy under token keys |
| `status` | `status.manipulation.<TOKEN>` | 9 `Manipulation` tokens | 7/9 in integration `entity.sensor.action_required.state.*` (`BU_REMOVED`→`brew_unit_removed`, rest mechanical); `MOVE_CUP_TO_FROTHER`, `FLUSH_REQUIRED` exist in 29 languages only in the card bundles (`action.*`) | copy 7; port the 2 card translations |
| `status` | `status.info_message.<TOKEN>` | 5 `InfoMessage` tokens | none anywhere | newly author English (`FILL_BEANS_1` "Fill bean hopper 1", `FILL_BEANS_2` "Fill bean hopper 2", `EASY_CLEAN` "Easy Clean recommended", `POWDER_FILLED` "Ground coffee filled", `PREPARATION_CANCELLED` "Preparation cancelled") + translate 29-way |
| `values` | `values.<family>.<token>` | 20 freestyle tokens (family-scoped keys — bare tokens collide) | card `values.*` (18 tokens, 29 langs) for process/intensity/aroma/temperature/shots; panel `hopper.*`/`beans.*` strings inform `blend`; integration: none | port card translations; author `blend` labels; drop the card-only `extra_strong` (no server token — never served) |
| `values` | `values.directkey_category.<token>` | 7 DirectKey categories | panel `recipes.cat.*` (7, 29 langs); card `drinks.*` (7, 29 langs) | port panel translations |
| `recipes` | `recipes.name.<name_key>` | 24 Melitta keys + Nivona descriptor name_keys | integration `entity.select.recipe.state.*` (24, dormant, 29 langs); Nivona-only names: none | copy the 24; author Nivona `name_key` entries (§6.3.6), reusing overlapping keys (`espresso`, `cappuccino`, …), newly author the remainder |
| `recipes` | `recipes.category.<token>` | 5 contract categories (`espresso coffee milk_drink water my_coffee`) | none (panel's 7 DirectKey categories are a different set) | newly author + translate |
| `actions` | `actions.<token>.label` | 16 §6.2.2 action tokens | integration `entity.button.<key>.name` (dormant, 29 langs) for the 12 existing button keys; missing: `brew_freestyle`, `brew_directkey`, `factory_reset_settings`, `factory_reset_recipes` | copy 12; newly author 4 |
| `actions` | `actions.<token>.description` (optional key) | 8 in 0.92 | card `maintenance.actions.<key>.desc` (8, 29 langs) | port the 8; other actions ship without description |
| `actions` | `actions._groups.<group>` | 6 known groups (§6.2.3) | card `maintenance.groups.*` (partial) | port existing + newly author the rest |

#### 6.3.5 Client consumption rules (normative)

1. Per-key preference order: **server string → client bundle string → humanized
   raw token** (§5.3.2 unchanged as last resort). Client bundles remain for UI
   chrome and as the fallback layer — server i18n never becomes a hard
   dependency.
2. Fetch/refetch/persistence per §6.3.2 (fingerprint-change and locale-change
   triggers; `strings_version` as the cache key).
3. Status/manipulation/value tokens MUST no longer be rendered raw when server
   strings are available (this normatively fixes the panel's current raw-token
   rendering in `melitta-status.js` / `melitta-brew-wizard.js`).
4. Unknown keys in `strings` are ignored; missing keys fall through the
   preference order per key, not per fetch.
5. Casing is significant and byte-equal to contract tokens (§6.3.1).
6. **Implementation shape (normative for card and panel alike):** the string
   store is split into (a) a **pure synchronous registry** with zero HA imports
   — `setServerStrings(map | null)`, `serverString(key)`,
   `resetServerStrings()` (the existing `currentLang` singleton pattern) — which
   is all that label/format modules may import, and (b) a hass-coupled
   fetch/cache/failure-classification half, called only from top-level wiring,
   which feeds (a). This preserves the pure-module isolation the existing test
   suites depend on and keeps label functions synchronous.
7. Family-scoped value lookup goes through a new
   `displayNameFor(family, token)` (server key `values.<family>.<token>` →
   bundle `values.<token>` → humanized token). The legacy bare-token
   `displayName(token)` stays unchanged as the bundle-only path.

#### 6.3.6 Recipe keying: `name_key` (additive `Recipe` field)

i18n keys recipes by a stable **`name_key`**, not `recipe_id` — Nivona recipe
ids collide across families (id 0 is a different drink per family), so the v1
sketch's `recipes.name.200` id-keying is replaced. Contract delta (additive):

```ts
interface Recipe {  // §3.3, additive field
  name_key?: string;   // stable ASCII lower_snake i18n key
  // Recipe.name stays the English fallback exactly as v1 specified
}
```

* **Melitta:** the lower_snake key matching the existing 24-entry translation
  block (`espresso`, `cafe_creme`, `latte_macchiato_extra`, …).
* **Nivona:** `name_key` becomes an **explicit authored field on
  `RecipeDescriptor`** (`coffee_platform/domain.py`) — ASCII lower_snake,
  written once per descriptor (`caffe_latte`, `frothy_milk`,
  `chilled_espresso`, …). Runtime derivation from the display name is
  **forbidden** as the normative rule: descriptor names contain non-ASCII
  ("Caffè Latte") and a display rename would silently orphan 29 translations.
  Derivation (NFKD → strip diacritics → lowercase → spaces→underscores) is
  defined only as the one-off seeding-script default, hand-reviewed. The full
  per-family `name_key` sets — every Nivona family, including the NICR 8107
  chilled variants — are pinned in a test so a descriptor rename cannot move a
  key, and every derived/authored key is asserted to have a
  `recipes.name.<name_key>` entry in `ui_strings/en.json`.

#### 6.3.7 Machine-domain string families added in 0.94 (v3.1 amendment)

All three clients — PWA (`src/locales/*.json`), panel (`www/i18n/locales/*.js`)
and card (`src/localize/languages/*.json`) — still carry their own copies of
wording that belongs to the machine, not to a client: brew-guide vocabulary,
machine-state descriptions, service-cycle sublabels, sommelier LLM error hints
and labels for the well-known free-form suggestion values. Three copies mean
three translation bills and three chances to word the same thing differently.
This amendment moves those families to the server. It is a strings wave, not a
mechanism: additive within `contract_version: 1` (§6.0), served by the existing
`i18n/get` loader on the existing `strings_version` axis (§6.3.2), zero new
fingerprint inputs.

**(a) Families (normative key shapes).** English wording is sourced from the
existing client bundles wherever they already say it well (all three are this
project's own repos); it is re-authored only where a bundle is vague, and stays
in the interface voice — plain verbs, sentence case, no apologies.

| keyspace | count | source | notes |
| --- | --- | --- | --- |
| `wizard.<dotted.key>` | 29 | PWA `wizard.*` (primary), panel `wizard.*` (second reading) | brew-guide vocabulary; key names **byte-equal to the PWA bundle** (`wizard.title`, `wizard.step.cup`, `wizard.machine.start_full`, `wizard.finish.title`, `wizard.close.message`, …) |
| `status.process.<TOKEN>.description` | 12 | newly authored | one sentence describing the state to a user |
| `status.sub_process.<TOKEN>.description` | ≤5 | newly authored | served **only where it adds meaning** over the label |
| `sommelier.error.<code>` | 5 | PWA `sommelier.error.*`, card `sommelier.err.*`, panel `sommelier.err.*` | codes: `no_llm_agent`, `no_llm_agent_selected`, `llm_agent_missing`, `timeout`, `unauthorized` |
| `sommelier.milk.<token>` `sommelier.syrup.<token>` `sommelier.topping.<token>` `sommelier.liqueur.<token>` `sommelier.note.<token>` | 8/7/5/4/10 | PWA `sommelier.milk_*` / `syrup_*` / `topping_*` / `liqueur_*` / `note_*` | labels for the well-known suggestion values |

* **`wizard` is a new i18n domain** (7th member of `_I18N_DOMAINS`; the domain
  of a key is its first dot segment). Additive per §5.2 rule 10 — clients that
  omit `domains` (all shipped clients) receive it automatically; a client that
  sends an explicit list must add `"wizard"`.
* **Placeholders are carried verbatim** — `{n}`, `{m}`, `{cup}`, `{ml}`,
  `{sec}`, `{prompt}` keep their names, count and substitution semantics per
  key. Translators may reorder them within a sentence; nobody may rename or
  drop one. `{prompt}` passes machine text through unchanged.
* **`.description` keys live in the flat keyspace beside their labels**: both
  `status.process.READY` and `status.process.READY.description` exist and are
  looked up by exact key — no prefix scanning, no key nesting. A missing
  `.description` means "show the label alone", never an error.
* **Free-form caveat (normative).** The five suggestion families label values
  of fields that stay free-form by design (§9.2.4). Serving a label for a known
  token MUST NOT be read as a closed vocabulary: these families are **not**
  served by `vocab/get`, the server keeps accepting and storing arbitrary text
  in those fields, and clients MUST render unknown user text **verbatim** — no
  filtering, no coercion to a token, no "unknown value" state. The label map is
  display sugar over an open field; the token set may grow additively and never
  narrows the accepted input space.

**(b) Completeness rule enforced this round.** `en.json` stays complete with no
orphans (§6.3.3, unchanged). New: **for every family the server actually
serves, all 29 locales MUST be complete** at the end of this wave, enforced by
pytest over the served keyspace. This closes the gap that motivated the round —
`en.json` carries 212 keys against 185–191 elsewhere, so genuinely-served
strings (e.g. `settings.*.description`) reach non-English users in English. The
§6.3.3 sparse allowance is **not** withdrawn: it remains the rule for future
additions, so a new token can still ship English-only over the en overlay
without 29 hand translations gating the commit. The invariant is "complete at
the end of the wave that serves it", not "translated before it may exist".

The two rules are reconciled in one enforcement point:
`tests/test_ui_contract_i18n_assets.py` asserts, per locale, both directions
against `en.json` — subset (§6.3.3: never invent a key) and superset over the
**served keyspace**, defined as `en.json` minus the explicit
`SPARSE_EXEMPT_KEYS` set. Invoking the sparse allowance for a new token is
therefore an entry in that set, reviewed like any other diff, rather than a
silent locale-wide gap; the set is empty as of this wave. Placeholder integrity
(a) is checked in every locale too — the `{...}` spans of a translated value
must match the spans of its `en.json` counterpart, name for name and count for
count, in any order.

**(c) Client tiering.** Clients SHOULD prefer the server strings for every
family above, through the unchanged §6.3.5.1 preference order (server string →
client bundle → humanized token). Client bundles keep these keys as **tier 2
only** — the offline / pre-0.94-server fallback. No client deletes its bundle in
this round: PWA offline mode and installs still on ≤0.93 depend on it, and the
bundles remain the only source for genuine client chrome (navigation, editor
labels, connection UI), which is out of the machine domain and is not served.

---

## 7. Implementation plan (0.91)

Ownership zones are file-disjoint so agents can run in parallel. Zone I-A0 and
I-A have no dependencies on each other's files but I-B/I-C/I-D need both, so
I-A0 and I-A merge first (or later zones develop against the frozen public
signatures in this spec). Zone C-D integrates the card last.

### 7.1 Integration side (`melitta-ha-integration`)

**Zone I-A0 — client-side base-recipe cache (BLE/client layer; NEW — prerequisite).**

The Melitta base-recipe composition cache currently lives on the
`MelittaRecipeSelect` entity (`self._all_recipes`) as flattened
display-name-keyed dicts — the raw `RecipeComponent` (needed for `blend` and as
`build_icon_spec` input) is discarded at preload, and `panel_api` ships
`base_recipes: []`. The contract builder must not depend on an entity's private
state.

* `ble_client.py` (recipes mixin): new cache `client.base_recipes: dict[int,
  MachineRecipe]` keyed by `RecipeId` int, storing **raw**
  `MachineRecipe`/`RecipeComponent` objects; populated by the existing
  post-connect preload and refresh paths, next to `_directkey_recipes` /
  `_profile_names`; bump a `recipe_cache_generation` counter on every (re)fill —
  a `contract_fingerprint` input.
* `select.py`'s `_preload_recipes` / `_on_recipe_refresh` become consumers of
  this cache, deriving their display attrs via `ui_contract.component_to_tokens`.
* `panel_api.py` `_ws_recipes_list` fills `base_recipes` from the same cache
  (removing the documented gap in its docstring).
* Tests: cache fill on preload, generation bump, select.py parity with previous
  attribute output.
* **Must land before I-B and I-C.**

**Zone I-A — contract builder module (no existing files touched).**

* `custom_components/melitta_barista/ui_contract.py` (new):
  * `CONTRACT_VERSION: int = 1`, `ICON_SPEC_VERSION: int = 1`.
  * Status vocab constants generated from the real enums
    (`[m.name for m in MachineProcess]` etc.) — never hand-copied lists.
  * `build_status_tokens(status, connected) -> dict` — the §3.4 block B
    attributes (manipulation_token null-vs-"NONE" rule included).
  * `build_bridge_attributes(entry, client) -> dict` — §3.4 block A, including
    `compute_contract_fingerprint(...)` per §5.1.
  * `build_vocabularies(caps) -> dict`, `build_capabilities_block(client) -> dict`
    (incl. `hopper_count` unknown-type→2 rule, `has_milk_system`,
    `supports_freestyle`, `tolerated_brew_manipulations` int→name serialization,
    §3.5).
  * `build_icon_spec(components, additives=()) -> dict | None` implementing §4
    (pure; accepts token-level component dicts, not protocol structs; emits
    `fill_level`; normalizes `color_hint` to `#RRGGBB`/None).
  * `component_to_tokens(recipe_component) -> dict` — protocol
    `RecipeComponent` → token dict; **omits `blend` for wire byte 0/unknown**.
  * `build_ui_contract(entry, client) -> dict` — the full §3.3 document; raises
    a typed error the WS layer maps to `contract_not_ready` when
    `client.capabilities is None`.
  * Logger literal `"melitta_barista"`; docstrings on all public symbols.
* `tests/test_ui_contract.py` (new): vocab completeness vs enums; capability
  derivations for both example machines **plus the unknown-machine_type Melitta
  case (hopper_count == 2)**; tolerated-manipulation serialization (900 family
  `(11,)` → `["MOVE_CUP_TO_FROTHER"]`, unknown ints omitted); blend byte 0
  omitted; icon determinism (same input → equal dict); §4.10 AND §4.11 worked
  examples byte-exact; fill_level values; fraction-sum invariant; category
  defaults; empty-composition → `None`; additive layers + color_hint
  normalization; fingerprint changes on machine_type/recipe-generation change.

**Zone I-B — WS endpoint (panel_api.py + API docs).**

* `custom_components/melitta_barista/panel_api.py`: `_ws_ui_contract` sync
  `@callback` handler per the `_ws_status` pattern; schema
  `{vol.Required("entry_id"): str}`; register via
  `_wrap_sync_with_schema(..., admin=False)` inside
  `async_register_panel_websocket`; errors `entry_not_found` /
  `client_not_ready` / **`contract_not_ready`** (§2.2).
* `docs/SOMMELIER_API.md`: document the endpoint (request/response, link here).
* `tests/test_panel_ui_contract_ws.py` (new file — do not touch existing panel
  tests): registration, happy path against a mocked client, all three error
  codes (incl. capabilities-None → `contract_not_ready`), non-admin access.

**Zone I-C — entity surface (sensor.py, select.py).**

* `sensor.py`:
  * `MelittaConnectionSensor` gains the §3.4 block A bridge attributes via
    `ui_contract.build_bridge_attributes`; no `available` override is added
    (stays always-available); `native_value` unchanged.
  * `MelittaStateSensor.extra_state_attributes` gains the §3.4 block B tokens
    via `ui_contract.build_status_tokens`; `native_value` and the availability
    gate stay byte-identical (frozen, §5.2.3). `info_messages` content unchanged
    (now a frozen token list; no alias key).
* `select.py`: in `MelittaRecipeSelect.extra_state_attributes`, add `"icon":
  build_icon_spec(...)` and per-component `"blend"` inside each `recipes` entry
  (sourced from the I-A0 cache). **Recorder guard:** the selected recipe's dict
  is also flattened to top-level attributes and only `recipes` is
  recorder-excluded — flatten only scalar `c1_`/`c2_` keys; structured extras
  (`icon`) live exclusively inside `recipes`. Test asserts top-level attrs stay
  scalar.
* `tests/test_sensor_status_tokens.py`, `tests/test_select_icon_attr.py` (new
  files): bridge attrs always present incl. disconnected; token attrs across
  connected/statusless/unknown-code paths (manipulation_token null-vs-NONE);
  fingerprint attribute updates on refinement; icon presence and stability in
  recipe attrs; top-level-scalar invariant; regression: both sensors'
  `native_value` and availability unchanged.

**Zone I-D — sommelier surface + release chores (runs after I-A0–I-C merge).**

* `sommelier_api.py`: attach `icon` to generate results / saved-recipe /
  favorites payloads via `build_icon_spec` with additive slots (§3.9).
* `tests/test_sommelier_icon.py` (new).
* Release integration (single owner to avoid merge noise): `manifest.json` →
  `0.91.0`, `CHANGELOG.md` entry (English), tag/release per project rules.
* No integration-side i18n changes in 0.91 (tokens are not user-visible strings
  server-side; card-side display keys are Zone C-B).

All tests: `.venv/bin/python -m pytest tests/ --timeout=10`.

### 7.2 Card side (`melitta-barista-card`)

**Zone C-A — contract client (new files only).**

* `src/contract.ts` (new): TS types `UiContract`, `IconSpec`, `Layer`, `Foam`,
  `StatusTokens`, `BridgeAttrs`; `SUPPORTED_CONTRACT_VERSIONS = [1]`;
  `readBridgeAttrs(connectionEntityAttrs): BridgeAttrs | null` — returns null
  unless `contract_version` is present AND supported (the attribute-surface
  version gate, §5.3.3);
  `readStatusTokens(stateEntityAttrs, bridge): StatusTokens | null`;
  `fetchUiContract(hass, entryId): Promise<UiContract | null>` with **failure
  classification** per §2.3.5 (durable vs transient, `console.warn` once);
  session cache keyed by `entry_id + contract_fingerprint`; transient-retry
  hook driven by the bridge `connected` false→true transition;
  `validateContract` (version + minimal structural check → null on mismatch;
  MUST NOT reject unknown token values).
* `tests/contract.test.ts` (new): parse §3.7/§3.8 example payloads verbatim as
  fixtures; unknown-version rejection (WS and attribute surface); malformed →
  null; transient-vs-durable classification; retry-on-connected-transition;
  fingerprint-keyed cache invalidation.

**Zone C-B — machine-state tokens + token display strings.**

* Files owned: `src/machine-state.ts`, `src/const.ts` (**STATE_COLORS block
  only**), `src/localize/languages/*.json` (all 29). C-D consumes but must not
  edit these.
* `src/machine-state.ts`: `computeMachineStatus` gains a token-first path — if
  the injected reader yields non-null `StatusTokens` (which already implies the
  version gate passed), derive `isConnected`/`isReady`/`isBrewing`/`isBusy`/
  activity/color from tokens (§3.4 rules; state-sensor-unavailable → offline
  body); otherwise fall through to the existing string-matching branch
  unchanged.
* **Normative v1 token→card-i18n-key map** (the display-string story until §6.3
  lands): `PRODUCT`→`brewing`, `READY`→`ready`, `SWITCH_OFF`→`off`,
  `CLEANING`/`EASY_CLEAN`/`INTENSIVE_CLEAN`→`cleaning`, `DESCALING`→`descaling`,
  `BUSY`→`busy`; NEW keys added to all 29 locale bundles for `FILTER_INSERT`,
  `FILTER_REPLACE`, `FILTER_REMOVE`, `EVAPORATING`, plus `activity.*`
  (GRINDING/COFFEE/STEAM/WATER/PREPARE) and `action.*` (all manipulation
  tokens). Unknown tokens fall back to `localizeOptional` raw-token display.
* `STATE_COLORS` gains token keys (`READY`, `PRODUCT`, …) alongside the legacy
  lowercase-English keys.
* `tests/machine-state.test.ts`: extend with token fixtures (both modes must
  pass; legacy assertions stay untouched); i18n-key-map coverage test.

**Zone C-C — icon renderer from spec.**

* `src/icons.ts`: extract a pure `computeIconGeometry(spec: IconSpec, size):
  IconGeometry` (path data, layer rects, fills as role/intensity descriptors —
  no Lit, testable without DOM), plus a thin `coffeeIconSvgFromSpec(spec, size,
  uid)` template wrapper reusing the existing geometry primitives; map `glass`
  to cup/tall shapes (unknown → cup); **fill to `fill_level`, layers by
  `fraction` within it**; coffee darkness from `intensity`; crema stripe; foam
  cap; steam wisps from `spec.steam`; `color_hint` applied as escaped fill data
  only, optionally contrast-adjusted; unknown role → neutral layer; invalid
  spec or unknown `spec_version` → existing `DEFAULT` path. Name-keyed `DRINKS`
  table remains as legacy fallback.
* `tests/icons-spec.test.ts` (new): geometry assertions for the §3.7
  Espresso/Latte-Macchiato and §3.8 Cappuccino specs (incl. fill_level
  heights); unknown-role, unknown-spec_version and null-spec fallbacks.

**Zone C-D — card wiring (single owner, after C-A..C-C).**

* `melitta-barista-card.ts`: on prefix resolution read `BridgeAttrs` from
  `sensor.<prefix>_connection` and `StatusTokens` from `sensor.<prefix>_state`;
  if token mode, `fetchUiContract` with `bridge.entry_id`, refetch on
  `contract_fingerprint` change, transient-retry on `connected` transition;
  detection re-evaluated each `willUpdate` (not sticky); pass contract + tokens
  down; recipes section prefers per-recipe `icon` from the `recipes` attribute /
  contract catalog over name lookup; freestyle pickers and portion sliders
  prefer `contract.vocabularies.freestyle` / `contract.limits` over `const.ts`;
  contract-fetch failure degrades only contract-derived features (token status
  stays active). Tracked-ids list already contains both sensors (no change
  needed for re-render).
* `src/sections/*.ts` touch-ups strictly limited to consuming props C-D passes
  (no section reads `hass` for contract data directly).
* Version bump to 2.4.0, dist rebuild, `npm test` (vitest) green including all
  legacy-mode suites.

### 7.3 Sequencing

```mermaid
flowchart LR
  A0[I-A0 client recipe cache] --> B[I-B WS endpoint]
  A0 --> C[I-C entity attrs]
  A[I-A ui_contract.py + tests] --> B
  A --> C
  A --> D[I-D sommelier + release]
  B --> D
  C --> D
  CA[C-A contract.ts] --> CD[C-D card wiring]
  CB[C-B tokens + i18n keys] --> CD
  CC[C-C icons from spec] --> CD
  D -. release 0.91.0 .-> CD
```

Integration 0.91.0 ships first and is verified with the *old* card against the
matrix (§5.4, top-right cell) on the live HA before the card 2.4.0 release flips
the bottom-right cell on. The live verification checklist includes: machine off
at HA start (bridge attrs present, state sensor unavailable), reconnect
(fingerprint bump observed), and recipe-preload catch-up (second fetch carries
components).

### 7.4 Follow-ups (post-0.91 amendments)

* **Panel as consumer.** The integration's own panel
  (`custom_components/melitta_barista/www/`) is a contract consumer like the
  card and PWA: it renders the per-recipe `icon` IconSpecs already carried by
  the sommelier recipe/favorites/history payloads (§3.9) — previously received
  but never rendered — and the §3.10 brand badge (wordmark + accent colors,
  plus the user-supplied logo image when `logo_url` is non-null, with wordmark
  text as the fallback). `icon: null` / absent `brand_theme` fall back to the
  panel's generic drink glyph / neutral unbranded chrome per §5.3.2.

---

## 8. Implementation plan (0.92)

Ownership zones are file-disjoint per the §7 conventions. Integration ships
first as **0.92.0b1** (beta), is verified against the shipped 2.6.1 card
(invisible-additive check, top-right cell of the §5.4 amendment matrix), then
the card ships **2.7.0**. All integration tests:
`.venv/bin/python -m pytest tests/ --timeout=10`.

### 8.1 Integration side (`melitta-ha-integration`, 0.92.0b1)

**Zone I-E — catalog builders (`ui_contract.py`, `coffee_platform/domain.py`
capability fields, the `async_setup_entry` version stash, + I-E tests only).**

* `MachineCapabilities` gains `verified_maintenance_processes:
  tuple[int, ...] | None = None` (§6.2.6; Melitta families `None`, all Nivona
  families `()` in b1) and `RecipeDescriptor` gains the authored `name_key`
  field (§6.3.6) — seeded across all family tables.
* `async_setup_entry`: resolve the manifest version once via
  `async_get_integration` (existing pattern) → `client.integration_version`;
  both fingerprint call sites read it from `client` (§5.1 single-source rule).
  `ui_contract.py` stays import-pure — no manifest I/O inside it.
* `build_parameters(capabilities_block) -> dict` — §6.1.3 families from the
  same const maps/filters as `build_vocabularies`; scopes per
  `supports_freestyle`/`supports_brew_overrides`.
* `build_action_catalog(client, capabilities_block) -> list[dict]` — §6.2.2
  contents; gating on `supported_extensions` (HC/HJ), `supports_recipe_writes`,
  `supports_factory_reset`, and `verified_maintenance_processes`;
  `switch_off.requires == ["connected"]` encoded here; `ActionParam`s built
  from the live service schemas, not hand copies.
* `build_ui_contract` gains `parameters`, `actions`,
  `forbidden_combinations: []`, `strings_version`; recipe entries gain
  `name_key`; `compute_contract_fingerprint` gains the
  `client.integration_version` input.
* Docstrings on every new public symbol (boy-scout rule); logger literal
  unchanged.
* `tests/test_ui_contract_parameters.py`: mirror invariant
  (`parameters.<family>.tokens == vocabularies.freestyle.<family>`,
  `parameters.portion_ml == limits.portion_ml`, byte-exact); §6.1.4 Melitta TS
  and Nivona 700 payloads pinned verbatim; scope gating; 3-level intensity
  slice; single-hopper blend.
* `tests/test_ui_contract_actions.py`: full catalog per brand pinned;
  `switch_off` connected-only; factory-reset entries destructive + gated (8000
  → `available: false`); HC/HJ/HD gating of `brew_directkey` /
  `brew_freestyle` / `reset_recipe`; **family 700 → all process-starting
  maintenance entries `available: false`** (§6.2.6); `ActionParam`s diffed
  against `BREW_FREESTYLE_SCHEMA` / `BREW_DIRECTKEY_SCHEMA` /
  `RESET_RECIPE_SCHEMA`; every service-kind entry carries `entity_suffix`;
  fingerprint changes on integration-version change; **bridge-vs-document
  fingerprint equality invariant**; per-family `name_key` sets pinned
  (all Nivona families incl. 8000 chilled variants).

**Zone I-F — string assets (`ui_strings/*.json` × 29, seeding script, asset tests).**

* Create `custom_components/melitta_barista/ui_strings/{en,…}.json` per
  §6.3.3/§6.3.4: copy from the dormant `entity.*` blocks, port card/panel
  bundle translations (both are this project's own repos), newly author the
  listed gaps (info messages, blend labels, recipe categories, 4 action
  labels, Nivona recipe names, group labels) in English + 29 translations for
  the 0.92 set. `strings.json`/`translations/*.json` are NOT touched.
* One-off seeding script in `scripts/` (not shipped in the component) for the
  mechanical copy/port incl. the §6.3.6 name derivation; hand-review of
  authored strings.
* `tests/test_ui_contract_i18n_assets.py`: `en.json` completeness — every
  builder-emittable token keyed (process/sub_process/manipulation/info_message
  enums, const-map value tokens, DirectKey categories, Melitta name_keys,
  **every registered family's `RecipeDescriptor.name_key`** via the shared
  derivation/authored values, §6.2.2 action tokens + `_groups`); no orphan
  keys in `en.json`; `values.intensity` has exactly the 5 server tokens (no
  `extra_strong`); other locales validated for key-subset-of-en only (sparse
  allowed, §6.3.3). Imports builder token constants read-only — the
  dependency points I-E → I-F.
* Definition-of-done includes a local hassfest run (translations untouched).

**Zone I-G — WS endpoint (`panel_api.py`, `docs/SOMMELIER_API.md`, new test file).**

* `_ws_i18n_get` **async** handler: §6.3.1 locale resolution against the known
  locale set; executor-based JSON loader with en-overlay merge, cached in
  `hass.data[DOMAIN]` per resolved locale (§6.3.3); domain filter;
  `strings_version` from `client`-independent cached manifest version;
  `_send_versioned` envelope. Registered `admin=False` inside
  `async_register_panel_websocket` (auto-listed by `api/info`).
* `tests/test_panel_i18n_ws.py`: `de-DE`→`de` and `xx`→`en` resolution;
  per-key en overlay (sparse locale fixture); domain filtering; unknown
  domains ignored; `strings_version` equals manifest version; non-admin
  access; flat-key format pinned (`status.process.READY`); loader cache hit
  (one executor read per locale).

**Zone I-H — panel consumers (`www/` only; after I-G).**

* `melitta-panel.js`: fetch `i18n/get` once per locale alongside the contract
  fetch; feed a pure resolver (§6.3.5.6 shape) passed down as props.
* `melitta-status.js`, `melitta-brew-wizard.js`, `melitta-sommelier.js`:
  render status/manipulation/value tokens via server strings → panel bundle →
  humanized token (replacing raw-token rendering).
* `melitta-recipes.js`: prefer `contract.parameters` per-parameter with the
  §6.1.5 three-tier fallback; DirectKey category labels via
  `values.directkey_category.*`.
* No JS test harness exists for `www/` — verification via the live-HA manual
  checklist (§8.3).

**Zone I-I — release (single owner, after I-E…I-H merge).**

* `manifest.json` → `0.92.0b1`; `CHANGELOG.md` (English); merge this amendment
  into `docs/UI_CONTRACT.md` per the merge instructions; tag `v0.92.0b1`,
  GitHub prerelease, per project release rules.

### 8.2 Card side (`melitta-barista-card`, 2.7.0)

**Zone C-E — contract types + i18n registry (new/typed surface; LANDS FIRST).**

* `src/contract.ts`: additive optional types `ParameterDescriptor`,
  `ActionEntry`, `ActionInvocation`, `ActionParam`, `parameters?`, `actions?`,
  `forbidden_combinations?`, `strings_version?`, `Recipe.name_key?`.
  **`validateContract` is not extended** — it MUST NOT require any v2 field
  (§6.0.1).
* `src/server-i18n.ts` (new), split per §6.3.5.6: (a) pure registry —
  `setServerStrings`, `serverString`, `resetServerStrings`, zero HA imports;
  (b) `fetchServerStrings(hass, locale)` with durable-`unknown_command`
  classification and `locale + strings_version` caching. Only (a) is imported
  by label/format modules; (b) is called from C-I wiring.
* `tests/contract-v2.test.ts`, `tests/server-i18n.test.ts`: v2 payload parsing
  (both §6.1.4 fixtures verbatim); v1 payload without v2 fields still valid;
  unknown descriptor/invocation kinds dropped; registry set/get/reset; cache +
  durable fallback; fingerprint-change re-fetch with matching-`strings_version`
  short-circuit.

**Zone C-F — parameter wiring (`src/contract-wiring.ts`, `src/sections/controls.ts`; after C-E).**

* Generalize `resolveFreestyleVocab` → `resolveParameters(contract)` with the
  normative three-tier fallback (§6.1.5): `parameters.<family>` → v1
  `vocabularies.freestyle.<family>`/`limits.portion_ml` → `const.ts`, per
  parameter (keeping the existing `stringList`/`portionLimit` guards); range
  descriptors drive sliders (min/max/step/unit), enums drive segments;
  `applies_to` filters per component process; unknown kind/scope → that
  parameter falls back / is not rendered.
* Migrates the `controls.ts` call sites to `displayNameFor(family, token)`
  (signature frozen in §6.3.5.7; internals are C-H's).
* Tests: three-tier fallback per parameter (incl. v1-server → tier 2, not tier
  3); 3-level intensity; brew-override scope not rendered in freestyle UI.

**Zone C-G — action catalog logic (`src/action-catalog.ts` NEW + `src/sections/maintenance.ts`; after C-E).**

* Pure module `src/action-catalog.ts` (no Lit, no hass — vitest-testable):
  `resolveActionCatalog(contract)` — parse, drop unknown-invocation-kind
  entries, `available:false` filtering, group ordering (§6.2.3), legacy-array
  fallback when `actions` is absent; `evalRequires(requires, {statusTokens,
  connected})` with unknown-token→satisfied; confirm/destructive policy; and
  `planActionInvocation(entry, prefix, formState)` returning
  `{button: suffix} | {domain, service, data}` with `entity_id =
  button.<prefix>_<entity_suffix>` always set for service kind.
* `maintenance.ts` becomes a thin renderer over the resolved list:
  catalog-drives `cleaning`/`filter`/`power`/`danger` + unknown groups
  (§6.2.5.2 — brew/control entries stay informational; the card keeps its
  bespoke brew sections, so `params_ref` handling never enters maintenance
  rendering); icons from `entry.icon` with `mdi:cog` default; group headers
  via `actions._groups.*` → bundle → humanized; labels/descriptions per
  §6.3.5.1; destructive styling + forced confirm.
* **Does not touch `melitta-barista-card.ts`** — dispatch wiring is C-I's.
* `tests/action-catalog.test.ts` (pure): catalog resolution, group order,
  legacy fallback, unknown-kind dropped, `evalRequires` incl. switch_off
  connected-not-ready, destructive⇒confirm, service plan carries `entity_id`,
  param assembly pinned to the §6.2.2 shapes. Rendering itself is verified by
  the §8.3 live checklist (no DOM harness exists).

**Zone C-H — display-string preference (`src/machine-state.ts` label fns, `src/format.ts`; after C-E).**

* `processLabel`/`activityLabel`/`actionLabel` gain the server-string
  preference layer via the **pure registry only** (§6.3.5.6);
  `PROCESS_TOKEN_I18N`/`ACTIVITY_TOKEN_I18N`/`ACTION_TOKEN_I18N` and the 29
  card bundles stay untouched as the fallback layer.
* `format.ts` gains `displayNameFor(family, token)` (§6.3.5.7); legacy
  `displayName(token)` unchanged. Call-site migration belongs to C-F.
* Tests: preference order per key; missing server key falls through; module
  purity preserved (no new imports beyond the registry); legacy suites
  unchanged.

**Zone C-I — wiring + release (single owner, after C-E…C-H).**

* `melitta-barista-card.ts`: fetch server strings when token mode is active
  (triggers per §6.3.2: locale change, fingerprint change via the existing
  `noteBridgeUpdate` path) → `setServerStrings`; pass
  `parameters`/`actions`-derived props and resolvers down (sections never read
  `hass` directly, per v1 rule); wire `planActionInvocation` results into
  `pressButton`/`hass.callService` dispatch; version `2.7.0`; dist rebuild;
  full vitest run green including all legacy-mode suites.

### 8.3 Sequencing

```mermaid
flowchart LR
  IE[I-E catalog builders] --> IF[I-F ui_strings assets]
  IF --> IG[I-G i18n WS endpoint]
  IG --> IH[I-H panel consumers]
  IE --> IH
  IE --> II[I-I release 0.92.0b1]
  IH --> II
  CE[C-E types + i18n registry] --> CF[C-F parameters]
  CE --> CG[C-G action catalog]
  CE --> CH[C-H display strings]
  CF --> CI[C-I wiring 2.7.0]
  CG --> CI
  CH --> CI
  II -. beta verified with card 2.6.1 .-> CI
```

Live-HA beta checklist before 2.7.0: 2.6.1 card fully unchanged against
0.92.0b1 (invisible-additive proof); panel shows translated status tokens in a
non-English HA locale; `i18n/get` en-overlay observed for an untranslated key;
fingerprint change observed across an integration upgrade, and exactly one
i18n re-fetch armed by it; catalog-driven maintenance rendering on the panel;
Nivona entry (when available) serves override parameters, factory-reset
entries, and maintenance entries `available: false` (per §6.2.6 until the #36
matrix lands).

---

## 9. The v3 feature set (0.93, normative)

### 9.0 Versioning decision (normative)

**All three v3 features ship additively within `contract_version: 1`.** No bump.
This is the third application of the §6.0 precedent and the same reasoning
applies verbatim: nothing in 0.93 removes, renames, re-types, or re-cases a
v1/v2 surface, and a bump would push every shipped 2.4–2.7 card into full
legacy mode for zero benefit.

Consequent normative rules (extending §6.0's, which remain in force):

1. **Per-feature presence gating.** Clients detect each v3 feature by
   field/command presence, never by version: `settings` present →
   descriptor-driven settings UI; `directkey` present → contract-driven
   DirectKey/profile model; `vocab/get` answered → vocab-driven sommelier
   enums. `validateContract` MUST NOT require any v3 field.
2. `SUPPORTED_CONTRACT_VERSIONS` stays `[1]` in all clients.
3. Unknown values inside v3 structures follow §5.3.2: unknown setting
   `control` → that entry falls back to the client's hardcoded rendering for
   that setting token, or is skipped if the token is also unknown; unknown
   setting/action `group` → rendered after known groups under a humanized
   header (§6.2.3 rule); unknown `levels`/`options` token → the raw numeric
   value is shown; unknown vocab family in `vocab/get` → ignored; unknown
   i18n keys → ignored.
4. **Per-feature degradation** (§6.0.4 extended unchanged): failure or absence
   of any one v3 feature never degrades another v3 feature, any v2 feature,
   or v1 behaviour.

### 9.1 Settings descriptors

Machine settings today are three parallel server mechanisms (Melitta
hand-coded switch/number tables, Nivona capability-driven descriptor
selects/numbers, per-family gating via `TS_ONLY_SETTINGS` /
`unsupported_generic_setting_ids` / `_MODEL_SETTINGS_EXCLUDE`) that both
clients re-hardcode as identical suffix tables and level-label maps, hiding
missing entities by existence check. v3 serves the resolved, per-machine
settings surface as data, so clients render a settings section from the
contract and the hardcoded tables become fallback tier 3.

#### 9.1.1 Shape

Additive top-level contract field `settings: SettingEntry[]`. **Served order is
the normative render order** (grouped; groups in the §9.1.3 order).

```ts
settings?: SettingEntry[];

interface SettingEntry {
  setting: string;            // lower_snake stable token (§3.1); byte-equal to
                              // the entity suffix — equality is TEST-ENFORCED
                              // (§9.1.2 rule 1), one identity, two uses
  control: string;            // known: "switch" | "number" | "select" (open, §5.3.2)
  group: string;              // known: "brew" | "water" | "power" | "system" (open)
  icon?: string;              // "mdi:<name>"; absent/malformed → mdi:tune
  entity: {
    domain: string;           // "switch" | "number" | "select"
    entity_suffix: string;    // clients assemble <domain>.<prefix>_<entity_suffix>,
  };                          // exactly the §6.2.1 anchor convention
  writable: boolean;          // false → render read-only (descriptor.is_writable)
  // control == "number":
  min?: number; max?: number; step?: number;
  unit?: string;              // known: "min" | "h"
  display?: string;           // known: "slider" | "box" — advisory hint only
  levels?: { value: number; token: string }[];   // semantic ladder for discrete scales
  // control == "select":
  options?: { value: number; token: string | null; label: string }[];
}
```

Rules:

* `levels`/`options` **tokens are the semantic identity; values are the wire
  mapping.** The same token ladder is served with different values where the
  hardware differs — Melitta water hardness is 1-based (`1→soft … 4→very_hard`),
  Nivona is 0-based (`0→soft … 3→very_hard`) — resolving the cross-surface
  divergence as data instead of client special-casing.
* `options[].label` **mirrors the select entity's current option strings,
  derived at build time from the descriptor tables in
  `brands/nivona/_options.py`** (single source; the byte-equality pytest is
  structural, not aspirational). Changing an option string is an
  **entity-layer breaking change** (users' HA automations match on it),
  governed outside this contract; contract clients are unaffected by any
  future change because they write the *served* label (§9.1.6 rule 4) — a
  later token-valued-options migration of the entity layer would not break
  them.
* `token` is present only where a semantic ladder has been authored. 0.93
  tokenizes: `_HARDNESS_OPTIONS` → `soft medium hard very_hard`, and the
  **shared** `_OFF_ON_OPTIONS` / `_TEMP_ON_OFF` tables → `off on`. The shared
  tables are annotated **once**, so every descriptor key referencing them
  (≈10 across the Nivona families — `off_rinse`, `cup_heater`,
  `milk_products_active`, `direct_start_deactivated`, `touch_lock`,
  `auto_on_deactivated`, `save_energy`, `tank_light`, `power_on_rinse`, plus
  `coffee_temperature` on the 8000) emits tokenized options. Their labels
  localize through the shared `settings._levels.*` tier (§9.1.4), so the
  fan-out costs **zero** extra i18n keys. All other option tables ship
  `token: null` + label-only in 0.93; tokenizing further tables later is
  additive.
* A number entry without `levels` (e.g. `auto_off_after`, `language`) is a
  plain numeric control; clients MUST NOT invent level labels for it.

#### 9.1.2 Token, binding, and gating rules (normative)

1. `setting` tokens are byte-equal to the entity suffixes clients already
   address — **and this equality is test-enforced, not "by construction"**:
   * *Melitta:* suffixes derive from slugified English entity names; the Zone
     I-J pytest pins `slugify(name) == token` for every
     `MELITTA_SETTING_TABLES` row (`energy_saving`, `auto_bean_select`,
     `rinsing_disabled`, `water_hardness`, `auto_off_after`,
     `brew_temperature`, `language`, `filter`).
   * *Nivona:* `BrandSettingSelect`/`BrandSettingNumber` set
     `_attr_translation_key = descriptor.key` with `has_entity_name`, so HA
     derives the entity_id suffix from the **slugified translated name**
     (`entity.<domain>.<key>.name` in the server-language `translations/`
     file). Today every pair matches only because those name strings are
     English. **Anchored-entity naming invariant:** for every Nivona
     `SettingDescriptor` whose key is served in this block,
     `slugify(entity.<domain>.<key>.name) == descriptor.key` MUST hold in
     **all 29** `translations/*.json` files, pinned by a Zone I-J pytest.
     Those specific name strings are therefore frozen to slug-equal
     (effectively English) forms; localizing them is forbidden while they
     anchor the contract. (The alternative — dropping
     `_attr_translation_key` with an entity-registry migration — is
     deliberately deferred; noted in `docs/BACKLOG.md`.)

   Note the deliberate token distinction: Melitta's id-22 entity is token
   `brew_temperature` (its entity name), while Nivona's id-102 descriptor
   keeps its key `temperature` — two different tokens for two different
   settings; no collision with the freestyle `temperature` family because
   settings live in their own keyspace.
2. The Nivona `water_hardness` and Melitta `water_hardness` tokens are
   **intentionally identical** — one label/level keyspace serves both brands
   (§9.1.4), despite the 0-based/1-based value offset (carried per-entry in
   `levels`/`options` values).
3. `rinsing_disabled` binds to the existing switch entity semantics (on =
   rinsing disabled). The contract carries **no inversion logic**; the machine
   register's inverted sense (`RINSING_OFF`, id 18) is an entity-layer
   concern, invisible here.
4. Per-brew parameters are **not settings**: freestyle selects/numbers and
   Nivona brew-override numbers stay exclusively in `parameters` (§6.1). The
   `settings` block carries machine configuration only.
5. **Predicate-equality invariant.** The builder and entity registration
   consume the *same tables and predicates* — `MELITTA_SETTING_TABLES`
   (incl. its TS-only flags, replacing `TS_ONLY_SETTINGS` as data),
   `capabilities.unsupported_generic_setting_ids` (family), and the
   `capabilities.settings` descriptors post-`_MODEL_SETTINGS_EXCLUDE` (model)
   — *given the same `(family, model, machine_type)` input*. A Zone I-J
   pytest evaluates **both predicate implementations** across all family ×
   machine-type combinations and pins set equality. This is **predicate**
   equality, not live-registry equality: entities register once at platform
   setup with the then-known machine type (name-detected, or the assume-TS
   default), while the contract block re-evaluates per request with the
   HR-refined machine type. After a mid-session refinement (e.g. a handshake
   confirming BARISTA_T on a machine whose name detection failed) the
   contract drops `auto_bean_select` and the extra profile slots via the
   fingerprint, while the stale entities persist until the entry reloads —
   this divergence is **expected and intended**: contract-driven clients
   render from the contract, the entity-absence rule (§9.1.6 rule 2) governs
   the opposite direction, and the entity set converges on reload. For
   exactly this reason, client entity-existence gating **remains REQUIRED**
   inside tier 1 (it is not "redundant").
6. Unknown `machine_type` (pre-refinement) follows the existing TS-assumption
   precedent (`PROFILE_COUNTS`, `get_available_recipes`): `auto_bean_select`
   is served. Post-handshake machine-type refinement changes the block and is
   delivered by the fingerprint (`machine_type` is a §5.1 input), per rule 5's
   convergence semantics.
7. **Icon provenance.** `build_settings_block` reads icons at build time from
   the same `const.py` tables the entities register with (Melitta) — icon
   parity is structural, and the §9.1.3 table below documents it. Nivona
   descriptor entries serve `mdi:tune`.

#### 9.1.3 Contents served in 0.93 (normative tables)

Group order: `brew, water, power, system`, then unknown groups in served
order. Group headers localize via `settings._groups.<group>`.

**Melitta (both families; Δ = gated):**

| setting | id | control | group | binding | data | icon |
| --- | --- | --- | --- | --- | --- | --- |
| `auto_bean_select` | 16 | switch | brew | `switch` / `auto_bean_select` | Δ absent on confirmed BARISTA_T (TS-only flag) | mdi:grain |
| `brew_temperature` | 22 | number | brew | `number` / `brew_temperature` | 0–2/1, slider, levels `low normal high` | mdi:thermometer |
| `water_hardness` | 11 | number | water | `number` / `water_hardness` | 1–4/1, slider, levels `soft medium hard very_hard` | mdi:water-opacity |
| `filter` | 91 | number | water | `number` / `filter` | 0–1/1, slider, levels `off on` | mdi:filter-outline |
| `rinsing_disabled` | 18 | switch | water | `switch` / `rinsing_disabled` | | mdi:water-off |
| `energy_saving` | 12 | switch | power | `switch` / `energy_saving` | | mdi:leaf |
| `auto_off_after` | 13 | number | power | `number` / `auto_off_after` | 15–240/15, box, unit `min`, no levels | mdi:timer-off-outline |
| `language` | 15 | number | system | `number` / `language` | 0–15/1, box, **no levels** (register mapping unverified — issue #10 79x precedent; served numeric-only) | mdi:translate |

The icon column mirrors the entity icons in `MELITTA_SETTING_TABLES` (the
values `switch.py`/`number.py` register today — including `mdi:water-opacity`
and `mdi:filter-outline`, which earlier drafts misquoted). Errata rule
(extends the §6.2.2 errata precedent): where a client bundle icon differs
(card `energy_saving` mdi:lightning-bolt), the served value — identical to
the entity icon — is normative.

**Nivona:** one `select` entry per post-exclusion `SettingDescriptor`
(options from the descriptor, `writable` from `is_writable`, icon mdi:tune,
group per the builder table: `temperature`/`coffee_temperature`/
`milk_temperature`/`milk_foam_temperature`/`profile`/`cup_heater` → brew;
`water_hardness`/`off_rinse`/`power_on_rinse` → water; `auto_off`/
`save_energy`/`auto_on_deactivated` → power; everything else → system) and
one `number` entry per options-less descriptor (group power; unit `h` for
hours, `min` for minutes). **Options-less number ranges come from a single
shared pure helper** `nivona_number_range(descriptor)` (hours → 0–23,
minutes → 0–59, else 0–255), consumed by *both* `BrandSettingNumber` and
`build_settings_block` — replacing the key-substring heuristic currently
inlined in the entity, so a future descriptor cannot get two different
ranges. `language` never appears (all Nivona families exclude generic id 15).
NICR 758 omits `profile`; 79x omits `off_rinse` — all via rule §9.1.2.5, no
new mechanism.

#### 9.1.4 i18n — `settings` domain

New `ui_strings/` keyspace (flat, §6.3.1 format; `settings` added to
`_I18N_DOMAINS`):

* `settings.<setting>.label` — display name.
* `settings.<setting>.description` — optional (same optionality precedent as
  `actions.<token>.description`).
* `settings.<setting>.levels.<token>` — setting-scoped level/option token
  labels (for tokens whose translation is setting-dependent).
* `settings._levels.<token>` — **shared** token labels (0.93: `off`, `on`),
  the fallback tier for tokens whose translation is setting-independent.
  Authored **once** ×29, this tier serves Melitta `filter` and every
  `_OFF_ON_OPTIONS`/`_TEMP_ON_OFF`-backed Nivona select (§9.1.1) with two
  keys total.
* `settings._groups.<group>` — 4 group headers.

**Resolution chain (normative, extends §6.3.5.1):**
`settings.<setting>.levels.<token>` → `settings._levels.<token>` → client
bundle → humanized token / raw value.

Seeding sources (reuse-first, per the §6.3.4 discipline):

| keyspace | tokens | 29-language source today | 0.93 action |
| --- | --- | --- | --- |
| `settings.<setting>.label` | ~30 (8 Melitta + ~22 Nivona-key union) | integration dormant `entity.{switch,number,select}.<key>.name` blocks in `translations/*.json` — complete ×29 (the *dormant translated* blocks; distinct from the anchored name strings frozen by §9.1.2.1) | **port** under token keys |
| `settings.<setting>.description` | 6 (`energy_saving`, `auto_bean_select`, `rinsing_disabled`, `water_hardness`, `auto_off_after`, `brew_temperature`) | PWA `src/locales` en/de/ru (switch descriptions + the number-setting `*_desc` keys the PWA already renders) | port 6 × 3 locales; sparse-others (allowed by §6.3.3 — en is complete) |
| `settings.water_hardness.levels.*` (4), `settings.brew_temperature.levels.*` (3) | 7 | card `localize/languages/*.json` `settings.levels.*` — ×29, numeric-keyed | **port + re-key** numeric→token (`1`→`soft`, `0`→`low`, …); Nivona `water_hardness` options reuse the same 4 keys free |
| `settings._levels.off` / `settings._levels.on` | 2 | none | **newly author** (trivial) + translate 29-way — covers filter + all ~10 off/on Nivona selects via the shared tier |
| `settings._groups.*` | 4 | none | **newly author** + translate 29-way |

The Zone I-L en-completeness test requires every emittable setting token,
group, and level/option token to be **resolvable via the chain** (per-setting
key *or* `_levels` key) — not per-setting keys for shared tokens.

Nivona option labels without tokens are served via `label` only; no key is
minted for a token that doesn't exist (§6.3.4 rule: nothing served without a
home). Tokenizing more tables later brings its keys with it, additively.

#### 9.1.5 Example payloads (pinned verbatim in tests)

Melitta Barista TS (extends §3.7; abbreviated to four representative entries —
the test pins all eight):

```json
"settings": [
  { "setting": "auto_bean_select", "control": "switch", "group": "brew",
    "icon": "mdi:grain",
    "entity": { "domain": "switch", "entity_suffix": "auto_bean_select" },
    "writable": true },
  { "setting": "brew_temperature", "control": "number", "group": "brew",
    "icon": "mdi:thermometer",
    "entity": { "domain": "number", "entity_suffix": "brew_temperature" },
    "writable": true, "min": 0, "max": 2, "step": 1, "display": "slider",
    "levels": [ { "value": 0, "token": "low" },
                { "value": 1, "token": "normal" },
                { "value": 2, "token": "high" } ] },
  { "setting": "water_hardness", "control": "number", "group": "water",
    "icon": "mdi:water-opacity",
    "entity": { "domain": "number", "entity_suffix": "water_hardness" },
    "writable": true, "min": 1, "max": 4, "step": 1, "display": "slider",
    "levels": [ { "value": 1, "token": "soft" },
                { "value": 2, "token": "medium" },
                { "value": 3, "token": "hard" },
                { "value": 4, "token": "very_hard" } ] },
  { "setting": "auto_off_after", "control": "number", "group": "power",
    "icon": "mdi:timer-off-outline",
    "entity": { "domain": "number", "entity_suffix": "auto_off_after" },
    "writable": true, "min": 15, "max": 240, "step": 15,
    "unit": "min", "display": "box" }
]
```

Nivona 700 (NICR 769, extends §3.8; `auto_off` options elided here, pinned in
full in the test):

```json
"settings": [
  { "setting": "temperature", "control": "select", "group": "brew",
    "icon": "mdi:tune",
    "entity": { "domain": "select", "entity_suffix": "temperature" },
    "writable": true,
    "options": [ { "value": 0, "token": null, "label": "normal" },
                 { "value": 1, "token": null, "label": "high" },
                 { "value": 2, "token": null, "label": "max" },
                 { "value": 3, "token": null, "label": "individual" } ] },
  { "setting": "profile", "control": "select", "group": "brew",
    "icon": "mdi:tune",
    "entity": { "domain": "select", "entity_suffix": "profile" },
    "writable": true,
    "options": [ { "value": 0, "token": null, "label": "dynamic" },
                 { "value": 1, "token": null, "label": "constant" },
                 { "value": 2, "token": null, "label": "intense" },
                 { "value": 3, "token": null, "label": "individual" } ] },
  { "setting": "water_hardness", "control": "select", "group": "water",
    "icon": "mdi:tune",
    "entity": { "domain": "select", "entity_suffix": "water_hardness" },
    "writable": true,
    "options": [ { "value": 0, "token": "soft",      "label": "soft" },
                 { "value": 1, "token": "medium",    "label": "medium" },
                 { "value": 2, "token": "hard",      "label": "hard" },
                 { "value": 3, "token": "very_hard", "label": "very hard" } ] },
  { "setting": "off_rinse", "control": "select", "group": "water",
    "icon": "mdi:tune",
    "entity": { "domain": "select", "entity_suffix": "off_rinse" },
    "writable": true,
    "options": [ { "value": 0, "token": "off", "label": "off" },
                 { "value": 1, "token": "on",  "label": "on" } ] },
  { "setting": "auto_off", "control": "select", "group": "power",
    "icon": "mdi:tune",
    "entity": { "domain": "select", "entity_suffix": "auto_off" },
    "writable": true,
    "options": [ { "value": 0, "token": null, "label": "10 min" },
                 { "value": 9, "token": null, "label": "off" } ] }
]
```

#### 9.1.6 Client consumption rules

1. Three-tier fallback per §5.3.6: `contract.settings` → hardcoded
   suffix/meta tables + entity existence → hidden section.
2. **Entity absence gates rendering (normative).** An entry whose bound
   entity has no state object in `hass.states` (user-disabled in the entity
   registry, renamed entity_id, or the registration lag of §9.1.2.5) MUST NOT
   be rendered as a writable control: hide it, or render it as an explicit
   "unavailable" read-only row. Contract presence never overrides entity
   absence; the entity-existence check clients perform today stays a
   **required** part of tier 1.
3. Labels/levels resolve per the §9.1.4 chain (server per-setting key →
   server `_levels` key → client bundle → humanized token / raw value).
   Descriptions resolve server key → client bundle (via the client's own
   legacy-key map, e.g. the PWA's `*_desc` keys) → omit.
4. Writes go through the bound entity exactly as today
   (`switch.turn_on/off`, `number.set_value`, `select.select_option` with the
   served `label` string — the served label always matches the entity's
   current options by construction, §9.1.1). The server re-validates
   regardless (§5.2.5).
5. `min`/`max`/`step` from the contract SHOULD still be cross-checked against
   the live entity attributes when the entity is available; the entity is
   authoritative for the current instant, the contract for rendering before
   the entity loads.
6. `writable: false` → read-only display, never a disabled write control that
   suggests a temporary state.

### 9.2 Sommelier vocabulary

#### 9.2.1 Delivery decision (normative): a machine-independent WS command, NOT a contract block

The sommelier enums are served by a new WS command, not inside the contract
document. Rationale:

* **Module boundary.** `ui_contract.py` is pure and BLE-free and MUST NOT
  import sommelier modules; the vocabulary's single source of truth is
  `sommelier_api.py` (`VALID_*`) + `ai_recipes.py` (`CUP_SIZE_VOLUMES`).
  Embedding it in the contract would cross that boundary or force a hand
  copy — forbidden by the never-hand-copy rule (ui_contract.py:12-14).
* **Scope.** The vocabulary is installation-scoped, identical for every
  entry; the contract document is machine-scoped and requires a ready client
  (`contract_not_ready` pre-handshake). Sommelier UIs (bean library, profile
  editor) legitimately run with no machine connected — the PWA's bean screens
  must not block on a handshake.
* **Fingerprint hygiene.** Vocab changes only with the integration version.
  Keeping it out of the document keeps the §5.1 input set unchanged; caching
  rides `strings_version` (fingerprint-transitive), so the §6.3.2 refetch
  machinery delivers updates with zero new triggers.
* **Precedent.** `i18n/get` (§6.3.1) established the machine-independent,
  `admin=False`, `_send_versioned` WS lane; this is its second occupant —
  and it is **named into that lane**, not into the sommelier namespace
  (below).

#### 9.2.2 Endpoint

* **Name:** `melitta_barista/vocab/get`. Deliberately **outside** the
  `melitta_barista/sommelier/*` namespace: every `sommelier/*` command is
  `require_admin`, and parking the one non-admin command inside that
  namespace is an audit trap (a future security pass "fixing" it would break
  non-admin sessions). `vocab/get` sits beside `i18n/get` in the
  machine-independent constant-data lane, where `admin=False` is the lane
  norm. Nothing shipped calls any other name.
* **Schema:** `{"type": ...}` — no arguments; not entry-scoped; no locale
  (labels come from `i18n/get` domain `sommelier`).
* **Auth:** `admin=False` (constant, non-sensitive data — same class as
  `i18n/get`; a code comment and a `docs/SOMMELIER_API.md` note record this
  deliberately). Registered inside `async_register_panel_websocket`
  (auto-listed by `api/info`). Sync `@callback` via `_wrap_sync_with_schema`
  (pure constant data).
* **`strings_version` source (normative):** resolved **once in
  `async_setup_entry`** and stashed as
  `hass.data[DOMAIN]["ui_strings_version"]`, next to the existing
  `client.integration_version` resolution and **before** WS registration.
  Both `_ws_i18n_get` and the vocab handler read the stash; the lazy
  `async_get_integration` path inside `i18n/get` is removed (§5.1
  single-source precedent). This is what makes a sync handler possible and
  guarantees no client can ever cache vocab under an `unknown` version — the
  failure mode that would otherwise arm a permanent refetch loop when
  `vocab/get` is the session's first call (the likely order for a sommelier
  screen with no machine connected).
* **Caching:** cache axis `strings_version`; refetch trigger is the
  `contract_fingerprint` change (or session start), exactly the §6.3.2 rule.
  If a refetch returns the cached `strings_version`, the cached vocab stands.

```jsonc
// response (via _send_versioned)
{ "schema_version": 1,
  "strings_version": "0.93.0",
  "vocab": {
    "roast":       { "tokens": ["light", "medium", "medium_dark", "dark"] },
    "bean_type":   { "tokens": ["arabica", "arabica_robusta", "robusta"] },
    "origin":      { "tokens": ["single_origin", "blend"] },
    "mood":        { "tokens": ["energizing", "relaxing", "dessert", "classic"],
                     "multi": true },
    "occasion":    { "tokens": ["morning", "after_lunch", "guests", "romantic", "work"] },
    "cup_size":    { "tokens": ["espresso_cup", "cup", "mug", "tall_glass", "travel"],
                     "volumes_ml": { "espresso_cup": [60, 90], "cup": [150, 200],
                                     "mug": [250, 350], "tall_glass": [300, 400],
                                     "travel": [350, 500] } },
    "temperature": { "tokens": ["auto", "hot", "iced"] },
    "caffeine":    { "tokens": ["regular", "low", "decaf_evening"] },
    "dietary":     { "tokens": ["no_sugar", "lactose_free", "low_calorie", "vegan"],
                     "multi": true },
    "mode":        { "tokens": ["surprise_me", "custom"] },
    "extras_kind": { "tokens": ["syrup", "topping", "liqueur"] }
  } }
```

Each family is an open object (additive room for metadata like `volumes_ml`);
unknown families and unknown metadata keys are ignored by clients.

#### 9.2.3 Families served, with the authoritative server-side source of each

Only enumerations the server actually **enforces** are served. The WS builder
reads the `sommelier_api.py` constants directly. **Served token order always
comes from the ordered `sommelier_api.py` lists** (the wire lists are
ordered); the `ai_recipes.py` duplicates are Python *sets*, so the pytest
pinning them against the served families asserts **set-equality** (element
membership, not order) — order is solely the list side's property.

| family | authoritative source | enforcement point |
| --- | --- | --- |
| `roast` | `VALID_ROASTS` (sommelier_api.py) | `vol.In` in `BEAN_SCHEMA` + `beans/update`; pydantic `Literal` in autofill |
| `bean_type` | `VALID_BEAN_TYPES` | same |
| `origin` | `VALID_ORIGINS` | same |
| `mood` | `VALID_MOODS` (= ai_recipes copy) | `vol.In` in `generate` (single + multi); prompt re-filter |
| `occasion` | `VALID_OCCASIONS` | `vol.In` in `generate` |
| `cup_size` | `VALID_CUP_SIZES`; volumes from `CUP_SIZE_VOLUMES` (ai_recipes.py) | `vol.In` in `generate`; LLM output re-validated with `mug` fallback |
| `temperature` | **`VALID_GENERATE_TEMPERATURES = ["auto", "hot", "iced"]` — newly hoisted named constant in sommelier_api.py** (today an inline `vol.In` literal at the generate schema; the only named superset, `VALID_TEMP_PREFS`, is dead code slated for §9.2.4 removal and must not be the source). The hoisted list is used by the generate `vol.In` AND the vocab builder; pinned set-equal to ai_recipes' `VALID_TEMPERATURE_PREFS`. | `vol.In` in `generate` |
| `caffeine` | `VALID_CAFFEINE_PREFS` | `vol.In` in `generate` |
| `dietary` | `VALID_DIETARY` | `vol.In` in `generate`; prompt hint mapping |
| `mode` | `VALID_MODES` | `vol.In` in `generate` |
| `extras_kind` | `_ADDITIVE_SLOTS` (singular slot names; the fixed keys of the pydantic `RecipeExtras` model) | fixed model keys + `_validate_extras` |

Note: `VALID_EXTRAS_CATEGORIES` (plural — `syrups/toppings/liqueurs`) is a
different surface (the `extras/set` storage command) and is **not** served;
the vocabulary serves the singular slot tokens that recipes actually carry.

Machine-dependent sommelier constraints (`supported_aromas`,
`supported_temperatures`, hopper data, `supports_recipe_writes`) remain in
`capabilities/get` — the vocabulary is machine-independent by construction
and MUST stay that way.

#### 9.2.4 Deliberately NOT served (free-form families) — normative

Serving an enum the server does not enforce would advertise a false contract.
The following are free-form **by design** and MUST NOT be served as vocab:

* **Milk types** — the whitelist was deliberately removed so users can store
  localized names ("Ультрапастеризованное 3%"); `milk_config.milk_type` is
  free TEXT. The PWA's `MILK_OPTIONS` list (which already diverges from the
  dead server list) is re-classified as client-local *suggestions*.
* **Flavor notes** — the legacy whitelist was dropped for the dynamic-tag UI;
  `BEAN_SCHEMA` accepts any string list. The 10-token PWA/`VALID_FLAVOR_NOTES`
  set is suggestion chips over a free-form field.
* **Extras item names** (syrup/topping/liqueur values) — user-populated
  catalogue rows (panel `syrups`/`toppings` tables, `user_extras`), validated
  as free strings by design in `_validate_extras`. Only the *kind* slots are
  enumerable (§9.2.3).
* **Profile `temperature_pref`** (`hot_only`/`cold_ok`/`prefer_cold`) — the
  `VALID_TEMP_PREFS` superset is enforced **nowhere** (dead code) and the DB
  column is unconstrained TEXT. Not served in 0.93. Follow-up (post-0.93): either
  add server-side enforcement and then serve it additively, or fold the
  profile preference into the served `temperature` family with a data
  migration — decision deferred, tracked in `docs/BACKLOG.md`.
* **Profile `preferences` dict values** — plain dict, no enum enforcement.

**Dead-code cleanup (same release):** `VALID_MILK_TYPES`,
`VALID_FLAVOR_NOTES`, and `VALID_TEMP_PREFS` in `sommelier_api.py` are
removed (or reduced to a comment) so no future reader mistakes them for
enforced enums. Internal-only change; no wire impact.

#### 9.2.5 i18n — `sommelier` domain

New `ui_strings/` keyspace, family-scoped: `sommelier.<family>.<token>`
(e.g. `sommelier.roast.medium_dark`, `sommelier.cup_size.espresso_cup`,
`sommelier.extras_kind.syrup`). `sommelier` is added to `_I18N_DOMAINS`.

| keyspace | tokens | 29-language source today | 0.93 action |
| --- | --- | --- | --- |
| `sommelier.mood.*` (4), `sommelier.occasion.*` (5), `sommelier.cup_size.*` (5), `sommelier.temperature.*` (3), `sommelier.caffeine.*` (3), `sommelier.dietary.*` (4) | 24 | panel `www/i18n/locales/*.js` `sommelier.{mood,occasion,cup,temp,caffeine,diet}.*` — ×29, complete | **port** (re-keyed to the vocab family names; note `cup`→`cup_size`, `temp`→`temperature`, `diet`→`dietary`) |
| `sommelier.roast.*` (4), `sommelier.bean_type.*` (3), `sommelier.origin.*` (2) | 9 | PWA `src/locales` en/de/ru only (panel renders raw tokens today — a known gap this amendment fixes) | port en/de/ru from the PWA; **newly author ×26** |
| `sommelier.mode.*` (2), `sommelier.extras_kind.*` (3) | 5 | **PWA en/de/ru only** — the panel bundles contain neither `sommelier.mode.*` nor any extras-kind label keys (verified against all 29 `www/i18n/locales/*.js`) | port en/de/ru; **newly author ×26** |

**Honest authoring budget:** 14 tokens (roast 4 + bean_type 3 + origin 2 +
mode 2 + extras_kind 3) × 26 locales ≈ **364 newly authored strings**, plus
the hand-review pass — planned as an explicit line item in Zone I-L, not a
seeding-time discovery.

#### 9.2.6 Client consumption rules

1. Three-tier per family: served `vocab.<family>.tokens` → the client's
   hardcoded option list → hide the picker (never invent tokens).
2. Labels per §6.3.5.1 via `sommelier.<family>.<token>`; the panel's current
   raw-token rendering of roast/bean_type/origin is normatively fixed by this
   (same rule as §6.3.5.3).
3. `volumes_ml` is advisory display data (cup-size hints, wizard cup-step
   synthesis); the server re-validates `cup_type` regardless.
4. **Cup-size token normalization (server-side, normative).** The PWA's
   legacy `"espresso"` token is wrong — the server token is `espresso_cup` —
   and the bad token is **not** client-local state: PWA ≤1.8.3 persists it
   *server-side* via `profiles/add|update` into the unconstrained
   `profiles.cup_size` / `preferences.default_cup_size` TEXT columns, from
   which generate-time reads then hit the hard `vol.In(VALID_CUP_SIZES)`
   rejection (and silently miss the `CUP_SIZE_VOLUMES` prompt lookup). 0.93
   therefore ships, in Zone I-K:
   * a one-time `sommelier_db` migration rewriting the legacy token
     `espresso`→`espresso_cup` in both columns, and
   * write-path normalization in `profiles/add|update` through the same
     alias map, so a still-running 1.8.3 client re-saving `espresso` is
     corrected on ingest.

   Clients then simply adopt the served token list; client-side migration
   applies only to genuinely local state (e.g. localStorage form drafts).
5. Free-form fields (milk, flavor notes, extras items) keep client-local
   suggestion lists, clearly marked non-authoritative; user input is never
   restricted to them.
6. **Admin asymmetry (normative note).** `vocab/get` is `admin=False`, but
   `sommelier/generate` and `sommelier/brew_phase` remain `require_admin` in
   0.93 (their gating is unchanged by this amendment — relaxing it is a
   security-posture decision out of scope for an additive amendment; tracked
   in `docs/BACKLOG.md`). A non-admin session can therefore
   render vocab-driven pickers and then fail at generate/brew time; clients
   MUST map the WS `unauthorized` error to a localized "sommelier generation
   requires a Home Assistant admin user" hint (the PWA does so in Zone P-H,
   alongside the `no_llm_agent*` codes).

### 9.3 DirectKey / profile model

#### 9.3.1 Where the physical-button truth lives (normative)

The fact "Barista TS Smart has no physical Milk button" currently exists only
as four comments across two PWA files. It becomes server data:

* New table in `const.py`, following the TS-gating table precedent:

  ```python
  DIRECTKEY_NO_BUTTON_CATEGORIES: dict[MachineType, frozenset[DirectKeyCategory]] = {
      MachineType.BARISTA_TS: frozenset({DirectKeyCategory.MILK}),
  }
  ```

* `machine_type is None` (pre-refinement) follows the **TS row** — consistent
  with the existing assume-TS precedents (`PROFILE_COUNTS` → 8,
  `get_available_recipes` → full TS list). Confirmed `BARISTA_T` → no
  exclusions. Refinement semantics per §9.1.2.5 (contract flips via the
  fingerprint; stale entities converge on reload).
* **Semantics:** `machine_button: false` means the machine's own front panel
  has no dedicated key for this category. It does **not** remove the category:
  the recipe slot exists, `brew_directkey`/`save_directkey` keep accepting all
  7 tokens, and the server keeps serving all 7 category entries. Clients MAY
  hide or de-emphasize `machine_button: false` categories (the PWA's current
  hard omission of `milk` is re-classified as exactly this flag).

No `MachineCapabilities` field is added — the concept is Melitta-specific
(DirectKey is HC-gated) and `coffee_platform` stays brand-generic.

#### 9.3.2 Shape

Additive top-level contract field `directkey`, present **iff** `"HC" in
brand.supported_extensions` (Melitta only; absent = feature absent, §9.0.1):

```ts
directkey?: {
  categories: DirectKeyCategoryEntry[];   // always all 7, in DirectKeyCategory
                                          // enum order (normative render order)
  profiles: ProfileSlotEntry[];           // length == capabilities.my_coffee_slots + 1
  profile_select_entity_suffix: string;   // "profile" — the select entity anchor
  active_profile_attribute: string;       // "active_profile" — attribute on that select
};

interface DirectKeyCategoryEntry {
  category: string;         // token, byte-equal to _DIRECTKEY_CATEGORIES and to
                            // the values.directkey_category.* i18n keys
  id: number;               // wire category 0..6 (slot id = 302 + profile*10 + id)
  machine_button: boolean;  // §9.3.1
  icon: string;             // mdi fallback icon; composition-derived IconSpecs
                            // (recipes/list) take precedence where available
}

interface ProfileSlotEntry {
  slot: number;                   // 0..my_coffee_slots — stable identity (PR #6 rule)
  fixed?: true;                   // slot 0 only: always active, non-renameable,
                                  // recipes not resettable/editable
  name_key?: string;              // slot 0 only: "my_coffee" — localized via the
                                  // existing recipes.category.my_coffee key (reused)
  name_entity_suffix?: string;    // slots >= 1: "profile_<n>_name" (text entity)
  active_entity_suffix?: string;  // slots >= 1: "profile_<n>_active" (switch entity)
}
```

Normative category icon table (`DIRECTKEY_CATEGORY_ICONS` in `const.py`;
absent/malformed → `mdi:cup` default): `espresso` mdi:coffee, `cafe_creme`
mdi:coffee-outline, `cappuccino` mdi:coffee, `latte_macchiato`
mdi:glass-mug-variant, `milk_froth` mdi:cup, `milk` mdi:cup-outline, `water`
mdi:cup-water.

Profile-model semantics encoded (and pinned by tests, matching the shipped
server behaviour): slot 0 is always present and active with no name text
entity and no activity switch (`reset_all_profile_recipes` refuses it); slots
1..N are visible iff their activity switch is `on`, renameable via the text
entity, and editable/resettable; `active_profile` is **client-side selector
state** on the integration's BLE client, consumed by `brew_directkey` and (as
the default target) `save_directkey` — the machine itself is never told about
it.

#### 9.3.3 Example payload (Melitta Barista TS, extends §3.7; pinned verbatim)

```json
"directkey": {
  "categories": [
    { "category": "espresso",        "id": 0, "machine_button": true,  "icon": "mdi:coffee" },
    { "category": "cafe_creme",      "id": 1, "machine_button": true,  "icon": "mdi:coffee-outline" },
    { "category": "cappuccino",      "id": 2, "machine_button": true,  "icon": "mdi:coffee" },
    { "category": "latte_macchiato", "id": 3, "machine_button": true,  "icon": "mdi:glass-mug-variant" },
    { "category": "milk_froth",      "id": 4, "machine_button": true,  "icon": "mdi:cup" },
    { "category": "milk",            "id": 5, "machine_button": false, "icon": "mdi:cup-outline" },
    { "category": "water",           "id": 6, "machine_button": true,  "icon": "mdi:cup-water" }
  ],
  "profiles": [
    { "slot": 0, "fixed": true, "name_key": "my_coffee" },
    { "slot": 1, "name_entity_suffix": "profile_1_name", "active_entity_suffix": "profile_1_active" },
    { "slot": 2, "name_entity_suffix": "profile_2_name", "active_entity_suffix": "profile_2_active" },
    { "slot": 3, "name_entity_suffix": "profile_3_name", "active_entity_suffix": "profile_3_active" },
    { "slot": 4, "name_entity_suffix": "profile_4_name", "active_entity_suffix": "profile_4_active" },
    { "slot": 5, "name_entity_suffix": "profile_5_name", "active_entity_suffix": "profile_5_active" },
    { "slot": 6, "name_entity_suffix": "profile_6_name", "active_entity_suffix": "profile_6_active" },
    { "slot": 7, "name_entity_suffix": "profile_7_name", "active_entity_suffix": "profile_7_active" },
    { "slot": 8, "name_entity_suffix": "profile_8_name", "active_entity_suffix": "profile_8_active" }
  ],
  "profile_select_entity_suffix": "profile",
  "active_profile_attribute": "active_profile"
}
```

(Barista T: `milk.machine_button: true` — no exclusion row — and 5 profile
entries, slots 0–4.)

#### 9.3.4 Relationship to `recipes/list` and the profile-select attribute: split, not duplicate

The contract `directkey` block carries the **model** (categories, buttons,
slots, bindings — fingerprint-cached, changes rarely); `recipes/list` carries
the **data** (current per-slot recipe contents — live, refetched on
`recipe_cache_generation` changes). No recipe contents are duplicated into
the contract, and no category/slot model is duplicated into `recipes/list`
beyond the identity fields below.

* **`recipes/list` additive delta:** every `directkey` recipe row gains
  `"category": "<token>"` (derived server-side from the enum — clients stop
  duplicating the `(id - 302) % 10` math and the Title-case reverse maps).
  The existing `id`, `name`, `type`, `icon`, `components` fields are
  unchanged; `name` labels are frozen (§5.2 rule 8).
* **Profile-select `directkey_recipes` attribute:** frozen legacy surface
  (§5.2 rule 8) — display-name keys, closed set, kept for the shipped card
  and PWA 1.x. New consumption goes through `recipes/list` + the contract
  block. No new keys will ever be added inside it.
* A pytest pins three-way consistency: contract `categories[].category` ==
  `_DIRECTKEY_CATEGORIES` == the `values.directkey_category.*` key set, and
  `len(profiles)` == `capabilities.my_coffee_slots + 1`.

#### 9.3.5 Action catalog delta: `save_directkey` (17th entry)

The `save_directkey` service becomes a catalog entry so the panel's
`DEFAULT_C1`/`DEFAULT_C2` schema-default duplication dies. All params are
introspected from `SAVE_DIRECTKEY_SCHEMA` (byte-equal, pytest-diffed — the
§6.2.2 rule; the table below mirrors **exactly** what the shipped
`_marker_required`/`_marker_default` helpers emit, marker asymmetries
included); the schema has **no blend and no two_cups fields**, so no
`params_ref` is needed:

| action | group | process | invocation | confirm | requires | icon |
| --- | --- | --- | --- | --- | --- | --- |
| `save_directkey` | control | null | service `save_directkey`, **`entity_suffix: "brew"`** (the `_SERVICE_ANCHOR_SUFFIX` convention used by all three existing service entries — required: clients build the `entity_id` anchor from it, and the shipped card's `readInvocation` drops a service entry without one) | yes (overwrites a slot) | `["ready"]` | mdi:content-save |

Params (introspected; required/default flags exact):

* `category` — enum (7 tokens), **required, no default**.
* `profile_id` — int, ranges `[[0,8]]`, **optional, no default** (omitted →
  the active profile).
* `process1` — enum, **required with default** `coffee`. The live schema
  declares `vol.Required("process1", default="coffee")` while every sibling
  component field is `vol.Optional`; introspection therefore emits
  `required: true` **plus** a default for this one field. The asymmetry is
  mirrored, not normalized — the byte-equality rule makes introspection
  authoritative (matching the `BREW_FREESTYLE` Required-with-default
  precedent), and the pinned test fixture encodes it.
* Optional-with-default: `intensity1` (enum, `medium`), `aroma1` (enum,
  `standard`), `portion1_ml` (int, `[[5,250]]`, `40`), `temperature1` (enum,
  `normal`), `shots1` (enum, `one`), `process2` (enum, `none`), `intensity2`
  (enum, `medium`), `aroma2` (enum, `standard`), `portion2_ml` (int,
  `[[0,250]]`, `0`), `temperature2` (enum, `normal`), `shots2` (enum,
  `none`).

`available` iff `"HC" in supported_extensions` (same gate as
`brew_directkey`). Group `control` is deliberate: card 2.7 renders `control`
with bespoke UI, so the new entry is informational there (§6.2.5.2) — zero
2.7 behaviour change. The static `[[0,8]]` `profile_id` range mirrors the
schema (byte-equality rule); the machine's real slot count lives in
`directkey.profiles`, which clients use for slot pickers.

#### 9.3.6 Client consumption rules

1. Three-tier per §5.3.6: `contract.directkey` → hardcoded category arrays +
   display-name reverse maps + string-template entity addressing → feature
   hidden (Nivona/no-HC).
2. Category render order = served order. Labels via the existing
   `values.directkey_category.<token>` keys (×29, already shipped in 0.92 —
   **zero new i18n keys** for this feature). Icons: served recipe `icon`
   (IconSpec) where a recipe row exists, else the category `icon` mdi.
3. `machine_button: false` → hide or visually de-emphasize; never disable
   the BLE brew path because of it.
4. Profile UI: slots and bindings from `profiles` entries (no more
   `profile_${n}_name` string templates); slot 0 editing/renaming/reset UI
   suppressed via `fixed`; slot visibility via the bound activity switch
   state; selection writes the profile select; `active_profile` read from the
   served attribute name.
5. Recipe contents and slot ids come from `recipes/list` rows joined on
   `profile_id` + `category` token (the display-name reverse maps become
   fallback parsing for pre-0.93 servers only).
6. **Entity absence gates rendering (mirror of §9.1.6 rule 2).** A profile
   slot whose bound name/active entity has no state object in `hass.states`
   (user-disabled, renamed, or the slot-count lag of §9.1.2.5 after a
   BARISTA_T refinement) is not rendered as editable — hide it or show it
   read-only; the profile select itself absent → the whole profile UI falls
   back per rule 1. Contract presence never overrides entity absence.

### 9.4 Fingerprint, `strings_version`, and propagation

* Per the §5.1 delta: **no new fingerprint inputs.** `settings` and
  `directkey` changes (machine-type refinement flipping `auto_bean_select` or
  the milk button; a release editing a table) are delivered through existing
  inputs (`machine_type`, `family_key`, `model_name`, `supported_extensions`,
  `my_coffee_slots`, `integration_version`) within the §5.2.6 ~2 s window.
  The entity registry may lag a mid-session refinement until reload
  (§9.1.2.5); the contract is the leading surface and clients hide the
  dropped entries via the entity-absence rules.
* The sommelier vocabulary and the two new i18n domains version on
  `strings_version` (setup-time stash, §9.2.2); an integration upgrade churns
  every fingerprint (§5.1), arming exactly one vocab + i18n refetch per
  client session — the same single-refetch economics as 0.92, with
  `strings_version` as the short-circuit storage key.
* A pytest extends the §5.1 invariant suite: bridge-vs-document fingerprint
  equality is unaffected by v3 (no new call-site inputs exist to diverge).

### 9.5 Compatibility

Governed by the §5.4 0.93 feature-level matrix (see the §5.4 delta above).
Definition-of-done for 0.93.0b1 includes the invisible-additive proof against
the shipped card 2.7.0 and PWA 1.8.3 (top rows of the matrix) on the live HA
before any client ships — including the two **expected** observable deltas:
grown unfiltered `i18n/get` payloads for card 2.7.0 (§5.2 rule 10) and the
cup-size DB normalization for PWA 1.8.3 profiles (§9.2.6.4).

---

## 10. Implementation plan (0.93)

Ownership zones are file-disjoint per the §7/§8 conventions (the PWA zone map
now lists `src/hooks/` explicitly — it was previously invisible to the plan).
The integration ships first as **0.93.0b1**, is verified against card 2.7.0
and PWA 1.8.3 (invisible-additive check), then the client wave lands: PWA
**2.0.0** (the driver — full v1+v2+v3 port), card **2.8.0** (v3 delta), panel
(ships inside 0.93). All integration tests: `.venv/bin/python -m pytest
tests/ --timeout=10`.

### 10.1 Integration side (`melitta-ha-integration`, 0.93.0b1)

**Zone I-J — contract builders and setting tables (`ui_contract.py`,
`const.py`, `switch.py`, `number.py`, `brands/nivona/_options.py`, + I-J
tests only).**

* `const.py`: `MELITTA_SETTING_TABLES` (pure data: setting id, control kind,
  min/max/step, mode, icon, English name, TS-only flag — the content of
  today's `SWITCH_DEFINITIONS`/`SETTING_DEFINITIONS` plus the
  `TS_ONLY_SETTINGS` flag, moved per §5.2 rule 9);
  `DIRECTKEY_NO_BUTTON_CATEGORIES` (§9.3.1); `SETTING_LEVEL_TOKENS`
  (water_hardness/brew_temperature/filter ladders);
  `DIRECTKEY_CATEGORY_ICONS`.
* `switch.py`/`number.py`: rebuilt to consume `MELITTA_SETTING_TABLES` —
  entity ids, names, icons, ranges, and behaviour byte-identical (a snapshot
  test proves it); `number.py`'s inline range heuristic for Nivona
  options-less descriptors replaced by the shared helper.
* `brands/nivona/_options.py`: token annotations for `_HARDNESS_OPTIONS` and
  the shared `_OFF_ON_OPTIONS`/`_TEMP_ON_OFF` (labels untouched); new pure
  helper `nivona_number_range(descriptor)` (§9.1.3) consumed by both
  `BrandSettingNumber` and the builder.
* `ui_contract.py`: `build_settings_block(caps, machine_type, brand)` —
  consumes `MELITTA_SETTING_TABLES` / descriptors / the same gating
  predicates as entity registration (§9.1.2.5), icons read from the shared
  tables (§9.1.2.7); `build_directkey_block(caps, machine_type, brand)`;
  `build_action_catalog` gains the `save_directkey` entry with
  `SAVE_DIRECTKEY_SCHEMA` introspection and the `"brew"` anchor suffix
  (existing lazy-import + `_schema_entry` helpers); `build_ui_contract` gains
  `settings` and `directkey`. Module stays pure/BLE-free (it imports only
  `const.py` and `brands/nivona/_options.py` — no `homeassistant.components`
  import is needed anymore); docstrings per the boy-scout rule; logger
  literal unchanged.
* `tests/test_ui_contract_settings.py`: §9.1.5 Melitta TS + Nivona 700
  payloads pinned verbatim (full, including all `auto_off` options);
  per-family pinned sets (T drops `auto_bean_select`; 79x drops `off_rinse`;
  758 drops `profile`; `language` absent on every Nivona family, present on
  Melitta); **predicate-equality invariant** — builder vs entity-registration
  predicates evaluated across all family × machine-type combinations
  (§9.1.2.5), both now reading the shared tables; **naming invariants** —
  `slugify(name) == token` for every `MELITTA_SETTING_TABLES` row, and
  `slugify(entity.<domain>.<key>.name) == descriptor.key` for every anchored
  Nivona descriptor across **all 29** `translations/*.json` (§9.1.2.1);
  option labels byte-equal to `_options.py` (structural via build-time
  derivation); level values match entity min/max; `nivona_number_range`
  parity between entity and builder; entity-surface snapshot (ids, names,
  icons unchanged by the table move).
* `tests/test_ui_contract_directkey.py`: block absent for Nivona; §9.3.3
  payload pinned verbatim; TS `milk.machine_button == false`, T all-true,
  `machine_type None` follows TS; `len(profiles) == my_coffee_slots + 1`;
  slot-0 `fixed` + `name_key`; category tokens == `_DIRECTKEY_CATEGORIES` ==
  `values.directkey_category.*` key set.
* `tests/test_ui_contract_actions.py` (extend): 17-entry catalog;
  `save_directkey` params diffed against `SAVE_DIRECTKEY_SCHEMA` — including
  the exact required/default flags of §9.3.5 (`category` required-no-default,
  `process1` required-with-default, `profile_id` optional-no-default, rest
  optional-with-default), no blend, no two_cups; invocation
  `entity_suffix == "brew"`; HC gating; confirm true; group `control`.

**Zone I-K — WS surfaces and DB migration (`panel_api.py`,
`sommelier_api.py`, `sommelier_db.py`, `__init__.py` — the
`strings_version` stash only, `docs/SOMMELIER_API.md`, + I-K tests only).**

* `__init__.py`: resolve and stash
  `hass.data[DOMAIN]["ui_strings_version"]` in `async_setup_entry`, next to
  the `client.integration_version` resolution, **before** WS registration
  (§9.2.2).
* `sommelier_api.py`: hoist `VALID_GENERATE_TEMPERATURES` and use it in the
  generate `vol.In` (§9.2.3); `build_sommelier_vocab()` from the ordered
  `VALID_*` lists + `CUP_SIZE_VOLUMES`; dead-code removal
  (`VALID_MILK_TYPES`, `VALID_FLAVOR_NOTES`, `VALID_TEMP_PREFS`, §9.2.4);
  cup-size alias normalization on the `profiles/add|update` write path
  (§9.2.6.4).
* `sommelier_db.py`: one-time migration rewriting `espresso`→`espresso_cup`
  in `profiles.cup_size` and `preferences.default_cup_size` (strict
  migration-runner rules from 0.89 apply).
* `panel_api.py`: register `melitta_barista/vocab/get` (§9.2.2,
  `admin=False` with the intentional-gating code comment, `_send_versioned`,
  `strings_version` from the setup-time stash); `_ws_i18n_get` switches to
  the stash (lazy `async_get_integration` path removed); `_I18N_DOMAINS` +=
  `{"settings", "sommelier"}`; `_ws_recipes_list` directkey rows gain the
  `category` token field (§9.3.4).
* `docs/SOMMELIER_API.md`: document `vocab/get` (name, lane, the deliberate
  `admin=False` rationale) and the admin asymmetry note (§9.2.6.6).
* `tests/test_sommelier_vocab_ws.py`: full payload pinned verbatim
  (order-sensitive, from the sommelier_api lists); `strings_version` ==
  manifest, served correctly when `vocab/get` is the session's **first** WS
  call (no i18n/get before it); non-admin access; no entry_id required;
  sommelier_api-vs-ai_recipes **set-equality** pins (mood/occasion/cup_size/
  temperature/caffeine/dietary + volume map); free-form families provably
  absent; cup-size DB migration + write-path normalization covered.
* Extend `tests/test_panel_i18n_ws.py` (six-domain filtering; explicit
  old-four-domain requests byte-identical; **unfiltered requests include the
  new domains** — the §5.2 rule 10 mechanism pinned) and the recipes/list
  tests (`category` field per row; `name` labels unchanged).

**Zone I-L — string assets (`ui_strings/*.json` × 29, seeding script,
after I-J for token constants).**

* Seed the `settings.*` and `sommelier.*` keys per the §9.1.4/§9.2.5 source
  tables: port from `translations/*.json` dormant entity blocks (~30 labels
  ×29), card `settings.levels.*` bundles (7 level tokens ×29, re-keyed
  numeric→token), panel sommelier locales (24 tokens ×29), PWA locales
  (en/de/ru seeds for roast/bean_type/origin/mode/extras_kind + the 6
  setting descriptions); newly author + translate the flagged gaps —
  `settings._levels.{off,on}` (2 ×29, the shared tier), `settings._groups.*`
  (4 ×29), and the honest sommelier budget of **14 tokens ×26 ≈ 364
  strings** (§9.2.5). Seeding-script extension in `scripts/` (not shipped);
  hand-review of authored strings; `strings.json`/`translations/` untouched
  except that the anchored Nivona name strings are now test-frozen
  (§9.1.2.1 — hassfest regression run in definition-of-done).
* Extend `tests/test_ui_contract_i18n_assets.py`: en completeness for every
  emittable setting token, group, and vocab token, with level/option tokens
  resolvable via the **chain** (per-setting key or `_levels` key, §9.1.4);
  no orphans; sparse-subset validation for the other 28 unchanged.

**Zone I-M — panel consumers (`www/` only; after I-K + I-L).**

* `melitta-recipes.js`: prefer the served `category` token per row (the
  `(id - 302) % 10` math and `DIRECTKEY_OFFSET` copy become fallback for
  nothing — panel is version-matched — so they are deleted); drop
  `DEFAULT_C1`/`DEFAULT_C2` in favour of `save_directkey` catalog param
  defaults.
* `melitta-sommelier.js`, `melitta-beans.js`, `melitta-additives.js`:
  option lists from `vocab/get` with the existing hardcoded arrays as
  fallback; roast/bean_type/origin rendered via `sommelier.*` server strings
  (fixes the raw-token rendering); free-form fields untouched.
* No settings consumer (the panel has no machine-settings tab — §5.4 note).
* Verification via the live-HA manual checklist (§10.4) — no JS harness for
  `www/`.

**Zone I-N — release (single owner, after I-J…I-M).**

* `manifest.json` → `0.93.0b1`; `CHANGELOG.md` (English); merge the
  0.93 amendment into `docs/UI_CONTRACT.md` per its merge instructions (the
  amendment's design-note appendix stays a review record, outside this
  document); tag `v0.93.0b1`, GitHub prerelease.

### 10.2 PWA side (`melitta-barista-app`, 2.0.0 — full v1+v2+v3 port)

**Zone P-0 — CI enablement (`package.json`, `.github/workflows/ci.yml`;
lands FIRST, before P-A/P-B).** `package.json` gains
`"test": "vitest run"`; `ci.yml` gains a `- run: npm test` step between lint
and build; confirm the repo's 7 existing test suites are green. Today those
suites **never run in CI** — this pre-zone exists precisely so that every
subsequent zone's tests actually gate merges (placing it inside the release
zone, as an earlier draft did, would have gated nothing).

**Zone P-A — contract core (`src/lib/contract.ts` NEW, `src/lib/ha.ts`,
`src/lib/icons.ts` NEW).** `ui_contract/get` fetch over the existing
`home-assistant-js-websocket` connection; `validateContract` requiring only
v1 fields (§6.0.1); `SUPPORTED_CONTRACT_VERSIONS = [1]` with the two §5.4
mismatch screens; bridge-attribute watcher on the connection sensor;
per-`entry_id` last-good persistence with stale marking; per-feature
presence-gating helpers. `ha.ts` additionally gains a `selectOption` helper
(`select.select_option` — needed by P-E's Nivona support). `icons.ts`: the
app has **no mdi renderer** (lucide-react only), so served `mdi:*` names
resolve through a small mdi-name → lucide/local-asset map with a generic
icon as final fallback (§5.3.2-style degradation; the served icon field is a
hint, never a hard requirement). Tests: `tests/contract.test.ts` (both
§6.1.4 fixtures + §9.1.5/§9.3.3 fixtures; v1-only document valid; mismatch
directions; persistence revalidation), `tests/icons.test.ts`.

**Zone P-B — server strings (`src/lib/server-strings.ts` NEW,
`src/lib/i18n.ts`).** §6.3.5.6 split: pure registry (`setServerStrings`/
`serverString`/`resetServerStrings`) + fetch half (`i18n/get`, all six
domains, `locale + strings_version` persistence with §6.3.2 revalidation);
en/de/ru bundles stay as the fallback tier. Tests:
`tests/server-strings.test.ts` (preference order, overlay, revalidation
short-circuit).

**Zone P-C — v1 adoption (`StatusBar.tsx`, `StatusOverlay.tsx`,
`ConnectScreen.tsx`, `RecipeGrid.tsx`, `RecipeCard.tsx`, `CoffeeIcon.tsx`,
`RecipeCarousel.tsx`).** Token-mode status from the state-sensor attributes
(replacing `native_value` string matching); capability-gated sections;
recipe catalog + `name_key` labels from the contract; IconSpec rendering
(served spec → existing `CoffeeIcon` drawing as the §5.3.2 fallback).
Tests extend `CoffeeIcon.test.tsx`, `RecipeCarousel.test.tsx`; new
`tests/status-tokens.test.ts`.

**Zone P-D — v2 adoption (`FreestyleSection.tsx`, `FreestyleGlass.tsx`,
`RecipeEditModal.tsx`, `MaintenanceSection.tsx`, `src/lib/actions.ts` NEW).**
Three-tier parameter resolution (§6.1.5 — tier 3 is the PWA's current
consts); action-catalog module porting the card's pure `action-catalog.ts`
semantics (`resolveActionCatalog`, `evalRequires` with fail-open unknowns,
`planActionInvocation` with `entity_id = button.<prefix>_<entity_suffix>`,
destructive⇒confirm, `available:false` hidden); maintenance section becomes
a catalog renderer — the #36 hazard class disappears for the PWA.
`RecipeEditModal.tsx` (the sole `save_directkey` call site, hence owned
here, not in P-G) adopts the `save_directkey` catalog entry's introspected
defaults in place of its hardcoded ones. Tests: `tests/actions.test.ts`,
`tests/parameters.test.ts`.

**Zone P-E — v3 settings (`SettingsSection.tsx`).** Render from
`contract.settings`: switches, level segments/sliders from `levels`, unit
numbers, **select controls via P-A's `selectOption` (Nivona support arrives
free)**; the §9.1.6 entity-absence rule enforced (no state object → hidden/
unavailable row, never a live control); `SWITCHES`/`NUMBERS`/`LEVEL_LABELS`
tables demoted to tier-2 fallback; labels/descriptions via `settings.*`
server strings (incl. the `_levels` shared tier and the legacy `*_desc` key
map) → local bundle → humanized; icons via P-A's `icons.ts`. Tests:
`tests/settings-section.test.tsx`.

**Zone P-F — v3 sommelier vocab (`SommelierGenerate.tsx`,
`SommelierBeanDialog.tsx`, `SommelierProfileDialog.tsx`,
`SommelierBeans.tsx`, `src/hooks/useSommelier.ts`).** Owns the sommelier WS
hook (`useSommelier.ts`), which gains the `vocab/get` fetch + caching; enum
pickers from the served vocab with the current arrays as fallback; **no
client-side cup-size migration** — the server normalizes (§9.2.6.4), the
client simply adopts the served list (localStorage form drafts are the only
locally normalized state); `TEMP_PREFS` picker removed pending the §9.2.4
follow-up; labels via `sommelier.*` server strings; milk/flavor-note/
extras-item lists re-labelled as local suggestions over free-form input.
Tests: `tests/sommelier-vocab.test.ts`.

**Zone P-G — v3 DirectKey/profiles (`BrewSection.tsx`,
`src/lib/entities.ts`).** Categories from `contract.directkey` (order,
labels via `values.directkey_category.*`, icons via `icons.ts`,
`machine_button` de-emphasis — the four source comments die); recipe rows
joined via `recipes/list` `category` tokens; `DIRECTKEY_CATEGORIES`/
`DIRECTKEY_DISPLAY_TO_KEY` demoted to fallback; profile slots, bindings,
slot-0 gating, and the §9.3.6 entity-absence rule from `profiles` entries
(string templates deleted). Tests: `tests/directkey-model.test.ts`.

**Zone P-H — brew-phase wizard + sommelier error codes (`BrewWizard.tsx`
NEW, `src/hooks/useBrewPhase.ts` NEW, `SommelierRecipeCard.tsx`,
`SommelierSection.tsx`).** Port of the panel step-machine
(`melitta-brew-wizard.js` semantics): numbered linear steps from `steps.pre`
+ `machine_phases` (interleaving `user_action_before`) + `steps.post`;
synthesized cup-placement step from `cup_type` +
`vocab.cup_size.volumes_ml`; per-phase brewing and extended-status polling
via the new `useBrewPhase.ts` hook (WS `melitta_barista/sommelier/
brew_phase` + real progress/`is_brewing`) — a separate hook so P-H does not
touch P-F's `useSommelier.ts`, only consumes its exports;
`confirm_prompt`/`awaiting_confirmation` gate integration; localStorage
re-entry with the 2 h TTL. Sommelier generate/brew errors mapped by code —
`no_llm_agent`, `no_llm_agent_selected`, `llm_agent_missing`, timeout, and
**`unauthorized`** (the §9.2.6.6 admin-requirement hint) — to localized
guidance (keys added to the PWA en/de/ru bundles). Tests:
`tests/brew-wizard.test.tsx` (step synthesis, phase sequencing, re-entry
TTL, error-code mapping incl. unauthorized).

**Zone P-I — wiring + release (single owner, after P-A…P-H).** `App.tsx`
wires the contract/i18n/vocab providers; version `2.0.0`; deploy workflow
unchanged. (CI enablement moved to P-0.)

### 10.3 Card side (`melitta-barista-card`, 2.8.0)

**Zone C-J — types + settings section (`src/contract.ts`,
`src/sections/settings.ts`, `src/settings-catalog.ts` NEW).** Additive
`SettingEntry` types (`validateContract` untouched); pure
`resolveSettings(contract)` with the §5.3.6 tiers (`contract.settings` →
`SWITCH_KEYS`/`NUMBER_KEYS`/META + entity existence → hidden) and the
§9.1.6 entity-absence rule inside tier 1; the settings renderer gains
writable level controls for numbers (today read-only) and select rows
(Nivona) when catalog-driven; labels/levels per §6.3.5 + the `_levels`
shared tier. `tests/settings-catalog.test.ts`: tier fallback, level-token
rendering (per-setting and shared-tier), missing-entity handling, unknown
control/group handling, legacy suites unchanged.

**Zone C-K — DirectKey model (`src/directkey.ts`, `src/profile.ts`,
`src/const.ts` annotations only).** Consume `contract.directkey`: category
set/order/icons/`machine_button`; profile slots from `profiles` entries
(preserving the PR #6 stable-slot rule and its `console.warn`) with the
§9.3.6 entity-absence rule. Data path: the card keeps reading the frozen
`directkey_recipes` select attribute — **not** because `recipes/list` is
unavailable to it (it is `admin=False` and the card has the connection), but
because the attribute is **push-updated with entity state** while
`recipes/list` is a poll lane requiring its own
fingerprint/`recipe_cache_generation` refetch plumbing; the contract block
is the model path over that pushed data. Card 2.9+ MAY adopt `recipes/list`
with the panel's refetch trigger if the attribute freeze ever pinches.
`tests/directkey-model.test.ts`.

**Zone C-L — wiring + release (`melitta-barista-card.ts`,
`src/sections/directkey.ts`; after C-J/C-K).** Pass resolved
settings/directkey props down (sections never read `hass`); `milk` rendered
per `machine_button`; version `2.8.0`; dist rebuild; full vitest green
including all legacy suites.

### 10.4 Sequencing

```mermaid
flowchart LR
  IJ[I-J contract builders + tables] --> IL[I-L ui_strings assets]
  IK[I-K WS surfaces + DB migration] --> IM[I-M panel consumers]
  IL --> IM
  IJ --> IN[I-N release 0.93.0b1]
  IK --> IN
  IM --> IN
  IN -. beta verified: card 2.7.0 + PWA 1.8.3 behaviour unchanged .-> PW[PWA wave]
  IN -.-> CW[card wave]
  P0[P-0 CI enablement] --> PA[P-A contract core]
  P0 --> PB[P-B server strings]
  PA --> PC[P-C v1] --> PD[P-D v2]
  PB --> PC
  PD --> PE[P-E settings] & PF[P-F sommelier] & PG[P-G directkey]
  PF --> PH[P-H wizard + errors]
  PE --> PI[P-I wiring + release 2.0.0]
  PG --> PI
  PH --> PI
  CJ[C-J settings] --> CL[C-L wiring 2.8.0]
  CK[C-K directkey] --> CL
```

Live-HA beta checklist before any client ships: card 2.7.0 and PWA 1.8.3
**behaviour** unchanged against 0.93.0b1 (invisible-additive proof), with the
two expected observable deltas confirmed harmless — card 2.7.0's unfiltered
`i18n/get` responses grown by the new `settings.*`/`sommelier.*` keys
(§5.2 rule 10) and, for any PWA-created profile, the cup-size token
normalized in the DB (§9.2.6.4; 1.8.3 profile dialog shows no preselected
cup size until re-save — cosmetic); `settings` block matches the entity set
predicted by the shared tables on the live TS machine; machine-type
refinement observed flipping `auto_bean_select` + `milk.machine_button` in
the **contract** within one poll cycle via the fingerprint, while the stale
entities persist until reload and a v3 client hides them (the §9.1.2.5
convergence semantics, verified end to end); `vocab/get` served with the
correct `strings_version` as the session's first call and cached across
exactly one refetch per upgrade; panel beans tab shows localized roast
labels in a non-English locale; `recipes/list` rows carry `category`
tokens; Nivona entry (when available) serves select descriptors with
tokenized hardness (per-setting + `_levels` resolution both exercised) and
no `directkey` block.
---

## Appendix A — Design notes (review resolutions)

All blocker and major findings from the 2026-09-02 adversarial review are
incorporated above. Minor findings were all accepted; two were choice points
resolved as follows:

* **Steam parity (minor/client-impl):** of the two offered options, v1 keeps
  parity with the current card — `steam` requires a non-cold **coffee**
  component (§4.7) — rather than adopting "hot milk/water steams". Rationale:
  zero visual regressions in the token/legacy A-B tests; physical honesty can
  ship later as an additive `spec_version` change.
* **Fill-level mechanism (major/client-impl):** implemented as an explicit
  server-computed `fill_level` field *plus* normative nominal volumes (both
  offered options), so clients need no volume table for known glasses but can
  still derive a sane fill for future glass tokens.
* **`info_message_tokens` alias (minor/compat):** dropped before shipping;
  the existing `info_messages` key is promoted to a frozen token list (§3.4).
* **Recorder duplication of `entry_id`/`contract_version` (minor/compat):**
  resolved for free by relocating the bridge block to the rarely-changing
  connection sensor.

No review findings were rejected.

### A.1 Amendment log

Versioned amendments landed during implementation (contract semantics
unchanged unless noted; all are corrections of the document to the normative
anchors it already declared):

1. **Intensity level-2 token is `medium`, not `normal`.** §3.1 anchors value
   tokens byte-for-byte to the const-map keys, and `INTENSITY_MAP`'s key for
   wire value 2 is `"medium"` — the only spelling all server write paths
   (`_resolve_enum`) accept. The draft's `normal` in §3.2, §3.3, the §3.7/§3.8
   example payloads, §4.3, §4.8, §4.10/§4.11 and the §6.1 sketch contradicted
   that anchor and is replaced throughout. (`normal` remains the temperature
   level-1 token — unchanged.) The server-side icon builder defensively
   accepts `normal` as an input alias for intensity index 2, but never emits
   it.
2. **§2.3.5 transient class** gains "a malformed payload whose
   `contract_version` IS supported" (structural validation failure — assumed
   server-side transient, e.g. a partially built document). Client-side
   classification clarification only.
3. **§5.2.6** relaxed from "in the same state write" to "no later than the
   next connection-sensor state write (within one status-poll cycle, ~2 s)".
   Fingerprint inputs (recipe-preload completion, machine-type refinement)
   change outside entity-write paths; the ~2 s status poll is the propagation
   channel and is well within the §2.3.4 refetch-trigger needs.
4. **WS `recipes/list` icon surface confirmed** (§2.1): every recipe entry
   (`base_recipes` and DirectKey rows) carries `icon: IconSpec | null` — the
   §7.1 zone plan omitted this surface by mistake; the delivery table wins.
5. **§3.10 `brand_theme` added** (additive within `contract_version: 1`, no
   version bump): brand badge data block in the §3.3 shape (after `machine`),
   per-brand normative colors, user-supplied-file-only `logo_url`, a
   brand-logo presence flag as a new `contract_fingerprint` input (§5.1), and
   the §7.4 panel-as-consumer note. Absent block ⇒ neutral rendering; no
   client-breaking change.

6. **§6 replaced by the normative v2 feature set (0.92)**, shipping additively
   within `contract_version: 1` per the §6.0 decision (brand_theme precedent,
   amendment 5). Notable deltas from the v1 sketches: `parameters` mirrors and
   **closes** the v1 `vocabularies.freestyle`/`limits.portion_ml` blocks
   (mirror-and-freeze, §6.1.2) instead of superseding them, with a normative
   three-tier client fallback; the action-catalog shape gains
   `invocation`/`requires`/`destructive`/`icon`, makes the service-kind
   `entity_suffix` anchor and schema-byte-equal params normative, encodes the
   switch_off connected-only precedent as data, and gates maintenance
   availability on a new per-family `verified_maintenance_processes`
   verification field (Nivona ships unavailable-by-default until the #36
   matrix lands); `forbidden_combinations` lands with a defined shape and
   empty content; i18n keys recipes by an authored additive `Recipe.name_key`
   (Nivona id collisions; runtime name-derivation forbidden) and is cached by
   a dedicated `strings_version` axis carried in both the i18n response and
   the contract document; the integration version string joins the fingerprint
   inputs under a single-source rule; all served strings live in a new
   hassfest-invisible `ui_strings/` asset set served by a dedicated loader
   (the `translations/` category approach was rejected — CI), seeded from the
   existing integration/card/panel 29-locale assets per the §6.3.4 table, with
   asymmetric completeness (en complete, other locales may be sparse over the
   en overlay).

7. **(0.93)** Third additive feature wave within `contract_version: 1`
   (§9.0, extending the §6.0 precedent): `settings` descriptors with
   semantic level/option tokens (values-as-wire-mapping — resolving the
   1-based/0-based hardness divergence as data; setting-scoped keys with a
   shared `settings._levels.*` tier for setting-independent tokens; setting
   tables moved to `const.py` as the single source for entities and builder;
   predicate-equality and anchored-entity-naming invariants pinned by test),
   machine-independent `melitta_barista/vocab/get` (enforced enums only;
   `VALID_GENERATE_TEMPERATURES` hoisted; free-form families normatively
   excluded; `VALID_MILK_TYPES`/`VALID_FLAVOR_NOTES`/`VALID_TEMP_PREFS` dead
   code removed; `strings_version` stashed at setup for both i18n and
   vocab), and the `directkey` model block
   (`DIRECTKEY_NO_BUTTON_CATEGORIES` gives the "no milk button on Barista
   TS" fact a server home; profile slot model as data; `save_directkey`
   catalog entry with the `"brew"` anchor and exact introspected
   required/default flags; `category` token added to `recipes/list` rows;
   display-name-keyed legacy surfaces mirror-and-frozen as §5.2 rule 8).
   Entity absence normatively gates client rendering (§9.1.6.2/§9.3.6.6).
   Zero new fingerprint inputs; two new i18n domains on the
   `strings_version` axis; the legacy PWA cup-size token `espresso`
   normalized to `espresso_cup` **server-side** (DB migration + write-path
   aliasing).

8. **(v3.1, 0.94) Machine-domain strings wave** (§6.3.7) — additive within
   `contract_version: 1`, no new mechanism and no new fingerprint input. Five
   families move from the three client bundles to `ui_strings/`: `wizard.*`
   (29 keys, a new `wizard` i18n domain, PWA key names and placeholders kept
   byte-equal), `status.process.<TOKEN>.description` and
   `status.sub_process.<TOKEN>.description` (flat keyspace beside the existing
   bare labels), `sommelier.error.<code>` (5 LLM pre-flight codes), and
   `sommelier.{milk,syrup,topping,liqueur,note}.<token>` (34 labels for
   well-known suggestion values — **free-form families stay free-form**: not
   served by `vocab/get`, unknown user text renders verbatim, §9.2.4 intact).
   Completeness tightened for this wave only: all 29 locales complete for the
   served keyspace (closing the observed 212 vs 185–191 gap that shipped
   `settings.*.description` to non-English users in English), while the §6.3.3
   sparse allowance stays in force for future additions. Client bundles are
   demoted to the tier-2 offline / pre-0.94 fallback, not deleted.

### A.2 Design notes (0.92 adversarial round)

All blocker and major findings are incorporated above:

* **hassfest blocker (×3 convergent findings):** `translations/` category
  approach replaced by `ui_strings/<locale>.json` + own loader (§6.3.3). The
  three findings proposed three directory names (`contract_strings/`,
  `ui_strings/`, `ui_translations/`); unified on `ui_strings/`.
* **Fingerprint dual-surface divergence** → `client.integration_version`
  single-source rule + equality-invariant test (§5.1 amendment, Zone I-E).
* **i18n cache staleness / unobservable axis (×2):** `strings_version` added to
  the contract document; fingerprint change is the re-fetch trigger,
  `strings_version` the storage key (§6.3.2).
* **#36 maintenance-audit gap** → `verified_maintenance_processes`, Nivona
  unavailable-by-default in b1 (§6.2.6).
* **Service invocation entity anchor + schema-pinned params** → §6.2.1/§6.2.2.
* **Three-tier parameter fallback** (v1-vocabulary tier restored) → §6.1.5,
  §5.4 cell corrected.
* **Action icons + group labels** → `ActionEntry.icon` (mdi identifiers are
  data, not brand assets), `actions._groups.*` keys, humanize fallback
  (§6.2.1/§6.2.3).
* **Card zone graph and file ownership** → C-E lands first; C-G confined to a
  pure `action-catalog.ts` module + thin renderer; card dispatch moved to C-I
  (§8.2/§8.3).
* **Pure-module isolation of label fns** → registry/fetch split (§6.3.5.6).
* **`displayName` family scoping** → `displayNameFor` frozen signature, call
  sites assigned to C-F (§6.3.5.7).
* **Untestable DOM assertions** → C-G tests target the pure module; rendering
  verified on live HA (§8.2).

Minor findings — resolutions:

* §5.1 i18n/fingerprint contradiction: accepted; reworded as
  side-effect-churn-by-design (§5.1 amendment).
* Nivona name_key parity gap: accepted; per-family descriptor iteration in the
  I-F asset test + authored `name_key` field (§6.3.6).
* Sync source for the version input: accepted; folded into the single-source
  rule (§5.1 amendment, Zone I-E).
* 29-way hard parity: accepted; relaxed to en-complete + sparse locales over
  the en overlay (§6.3.3). **Rejected sub-suggestion:** keeping a
  "non-blocking full-parity report" test — CI has no non-blocking channel and
  a permanently-yellow test is noise; sparse locales are legitimate under the
  relaxed rule.
* name_key derivation instability: accepted; explicit authored field, runtime
  derivation forbidden, NFKD seeding rule, pinned sets (§6.3.6).
* i18n refetch trigger sentence: accepted (§6.3.2).
* I-E→I-F cross-zone test direction: accepted; the every-action-token-keyed
  assertion moved to the I-F asset test, which imports builder constants
  read-only, and the §8.3 graph gained the IE→IF edge.
* Brew-group bespoke-UI duplication: accepted; §6.2.5.2 informational-entries
  rule; card catalog-drives cleaning/filter/power/danger only.

No other findings were rejected.
