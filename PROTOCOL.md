# Melitta Barista TS Smart — BLE Protocol Documentation

Reverse-engineered BLE protocol for Melitta Barista TS Smart coffee machines.

## BLE GATT

| Parameter | Value |
|---|---|
| **Service UUID** | `0000ad00-b35c-11e4-9813-0002a5d5c51b` |
| **Notify Characteristic** | `0000ad02-b35c-11e4-9813-0002a5d5c51b` |
| **Write Characteristic** | `0000ad01-b35c-11e4-9813-0002a5d5c51b` |
| **Max Write Size (MTU)** | 20 bytes (frames > 20 bytes must be chunked) |
| **BLE Library (app)** | SweetBlue (com.idevicesinc.sweetblue) |
| **Device Name Pattern** | Starts with `8604` (e.g. `860400E250429374203-`) |

## Connection Flow

```
BLE Connect → Subscribe ad02 → HU Handshake → Poll HX (status)
```

1. **Bond** device (Numeric Comparison — requires `DisplayYesNo` agent capability)
2. **Connect** via BLE GATT
3. **Subscribe** to notifications on `ad02`
4. **HU Handshake** — app-initiated challenge-response, provides `key_prefix` for subsequent frames
5. **HV** — read firmware version
6. **HX** — poll status every 1–5 seconds

### Bonding

The machine uses **Numeric Comparison** pairing (not Just Works). The BLE agent must support `DisplayYesNo` capability and auto-confirm the passkey in the `RequestConfirmation` D-Bus method.

Standard `NoInputNoOutput` agents will fail with `device_confirm_passkey: Operation not permitted`.

## HU Handshake (Challenge-Response)

The handshake is **app-initiated** — the machine will not communicate until it receives the HU challenge.

### Flow

1. App generates 4 random bytes (`challenge`)
2. App computes 2-byte CRC of the challenge using `_CRC_TABLE` (from `Q3/r.java`)
3. App sends HU frame: `challenge(4) + crc(2)` = 6 bytes payload (NOT encrypted with key_prefix)
4. Machine responds with HU frame: `challenge_echo(4) + key_prefix(2) + validation(2)` = 8 bytes
5. App extracts `key_prefix` (bytes 4–5) and uses it in all subsequent encrypted frames

### CRC Computation (from Q3/r.java)

```python
def compute_handshake_crc(length: int, data: bytes) -> bytes:
    # Byte 1: CRC with initial offset 0
    b5 = CRC_TABLE[(data[0] + 256) % 256]
    for i in range(1, length):
        b5 = CRC_TABLE[((b5 ^ data[i]) + 256) % 256]
    byte1 = (b5 + 93) & 0xFF

    # Byte 2: CRC with initial offset 1
    b7 = CRC_TABLE[(data[0] + 257) % 256]
    for i in range(1, length):
        b7 = CRC_TABLE[((b7 ^ data[i]) + 256) % 256]
    byte2 = (b7 + 167) & 0xFF

    return bytes([byte1, byte2])
```

## Frame Format

### Outgoing (pre-encryption)

```
S (0x53) | Command (1-2 bytes ASCII) | [key_prefix (2)] | [Payload] | Checksum (1) | E (0x45)
```

### Encryption

After assembling the frame, everything between the command bytes and `E` is RC4-encrypted:

```
S | Command | RC4_ENCRYPT(key_prefix + payload + checksum) | E
```

- `key_prefix` is included in encrypted frames after handshake is complete
- HU handshake frame does NOT include `key_prefix` (it hasn't been established yet)
- ACK (`A`) and NACK (`N`) frames are **NOT encrypted** — sent as plaintext: `S + A/N + checksum + E`

### Checksum Calculation

Computed **before** encryption, over all bytes from index 1 to end (inclusive), with a zero placeholder for the checksum byte itself:

```python
def calculate_checksum(frame_bytes: bytes, length: int) -> int:
    s = 0
    for i in range(1, length):
        s = (s + frame_bytes[i]) & 0xFF
    return (~s) & 0xFF
```

### Frame Timeout

3 seconds for ACK/response. Original app also has a 1-second receive timeout per frame (cancels incomplete frame collection).

### Frame Parsing Algorithm (from `Q3/q.java`)

The frame parser processes incoming BLE notifications **byte by byte**:

1. **Empty buffer**: Only `S` (0x53) starts frame collection. All other bytes are ignored.
2. **Collecting**: Each byte is appended to a 128-byte buffer.
   - `S` (0x53) **inside** a frame is treated as regular data (RC4 ciphertext can contain any byte value).
   - Buffer overflow at 128 bytes resets the buffer.
3. **`E` (0x45) received** with buffer ≥ 4 bytes: Check if the buffer length matches any registered command:
   - Extract 1-char and 2-char command from `buffer[1:2]` / `buffer[1:3]`
   - For each matching registered command: `expected = 1(S) + cmd_len + payload_size + 1(checksum) + 1(E)`
   - If `expected == buffer_length`: decrypt, verify checksum, accept frame, clear buffer
   - If **no match**: `E` was in encrypted data — **continue collecting** (do NOT clear buffer)
4. **Checksum verification** (after RC4 decryption): `~(sum of bytes[1..N-2]) & 0xFF == 0`

**Critical insight**: Both `S` (0x53) and `E` (0x45) can appear inside RC4-encrypted data.
The parser disambiguates by checking the total frame length against known command sizes.
Only when `E` arrives at exactly the right position is the frame considered complete.

## Encryption: RC4

### Key Derivation

The RC4 key is derived from a hardcoded AES-encrypted blob:

1. **AES Key** (32 bytes) = `AES_KEY_PART_B (17 bytes)` || `AES_KEY_PART_A (15 bytes)`:
   - Source: `Q3/g.java` fields `f2316b` + `f2315a`

2. **IV** (16 bytes) from `C0390b` constructor (`f5091c`)

3. **Encrypted blob** (48 bytes) = `AbstractC0940a.f9048a` (NOT `f9049b` which is the AES key for anti-tampering)

4. **Decrypt**: `AES/CBC/PKCS5Padding` → strip PKCS5 padding → **32-byte RC4 key**

Derived key (ASCII): `MEL_090217_V10_?R4.wozJ!(*q2ds3#`

### RC4 Stream Cipher

Standard RC4: KSA with derived key, then PRGA XOR on each byte.

**Important**: RC4 is a stream cipher with internal state. Each frame uses a **fresh RC4 instance** — the key schedule is reset per frame (no persistent state across frames).

## Commands

### Outgoing (App → Machine)

| Command | Description | Payload Size | Encrypted |
|---|---|---|---|
| `HU` | Handshake challenge | 6 bytes | Yes (no key_prefix) |
| `HA` | Read alphanumeric value | 2 bytes | Yes |
| `HB` | Write alphanumeric value | 66 bytes | Yes |
| `HC` | Read recipe | 2 bytes | Yes |
| `HE` | Start process | 18 bytes | Yes |
| `HJ` | Write recipe | 66 bytes | Yes |
| `HR` | Read numerical value | 2 bytes | Yes |
| `HV` | Read firmware version | 0 bytes | Yes |
| `HW` | Write numerical value | 6 bytes | Yes |
| `HX` | Read machine status | 0 bytes | Yes |
| `HZ` | Cancel process | 4 bytes | Yes |

### Incoming (Machine → App)

Payload sizes from protocol analysis.
These are the sizes the frame parser uses to detect frame boundaries.

| Command | Description | Payload Size | Encrypted | Used Data |
|---|---|---|---|---|
| `A` | ACK (success) | 0 bytes | No | — |
| `N` | NACK (failure) | 0 bytes | No | — |
| `HU` | Handshake response | 8 bytes | Yes | 8 bytes |
| `HA` | Alphanumeric value | 66 bytes | Yes | 66 bytes |
| `HC` | Recipe data | 66 bytes | Yes | 19 bytes (rest padding) |
| `HF` | Unknown | 16 bytes | Yes | — |
| `HL` | Unknown | 20 bytes | Yes | — |
| `HP` | Unknown | 14 bytes | Yes | — |
| `HQ` | Unknown | 15 bytes | Yes | — |
| `HR` | Numerical value | 6 bytes | Yes | 6 bytes |
| `HV` | Firmware version | 11 bytes | Yes | 11 bytes |
| `HX` | Machine status | 8 bytes | Yes | 8 bytes |

> **IMPORTANT — HC vs HJ format difference:**
>
> **HC response** (read recipe) payload: `recipe_id(2) + recipe_type(1) + comp1(8) + comp2(8)` = 19 bytes.
> **No recipe_key byte!**
>
> **HJ request** (write recipe) payload: `recipe_id(2) + recipe_type(1) + recipe_key(1) + comp1(8) + comp2(8)` = 20+ bytes.
> **recipe_key is mandatory** and must match the RecipeType→RecipeKey mapping below.
>
> HB, HE, HJ, HW, HZ are **write-only** — machine responds with `A`/`N`, not with the same command.

## Brewing Protocol

Brewing a recipe requires a **3-step sequence**. Sending HE alone will be ACK'd but **not execute**.

### Sequence (from `G3/n.java`)

```
HC (read recipe) → HJ (write to temp slot 400) → HB (write name, id=401) → HE (start process)
```

### Step 1: Read Recipe (HC)

Read the built-in recipe parameters from the machine.

- **Request payload**: `struct.pack(">h", recipe_id)` (e.g., 200 for Espresso)
- **Response payload** (66 bytes total, 19 significant):

| Offset | Size | Field |
|---|---|---|
| 0 | 2 | recipe_id (big-endian short) |
| 2 | 1 | recipe_type |
| 3 | 8 | component1 |
| 11 | 8 | component2 |
| 19 | 47 | padding (zeros) |

> **No recipe_key in HC response!** The HJ write format has recipe_key at offset 3,
> but the HC read response goes directly from recipe_type to component1.

### Step 2: Write Recipe to Temp Slot (HJ)

Write the recipe to temporary slot **400** (`FreestyleConstants.TEMP_RECIPE`).

- **Payload**: 66 bytes

| Offset | Size | Field | Value |
|---|---|---|---|
| 0 | 2 | recipe_id | 400 (0x0190) — temp slot |
| 2 | 1 | recipe_type | See RecipeType enum |
| 3 | 1 | recipe_key | See RecipeKey enum |
| 4 | 8 | component1 | RecipeComponent bytes |
| 12 | 8 | component2 | RecipeComponent bytes |
| 20 | 8 | component3 | Optional, usually zeros |
| 28 | 38 | padding | Zeros |

### Step 3: Write Recipe Name (HB)

Write the display name to value ID **401** (`FreestyleConstants.FREESTYLE_NAME`).

- **Payload**: `struct.pack(">h", 401) + name_utf8.ljust(64, b"\x00")` = 66 bytes

### Step 4: Start Process (HE)

Start the brewing process.

- **Payload**: 18 bytes

| Offset | Size | Field | Value |
|---|---|---|---|
| 0 | 2 | process_type | 4 (PRODUCT) |
| 2 | 2 | fixed_value | 2 (0x0002) |
| 4 | 2 | zeros | 0 |
| 6 | 2 | milk_flag | 0 or 1 (for milk-based drinks) |
| 8 | 8 | padding | Zeros |

### Delays

The app inserts **200ms** delays between each step (HJ → HB → HE).

### Verified Example: Espresso

```
HC  recipe_id=200 → comp1: process=1 shots=1 blend=1 intensity=3 aroma=0 temp=2 portion=8(40ml)
HJ  slot=400, type=0, key=0, comp1=0101010300020800, comp2=0000000000020000
HB  id=401, name="Espresso"
HE  process=4, data=00020000000000000000000000000000

Timeline: Grinding (sub=1) 0→9% → Coffee (sub=2) 9→100% → Ready (~48s total)
```

## RecipeType 

| Name | Value |
|---|---|
| ESPRESSO | 0 |
| RISTRETTO | 1 |
| LUNGO | 2 |
| ESPRESSO_DOPIO | 3 |
| RISETTO_DOPIO | 4 |
| CAFE_CREME | 5 |
| CAFE_CREME_DOPIO | 6 |
| AMERICANO | 7 |
| AMERICANO_EXTRA | 8 |
| LONG_BLACK | 9 |
| RED_EYE | 10 |
| BLACK_EYE | 11 |
| DEAD_EYE | 12 |
| CAPPUCCINO | 13 |
| ESPR_MACCHIATO | 14 |
| CAFFE_LATTE | 15 |
| CAFE_AU_LAIT | 16 |
| FLAT_WHITE | 17 |
| LATTE_MACCHIATO | 18 |
| LATTE_MACCHIATO_EXTRA | 19 |
| LATTE_MACCHIATO_TRIPLE | 20 |
| MILK | 21 |
| MILK_FROTH | 22 |
| WATER | 23 |
| FREESTYLE | 24 |

## RecipeKey 

| Name | Value |
|---|---|
| ESPRESSO | 0 |
| COFFEE | 1 |
| CAPPUCCINO | 2 |
| MACCHIATO | 3 |
| MILK_FROTH | 4 |
| MILK | 5 |
| WATER | 6 |
| MENU | 7 |

### RecipeType → RecipeKey Mapping (from `E3/Z.java`)

Each RecipeType must be paired with the correct RecipeKey when writing via HJ:

| RecipeType | Name | RecipeKey |
|---|---|---|
| 0–4 | Espresso, Ristretto, Lungo, Dopio variants | ESPRESSO (0) |
| 5–9 | Café Crème, Americano, Long Black | COFFEE (1) |
| 10–12 | Red Eye, Black Eye, Dead Eye | COFFEE (1) |
| 13 | Cappuccino | CAPPUCCINO (2) |
| 14 | Espresso Macchiato | CAPPUCCINO (2) |
| 15 | Caffè Latte | CAPPUCCINO (2) |
| 16 | Café au Lait | CAPPUCCINO (2) |
| 17 | Flat White | CAPPUCCINO (2) |
| 18–20 | Latte Macchiato, Extra, Triple | MACCHIATO (3) |
| 21 | Milk | MILK (5) |
| 22 | Milk Froth | MILK_FROTH (4) |
| 23 | Water | WATER (6) |
| 24 | Freestyle | MENU (7) |

> **Note**: Espresso Macchiato (14) maps to CAPPUCCINO (2), **not** MACCHIATO (3).
> MACCHIATO key is only for Latte Macchiato variants (18–20).

## Status (HX) Payload — 8 bytes

| Offset | Size | Field | Format |
|---|---|---|---|
| 0 | 2 | process | big-endian short |
| 2 | 2 | sub_process | big-endian short |
| 4 | 1 | info_messages | bitfield |
| 5 | 1 | manipulation | enum |
| 6 | 2 | progress | big-endian short (0–100%) |

### Process

| Name | Value | Description |
|---|---|---|
| READY | 2 | Machine idle, ready for commands |
| PRODUCT | 4 | Making a drink |
| CLEANING | 9 | Cleaning cycle |
| DESCALING | 10 | Descaling |
| FILTER_INSERT | 11 | Insert filter |
| FILTER_REPLACE | 12 | Replace filter |
| FILTER_REMOVE | 13 | Remove filter |
| SWITCH_OFF | 16 | Switching off |
| EASY_CLEAN | 17 | Easy clean |
| INTENSIVE_CLEAN | 19 | Intensive clean |
| EVAPORATING | 20 | Evaporating |
| BUSY | 99 | Busy |

### SubProcess

| Name | Value | Description |
|---|---|---|
| GRINDING | 1 | Grinding beans |
| COFFEE | 2 | Extracting coffee |
| STEAM | 3 | Steaming milk |
| WATER | 4 | Dispensing water |
| PREPARE | 5 | Preparing |

### InfoMessage (bitfield)

| Bit | Name |
|---|---|
| 0 | FILL_BEANS_1 |
| 1 | FILL_BEANS_2 |
| 2 | EASY_CLEAN |
| 3 | POWDER_FILLED |
| 4 | PREPARATION_CANCELLED |

### Manipulation

| Name | Value | Description |
|---|---|---|
| NONE | 0 | No action needed |
| BU_REMOVED | 1 | Brew unit removed |
| TRAYS_MISSING | 2 | Drip trays missing |
| EMPTY_TRAYS | 3 | Empty the trays |
| FILL_WATER | 4 | Fill water tank |
| CLOSE_POWDER_LID | 5 | Close powder lid |
| FILL_POWDER | 6 | Fill powder |

## Recipe IDs (Built-in)

| Name | ID |
|---|---|
| ESPRESSO | 200 |
| RISTRETTO | 201 |
| LUNGO | 202 |
| ESPRESSO_DOPIO | 203 |
| RISETTO_DOPIO | 204 |
| CAFE_CREME | 205 |
| CAFE_CREME_DOPIO | 206 |
| AMERICANO | 207 |
| AMERICANO_EXTRA | 208 |
| LONG_BLACK | 209 |
| RED_EYE | 210 |
| BLACK_EYE | 211 |
| DEAD_EYE | 212 |
| CAPPUCCINO | 213 |
| ESPR_MACCHIATO | 214 |
| CAFFE_LATTE | 215 |
| CAFE_AU_LAIT | 216 |
| FLAT_WHITE | 217 |
| LATTE_MACCHIATO | 218 |
| LATTE_MACCHIATO_EXTRA | 219 |
| LATTE_MACCHIATO_TRIPLE | 220 |
| MILK | 221 |
| MILK_FROTH | 222 |
| WATER | 223 |

### Special IDs

| Name | ID | Description |
|---|---|---|
| TEMP_RECIPE | 400 | Temporary slot used for brewing |
| FREESTYLE_NAME | 401 | Alphanumeric value ID for recipe name |

## RecipeComponent — 8 bytes

| Offset | Field | Type | Values |
|---|---|---|---|
| 0 | process | byte | NONE=0, COFFEE=1, STEAM=2, WATER=3 |
| 1 | shots | byte | NONE=0, ONE=1, TWO=2, THREE=3 |
| 2 | blend | byte | BARISTA_T=0, BLEND_1=1, BLEND_2=2 |
| 3 | intensity | byte | VERY_MILD=0, MILD=1, MEDIUM=2, STRONG=3, VERY_STRONG=4 |
| 4 | aroma | byte | STANDARD=0, INTENSE=1 |
| 5 | temperature | byte | COLD=0, NORMAL=1, HIGH=2 |
| 6 | portion | byte | value × 5 = ml (e.g., 8 = 40ml) |
| 7 | reserve | byte | Always 0 |

## Machine Settings (Numerical Values HR/HW)

| Name | ID | Notes |
|---|---|---|
| WATER_HARDNESS | 11 | |
| ENERGY_SAVING | 12 | |
| AUTO_OFF_AFTER | 13 | Minutes |
| AUTO_OFF_WHEN | 14 | |
| LANGUAGE | 15 | |
| AUTO_BEAN_SELECT | 16 | |
| RINSING_OFF | 18 | |
| CLOCK | 20 | |
| CLOCK_SEND | 21 | |
| TEMPERATURE | 22 | |
| FILTER | 91 | |

## Notes

- All multi-byte integers are **big-endian**
- Write operations (HB, HJ, HW, HE, HZ) require ACK — `A` = success, `N` = failure
- Read operations (HA, HC, HR, HV, HX) return data in the corresponding frame type
- RC4 key is per-frame (fresh KSA each time), not a persistent stream
- Frames exceeding 20 bytes BLE MTU must be split into chunks
- `key_prefix` changes on every new connection (obtained from HU handshake)
- The machine uses a random BLE address (not static MAC)
- Verified firmware: `02590029014` (model EF-BTLE, FW: EF_1.00R4__386)
