"""Tests for the LiveCapabilities dataclass."""

from __future__ import annotations

import json

import pytest

from custom_components.melitta_barista.capabilities import LiveCapabilities


def test_dataclass_defaults():
    """LiveCapabilities with all defaults is constructable and frozen."""
    cap = LiveCapabilities(
        schema_version=1,
        family_key="barista_ts",
        model_name="Melitta Barista TS Smart",
        supported_processes=("coffee", "milk", "water"),
        supported_intensities=("very_mild", "mild", "medium", "strong", "very_strong"),
        supported_aromas=("standard", "intense"),
        supported_temperatures=("cold", "normal", "high"),
        supported_shots=("none", "one", "two", "three"),
        portion_limits={"coffee": {"min": 5, "max": 250, "step": 5}},
        forbidden_combinations=(),
    )
    assert cap.family_key == "barista_ts"
    # frozen — assignment should raise
    with pytest.raises(Exception):
        cap.family_key = "x"  # type: ignore[misc]


def test_json_roundtrip():
    """LiveCapabilities.to_json() then from_json() reconstructs identical object."""
    original = LiveCapabilities(
        schema_version=1,
        family_key="700",
        model_name="Nivona 7xx",
        supported_processes=("coffee", "milk"),
        supported_intensities=("mild", "medium", "strong"),
        supported_aromas=("standard",),
        supported_temperatures=("normal",),
        supported_shots=("one", "two"),
        portion_limits={"coffee": {"min": 10, "max": 200, "step": 10}, "milk": {"min": 20, "max": 240, "step": 10}},
        forbidden_combinations=(),
    )
    blob = original.to_json()
    parsed = json.loads(blob)
    assert parsed["family_key"] == "700"
    restored = LiveCapabilities.from_json(blob)
    assert restored == original


def test_from_json_rejects_wrong_schema_version():
    """Reading a payload with unsupported schema_version raises ValueError."""
    blob = json.dumps({"schema_version": 99, "family_key": "x"})
    with pytest.raises(ValueError, match="schema_version"):
        LiveCapabilities.from_json(blob)


from unittest.mock import MagicMock

from custom_components.melitta_barista.brands.base import MachineCapabilities
from custom_components.melitta_barista.capabilities import derive_capabilities


def _make_client(family_key: str, model_name: str, strength_levels: int = 5,
                 has_aroma_balance: bool = True):
    """Build a mock client with a minimal MachineCapabilities attached."""
    caps = MachineCapabilities(
        family_key=family_key,
        model_name=model_name,
        supports_recipe_writes=True,
        supports_stats=True,
        my_coffee_slots=8,
        strength_levels=strength_levels,
        has_aroma_balance=has_aroma_balance,
        image_transfer=None,
        fluid_scale_factor=1,
        brew_command_mode=0x0B,
        recipe_text_encoding="utf16_le",
        tolerated_brew_manipulations=(),
        recipes=(),
        settings=(),
        stats=(),
    )
    client = MagicMock()
    client.capabilities = caps
    return client


def test_derive_for_melitta_barista_ts():
    """Melitta Barista TS has 5 intensities, both aromas, all temperatures, all process types."""
    client = _make_client("barista_ts", "Melitta Barista TS Smart", strength_levels=5)
    cap = derive_capabilities(client)

    assert cap.schema_version == 2
    assert cap.family_key == "barista_ts"
    assert cap.model_name == "Melitta Barista TS Smart"
    assert cap.supported_processes == ("none", "coffee", "milk", "water")
    assert cap.supported_intensities == (
        "very_mild", "mild", "medium", "strong", "very_strong",
    )
    assert cap.supported_aromas == ("standard", "intense")
    assert cap.supported_temperatures == ("cold", "normal", "high")
    assert cap.supported_shots == ("none", "one", "two", "three")
    # P1a: portion_limits filled with global default for every supported process
    assert "coffee" in cap.portion_limits
    assert cap.portion_limits["coffee"]["min"] >= 0
    assert cap.portion_limits["coffee"]["max"] <= 250
    # P1a: forbidden combinations not populated yet
    assert cap.forbidden_combinations == ()


def test_derive_for_3level_intensity_machine():
    """A family with strength_levels=3 reports the 3 middle intensities."""
    client = _make_client("nivona_short", "Nivona", strength_levels=3)
    cap = derive_capabilities(client)
    assert cap.supported_intensities == ("mild", "medium", "strong")


def test_derive_without_aroma_balance():
    """A family with has_aroma_balance=False reports only 'standard' aroma."""
    client = _make_client("nivona_basic", "Nivona basic", has_aroma_balance=False)
    cap = derive_capabilities(client)
    assert cap.supported_aromas == ("standard",)


def test_derive_raises_when_client_has_no_capabilities():
    """If client.capabilities is None, derive raises ValueError."""
    client = MagicMock()
    client.capabilities = None
    with pytest.raises(ValueError, match="capabilities"):
        derive_capabilities(client)
