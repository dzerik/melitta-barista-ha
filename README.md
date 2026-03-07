# Melitta Barista Smart for Home Assistant

[![HACS Validation](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/integration)
[![Home Assistant](https://img.shields.io/badge/Home%20Assistant-2024.1%2B-blue.svg)](https://www.home-assistant.io/)
[![License: MIT](https://img.shields.io/github/license/dzerik/melitta-barista-ha)](https://github.com/dzerik/melitta-barista-ha/blob/main/LICENSE)
[![GitHub Release](https://img.shields.io/github/v/release/dzerik/melitta-barista-ha)](https://github.com/dzerik/melitta-barista-ha/releases)

A custom Home Assistant integration for controlling **Melitta Barista T Smart** and **Melitta Barista TS Smart** coffee machines over Bluetooth Low Energy (BLE). Monitor machine status, brew recipes, adjust settings, and trigger maintenance -- all from your Home Assistant dashboard.

---

## Supported Models

| Model | Type ID | BLE Prefixes | Recipes | Bean Hoppers |
|-------|---------|--------------|---------|--------------|
| **Barista T Smart** | 258 | 8301, 8311, 8401 | 21 | 1 (single) |
| **Barista TS Smart** | 259 | 8501, 8601, 8604 | 24 | 2 (dual) |

The machine model is automatically detected from the BLE device name and confirmed via the BLE protocol. All entities, recipes, and settings are filtered per model.

## Features

- **Multi-model support** -- automatic detection of Barista T and Barista TS with model-specific entity filtering
- **Real-time status monitoring** -- machine state, brewing activity, progress percentage, and required user actions via BLE push notifications
- **21/24 built-in recipes** -- select a recipe from the dropdown and brew with one tap (3 extra recipes on TS model)
- **Machine settings control** -- water hardness, brew temperature, auto-off timer, energy saving, and more
- **Maintenance operations** -- easy clean, intensive clean, descaling, and power off
- **BLE auto-discovery** -- the integration detects your Melitta machine automatically during setup
- **Encrypted BLE protocol** -- full AES/RC4 encrypted communication as used by the official Melitta app
- **User profiles** -- read and edit user profile names on the machine
- **Freestyle recipes** -- build custom drinks with two configurable components (coffee/milk/water), adjustable intensity, temperature, shots, and portion sizes
- **Custom Lovelace card** -- dedicated card available separately: [melitta-barista-card](https://github.com/dzerik/melitta-barista-card)
- **Standalone PWA** -- full-screen React app for tablets and kiosks: [melitta-barista-app](https://github.com/dzerik/melitta-barista-app)
- **29 languages** -- full localization for all European and Slavic languages

## Supported Recipes

| # | Recipe | T | TS | # | Recipe | T | TS |
|---|--------|---|-----|---|--------|---|-----|
| 1 | Espresso | + | + | 13 | Dead Eye | -- | + |
| 2 | Ristretto | + | + | 14 | Cappuccino | + | + |
| 3 | Lungo | + | + | 15 | Espresso Macchiato | + | + |
| 4 | Espresso Doppio | + | + | 16 | Caffe Latte | + | + |
| 5 | Ristretto Doppio | + | + | 17 | Cafe au Lait | + | + |
| 6 | Cafe Creme | + | + | 18 | Flat White | + | + |
| 7 | Cafe Creme Doppio | + | + | 19 | Latte Macchiato | + | + |
| 8 | Americano | + | + | 20 | Latte Macchiato Extra | + | + |
| 9 | Americano Extra | + | + | 21 | Latte Macchiato Triple | + | + |
| 10 | Long Black | + | + | 22 | Milk | + | + |
| 11 | Red Eye | -- | + | 23 | Milk Froth | + | + |
| 12 | Black Eye | -- | + | 24 | Hot Water | + | + |

> Red Eye, Black Eye, and Dead Eye are only available on the Barista TS Smart (dual bean hopper model).

## Requirements

- **Home Assistant** 2024.1 or newer
- **Bluetooth adapter** -- a BLE-capable adapter accessible to your Home Assistant host (built-in or USB dongle)
- **Melitta Barista T Smart** or **Melitta Barista TS Smart** coffee machine with Bluetooth enabled
- **BLE range** -- the Home Assistant host must be within Bluetooth range of the machine (typically up to 10 meters)

## Installation

### Via HACS (recommended)

1. Open HACS in your Home Assistant instance.
2. Go to **Integrations** and select the three-dot menu in the top right corner.
3. Choose **Custom repositories**.
4. Add the repository URL: `https://github.com/dzerik/melitta-barista-ha`
5. Select category **Integration** and click **Add**.
6. Search for "Melitta Barista Smart" in HACS and install it.
7. Restart Home Assistant.

### Manual Installation

1. Download the latest release from the [GitHub releases page](https://github.com/dzerik/melitta-barista-ha/releases).
2. Copy the `custom_components/melitta_barista` directory into your Home Assistant `config/custom_components/` directory.
3. Restart Home Assistant.

## Custom Lovelace Card

A dedicated Lovelace card with recipe buttons, status display, and progress bar is available as a separate repository:

**[melitta-barista-card](https://github.com/dzerik/melitta-barista-card)** -- install via HACS (Frontend > Custom repositories) or manually.

## Standalone PWA (Tablet / Kiosk)

A standalone React PWA for controlling the coffee machine is available as a separate project:

**[melitta-barista-app](https://github.com/dzerik/melitta-barista-app)** -- a full-screen progressive web app designed for wall-mounted tablets and kiosk displays.

- Connects to Home Assistant via WebSocket API using a long-lived access token
- Auto-detects the Melitta machine from HA entities
- Three tabs: **Brew** (recipe grid with SVG icons), **Freestyle** (custom drink builder), **Settings** (machine configuration)
- Real-time brewing progress with cancel support
- Installable as a PWA on any device (Android, iOS, desktop)
- Dark coffee-themed UI optimized for touch

## Configuration

### Step 1: Enable Bluetooth on the machine

Make sure Bluetooth is enabled on your coffee machine (refer to the machine manual).

### Step 2: Add the integration

1. In Home Assistant, go to **Settings** > **Devices & Services** > **Add Integration**.
2. Search for **Melitta Barista Smart**.
3. If BLE discovery has found your machine, it will appear automatically. Otherwise, you can enter the MAC address manually.

### Step 3: Pair the device

The integration requires BLE pairing (bonding) with your coffee machine. During setup, you will be prompted to enable pairing mode on the machine:

1. On the machine, open the **Settings** menu and navigate to **Bluetooth** / **Connectivity**.
2. Enable **pairing mode** — the BLE icon on the machine should start blinking.
3. Press **Submit** in the Home Assistant setup dialog.
4. The integration will connect and pair automatically. If the machine shows a confirmation prompt, accept it.

> **Note:** The machine supports only one active BLE connection at a time. Make sure the official Melitta app is disconnected before pairing with Home Assistant.

If the device is already paired (e.g., via `bluetoothctl`), the integration detects this and skips the pairing step.

### Manual pairing via bluetoothctl

If automatic pairing does not work, you can pair manually via SSH on the Home Assistant host:

```bash
bluetoothctl
remove F1:2C:72:3F:75:ED        # Replace with your machine's MAC address
scan on                          # Wait for the machine to appear
pair F1:2C:72:3F:75:ED
trust F1:2C:72:3F:75:ED
info F1:2C:72:3F:75:ED           # Verify: Paired: yes, Bonded: yes, Trusted: yes
exit
```

Then add the integration in Home Assistant as described above.

Once configured, the integration creates a device with all available entities filtered for your machine model.

## Entities Reference

### Sensors

| Entity | Description |
|--------|-------------|
| State | Current machine state: Ready, Brewing, Cleaning, Descaling, Off, etc. |
| Activity | Current sub-process: Grinding, Extracting, Steaming, Dispensing Water, Preparing |
| Progress | Brewing or cleaning progress as a percentage |
| Action Required | Required user action: Fill Water, Empty Trays, Brew Unit Removed, etc. |
| Connection | BLE connection status: Connected or Disconnected (diagnostic) |
| Firmware | Firmware version reported by the machine (diagnostic) |

### Select

| Entity | Description |
|--------|-------------|
| Recipe | Dropdown selector for all available recipes (21 on T, 24 on TS). |
| Profile | Active user profile selector. |
| Freestyle Process 1 | Component 1 process: coffee, milk, or water. |
| Freestyle Intensity 1 | Component 1 brew intensity. |
| Freestyle Temperature 1 | Component 1 temperature level. |
| Freestyle Shots 1 | Component 1 number of shots. |
| Freestyle Process 2 | Component 2 process: none, coffee, milk, or water. |
| Freestyle Intensity 2 | Component 2 brew intensity. |
| Freestyle Temperature 2 | Component 2 temperature level. |
| Freestyle Shots 2 | Component 2 number of shots. |

### Buttons

| Entity | Description |
|--------|-------------|
| Brew | Brew the recipe selected in the Recipe dropdown. Available when machine is Ready and a recipe is selected. |
| Brew Freestyle | Brew the custom freestyle recipe using current freestyle parameters. |
| Cancel | Cancel the currently running operation. |
| Easy Clean | Start the easy clean cycle (configuration). |
| Intensive Clean | Start the intensive clean cycle (configuration). |
| Descaling | Start the descaling process (configuration). |
| Switch Off | Power off the machine (configuration). |

### Numbers

| Entity | Range | Description |
|--------|-------|-------------|
| Water Hardness | 1 -- 4 | Water hardness level for descaling schedule (configuration). |
| Auto Off After | 15 -- 240 min | Idle time before automatic power off (configuration). |
| Brew Temperature | 0 -- 2 | Brew temperature level: 0 = Cold, 1 = Normal, 2 = High (configuration). |
| Freestyle Portion 1 | 5 -- 250 ml | Component 1 portion size in milliliters. |
| Freestyle Portion 2 | 0 -- 250 ml | Component 2 portion size in milliliters (0 = disabled). |

### Switches

| Entity | Model | Description |
|--------|-------|-------------|
| Energy Saving | T, TS | Enable or disable energy saving mode (configuration). |
| Auto Bean Select | TS only | Enable or disable automatic bean blend selection (configuration). |
| Rinsing Disabled | T, TS | Enable or disable the automatic rinsing cycle (configuration). |

### Text

| Entity | Model | Description |
|--------|-------|-------------|
| Profile 1-4 Name | T | User profile names (read/write, configuration). |
| Profile 1-8 Name | TS | User profile names (read/write, configuration). |
| Freestyle Name | T, TS | Custom name for the freestyle recipe. |

## Localization

The integration includes translations for 29 languages:

English, Russian, Ukrainian, German, Polish, Czech, Slovak, French, Italian, Spanish, Portuguese, Dutch, Swedish, Danish, Norwegian, Finnish, Hungarian, Romanian, Greek, Turkish, Bulgarian, Croatian, Serbian, Slovenian, Bosnian, Macedonian, Estonian, Latvian, Lithuanian.

## Known Limitations

- **BLE range**: Bluetooth Low Energy has a limited range (typically up to 10 meters). Walls and other obstacles reduce effective range. Consider placing a Bluetooth-capable device (e.g., an ESPHome BLE proxy) near the machine if your Home Assistant host is too far away.
- **Single connection**: The machine supports only one active BLE connection at a time. If the official Melitta app is connected, the integration will not be able to connect, and vice versa.
- **Single BLE client**: The integration operates as a single BLE client. User profile names can be read and edited, but per-profile recipe customizations are not yet exposed.
- **Polling interval**: Machine status is polled every 5 seconds while connected. There may be a brief delay between a physical action and the state update in Home Assistant.
- **Recipe parameters**: Built-in recipes use the machine's stored default parameters. For full customization, use the Freestyle recipe builder with adjustable process, intensity, temperature, shots, and portion for each component.

## Troubleshooting

**The machine is not discovered during setup**
- Verify that Bluetooth is enabled on the machine (check the machine display or manual).
- Ensure the Home Assistant host has a working Bluetooth adapter. Run `bluetoothctl scan on` on the host to verify BLE scanning works.
- Move the Home Assistant host closer to the machine.
- Make sure no other device (e.g., the Melitta app on your phone) is currently connected to the machine.

**Connection fails with "D-Bus connection lost"**
- The device is not paired with the Home Assistant host. Follow the pairing instructions in the Configuration section above.
- If already paired, try removing and re-pairing: `bluetoothctl remove <MAC> && bluetoothctl pair <MAC>`.

**Connection drops frequently**
- BLE connections are sensitive to distance and interference. Reduce the distance between the host and the machine.
- Consider using an ESPHome Bluetooth proxy placed near the machine.
- Check Home Assistant logs for BLE-related errors: **Settings** > **System** > **Logs**, then filter for `melitta_barista`.

**Buttons show as unavailable**
- Recipe and maintenance buttons are only available when the machine state is "Ready". Check the State sensor.
- If the State sensor shows "unavailable", the BLE connection may be lost. Check the Connection sensor.

**Settings do not update**
- Number and switch entities read values from the machine. If the machine is disconnected, the last known value is displayed. Reconnect and trigger a manual refresh if needed.

**Enable debug logging**

Add the following to your `configuration.yaml`:

```yaml
logger:
  default: info
  logs:
    melitta_barista: debug
```

Restart Home Assistant and reproduce the issue, then check the logs.

## Contributing

Contributions are welcome. Please open an issue or submit a pull request on [GitHub](https://github.com/dzerik/melitta-barista-ha).

1. Fork the repository.
2. Create a feature branch.
3. Make your changes and add tests where applicable.
4. Submit a pull request with a clear description of the changes.

## License

This project is licensed under the [MIT License](LICENSE).
