"""Which Bluetooth scanners see the machine, and whether any of them can connect.

The integration drives the machine over an active, bonded GATT connection.
Some Home Assistant Bluetooth scanners only forward advertisements — Shelly
and SMLIGHT devices are the common ones — so a machine seen exclusively
through them is visible yet unreachable. Home Assistant's own lookups default
to ``connectable=True`` and hide that situation entirely: the machine simply
never shows up, and pairing fails without saying why. This module surfaces it
for the setup flow and for diagnostics (issue #44).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from homeassistant.components import bluetooth
from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger("melitta_barista")


@dataclass(frozen=True, slots=True)
class ScannerSighting:
    """One scanner that currently holds an advertisement from the machine."""

    source: str
    name: str
    scanner_type: str
    connectable: bool
    rssi: int | None


def async_scanner_sightings(
    hass: HomeAssistant, address: str,
) -> list[ScannerSighting]:
    """Every scanner — connectable or not — that currently sees ``address``.

    Never raises: the Bluetooth manager may be absent or mid-reload, and both
    callers (a setup form, a diagnostics download) must keep working then.
    """
    try:
        scanner_devices = bluetooth.async_scanner_devices_by_address(
            hass, address, connectable=False,
        )
    except Exception:  # noqa: BLE001 — registry probing must never break callers
        _LOGGER.debug("Scanner sighting lookup failed", exc_info=True)
        return []

    sightings: list[ScannerSighting] = []
    for scanner_device in scanner_devices:
        scanner = scanner_device.scanner
        source = str(getattr(scanner, "source", "") or "")
        rssi = getattr(scanner_device.advertisement, "rssi", None)
        sightings.append(
            ScannerSighting(
                source=source,
                name=str(getattr(scanner, "name", None) or source),
                scanner_type=type(scanner).__name__,
                connectable=bool(getattr(scanner, "connectable", False)),
                rssi=rssi if isinstance(rssi, int) else None,
            )
        )
    return sightings


def advertisement_only_scanner_names(sightings: list[ScannerSighting]) -> list[str]:
    """Names of the scanners seeing the machine when none of them can connect.

    Empty when the machine is not seen at all (off, out of range — the normal
    pairing path reports that) or when at least one connectable scanner sees
    it, since Home Assistant routes connections through that one.
    """
    if not sightings or any(sighting.connectable for sighting in sightings):
        return []
    return sorted({sighting.name for sighting in sightings})
