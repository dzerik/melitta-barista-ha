"""Diagnostics support for Melitta Barista Smart."""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .ble_client import MelittaBleClient

REDACT_KEYS = {"address", "unique_id"}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry,
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    client: MelittaBleClient = entry.runtime_data

    # Redact BLE address for privacy
    address = entry.data.get("address", "")
    redacted_address = (
        f"{address[:5]}:**:**:**:**:{address[-2:]}" if len(address) >= 17 else "redacted"
    )

    return {
        "entry": {
            "title": entry.title,
            "address": redacted_address,
            "source": entry.source,
            "version": entry.version,
        },
        "device": {
            "connected": client.connected,
            "firmware": client.firmware_version,
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
    }
