"""BrandRegistry — central lookup for BrandProfile instances.

Profiles live in ``brands/<slug>.py`` and are instantiated once at module
import. Lookup is by slug (config-entry stored value) or by BLE
advertisement local_name (regex match).
"""

from __future__ import annotations

import logging

from .base import (
    BrandProfile,
    FeatureNotSupported,
    MachineCapabilities,
    RecipeDescriptor,
    SettingDescriptor,
    StatDescriptor,
    supports_extension,
)
from .melitta import MelittaProfile
from .nivona import NivonaProfile

_LOGGER = logging.getLogger("melitta_barista")


_PROFILES: dict[str, BrandProfile] = {
    MelittaProfile.brand_slug: MelittaProfile(),
    NivonaProfile.brand_slug: NivonaProfile(),
}


def get_profile(slug: str) -> BrandProfile:
    """Return the registered profile for ``slug``. Raises KeyError."""
    return _PROFILES[slug]


def all_profiles() -> dict[str, BrandProfile]:
    """All registered profiles (slug → instance). Used by config_flow."""
    return dict(_PROFILES)


def detect_from_advertisement(local_name: str | None) -> BrandProfile | None:
    """Return the matching BrandProfile for a BLE advertisement local_name,
    or None if no profile recognises it.
    """
    if not local_name:
        return None
    for profile in _PROFILES.values():
        if profile.ble_name_regex.match(local_name):
            return profile
    return None


__all__ = [
    "BrandProfile",
    "FeatureNotSupported",
    "MachineCapabilities",
    "MelittaProfile",
    "NivonaProfile",
    "RecipeDescriptor",
    "SettingDescriptor",
    "StatDescriptor",
    "all_profiles",
    "detect_from_advertisement",
    "get_profile",
    "supports_extension",
]
