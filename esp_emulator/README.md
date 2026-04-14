# Nivona BLE Emulator

A BLE peripheral emulator that impersonates a Nivona NICR/NIVO coffee machine
on ESP32, for offline development of the Home Assistant `melitta_barista`
integration without access to a real machine. Also works with the official
Nivona Android app for protocol reverse-engineering.

Implements the full Eugster BLE protocol (service `AD00`) — HU handshake,
RC4 frame encryption, all documented `H*` commands, per-family recipe
layouts, and a finite-state machine that simulates brewing cycles.

## Hardware

Primary target: **Seeed XIAO ESP32-C6** (onboard PCB antenna or external
U.FL — GPIO14 switches between them, see `main/main.c::xiao_c6_rf_init`).

Also builds for **Seeed XIAO ESP32-S3 / S3 Plus** with minor changes to
`sdkconfig.defaults` (target + PSRAM config).

## Features

| Layer          | Implementation status |
| -------------- | --------------------- |
| Advertising    | Random static address (F1:…), mfg data `0x0D` + customer filter |
| DIS (`180A`)   | Manufacturer / model / serial / hw / fw / sw revisions          |
| GATT `AD00`    | AD01 (control), AD02 (notify), AD03 (write), AD04/5 (stub), AD06 (name) |
| Security       | Just Works pairing + bonding (NVS-persistent)                   |
| Framing        | `S + cmd + [kp] + payload + cs + E`, RC4, MTU chunking, per-cmd size gating |
| HU handshake   | Full 2-round verifier + session key negotiation                 |
| Commands       | HX (status), HV, HL, HI, HS, HR/HW, HA/HB, HE (brew), HZ, HY, HD, HN, Hp, HC/HJ (stub) |
| FSM            | process / sub_process / info / manipulation / progress          |
| Brew cycle     | READY → PRODUCT, progress 0→100%, async unsolicited HX pushes   |
| Storage        | Numerical + alphanumeric registers persisted in NVS             |
| Family switch  | CLI `family` command selects 600/700/79x/900/900-light/1030/1040/8000 |

## Prerequisites

- **ESP-IDF 5.4+** — tested with v5.4.1 RISC-V toolchain
- Host serial access to the board (native USB Serial/JTAG)
- WiFi network reachable by the board (for OTA + telnet)

## Setup

```bash
# 1. Set up WiFi credentials (one-time, never committed)
cp main/wifi_secrets.h.template main/wifi_secrets.h
$EDITOR main/wifi_secrets.h    # fill WIFI_SSID / WIFI_PASS

# 2. Configure ESP-IDF
. $IDF_PATH/export.sh
idf.py set-target esp32c6      # or esp32s3 — edit sdkconfig.defaults accordingly

# 3. Build and flash
idf.py build
idf.py -p /dev/ttyACM0 flash monitor
```

After the first USB flash, all subsequent updates go over the air:
```bash
curl -X POST --data-binary @build/nivona_emulator.bin \
     http://nivona-emu.local/ota
```

## Runtime endpoints

| Endpoint                        | Purpose                                                     |
| ------------------------------- | ----------------------------------------------------------- |
| `GET  http://<host>/`           | Firmware version, IDF version, compile time                 |
| `GET  http://<host>/diag`       | JSON of all diagnostic counters (connects, frames, HU, HX, …) |
| `POST http://<host>/ota`        | Flash a new `nivona_emulator.bin` — body is the raw binary  |
| `POST http://<host>/reboot`     | Reboot the device                                           |
| `telnet <host> 23`              | Interactive CLI — see commands below                        |

mDNS: the board announces itself as `MDNS_HOSTNAME.local` (default
`nivona-emu.local`), so you can use that instead of an IP.

## CLI commands (telnet or USB console)

```
help                     list registered commands
status                   show FSM state (process, sub_process, manip, progress)
diag                     show diagnostic counters
brew <pv>                start a brew cycle with the given process value
cancel                   cancel active brew
trigger <m>              set a manipulation (water_empty / beans_empty /
                         tray_full / clean / descale / none)
dump                     dump persisted register store
family <key>             switch emulated family — 600 / 700 / 79x / 900 /
                         900-light / 1030 / 1040 / 8000. Takes effect on reboot.
pair                     enter pairing mode (wipes bonds, restarts advertising)
forget                   wipe stored BLE bonds
reboot                   reboot the device
```

## Testing

Python tests in `tests/test_emulator.py` cover protocol helpers, HTTP
diagnostics, and full BLE round-trips. Run against a live board:

```bash
pip install bleak pytest pytest-asyncio requests
EMU_IP=192.168.1.29 EMU_MAC=F1:32:04:33:52:DA python3 tests/test_emulator.py
# or
python3 -m pytest tests/ -v -s
```

## Architecture

```
main/
├── main.c              application entry: lifecycle, RF switch, SM config
├── nivona_ble.c/h      advertising, GAP events, scan response
├── nivona_gatt.c/h     GATT service AD00 with 6 characteristics
├── nivona_dis.c/h      Device Information Service (180A)
├── nivona_frame.c/h    framing (S/E, checksum, RC4, per-cmd size gating)
├── nivona_crypto.c/h   RC4 + HU verifier + HU lookup table
├── nivona_dispatch.c/h command router with HU / HX / HV / HL / HI / HR / HW / …
├── nivona_fsm.c/h      process state machine (thread-safe)
├── nivona_store.c/h    numerical + alphanumeric register store with NVS
├── nivona_brew.c/h     async brew cycle task, unsolicited HX pushes
├── nivona_cli.c/h      esp_console REPL (USB Serial/JTAG)
├── nivona_wifi.c/h     WiFi STA + mDNS
├── nivona_ota.c/h      HTTP server (status / diag / ota / reboot)
└── nivona_telnet.c/h   TCP:23 bridge to esp_console + log mirroring
```

## Protocol references

- Upstream reverse engineering (field notes + reference client):
  `https://github.com/mpapierski/esp-coffee-bridge` — especially `docs/NIVONA.md`
- HA integration — source of truth for protocol constants:
  `../custom_components/melitta_barista/protocol.py`
  `../custom_components/melitta_barista/brands/nivona.py`

## Security notes

- `main/wifi_secrets.h` is `.gitignore`-d. Never commit it.
- The emulator accepts any pairing request (Just Works). Don't expose it
  on an untrusted network.
- OTA has no authentication. Keep port 80 on a trusted LAN.

## License

Same as the parent `melitta-ha-integration` project.
