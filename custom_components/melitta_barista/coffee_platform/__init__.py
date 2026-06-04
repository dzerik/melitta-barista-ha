"""coffee_platform — transport-agnostic coffee-machine platform.

Defines the CoffeeMachineClient Protocol, the MachineRegistry, and the
shared domain vocabulary (MachineStatus, MachineCapabilities,
BrandProfile, descriptors). Self-contained — no imports back into
melitta_barista internals — so it can be lifted into a standalone
coffee-platform-ha repo.
"""

from __future__ import annotations

from .contract import CoffeeMachineClient
from .domain import (
    BrandProfile,
    FeatureNotSupported,
    InfoMessage,
    MachineCapabilities,
    MachineProcess,
    MachineStatus,
    Manipulation,
    RecipeDescriptor,
    RecipeFieldLayout,
    SettingDescriptor,
    StatDescriptor,
    SubProcess,
    supports_extension,
)
from .registry import MachineRegistry

__all__ = [
    "BrandProfile",
    "CoffeeMachineClient",
    "FeatureNotSupported",
    "InfoMessage",
    "MachineCapabilities",
    "MachineProcess",
    "MachineRegistry",
    "MachineStatus",
    "Manipulation",
    "RecipeDescriptor",
    "RecipeFieldLayout",
    "SettingDescriptor",
    "StatDescriptor",
    "SubProcess",
    "supports_extension",
]
