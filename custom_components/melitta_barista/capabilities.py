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

_SUPPORTED_SCHEMA_VERSIONS = {1}


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
        )
