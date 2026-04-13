"""Tests for ai_recipes.py — LLM prompt builder and response parser."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import patch

import pytest

from custom_components.melitta_barista.ai_recipes import (
    PORTION_MAX,
    PORTION_MIN,
    PORTION_STEP,
    _build_prompt,
    _clamp_portion,
    _extract_json,
    _validate_component,
    _validate_recipes,
)


# ── Fixtures & Helpers ────────────────────────────────────────────────


def _bean(
    brand: str = "Melitta",
    product: str = "BellaCrema Espresso",
    roast: str = "dark",
    bean_type: str = "arabica",
    origin: str = "blend",
    origin_country: str | None = "Brazil",
    flavor_notes: list[str] | None = None,
    composition: str | None = "100% Arabica",
) -> dict[str, Any]:
    return {
        "brand": brand,
        "product": product,
        "roast": roast,
        "bean_type": bean_type,
        "origin": origin,
        "origin_country": origin_country,
        "flavor_notes": ["chocolate", "spicy"] if flavor_notes is None else flavor_notes,
        "composition": composition,
    }


def _valid_recipe_json(count: int = 1) -> list[dict[str, Any]]:
    """Return a list of valid recipe dicts."""
    return [
        {
            "name": f"Recipe {i}",
            "description": f"Description {i}",
            "blend": 1,
            "component1": {
                "process": "coffee", "intensity": "strong", "aroma": "intense",
                "temperature": "normal", "shots": "two", "portion_ml": 30,
            },
            "component2": {
                "process": "milk", "intensity": "medium", "aroma": "standard",
                "temperature": "high", "shots": "none", "portion_ml": 120,
            },
        }
        for i in range(count)
    ]


# ── _build_prompt ─────────────────────────────────────────────────────


class TestBuildPrompt:
    """Test prompt construction for various input combinations."""

    def test_single_hopper(self):
        """Prompt includes hopper 1 bean description."""
        prompt = _build_prompt(
            hopper1_bean=_bean(), hopper2_bean=None,
            milk_types=["whole"], mode="custom",
            preference="strong", count=3,
        )
        assert "Hopper 1 (blend=1)" in prompt
        assert "Melitta BellaCrema Espresso" in prompt
        assert "Roast: dark" in prompt
        assert "chocolate, spicy" in prompt
        assert "Brazil" in prompt
        assert "100% Arabica" in prompt
        assert "Hopper 2" not in prompt
        assert "exactly 3 unique coffee recipes" in prompt

    def test_two_hoppers(self):
        """Prompt includes both hopper descriptions."""
        prompt = _build_prompt(
            hopper1_bean=_bean(product="Bean A"),
            hopper2_bean=_bean(product="Bean B"),
            milk_types=[], mode="custom", preference=None, count=2,
        )
        assert "Hopper 1 (blend=1)" in prompt
        assert "Bean A" in prompt
        assert "Hopper 2 (blend=0)" in prompt
        assert "Bean B" in prompt

    def test_no_hoppers(self):
        """Prompt indicates no beans configured."""
        prompt = _build_prompt(
            hopper1_bean=None, hopper2_bean=None,
            milk_types=[], mode="custom", preference=None, count=1,
        )
        assert "No beans configured" in prompt

    def test_with_milk(self):
        """Prompt lists available milk types."""
        prompt = _build_prompt(
            hopper1_bean=None, hopper2_bean=None,
            milk_types=["whole", "oat"], mode="custom",
            preference=None, count=1,
        )
        assert "Available milk: whole, oat" in prompt

    def test_no_milk(self):
        """Prompt indicates black coffee only when no milk."""
        prompt = _build_prompt(
            hopper1_bean=None, hopper2_bean=None,
            milk_types=[], mode="custom", preference=None, count=1,
        )
        assert "No milk available" in prompt
        assert "black coffee" in prompt

    def test_surprise_me_mode(self):
        """Surprise me mode has creative instruction."""
        prompt = _build_prompt(
            hopper1_bean=None, hopper2_bean=None,
            milk_types=[], mode="surprise_me", preference=None, count=3,
        )
        assert "SURPRISE ME" in prompt
        assert "creative" in prompt

    def test_custom_mode_with_preference(self):
        """Custom mode with preference includes user text."""
        prompt = _build_prompt(
            hopper1_bean=None, hopper2_bean=None,
            milk_types=[], mode="custom", preference="iced latte", count=1,
        )
        assert '"iced latte"' in prompt

    def test_custom_mode_no_preference(self):
        """Custom mode without preference indicates none given."""
        prompt = _build_prompt(
            hopper1_bean=None, hopper2_bean=None,
            milk_types=[], mode="custom", preference=None, count=1,
        )
        assert "No specific preference" in prompt

    def test_bean_without_optional_fields(self):
        """Bean without origin_country and composition still works."""
        prompt = _build_prompt(
            hopper1_bean=_bean(origin_country=None, composition=None, flavor_notes=[]),
            hopper2_bean=None,
            milk_types=[], mode="custom", preference=None, count=1,
        )
        assert "Melitta BellaCrema Espresso" in prompt
        assert "Brazil" not in prompt
        assert "Composition" not in prompt
        assert "Flavor notes" not in prompt

    @pytest.mark.parametrize("hour,expected_word", [
        (6, "morning"),
        (14, "afternoon"),
        (19, "evening"),
        (23, "night"),
        (3, "night"),
    ])
    def test_time_of_day_advice(self, hour, expected_word):
        """Time-of-day section changes based on hour."""
        from datetime import datetime, timezone
        from unittest.mock import patch

        mock_dt = datetime(2026, 3, 28, hour, 0, 0, tzinfo=timezone.utc)
        with patch("custom_components.melitta_barista.ai_recipes.datetime") as mock_datetime:
            mock_datetime.now.return_value = mock_dt
            mock_datetime.side_effect = lambda *a, **kw: datetime(*a, **kw)
            prompt = _build_prompt(
                hopper1_bean=None, hopper2_bean=None,
                milk_types=[], mode="custom", preference=None, count=1,
            )
        assert expected_word in prompt.lower()


# ── _extract_json ─────────────────────────────────────────────────────


class TestExtractJson:
    """Test JSON extraction from various LLM response formats."""

    def test_direct_json_array(self):
        """Direct JSON array is parsed."""
        data = _valid_recipe_json(2)
        result = _extract_json(json.dumps(data))
        assert len(result) == 2
        assert result[0]["name"] == "Recipe 0"

    def test_markdown_code_block(self):
        """JSON inside markdown code block is extracted."""
        data = _valid_recipe_json(1)
        text = f"Here are the recipes:\n```json\n{json.dumps(data)}\n```"
        result = _extract_json(text)
        assert len(result) == 1

    def test_markdown_code_block_no_lang(self):
        """Code block without language specifier works."""
        data = _valid_recipe_json(1)
        text = f"```\n{json.dumps(data)}\n```"
        result = _extract_json(text)
        assert len(result) == 1

    def test_array_embedded_in_text(self):
        """JSON array embedded in surrounding text is found via regex."""
        data = _valid_recipe_json(1)
        text = f"Sure! Here are your recipes: {json.dumps(data)} Enjoy!"
        result = _extract_json(text)
        assert len(result) == 1

    def test_invalid_json_raises(self):
        """Completely invalid input raises ValueError."""
        with pytest.raises(ValueError, match="Could not extract JSON"):
            _extract_json("This is not JSON at all")

    def test_json_object_not_array_raises(self):
        """A JSON object (not array) raises ValueError."""
        with pytest.raises(ValueError, match="Could not extract JSON"):
            _extract_json('{"key": "value"}')

    def test_empty_string_raises(self):
        """Empty string raises ValueError."""
        with pytest.raises(ValueError, match="Could not extract JSON"):
            _extract_json("")

    def test_whitespace_around_json(self):
        """Leading/trailing whitespace is handled."""
        data = _valid_recipe_json(1)
        result = _extract_json(f"  \n  {json.dumps(data)}  \n  ")
        assert len(result) == 1

    def test_empty_array(self):
        """Empty JSON array is returned as-is."""
        result = _extract_json("[]")
        assert result == []

    def test_broken_json_in_code_block_falls_through(self):
        """Broken JSON in code block tries regex fallback."""
        text = "```json\n{broken}\n```\n[{\"name\": \"Test\"}]"
        result = _extract_json(text)
        # regex fallback finds the array at the end
        assert len(result) == 1


# ── _clamp_portion ────────────────────────────────────────────────────


class TestClampPortion:
    """Test portion_ml clamping and rounding."""

    def test_valid_value(self):
        assert _clamp_portion(100) == 100

    def test_rounds_to_step(self):
        """Values are rounded to nearest PORTION_STEP."""
        assert _clamp_portion(37) == 35
        assert _clamp_portion(38) == 40
        assert _clamp_portion(42) == 40
        assert _clamp_portion(43) == 45

    def test_clamp_max(self):
        """Values above PORTION_MAX are clamped."""
        assert _clamp_portion(300) == PORTION_MAX

    def test_clamp_min_zero(self):
        """Zero is allowed (for 'none' process)."""
        assert _clamp_portion(0) == 0

    def test_negative_clamped_to_zero(self):
        """Negative values are clamped to 0."""
        assert _clamp_portion(-10) == 0

    def test_non_numeric_returns_default(self):
        """Non-numeric values return 40 (default)."""
        assert _clamp_portion("abc") == 40
        assert _clamp_portion(None) == 40

    def test_float_converted(self):
        """Float values are converted to int."""
        assert _clamp_portion(37.5) == 35

    def test_string_number(self):
        """String number is converted."""
        assert _clamp_portion("100") == 100

    def test_portion_max_exact(self):
        """Exact max value stays."""
        assert _clamp_portion(PORTION_MAX) == PORTION_MAX

    def test_portion_min_exact(self):
        """Exact min value stays."""
        assert _clamp_portion(PORTION_MIN) == PORTION_MIN


# ── _validate_component ──────────────────────────────────────────────


class TestValidateComponent:
    """Test recipe component validation and normalization."""

    def test_valid_coffee_component(self):
        """Valid coffee component passes through."""
        comp = {
            "process": "coffee", "intensity": "strong", "aroma": "intense",
            "temperature": "normal", "shots": "two", "portion_ml": 30,
        }
        result = _validate_component(comp, is_comp2=False)
        assert result == comp

    def test_valid_milk_component(self):
        """Valid milk component passes through."""
        comp = {
            "process": "milk", "intensity": "medium", "aroma": "standard",
            "temperature": "high", "shots": "none", "portion_ml": 120,
        }
        result = _validate_component(comp, is_comp2=True)
        assert result["process"] == "milk"

    def test_none_process_comp2(self):
        """Process 'none' for comp2 returns all defaults."""
        result = _validate_component({"process": "none"}, is_comp2=True)
        assert result == {
            "process": "none",
            "intensity": "medium",
            "aroma": "standard",
            "temperature": "normal",
            "shots": "none",
            "portion_ml": 0,
        }

    def test_invalid_process_comp1_defaults_to_coffee(self):
        """Invalid process for comp1 defaults to 'coffee'."""
        result = _validate_component({"process": "invalid"}, is_comp2=False)
        assert result["process"] == "coffee"

    def test_invalid_process_comp2_defaults_to_none(self):
        """Invalid process for comp2 defaults to 'none'."""
        result = _validate_component({"process": "invalid"}, is_comp2=True)
        assert result["process"] == "none"
        assert result["portion_ml"] == 0

    def test_none_process_not_allowed_comp1(self):
        """'none' is not valid for comp1; defaults to coffee."""
        result = _validate_component({"process": "none"}, is_comp2=False)
        assert result["process"] == "coffee"

    def test_invalid_intensity_defaults_to_medium(self):
        """Invalid intensity defaults to 'medium'."""
        result = _validate_component(
            {"process": "coffee", "intensity": "super_strong"},
            is_comp2=False,
        )
        assert result["intensity"] == "medium"

    def test_invalid_aroma_defaults_to_standard(self):
        result = _validate_component(
            {"process": "coffee", "aroma": "extra"},
            is_comp2=False,
        )
        assert result["aroma"] == "standard"

    def test_invalid_temperature_defaults_to_normal(self):
        result = _validate_component(
            {"process": "coffee", "temperature": "boiling"},
            is_comp2=False,
        )
        assert result["temperature"] == "normal"

    def test_invalid_shots_coffee_defaults_to_one(self):
        """Invalid shots for coffee process defaults to 'one'."""
        result = _validate_component(
            {"process": "coffee", "shots": "four"},
            is_comp2=False,
        )
        assert result["shots"] == "one"

    def test_invalid_shots_milk_defaults_to_none(self):
        """Invalid shots for milk process defaults to 'none'."""
        result = _validate_component(
            {"process": "milk", "shots": "four"},
            is_comp2=False,
        )
        assert result["shots"] == "none"

    def test_empty_component_defaults(self):
        """Empty dict gets full defaults for comp1."""
        result = _validate_component({}, is_comp2=False)
        assert result["process"] == "coffee"
        assert result["intensity"] == "medium"
        assert result["aroma"] == "standard"
        assert result["temperature"] == "normal"
        assert result["shots"] == "none"
        assert result["portion_ml"] == 40  # default for coffee

    def test_portion_clamped(self):
        """Portion is clamped to valid range."""
        result = _validate_component(
            {"process": "coffee", "portion_ml": 999},
            is_comp2=False,
        )
        assert result["portion_ml"] == PORTION_MAX

    def test_comp1_portion_at_least_min(self):
        """Comp1 portion is raised to PORTION_MIN if too small."""
        result = _validate_component(
            {"process": "coffee", "portion_ml": 1},
            is_comp2=False,
        )
        assert result["portion_ml"] == PORTION_MIN

    def test_comp2_portion_can_be_zero(self):
        """Comp2 portion can be 0 (for valid processes, after clamping)."""
        result = _validate_component(
            {"process": "water", "portion_ml": 0},
            is_comp2=True,
        )
        # 0 stays 0 for comp2 since the min check only applies to comp1
        assert result["portion_ml"] == 0

    @pytest.mark.parametrize("process", ["coffee", "milk", "water"])
    def test_all_valid_processes_comp1(self, process):
        """All valid comp1 processes are accepted."""
        result = _validate_component({"process": process}, is_comp2=False)
        assert result["process"] == process

    @pytest.mark.parametrize("process", ["coffee", "milk", "water", "none"])
    def test_all_valid_processes_comp2(self, process):
        """All valid comp2 processes (including 'none') are accepted."""
        result = _validate_component({"process": process}, is_comp2=True)
        assert result["process"] == process

    @pytest.mark.parametrize("intensity", [
        "very_mild", "mild", "medium", "strong", "very_strong",
    ])
    def test_all_valid_intensities(self, intensity):
        result = _validate_component(
            {"process": "coffee", "intensity": intensity}, is_comp2=False,
        )
        assert result["intensity"] == intensity

    @pytest.mark.parametrize("shots", ["none", "one", "two", "three"])
    def test_all_valid_shots(self, shots):
        result = _validate_component(
            {"process": "coffee", "shots": shots}, is_comp2=False,
        )
        assert result["shots"] == shots


# ── _validate_recipes ─────────────────────────────────────────────────


class TestValidateRecipes:
    """Test full recipe list validation."""

    def test_valid_recipes(self):
        """Valid recipes pass through with all fields."""
        raw = _valid_recipe_json(2)
        result = _validate_recipes(raw)
        assert len(result) == 2
        assert result[0]["name"] == "Recipe 0"
        assert result[0]["blend"] == 1
        assert isinstance(result[0]["component1"], dict)

    def test_non_dict_items_skipped(self):
        """Non-dict items in the list are skipped."""
        raw = [_valid_recipe_json(1)[0], "not a dict", 42, None]
        result = _validate_recipes(raw)
        assert len(result) == 1

    def test_empty_list(self):
        """Empty list returns empty."""
        assert _validate_recipes([]) == []

    def test_name_truncated(self):
        """Long name is truncated to 100 chars."""
        raw = _valid_recipe_json(1)
        raw[0]["name"] = "A" * 200
        result = _validate_recipes(raw)
        assert len(result[0]["name"]) == 100

    def test_description_truncated(self):
        """Long description is truncated to 500 chars."""
        raw = _valid_recipe_json(1)
        raw[0]["description"] = "B" * 1000
        result = _validate_recipes(raw)
        assert len(result[0]["description"]) == 500

    def test_missing_name_uses_default(self):
        """Missing name defaults to 'AI Recipe'."""
        raw = [{"blend": 1, "component1": {}, "component2": {}}]
        result = _validate_recipes(raw)
        assert result[0]["name"] == "AI Recipe"

    def test_invalid_blend_defaults_to_1(self):
        """Invalid blend value defaults to 1."""
        raw = _valid_recipe_json(1)
        raw[0]["blend"] = 5
        result = _validate_recipes(raw)
        assert result[0]["blend"] == 1

    def test_blend_zero_valid(self):
        """Blend 0 (hopper 2) is valid."""
        raw = _valid_recipe_json(1)
        raw[0]["blend"] = 0
        result = _validate_recipes(raw)
        assert result[0]["blend"] == 0

    def test_missing_components_get_defaults(self):
        """Missing component dicts get defaults."""
        raw = [{"name": "Bare", "description": "Minimal", "blend": 1}]
        result = _validate_recipes(raw)
        assert result[0]["component1"]["process"] == "coffee"
        assert result[0]["component2"]["process"] == "coffee"

    def test_components_are_validated(self):
        """Components go through _validate_component."""
        raw = _valid_recipe_json(1)
        raw[0]["component1"]["intensity"] = "ULTRA"  # invalid
        raw[0]["component2"]["process"] = "none"  # valid for comp2
        result = _validate_recipes(raw)
        assert result[0]["component1"]["intensity"] == "medium"
        assert result[0]["component2"]["process"] == "none"


# ── Coffee Presets JSON ───────────────────────────────────────────────


class TestCoffeePresetsJson:
    """Validate structure of the coffee_presets.json file."""

    @pytest.fixture
    def presets(self) -> list[dict[str, Any]]:
        import json
        from pathlib import Path
        path = Path(__file__).parent.parent / "custom_components" / "melitta_barista" / "coffee_presets.json"
        with open(path) as f:
            return json.load(f)

    def test_is_list(self, presets):
        """Presets file is a JSON array."""
        assert isinstance(presets, list)

    def test_not_empty(self, presets):
        """Presets file has entries."""
        assert len(presets) > 0

    REQUIRED_FIELDS = {"id", "brand", "product", "roast", "bean_type", "origin", "flavor_notes"}

    def test_all_presets_have_required_fields(self, presets):
        """Every preset has all required fields."""
        for i, preset in enumerate(presets):
            missing = self.REQUIRED_FIELDS - set(preset.keys())
            assert not missing, f"Preset {i} ({preset.get('id', '?')}) missing: {missing}"

    def test_ids_unique(self, presets):
        """All preset IDs are unique."""
        ids = [p["id"] for p in presets]
        assert len(ids) == len(set(ids)), f"Duplicate IDs: {[x for x in ids if ids.count(x) > 1]}"

    def test_flavor_notes_are_lists(self, presets):
        """Flavor notes are always lists of strings."""
        for preset in presets:
            notes = preset["flavor_notes"]
            assert isinstance(notes, list), f"Preset {preset['id']}: flavor_notes is not a list"
            for note in notes:
                assert isinstance(note, str), f"Preset {preset['id']}: flavor note {note!r} is not a string"

    def test_roast_values_valid(self, presets):
        """Roast values are from expected set."""
        valid_roasts = {"light", "medium_light", "medium", "medium_dark", "dark"}
        for preset in presets:
            assert preset["roast"] in valid_roasts, (
                f"Preset {preset['id']}: unexpected roast '{preset['roast']}'"
            )

    def test_bean_type_values_valid(self, presets):
        """Bean type values are from expected set."""
        valid_types = {"arabica", "robusta", "arabica_robusta"}
        for preset in presets:
            assert preset["bean_type"] in valid_types, (
                f"Preset {preset['id']}: unexpected bean_type '{preset['bean_type']}'"
            )


# ── Timeout behavior ──────────────────────────────────────────────────

class TestAsyncGenerateRecipesTimeout:
    """Verify LLM call wraps in asyncio.wait_for with timeout."""

    @pytest.mark.asyncio
    async def test_timeout_raises_runtime_error(self):
        """If conversation.process hangs past LLM_TIMEOUT, RuntimeError is raised."""
        import asyncio
        from unittest.mock import AsyncMock, MagicMock
        from custom_components.melitta_barista.ai_recipes import (
            async_generate_recipes,
        )

        hass = MagicMock()
        hass.services.async_call = AsyncMock(side_effect=asyncio.TimeoutError())

        with patch("custom_components.melitta_barista.ai_recipes.LLM_TIMEOUT", 0.01):
            with pytest.raises(RuntimeError, match="timed out"):
                await async_generate_recipes(
                    hass=hass,
                    hopper1_bean=None,
                    hopper2_bean=None,
                    milk_types=[],
                    mode="discovery",
                    preference=None,
                    count=1,
                    llm_agent=None,
                )
