#!/usr/bin/env python3
"""Test BLE connection to Melitta through ESPHome BLE proxy.

Connects to ESPHome proxy via aioesphomeapi, finds Melitta via
BLE advertisements, then connects with GATT and attempts pairing.

Requires: pip install aioesphomeapi bleak pycryptodome
"""

import asyncio
import logging
import struct

from aioesphomeapi import APIClient

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)
_LOGGER = logging.getLogger("test_proxy")

PROXY_HOST = "ble-proxy-melitta.local"
PROXY_PORT = 6053
PROXY_NOISE_PSK = "WMZDLuzROKcw1GcFGsVT1saEEm8VaSoyUIprE0PsNi8="

MELITTA_ADDR_INT = 0xF12C723F75ED  # F1:2C:72:3F:75:ED
MELITTA_SERVICE_UUID = 0xAD00
CHAR_WRITE_HANDLE = None  # Will discover
CHAR_NOTIFY_HANDLE = None


def addr_to_str(addr_int):
    b = addr_int.to_bytes(6, "big")
    return ":".join(f"{x:02X}" for x in b)


async def main():
    cli = APIClient(PROXY_HOST, PROXY_PORT, password=None, noise_psk=PROXY_NOISE_PSK)
    await cli.connect(login=True)
    info = await cli.device_info()
    _LOGGER.info("Proxy: %s (ESPHome %s)", info.friendly_name, info.esphome_version)
    _LOGGER.info("BT proxy features: %d", info.bluetooth_proxy_feature_flags)

    # Step 1: Scan for Melitta
    _LOGGER.info("--- Step 1: Scanning for Melitta ---")
    melitta_found = asyncio.Event()
    melitta_addr = [None]

    def on_adv(adv):
        name = adv.name or ""
        if adv.address == MELITTA_ADDR_INT or "melitta" in name.lower() or "barista" in name.lower():
            _LOGGER.info(
                "MELITTA FOUND: %s (%s) RSSI=%d",
                name, addr_to_str(adv.address), adv.rssi,
            )
            melitta_addr[0] = adv.address
            melitta_found.set()

    cancel_adv = cli.subscribe_bluetooth_le_advertisements(on_adv)

    try:
        await asyncio.wait_for(melitta_found.wait(), timeout=20)
    except asyncio.TimeoutError:
        _LOGGER.error("Melitta not found in 20 seconds")
        cancel_adv()
        await cli.disconnect()
        return

    cancel_adv()
    address = melitta_addr[0]
    _LOGGER.info("Using address: %s (0x%012X)", addr_to_str(address), address)

    # Step 2: Connect via GATT
    _LOGGER.info("--- Step 2: GATT Connect ---")
    connected = asyncio.Event()
    disconnected = asyncio.Event()

    def on_connect(connected_flag, mtu, error):
        if error:
            _LOGGER.error("Connection error: %s", error)
            disconnected.set()
        else:
            _LOGGER.info("GATT connected! MTU=%d", mtu)
            connected.set()

    def on_disconnect():
        _LOGGER.info("GATT disconnected")
        disconnected.set()

    try:
        await cli.bluetooth_device_connect(
            address,
            on_bluetooth_connection_state=on_connect,
            timeout=30.0,
        )
    except Exception as e:
        _LOGGER.error("bluetooth_device_connect failed: %s: %s", type(e).__name__, e)
        await cli.disconnect()
        return

    # Wait for connection
    try:
        await asyncio.wait_for(connected.wait(), timeout=30)
    except asyncio.TimeoutError:
        _LOGGER.error("Connection timeout")
        await cli.disconnect()
        return

    # Step 3: Discover GATT services
    _LOGGER.info("--- Step 3: GATT Services ---")
    try:
        services = await cli.bluetooth_gatt_get_services(address)
        _LOGGER.info("Services count: %d", len(services.services))

        write_handle = None
        notify_handle = None

        for svc in services.services:
            uuid_hex = svc.uuid
            _LOGGER.info("  Service: %s (handles %d-%d)", uuid_hex, svc.handle, svc.handle)
            for char in svc.characteristics:
                props = char.properties
                _LOGGER.info("    Char: %s handle=%d props=%d", char.uuid, char.handle, props)
                uuid_low = char.uuid.lower()
                if "ad01" in uuid_low:
                    write_handle = char.handle
                    _LOGGER.info("    >>> WRITE char handle=%d", write_handle)
                elif "ad02" in uuid_low:
                    notify_handle = char.handle
                    _LOGGER.info("    >>> NOTIFY char handle=%d", notify_handle)

    except Exception as e:
        _LOGGER.error("Get services failed: %s: %s", type(e).__name__, e)

    # Step 4: Try pairing
    _LOGGER.info("--- Step 4: Pairing ---")
    try:
        await cli.bluetooth_device_pair(address)
        _LOGGER.info("Pairing succeeded!")
    except Exception as e:
        _LOGGER.warning("Pairing: %s: %s", type(e).__name__, e)

    # Step 5: Disconnect
    _LOGGER.info("--- Step 5: Disconnect ---")
    try:
        await cli.bluetooth_device_disconnect(address)
    except Exception:
        pass

    await cli.disconnect()
    _LOGGER.info("Done!")


if __name__ == "__main__":
    asyncio.run(main())
