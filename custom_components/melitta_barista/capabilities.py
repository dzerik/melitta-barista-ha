"""Live machine capabilities — typed model + JSON serialization.

Static portion (per-family defaults) lives in brands/*.py. The dynamic
portion (what this machine actually supports right now, possibly extended
by future BLE probing) lives here and is cached in the sommelier DB
`machine_capabilities` table.

For P1a there is no real BLE probing — `derive_capabilities()` only reads
from the brand profile + const.py maps. The model itself is forward-
compatible with future probing-driven fields (portion_limits per process,
forbidden_combinations) which will be populated incrementally.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any

from .const import (
    AROMA_MAP,
    INTENSITY_MAP,
    PROCESS_MAP,
    SHOTS_MAP,
    TEMPERATURE_MAP,
)

_SUPPORTED_SCHEMA_VERSIONS = {1, 2}

# Global portion default for P1a — protocol-wide range from service schema.
# In future plans, per-process / per-family overrides will land here.
_DEFAULT_PORTION_LIMITS: dict[str, int] = {"min": 0, "max": 250, "step": 5}


@dataclass(frozen=True)
class LiveCapabilities:
    """Effective capabilities of a connected machine."""

    schema_version: int
    family_key: str
    model_name: str
    supported_processes: tuple[str, ...]
    supported_intensities: tuple[str, ...]
    supported_aromas: tuple[str, ...]
    supported_temperatures: tuple[str, ...]
    supported_shots: tuple[str, ...]
    portion_limits: dict[str, dict[str, int]] = field(default_factory=dict)
    forbidden_combinations: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    # schema v2 — brand-honest gate for Sommelier custom-recipe writes.
    # Melitta families use the freestyle slot via the HJ protocol; Nivona
    # families declare supports_recipe_writes=False because their recipe
    # protocol differs. v1 cached blobs default this to True so existing
    # Melitta installs see no change.
    supports_recipe_writes: bool = True

    def to_json(self) -> str:
        """Serialize to a JSON string suitable for the DB blob column."""
        return json.dumps(asdict(self), sort_keys=True)

    @classmethod
    def from_json(cls, blob: str) -> "LiveCapabilities":
        """Parse a JSON blob; raises ValueError on unsupported schema_version."""
        data = json.loads(blob)
        sv = data.get("schema_version")
        if sv not in _SUPPORTED_SCHEMA_VERSIONS:
            raise ValueError(
                f"unsupported capabilities schema_version={sv!r}; "
                f"expected one of {sorted(_SUPPORTED_SCHEMA_VERSIONS)}"
            )
        # Tuples come back as lists from JSON — coerce.
        # `supports_recipe_writes` was added in schema v2; v1 blobs predate
        # the Nivona-safe gate and were all Melitta installs, so the default
        # True keeps existing caches behaving exactly as before.
        return cls(
            schema_version=sv,
            family_key=data["family_key"],
            model_name=data["model_name"],
            supported_processes=tuple(data["supported_processes"]),
            supported_intensities=tuple(data["supported_intensities"]),
            supported_aromas=tuple(data["supported_aromas"]),
            supported_temperatures=tuple(data["supported_temperatures"]),
            supported_shots=tuple(data["supported_shots"]),
            portion_limits=dict(data.get("portion_limits", {})),
            forbidden_combinations=tuple(data.get("forbidden_combinations", ())),
            supports_recipe_writes=bool(data.get("supports_recipe_writes", True)),
        )


def derive_capabilities(client: Any) -> LiveCapabilities:
    """Build LiveCapabilities from the client's static brand profile + const maps.

    P1a: no live BLE probing — the builder returns the enumeration of
    everything the family declares it supports through MachineCapabilities
    (e.g. `strength_levels=5` -> all 5 intensity steps; `has_aroma_balance=False`
    -> only 'standard'). portion_limits gets a global default; per-process
    overrides are a stretch goal for P1b+.
    """
    caps = getattr(client, "capabilities", None)
    if caps is None:
        raise ValueError(
            "client has no capabilities (MachineCapabilities is None); "
            "cannot derive — connect first",
        )

    # Intensities: ordered enum list, sliced to the family's strength_levels.
    all_intensities = sorted(INTENSITY_MAP.keys(), key=lambda k: INTENSITY_MAP[k])
    if caps.strength_levels == 5:
        intensities = tuple(all_intensities)
    elif caps.strength_levels == 3:
        # Center three steps for 3-level machines (mild/medium/strong).
        intensities = tuple(all_intensities[1:4])
    else:
        # Unknown — fall back to the full set.
        intensities = tuple(all_intensities)

    # Aromas: full set if has_aroma_balance, else just 'standard'.
    if caps.has_aroma_balance:
        aromas = tuple(sorted(AROMA_MAP.keys(), key=lambda k: AROMA_MAP[k]))
    else:
        aromas = ("standard",)

    processes = tuple(sorted(PROCESS_MAP.keys(), key=lambda k: PROCESS_MAP[k]))
    temperatures = tuple(sorted(TEMPERATURE_MAP.keys(), key=lambda k: TEMPERATURE_MAP[k]))
    shots = tuple(sorted(SHOTS_MAP.keys(), key=lambda k: SHOTS_MAP[k]))

    portion_limits = {p: dict(_DEFAULT_PORTION_LIMITS) for p in processes if p != "none"}

    return LiveCapabilities(
        schema_version=2,
        family_key=caps.family_key,
        model_name=caps.model_name,
        supported_processes=processes,
        supported_intensities=intensities,
        supported_aromas=aromas,
        supported_temperatures=temperatures,
        supported_shots=shots,
        portion_limits=portion_limits,
        forbidden_combinations=(),
        supports_recipe_writes=bool(caps.supports_recipe_writes),
    )
