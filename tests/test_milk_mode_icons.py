"""Drink icons must reflect preparation mode independently of temperature."""

import pytest

from custom_components.melitta_barista.protocol import RecipeComponent
from custom_components.melitta_barista.ui_contract import (
    build_icon_spec,
    component_to_tokens,
)


@pytest.mark.parametrize("temperature", [0, 1, 2])
@pytest.mark.parametrize("shots", [1, 2, 3])
def test_warm_milk_recipe_has_no_foam_at_any_temperature(shots, temperature):
    """A nonzero milk preparation byte must not draw a foam cap."""
    component = RecipeComponent(process=2, shots=shots, temperature=temperature, portion=20)
    spec = build_icon_spec([component_to_tokens(component)])
    assert spec["foam"] is None
    assert spec["layers"] == [
        {"role": "milk", "ml": 100, "fraction": 1.0, "intensity": 0.0},
    ]


@pytest.mark.parametrize("temperature", [0, 1, 2])
def test_pure_froth_recipe_uses_same_icon_at_any_temperature(temperature):
    """Changing temperature must not select or change milk preparation mode."""
    component = RecipeComponent(process=2, shots=0, temperature=temperature, portion=20)
    spec = build_icon_spec([component_to_tokens(component)])
    assert spec["foam"] == {"role": "milk_foam", "ml": 50, "fraction": 0.5}
    assert spec["total_ml"] == 100


@pytest.mark.parametrize(
    ("mode", "shots", "foam_ml"),
    [("warm_milk", "none", 0), ("milk_froth", "one", 50)],
)
def test_explicit_mode_controls_icon_before_serialization(mode, shots, foam_ml):
    """An explicit form choice takes precedence over remembered coffee shots."""
    spec = build_icon_spec([{
        "process": "milk", "portion_ml": 100, "temperature": "normal",
        "shots": shots, "milk_mode": mode,
    }])
    assert (spec["foam"]["ml"] if spec["foam"] else 0) == foam_ml


def test_mixed_warm_milk_and_froth_only_allocates_foam_from_froth():
    """Two milk stages retain their volumes and only the foamed stage contributes."""
    spec = build_icon_spec([
        {"process": "milk", "portion_ml": 100, "shots": "one", "temperature": "high"},
        {"process": "milk", "portion_ml": 50, "shots": "none", "temperature": "normal"},
    ])
    assert spec["foam"]["ml"] == 10
    assert [layer["ml"] for layer in spec["layers"]] == [100, 40]
    assert spec["total_ml"] == 150


def test_unknown_milk_mode_retains_legacy_visual_fallback():
    """Incomplete generic recipe descriptions still get their established icon."""
    spec = build_icon_spec([{"process": "milk", "portion_ml": 100, "temperature": "normal"}])
    assert spec["foam"]["ml"] == 20


@pytest.mark.parametrize("shots", [4, 255])
@pytest.mark.parametrize(("temperature", "foam_ml"), [(1, 20), (2, 50)])
def test_unknown_wire_preparation_preserves_legacy_icon(shots, temperature, foam_ml):
    """Normalizing an unknown byte to shots=none must not invent known froth."""
    component = RecipeComponent(process=2, shots=shots, temperature=temperature, portion=20)
    tokens = component_to_tokens(component)
    assert tokens["shots"] == "none"  # Existing public token default remains frozen.
    spec = build_icon_spec([tokens])
    assert spec["foam"]["ml"] == foam_ml


@pytest.mark.parametrize(("shots", "foam_ml"), [("one", 0), ("none", 50)])
def test_unrecognized_mode_string_still_uses_known_shots(shots, foam_ml):
    """A future mode string is ignored while existing raw semantics remain usable."""
    spec = build_icon_spec([{
        "process": "milk", "portion_ml": 100, "temperature": "normal",
        "shots": shots, "milk_mode": "future",
    }])
    assert (spec["foam"]["ml"] if spec["foam"] else 0) == foam_ml
