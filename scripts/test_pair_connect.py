#!/usr/bin/env python3
"""Test BLE connection to Melitta with pair=True.

Tests the new auto-pairing via Bleak's pair parameter.
Run with: python3 scripts/test_pair_connect.py [ADDRESS]

Requires: pip install bleak bleak-retry-connector pycryptodome
"""

import asyncio
import logging
import sys
import os

# Add project root to path for protocol import
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bleak import BleakClient, BleakScanner
from bleak.exc import BleakError

logging.basicConfig(level=logging.DEBUG, format="%(asctime)s %(name)s %(levelname)s %(message)s")
_LOGGER = logging.getLogger("test_pair")

DEFAULT_ADDR = "F1:2C:72:3F:75:ED"
MELITTA_SERVICE_UUID = "0000ad00-b35c-11e4-9813-0002a5d5c51b"
CHAR_NOTIFY = "0000ad02-b35c-11e4-9813-0002a5d5c51b"
CHAR_WRITE = "0000ad01-b35c-11e4-9813-0002a5d5c51b"


async def test_with_retry_connector(device, name):
    """Test using bleak-retry-connector with pair=True."""
    _LOGGER.info("=== Test 1: establish_connection(pair=True) ===")
    try:
        from bleak_retry_connector import (
            BleakClientWithServiceCache,
            establish_connection,
        )

        def on_disconnect(client):
            _LOGGER.info("Disconnected callback fired")

        client = await establish_connection(
            BleakClientWithServiceCache,
            device,
            name,
            disconnected_callback=on_disconnect,
            use_services_cache=True,
            max_attempts=3,
            pair=True,
        )
        _LOGGER.info("Connected via establish_connection!")
        _LOGGER.info("  MTU: %d", client.mtu_size)
        _LOGGER.info("  Services: %d", len(list(client.services)))

        for service in client.services:
            if "ad00" in str(service.uuid):
                _LOGGER.info("  Melitta service found: %s", service.uuid)
                for char in service.characteristics:
                    _LOGGER.info("    Char: %s props=%s", char.uuid, char.properties)

        await client.disconnect()
        _LOGGER.info("Disconnected cleanly")
        return True

    except Exception as e:
        _LOGGER.error("establish_connection failed: %s: %s", type(e).__name__, e)
        return False


async def test_raw_bleak(device, name):
    """Test using raw BleakClient with pair=True."""
    _LOGGER.info("=== Test 2: BleakClient(pair=True) ===")
    try:
        client = BleakClient(device, pair=True, timeout=20.0)
        await client.connect()
        _LOGGER.info("Connected via raw BleakClient!")
        _LOGGER.info("  MTU: %d", client.mtu_size)
        _LOGGER.info("  Paired: checking...")

        # Try reading a characteristic
        for service in client.services:
            if "ad00" in str(service.uuid):
                _LOGGER.info("  Melitta service: %s", service.uuid)

        await client.disconnect()
        _LOGGER.info("Disconnected cleanly")
        return True

    except Exception as e:
        _LOGGER.error("Raw BleakClient failed: %s: %s", type(e).__name__, e)
        return False


async def main():
    address = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_ADDR
    _LOGGER.info("Target: %s", address)

    # Scan for device first (best practice: use BLEDevice, not address)
    _LOGGER.info("Scanning for device...")
    device = await BleakScanner.find_device_by_address(address, timeout=10.0)

    if device:
        _LOGGER.info("Found: %s (%s)", device.name, device.address)
    else:
        _LOGGER.warning("Device not found in scan, using raw address")
        device = address

    name = device.name if hasattr(device, "name") else address

    # Test 1: bleak-retry-connector
    ok1 = await test_with_retry_connector(device, name)

    await asyncio.sleep(2)

    # Test 2: raw BleakClient
    ok2 = await test_raw_bleak(device, name)

    print("\n" + "=" * 50)
    print(f"Results:")
    print(f"  establish_connection(pair=True): {'OK' if ok1 else 'FAIL'}")
    print(f"  BleakClient(pair=True):          {'OK' if ok2 else 'FAIL'}")


if __name__ == "__main__":
    asyncio.run(main())
