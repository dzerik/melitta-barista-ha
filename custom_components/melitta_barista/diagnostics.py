"""Diagnostics support — dumps runtime state for bug reports."""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .bond_state import BondStateMachine
from .coffee_platform.contract import CoffeeMachineClient
from .const import DOMAIN
from .event import lifecycle_detector_key
from .lifecycle import BrewIntent
from .scanner_reach import advertisement_only_scanner_names, async_scanner_sightings


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


def _redact_source(source: str | None) -> str | None:
    """Redact scanner MACs while leaving non-MAC source labels readable."""
    if source is None:
        return None
    if len(source) == 17 and source.count(":") == 5:
        return _redact_address(source)
    return source


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


def _brew_intent_summary(client: CoffeeMachineClient) -> dict[str, Any] | None:
    """Summarise the staged `BrewIntent` without leaking user-authored text.

    A staged intent is the answer to "why did the brew event say
    `source: machine`" — the two usual causes are nothing staged at all and an
    intent older than the correlation TTL, and both are visible here. Recipe
    and profile names are reported as booleans, never as strings: presence is
    the whole diagnostic value, and the string itself is user-authored free
    text this block has no reason to ship.

    That is a property of this block, not a policy of the module: the download
    still carries `profiles.names` verbatim (a profile name is what a support
    thread refers to a slot by), so a new field here is not automatically safe
    to add as a raw string — decide it on its own merits.
    """
    intent = getattr(client, "brew_intent", None)
    if not isinstance(intent, BrewIntent):
        return None
    components = intent.components or ()
    return {
        "recipe_source": intent.recipe_source,
        "recipe_key": intent.recipe_key,
        "profile": intent.profile,
        "two_cups": intent.two_cups,
        "slot": intent.slot,
        "component_count": len(components),
        "age_s": round(intent.age_s(), 1),
        "ha_cancelled": intent.ha_cancelled,
        "has_recipe_name": bool(intent.recipe_name),
        "has_profile_name": bool(intent.profile_name),
    }


def _narration_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry, client: CoffeeMachineClient,
) -> dict[str, Any]:
    """State of the server-side narration path and the lifecycle detector.

    Everything here is `getattr`-based and never raises: this block exists so a
    bug report answers "no `description` on the event" (cold or wrong-locale
    string cache) and "no event at all" (the detector's latches) without a live
    debugger, and a diagnostics download must stay possible even when one of
    those subsystems is exactly what is broken.
    """
    domain_data = hass.data.get(DOMAIN) or {}
    narration_strings = domain_data.get("narration_strings")
    ui_strings_cache = domain_data.get("ui_strings_cache") or {}
    detector = domain_data.get(lifecycle_detector_key(entry.entry_id))
    snapshot = getattr(detector, "state_snapshot", None)
    detector_state: dict[str, Any] | None = None
    if callable(snapshot):
        try:
            taken = snapshot()
        except Exception:  # noqa: BLE001 - diagnostics must stay downloadable
            taken = None
        # `isinstance` rather than trust: a test double stashed here would
        # otherwise put a non-serialisable object in the JSON download.
        detector_state = taken if isinstance(taken, dict) else None
    return {
        "locale": domain_data.get("narration_locale"),
        "narration_keys": (
            len(narration_strings) if isinstance(narration_strings, dict) else None
        ),
        "ui_strings_resolution": dict(
            domain_data.get("ui_strings_resolution") or {},
        ),
        "ui_strings_cached_locales": sorted(
            str(locale) for locale in ui_strings_cache
        ),
        "brew_intent": _brew_intent_summary(client),
        "detector": detector_state,
    }


def _bluetooth_reach(hass: HomeAssistant, address: str) -> dict[str, Any]:
    """Every scanner currently seeing the machine, with its connectability.

    Answers "the machine is visible but never connects" in one download: a
    list made only of ``connectable: false`` scanners (Shelly, SMLIGHT) means
    no route for the bonded GATT connection exists (issue #44). Scanner names
    are left out because remote-scanner names embed the scanner's MAC; the
    class name identifies the kind of scanner without it.
    """
    sightings = async_scanner_sightings(hass, address) if address else []
    return {
        "scanners": [
            {
                "source": _redact_source(sighting.source),
                "type": sighting.scanner_type,
                "connectable": sighting.connectable,
                "rssi": sighting.rssi,
            }
            for sighting in sightings
        ],
        "advertisement_only": bool(advertisement_only_scanner_names(sightings)),
    }


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

    # Recovery-layer state (0.88): the bond-op audit trail whose absence
    # made the earlier bond-wipe regressions take days to reconstruct.
    bond = getattr(client, "bond", None)
    recovery: dict[str, Any] = {
        "consecutive_connect_failures": getattr(
            client, "_consecutive_connect_failures", None,
        ),
        "last_failure_class": getattr(client, "_last_failure_class", None),
        "ble_link_seen": getattr(client, "_ble_link_seen", None),
        "auth_fail_seen": getattr(client, "_auth_fail_seen", None),
        "unpaired_this_episode": getattr(
            client, "_unpaired_this_episode", None,
        ),
    }
    if isinstance(bond, BondStateMachine):
        recovery["bond"] = bond.as_dict()

    seen_sources = getattr(client, "seen_ble_sources", {})
    if not isinstance(seen_sources, dict):
        # Test doubles and older runtime clients may not expose the new
        # mapping yet. Diagnostics must remain downloadable during upgrades.
        seen_sources = {}
    bluetooth_affinity = {
        "affinity_source": _redact_source(
            getattr(client, "ble_source_affinity", None),
        ),
        "current_device_source": _redact_source(
            getattr(client, "ble_device_source", None),
        ),
        "last_connected_source": _redact_source(
            getattr(client, "last_connected_source", None),
        ),
        "migration_pending": getattr(client, "source_migration_pending", False),
        "seen_sources": {
            _redact_source(source) or "unknown": last_seen
            for source, last_seen in seen_sources.items()
        },
    }

    return {
        "entry": {
            "title": entry.title,
            "address": redacted_address,
            "source": entry.source,
            "version": entry.version,
            "entry_id": entry.entry_id,
            "unique_id": _redact_unique_id(entry.unique_id),
        },
        "recovery": recovery,
        "bluetooth_affinity": bluetooth_affinity,
        "bluetooth_reach": _bluetooth_reach(hass, address),
        "narration": _narration_diagnostics(hass, entry, client),
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
