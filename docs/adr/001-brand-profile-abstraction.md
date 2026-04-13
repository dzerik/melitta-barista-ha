# ADR 001 — BrandProfile abstraction

- Status: Accepted
- Date: 2026-04-13
- Deciders: dzerik
- Related: [docs/multi-brand-architecture.md](../multi-brand-architecture.md), [docs/oem-eugster-protocol.md](../oem-eugster-protocol.md)

## Context

Интеграция изначально написана под Melitta Barista. Recent research показал,
что Nivona NICR/NIVO использует тот же OEM Eugster/EFLibrary стек:
идентичный BLE service UUID `0000AD00-...`, одинаковая структура фреймов
`0x53...0x45` + checksum, одинаковый набор команд (`HU/HV/HR/HW/HX/HE/HZ/HY/
HD/HI/HA/HB`). Различия:

- **Customer crypto**: каждый бренд имеет свой 32-байтный runtime RC4-ключ
  (Melitta — derived from AES blob, Nivona — `NIV_060616_V10_...`).
- **HU verifier**: разные 256-байтные lookup tables и фолд-функции.
- **Recipe IDs / families**: Melitta нумерует 200-223, Nivona — by selector
  byte per family (600/700/79x/900/1030/1040/8000).
- **Optional commands**: Melitta использует `HC`/`HJ` для recipe read/write
  (расширение OEM-командсета). Nivona их не имеет.
- **Advertisement local_name**: разные регексы (Melitta `8401*`, Nivona
  `NIVONA-NNN-----`).

Без абстракции добавить Nivona = форкать всю интеграцию или хардкодить
условия `if brand == "nivona"` по всему коду.

## Decision

Вводим явный **`BrandProfile` интерфейс** (Python `Protocol` + dataclass'ы)
как **single source of truth для всех брендозависимых констант и логики**:

```python
class BrandProfile(Protocol):
    brand_slug: str                     # "melitta" / "nivona"
    brand_name: str                     # "Melitta" / "Nivona"
    ble_name_regex: re.Pattern
    service_uuid: str

    # Crypto
    runtime_rc4_key: bytes              # 32 bytes
    hu_table: bytes                     # 256 bytes lookup
    def hu_verifier(self, buf: bytes, start: int, count: int) -> bytes

    # Capabilities
    supported_extensions: frozenset[str]   # {"HC", "HJ"} for Melitta
    families: dict[str, MachineCapabilities]
    def detect_family(self, ble_name: str, dis: dict | None) -> str | None
```

Все остальные слои (transport, protocol, ble_client, mixins, entities,
config_flow) используют `brand_profile.<x>` вместо хардкоженных значений.

## Alternatives considered

### Alt 1 — subclassing `MelittaBleClient` → `NivonaBleClient`

Простой, но ведёт к code duplication: 80% кода между classes идентичен.
Плюс subclass-based polymorphism плохо работает с HA entity classes,
которые принимают `client: MelittaBleClient` в type hints.

**Rejected**: длинный путь к maintenance debt.

### Alt 2 — Strategy pattern с base class

`AbstractCoffeeProtocol` + `MelittaProtocol(AbstractCoffeeProtocol)` +
`NivonaProtocol(AbstractCoffeeProtocol)`. Классическое OOP, но требует
дублирования общей логики (framing, RC4) или жирного base class с
abstract methods.

**Rejected**: Pythonic решение через `Protocol` + composition элегантнее.

### Alt 3 — Конфиг-driven (JSON/YAML файлы профилей)

Бренды описываются как JSON-файлы, runtime их грузит. Crypto-tables как
hex-strings, regex как string. Plug & play без перекомпиляции.

**Rejected**: HU-verifier — не data, а функция (2-round S-box fold с
разными константами). Плюс security concern: пользователь может
подложить вредоносный профиль. Tradeoff не стоит свеч для двух брендов.

### Alt 4 — Вариант "ничего не делать"

Хардкодить Melitta + добавить Nivona через `if brand == ...` ветви. Быстро
сейчас, медленно потом.

**Rejected**: scope creep. Третий бренд (если когда-нибудь) — переписывать
всё с нуля.

## Consequences

### Positive

- Чистая extension story: новый бренд = новый файл `brands/X.py`.
- Тестируемость: каждый профиль покрывается своим test-set'ом.
- Type-safe через `Protocol` + `runtime_checkable`.
- Backward-compatible: existing Melitta entries не ломаются (миграция
  через `async_migrate_entry` v1→v2 добавляет `brand="melitta"`).
- Unique_id'ы entity'ей stable — никаких потерь user automations.

### Negative

- Дополнительный indirection в hot path (RC4 encrypt/decrypt, frame parse)
  — invocation `self._brand.runtime_rc4_key` вместо module-level
  `RUNTIME_KEY`. Cost: ~negligible (one attribute lookup).
- Тесты protocol требуют brand fixture (но это уже было — `_derive_rc4_key`
  was не purely deterministic side-effect free).
- Один новый concept для contributors понять (`BrandProfile`).

### Neutral

- `const.py` сохраняет brand-agnostic константы, brand-specific
  re-export'ит из `brands.melitta` для backward-compat (с deprecation
  warning через 2 minor-релиза).

## Implementation

См. [docs/multi-brand-architecture.md](../multi-brand-architecture.md)
"Migration path" для пошагового плана:

- Phase A (refactor): 0.40.0
- Phase B (Nivona): 0.41.0

## References

- [mpapierski/esp-coffee-bridge](https://github.com/mpapierski/esp-coffee-bridge) — `ModelInfo` POD как inspiration для `MachineCapabilities`.
- [mpapierski/esp-coffee-bridge-ha](https://github.com/mpapierski/esp-coffee-bridge-ha) — listener-driven entity spawning, schema-driven descriptors.
- [PEP 544 — Protocols](https://peps.python.org/pep-0544/) — Structural subtyping rationale.
