"""Tests for const.py — machine types, recipe filtering, settings."""

import pytest
from custom_components.melitta_barista.const import (
    BASE_RECIPES,
    BLE_PREFIXES_ALL,
    BLE_PREFIXES_T,
    BLE_PREFIXES_TS,
    MACHINE_MODEL_NAMES,
    MachineSettingId,
    MachineType,
    RecipeId,
    TS_ONLY_RECIPES,
    TS_ONLY_SETTINGS,
    detect_machine_type_from_name,
    get_available_recipes,
)


class TestMachineType:
    def test_type_ids(self):
        assert MachineType.BARISTA_T == 258
        assert MachineType.BARISTA_TS == 259

    def test_model_names(self):
        assert "T Smart" in MACHINE_MODEL_NAMES[MachineType.BARISTA_T]
        assert "TS Smart" in MACHINE_MODEL_NAMES[MachineType.BARISTA_TS]


class TestBLEPrefixes:
    def test_t_prefixes(self):
        assert BLE_PREFIXES_T == {"8301", "8311", "8401"}

    def test_ts_prefixes(self):
        assert BLE_PREFIXES_TS == {"8501", "8601", "8604"}

    def test_all_prefixes_union(self):
        assert BLE_PREFIXES_ALL == BLE_PREFIXES_T | BLE_PREFIXES_TS
        assert len(BLE_PREFIXES_ALL) == 6


class TestDetectMachineType:
    @pytest.mark.parametrize("prefix", ["8301", "8311", "8401"])
    def test_detect_barista_t(self, prefix: str):
        assert detect_machine_type_from_name(f"{prefix}ABCD1234") == MachineType.BARISTA_T

    @pytest.mark.parametrize("prefix", ["8501", "8601", "8604"])
    def test_detect_barista_ts(self, prefix: str):
        assert detect_machine_type_from_name(f"{prefix}ABCD1234") == MachineType.BARISTA_TS

    def test_unknown_device_returns_none(self):
        assert detect_machine_type_from_name("UnknownDevice") is None
        assert detect_machine_type_from_name("") is None

    def test_partial_prefix_no_match(self):
        assert detect_machine_type_from_name("830") is None
        assert detect_machine_type_from_name("860") is None


class TestRecipeFiltering:
    def test_total_recipes(self):
        assert len(list(RecipeId)) == 24

    def test_ts_only_recipes(self):
        assert TS_ONLY_RECIPES == {RecipeId.RED_EYE, RecipeId.BLACK_EYE, RecipeId.DEAD_EYE}

    def test_base_recipes_count(self):
        assert len(BASE_RECIPES) == 21

    def test_base_recipes_exclude_ts_only(self):
        for recipe in TS_ONLY_RECIPES:
            assert recipe not in BASE_RECIPES

    def test_get_available_recipes_ts(self):
        recipes = get_available_recipes(MachineType.BARISTA_TS)
        assert len(recipes) == 24
        assert RecipeId.RED_EYE in recipes

    def test_get_available_recipes_t(self):
        recipes = get_available_recipes(MachineType.BARISTA_T)
        assert len(recipes) == 21
        assert RecipeId.RED_EYE not in recipes
        assert RecipeId.BLACK_EYE not in recipes
        assert RecipeId.DEAD_EYE not in recipes

    def test_get_available_recipes_none_returns_all(self):
        recipes = get_available_recipes(None)
        assert len(recipes) == 24

    def test_recipe_id_range(self):
        for recipe in RecipeId:
            assert 200 <= recipe <= 223


class TestSettingsFiltering:
    def test_ts_only_settings(self):
        assert MachineSettingId.AUTO_BEAN_SELECT in TS_ONLY_SETTINGS

    def test_ts_only_settings_count(self):
        assert len(TS_ONLY_SETTINGS) == 1
