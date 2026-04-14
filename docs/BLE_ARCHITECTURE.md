# Melitta Barista & Nivona — BLE Architecture & Protocol

> Подробная техническая документация по BLE-стеку интеграции (общий Eugster/Frismag OEM-стек, используемый Melitta Barista T/TS Smart и Nivona NICR/NIVO 8xxx): от Bluetooth-слоя до HA-сущностей.

## Содержание

1. [Общая архитектура](#1-общая-архитектура)
2. [BLE-транспорт: Bleak → ESPHome Proxy → Машина](#2-ble-транспорт)
3. [Жизненный цикл соединения](#3-жизненный-цикл-соединения)
4. [Протокол Melitta BLE](#4-протокол-melitta-ble)
5. [Криптография](#5-криптография)
6. [Команды протокола](#6-команды-протокола)
7. [Реконнект и отказоустойчивость](#7-реконнект-и-отказоустойчивость)
8. [HA Entity-архитектура](#8-ha-entity-архитектура)
9. [Потоки данных](#9-потоки-данных)
10. [Найденные и исправленные проблемы](#10-найденные-и-исправленные-проблемы-v0232--v0233)
11. [Ссылки на HA API](#11-ссылки-на-ha-api)

---

## 1. Общая архитектура

```mermaid
graph TB
    subgraph "Home Assistant"
        HA_BT["HA Bluetooth Manager<br/>(habluetooth)"]
        INIT["__init__.py<br/>Integration Lifecycle"]
        BLE_CLIENT["ble_client.py<br/>MelittaBleClient"]
        PROTOCOL["protocol.py<br/>MelittaProtocol"]
        ENTITIES["Entities<br/>sensor / button / switch / select / number / text"]
    end

    subgraph "BLE Transport"
        BRC["bleak-retry-connector<br/>establish_connection()"]
        BLEAK["bleak<br/>BleakClient + GATT"]
    end

    subgraph "Hardware"
        ESP["ESPHome BLE Proxy<br/>(ESP32-C6)"]
        MACHINE["Melitta Barista Smart<br/>BLE Peripheral"]
    end

    HA_BT -->|"advertisement<br/>callbacks"| INIT
    INIT -->|"set_ble_device()"| BLE_CLIENT
    BLE_CLIENT -->|"write_func()"| PROTOCOL
    PROTOCOL -->|"build_frame()<br/>RC4 encrypt"| BLE_CLIENT
    BLE_CLIENT -->|"write_gatt_char()"| BRC
    BRC --> BLEAK
    BLEAK -->|"BLE over WiFi"| ESP
    ESP -->|"GATT"| MACHINE
    MACHINE -->|"notify char"| ESP
    ESP --> BLEAK
    BLEAK -->|"_on_notification()"| BLE_CLIENT
    BLE_CLIENT -->|"on_ble_data()"| PROTOCOL
    PROTOCOL -->|"status callbacks"| ENTITIES
    BLE_CLIENT -->|"connection callbacks"| ENTITIES
```

### Слои абстракции

| Слой | Модуль | Ответственность |
|------|--------|-----------------|
| **HA Integration** | `__init__.py` | Lifecycle, BLE advertisement callback, сервисы |
| **BLE Client** | `ble_client.py` | Управление соединением, reconnect, polling, locks |
| **Protocol** | `protocol.py` | Framing, шифрование, command/response, parsing |
| **Transport** | `bleak` + `bleak-retry-connector` | GATT read/write/notify, retry logic |
| **Proxy** | ESPHome BLE Proxy | WiFi↔BLE bridge (опционально) |
| **Entities** | `sensor.py`, `button.py`, ... | HA UI, состояние, actions |

---

## 2. BLE-транспорт

### BLE-характеристики машины

| Параметр | Значение |
|----------|----------|
| Service UUID | `0000ad00-b35c-11e4-9813-0002a5d5c51b` |
| Write Characteristic | `0000ad01-...` (write without response) |
| Notify Characteristic | `0000ad02-...` (notifications) |
| Address type | Static Random (`F1:xx:xx`) |
| MTU | 20 bytes (стандартный BLE) |

### Два режима подключения

```mermaid
graph LR
    subgraph "Режим 1: Локальный BlueZ"
        HA1["HA"] -->|"D-Bus"| BLUEZ["BlueZ<br/>(Linux)"]
        BLUEZ -->|"HCI"| BT_ADAPTER["USB BT Adapter"]
        BT_ADAPTER -->|"BLE"| M1["Melitta"]
    end

    subgraph "Режим 2: ESPHome Proxy"
        HA2["HA"] -->|"WiFi<br/>(aioesphomeapi)"| ESP32["ESP32-C6<br/>(ESPHome)"]
        ESP32 -->|"BLE"| M2["Melitta"]
    end
```

**Локальный BlueZ:**
- D-Bus Agent1 pairing через `ble_agent.py`
- `establish_connection(pair=True)` для bonding
- Прямой HCI доступ

**ESPHome BLE Proxy** (рекомендуемый):
- ESP32 как WiFi→BLE мост
- `ble_agent.py` детектирует отсутствие `Adapter1` D-Bus интерфейса, пропускает D-Bus pairing
- `pair=True` обрабатывается на стороне ESP32
- `address_type=1` (random) передаётся через `BLEDevice` из HA bluetooth cache
- Конфиг: `esphome/ble-proxy-xiao-c6.yaml`

### Интеграция с HA Bluetooth API

```python
# __init__.py: регистрация callback на BLE advertisements
bluetooth.async_register_callback(
    hass,
    _async_update_ble,       # вызывается при каждом advertisement
    {"address": address},    # фильтр по MAC-адресу
    bluetooth.BluetoothScanningMode.ACTIVE,
)
```

Когда машина включается и начинает advertising, HA Bluetooth Manager детектирует advertisement через ESPHome proxy и вызывает `_async_update_ble` → `set_ble_device()`, что обновляет `BLEDevice` и будит reconnect loop.

> **Документация HA:** [Bluetooth API — async_register_callback](https://developers.home-assistant.io/docs/core/bluetooth/api/)

### establish_connection и ble_device_callback

```python
# ble_client.py: _establish_connection()
client = await establish_connection(
    BleakClientWithServiceCache,    # кэширование GATT-сервисов
    self._ble_device,               # текущий BLEDevice
    self._device_name or self._address,
    disconnected_callback=self._on_disconnect,
    use_services_cache=True,
    ble_device_callback=lambda: self._ble_device,  # ВСЕГДА свежий reference
    max_attempts=3,
    pair=pair,
)
```

**Зачем `ble_device_callback`?** При retry `establish_connection` вызывает эту lambda для получения актуального `BLEDevice`. Между попытками мог прийти новый advertisement, обновивший `self._ble_device` через `set_ble_device()`.

> **Документация:** [bleak-retry-connector](https://github.com/Bluetooth-Devices/bleak-retry-connector)

---

## 3. Жизненный цикл соединения

### Полная state machine

```mermaid
stateDiagram-v2
    [*] --> Setup: async_setup_entry()

    Setup --> InitialConnect: _async_connect_and_poll()

    InitialConnect --> Connecting: connect()
    InitialConnect --> WaitBackoff: connection failed
    WaitBackoff --> WaitBackoff: timeout → delay*2
    WaitBackoff --> Connecting: advertisement wakes up
    WaitBackoff --> Connecting: backoff elapsed

    Connecting --> PairAttempt1: _connect_impl()
    PairAttempt1 --> Handshake: BLE connected (pair=False)
    PairAttempt1 --> PairAttempt2: bond not found
    PairAttempt2 --> Handshake: BLE connected (pair=True)
    PairAttempt2 --> PairAttempt3: stale bond
    PairAttempt3 --> Handshake: unpair + pair=True
    PairAttempt3 --> ConnectFailed: all 3 attempts failed

    Handshake --> ReadFirmware: HU handshake OK
    Handshake --> ConnectFailed: handshake timeout

    ReadFirmware --> Connected: version + machine type read
    Connected --> Polling: start_polling()

    Polling --> Polling: poll_status() every N sec
    Polling --> PollErrors: BleakError / timeout
    PollErrors --> Polling: error < max_consecutive
    PollErrors --> ForcedDisconnect: errors >= max_consecutive

    Connected --> Disconnected: _on_disconnect() callback
    ForcedDisconnect --> Disconnected: _safe_disconnect()

    Disconnected --> ReconnectLoop: _schedule_reconnect()
    ReconnectLoop --> Connecting: delay elapsed or advertisement
    ReconnectLoop --> ReconnectLoop: connect failed → backoff*2

    ConnectFailed --> ReconnectLoop: from _reconnect_loop
    ConnectFailed --> WaitBackoff: from _async_connect_and_poll

    Connected --> Shutdown: disconnect()
    Shutdown --> [*]: _auto_reconnect=False
```

### Последовательность подключения (happy path)

```mermaid
sequenceDiagram
    participant HA as Home Assistant
    participant INIT as __init__.py
    participant CLIENT as MelittaBleClient
    participant PROTO as MelittaProtocol
    participant BRC as bleak-retry-connector
    participant ESP as ESPHome Proxy
    participant MACH as Melitta Machine

    HA->>INIT: async_setup_entry()
    INIT->>INIT: bluetooth.async_ble_device_from_address()
    INIT->>CLIENT: MelittaBleClient(address, ble_device)
    INIT->>HA: bluetooth.async_register_callback()
    INIT->>INIT: _async_connect_and_poll() [background]

    Note over INIT,CLIENT: Initial Connect Phase
    INIT->>CLIENT: connect()
    CLIENT->>CLIENT: _connect_impl() [under _connect_lock]

    Note over CLIENT,MACH: Attempt 1: pair=False (reuse bond)
    CLIENT->>BRC: establish_connection(pair=False)
    BRC->>ESP: BLE connect
    ESP->>MACH: GATT connect
    MACH-->>ESP: connected
    ESP-->>BRC: BleakClient
    BRC-->>CLIENT: client

    CLIENT->>MACH: start_notify(ad02)

    Note over CLIENT,MACH: HU Handshake
    CLIENT->>PROTO: perform_handshake(write_func)
    PROTO->>PROTO: generate challenge (4 random bytes)
    PROTO->>PROTO: compute CRC
    PROTO->>CLIENT: write_func(HU frame)
    CLIENT->>MACH: write_gatt_char(ad01, HU frame)
    MACH-->>CLIENT: notify(ad02, HU response)
    CLIENT->>PROTO: on_ble_data(response)
    PROTO->>PROTO: extract key_prefix (2 bytes)
    PROTO-->>CLIENT: handshake OK

    Note over CLIENT,MACH: Post-connect
    CLIENT->>PROTO: read_version()
    MACH-->>CLIENT: HV response (firmware string)
    CLIENT->>PROTO: read_numerical(MACHINE_TYPE)
    MACH-->>CLIENT: HR response (type ID)

    CLIENT->>CLIENT: connection_callbacks(True)
    CLIENT->>CLIENT: _load_post_connect_data() [background]
    CLIENT->>CLIENT: start_polling()

    Note over CLIENT,MACH: Polling Loop
    loop Every poll_interval seconds
        CLIENT->>PROTO: read_status()
        PROTO->>MACH: HX request
        MACH-->>PROTO: HX response (8 bytes)
        PROTO->>CLIENT: status_callback(MachineStatus)
        CLIENT->>HA: Entity state updates
    end
```

### 3-ступенчатая стратегия pairing

```mermaid
flowchart TD
    START["connect()"] --> CHECK{"Уже подключен?"}
    CHECK -->|Да| DONE_OK["return True"]
    CHECK -->|Нет| A1

    A1["Attempt 1: pair=False<br/>(reuse existing bond)"] --> A1_OK{"Handshake OK?"}
    A1_OK -->|Да| CONNECTED["Connected!"]
    A1_OK -->|Нет| A2

    A2["Attempt 2: pair=True<br/>(create new bond)"] --> A2_OK{"Handshake OK?"}
    A2_OK -->|Да| CONNECTED
    A2_OK -->|Нет| UNPAIR

    UNPAIR["_try_unpair()<br/>(clear stale bond)"] --> A3

    A3["Attempt 3: pair=True<br/>(fresh bond after unpair)"] --> A3_OK{"Handshake OK?"}
    A3_OK -->|Да| CONNECTED
    A3_OK -->|Нет| FAILED["return False"]

    CONNECTED --> FW["Read firmware version"]
    FW --> TYPE["Read machine type"]
    TYPE --> CB["Notify connection callbacks"]
    CB --> POST["_load_post_connect_data()<br/>(background task)"]
    POST --> DONE_OK
```

**Зачем 3 попытки?**
1. **pair=False** — быстрый путь: bond уже есть на ESP32/BlueZ, повторный pairing не нужен
2. **pair=True** — первый pairing или bond был потерян (e.g. ESP32 перезагрузился)
3. **unpair + pair=True** — stale bond: машина забыла нас, но ESP32/BlueZ ещё помнит старый bond

---

## 4. Протокол Melitta BLE

### Формат фрейма

```
┌─────┬──────────┬────────────┬─────────────┬──────────┬─────┐
│  S  │ Command  │ Key Prefix │   Payload   │ Checksum │  E  │
│0x53 │ 1-2 char │  2 bytes   │  N bytes    │  1 byte  │0x45 │
└─────┴──────────┴────────────┴─────────────┴──────────┴─────┘
          │              │                         │
          │              └─── RC4 encrypted ───────┘
          │                   (key_prefix + payload + checksum)
          └── plaintext (command bytes)
```

- **S** (`0x53`) — маркер начала фрейма
- **Command** — 1-2 ASCII символа (`HU`, `HX`, `HC`, `HJ`, `HE`, `HB`, `HR`, `HA`, `HV`, `HW`, `A`, `N`)
- **Key Prefix** — 2 байта, получены при handshake, включаются во все фреймы после handshake
- **Payload** — данные команды (переменная длина)
- **Checksum** — `~(sum(cmd_bytes + payload)) & 0xFF`
- **E** (`0x45`) — маркер конца фрейма
- Всё после command bytes и до E **шифруется RC4** (кроме A/N — ACK/NACK)

### Приём фрейма (_process_byte)

```mermaid
flowchart TD
    BYTE["Входной байт"] --> EMPTY{"Буфер пуст?"}
    EMPTY -->|Да| IS_S{"byte == 0x53 (S)?"}
    IS_S -->|Да| ADD_S["Добавить в буфер,<br/>start timer"]
    IS_S -->|Нет| SKIP["Игнорировать"]

    EMPTY -->|Нет| TIMEOUT{"Прошло > 1 сек<br/>с начала фрейма?"}
    TIMEOUT -->|Да| CLEAR["Очистить буфер"]
    CLEAR --> IS_S

    TIMEOUT -->|Нет| OVERFLOW{"Буфер >= 128?"}
    OVERFLOW -->|Да| CLEAR2["Очистить буфер"]
    OVERFLOW -->|Нет| ADD["Добавить байт"]

    ADD --> IS_E{"byte == 0x45 (E)<br/>и буфер >= 4?"}
    IS_E -->|Нет| WAIT["Ждём следующий байт"]
    IS_E -->|Да| MATCH{"Длина совпадает<br/>с KNOWN_COMMANDS?"}

    MATCH -->|Да| PARSE["_try_parse_frame()"]
    MATCH -->|Нет| WAIT

    PARSE --> DECRYPT{"Encrypted?"}
    DECRYPT -->|Да| RC4["RC4 decrypt"]
    RC4 --> CS["Verify checksum"]
    DECRYPT -->|Нет| CS_PLAIN["Verify checksum<br/>(plaintext)"]
    CS --> DISPATCH["_dispatch_frame()"]
    CS_PLAIN --> DISPATCH
```

**Ключевые особенности парсера:**

1. **S (0x53) внутри фрейма — это данные**, не новый фрейм. RC4-шифрование может генерировать байт 0x53 в ciphertext. Оригинальная реализация в Java (`Q3/q.java`) делает то же самое.

2. **E (0x45) как маркер конца проверяется через длину.** Поскольку 0x45 тоже может появиться в ciphertext, парсер сравнивает текущую длину буфера с ожидаемой длиной для каждой известной команды. Если длина не совпадает — байт считается данными.

3. **1-секундный таймаут** сбрасывает буфер при фрагментации (MTU = 20 байт, фрейм до ~70 байт = 4 BLE-пакета).

### Чанкинг для BLE

```python
def chunk_for_ble(self, frame: bytes) -> list[bytes]:
    """Split frame into 20-byte BLE MTU chunks."""
    return [frame[i:i+20] for i in range(0, len(frame), 20)]
```

Один фрейм (например, HJ write recipe = 73 байта) разбивается на 4 чанка по 20 + остаток:
```
Chunk 1: [20 bytes] → write_gatt_char(ad01)
Chunk 2: [20 bytes] → write_gatt_char(ad01)
Chunk 3: [20 bytes] → write_gatt_char(ad01)
Chunk 4: [13 bytes] → write_gatt_char(ad01)
```

---

## 5. Криптография

### Инициализация шифрования

```mermaid
flowchart LR
    BLOB["Hardcoded AES blob<br/>(в исходном коде)"] --> AES["AES-CBC decrypt<br/>(pycryptodome)"]
    AES --> RC4_KEY["RC4 Key<br/>(32 bytes)"]
    RC4_KEY --> ENCRYPT["RC4 encrypt<br/>(send frames)"]
    RC4_KEY --> DECRYPT["RC4 decrypt<br/>(receive frames)"]
```

1. **AES-CBC** расшифровывает захардкоженный blob → получаем RC4-ключ (32 байта)
2. **RC4** (симметричный потоковый шифр) используется для шифрования/дешифрования всех фреймов
3. Каждый фрейм шифруется **независимо** (RC4 state сбрасывается для каждого фрейма)

### Handshake (HU command)

```mermaid
sequenceDiagram
    participant APP as Integration
    participant MACH as Machine

    Note over APP: Generate 4 random bytes (challenge)
    Note over APP: Compute CRC over challenge
    APP->>MACH: HU frame: challenge(4) + crc(2)
    Note over MACH: Verify CRC
    Note over MACH: Generate key_prefix(2)
    MACH-->>APP: HU response: challenge(4) + key_prefix(2) + validation(2)
    Note over APP: Store key_prefix for all future frames
```

**Key Prefix** — 2 байта, которые машина присваивает сессии. Включаются во **все** последующие фреймы (команды и ответы). Без key_prefix машина отклоняет команды.

---

## 6. Команды протокола

### Таблица команд

| Команда | Направление | Encrypted | Payload size | Описание |
|---------|-------------|-----------|-------------|----------|
| `HU` | ↔ | Нет* | 6/8 bytes | Handshake challenge-response |
| `HX` | ← | Да | 8 bytes | Status (процесс, прогресс, alerts) |
| `HC` | ← | Да | 66 bytes | Read recipe response |
| `HJ` | → | Да | 66 bytes | Write recipe |
| `HE` | → | Да | 18 bytes | Start process (brew, clean, etc.) |
| `HB` | → | Да | 4 bytes | Cancel process |
| `HR` | ← | Да | 6 bytes | Read numerical value |
| `HW` | → | Да | 6 bytes | Write numerical value |
| `HA` | ↔ | Да | 66 bytes | Read/write alphanumeric value |
| `HV` | ← | Да | 11 bytes | Read firmware version |
| `A` | ← | Нет | 0 bytes | ACK |
| `N` | ← | Нет | 0 bytes | NACK |

*HU использует RC4 для key_prefix exchange, но сам challenge не шифруется.

### HX — Machine Status (push, каждые ~5 сек)

```
Payload (8 bytes):
  ┌──────────────┬──────────────┬───────────────┬──────────────┬──────────────┐
  │ process (2B) │sub_process(2)│info_messages(1)│manipulation(1)│ progress(2B)│
  │  big-endian  │  big-endian  │   bitmask     │    enum      │  big-endian  │
  └──────────────┴──────────────┴───────────────┴──────────────┴──────────────┘
```

- **process**: `MachineProcess` enum (STANDBY=0, READY=2, PRODUCT=3, CLEANING=4, ...)
- **sub_process**: `SubProcess` enum (IDLE=0, GRINDING=1, BREWING=2, MILK_FOAMING=3, ...)
- **info_messages**: bitmask (WATER_EMPTY=0x01, TRAY_FULL=0x02, BEAN_EMPTY=0x04, ...)
- **manipulation**: `Manipulation` enum (NONE=0, INSERT_TRAY=1, EMPTY_GROUNDS=2, ...)
- **progress**: 0-100 (процент завершения текущего процесса)

### HC — Read Recipe (response)

```
Payload (66 bytes, значимые 19):
  ┌─────────────┬─────────────┬──────────────────┬──────────────────┬──────────┐
  │recipe_id (2)│recipe_type(1)│  component1 (8)  │  component2 (8)  │padding(47)│
  │ big-endian  │    enum     │  RecipeComponent │  RecipeComponent │   zeros   │
  └─────────────┴─────────────┴──────────────────┴──────────────────┴──────────┘
```

**ВАЖНО:** В HC response **НЕТ recipe_key** (в отличие от HJ write)!

### HJ — Write Recipe (request)

```
Payload (66 bytes):
  ┌─────────────┬─────────────┬────────────┬──────────────────┬──────────────────┬──────────┐
  │recipe_id (2)│recipe_type(1)│recipe_key(1)│  component1 (8)  │  component2 (8)  │padding(46)│
  │ big-endian  │    enum     │    enum    │  RecipeComponent │  RecipeComponent │   zeros   │
  └─────────────┴─────────────┴────────────┴──────────────────┴──────────────────┴──────────┘
```

**recipe_key обязателен** и определяется по recipe_type через маппинг.

### RecipeComponent (8 bytes)

```
  ┌─────────┬───────┬───────┬───────────┬───────┬─────────────┬─────────┬─────────┐
  │process  │ shots │ blend │ intensity │ aroma │ temperature │portion  │reserve  │
  │ (1 byte)│(1)    │(1)    │ (1)       │(1)    │ (1)         │(1, ×5ml)│(1)      │
  └─────────┴───────┴───────┴───────────┴───────┴─────────────┴─────────┴─────────┘
```

### Brew Flow (полный цикл заваривания)

```mermaid
sequenceDiagram
    participant UI as HA Button
    participant CLIENT as MelittaBleClient
    participant PROTO as MelittaProtocol
    participant MACH as Machine

    UI->>CLIENT: brew_recipe(recipe_id)

    Note over CLIENT: Acquire _brew_lock

    CLIENT->>PROTO: read_status()
    PROTO->>MACH: HX request
    MACH-->>PROTO: HX: process=READY

    alt Machine not ready
        CLIENT-->>UI: raise RuntimeError
    end

    CLIENT->>PROTO: read_recipe(recipe_id)
    PROTO->>MACH: HC request
    MACH-->>PROTO: HC: recipe data (66B)

    CLIENT->>PROTO: write_recipe(TEMP_ID=400, recipe)
    PROTO->>MACH: HJ frame (66B payload)
    MACH-->>PROTO: ACK (A)

    Note over CLIENT: sleep(200ms)

    CLIENT->>PROTO: write_alphanumeric(FREESTYLE_NAME=401, name)
    PROTO->>MACH: HA frame (66B)
    MACH-->>PROTO: ACK (A)

    Note over CLIENT: sleep(200ms)

    CLIENT->>PROTO: start_process(PRODUCT)
    PROTO->>MACH: HE frame (18B)
    MACH-->>PROTO: ACK (A)

    Note over MACH: Machine starts brewing

    loop Status updates (push)
        MACH-->>PROTO: HX: process=PRODUCT, progress=0..100
        PROTO->>CLIENT: status_callback
        CLIENT->>UI: Entity state update
    end

    MACH-->>PROTO: HX: process=READY
    Note over UI: Brewing complete
```

---

## 7. Реконнект и отказоустойчивость

### Обнаружение отключения (два пути)

```mermaid
flowchart TD
    DISCONNECT["Машина выключена / BLE потеряно"]

    DISCONNECT --> PATH1["Путь 1: BLE disconnect callback"]
    DISCONNECT --> PATH2["Путь 2: Poll errors"]

    PATH1 --> ON_DISC["_on_disconnect()"]
    ON_DISC --> SET_FALSE1["_connected = False<br/>_client = None"]
    SET_FALSE1 --> CB1["connection_callbacks(False)"]
    CB1 --> SCHED1["_schedule_reconnect()"]

    PATH2 --> POLL["_poll_loop(): BleakError"]
    POLL --> COUNT["consecutive_errors++"]
    COUNT --> CHECK{"errors >= max?<br/>(default: 3)"}
    CHECK -->|Нет| CONTINUE["Продолжить polling"]
    CHECK -->|Да| FORCE["_safe_disconnect()<br/>_connected = False"]
    FORCE --> CB2["connection_callbacks(False)"]
    CB2 --> SCHED2["_schedule_reconnect()"]

    SCHED1 --> LOOP["_reconnect_loop()"]
    SCHED2 --> LOOP
```

**Путь 1** — быстрый: BLE-стек (через ESPHome proxy или BlueZ) детектирует разрыв соединения по BLE supervision timeout и вызывает `disconnected_callback`. Задержка: обычно 2-10 секунд.

**Путь 2** — fallback: если disconnect callback не сработал (например, при silent disconnect), poll loop накапливает ошибки и через `max_consecutive_errors` (по умолчанию 3) принудительно отключается.

### Reconnect Loop с exponential backoff

```mermaid
sequenceDiagram
    participant LOOP as _reconnect_loop
    participant EVENT as _reconnect_event
    participant BT as HA Bluetooth
    participant CLIENT as connect()

    Note over LOOP: delay = reconnect_delay (default: 5s)

    loop while _auto_reconnect and not connected
        LOOP->>EVENT: wait(timeout=delay)

        alt Advertisement arrives
            BT->>EVENT: set() [via set_ble_device()]
            EVENT-->>LOOP: woken up early!
            Note over LOOP: delay = reconnect_delay (reset)
        else Timeout
            Note over LOOP: normal backoff elapsed
        end

        LOOP->>CLIENT: connect()

        alt Success
            CLIENT-->>LOOP: True
            LOOP->>LOOP: start_polling()
            Note over LOOP: return (loop ends)
        else Failure
            CLIENT-->>LOOP: False / exception
            Note over LOOP: delay = min(delay×2, max_delay)
        end
    end
```

**Backoff progression:** 5s → 10s → 20s → 40s → 80s → 160s → 300s (max)

**Мгновенный reconnect по advertisement:** Когда машина включается и начинает BLE advertising, ESPHome proxy форвардит advertisement → HA вызывает `set_ble_device()` → `_reconnect_event.set()` будит reconnect loop → попытка подключения с минимальной задержкой.

> **HA API:** [`bluetooth.async_register_callback`](https://developers.home-assistant.io/docs/core/bluetooth/api/) — регистрирует callback на каждый BLE advertisement от устройства с указанным MAC-адресом.

### Защита от race conditions (locks)

| Lock | Защищает | Используется в |
|------|----------|----------------|
| `_connect_lock` | Одновременные попытки подключения | `connect()` |
| `_write_lock` | Параллельные BLE write | `_write_ble()` |
| `_brew_lock` | Параллельные brew commands | `brew_recipe()`, `brew_directkey()`, `brew_freestyle()` |
| `_lock` (protocol) | Параллельные send_and_wait | `send_and_wait_ack()`, `send_and_wait_response()` |

---

## 8. HA Entity-архитектура

### Entity-дерево

```mermaid
graph TD
    subgraph "Sensors"
        S1["MelittaStateSensor<br/>process + sub_process"]
        S2["MelittaActivitySensor<br/>human-readable activity"]
        S3["MelittaProgressSensor<br/>0-100%"]
        S4["MelittaActionRequiredSensor<br/>manipulation label"]
        S5["MelittaConnectionSensor<br/>connected / disconnected"]
        S6["MelittaFirmwareSensor<br/>firmware version"]
        S7["MelittaTotalCupsSensor<br/>cup counters"]
    end

    subgraph "Buttons"
        B1["MelittaBrewButton ×25<br/>per recipe (espresso, latte...)"]
        B2["MelittaBrewFreestyleButton<br/>custom recipe"]
        B3["MelittaCancelButton<br/>cancel current process"]
        B4["MelittaMaintenanceButton ×5<br/>clean, descale, filter..."]
    end

    subgraph "Selects"
        SEL1["Recipe Select<br/>active recipe"]
        SEL2["Profile Select<br/>active profile 0-8"]
        SEL3["Freestyle Process/Intensity<br/>Aroma/Temperature/Shots"]
    end

    subgraph "Switches"
        SW1["MelittaSettingSwitch ×N<br/>HR/HW based settings"]
        SW2["MelittaProfileActivitySwitch ×8<br/>profile enabled/disabled"]
    end

    subgraph "Numbers"
        N1["Portion Size Number<br/>freestyle ml"]
        N2["Setting Numbers<br/>language, clock, filter..."]
    end
```

### Подписка на обновления

```mermaid
sequenceDiagram
    participant ENTITY as Entity (sensor/button)
    participant CLIENT as MelittaBleClient
    participant PROTO as MelittaProtocol
    participant MACH as Machine

    Note over ENTITY: async_added_to_hass()
    ENTITY->>CLIENT: add_status_callback(self._on_status_update)
    ENTITY->>CLIENT: add_connection_callback(self._on_connection_change)

    Note over MACH: Machine sends HX status
    MACH-->>PROTO: HX notification
    PROTO->>CLIENT: _on_status(MachineStatus)
    CLIENT->>ENTITY: status_callback(status)
    ENTITY->>ENTITY: self.async_write_ha_state()

    Note over ENTITY: async_will_remove_from_hass()
    ENTITY->>CLIENT: remove_status_callback(...)
    ENTITY->>CLIENT: remove_connection_callback(...)
```

**Все entity используют push-модель** — не polling. Машина отправляет HX status каждые ~5 секунд через BLE notification. Entity подписываются на callbacks и обновляют своё состояние реактивно.

---

## 9. Потоки данных

### Полный цикл: от BLE notification до HA UI

```mermaid
flowchart LR
    MACH["Machine<br/>BLE notify"] -->|"raw bytes<br/>(20B chunks)"| ESP["ESPHome<br/>Proxy"]
    ESP -->|"aioesphomeapi"| BLEAK["BleakClient<br/>notification callback"]
    BLEAK -->|"bytearray"| ON_NOTIF["_on_notification()"]
    ON_NOTIF -->|"bytes"| ON_BLE["protocol.on_ble_data()"]
    ON_BLE -->|"per byte"| PROCESS["_process_byte()"]
    PROCESS -->|"complete frame"| PARSE["_try_parse_frame()"]
    PARSE -->|"RC4 decrypt<br/>+ checksum verify"| DISPATCH["_dispatch_frame()"]

    DISPATCH -->|"HX payload"| STATUS["MachineStatus.from_payload()"]
    STATUS -->|"callback"| SENSOR["Sensor entity"]
    SENSOR -->|"async_write_ha_state()"| HA_UI["HA Frontend"]

    DISPATCH -->|"A/N"| ACK["_ack_future.set_result()"]
    ACK -->|"unblock"| SEND_WAIT["send_and_wait_ack()"]

    DISPATCH -->|"HC/HR/HA/HV"| FUTURE["_frame_futures[cmd].set_result()"]
    FUTURE -->|"unblock"| SEND_RESP["send_and_wait_response()"]
```

### Параллельность и асинхронность

```mermaid
gantt
    title Async Task Lifecycle
    dateFormat X
    axisFormat %s

    section Background Tasks
    _async_connect_and_poll    :0, 5
    _load_post_connect_data    :5, 8
    _poll_loop                 :8, 100
    _reconnect_loop (if needed):50, 60

    section Locks
    _connect_lock held         :0, 5
    _write_lock (per BLE write):active, 8, 9
    _brew_lock (during brew)   :active, 20, 25
```

---

## 10. Найденные и исправленные проблемы (v0.23.2 — v0.23.3)

### Критические баги

#### Bug 1: Reconnect loop отменяет сам себя (v0.23.2)

```mermaid
sequenceDiagram
    participant LOOP as _reconnect_loop
    participant CONNECT as connect()
    participant IMPL as _connect_impl()

    Note over LOOP: _reconnect_task = this task
    LOOP->>CONNECT: await self.connect()
    CONNECT->>IMPL: await self._connect_impl()

    Note over IMPL: self._reconnect_task.cancel()
    Note over IMPL: ⚠️ Cancels ITSELF!

    IMPL-->>CONNECT: CancelledError at next await
    Note over LOOP: Task silently dies
    Note over LOOP: ❌ No reconnection ever happens
```

**Причина:** `_connect_impl()` содержал `self._reconnect_task.cancel()` для предотвращения дублирования. Но когда вызывался из `_reconnect_loop`, `_reconnect_task` указывал на текущий task.

**Исправление:** `asyncio.current_task() is not self._reconnect_task` guard.

#### Bug 2: Shared `_reconnect_event` race condition (v0.23.3)

```mermaid
sequenceDiagram
    participant INIT as _async_connect_and_poll
    participant BLE as BLE advertisement
    participant SET as set_ble_device()
    participant SCHED as _schedule_reconnect()
    participant RLOOP as _reconnect_loop

    Note over INIT: Waiting on _reconnect_event

    BLE->>SET: advertisement arrives
    SET->>SET: _reconnect_event.set()
    SET->>SCHED: _schedule_reconnect()
    SCHED->>RLOOP: create task

    Note over RLOOP: _reconnect_event.clear()
    Note over INIT: ⚠️ Event consumed by RLOOP!

    par Both try connect()
        INIT->>INIT: connect() — wins lock
        RLOOP->>RLOOP: connect() — waits for lock
    end

    Note over RLOOP: Gets True (already connected)
    RLOOP->>RLOOP: start_polling() — ⚠️ restarts poll!
```

**Причина:** Два цикла (`_async_connect_and_poll` и `_reconnect_loop`) слушали один `_reconnect_event`. `set_ble_device()` создавал дубликат reconnect loop.

**Исправление:** `set_ble_device()` не вызывает `_schedule_reconnect()` если reconnect task уже существует.

### Проблемы надёжности (v0.23.3)

| # | Проблема | Исправление |
|---|----------|-------------|
| 3 | `_load_post_connect_data` — fire-and-forget task, не отменяется при disconnect | Сохраняется в `_post_connect_task`, отменяется в `disconnect()` |
| 4 | `MelittaProtocol()` создавалась без `frame_timeout` (Options Flow игнорировался) | Передаём `frame_timeout=self._frame_timeout` |
| 5 | `write_alpha()` безусловно перезапускала polling | Добавлен `was_polling` guard |
| 6 | `send_and_wait_response()` — stale future при ошибке write | `finally: self._frame_futures.pop(command, None)` |
| 7 | Cup counter refresh конфликтовал с brew sequence | Проверка `self._brew_lock.locked()` перед запуском |

---

## 11. Ссылки на HA API

### Используемые HA Bluetooth API

| API | Где | Для чего |
|-----|-----|----------|
| [`bluetooth.async_register_callback`](https://developers.home-assistant.io/docs/core/bluetooth/api/) | `__init__.py:139` | Получение fresh `BLEDevice` при каждом advertisement |
| [`bluetooth.async_ble_device_from_address`](https://developers.home-assistant.io/docs/core/bluetooth/api/) | `__init__.py:107` | Initial `BLEDevice` из кэша при setup |
| [`bluetooth.BluetoothScanningMode.ACTIVE`](https://developers.home-assistant.io/docs/core/bluetooth/api/) | `__init__.py:148` | Активное BLE сканирование через proxy |
| [`entry.async_on_unload`](https://developers.home-assistant.io/docs/config_entries_index/) | `__init__.py:150` | Автоматическая отмена callback при unload |
| [`hass.async_create_task`](https://developers.home-assistant.io/docs/asyncio_index/) | `__init__.py:170` | Background connect без блокировки setup |
| [`ConfigEntry.runtime_data`](https://developers.home-assistant.io/docs/config_entries_index/) | `__init__.py:160` | Хранение клиента в runtime данных entry |

### Используемые библиотеки

| Библиотека | Версия | Для чего |
|-----------|--------|----------|
| [`bleak`](https://bleak.readthedocs.io/) | ≥ 0.21.0 | GATT read/write/notify, BLE connection |
| [`bleak-retry-connector`](https://github.com/Bluetooth-Devices/bleak-retry-connector) | ≥ 3.0.0 | `establish_connection()`, service cache, retry |
| [`pycryptodome`](https://www.pycryptodome.org/) | ≥ 3.0.0 | AES-CBC для derivation RC4-ключа |

### Конфигурируемые параметры (Options Flow)

| Параметр | Default | Описание |
|----------|---------|----------|
| `poll_interval` | 5.0s | Интервал polling HX status |
| `reconnect_delay` | 5.0s | Начальная задержка reconnect |
| `reconnect_max_delay` | 300s | Максимальная задержка reconnect |
| `max_consecutive_errors` | 3 | Poll errors до forced disconnect |
| `ble_connect_timeout` | 30s | Таймаут BLE connect |
| `frame_timeout` | 5s | Таймаут ожидания ответа на команду |
| `initial_connect_delay` | 2.0s | Задержка перед первым connect |
| `recipe_retries` | 2 | Retry для read/write recipe |

---

*Документация актуальна для версии v0.23.3. Последнее обновление: 2026-03-19.*
