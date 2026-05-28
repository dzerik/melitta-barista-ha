# BrandProfile Contract Specification

Status: **stable** as of v0.79.0 (PR-30/31/32/33).

This document describes the contract a new coffee-machine brand must
satisfy to plug into the integration. The contract lives in
`custom_components/melitta_barista/brands/base.py` as the
`BrandProfile` `Protocol` and the `MachineCapabilities` dataclass.

The integration's shared layers (`ble_client.py`, `_ble_commands.py`,
`_ble_recipes.py`, HA entity platforms — `sensor.py`, `button.py`,
`select.py`, `number.py`, `switch.py`) do **not** test
`brand_slug == "X"` or import from `brands.<vendor>` directly. All
brand-specific behaviour flows through the `BrandProfile` Protocol
methods and per-family `MachineCapabilities` flags.

---

## 1. Required `BrandProfile` surface

### Identity

```python
brand_slug: str            # lowercase, no spaces; e.g. "melitta", "nivona"
brand_name: str            # display name; e.g. "Melitta", "Nivona"
service_uuid: str          # BLE GATT primary service UUID
ble_name_regex: re.Pattern[str]   # matches advertisement local_name
handshake_response_size: int      # bytes the handshake reply takes (8 for current brands)
```

`brand_slug` is used in config-entry data and entity unique-ids; once
released it MUST NOT change (it would orphan user devices).

### Capability tables

```python
supported_extensions: frozenset[str]    # BLE opcodes the brand exposes ("HC", "HJ", …)
families: dict[str, MachineCapabilities]  # family_key → caps
```

`supported_extensions` documents which optional opcodes the brand
firmware supports. Used by shared layers to decide whether to attempt
e.g. `read_recipe` (HC). Current values:

| Brand | `supported_extensions` |
|---|---|
| Melitta | `{"HC", "HJ"}` |
| Nivona | `frozenset()` (empty — uses HE-selector brewing) |

### Crypto

```python
runtime_rc4_key: bytes              # 32-byte ASCII key
hu_table: bytes                     # 256-byte CRC table

def hu_verifier(self, buf: bytes, start: int, count: int) -> bytes:
    """2-byte handshake verifier — brand-specific table-driven fold."""
```

The handshake verifier algorithm shape is the same across brands but
the table values and the per-round offsets differ. Implement on the
brand profile class.

### Family resolution + status parsing

```python
def detect_family(self, ble_name: str, dis: dict[str, str] | None) -> str | None:
    """Map advertisement local_name (+ optional DIS data) to a key in `families`."""

def capabilities_for(self, family_key: str) -> MachineCapabilities:
    """Return capability bag for the family. Raises KeyError if unknown."""

def parse_status(self, family_key: str | None, data: bytes):
    """Map an HX status payload to MachineStatus with brand-specific process codes."""
```

Different brands use different raw numbers for the same logical state.
Examples observed in production:
- Melitta: `READY = 2`, `PRODUCT = 4`
- Nivona NIVO 8000: `READY = 3`, `PRODUCT = 4`
- Nivona 600/700/79x/900/1030/1040: `READY = 8`, `PRODUCT = 11`

`parse_status` translates raw integers to the brand-agnostic
`MachineProcess` enum.

### Recipe write-path contract (v0.79.0+)

These methods exist so shared mixins (`_ble_commands`, `_ble_recipes`)
and HA platforms don't `import from brands.<vendor>` directly. Brands
that don't expose the corresponding feature return `None` / `1` /
`False` stubs.

```python
temp_recipe_type_register: int | None      # ClassVar — fixed HW register or None

def temp_recipe_register(self, family_key, recipe_id, field) -> int | None: ...
def fluid_write_scale(self, family_key) -> int: ...      # 1 by default, 10 for some families
def mycoffee_layout(self, family_key) -> RecipeFieldLayout | None: ...
def mycoffee_register(self, slot, offset) -> int | None: ...
def is_chilled_selector(self, selector) -> bool: ...
```

| Method | Returns when not supported |
|---|---|
| `temp_recipe_type_register` | `None` (class attribute) |
| `temp_recipe_register` | `None` |
| `fluid_write_scale` | `1` |
| `mycoffee_layout` | `None` |
| `mycoffee_register` | `None` |
| `is_chilled_selector` | `False` |

---

## 2. Required `MachineCapabilities` fields

Each entry in `families` is a `MachineCapabilities` dataclass with
these mandatory fields:

```python
family_key: str            # matches dict key — used by parse_status etc.
model_name: str            # surfaced as HA `model` attribute on the device
```

### Feature flags (defaults shown)

```python
supports_recipe_writes: bool = False    # HC/HD/HJ recipe editing
supports_stats: bool = False            # has populated `stats` tuple
supports_factory_reset: bool = False    # firmware exposes factory-reset HE commands
supports_brew_overrides: bool = False   # per-brew temp-recipe slot pattern
uses_legacy_total_cups_sensor: bool = False  # Melitta-only hand-tailored sensor

my_coffee_slots: int = 0
strength_levels: int = 5
has_aroma_balance: bool = False
first_mycoffee_selector: int = 20       # MyCoffee HE selector base (20 for Nivona)
```

### Protocol quirks

```python
fluid_scale_factor: int = 1             # ml × N when writing fluid amounts (Nivona 900 = 10)
brew_command_mode: int = 0x0B           # HE byte; 0x04 for NIVO 8000
recipe_text_encoding: str = "legacy_1byte"   # vs "utf16_le"
tolerated_brew_manipulations: tuple[int, ...] = ()  # Manipulation flags that don't block brew
```

### Tables (filled per family)

```python
recipes: tuple[RecipeDescriptor, ...] = ()      # drink selectors
settings: tuple[SettingDescriptor, ...] = ()    # HR-readable settings
stats: tuple[StatDescriptor, ...] = ()          # HR counters
```

**Important:** Melitta leaves `settings = ()` even though it has
settings entities — Melitta's settings are surfaced through the legacy
hand-coded `MachineSettingSelect/Number/Switch` entities (driven by
`const.MachineSettingId`), not the capability-driven `BrandSettingX`
entities. The capability-driven entities check `if caps.settings:` and
skip when empty.

---

## 3. How to add a new brand

### Step 1 — create the package

```
brands/<vendor>/
  __init__.py              # YourProfile class + module-level helpers
  _crypto.py               # RC4 key + HU table constants (if brand-wide)
  _options.py              # setting-option enums (optional)
  _registers.py            # register bases + address helpers (if brand has them)
  _stats_helpers.py        # _count / _pct / _flag factories (optional)
  _prefixes.py             # serial-prefix → family map (if multiple families)
  _family_<key>.py         # one per family, exporting CAPABILITIES + EXPORTS dict
```

Patterns for `_family_*.py` and the `EXPORTS` aggregation loop in
`__init__.py` are documented in `brands/nivona/` — copy that structure.

### Step 2 — implement the BrandProfile Protocol

Minimum boilerplate:

```python
class YourProfile:
    brand_slug: ClassVar[str] = "your_vendor"
    brand_name: ClassVar[str] = "Your Vendor"
    service_uuid: ClassVar[str] = "..."
    handshake_response_size: ClassVar[int] = 8
    ble_name_regex: ClassVar[re.Pattern[str]] = re.compile(r"^...")
    supported_extensions: ClassVar[frozenset[str]] = frozenset()
    families: ClassVar[dict[str, MachineCapabilities]] = _YOUR_FAMILIES

    # Crypto + handshake — required
    @property
    def runtime_rc4_key(self) -> bytes: ...
    @property
    def hu_table(self) -> bytes: ...
    def hu_verifier(self, buf, start, count) -> bytes: ...

    # Family resolution + status parsing — required
    def detect_family(self, ble_name, dis=None) -> str | None: ...
    def capabilities_for(self, family_key) -> MachineCapabilities: ...
    def parse_status(self, family_key, data): ...

    # Recipe write-path — implement or use stub returns
    temp_recipe_type_register: ClassVar[int | None] = None
    def temp_recipe_register(self, *a) -> int | None: return None
    def fluid_write_scale(self, family_key) -> int: return 1
    def mycoffee_layout(self, family_key) -> RecipeFieldLayout | None: return None
    def mycoffee_register(self, slot, offset) -> int | None: return None
    def is_chilled_selector(self, selector) -> bool: return False
```

### Step 3 — register in `brands/__init__.py`

```python
from .your_vendor import YourProfile
# add YourProfile() to the BrandRegistry instance
```

### Step 4 — add tests

Minimum test coverage:
- `test_brands.py::test_<vendor>_*` — verify family detection, HU
  verifier with a known vector, capability shape
- `test_protocol.py::test_<vendor>_handshake_*` — verify handshake
  reply matches expected verifier output
- `test_<vendor>_parse_status` — pin process-code dispatch

---

## 4. Anti-patterns

These were removed in v0.79.0 and MUST NOT come back:

❌ `if client.brand.brand_slug == "your_vendor": ...` in shared layers
   → use a capability flag on `MachineCapabilities`.

❌ `from .brands.your_vendor import some_helper` in shared layers
   → lift the helper to a `BrandProfile` method with a stub on other brands.

❌ `isinstance(client.brand, YourProfile)`
   → same — use capability flag.

❌ Hardcoded family-key lists like `_FACTORY_RESET_FAMILIES = {"600", ...}`
   → use capability flag on `MachineCapabilities`.

❌ Mutating `MachineCapabilities` fields after construction
   → it's `@dataclass(frozen=True)`; use `dataclasses.replace()`.

---

## 5. What's NOT in the contract yet

Forward-looking — not blocking new brands today, but worth flagging:

- **No external plugin loader.** All brands live in-tree. Adding a 4th
  brand still requires editing `brands/__init__.py` to register the
  profile. Plugin-from-disk discovery is deliberately not built.
- **No versioning of the Protocol surface.** Adding a method to
  `BrandProfile` is a breaking change for any out-of-tree implementer.
  There are zero out-of-tree implementers today, so we move freely.
- **No formal capability-coverage test framework.** A new brand
  passing 0 tests is technically allowed; we rely on PR review to
  catch missing coverage.

When/if these become bottlenecks (community-driven 3rd brand, external
plugin requests), revisit. Until then the contract above is sufficient.
