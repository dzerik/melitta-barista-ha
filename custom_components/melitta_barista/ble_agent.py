"""BlueZ D-Bus Agent1 for BLE pairing.

IMPORTANT: This module must NOT use 'from __future__ import annotations'
because dbus-fast inspects type annotations at class definition time
to determine D-Bus type signatures.
"""

import asyncio
import logging

from dbus_fast import BusType, Variant
from dbus_fast.aio import MessageBus
from dbus_fast.service import ServiceInterface, dbus_method

_LOGGER = logging.getLogger("melitta_barista")

_AGENT_PATH = "/melitta/pairing_agent"


class _NoInputOutputAgent(ServiceInterface):
    """BlueZ Agent1 for 'Just Works' BLE pairing (NoInputNoOutput)."""

    def __init__(self) -> None:
        super().__init__("org.bluez.Agent1")

    @dbus_method()
    def Release(self) -> None:
        _LOGGER.debug("Agent1.Release")

    @dbus_method()
    def RequestConfirmation(self, device: "o", passkey: "u") -> None:
        _LOGGER.debug("Agent1.RequestConfirmation %s passkey=%s", device, passkey)

    @dbus_method()
    def RequestAuthorization(self, device: "o") -> None:
        _LOGGER.debug("Agent1.RequestAuthorization %s", device)

    @dbus_method()
    def AuthorizeService(self, device: "o", uuid: "s") -> None:
        _LOGGER.debug("Agent1.AuthorizeService %s uuid=%s", device, uuid)

    @dbus_method()
    def RequestPasskey(self, device: "o") -> "u":
        _LOGGER.debug("Agent1.RequestPasskey %s", device)
        return 0

    @dbus_method()
    def RequestPinCode(self, device: "o") -> "s":
        _LOGGER.debug("Agent1.RequestPinCode %s", device)
        return "0000"

    @dbus_method()
    def DisplayPasskey(self, device: "o", passkey: "u", entered: "q") -> None:
        _LOGGER.debug("Agent1.DisplayPasskey %s passkey=%s", device, passkey)

    @dbus_method()
    def DisplayPinCode(self, device: "o", pincode: "s") -> None:
        _LOGGER.debug("Agent1.DisplayPinCode %s pin=%s", device, pincode)

    @dbus_method()
    def Cancel(self) -> None:
        _LOGGER.debug("Agent1.Cancel")


async def _wait_for_device(bus, device_path: str, timeout: float = 15.0) -> bool:
    """Wait for a BLE device to appear in BlueZ (via advertisements)."""
    for _ in range(int(timeout)):
        try:
            await bus.introspect("org.bluez", device_path)
            return True
        except Exception:
            await asyncio.sleep(1)
    return False


async def async_pair_device(address: str, timeout: float = 30.0) -> str:
    """Pair a BLE device via D-Bus BlueZ API with a registered Agent1.

    Returns: "ok", "pairing_failed", or "cannot_connect".
    """
    device_path = "/org/bluez/hci0/dev_" + address.upper().replace(":", "_")
    bus = None
    agent = _NoInputOutputAgent()
    discovery_started = False

    try:
        bus = await MessageBus(bus_type=BusType.SYSTEM).connect()

        # Export agent and register it with BlueZ
        bus.export(_AGENT_PATH, agent)

        introspection = await bus.introspect("org.bluez", "/org/bluez")
        proxy = bus.get_proxy_object("org.bluez", "/org/bluez", introspection)
        agent_mgr = proxy.get_interface("org.bluez.AgentManager1")

        await agent_mgr.call_register_agent(_AGENT_PATH, "NoInputNoOutput")
        _LOGGER.debug("Agent registered at %s", _AGENT_PATH)

        try:
            await agent_mgr.call_request_default_agent(_AGENT_PATH)
        except Exception:
            _LOGGER.debug("RequestDefaultAgent failed (non-critical)")

        # Get adapter interface
        adapter_introspection = await bus.introspect(
            "org.bluez", "/org/bluez/hci0"
        )
        adapter_proxy = bus.get_proxy_object(
            "org.bluez", "/org/bluez/hci0", adapter_introspection
        )
        adapter = adapter_proxy.get_interface("org.bluez.Adapter1")

        # Check if device is known to BlueZ
        device_known = False
        try:
            dev_introspection = await bus.introspect("org.bluez", device_path)
            dev_proxy = bus.get_proxy_object(
                "org.bluez", device_path, dev_introspection
            )
            dev_props = dev_proxy.get_interface(
                "org.freedesktop.DBus.Properties"
            )
            paired_var = await dev_props.call_get(
                "org.bluez.Device1", "Paired"
            )
            if paired_var.value:
                _LOGGER.info("Device %s is already paired", address)
                return "ok"
            device_known = True
        except Exception:
            _LOGGER.debug("Device %s not known to BlueZ, starting discovery", address)

        # If device not known, start discovery and wait for it
        if not device_known:
            try:
                await adapter.call_start_discovery()
                discovery_started = True
                _LOGGER.debug("Discovery started, waiting for %s", address)
            except Exception as ex:
                _LOGGER.debug("StartDiscovery failed: %s", ex)

            if not await _wait_for_device(bus, device_path, timeout=15.0):
                _LOGGER.error(
                    "Device %s not found after discovery. "
                    "Make sure it is powered on and in range.",
                    address,
                )
                return "cannot_connect"

        # Pair
        _LOGGER.info("Initiating D-Bus pairing with %s", address)
        dev_introspection = await bus.introspect("org.bluez", device_path)
        dev_proxy = bus.get_proxy_object(
            "org.bluez", device_path, dev_introspection
        )
        device_iface = dev_proxy.get_interface("org.bluez.Device1")

        try:
            await asyncio.wait_for(
                device_iface.call_pair(), timeout=timeout
            )
        except asyncio.TimeoutError:
            _LOGGER.error("Pairing timeout for %s", address)
            return "pairing_failed"
        except Exception as ex:
            if "AlreadyExists" in str(ex):
                _LOGGER.info("Device %s already paired", address)
            else:
                _LOGGER.error("Pairing failed for %s: %s", address, ex)
                return "pairing_failed"

        # Trust
        try:
            dev_props = dev_proxy.get_interface(
                "org.freedesktop.DBus.Properties"
            )
            await dev_props.call_set(
                "org.bluez.Device1", "Trusted", Variant("b", True)
            )
            _LOGGER.debug("Device %s set as trusted", address)
        except Exception:
            _LOGGER.warning("Failed to set Trusted for %s", address)

        _LOGGER.info("Pairing with %s succeeded", address)
        return "ok"

    except Exception:
        _LOGGER.exception("D-Bus pairing error for %s", address)
        return "pairing_failed"
    finally:
        if bus:
            if discovery_started:
                try:
                    adapter_introspection = await bus.introspect(
                        "org.bluez", "/org/bluez/hci0"
                    )
                    adapter_proxy = bus.get_proxy_object(
                        "org.bluez", "/org/bluez/hci0", adapter_introspection
                    )
                    adapter = adapter_proxy.get_interface("org.bluez.Adapter1")
                    await adapter.call_stop_discovery()
                except Exception:
                    pass
            try:
                introspection = await bus.introspect(
                    "org.bluez", "/org/bluez"
                )
                proxy = bus.get_proxy_object(
                    "org.bluez", "/org/bluez", introspection
                )
                agent_mgr = proxy.get_interface("org.bluez.AgentManager1")
                await agent_mgr.call_unregister_agent(_AGENT_PATH)
            except Exception:
                pass
            bus.disconnect()
