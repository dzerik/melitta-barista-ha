# UI Contract Specification (v1)

Status: **Design accepted for 0.91**. The v2 feature set — parameter catalogs,
action catalog and i18n-over-WS — is **normative as of 0.92** (§6, shipped
additively within `contract_version: 1`; implementation plan §8; Appendix A.1
amendment 6).

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
