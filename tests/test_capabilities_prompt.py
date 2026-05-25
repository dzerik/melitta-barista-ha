"""R4 — _build_prompt consumes LiveCapabilities and emits capability-driven enums."""

from __future__ import annotations

import pytest

from custom_components.melitta_barista.ai_recipes import _build_prompt
from custom_components.melitta_barista.capabilities import LiveCapabilities


def _make_caps(**overrides) -> LiveCapabilities:
    defaults = dict(
        schema_version=1,
        family_key="test_family",
        model_name="Test Machine",
        supported_processes=("coffee", "milk"),  # no water/none
        supported_intensities=("mild", "medium", "strong"),  # 3-level
        supported_aromas=("standard",),  # no intense
        supported_temperatures=("normal",),  # no cold/high
        supported_shots=("one", "two"),  # no none/three
        portion_limits={
            "coffee": {"min": 5, "max": 200, "step": 5},
            "milk": {"min": 10, "max": 200, "step": 5},
        },
        forbidden_combinations=(),
    )
    defaults.update(overrides)
    return LiveCapabilities(**defaults)


def _common_kwargs():
    return dict(
        mode="surprise_me",
        preference="",
        count=3,
        hopper1_bean=None,
        hopper2_bean=None,
        milk_types=[],
        extras={"syrups": [], "toppings": [], "liqueurs": []},
        ice_available=False,
        cup_size=None,
        temperature_pref=None,
        weather=None,
        moods=[],
        mood=None,
        occasion=None,
        dietary=[],
        caffeine_pref=None,
        servings=1,
        cups_today=0,
        intro=None,
        language=None,
        omit_output_format=True,
    )


def test_build_prompt_without_caps_keeps_legacy_universal_block():
    """When caps=None, the Machine Capabilities section enumerates the universal set."""
    prompt = _build_prompt(caps=None, **_common_kwargs())
    assert "Machine Capabilities" in prompt
    assert '"coffee"' in prompt
    assert '"milk"' in prompt
    assert '"water"' in prompt


def test_build_prompt_with_caps_emits_only_supported_processes():
    """When caps is provided, the section lists ONLY supported_processes."""
    caps = _make_caps()
    prompt = _build_prompt(caps=caps, **_common_kwargs())
    caps_section = prompt.split("## Machine Capabilities")[1].split("##")[0]
    assert '"coffee"' in caps_section
    assert '"milk"' in caps_section
    # water/none NOT supported → must not appear in capabilities section
    assert '"water"' not in caps_section
    assert '"none"' not in caps_section


def test_build_prompt_with_caps_lists_intensities_temperatures_aromas_shots():
    """All five enum dimensions follow capabilities."""
    caps = _make_caps()
    prompt = _build_prompt(caps=caps, **_common_kwargs())
    caps_section = prompt.split("## Machine Capabilities")[1].split("##")[0]
    assert '"mild"' in caps_section and '"medium"' in caps_section and '"strong"' in caps_section
    assert '"very_mild"' not in caps_section and '"very_strong"' not in caps_section
    assert '"standard"' in caps_section
    assert '"intense"' not in caps_section
    assert '"normal"' in caps_section
    assert '"cold"' not in caps_section and '"high"' not in caps_section
    assert '"one"' in caps_section and '"two"' in caps_section


def test_build_prompt_with_caps_emits_instruction_to_ignore_schema_extras():
    """A clear instruction tells LLM to prefer caps over JSON schema when they disagree."""
    caps = _make_caps()
    prompt = _build_prompt(caps=caps, **_common_kwargs())
    caps_section = prompt.split("## Machine Capabilities")[1].split("##")[0]
    section_lower = caps_section.lower()
    assert ("only the values listed" in section_lower
            or "ignore" in section_lower
            or "do not use" in section_lower), \
        f"Expected an override-instruction in caps section, got: {caps_section!r}"


def test_build_prompt_with_caps_uses_per_process_portion_range():
    """portion_ml range comes from caps.portion_limits when set."""
    caps = _make_caps(portion_limits={"coffee": {"min": 10, "max": 180, "step": 10}})
    prompt = _build_prompt(caps=caps, **_common_kwargs())
    caps_section = prompt.split("## Machine Capabilities")[1].split("##")[0]
    assert "10" in caps_section
    assert "180" in caps_section
