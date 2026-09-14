"""Tests for scanner reachability: visible-but-unconnectable machines (issue #44)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from homeassistant.core import HomeAssistant

from custom_components.melitta_barista.scanner_reach import (
    ScannerSighting,
    advertisement_only_scanner_names,
    async_scanner_sightings,
)

from . import MOCK_ADDRESS

_LOOKUP = (
    "custom_components.melitta_barista.scanner_reach.bluetooth."
    "async_scanner_devices_by_address"
)


class ShellyBLEScanner:
    """Stand-in for an advertisement-only remote scanner."""

    def __init__(self, source: str, name: str) -> None:
        self.source = source
        self.name = name
        self.connectable = False


class HaScanner:
    """Stand-in for a connectable scanner (local adapter / ESPHome proxy)."""

    def __init__(self, source: str, name: str) -> None:
        self.source = source
        self.name = name
        self.connectable = True


def _scanner_device(scanner, rssi=-70):
    return SimpleNamespace(
        scanner=scanner, ble_device=object(),
        advertisement=SimpleNamespace(rssi=rssi),
    )


def _sighting(name: str, *, connectable: bool) -> ScannerSighting:
    return ScannerSighting(
        source=name, name=name, scanner_type="X",
        connectable=connectable, rssi=None,
    )


async def test_sightings_include_non_connectable_scanners(
    hass: HomeAssistant,
) -> None:
    """The lookup asks for every scanner, not only the connectable ones."""
    devices = [
        _scanner_device(ShellyBLEScanner("AA:00:00:00:00:01", "shelly-hall"), -60),
        _scanner_device(HaScanner("hci0", "hci0 (00:1A:7D:DA:71:13)"), None),
    ]
    with patch(_LOOKUP, return_value=devices) as lookup:
        sightings = async_scanner_sightings(hass, MOCK_ADDRESS)

    lookup.assert_called_once_with(hass, MOCK_ADDRESS, connectable=False)
    assert sightings == [
        ScannerSighting(
            source="AA:00:00:00:00:01", name="shelly-hall",
            scanner_type="ShellyBLEScanner", connectable=False, rssi=-60,
        ),
        ScannerSighting(
            source="hci0", name="hci0 (00:1A:7D:DA:71:13)",
            scanner_type="HaScanner", connectable=True, rssi=None,
        ),
    ]


async def test_sightings_survive_a_missing_bluetooth_manager(
    hass: HomeAssistant,
) -> None:
    """A registry that raises yields no sightings instead of an exception."""
    with patch(_LOOKUP, side_effect=RuntimeError("no bluetooth manager")):
        assert async_scanner_sightings(hass, MOCK_ADDRESS) == []


def test_advertisement_only_when_no_scanner_can_connect() -> None:
    """Only non-connectable sightings → their names, sorted and de-duplicated."""
    sightings = [
        _sighting("shelly-kitchen", connectable=False),
        _sighting("shelly-hall", connectable=False),
        _sighting("shelly-hall", connectable=False),
    ]
    assert advertisement_only_scanner_names(sightings) == [
        "shelly-hall", "shelly-kitchen",
    ]


def test_not_advertisement_only_when_a_connectable_scanner_sees_it() -> None:
    """One connectable route is enough — HA connects through that one."""
    sightings = [
        _sighting("shelly-hall", connectable=False),
        _sighting("esphome-kitchen", connectable=True),
    ]
    assert advertisement_only_scanner_names(sightings) == []


def test_not_advertisement_only_when_the_machine_is_not_seen() -> None:
    """An unseen machine is the ordinary off/out-of-range case, not this one."""
    assert advertisement_only_scanner_names([]) == []
