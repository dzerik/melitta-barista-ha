#!/usr/bin/env python3
"""Melitta Barista BLE ID Scanner.

Scans all numerical (HR), alphanumeric (HA), and recipe (HC) IDs
to discover undocumented data stored in the machine.

Usage:
    python scripts/ble_scanner.py [BLE_ADDRESS]

If no address is provided, performs BLE discovery first.

Requirements:
    pip install bleak pycryptodome
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import importlib
import importlib.util
import logging
import os
import struct
import sys
import types
from datetime import datetime
from pathlib import Path

# Import melitta_barista modules exactly like brew_espresso.py
pkg_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "custom_components", "melitta_barista")
pkg_dir = os.path.normpath(pkg_dir)
pkg = types.ModuleType("melitta_barista")
pkg.__path__ = [pkg_dir]
pkg.__package__ = "melitta_barista"
sys.modules["melitta_barista"] = pkg
for _mod_name in ("const", "protocol"):
    _spec = importlib.util.spec_from_file_location(
        f"melitta_barista.{_mod_name}", os.path.join(pkg_dir, f"{_mod_name}.py"))
    _mod = importlib.util.module_from_spec(_spec)
    sys.modules[f"melitta_barista.{_mod_name}"] = _mod
    _spec.loader.exec_module(_mod)

from melitta_barista.protocol import (  # type: ignore[import-untyped]
    AlphanumericValue,
    MachineRecipe,
    MelittaProtocol,
    NumericalValue,
)
from melitta_barista.const import CHAR_NOTIFY, CHAR_WRITE  # type: ignore[import-untyped]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
_LOGGER = logging.getLogger("ble_scanner")

# Reduce bleak noise
logging.getLogger("bleak").setLevel(logging.WARNING)

MELITTA_SERVICE_UUID = "0000ad00-b35c-11e4-9813-0002a5d5c51b"

# Known IDs for annotation
KNOWN_NUMERICAL: dict[int, str] = {
    6: "MACHINE_TYPE",
    11: "WATER_HARDNESS",
    12: "ENERGY_SAVING",
    13: "AUTO_OFF_AFTER",
    14: "AUTO_OFF_WHEN",
    15: "LANGUAGE",
    16: "AUTO_BEAN_SELECT",
    18: "RINSING_OFF",
    20: "CLOCK",
    21: "CLOCK_SEND",
    22: "TEMPERATURE",
    91: "FILTER",
    311: "USER_1_ACTIVITY",
    321: "USER_2_ACTIVITY",
    331: "USER_3_ACTIVITY",
    341: "USER_4_ACTIVITY",
    351: "USER_5_ACTIVITY",
    361: "USER_6_ACTIVITY",
    371: "USER_7_ACTIVITY",
    381: "USER_8_ACTIVITY",
}

KNOWN_ALPHA: dict[int, str] = {
    310: "USER_1_NAME",
    320: "USER_2_NAME",
    330: "USER_3_NAME",
    340: "USER_4_NAME",
    350: "USER_5_NAME",
    360: "USER_6_NAME",
    370: "USER_7_NAME",
    380: "USER_8_NAME",
    401: "FREESTYLE_NAME",
}

KNOWN_RECIPE: dict[int, str] = {
    200: "ESPRESSO", 201: "RISTRETTO", 202: "LUNGO",
    203: "ESPRESSO_DOPIO", 204: "RISETTO_DOPIO",
    205: "CAFE_CREME", 206: "CAFE_CREME_DOPIO",
    207: "AMERICANO", 208: "AMERICANO_EXTRA",
    209: "LONG_BLACK", 210: "RED_EYE", 211: "BLACK_EYE", 212: "DEAD_EYE",
    213: "CAPPUCCINO", 214: "ESPR_MACCHIATO",
    215: "CAFFE_LATTE", 216: "CAFE_AU_LAIT", 217: "FLAT_WHITE",
    218: "LATTE_MACCHIATO", 219: "LATTE_MACCHIATO_EXTRA",
    220: "LATTE_MACCHIATO_TRIPLE",
    221: "MILK", 222: "MILK_FROTH", 223: "WATER",
    400: "TEMP_RECIPE",
}

DELAY_BETWEEN_REQUESTS = 0.3  # seconds


class ScanResult:
    """Collects scan results."""

    def __init__(self) -> None:
        self.numerical: list[tuple[int, str, int, str]] = []  # id, name, value, raw_hex
        self.alpha: list[tuple[int, str, str, str]] = []  # id, name, value, raw_hex
        self.recipe: list[tuple[int, str, str]] = []  # id, name, summary
        self.failed_numerical: list[int] = []
        self.failed_alpha: list[int] = []
        self.failed_recipe: list[int] = []


async def discover_device() -> str | None:
    """Discover Melitta device via BLE scan."""
    from bleak import BleakScanner

    _LOGGER.info("Scanning for Melitta devices (10s)...")
    devices = await BleakScanner.discover(timeout=10.0)
    for d in devices:
        if d.name and d.name.startswith("8604"):
            _LOGGER.info("Found: %s (%s)", d.name, d.address)
            return d.address
        metadata = d.metadata or {}
        uuids = metadata.get("uuids", [])
        if MELITTA_SERVICE_UUID in uuids:
            _LOGGER.info("Found: %s (%s)", d.name, d.address)
            return d.address
    return None


async def scan(
    address: str,
    hr_range: tuple[int, int],
    ha_range: tuple[int, int],
    hc_range: tuple[int, int],
) -> ScanResult:
    """Connect and scan all ID ranges."""
    from bleak import BleakClient

    protocol = MelittaProtocol()
    result = ScanResult()

    client = BleakClient(address, timeout=15.0)

    _LOGGER.info("Connecting to %s...", address)
    await client.connect(dangerous_use_bleak_cache=True)

    if not client.is_connected:
        _LOGGER.error("Failed to connect")
        return result

    _LOGGER.info("Connected. Subscribing to notifications...")

    async def write_ble(data: bytes) -> None:
        await client.write_gatt_char(CHAR_WRITE, data)

    await client.start_notify(CHAR_NOTIFY, lambda _s, d: protocol.on_ble_data(bytes(d)))

    _LOGGER.info("Performing handshake...")
    if not await protocol.perform_handshake(write_ble):
        _LOGGER.error("Handshake failed!")
        await client.disconnect()
        return result

    _LOGGER.info("Handshake OK. Starting scan...")

    # --- Phase 1: Scan HR (Numerical Values) ---
    hr_start, hr_end = hr_range
    total_hr = hr_end - hr_start + 1
    _LOGGER.info("=== Phase 1: HR (Numerical) IDs %d-%d (%d total) ===", hr_start, hr_end, total_hr)

    for i, vid in enumerate(range(hr_start, hr_end + 1)):
        if (i + 1) % 50 == 0 or i == 0:
            _LOGGER.info("  HR progress: %d/%d", i + 1, total_hr)

        payload = struct.pack(">h", vid)
        data = await protocol.send_and_wait_response("HR", payload, write_ble)

        if data is not None:
            name = KNOWN_NUMERICAL.get(vid, "???")
            tag = " (KNOWN)" if vid in KNOWN_NUMERICAL else " ** NEW **"
            if len(data) >= 6:
                nv = NumericalValue.from_payload(data)
                if nv:
                    _LOGGER.info(
                        "  HR id=%d (%s) value=%d (0x%08x)%s  raw=%s",
                        vid, name, nv.value, nv.value & 0xFFFFFFFF, tag, data.hex(),
                    )
                    result.numerical.append((vid, name, nv.value, data.hex()))
                else:
                    result.numerical.append((vid, name, 0, data.hex()))
            else:
                _LOGGER.info(
                    "  HR id=%d (%s) short_payload=%s (%d bytes)%s",
                    vid, name, data.hex(), len(data), tag,
                )
                result.numerical.append((vid, name, 0, data.hex()))
        else:
            result.failed_numerical.append(vid)

        await asyncio.sleep(DELAY_BETWEEN_REQUESTS)

    _LOGGER.info("HR scan done: %d found, %d failed/NACK",
                  len(result.numerical), len(result.failed_numerical))

    # --- Phase 2: Scan HA (Alphanumeric Values) ---
    ha_start, ha_end = ha_range
    total_ha = ha_end - ha_start + 1
    _LOGGER.info("=== Phase 2: HA (Alphanumeric) IDs %d-%d (%d total) ===", ha_start, ha_end, total_ha)

    for i, vid in enumerate(range(ha_start, ha_end + 1)):
        if (i + 1) % 50 == 0 or i == 0:
            _LOGGER.info("  HA progress: %d/%d", i + 1, total_ha)

        payload = struct.pack(">h", vid)
        data = await protocol.send_and_wait_response("HA", payload, write_ble)

        if data is not None:
            name = KNOWN_ALPHA.get(vid, "???")
            tag = " (KNOWN)" if vid in KNOWN_ALPHA else " ** NEW **"
            if len(data) >= 3:
                av = AlphanumericValue.from_payload(data)
                if av:
                    display_val = av.value if av.value.strip() else f"(empty: {data[2:].hex()})"
                    _LOGGER.info(
                        "  HA id=%d (%s) value=%r%s  raw=%s",
                        vid, name, display_val, tag, data.hex(),
                    )
                    result.alpha.append((vid, name, av.value, data.hex()))
                else:
                    result.alpha.append((vid, name, "", data.hex()))
            else:
                _LOGGER.info(
                    "  HA id=%d (%s) short_payload=%s (%d bytes)%s",
                    vid, name, data.hex(), len(data), tag,
                )
                result.alpha.append((vid, name, "", data.hex()))
        else:
            result.failed_alpha.append(vid)

        await asyncio.sleep(DELAY_BETWEEN_REQUESTS)

    _LOGGER.info("HA scan done: %d found, %d failed/NACK",
                  len(result.alpha), len(result.failed_alpha))

    # --- Phase 3: Scan HC (Recipe) IDs ---
    hc_start, hc_end = hc_range
    total_hc = hc_end - hc_start + 1
    _LOGGER.info("=== Phase 3: HC (Recipe) IDs %d-%d (%d total) ===", hc_start, hc_end, total_hc)

    for i, vid in enumerate(range(hc_start, hc_end + 1)):
        if (i + 1) % 50 == 0 or i == 0:
            _LOGGER.info("  HC progress: %d/%d", i + 1, total_hc)

        payload = struct.pack(">h", vid)
        data = await protocol.send_and_wait_response("HC", payload, write_ble)

        if data is not None:
            name = KNOWN_RECIPE.get(vid, "???")
            tag = " (KNOWN)" if vid in KNOWN_RECIPE else " ** NEW **"
            if len(data) >= 19:
                mr = MachineRecipe.from_payload(data)
                if mr and mr.component1 and mr.component2:
                    summary = (
                        f"type={mr.recipe_type} "
                        f"c1=[proc={mr.component1.process} shots={mr.component1.shots} "
                        f"int={mr.component1.intensity} {mr.component1.portion_ml}ml] "
                        f"c2=[proc={mr.component2.process} shots={mr.component2.shots} "
                        f"int={mr.component2.intensity} {mr.component2.portion_ml}ml]"
                    )
                else:
                    summary = f"parse_failed raw={data.hex()} ({len(data)} bytes)"
            else:
                summary = f"short_payload={data.hex()} ({len(data)} bytes)"
            _LOGGER.info("  HC id=%d (%s) %s%s", vid, name, summary, tag)
            result.recipe.append((vid, name, summary))
        else:
            result.failed_recipe.append(vid)

        await asyncio.sleep(DELAY_BETWEEN_REQUESTS)

    _LOGGER.info("HC scan done: %d found, %d failed/NACK",
                  len(result.recipe), len(result.failed_recipe))

    # Disconnect
    _LOGGER.info("Disconnecting...")
    await client.disconnect()

    return result


def save_results(result: ScanResult, output_dir: Path) -> None:
    """Save results to CSV files."""
    output_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")

    # HR results
    hr_file = output_dir / f"scan_hr_{ts}.csv"
    with open(hr_file, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["id", "known_name", "value", "value_hex", "raw_payload_hex", "is_new"])
        for vid, name, value, raw in result.numerical:
            w.writerow([vid, name, value, f"0x{value & 0xFFFFFFFF:08x}", raw, name == "???"])
    _LOGGER.info("HR results saved to %s", hr_file)

    # HA results
    ha_file = output_dir / f"scan_ha_{ts}.csv"
    with open(ha_file, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["id", "known_name", "value", "raw_payload_hex", "is_new"])
        for vid, name, value, raw in result.alpha:
            w.writerow([vid, name, value, raw, name == "???"])
    _LOGGER.info("HA results saved to %s", ha_file)

    # HC results
    hc_file = output_dir / f"scan_hc_{ts}.csv"
    with open(hc_file, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["id", "known_name", "summary", "is_new"])
        for vid, name, summary in result.recipe:
            w.writerow([vid, name, summary, name == "???"])
    _LOGGER.info("HC results saved to %s", hc_file)


def print_summary(result: ScanResult) -> None:
    """Print a summary of new (undocumented) findings."""
    new_hr = [(v, n, val, r) for v, n, val, r in result.numerical if n == "???"]
    new_ha = [(v, n, val, r) for v, n, val, r in result.alpha if n == "???"]
    new_hc = [(v, n, s) for v, n, s in result.recipe if n == "???"]

    print("\n" + "=" * 70)
    print("SCAN SUMMARY")
    print("=" * 70)
    print(f"\nHR (Numerical): {len(result.numerical)} found, {len(new_hr)} NEW")
    print(f"HA (Alphanumeric): {len(result.alpha)} found, {len(new_ha)} NEW")
    print(f"HC (Recipe): {len(result.recipe)} found, {len(new_hc)} NEW")

    if new_hr:
        print("\n--- NEW Numerical Values (HR) ---")
        for vid, _, value, raw in new_hr:
            print(f"  ID {vid:4d} = {value} (0x{value & 0xFFFFFFFF:08x})  raw: {raw}")

    if new_ha:
        print("\n--- NEW Alphanumeric Values (HA) ---")
        for vid, _, value, raw in new_ha:
            print(f"  ID {vid:4d} = {value!r}  raw: {raw}")

    if new_hc:
        print("\n--- NEW Recipe IDs (HC) ---")
        for vid, _, summary in new_hc:
            print(f"  ID {vid:4d}: {summary}")

    if not new_hr and not new_ha and not new_hc:
        print("\nNo new (undocumented) IDs found.")

    print()


def parse_range(s: str) -> tuple[int, int]:
    """Parse 'start-end' or 'start:end' range string."""
    for sep in ("-", ":"):
        if sep in s:
            parts = s.split(sep, 1)
            return int(parts[0]), int(parts[1])
    raise argparse.ArgumentTypeError(f"Invalid range: {s!r} (expected 'start-end')")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Scan Melitta Barista BLE IDs to discover undocumented data"
    )
    parser.add_argument("address", nargs="?", help="BLE address (auto-discover if omitted)")
    parser.add_argument("--hr", type=parse_range, default="0-500",
                        help="HR (numerical) ID range (default: 0-500)")
    parser.add_argument("--ha", type=parse_range, default="0-500",
                        help="HA (alphanumeric) ID range (default: 0-500)")
    parser.add_argument("--hc", type=parse_range, default="0-500",
                        help="HC (recipe) ID range (default: 0-500)")
    parser.add_argument("--no-hr", action="store_true", help="Skip HR scan")
    parser.add_argument("--no-ha", action="store_true", help="Skip HA scan")
    parser.add_argument("--no-hc", action="store_true", help="Skip HC scan")
    parser.add_argument("--output", type=Path, default=Path("scan_results"),
                        help="Output directory for CSV files (default: scan_results/)")
    parser.add_argument("--delay", type=float, default=0.3,
                        help="Delay between requests in seconds (default: 0.3)")
    parser.add_argument("-v", "--verbose", action="store_true", help="Verbose logging")

    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)
        logging.getLogger("bleak").setLevel(logging.INFO)

    global DELAY_BETWEEN_REQUESTS
    DELAY_BETWEEN_REQUESTS = args.delay

    # Use empty ranges if skipped
    hr_range = args.hr if not args.no_hr else (0, -1)
    ha_range = args.ha if not args.no_ha else (0, -1)
    hc_range = args.hc if not args.no_hc else (0, -1)

    async def run() -> None:
        address = args.address
        if not address:
            address = await discover_device()
            if not address:
                _LOGGER.error("No Melitta device found. Specify address manually.")
                sys.exit(1)

        _LOGGER.info("Target device: %s", address)

        total_ids = sum(
            max(0, r[1] - r[0] + 1) for r in [hr_range, ha_range, hc_range]
        )
        est_time = total_ids * (DELAY_BETWEEN_REQUESTS + 0.1)
        _LOGGER.info(
            "Scanning %d IDs total, estimated time: ~%.0f seconds (%.1f min)",
            total_ids, est_time, est_time / 60,
        )

        result = await scan(address, hr_range, ha_range, hc_range)
        save_results(result, args.output)
        print_summary(result)

    asyncio.run(run())


if __name__ == "__main__":
    main()
