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
    def RequestConfirmation(self, device: "o", passkey: "u") -> None:  # noqa: F821
        _LOGGER.debug("Agent1.RequestConfirmation %s passkey=%s", device, passkey)

    @dbus_method()
    def RequestAuthorization(self, device: "o") -> None:  # noqa: F821
        _LOGGER.debug("Agent1.RequestAuthorization %s", device)

    @dbus_method()
    def AuthorizeService(self, device: "o", uuid: "s") -> None:  # noqa: F821
        _LOGGER.debug("Agent1.AuthorizeService %s uuid=%s", device, uuid)

    @dbus_method()
    def RequestPasskey(self, device: "o") -> "u":  # noqa: F821
        _LOGGER.debug("Agent1.RequestPasskey %s", device)
        return 0

    @dbus_method()
    def RequestPinCode(self, device: "o") -> "s":  # noqa: F821
        _LOGGER.debug("Agent1.RequestPinCode %s", device)
        return "0000"

    @dbus_method()
    def DisplayPasskey(self, device: "o", passkey: "u", entered: "q") -> None:  # noqa: F821
        _LOGGER.debug("Agent1.DisplayPasskey %s passkey=%s", device, passkey)

    @dbus_method()
    def DisplayPinCode(self, device: "o", pincode: "s") -> None:  # noqa: F821
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
        except Exception:  # noqa: BLE001 — D-Bus introspect raises various errors
            await asyncio.sleep(1)
    return False


async def _get_adapter(bus):
    """Get BlueZ Adapter1 interface, or None if not available."""
    try:
        introspection = await bus.introspect("org.bluez", "/org/bluez/hci0")
        proxy = bus.get_proxy_object("org.bluez", "/org/bluez/hci0", introspection)
        return proxy.get_interface("org.bluez.Adapter1")
    except Exception: # noqa: BLE001 — D-Bus introspect raises various errors
        return None


async def _register_agent(bus, agent):
    """Export agent and register it with BlueZ AgentManager1."""
    bus.export(_AGENT_PATH, agent)
    introspection = await bus.introspect("org.bluez", "/org/bluez")
    proxy = bus.get_proxy_object("org.bluez", "/org/bluez", introspection)
    agent_mgr = proxy.get_interface("org.bluez.AgentManager1")
    await agent_mgr.call_register_agent(_AGENT_PATH, "NoInputNoOutput")
    _LOGGER.debug("Agent registered at %s", _AGENT_PATH)
    try:
        await agent_mgr.call_request_default_agent(_AGENT_PATH)
    except Exception: # noqa: BLE001 — D-Bus introspect raises various errors
        _LOGGER.debug("RequestDefaultAgent failed (non-critical)")
    return agent_mgr


async def _check_already_paired(bus, device_path: str, address: str) -> bool | None:
    """Check if device is already paired. Returns True/False or None if unknown."""
    try:
        introspection = await bus.introspect("org.bluez", device_path)
        proxy = bus.get_proxy_object("org.bluez", device_path, introspection)
        props = proxy.get_interface("org.freedesktop.DBus.Properties")
        paired_var = await props.call_get("org.bluez.Device1", "Paired")
        if paired_var.value:
            _LOGGER.info("Device %s is already paired", address)
            return True
        return False
    except Exception: # noqa: BLE001 — D-Bus introspect raises various errors
        _LOGGER.debug("Device %s not known to BlueZ", address)
        return None


async def _discover_device(adapter, bus, device_path: str, address: str) -> bool:
    """Start discovery and wait for device to appear."""
    try:
        await adapter.call_start_discovery()
        _LOGGER.debug("Discovery started, waiting for %s", address)
    except Exception as ex:  # noqa: BLE001 — D-Bus StartDiscovery
        _LOGGER.debug("StartDiscovery failed: %s", ex)

    if not await _wait_for_device(bus, device_path, timeout=15.0):
        _LOGGER.error(
            "Device %s not found after discovery. "
            "Make sure it is powered on and in range.",
            address,
        )
        return False
    return True


async def _pair_and_trust(bus, device_path: str, address: str, timeout: float) -> str:
    """Perform pairing and set device as trusted. Returns result string."""
    introspection = await bus.introspect("org.bluez", device_path)
    proxy = bus.get_proxy_object("org.bluez", device_path, introspection)
    device_iface = proxy.get_interface("org.bluez.Device1")

    try:
        await asyncio.wait_for(device_iface.call_pair(), timeout=timeout)
    except asyncio.TimeoutError:
        _LOGGER.error("Pairing timeout for %s", address)
        return "pairing_timeout"
    except Exception as ex:  # noqa: BLE001 — D-Bus Pair() raises various errors
        if "AlreadyExists" in str(ex):
            _LOGGER.info("Device %s already paired", address)
        else:
            _LOGGER.error("Pairing failed for %s: %s", address, ex)
            return "pairing_failed"

    # Trust the device
    try:
        props = proxy.get_interface("org.freedesktop.DBus.Properties")
        await props.call_set("org.bluez.Device1", "Trusted", Variant("b", True))
        _LOGGER.debug("Device %s set as trusted", address)
    except Exception: # noqa: BLE001 — D-Bus introspect raises various errors
        _LOGGER.warning("Failed to set Trusted for %s", address)

    _LOGGER.info("Pairing with %s succeeded", address)
    return "ok"


async def _cleanup(bus, adapter, discovery_started: bool) -> None:
    """Clean up: stop discovery and unregister agent."""
    if discovery_started and adapter:
        try:
            await adapter.call_stop_discovery()
        except Exception:  # noqa: BLE001 — D-Bus cleanup
            pass
    try:
        introspection = await bus.introspect("org.bluez", "/org/bluez")
        proxy = bus.get_proxy_object("org.bluez", "/org/bluez", introspection)
        agent_mgr = proxy.get_interface("org.bluez.AgentManager1")
        await agent_mgr.call_unregister_agent(_AGENT_PATH)
    except Exception: # noqa: BLE001 — D-Bus introspect raises various errors
        pass
    try:
        bus.disconnect()
    except Exception: # noqa: BLE001 — D-Bus introspect raises various errors
        pass


async def async_get_paired_adapter_source(address: str) -> str | None:
    """Return the local BlueZ adapter MAC that owns an existing bond.

    Home Assistant may expose the same peer through a local adapter and many
    remote ESPHome scanners. For upgraded entries that pre-date source
    affinity, this lets us bootstrap the affinity from BlueZ's persisted
    ``Device1.Paired`` state instead of guessing from RSSI.

    Returns ``None`` when system D-Bus is unavailable, the peer is not paired
    locally, or the owning adapter address cannot be resolved. Remote proxy
    bonds are intentionally not guessed here; they are learned after a
    successful encrypted handshake.
    """
    bus = None
    try:
        bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
        introspection = await bus.introspect("org.bluez", "/")
        proxy = bus.get_proxy_object("org.bluez", "/", introspection)
        manager = proxy.get_interface("org.freedesktop.DBus.ObjectManager")
        objects = await manager.call_get_managed_objects()

        target = address.upper()

        def _value(value):
            return getattr(value, "value", value)

        for path, interfaces in objects.items():
            device = interfaces.get("org.bluez.Device1")
            if not device:
                continue
            device_address = _value(device.get("Address"))
            paired = bool(_value(device.get("Paired")))
            if not device_address or str(device_address).upper() != target or not paired:
                continue

            adapter_path = str(path).rsplit("/dev_", 1)[0]
            adapter = objects.get(adapter_path, {}).get("org.bluez.Adapter1", {})
            source = _value(adapter.get("Address"))
            if source:
                source = str(source).upper()
                _LOGGER.info(
                    "Existing BlueZ bond for %s belongs to local adapter %s",
                    address, source,
                )
                return source
            return None
    except Exception:  # noqa: BLE001 — D-Bus is optional for proxy-only installs
        _LOGGER.debug(
            "Could not determine local bond owner for %s", address, exc_info=True,
        )
        return None
    finally:
        if bus is not None:
            try:
                bus.disconnect()
            except Exception:  # noqa: BLE001 — best-effort cleanup
                pass

async def async_pair_device(address: str, timeout: float = 30.0) -> str:
    """Pair a BLE device via D-Bus BlueZ API with a registered Agent1.

    When no local BlueZ adapter is available (e.g. using ESPHome BLE proxy),
    pairing is skipped — the proxy handles BLE bonding at the ESP32 level.

    Returns: "ok", "pairing_failed", "pairing_timeout", or "cannot_connect".
    """
    device_path = "/org/bluez/hci0/dev_" + address.upper().replace(":", "_")
    bus = None
    discovery_started = False
    adapter = None

    try:
        try:
            bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
        except Exception:  # noqa: BLE001 — dbus_fast raises many shapes
            # System D-Bus not reachable. Two valid scenarios:
            #
            #   1. ESPHome BLE proxy in use — bonding handled at the
            #      ESP32 level, no host D-Bus needed. Continuing is
            #      correct here.
            #   2. Local BLE adapter via Docker / VPS where /run/dbus
            #      is not mounted into the container — bonding WILL
            #      fail later with "Authentication failed" or
            #      "No agent available" because BlueZ has no agent
            #      to talk to even though the adapter is there.
            #
            # We can't reliably tell the two apart from inside the
            # pairing function (would require HA's bluetooth scanner
            # registry). Continue optimistically; if Bleak's
            # `pair=True` later fails, the README troubleshooting
            # section ("Running HA in a Docker container") covers
            # the remediation. Reported in #14 / #15.
            _LOGGER.info(
                "System D-Bus not reachable for %s — skipping D-Bus pairing. "
                "If this is an ESPHome BLE proxy setup, this is expected. "
                "If using a local BLE adapter via Docker, ensure /run/dbus "
                "is mounted into the container and the host has `bluez` "
                "installed — see README troubleshooting section.",
                address,
            )
            return "ok"

        adapter = await _get_adapter(bus)
        if adapter is None:
            _LOGGER.info(
                "No functional BlueZ adapter (hci0) found. "
                "Assuming ESPHome BLE proxy — skipping D-Bus pairing for %s",
                address,
            )
            try:
                bus.disconnect()
            except Exception:  # noqa: BLE001 — D-Bus cleanup
                pass
            return "ok"

        agent = _NoInputOutputAgent()
        await _register_agent(bus, agent)

        # Check if already paired
        paired = await _check_already_paired(bus, device_path, address)
        if paired is True:
            return "ok"

        # If device not known, discover it
        if paired is None:
            if not await _discover_device(adapter, bus, device_path, address):
                return "cannot_connect"
            discovery_started = True

        # Pair and trust
        _LOGGER.info("Initiating D-Bus pairing with %s", address)
        return await _pair_and_trust(bus, device_path, address, timeout)

    except Exception: # noqa: BLE001 — D-Bus introspect raises various errors
        _LOGGER.exception("D-Bus pairing error for %s", address)
        return "pairing_failed"
    finally:
        if bus:
            await _cleanup(bus, adapter, discovery_started)
