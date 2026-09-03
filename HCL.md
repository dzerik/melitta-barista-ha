# Hardware Compatibility List

> Living document. Confirmed by the maintainer (✅), the community (👥), or
> known-broken (❌). If you have a working — or broken — combo not listed
> below, please open a bug report (the template asks for adapter type + model)
> and we'll keep this table fresh.

There are **two BLE transport topologies** to choose from. Read the
[README's BLE topology section](README.md#ble-topology--strongly-prefer-an-esphome-proxy)
before opening a bug report — the dev / test coverage gap matters.

---

## 1. BLE transport (host → machine)

### 1.1 ESPHome BLE proxy 🟢 **recommended**

A second-hand or $10 new ESP32 board flashed with ESPHome's
[`bluetooth_proxy`](https://esphome.io/projects/?type=bluetooth) component sits
near the coffee machine and bridges its BLE traffic over Wi-Fi to Home
Assistant's `bluetooth` integration. This is the **primary tested path** in
this repository — the maintainer's development setup, the BLE-proxy `pair=True`
delegation, and the protocol-level retry logic all live and die against this
topology day-to-day.

| Board | Chipset | Status | Tested by | Notes |
|---|---|---|---|---|
| **Seeed XIAO ESP32-S3** | ESP32-S3 (dual-core, BLE 5.0) | ✅ **reference** | dzerik (daily driver) | Repo ships [`esphome/ble-proxy-xiao-s3.yaml`](esphome/ble-proxy-xiao-s3.yaml). The maintainer's live proxy — every release is dogfooded against it. Dual-core: Wi-Fi and BLE don't compete for CPU. Field-verified rescue for a struggling WROOM-32D setup (#10): RSSI improved from −76…−88 to −56…−62 dBm at the same spot. |
| **Seeed XIAO ESP32-C6** | ESP32-C6 (single-core, BLE 5.3, Wi-Fi 6) | ✅ maintainer-tested | dzerik | [`esphome/ble-proxy-xiao-c6.yaml`](esphome/ble-proxy-xiao-c6.yaml). Solid, but the C6 BLE controller can wedge into a `status=133` / HCI `0x2043 Cmd Disallowed` connect loop after an aborted session ([esphome#17856](https://github.com/esphome/esphome/issues/17856)) — the config ships an `esphome.<proxy>_restart_ble` action that clears it without a reboot. |
| **ESP32-C6-DevKitC-1 + clones** (QIQIAZI, WeAct, …) | ESP32-C6-WROOM-1 | 👥 field-confirmed (#35) | community | Use [`esphome/ble-proxy-c6-devkit.yaml`](esphome/ble-proxy-c6-devkit.yaml) — correct 4 MB flash declaration, no XIAO-specific RF-switch stanzas. Same C6 controller caveat as above. |
| **Classic ESP32-WROOM-32 / 32D / 32U boards** | ESP32 (single-core radio path, BLE 4.2) | ⚠️ **not recommended** | field case (#10) | Field-verified failure mode: at **under 1 m** from the machine — RSSI −76…−88 dBm, constant HCI `0x3e` "connection failed to establish" loops, multi-minute pairing timeouts, and the ESP itself dropping off Wi-Fi. One shared 2.4 GHz radio + typically weak PCB antennas. If you must use one: `wifi: power_save_mode: NONE` and a solid 5 V/1 A+ supply are **mandatory** — and may still not be enough; swapping to a XIAO ESP32-S3 resolved the field case immediately. |
| **M5Stack ATOM Lite** | ESP32-PICO-D4 (BLE 4.2) | ⚠️ same class as WROOM-32 | — | Classic-ESP32 radio; the WROOM-32 caveats apply. Fine as an advertisement-only relay, risky as the machine's active proxy. |

**Why this topology is preferred for triage:**

- `pair=True` from `bleak-retry-connector` delegates to the ESP32's
  Bluedroid/NimBLE stack, which sidesteps every BlueZ pairing quirk
  (`No agent available`, `Authentication failed`, `bluetoothd` SEGFAULTs in
  headless Linux setups).
- The proxy's BLE state is recoverable through a single OTA reflash, never
  through fighting D-Bus.
- Repeatable across operating systems — HA OS, Container, Supervised, Core,
  bare metal — because the BLE stack runs on the ESP, not the host.

#### Required / used by this integration

Stock `bluetooth_proxy` covers the happy path, but
[`esphome/ble-proxy-xiao-c6.yaml`](esphome/ble-proxy-xiao-c6.yaml) ships
**four integration-specific extras** that the `melitta_barista` code path
explicitly calls. Bring-your-own YAML works only if you mirror them — the
template is the source of truth:

| Knob | Required by | Why |
|---|---|---|
| `wifi: { power_save_mode: NONE }` | every connection | **The most-missed line in bring-your-own configs.** ESPHome's ESP32 default is light sleep, which causes exactly the failure trio seen in the field (#10): HCI `0x3e` connection-establish loops, multi-minute pairing timeouts, and the ESP intermittently dropping off Wi-Fi. |
| `bluetooth_proxy: { active: true }` | every brew + handshake | Stock ESPHome defaults to passive ad relay only. We need GATT writes (recipe HJ frames, freestyle brew, settings) — these flow through `active: true`. Without it the handshake never even starts. |
| `bluetooth_proxy: { connection_slots: 3 }` | every connection | Since ESPHome 2026.5 only `bluetooth_proxy` consumes connection slots (the scanner's ADV/SCAN instance is added by codegen on top), so the `esp32_ble` default `max_connections` already matches — no explicit `esp32_ble` block needed. Don't over-allocate on RAM-tight single-core chips. |
| Custom `api.actions: clear_ble_bonds` | "Hard Repair" / `force_pair_full` repair flow | When the ESP keeps a stale LTK and rejects fresh SMP with `auth fail reason=82`, the integration calls `esphome.<proxy>_clear_ble_bonds` to wipe the NVS bond table — there is no stock equivalent. Without it the Hard Repair path degrades to a clearly-worded error and a manual reflash. |
| Custom `api.actions: disconnect_ble_peer` | same repair flow, when a slot is stuck `ESTABLISHED` | Forces `esp_ble_gap_disconnect` on a half-closed link the GAP layer no longer tracks, so the next pair attempt opens a fresh SMP exchange. |
| Custom `api.actions: restart_ble` | C6 controller-wedge recovery; called by Hard Repair when available | `ble.disable → 2 s → ble.enable`: reinitializes the BLE stack **without** rebooting the ESP and **without** touching NVS bonds. The only remedy short of a reboot for the C6 stale-pending-create-connection wedge (`status=133` + HCI `0x2043` loop). |
| `esphome: { min_version: 2026.7.0 }` | all of the above | Guarantees the `api.actions:` schema (2025.8+), the bluetooth_proxy slot-leak fix (2026.5.1, [esphome#16588](https://github.com/esphome/esphome/pull/16588)) and the stale-subscriber takeover (2026.7.0, [esphome#17423](https://github.com/esphome/esphome/pull/17423) — cures "proxy connected but no advertisements" after an HA restart). |

#### Multiple proxies in the house? Pin the machine to ONE

These machines keep **a single bond slot tied to the identity of the one
proxy that paired**. Home Assistant routes each connection through whichever
connectable proxy has the best signal *at that moment* — if that's ever a
different proxy, the machine rejects the encryption with
`auth fail reason=82` and the connection drops (field case #10).

**As of integration 0.91.0** this is handled in software: bonded BLE
**source affinity** pins every connect to the proxy that owns the bond
(learned from the successful encrypted handshake), so all proxies may keep
`bluetooth_proxy: { active: true }`. A different bond-owning source can be
selected deliberately in the integration's options (Automatic / local
adapter / a specific proxy); the switch takes effect after the next
successful handshake.

**On integration versions before 0.91.0** apply the manual rule instead:

- exactly **one** proxy in the machine's radio range runs
  `bluetooth_proxy: { active: true }`;
- every other proxy sets `active: false` — advertisements still flow
  (presence/sensor integrations keep working), but connections and the bond
  always go through the machine's own proxy;
- non-bonding BLE devices (blinds, sensors) are unaffected by this rule —
  it exists only for bonded peripherals like these coffee machines.

The integration falls back gracefully when an action is missing
(`hass.services.has_service` check + an explicit error message naming the
exact YAML snippet to add — see `__init__.py:_handle_force_repair`), so a
stock-config `bluetooth_proxy` still **works for daily use**. The lossy
degradation is confined to the recovery flows: stuck-bond / stuck-slot
incidents then need a manual proxy reboot instead of a service call from
HA's Repairs dialog.

The XIAO C6 yaml additionally toggles antenna-switch GPIOs (FM8625H IC on
GPIO3 + GPIO14) and a status LED on GPIO15. These are board-specific to the
Seeed XIAO ESP32-C6 and harmless to omit on other boards.

### 1.2 Local Bluetooth adapter ⚠️ supported, less-tested

Built-in BLE on the Home Assistant host (a Raspberry Pi's onboard adapter, a
USB dongle, a workstation chipset). Goes through BlueZ + a D-Bus `Agent1`
that this integration registers automatically (`ble_agent.py`'s
`_NoInputOutputAgent` for "Just Works" pairing).

This topology **works**, but the integration's pair / reconnect path under
BlueZ has fewer combined test-hours than the proxy path because the
maintainer's dev setup doesn't use it. Edge cases reported through GitHub
issues are typically here.

| Adapter | Chipset | Status | Notes |
|---|---|---|---|
| **Raspberry Pi 4 / 5 onboard BLE** | Cypress / Infineon CYW43455 (Pi 4) / CYW43455 (Pi 5) | 👥 community-confirmed | Works after the routine `bluetoothctl` setup. Pi's antenna design varies — keep the machine within line of sight if range is a problem. |
| **Intel Wireless 7265 / 8260 / 8265 / AX200 / AX201 / AX210** | Intel | 👥 community-confirmed | Standard Linux BlueZ path. Reliable. |
| **CSR8510 USB dongle** | CSR / Qualcomm | 👥 community-confirmed | The classic $5 BLE dongle. Verified through unrelated HA integrations; assumed-good here. |
| **TP-Link UB500 / UB400** | RTL8761B | 👥 community-confirmed | Needs `rtl_bt_*` firmware on Debian-style distros. Otherwise drop-in. |
| **Apple Broadcom built-in (Mac mini / iMac)** | BCM2046B1 / BCM20702A0 (USB ID `05ac:828d`) | ✅ verified (#14) | Full-stack verified on Ubuntu 24.04 + HA Container + BlueZ 5.72 with NICR 779 (Nivona 7xx). HCI/LMP 4.0, manufacturer ID `0x000f`. |
| **Built-in BLE on `homeassistant.local` HA OS** | varies (host hardware) | 👥 case-by-case | Most failures here come down to `bluetoothd` cache or stale pairing — see the troubleshooting notes below and in #14. |

#### Known headless-Linux quirks (from #14)

- Stale BlueZ cache for a previously-discovered machine can survive across
  factory-reset + re-add. Cleanup: `bluetoothctl disconnect <MAC>` then
  `bluetoothctl remove <MAC>`, then add the integration again with the
  machine in pair mode.
- `bluetoothd` can SEGFAULT on certain pairing-cancel paths if no D-Bus
  agent is registered when the request arrives. The integration's
  `_NoInputOutputAgent` covers this.

### 1.3 BlueZ on Docker / VPS without a desktop session

Same as 1.2 but specifically headless. The D-Bus pairing agent in the
integration is what unblocks this — there's no Blueman / gnome-bluetooth
helper to fall back on.

**Container prerequisites (often missed — root cause of "HU handshake
timeout" misdiagnoses, see #14 follow-up)**:

1. **`bluez` must be installed on the host** (NOT just `bluez-obexd`).
   Without `bluetoothd` running on the host, no GATT pairing happens
   even though the integration loads cleanly.
   ```bash
   sudo apt update && sudo apt install -y bluez
   sudo systemctl enable --now bluetooth
   ```

2. **D-Bus socket must be mounted into the container.** Add to your
   `docker run` / `docker-compose` config:
   ```yaml
   volumes:
     - /run/dbus:/run/dbus:ro
   ```
   Without this, the integration cannot talk to `bluetoothd` at all and
   `Authentication failed` / `No agent available` errors surface during
   first pairing.

3. **`--privileged` or capability `NET_ADMIN` + `BLUETOOTH_ADMIN`**
   typically required for BLE scanning from inside a container.

4. **Network mode `host`** recommended (matches HA's expected
   discovery behaviour).

If something still breaks after these prerequisites, please include
`bluetoothctl show` output and `dmesg | grep -i bluetooth` in the bug
report.

---

## 2. Coffee machines

Authoritative table is in the
[README's "Supported brands and models" section](README.md#supported-brands-and-models).
Quick reference for triage:

| Brand | Family | Recipe writes | Recipe reads | Auto-brew via Sommelier | Notes |
|---|---|---|---|---|---|
| **Melitta** | Barista T Smart | ✅ | ✅ | ✅ | Single-hopper. Stable. |
| **Melitta** | Barista TS Smart | ✅ | ✅ | ✅ | Dual-hopper. Stable. |
| **Nivona** | NICR 6xx | ❌ | ❌ | ❌ (print-only) | Sommelier panel still works as a recipe notebook. |
| **Nivona** | NICR 7xx (756–789) | ❌ | ❌ | ❌ (print-only) | Includes NICR 779 — regex fix in v0.74.2 (#14). |
| **Nivona** | NICR 79x | ❌ | ❌ | ❌ (print-only) | — |
| **Nivona** | NICR 9xx | ❌ | ❌ | ❌ (print-only) | Fluid amounts written as ml × 10 — handled internally. |
| **Nivona** | NICR 1030 / 1040 | ❌ | ❌ | ❌ (print-only) | — |
| **Nivona** | NIVO 8xxx | ❌ | ❌ | ❌ (print-only) | Different brew opcode (`0x04` vs `0x0B`) — handled. |

"Auto-brew via Sommelier" is gated on the family's `supports_recipe_writes`
flag. All Nivona families decline because the integration cannot write a
custom freestyle recipe to the machine's recipe table. The Sommelier UI
still generates recipes, lets you rate / favorite / save them as presets,
and shows the steps to brew manually via the machine's own selector — only
the "Start brewing" button is disabled. See
[CHANGELOG 0.73.0](CHANGELOG.md) for the brand-honesty gate.

---

## 3. Contributing to this list

If your hardware combination isn't listed:

1. File a bug report (works ✅ or doesn't ❌) via
   [the issue template](https://github.com/dzerik/melitta-barista-ha/issues/new?template=bug_report.yml).
2. The template asks for **BLE adapter chipset / model** — fill it in.
3. The maintainer adds an HCL.md row in the next release.

If you can reproduce a fresh BlueZ / D-Bus quirk specifically against
section 1.2 — please include `bluetoothctl show`, `bluetoothctl info <MAC>`,
and the `melitta_barista` debug log around the failed connection. Those
three artifacts are what we need to upgrade an entry from 👥 to ✅ or move
it to ❌.
