# UI Contract Specification (v1)

Status: **Design accepted for 0.91** (implementation next). Enum catalogs, action catalog
and i18n-over-WS are **specified as v2 sketches only** (0.92, §6).

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
     network/auth errors: the card auto-retries on the next `false→true`
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
* `intensity`: `very_mild`(0) `mild`(1) `normal`(2) `strong`(3) `very_strong`(4)
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
      intensity: string[];        // 5-level: all; 3-level: ["mild","normal","strong"]
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
      "intensity": ["very_mild", "mild", "normal", "strong", "very_strong"],
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
        "c1": { "process": "milk", "intensity": "normal", "aroma": "standard",
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
      "intensity": ["mild", "normal", "strong"],
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
composition; icons come from category defaults, §4.8.)

### 3.9 Sommelier recipes — icon next to the recipe

Sommelier surfaces (`sommelier/generate` results, `saved_recipes` listings,
`favorites`) attach the same `icon: IconSpec` object to each recipe payload,
computed by the same builder from the recipe's `machine_phases` components plus
**additive slots**: each additive becomes an `additive` layer (§4.6) with `label`
set to the additive's display name and `color_hint` when the DB has one
(normalized to `#RRGGBB` or `null`, §3.6). This is purely additive to the
existing sommelier schemas.

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
intensity_idx = index in [very_mild, mild, normal, strong, very_strong]  # 0..4
shot_count    = index in [none, one, two, three]                          # 0..3
extra_shots   = max(shot_count - 1, 0)
darkness      = clamp(0.30 + 0.125 * intensity_idx + 0.10 * extra_shots, 0.30, 1.00)
```

Layer: `{role: "coffee", ml: portion_ml, intensity: round(darkness, 2)}`.
Examples: very_mild/1 shot → 0.30; normal/1 → 0.55; strong/1 → 0.68 (canonical
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
| `coffee` (café crème/lungo class) | coffee 120 ml, normal, one shot |
| `milk_drink` | c1 coffee 40 ml strong/one + c2 milk 140 ml normal |
| `water` | water 200 ml, high |
| `my_coffee` / `""` / unknown | return `None` → client default icon |

### 4.9 Fractions and rounding (determinism)

* `fraction = round(ml / total_ml, 2)` for every layer and foam.
* All `ml` values are integers; `intensity` and `fill_level` rounded to 2 decimals.
* The server does not force fractions to sum to exactly 1.0; the client normalizes
  (§3.6). Tests assert `abs(1.0 - Σfractions) <= 0.02` for every generated spec.

### 4.10 Worked example A — Latte Macchiato (c1 milk 160 normal, c2 coffee 40 strong/one)

1. Both components survive filtering; order: milk bottom, coffee above.
2. Milk splits: foam_ratio 0.20 (not sole component) → foam round5(32)=30 ml,
   body 130 ml.
3. Coffee darkness: 0.30 + 0.125·3 + 0 = 0.675 → 0.68. Not topmost (foam above) →
   no crema.
4. total_ml = 130 + 40 + 30 = 200; milk_first ∧ has_coffee → `tall_glass`;
   fill_level = round(min(1, 200/320), 2) = 0.63.
5. steam: coffee temp `normal` → true.

Result: exactly the Latte Macchiato icon in §3.7.

### 4.11 Worked example B — Cappuccino (§4.8 milk_drink synthetic: c1 coffee 40 strong/one, c2 milk 140 normal)

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
  recipe-cache generation counter)`. It changes on: handshake completion, family
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
6. Whenever any input of the fingerprint changes, the server MUST update the
   `contract_fingerprint` bridge attribute in the same state write that exposes
   the changed content.

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

---

## 6. Deferred to v2 (0.92) — shape sketches only

All three ship as **additive** fields/commands within `contract_version: 1`.

### 6.1 Enum catalogs with ranges

Extends `vocabularies.freestyle` from bare token lists to self-describing
parameter catalogs (the `{token,min,max,step}` unification):

```jsonc
"parameters": {
  "portion_ml":  { "kind": "range", "per_component": true,
                   "c1": { "min": 5, "max": 250, "step": 5 },
                   "c2": { "min": 0, "max": 250, "step": 5 } },
  "intensity":   { "kind": "enum", "tokens": ["mild", "normal", "strong"] },
  "blend":       { "kind": "enum", "tokens": ["hopper_1", "hopper_2"],
                   "applies_to": ["coffee"] },
  "shots":       { "kind": "enum", "tokens": ["none", "one", "two", "three"],
                   "applies_to": ["coffee"] }
}
```

Clamp rules travel as data; enforcement stays server-side (§1.2).

### 6.2 Action catalog

Replaces the card's three hardcoded maintenance arrays and the
suffix-probing convention:

```jsonc
"actions": [
  { "action": "easy_clean",   "process": "EASY_CLEAN",  "group": "cleaning",
    "confirm": true,  "entity_suffix": "easy_clean",  "available": true },
  { "action": "descaling",    "process": "DESCALING",   "group": "cleaning",
    "confirm": true,  "entity_suffix": "descaling",   "available": true },
  { "action": "filter_insert","process": "FILTER_INSERT","group": "filter",
    "confirm": false, "entity_suffix": "filter_insert","available": true },
  { "action": "switch_off",   "process": "SWITCH_OFF",  "group": "power",
    "confirm": true,  "entity_suffix": "switch_off",   "available": true },
  { "action": "factory_reset","process": null,           "group": "danger",
    "confirm": true,  "entity_suffix": "factory_reset","available": false }
]
```

`available` finally gives per-family maintenance truth (today buttons register
unconditionally). Requires the per-family maintenance-support audit first — that
audit is the actual 0.92 work; the shape above is settled.

### 6.3 Machine-domain i18n over WS

```jsonc
// request
{ "type": "melitta_barista/i18n/get", "locale": "de", "domains": ["status", "recipes", "actions"] }
// response (server falls back de-DE → de → en per key)
{ "schema_version": 1, "locale": "de", "resolved_locale": "de",
  "strings": { "status.process.READY": "Bereit",
               "recipes.name.200": "Espresso",
               "actions.easy_clean": "Easy Clean" } }
```

Served from the integration's 29-locale `translations/` assets (single source of
truth); the card keeps only card-chrome strings in its own bundles. Recipe `name`
in the contract then becomes a fallback, keyed by `recipe_id`. Until then, v1
token display uses the card-side key map of §7.2 Zone C-B.

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