# Аудит кодовой базы Melitta Barista HA Integration

- **Дата**: 2026-03-08
- **Версия**: 0.11.3
- **Аудитор**: Claude Code (автоматизированный аудит)
- **Инструменты**: ruff 0.15.5, bandit 1.9.4, radon 6.0.1, vulture 2.15, pytest 9.0.0, gitleaks, pip-audit

---

## Резюме

| Категория | Оценка | Критических | Важных | Мелких |
|-----------|--------|-------------|--------|--------|
| Безопасность | **B+** | 0 | 1 | 1 |
| BLE Crypto | **B** | 0 | 1 | 1 |
| BLE Lifecycle | **A-** | 0 | 0 | 1 |
| HA Integration Patterns | **A-** | 0 | 0 | 1 |
| Архитектура / SOLID | **A-** | 0 | 0 | 2 |
| Тесты | **B+** | 0 | 1 | 1 |
| Error Handling | **B+** | 0 | 0 | 2 |
| Код: размер / сложность | **B+** | 0 | 0 | 2 |
| Документация | **A** | 0 | 0 | 1 |
| Git / CI | **A** | 0 | 0 | 0 |
| **ОБЩАЯ** | **B+** | **0** | **3** | **12** |

---

## Скоринг

| Метрика | Значение |
|---------|----------|
| **Security Score** | **87/100 (B+)** |
| **TDI** | (0×10 + 3×3 + 12×1) / 100 × 100 = **2.1 (LOW)** |
| **Test Coverage** | **82% (B+)** |
| **HA Quality** | **9/10 checks passed** |
| **HACS Ready** | **YES** |

### Сравнение с предыдущим аудитом (v0.11.1)

| Метрика | v0.11.1 | v0.11.3 | Изменение |
|---------|---------|---------|-----------|
| Ruff errors | 18 | 0 | **ALL FIXED** |
| Bandit findings | 3 | 2 | B413 suppressed (false positive) |
| Failing tests | 1 | 0 | **FIXED** |
| Test count | 123 | 174 | **+51** |
| Coverage | 71% | 82% | **+11%** |
| Broad `except Exception` | 30 | 3 | **-27** (только callbacks) |
| CC > 15 functions | 2 | 0 | **FIXED** (max CC=14) |
| TDI | 5.1 (MODERATE) | 2.1 (LOW) | **Улучшение** |

---

## 1. Безопасность

### 1.1 Hardcoded Secrets

**Статус: OK**

- Нет hardcoded паролей, API ключей, PSK в production коде
- `gitleaks`: 0 leaks found (65 коммитов просканировано)
- `.gitignore` покрывает `esphome/secrets.yaml`, `decompiled/`, `jadx/`
- BLE адреса есть только в `scripts/` (тестовые утилиты), не в production коде

### 1.2 BLE Crypto

**Статус: B** — AES-CBC корректен, но hardcoded shared key (by design)

| Проверка | Результат |
|----------|----------|
| AES mode | CBC (OK, не ECB) |
| IV | Hardcoded static IV (**важная** — но вынужденная: протокол Melitta) |
| Key | AES key для дешифровки RC4 seed — hardcoded (by design, reverse-engineered из APK) |
| Padding | PKCS5 padding strip (OK) |
| RC4 stream cipher | Используется для BLE frame encryption |

> **Контекст**: AES key и IV hardcoded потому что это reverse-engineered протокол Melitta. Ключи одинаковые для всех машин — это design решение производителя, не баг интеграции.

- **ВАЖНАЯ**: Bandit B413 — `pyCrypto` deprecated. Используется `pycryptodome` (fork), который активно поддерживается. Bandit не различает оригинальный pyCrypto и pycryptodome. **False positive** — стоит добавить `# nosec B413`.

### 1.3 BLE Payload Validation

**Статус: A** — Все `from_payload` / `from_bytes` имеют length guards

| Parser | Guard |
|--------|-------|
| `MachineStatus.from_payload` | `len(data) < 8` → return default |
| `RecipeComponent.from_bytes` | `len(data) < 8` → return None + log |
| `MachineRecipe.from_payload` | `len(data) < 19` → return None + log |
| `NumericalValue.from_payload` | `len(data) < 6` → return None + log |
| `AlphanumericValue.from_payload` | `len(data) < 2` → return None + log |

UTF-8 decode: `errors="replace"` — корректно.

### 1.4 D-Bus Security

**Статус: A-**

- D-Bus connection закрывается в finally-блоке через `_cleanup()` helper
- Fallback при отсутствии Adapter1 интерфейса (ESPHome proxy)
- Timeout: `asyncio.wait_for(call_pair(), timeout=timeout)` — OK
- PIN code: `"0000"` hardcoded — стандартный BLE JustWorks pairing, OK
- Функция разбита на 6 helper-ов (CC снижен с 17 до 5)

### 1.5 Supply Chain

**Статус: B-**

- 3 CVE найдено pip-audit:
  - `pillow 12.0.0` CVE-2026-25990 (fix: 12.1.1) — косвенная зависимость HA
  - `pip 25.1.1` CVE-2025-8869, CVE-2026-1703 (fix: 25.3/26.0) — не runtime
- Зависимости в manifest.json pinned с `>=` (OK)
- Минимальный набор: bleak, bleak-retry-connector, pycryptodome

### Security Score Breakdown

| Проверка | Вес | Оценка |
|----------|-----|--------|
| Нет hardcoded secrets | 15 | +15 |
| BLE crypto (AES-CBC, not ECB) | 15 | +12 (static IV) |
| BLE payload validation | 10 | +10 |
| D-Bus pairing | 10 | +10 |
| Error handling / no data leaks | 10 | +10 |
| Race conditions | 15 | +15 |
| Config flow validation | 10 | +10 |
| Dependencies | 10 | +5 (CVEs in transitive deps) |
| .gitignore covers secrets | 5 | +5 |
| **Total** | **100** | **87** |

---

## 2. Архитектура

### 2.1 HA Integration Patterns

**Статус: A-** — Очень хорошее соответствие HA best practices

| Проверка | Статус |
|----------|--------|
| `async_added_to_hass` для callbacks | OK (все 12 entity files) |
| `async_will_remove_from_hass` | Не реализован — **мелкая** |
| `available` property | OK — отражает BLE connection |
| `unique_id` стабильный (BLE address) | OK |
| `device_info` единообразен | OK |
| Config flow: bluetooth discovery + manual | OK |
| `services.yaml` | OK — `brew_freestyle` |
| Translations (29 языков) | OK |
| HACS manifest | OK |
| `_write_lock` для serialized GATT | OK (добавлен в v0.11.2) |

- **Мелкая**: `async_will_remove_from_hass` не реализован в entities — connection callbacks не очищаются при unload

### 2.2 BLE Lifecycle

**Статус: A-**

| Проверка | Статус |
|----------|--------|
| Connect → discover → subscribe → ready | OK |
| Disconnect callback → reconnect | OK |
| `pair=True` на обоих путях | OK |
| `establish_connection` + fallback `BleakClient` | OK |
| `self._client` захват в local var | OK (5 мест) |
| `asyncio.Lock` для connect | OK (`_connect_lock`) |
| `asyncio.Lock` для write | OK (`_write_lock`) |
| Service caching | OK (`use_services_cache=True`) |

- **Мелкая**: BLE disconnect during brew — reconnect работает, но brew state может потеряться

### 2.3 SOLID

- **S**: В целом OK. `ble_client.py` (647 строк) — пограничный God module, но для HA integration допустимо
- **O**: Новые рецепты добавляются через `const.py` enums — OK
- **L**: Entity наследование от HA base classes корректное
- **D**: `ble_client` инжектится в entities через `__init__.py` setup — OK

`ble_agent.py:async_pair_device()` разбит на 6 helpers (было 154 строк, CC=17 → стало ~30 строк, CC=5) — **FIXED в v0.11.3**

### 2.4 DRY

Vulture (dead code): 4 находки (все допустимые)

| Файл | Dead code | Тип |
|------|-----------|-----|
| `__init__.py:110` | `change` unused var | **мелкая** (HA track callback pattern) |
| `ble_agent.py:53` | `entered` unused var | **мелкая** (D-Bus method signature) |
| `switch.py:111,116` | `kwargs` unused | **мелкая** (HA interface contract) |

> Все 4 — false positives: переменные требуются для сигнатуры интерфейсов (D-Bus, HA).

---

## 3. Тесты и Runtime

### 3.1 Test Coverage: 82% (B+)

```
174 passed, 0 failed, 3 warnings
```

| Модуль | Coverage | Оценка | Изменение |
|--------|----------|--------|-----------|
| `const.py` | 100% | Excellent | — |
| `config_flow.py` | 100% | Excellent | **23% → 100%** |
| `sensor.py` | 94% | Excellent | — |
| `ble_agent.py` | 93% | Excellent | **0% → 93%** |
| `number.py` | 89% | Good | — |
| `text.py` | 88% | Good | — |
| `protocol.py` | 82% | Good | — |
| `button.py` | 82% | Good | — |
| `switch.py` | 81% | Good | — |
| `select.py` | 77% | Good | +1% |
| `__init__.py` | 72% | OK | +1% |
| `ble_client.py` | 62% | Needs work | — |

- **Важная**: `ble_client.py` покрытие 62% — основной BLE модуль, нужно больше тестов (connect flows, reconnect, brew sequences)

### 3.2 Test Warnings

3 RuntimeWarning в `test_ble_agent.py` — unawaited coroutines в mock chain. Не влияют на корректность, но стоит почистить mock setup.

### 3.3 Error Handling

**Статус: B+** (было C+)

| Паттерн | Количество | Оценка |
|---------|-----------|--------|
| `except Exception:` (broad catch) | **3** | OK — только в callbacks (user code isolation) |
| `except (BleakError, OSError, asyncio.TimeoutError)` | 15 | OK — specific BLE exceptions |
| `except (AttributeError, ValueError)` | 2 | OK — HA bluetooth API |
| Все exceptions логируются | ~95% | Good |
| Единый логгер `melitta_barista` | OK (все 11 файлов) |
| `print()` в production | 0 | OK |

Оставшиеся 3 `except Exception` — в callback dispatchers (корректный паттерн: изоляция user-code ошибок).

### 3.4 Code Complexity

Radon CC (Cyclomatic Complexity >= C):

| Функция | CC | Оценка |
|---------|----|----|
| `config_flow._async_discover_devices` | **14** | C — borderline, но приемлемо (discovery logic) |
| `protocol._dispatch_frame` | **12** | C — приемлемо (message dispatch) |
| `ble_client._connect_impl` | **11** | C — приемлемо (connection flow) |
| `__init__._async_cleanup_legacy` | **11** | C — приемлемо (migration cleanup) |

Radon MI (Maintainability Index): все модули A или B — нет проблем.

> **Прогресс**: было 2 функции с CC>15 (D-уровень), теперь 0. Максимум CC=14.

### 3.5 Ruff Linter

**0 errors** — all checks passed.

> **Прогресс**: было 18 errors (v0.11.1) → 14 в тестах (v0.11.3) → 0 (текущий).

### 3.6 Bandit Security

2 findings (Low severity, acceptable):

| ID | Severity | Файл | Описание | Статус |
|----|----------|------|----------|--------|
| ~~B413~~ | ~~High~~ | ~~`protocol.py:12`~~ | ~~pyCrypto deprecated~~ | **Suppressed** (`# nosec B413`) |
| B110 | Low | `ble_agent.py:170` | try/except/pass | Cleanup code — допустимо |
| B110 | Low | `ble_agent.py:177` | try/except/pass | Cleanup code — допустимо |

---

## 4. LLM Analysis

### 4.1 Бизнес-процессы

Интеграция реализует:

1. **Brew Flow**: выбор рецепта → формирование BLE команды → отправка → мониторинг
2. **Freestyle Brew**: кастомный рецепт с параметрами (intensity, temperature, shots, portion)
3. **Profile Management**: переключение профилей, DirectKey расчёт
4. **Machine Monitoring**: состояние, прогресс, необходимые действия
5. **Cup Counting**: счётчики по рецептам + общий, auto-refresh после brew
6. **Configuration**: hardness воды, auto-off, brew temperature
7. **Maintenance**: очистка, промывка, декальцинация

Бизнес-логика хорошо структурирована и понятна из кода.

### 4.2 BLE Protocol

```mermaid
sequenceDiagram
    participant HA as Home Assistant
    participant BLE as BleClient
    participant Proto as Protocol
    participant Machine as Melitta

    HA->>BLE: connect()
    BLE->>Machine: GATT Connect + Discover Services
    BLE->>Machine: Subscribe Notify (ad02)
    BLE->>Proto: build_frame(CMD_HANDSHAKE)
    Proto->>Proto: AES-CBC decrypt → RC4 key
    Proto-->>BLE: handshake frame
    BLE->>Machine: Write (ad01)
    Machine-->>BLE: Notify (ad02) — handshake response
    BLE->>Proto: on_data() → RC4 init
    Proto-->>BLE: connected + state

    Note over HA,Machine: Ready for commands

    HA->>BLE: brew_recipe(espresso)
    BLE->>Proto: build_frame(CMD_BREW, recipe_id)
    Proto->>Proto: RC4 encrypt
    Proto-->>BLE: encrypted frame
    BLE->>Machine: Write (ad01)
    Machine-->>BLE: Notify — state updates
    BLE->>Proto: parse frame
    Proto-->>HA: state callback → entity update
```

### 4.3 Качество именования

- Enum names понятны: `MachineProcess.BREWING`, `MachineState.READY`
- BLE characteristics именованы: `CHAR_WRITE (ad01)`, `CHAR_NOTIFY (ad02)`
- Methods self-documenting: `brew_recipe`, `read_recipe_details`, `refresh_cup_counters`
- Constants в `const.py` хорошо организованы (recipes, DirectKey, translations)

### 4.4 Граничные случаи

| Кейс | Обработан? |
|------|-----------|
| BLE disconnect during brew | Частично — reconnect, но brew state может потеряться |
| Short BLE payload | Да — length guards на всех parsers |
| Concurrent GATT writes | Да — `_write_lock` (asyncio.Lock) |
| Machine powered off mid-command | Да — disconnect callback |
| ESPHome proxy restart | Да — reconnect через HA bluetooth |
| Invalid recipe ID | Нет — no validation before BLE write |
| Two HA instances | Нет — BLE single connection, second will fail to connect |

---

## TOP 10 Findings

| # | Уровень | Проблема | Статус |
|---|---------|----------|--------|
| 1 | ~~Важная~~ | ~~Failing test `test_recipe_select_option`~~ | **FIXED v0.11.2** |
| 2 | ~~Важная~~ | ~~`config_flow.py` test coverage 23%~~ | **FIXED v0.11.3** (100%) |
| 3 | ~~Важная~~ | ~~`ble_agent.py` test coverage 0%~~ | **FIXED v0.11.3** (93%) |
| 4 | ~~Важная~~ | ~~30 broad `except Exception` catches~~ | **FIXED v0.11.3** (3 remain, callbacks only) |
| 5 | ~~Важная~~ | ~~`async_step_user` CC=18~~ | **FIXED v0.11.3** (extracted `_async_discover_devices`) |
| 6 | ~~Важная~~ | ~~`async_pair_device` 154 lines, CC=17~~ | **FIXED v0.11.3** (6 helpers, CC=5) |
| 7 | ~~Важная~~ | ~~Нет `_write_lock` для GATT writes~~ | **FIXED v0.11.2** |
| 8 | ~~Важная~~ | ~~Bandit B413 — pycryptodome flagged as pyCrypto~~ | **FIXED** (`# nosec B413`) |
| 9 | Важная | `ble_client.py` coverage 62% | Open |
| 10 | ~~Важная~~ | ~~14 ruff errors в тестах (unused imports/vars)~~ | **FIXED** (0 errors) |

### Новые findings (v0.11.3)

| # | Уровень | Проблема | Файл |
|---|---------|----------|------|
| 11 | Мелкая | 3 RuntimeWarning в test_ble_agent (unawaited coroutines) | `tests/test_ble_agent.py` |
| 12 | Мелкая | `async_will_remove_from_hass` не реализован | entities |
| 13 | Мелкая | `_async_discover_devices` CC=14 (borderline) | `config_flow.py` |
| 14 | Мелкая | No recipe ID validation before BLE write | `ble_client.py` |
| 15 | Мелкая | CVEs в transitive deps (pillow, pip) | venv |

---

## План действий

### Немедленно

1. ~~**Исправить 14 ruff errors в тестах**~~ — **DONE**
2. ~~**Добавить `# nosec B413`**~~ — **DONE**

### В течение спринта

3. **Поднять coverage `ble_client.py`** (62% → 80%+) — тесты для connect flows, reconnect, brew sequences
4. **Почистить RuntimeWarning** в test_ble_agent.py (unawaited mock coroutines)

### Плановый рефакторинг

5. **Рассмотреть `async_will_remove_from_hass`** для cleanup callbacks
6. **Recipe ID validation** перед BLE write
7. **Обновить transitive deps** (pillow, pip) при следующем обновлении HA

---

## ФИНАЛЬНАЯ ОЦЕНКА

| Метрика | v0.11.1 | v0.11.3 |
|---------|---------|---------|
| **Общая оценка** | **B** | **B+** |
| **HACS Ready** | YES | YES |
| **Security Risk** | LOW | LOW |
| **Refactor Required** | MINOR | MINIMAL |
| **Production Ready** | YES (с оговоркой) | **YES** |

### Сильные стороны

- Отличное соответствие HA integration patterns
- Robust BLE payload validation с length guards
- Хорошая защита от race conditions (connect_lock, write_lock, local var capture)
- Единообразный логгер, чистые translations (29 языков)
- Gitleaks clean, no hardcoded secrets
- 174 теста, 82% coverage, 0 failures
- Exception handling сужен до конкретных типов (BleakError, OSError, TimeoutError)
- Все функции CC < 15 (максимум 14)
- 0 ruff errors в production коде

### Области для улучшения

- `ble_client.py` coverage 62% — основной модуль
- `ble_client.py` coverage 62% — основной BLE модуль
- `async_will_remove_from_hass` не реализован
- 3 RuntimeWarning в тестах (unawaited mock coroutines)
