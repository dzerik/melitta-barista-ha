"""Diagnostics support — dumps runtime state for bug reports."""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .coffee_platform.contract import CoffeeMachineClient
from .const import DOMAIN


def _redact_address(address: str) -> str:
    """Mask the middle octets of a BLE MAC, e.g. ``C8:F4:**:**:**:**:43``.

    Deliberately partial (not ``**REDACTED**``): the visible first/last
    octets let entries be correlated in bug reports without exposing the
    full address.
    """
    return (
        f"{address[:5]}:**:**:**:**:{address[-2:]}"
        if len(address) >= 17 else "redacted"
    )


def _redact_unique_id(unique_id: str | None) -> str | None:
    """Mask a colon-less MAC unique_id consistently with ``_redact_address``.

    ``None`` passes through — the absence of a unique_id is itself a
    diagnostic signal (pre-dedup entries).
    """
    if unique_id is None:
        return None
    return (
        f"{unique_id[:4]}******{unique_id[-2:]}"
        if len(unique_id) == 12 else "redacted"
    )


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry,
) -> dict[str, Any]:
    """Return diagnostics for a config entry.

    Redaction policy: BLE addresses and unique_ids are partially masked
    (first and last octets visible); ``entry_id`` is an opaque random token
    and stays unredacted. All entries of the domain are listed in
    ``domain_entries`` so duplicate-entry situations (issue #10) are
    visible in a single diagnostics download.
    """
    client: CoffeeMachineClient = entry.runtime_data

    # Redact BLE address for privacy
    address = entry.data.get("address", "")
    redacted_address = _redact_address(address)

    # Frame logs for protocol-level diagnostics. _recent_frames captures raw
    # bytes on every BLE notification (pre-decryption). _frame_log on the
    # protocol object stores decoded payloads — useful for inspecting
    # unsolicited commands like HF / HQ / HP that the integration does not
    # currently decode.
    protocol = getattr(client, "_protocol", None)
    frame_log = list(getattr(protocol, "_frame_log", []))
    recent_frames = list(getattr(client, "_recent_frames", []))

    domain_entries = hass.config_entries.async_entries(DOMAIN)

    return {
        "entry": {
            "title": entry.title,
            "address": redacted_address,
            "source": entry.source,
            "version": entry.version,
            "entry_id": entry.entry_id,
            "unique_id": _redact_unique_id(entry.unique_id),
        },
        "domain_entries": {
            "count": len(domain_entries),
            "entries": [
                {
                    "entry_id": e.entry_id,
                    "unique_id": _redact_unique_id(e.unique_id),
                    "title": e.title,
                    "source": e.source,
                    "state": str(e.state),
                    "is_current": e.entry_id == entry.entry_id,
                }
                for e in domain_entries
            ],
        },
        "device": {
            "connected": client.connected,
            "firmware": client.firmware_version,
            "serial": client.serial_number,
            "features": str(client.features) if client.features is not None else None,
            "machine_type": str(client.machine_type) if client.machine_type else None,
            "model_name": client.model_name,
        },
        "status": {
            "process": str(client.status.process) if client.status else None,
            "sub_process": str(client.status.sub_process) if client.status else None,
            "progress": client.status.progress if client.status else None,
            "is_ready": client.status.is_ready if client.status else None,
        },
        "counters": {
            "total_cups": client.total_cups,
            "per_recipe": dict(client.cup_counters),
        },
        "profiles": {
            "count": len(client.profile_names),
            "active_profile": client.active_profile,
            "names": dict(client.profile_names),
        },
        "options": dict(entry.options),
        "ble_trace": {
            "recent_frames_raw": recent_frames,
            "frame_log_decoded": frame_log,
        },
    }
