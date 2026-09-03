# Melitta Barista — WebSocket API reference

**API version:** 1.0
**Integration version:** 0.66.0
**Last reviewed:** 2026-05-26

This document is the canonical contract for the integration's WebSocket
surface. Bumping `api_version` requires updating this doc.

## Versioning

- `api_version` covers the integration-wide WS surface. Bump **major**
  on a breaking change to any endpoint's input or output shape. Bump
  **minor** on additive changes (new endpoint, new optional field, new
  optional response key).
- Individual endpoints may carry their own `schema_version` inside the
  response (planned for P6b); for now the integration-wide version is
  the only contract.

## Conventions

- `id` at the top level is reserved by the HA WebSocket framework for
  the message id. Endpoints that need to reference an entity id use a
  named parameter (`additive_id`, `favorite_id`, `preset_id`, etc.).
- `require_admin` endpoints reject unauthenticated WS sessions and any
  session whose user lacks admin rights.
- All endpoints return either `connection.send_result(msg["id"], ...)`
  (success) or `connection.send_error(msg["id"], code, message)`
  (failure). Error codes are documented per endpoint where they aren't
  the framework defaults.
- Voluptuous schemas are translated below to plain-English types
  (`str (max 80)`, `int (1..5)`, `list[str]`, `bool`, etc.). When a
  parameter has a `default`, it is shown in the parameter description.
- Many `send_result` calls pass `{}` (success ack with no payload) or
  pass no payload at all (the framework synthesizes `{ "result": null }`).
  Both are documented as "empty success ack".

## Discovery

| Type | Returns |
|---|---|
| `melitta_barista/api/info` | `{api_version, integration_version, schema_db_version, endpoints: [...]}` |

(The `api/info` endpoint is being added in 0.66.0 — see the entry below.
At the time this document was committed, only the contract was reserved.)

---

## Namespaces

The following sections group endpoints by their `melitta_barista/<namespace>/...` prefix.

- [`api/*`](#api) — discovery (reserved for `api/info`, 0.66.0)
- [`entries`](#entries) — list config entries
- [`status`](#status) — runtime status snapshot per config entry
- [`diagnostics*`](#diagnostics) — diagnostic ring buffers
- [`recipes/*`](#recipes) — machine-cached DirectKey recipes
- [`producers/*`](#producers) — coffee producer catalogue (panel)
- [`syrups/*` + `toppings/*`](#additives-syrups--toppings) — additive
  catalogue (panel, factory-generated)
- [`tags/*`](#tags) — flavor tag catalogue
- [`beans/*`](#beans-autofill) — LLM autofill helper (panel)
- [`prompts/*`](#prompts) — user-editable prompt slots
- [`llm/agents`](#llm-agents) — list HA conversation agents
- [`capabilities/*`](#capabilities) — live machine capabilities
- [`ui_contract/*`](#ui_contract) — UI Contract v1 document (renderer-facing)
- [`i18n/*`](#i18n) — machine-domain display strings (UI Contract v2)
- [`vocab/*`](#vocab) — sommelier enum vocabulary (UI Contract v3)
- [`sommelier/beans/*`](#sommelier-beans) — bean catalogue (sommelier)
- [`sommelier/hoppers/*`](#sommelier-hoppers) — hopper-to-bean mapping
- [`sommelier/milk/*`](#sommelier-milk) — available milk types
- [`sommelier/extras/*`](#sommelier-extras) — extras whitelist
- [`sommelier/generate`](#sommelier-generate) — AI recipe generation
- [`sommelier/brew`](#sommelier-brew) — brew a generated recipe
- [`sommelier/favorites/*`](#sommelier-favorites) — favorites CRUD + brew
- [`sommelier/history/*`](#sommelier-history) — generation history
- [`sommelier/recipe/*`](#sommelier-recipe-ratings) — recipe ratings
- [`sommelier/presets/*`](#sommelier-presets) — user-managed preset templates
- [`sommelier/bean_presets/*`](#sommelier-bean-presets) — static bean preset
  catalogue
- [`sommelier/settings/*`](#sommelier-settings) — sommelier-wide settings
- [`sommelier/preferences/*`](#sommelier-preferences) — user preferences
- [`sommelier/profiles/*`](#sommelier-profiles) — user profile templates

---

## `api`

### `melitta_barista/api/info`

| | |
|---|---|
| **Decorators** | none (planned: none — public discovery) |
| **Stability** | reserved (0.66.0 P6a Task 2) |
| **Introduced** | 0.66.0 |

**Inputs**
- none

**Response (planned)**
```json
{
  "api_version": "1.0",
  "integration_version": "0.66.0",
  "schema_db_version": 6,
  "endpoints": ["melitta_barista/status", "..."]
}
```

**Notes**
- Reserved here so callers can negotiate the API version before issuing
  feature calls. The handler itself lands in P6a Task 2 — this entry is
  a forward reference.

---

## `entries`

Defined in `__init__.py` (panel bootstrap handler).

### `melitta_barista/entries`

| | |
|---|---|
| **Decorators** | `@callback` (sync); no admin requirement |
| **Stability** | stable |
| **Introduced** | ≤0.65.0 |

**Inputs**
- none

**Response**
```json
{
  "entries": [
    {
      "entry_id": "abc123",
      "title": "Melitta Barista",
      "address": "F1:01:02:03:04:05",
      "brand": "melitta"
    }
  ]
}
```

**Notes**
- Lists every melitta_barista config entry so the SPA panel can offer a
  picker in multi-machine setups. Read-only, no auth gate.

---

## `status`

Defined in `panel_api.py`; sync handler wrapped by `_wrap_sync_with_schema`.

### `melitta_barista/status`

| | |
|---|---|
| **Decorators** | sync `@callback`; no admin requirement |
| **Stability** | stable |
| **Introduced** | ≤0.65.0 |

**Inputs**
- `entry_id: str` (required)

**Response — when no live client for the entry**
```json
{ "available": false }
```

**Response — when client is live**
```json
{
  "available": true,
  "address": "F1:...",
  "connected": true,
  "firmware": "1.2.3",
  "features": { "name": "FEATURE_X", "raw": 42 },
  "dis": { "manufacturer_name": "Melitta", "...": "..." },
  "machine_type": "BARISTA_TS_SMART",
  "model": "Barista TS Smart",
  "capabilities": {
    "model_name": "...",
    "family_key": "...",
    "my_coffee_slots": 4
  },
  "last_handshake_at": 1716700000.0,
  "active_profile": 1,
  "selected_recipe": 200,
  "total_cups": 1234,
  "cup_counters": { "espresso": 100, "...": 0 },
  "status": {
    "process": "...",
    "sub_process": "...",
    "manipulation": "...",
    "info_messages": 0,
    "progress": 0.5
  }
}
```

**Notes**
- `features`, `dis`, `capabilities`, `model`, `machine_type`, and `status`
  can each be `null` if not yet read from the machine.

---

## `diagnostics`

Defined in `panel_api.py`. All three handlers are admin-only.

### `melitta_barista/diagnostics`

| | |
|---|---|
| **Decorators** | sync `@callback`, `require_admin` |
| **Stability** | stable |
| **Introduced** | ≤0.65.0 |

**Inputs**
- `entry_id: str` (required)

**Response — when entry/client unknown**
```json
{ "available": false }
```

**Response — when client is live**
```json
{
  "available": true,
  "address": "F1:...",
  "brand": "melitta",
  "proxy": "local" | "remote" | "unknown",
  "poll_interval": 30,
  "ble_connect_timeout": 10,
  "frame_timeout": 5,
  "recent_errors": ["...", "..."],
  "recent_frames": ["...", "..."]
}
```

**Notes**
- `proxy` is best-effort: deduced from the BleakDevice details dict
  (`source` containing `":"` ⇒ ESPHome proxy; otherwise local). Hence
  "unknown" when the heuristic can't decide.

### `melitta_barista/diagnostics/clear`

| | |
|---|---|
| **Decorators** | sync `@callback`, `require_admin` |
| **Stability** | stable |
| **Introduced** | ≤0.65.0 |

**Inputs**
- `entry_id: str` (required)

**Response**
```json
{ "cleared": true }
```

**Errors**
- `not_found` — no live client for the given `entry_id`.

**Notes**
- Clears the per-entry ring buffers (`_recent_errors`, `_recent_frames`)
  **and** the domain-wide `_llm_call_buffer` (cross-entry).

### `melitta_barista/diagnostics/llm_calls`

| | |
|---|---|
| **Decorators** | sync `@callback`, `require_admin` |
| **Stability** | stable |
| **Introduced** | ≤0.65.0 |

**Inputs**
- none (domain-wide buffer; no `entry_id`)

**Response**
```json
{
  "llm_calls": [
    {
      "ts": 1716700000.0,
      "slot": "sommelier_intro",
      "agent_id": "conversation.smartchain_openai",
      "prompt": "...",
      "prompt_len": 1234,
      "raw": "...",
      "raw_len": 500,
      "via": "smartchain_structured" | "text_with_validation",
      "validation_errors": []
    }
  ]
}
```

**Notes**
- Ring buffer is capped at 20 entries (`_llm_call_buffer.maxlen=20`).
- Prompt and raw fields are truncated to 8000 chars; full length kept in
  `prompt_len` / `raw_len`.

---

## `recipes`

### `melitta_barista/recipes/list`

| | |
|---|---|
| **Decorators** | sync `@callback`; no admin requirement |
| **Stability** | stable |
| **Introduced** | ≤0.65.0 |

**Inputs**
- `entry_id: str` (required)

**Response**
```json
{
  "base_recipes": [
    {
      "id": 200,
      "name": "Espresso",
      "type": 24,
      "icon": { "spec_version": 1, "...": "..." },
      "components": [
        {
          "process": "coffee",
          "process_code": 1,
          "shots": "one",
          "intensity": "strong",
          "aroma": "standard",
          "temperature": "normal",
          "blend": 1,
          "portion_ml": 40
        },
        null
      ]
    }
  ],
  "directkey": [
    {
      "profile_id": 0,
      "profile_name": "Profile 0",
      "recipes": [
        {
          "id": 256,
          "name": "Espresso",
          "category": "espresso",
          "type": 24,
          "icon": { "spec_version": 1, "...": "..." },
          "components": [
            {
              "process": "coffee",
              "process_code": 1,
              "shots": "one",
              "intensity": "medium",
              "aroma": "standard",
              "temperature": "normal",
              "blend": 1,
              "portion_ml": 40
            },
            null
          ]
        }
      ]
    }
  ]
}
```

**Errors**
- `not_found` — no live client for `entry_id`.

**Notes**
- `base_recipes` (HR/HS, IDs 200-223) is served from the client-side
  base-recipe cache (`client.base_recipes`, UI Contract §7.1 Zone I-A0),
  filled by the post-connect preload; it is `[]` until the cache fills.
- Since 0.91.0 every recipe entry carries `icon`: the composition-derived
  `IconSpec` (`docs/UI_CONTRACT.md` §3.6) or `null` for empty slots —
  WS `recipes/list` is one of the §2.1 icon delivery surfaces.
- Since 0.93.0 every `directkey` recipe row additionally carries
  `category`: the lower_snake DirectKey category token
  (`espresso`, `cafe_creme`, `cappuccino`, `latte_macchiato`,
  `milk_froth`, `milk`, `water`; `""` for an out-of-enum category byte)
  — UI Contract §9.3.4. Clients join on `profile_id` + `category`
  instead of duplicating the `(id - 302) % 10` math or the Title-case
  reverse maps. The Title-case `name` labels are a frozen legacy
  surface (§5.2 rule 8) and will never change spelling.
  `base_recipes` rows are unchanged.
- Each component may be `null` if absent (e.g. single-pour recipe ⇒
  `components[1] == null`).

---

## `producers`

CRUD set for the producer catalogue, panel-side.

### `melitta_barista/producers/list`

| | |
|---|---|
| **Decorators** | `async_response`; no admin requirement |
| **Stability** | stable |
| **Introduced** | ≤0.65.0 |

**Inputs**
- none

**Response**
```json
{
  "producers": [
    { "id": 1, "name": "Lavazza", "country": "Italy", "website": "...", "notes": "..." }
  ]
}
```

### `melitta_barista/producers/add`

| | |
|---|---|
| **Decorators** | `require_admin`, `async_response` |
| **Stability** | stable |
| **Introduced** | ≤0.65.0 |

**Inputs**
- `name: str` (required)
- `country: str` (optional)
- `website: str` (optional)
- `notes: str` (optional)

**Response**
```json
{ "id": 42 }
```

**Errors**
- `db_error` — INSERT failed (e.g. UNIQUE constraint on `name`).

### `melitta_barista/producers/update`

| | |
|---|---|
| **Decorators** | `require_admin`, `async_response` |
| **Stability** | stable |
| **Introduced** | ≤0.65.0 |

**Inputs**
- `producer_id: int` (required) — note the non-`id` name; see [Conventions](#conventions).
- `name: str` (optional)
- `country: str` (optional)
- `website: str` (optional)
- `notes: str` (optional)

**Response**
```json
{ "updated": true }
```

**Errors**
- `no_fields` — no patchable field was supplied.

### `melitta_barista/producers/delete`

| | |
|---|---|
| **Decorators** | `require_admin`, `async_response` |
| **Stability** | stable |
| **Introduced** | ≤0.65.0 |

**Inputs**
- `producer_id: int` (required)

**Response**
```json
{ "deleted": true }
```

**Notes**
- Always returns `{deleted: true}` whether or not the row existed (DELETE
  is idempotent in SQLite).

---

## `additives` (syrups + toppings)

Both `syrups` and `toppings` are independent tables sharing the same
shape. The five endpoints in each family are generated by closures
(`_make_additive_handlers`, `_make_additive_update_handler`,
`_make_additive_set_available_handler`) — each invocation produces a
concrete handler at runtime. The two families are documented together;
substitute `<table>` with `syrups` or `toppings`.

### `melitta_barista/<table>/list`

| | |
|---|---|
| **Decorators** | `async_response`; no admin requirement |
| **Stability** | stable |
| **Introduced** | ≤0.65.0 |

**Inputs**
- none

**Response**
```json
{
  "syrups": [
    { "id": 1, "name": "Vanilla", "brand": "Monin", "notes": "...", "available": true }
  ]
}
```
(The result key matches `<table>` — `"syrups"` or `"toppings"`.)

**Notes**
- `available` defaults to `true` for legacy rows where the column is
  NULL (pre-P4a DBs).

### `melitta_barista/<table>/add`

| | |
|---|---|
| **Decorators** | `require_admin`, `async_response` |
| **Stability** | stable |
| **Introduced** | ≤0.65.0 |

**Inputs**
- `name: str` (required)
- `brand: str` (optional)
- `notes: str` (optional)

**Response**
```json
{ "id": 7 }
```

### `melitta_barista/<table>/update`

| | |
|---|---|
| **Decorators** | `require_admin`, `async_response` |
| **Stability** | stable |
| **Introduced** | ≤0.65.0 |

**Inputs**
- `additive_id: int` (required)
- `name: str` (optional)
- `brand: str` (optional)
- `notes: str` (optional)
- `available: bool` (optional)

**Response**
```json
{ "updated": true }
```

**Errors**
- `no_fields` — no patchable field was supplied.

### `melitta_barista/<table>/delete`

| | |
|---|---|
| **Decorators** | `require_admin`, `async_response` |
| **Stability** | stable |
| **Introduced** | ≤0.65.0 |

**Inputs**
- `additive_id: int` (required)

**Response**
```json
{ "deleted": true }
```

### `melitta_barista/<table>/set_available`

| | |
|---|---|
| **Decorators** | `require_admin`, `async_response` |
| **Stability** | stable |
| **Introduced** | ≤0.65.0 |

**Inputs**
- `additive_id: int` (required)
- `available: bool` (required)

**Response**
```json
{ "updated": true }
```

**Errors**
- `not_found` — additive id does not exist.

**Notes**
- Convenience endpoint to flip the `available` flag without going through
  the full update endpoint. As of P4b the Sommelier prompt reads the
  catalogue directly, so this no longer mirrors into `user_extras`.

**Concrete endpoint expansion**

| Type |
|---|
| `melitta_barista/syrups/list` |
| `melitta_barista/syrups/add` |
| `melitta_barista/syrups/update` |
| `melitta_barista/syrups/delete` |
| `melitta_barista/syrups/set_available` |
| `melitta_barista/toppings/list` |
| `melitta_barista/toppings/add` |
| `melitta_barista/toppings/update` |
| `melitta_barista/toppings/delete` |
| `melitta_barista/toppings/set_available` |

---

## `tags`

Flavor-tag catalogue. The list endpoint unions explicit tags with any
tag string referenced by a coffee bean row.

### `melitta_barista/tags/list`

| | |
|---|---|
| **Decorators** | `async_response`; no admin requirement |
| **Stability** | stable |
| **Introduced** | ≤0.65.0 |

**Inputs**
- none

**Response**
```json
{ "tags": ["chocolate", "citrus", "fruity"] }
```

**Notes**
- Returns the union of explicit `flavor_tags` rows and any tag string
  found inside any `coffee_beans.flavor_notes` array. Sorted, deduped.

### `melitta_barista/tags/add`

| | |
|---|---|
| **Decorators** | `require_admin`, `async_response` |
| **Stability** | stable |
| **Introduced** | ≤0.65.0 |

**Inputs**
- `name: str` (required, must be non-empty after `.strip()`)

**Response**
```json
{ "name": "chocolate" }
```

**Errors**
- `empty` — `name` was empty / whitespace-only.

**Notes**
- INSERT OR IGNORE — idempotent.

### `melitta_barista/tags/delete`

| | |
|---|---|
| **Decorators** | `require_admin`, `async_response` |
| **Stability** | stable |
| **Introduced** | ≤0.65.0 |

**Inputs**
- `name: str` (required)

**Response**
```json
{ "deleted": true }
```

**Notes**
- Removes only the explicit `flavor_tags` row. Beans referencing the tag
  by string keep their reference.

---

## `beans` autofill

The bean catalogue itself lives at `sommelier/beans/*` (see [Sommelier
Beans](#sommelier-beans)). The single `beans/autofill` endpoint is an
LLM helper that fills bean attributes from `brand` + `product`.

### `melitta_barista/beans/autofill`

| | |
|---|---|
| **Decorators** | `require_admin`, `async_response` |
| **Stability** | stable |
| **Introduced** | ≤0.65.0 |

**Inputs**
- `brand: str` (required)
- `product: str` (required)
- `website: str` (optional) — producer page URL hint
- `agent_id: str` (optional) — override the configured conversation
  agent for this single call

**Response**
```json
{
  "raw": "...",
  "parsed": {
    "roast": "medium",
    "bean_type": "arabica",
    "origin": "blend",
    "origin_country": "Italy",
    "flavor_notes": ["chocolate", "nutty"],
    "composition": "...",
    "brewing_recommendation": "..."
  },
  "validation_errors": [],
  "via": "smartchain_structured" | "text_with_validation"
}
```

**Errors**
- `conversation_error` — the LLM call itself failed (HA log has the
  exception).

**Notes**
- `parsed` is the validated dict on success, the unvalidated dict on
  validation failure (so the UI can still preview), or `null` when the
  reply wasn't valid JSON.
- `validation_errors` is a list of `{ "loc": ..., "msg": ... }` entries
  from Pydantic, empty on success.
- `via` reflects which code path produced the result.

---

## `prompts`

CRUD-ish endpoints for user-editable LLM prompt slots. The "save" /
"reset" operations only ever touch the `panel_prompts` table — the
bundled defaults live in `DEFAULT_PROMPTS`.

### `melitta_barista/prompts/list`

| | |
|---|---|
| **Decorators** | `require_admin`, `async_response` |
| **Stability** | stable |
| **Introduced** | ≤0.65.0 |

**Inputs**
- none

**Response**
```json
{
  "prompts": [
    {
      "slot": "beans_autofill",
      "default": "...",
      "template": "...",
      "is_default": true,
      "schema": { "type": "object", "properties": { "...": {} } },
      "placeholders": [ { "name": "brand", "desc": "Producer name (e.g. Lavazza)" } ]
    }
  ]
}
```

**Notes**
- One entry per registered slot in `DEFAULT_PROMPTS` (`beans_autofill`,
  `sommelier_intro`). `schema` is `null` for slots without a Pydantic
  model.

### `melitta_barista/prompts/save`

| | |
|---|---|
| **Decorators** | `require_admin`, `async_response` |
| **Stability** | stable |
| **Introduced** | ≤0.65.0 |

**Inputs**
- `slot: str` (required) — must be one of the known slot ids.
- `template: str` (required)

**Response**
```json
{ "saved": true }
```

**Errors**
- `unknown_slot` — `slot` is not in `DEFAULT_PROMPTS`.

### `melitta_barista/prompts/preview`

| | |
|---|---|
| **Decorators** | `require_admin`, `async_response` |
| **Stability** | stable |
| **Introduced** | ≤0.65.0 |

**Inputs**
- `slot: str` (required)
- `entry_id: str` (optional) — when provided for the `sommelier_intro`
  slot, hydrates `LiveCapabilities` so the preview matches what
  `/sommelier/generate` would produce.

**Response**
```json
{
  "prompt": "...full assembled prompt text...",
  "sample": { "count": 3, "mode": "surprise_me" }
}
```

**Errors**
- `unknown_slot` — `slot` is not in `DEFAULT_PROMPTS`.

**Notes**
- The response shape branches on `slot`:
  - `beans_autofill` uses a hardcoded `brand`/`product`/`website_hint`
    sample.
  - `sommelier_intro` runs the real `_build_prompt` with current DB
    state (hoppers, milk, extras) plus optional capabilities.
  - Other slots fall back to placeholder substitution via
    `PROMPT_PLACEHOLDERS`.
  The wrapper shape (`{prompt, sample}`) is the same across branches.

### `melitta_barista/prompts/reset`

| | |
|---|---|
| **Decorators** | `require_admin`, `async_response` |
| **Stability** | stable |
| **Introduced** | ≤0.65.0 |

**Inputs**
- `slot: str` (required)

**Response**
```json
{ "reset": true }
```

**Notes**
- Drops the user override; falls back to the bundled `DEFAULT_PROMPTS`
  value. Idempotent — succeeds even if no override existed.

---

## `llm` agents

### `melitta_barista/llm/agents`

| | |
|---|---|
| **Decorators** | `async_response`; no admin requirement |
| **Stability** | stable |
| **Introduced** | ≤0.65.0 |

**Inputs**
- none

**Response**
```json
{
  "agents": [
    { "id": "homeassistant", "name": "Home Assistant (default)" },
    { "id": "conversation.smartchain_openai", "name": "SmartChain — OpenAI" }
  ]
}
```

**Notes**
- Always includes the synthetic `homeassistant` legacy default first;
  the remainder is taken from the live `conversation` domain state
  machine (`hass.states.async_all("conversation")`).

---

## `capabilities`

### `melitta_barista/capabilities/get`

| | |
|---|---|
| **Decorators** | `async_response`; no admin requirement |
| **Stability** | stable |
| **Introduced** | ≤0.65.0 |

**Inputs**
- `entry_id: str` (required)

**Response**
```json
{
  "schema_version": 1,
  "entry_id": "abc123",
  "source": "cache" | "derive",
  "probed_at": "2026-05-26T10:00:00+00:00" | null,
  "capabilities": {
    "family_key": "...",
    "model_name": "...",
    "supported_processes": ["coffee", "milk", "water"],
    "supported_intensities": ["mild", "medium", "strong"],
    "supported_aromas": ["standard", "intense"],
    "supported_temperatures": ["normal", "high"],
    "supported_shots": ["one", "two"],
    "portion_limits": { "min": 10, "max": 250 },
    "forbidden_combinations": []
  }
}
```

**Errors**
- `entry_not_found` — config entry id is unknown or has no live client
  and DB cache is empty.
- `client_not_ready` — live derive failed because the runtime client
  isn't initialised yet.

**Notes**
- Response shape is identical between cache hit (`source: "cache"`,
  `probed_at` populated) and live derive (`source: "derive"`,
  `probed_at: null`). The `schema_version` field is hardcoded `1`.
- If the cached row deserializes badly (corrupt JSON, future schema),
  the handler logs and falls through to the live-derive path silently.

---

## `ui_contract`

### `melitta_barista/ui_contract/get`

| | |
|---|---|
| **Decorators** | sync `@callback` via `_wrap_sync_with_schema(..., admin=False)`; no admin requirement |
| **Stability** | stable (contract-versioned, see `docs/UI_CONTRACT.md`) |
| **Introduced** | 0.91.0 |

**Inputs**
- `entry_id: str` (required) — multi-entry installs must address a
  specific config entry.

**Response**

The full UI Contract v1 document (normative shape: `docs/UI_CONTRACT.md`
§3.3), wrapped in the standard `schema_version` envelope. Abbreviated:

```json
{
  "schema_version": 1,
  "contract_version": 1,
  "contract_fingerprint": "9f3ac1d24b07",
  "entry_id": "abc123",
  "generated_at": "2026-09-02T10:15:00Z",
  "source": "live",
  "machine": { "brand": "melitta", "machine_type": "BARISTA_TS", "...": "..." },
  "capabilities": { "supports_freestyle": true, "hopper_count": 2, "...": "..." },
  "vocabularies": { "status": { "...": "..." }, "freestyle": { "...": "..." } },
  "limits": { "portion_ml": { "c1": { "min": 5, "max": 250, "step": 5 },
                              "c2": { "min": 0, "max": 250, "step": 5 } } },
  "recipes": [ { "recipe_id": 200, "name": "Espresso", "category": "espresso",
                 "icon": { "spec_version": 1, "...": "..." } } ],
  "status_attribute_entity": "state",
  "bridge_attribute_entity": "connection"
}
```

**Errors** (all client-retriable, `docs/UI_CONTRACT.md` §2.3 step 5)
- `entry_not_found` — no such config entry.
- `client_not_ready` — entry exists but `runtime_data` has no client.
- `contract_not_ready` — client exists but has no `MachineCapabilities`
  yet (no successful handshake — fresh install with the machine off, HA
  restart before reconnect). The server never invents placeholder
  capability values; clients MUST treat this as transient, never as
  "server has no contract support".

**Notes**
- The WS `schema_version` versions the transport envelope; the contract
  additionally carries its own `contract_version` (compatibility gate)
  and `contract_fingerprint` (per-machine content revision — clients
  cache per `entry_id + contract_fingerprint` and refetch when the
  bridge attribute changes).
- Read-only, pure in-memory build (no DB, no BLE) — same auth class as
  `melitta_barista/status`, `recipes/list` and `api/info`.
- Renderer-facing counterpart of `capabilities/get` (which stays the
  LLM-facing surface); the two version independently.

---

## `i18n`

### `melitta_barista/i18n/get`

| | |
|---|---|
| **Decorators** | `@websocket_api.async_response` (executor file I/O on first use per locale); no admin requirement |
| **Stability** | stable (UI Contract v2, see `docs/UI_CONTRACT.md` §6.3) |
| **Introduced** | 0.92.0 |

**Inputs**
- `locale: str` (required) — the requested display locale, e.g. `"de-DE"`.
- `domains: list[str]` (optional) — subset of `status`, `values`,
  `recipes`, `actions`, `settings`, `sommelier` (the last two added in
  0.93, UI Contract §9.1.4/§9.2.5). Unknown domains are ignored;
  omitted = all — so unfiltered responses grow additively with new
  domains (§5.2 rule 10; clients rely on unknown-key tolerance, not
  filtering).

**Not entry-scoped** — the served strings are machine-independent, so
there is no `entry_id` parameter.

**Response**

```json
{
  "schema_version": 1,
  "locale": "de-DE",
  "resolved_locale": "de",
  "strings_version": "0.92.0",
  "strings": {
    "status.process.READY": "Bereit",
    "status.manipulation.FILL_WATER": "Wassertank füllen",
    "values.intensity.very_mild": "Sehr mild",
    "recipes.name.espresso": "Espresso",
    "actions.easy_clean.label": "Easy Clean",
    "actions._groups.cleaning": "Reinigung"
  }
}
```

- `resolved_locale` — the locale file that won the resolution chain:
  requested locale → exact match → base language (`de-DE` → `de`) →
  `en`. Per-key fallback to English applies on top: the winning locale
  file is overlaid onto `en.json`, so a sparse locale still serves
  every key (missing ones in English).
- `strings_version` — the integration `manifest.json` version, the
  client-side cache axis for server strings (`locale +
  strings_version`, `docs/UI_CONTRACT.md` §6.3.2). It carries no
  semantics beyond equality. Since 0.93 it is resolved once in
  `async_setup_entry` and stashed as
  `hass.data[DOMAIN]["ui_strings_version"]` before WS registration
  (§9.2.2) — this endpoint and `vocab/get` both read the stash; the
  former lazy per-request resolution was removed (§5.1 single-source
  rule).
- `strings` — a flat, dot-joined map, keys byte-equal to the contract
  tokens they describe (`status.*` embeds `UPPER_SNAKE`, `values.*` /
  `recipes.*` / `actions.*` embed `lower_snake`; no client may
  case-fold keys).

**Notes**
- Strings live in `custom_components/melitta_barista/ui_strings/
  <locale>.json` (29 locales); the merged en-overlay map is cached in
  `hass.data` per resolved locale, so each locale is read from disk at
  most once per HA run (the assets are immutable between upgrades).
- i18n failure on the client (e.g. `unknown_command` from a pre-0.92
  server) degrades only display strings — never token semantics,
  catalogs, or status handling.
- Read-only, informational — same auth class as `ui_contract/get`,
  `status`, `recipes/list` and `api/info`.

---

## `vocab`

### `melitta_barista/vocab/get`

| | |
|---|---|
| **Decorators** | sync `@callback` via `_wrap_sync_with_schema(..., admin=False)` |
| **Stability** | stable (UI Contract v3, see `docs/UI_CONTRACT.md` §9.2) |
| **Introduced** | 0.93.0 |

**Inputs**
- none — **not entry-scoped**, no `locale`. The vocabulary is
  installation-scoped and machine-independent by construction; labels
  come from `i18n/get` domain `sommelier`.

**Response**

```json
{
  "schema_version": 1,
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
  }
}
```

**Notes**
- **Lane and auth (deliberate).** The command is named into the
  machine-independent constant-data lane beside `i18n/get` —
  deliberately *outside* the all-`require_admin` `sommelier/*`
  namespace. `admin=False` is intentional and load-bearing: the data
  is constant and non-sensitive (same class as `i18n/get`), and
  sommelier screens (bean library, profile editor) legitimately run in
  non-admin sessions with no machine connected. A future security pass
  must **not** "fix" this endpoint to `require_admin`.
- **Admin asymmetry** (UI Contract §9.2.6.6): `sommelier/generate` and
  `sommelier/brew_phase` remain `require_admin`. A non-admin session
  can therefore render vocab-driven pickers and then fail at
  generate/brew time; clients MUST map the WS `unauthorized` error to
  a localized "sommelier generation requires a Home Assistant admin
  user" hint.
- Serves **only enforced enums** (each family reads its authoritative
  ordered `VALID_*` list in `sommelier_api.py`, plus
  `CUP_SIZE_VOLUMES` from `ai_recipes.py`). Free-form families — milk
  types, flavor notes, extras item names, profile `temperature_pref` —
  are normatively excluded (§9.2.4): serving an unenforced enum would
  advertise a false contract. Client option lists for those fields are
  local suggestions over free-form input.
- Each family is an open object (additive room for metadata like
  `volumes_ml`); clients ignore unknown families and unknown metadata
  keys. Token order is the wire order. `volumes_ml` is advisory
  display data — the server re-validates `cup_type` regardless.
- `strings_version` is the client cache axis (same value and stash as
  `i18n/get`); refetch on `contract_fingerprint` change or session
  start, and keep the cached vocab when a refetch returns the cached
  version (§9.2.2).

---

## Sommelier — beans

### `melitta_barista/sommelier/beans/list`

| | |
|---|---|
| **Decorators** | `async_response`; no admin requirement |
| **Stability** | stable |
| **Introduced** | ≤0.65.0 |

**Inputs**
- none

**Response**
```json
{ "beans": [ { "id": "...", "brand": "...", "product": "...", "...": "..." } ] }
```

### `melitta_barista/sommelier/beans/add`

| | |
|---|---|
| **Decorators** | `require_admin`, `async_response` |
| **Stability** | stable |
| **Introduced** | ≤0.65.0 |

**Inputs**
- `brand: str` (required)
- `product: str` (required)
- `roast: "light" \| "medium" \| "medium_dark" \| "dark"` (required)
- `bean_type: "arabica" \| "arabica_robusta" \| "robusta"` (required)
- `origin: "single_origin" \| "blend"` (required)
- `origin_country: str` (optional)
- `flavor_notes: list[str]` (optional, default `[]`) — free-form tag list.
- `composition: str` (optional)
- `preset_id: str` (optional)

**Response**
```json
{ "bean": { "id": "...", "brand": "...", "...": "..." } }
```

### `melitta_barista/sommelier/beans/update`

| | |
|---|---|
| **Decorators** | `require_admin`, `async_response` |
| **Stability** | stable |
| **Introduced** | ≤0.65.0 |

**Inputs**
- `bean_id: str` (required)
- All add-fields as **optional** (`brand`, `product`, `roast`, `bean_type`,
  `origin`, `origin_country`, `flavor_notes`, `composition`, `preset_id`).

**Response**
```json
{ "bean": { "id": "...", "...": "..." } }
```

**Errors**
- `not_found` — bean id is unknown.

### `melitta_barista/sommelier/beans/delete`

| | |
|---|---|
| **Decorators** | `require_admin`, `async_response` |
| **Stability** | stable |
| **Introduced** | ≤0.65.0 |

**Inputs**
- `bean_id: str` (required)

**Response**
- empty success ack.

**Errors**
- `not_found` — bean id is unknown.

---

## Sommelier — hoppers

### `melitta_barista/sommelier/hoppers/get`

| | |
|---|---|
| **Decorators** | `async_response`; no admin requirement |
| **Stability** | stable |
| **Introduced** | ≤0.65.0 |

**Inputs**
- none

**Response**
```json
{
  "hopper1": { "bean": { "id": "...", "...": "..." } | null },
  "hopper2": { "bean": { "id": "...", "...": "..." } | null }
}
```

**Notes**
- Response body is the bare hoppers dict — there is no wrapper key.

### `melitta_barista/sommelier/hoppers/assign`

| | |
|---|---|
| **Decorators** | `require_admin`, `async_response` |
| **Stability** | stable |
| **Introduced** | ≤0.65.0 |

**Inputs**
- `hopper_id: 1 \| 2` (required)
- `bean_id: str \| null` (optional) — pass `null` (or omit) to clear.

**Response**
- empty success ack.

---

## Sommelier — milk

### `melitta_barista/sommelier/milk/get`

| | |
|---|---|
| **Decorators** | `async_response`; no admin requirement |
| **Stability** | stable |
| **Introduced** | ≤0.65.0 |

**Inputs**
- none

**Response**
```json
{ "milk_types": ["Oat", "Whole", "..."] }
```

### `melitta_barista/sommelier/milk/set`

| | |
|---|---|
| **Decorators** | `require_admin`, `async_response` |
| **Stability** | stable |
| **Introduced** | ≤0.65.0 |

**Inputs**
- `milk_types: list[str]` (required) — free-form list of names; the
  legacy English-only whitelist (`VALID_MILK_TYPES`) was dropped in
  favour of free-text so Cyrillic / brand names work.

**Response**
- empty success ack.

---

## Sommelier — extras

### `melitta_barista/sommelier/extras/get`

| | |
|---|---|
| **Decorators** | `async_response`; no admin requirement |
| **Stability** | stable |
| **Introduced** | ≤0.65.0 |

**Inputs**
- none

**Response**
```json
{ "extras": { "syrups": ["Vanilla"], "toppings": ["Cocoa"], "liqueurs": [] } }
```

### `melitta_barista/sommelier/extras/set`

| | |
|---|---|
| **Decorators** | `require_admin`, `async_response` |
| **Stability** | stable |
| **Introduced** | ≤0.65.0 |

**Inputs**
- `category: "syrups" \| "toppings" \| "liqueurs"` (required)
- `items: list[str]` (required)

**Response**
- empty success ack.

---

## Sommelier — generate

### `melitta_barista/sommelier/generate`

| | |
|---|---|
| **Decorators** | `require_admin`, `async_response` |
| **Stability** | stable |
| **Introduced** | ≤0.65.0 |

**Inputs**
- `mode: "surprise_me" \| "custom"` (optional, default `surprise_me`)
- `preference: str` (optional)
- `count: int (1..5)` (optional, default `3`)
- `mood: "energizing" \| "relaxing" \| "dessert" \| "classic"` (optional) — legacy single-value field.
- `moods: list[<mood>]` (optional) — multi-select; wins over `mood`.
- `occasion: "morning" \| "after_lunch" \| "guests" \| "romantic" \| "work"` (optional)
- `temperature: "auto" \| "hot" \| "iced"` (optional, default `auto`)
- `servings: int (1..4)` (optional, default `1`)
- `dietary: list["no_sugar" \| "lactose_free" \| "low_calorie" \| "vegan"]` (optional)
- `caffeine_pref: "regular" \| "low" \| "decaf_evening"` (optional)
- `cup_size: "espresso_cup" \| "cup" \| "mug" \| "tall_glass" \| "travel"` (optional)
- `allow_syrups: list[str]` (optional) — whitelist override.
- `allow_toppings: list[str]` (optional) — whitelist override.
- `allow_milk: list[str]` (optional) — whitelist override.
- `agent_id: str` (optional) — per-request override of the conversation agent.
- `entry_id: str` (optional) — scope `LiveCapabilities` lookup; defaults to first entry.

**Response**
```json
{
  "session": {
    "id": "...",
    "recipes": [ { "id": "...", "name": "...", "...": "..." } ],
    "mode": "surprise_me",
    "preference": null,
    "...": "..."
  }
}
```

**Errors**
- `generation_failed` — `_structured_call` raised (LLM transport error).
- `no_recipes` — LLM returned an empty or unparsable `recipes` array
  (`validation_errors` are included in the message body).

**Notes**
- The complete `session` shape is built by `db.async_create_session`
  and includes the persisted recipes, mood/occasion/temperature inputs,
  weather/people-home context, profile id, etc.

---

## Sommelier — brew

### `melitta_barista/sommelier/brew`

| | |
|---|---|
| **Decorators** | `require_admin`, `async_response` |
| **Stability** | stable |
| **Introduced** | ≤0.65.0 |

**Inputs**
- `recipe_id: str` (required) — id of a recipe returned by `generate`.

**Response**
- empty success ack.

**Errors**
- `not_found` — recipe id is unknown.
- `no_device` — no live BLE client / config entry.
- `brew_failed` — BLE brew call raised; see HA logs.

**Notes**
- Always calls `async_mark_recipe_brewed` on success.
- Internally invokes `_brew_recipe_components` which handles single-
  and two-phase recipes (a missing phase[1] is encoded as a `none`-
  process component2).

---

## Sommelier — favorites

### `melitta_barista/sommelier/favorites/list`

| | |
|---|---|
| **Decorators** | `async_response`; no admin requirement |
| **Stability** | stable |
| **Introduced** | ≤0.65.0 |

**Inputs**
- none

**Response**
```json
{ "favorites": [ { "id": "...", "name": "...", "...": "..." } ] }
```

### `melitta_barista/sommelier/favorites/add`

| | |
|---|---|
| **Decorators** | `require_admin`, `async_response` |
| **Stability** | stable |
| **Introduced** | ≤0.65.0 |

**Inputs**
- `recipe_id: str` (required) — must be a previously-generated recipe.

**Response**
```json
{ "favorite": { "id": "...", "...": "..." } }
```

**Errors**
- `not_found` — recipe id is unknown.

**Notes**
- Pulls the source recipe and the current hopper bean to populate
  `source_recipe_id` / `source_bean_id` on the new favorite row.

### `melitta_barista/sommelier/favorites/remove`

| | |
|---|---|
| **Decorators** | `require_admin`, `async_response` |
| **Stability** | stable |
| **Introduced** | ≤0.65.0 |

**Inputs**
- `favorite_id: str` (required)

**Response**
- empty success ack.

**Errors**
- `not_found` — favorite id is unknown.

### `melitta_barista/sommelier/favorites/update`

| | |
|---|---|
| **Decorators** | `require_admin`, `async_response` |
| **Stability** | stable |
| **Introduced** | ≤0.65.0 |

**Inputs**
- `favorite_id: str` (required)
- `name: str` (optional)
- `description: str` (optional)
- `note: str \| null` (optional)

**Response**
```json
{}
```

**Errors**
- `no_fields` — none of `name` / `description` / `note` was provided.
- `invalid_update` — DB layer raised `ValueError`.
- `not_found` — favorite id is unknown.

### `melitta_barista/sommelier/favorites/brew`

| | |
|---|---|
| **Decorators** | `require_admin`, `async_response` |
| **Stability** | stable |
| **Introduced** | ≤0.65.0 |

**Inputs**
- `favorite_id: str` (required)

**Response**
- empty success ack.

**Errors**
- `not_found` — favorite id is unknown.
- `no_device` — no live BLE client / config entry.
- `brew_failed` — BLE brew call raised; see HA logs.

**Notes**
- Always increments the favorite's brew counter on success.

---

## Sommelier — history

### `melitta_barista/sommelier/history/list`

| | |
|---|---|
| **Decorators** | `async_response`; no admin requirement |
| **Stability** | stable |
| **Introduced** | ≤0.65.0 |

**Inputs**
- `limit: int (1..100)` (optional, default `20`)
- `offset: int (>=0)` (optional, default `0`)

**Response**
```json
{ "sessions": [ { "id": "...", "created_at": "...", "...": "..." } ] }
```

### `melitta_barista/sommelier/history/clear`

| | |
|---|---|
| **Decorators** | `require_admin`, `async_response` |
| **Stability** | stable |
| **Introduced** | ≤0.65.0 |

**Inputs**
- `keep_favorited: bool` (optional, default `true`)

**Response**
```json
{ "cleared": 42 }
```

**Notes**
- Returns the count of sessions actually removed. With `keep_favorited:
  true` (default), sessions linked to a favorite are preserved.

---

## Sommelier — recipe ratings

### `melitta_barista/sommelier/recipe/rate`

| | |
|---|---|
| **Decorators** | `require_admin`, `async_response` |
| **Stability** | stable |
| **Introduced** | ≤0.65.0 |

**Inputs**
- `target_id: str` (required)
- `target_type: "generated" \| "favorite"` (required)
- `rating: int (1..5)` (required)
- `note: str \| null` (optional)

**Response**
```json
{}
```

**Errors**
- `invalid_rating` — DB layer raised `ValueError` (defensive — voluptuous
  catches range violations first).

**Notes**
- Upserts on `(target_id, target_type)`; a second call replaces the
  prior rating.

### `melitta_barista/sommelier/recipe/unrate`

| | |
|---|---|
| **Decorators** | `require_admin`, `async_response` |
| **Stability** | stable |
| **Introduced** | ≤0.65.0 |

**Inputs**
- `target_id: str` (required)
- `target_type: "generated" \| "favorite"` (required)

**Response**
```json
{}
```

**Notes**
- Idempotent — succeeds even if no rating existed.

---

## Sommelier — presets

User-managed preset templates. Distinct from `sommelier/bean_presets`
(the static catalogue) — these are CRUD-managed structured payloads.

### `melitta_barista/sommelier/presets/list`

| | |
|---|---|
| **Decorators** | `require_admin`, `async_response` |
| **Stability** | stable |
| **Introduced** | ≤0.65.0 |

**Inputs**
- none

**Response**
```json
{ "presets": [ { "id": "...", "name": "...", "description": "...", "payload": {} } ] }
```

### `melitta_barista/sommelier/presets/add`

| | |
|---|---|
| **Decorators** | `require_admin`, `async_response` |
| **Stability** | stable |
| **Introduced** | ≤0.65.0 |

**Inputs**
- `name: str (1..80)` (required)
- `description: str (max 500)` (optional)
- `payload: dict` (required)

**Response**
```json
{ "id": "..." }
```

### `melitta_barista/sommelier/presets/update`

| | |
|---|---|
| **Decorators** | `require_admin`, `async_response` |
| **Stability** | stable |
| **Introduced** | ≤0.65.0 |

**Inputs**
- `preset_id: str` (required)
- `name: str (1..80)` (optional)
- `description: str (max 500)` (optional)
- `payload: dict` (optional)

**Response**
```json
{ "updated": true }
```

**Errors**
- `no_fields` — none of `name` / `description` / `payload` provided.
- `system_preset_readonly` — the preset is a bundled / built-in one
  and cannot be modified.
- `invalid_update` — generic `ValueError` from the DB layer.
- `not_found` — preset id is unknown.

### `melitta_barista/sommelier/presets/delete`

| | |
|---|---|
| **Decorators** | `require_admin`, `async_response` |
| **Stability** | stable |
| **Introduced** | ≤0.65.0 |

**Inputs**
- `preset_id: str` (required)

**Response**
```json
{ "deleted": true }
```

**Errors**
- `system_preset_readonly` — built-in presets cannot be deleted.
- `invalid_delete` — generic `ValueError` from the DB layer.
- `not_found` — preset id is unknown.

---

## Sommelier — bean presets

Static catalogue moved here in 0.64.0. Read-only — bundled JSON file.

### `melitta_barista/sommelier/bean_presets/list`

| | |
|---|---|
| **Decorators** | `async_response`; no admin requirement |
| **Stability** | stable |
| **Introduced** | ≤0.65.0 |

**Inputs**
- none

**Response**
```json
{ "presets": [ { "id": "lavazza_crema_e_aroma", "brand": "Lavazza", "...": "..." } ] }
```

**Errors**
- `load_failed` — bundled `coffee_presets.json` could not be read.

**Notes**
- Cached in module-level state after first successful load; subsequent
  calls hit the cache and never re-read the file.

---

## Sommelier — settings

Sommelier-wide settings stored in the shared `settings` table. The
allowed keys are gated by `VALID_SETTING_KEYS` (currently
`["llm_agent_id"]`) — see source for why this is a hard schema guarantee
rather than a UX gate.

### `melitta_barista/sommelier/settings/get`

| | |
|---|---|
| **Decorators** | `async_response`; no admin requirement |
| **Stability** | stable |
| **Introduced** | ≤0.65.0 |

**Inputs**
- none

**Response**
```json
{ "settings": { "llm_agent_id": "conversation.smartchain_openai" } }
```

### `melitta_barista/sommelier/settings/set`

| | |
|---|---|
| **Decorators** | `require_admin`, `async_response` |
| **Stability** | stable |
| **Introduced** | ≤0.65.0 |

**Inputs**
- `key: "llm_agent_id"` (required) — only key currently allowed.
- `value: str` (required)

**Response**
- empty success ack.

---

## Sommelier — preferences

User preferences stored in `user_preferences`. Allowed keys gated by
`VALID_PREFERENCE_KEYS`.

### `melitta_barista/sommelier/preferences/get`

| | |
|---|---|
| **Decorators** | `async_response`; no admin requirement |
| **Stability** | stable |
| **Introduced** | ≤0.65.0 |

**Inputs**
- none

**Response**
```json
{ "preferences": { "default_cup_size": "mug", "...": "..." } }
```

### `melitta_barista/sommelier/preferences/set`

| | |
|---|---|
| **Decorators** | `require_admin`, `async_response` |
| **Stability** | stable |
| **Introduced** | ≤0.65.0 |

**Inputs**
- `key: "default_cup_size" \| "default_temperature" \| "default_caffeine" \| "default_dietary"` (required)
- `value: str` (required)

**Response**
- empty success ack.

---

## Sommelier — profiles

User-managed preference profiles (cup size, dietary, caffeine, etc.).
One profile may be "active" at a time and feeds defaults into
`/sommelier/generate`.

### `melitta_barista/sommelier/profiles/list`

| | |
|---|---|
| **Decorators** | `async_response`; no admin requirement |
| **Stability** | stable |
| **Introduced** | ≤0.65.0 |

**Inputs**
- none

**Response**
```json
{ "profiles": [ { "id": "...", "name": "...", "active": false, "...": "..." } ] }
```

### `melitta_barista/sommelier/profiles/add`

| | |
|---|---|
| **Decorators** | `require_admin`, `async_response` |
| **Stability** | stable |
| **Introduced** | ≤0.65.0 |

**Inputs**
- `name: str` (required)
- `preferences: dict` (optional, default `{}`) — merged into the new
  profile row (e.g. `cup_size`, `dietary`, `caffeine_pref`).

**Response**
```json
{ "profile": { "id": "...", "name": "...", "...": "..." } }
```

**Notes**
- Since 0.93.0 legacy cup-size tokens are normalized on ingest
  (`espresso` → `espresso_cup`, UI Contract §9.2.6.4) in both add and
  update; a one-time DB migration (schema v11) rewrote previously
  stored legacy tokens in `sommelier_profiles.cup_size` and
  `user_preferences.default_cup_size`.

### `melitta_barista/sommelier/profiles/update`

| | |
|---|---|
| **Decorators** | `require_admin`, `async_response` |
| **Stability** | stable |
| **Introduced** | ≤0.65.0 |

**Inputs**
- `profile_id: str` (required)
- `name: str` (optional)
- `preferences: dict` (optional)

**Response**
```json
{ "profile": { "id": "...", "...": "..." } }
```

**Errors**
- `not_found` — profile id is unknown.

### `melitta_barista/sommelier/profiles/delete`

| | |
|---|---|
| **Decorators** | `require_admin`, `async_response` |
| **Stability** | stable |
| **Introduced** | ≤0.65.0 |

**Inputs**
- `profile_id: str` (required)

**Response**
- empty success ack.

**Errors**
- `not_found` — profile id is unknown.

### `melitta_barista/sommelier/profiles/activate`

| | |
|---|---|
| **Decorators** | `require_admin`, `async_response` |
| **Stability** | stable |
| **Introduced** | ≤0.65.0 |

**Inputs**
- `profile_id: str` (required)

**Response**
- empty success ack.

**Errors**
- `not_found` — profile id is unknown.

**Notes**
- Deactivates all other profiles atomically (DB-level constraint).

---

## Endpoint index

The authoritative, always-current enumeration of registered WS commands is
served at runtime by `melitta_barista/api/info` (it lists every command
registered in `async_register_panel_websocket` and the sommelier module).
A hand-maintained table went stale repeatedly and was removed — consult
the per-endpoint sections above or `api/info` instead.
