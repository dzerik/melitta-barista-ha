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
