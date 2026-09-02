"""Tests for the UI Contract v1 builder module (docs/UI_CONTRACT.md §7.1 Zone I-A)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from custom_components.melitta_barista.brands.base import MachineCapabilities
from custom_components.melitta_barista.brands.melitta import MelittaProfile
from custom_components.melitta_barista.brands.nivona import NivonaProfile
from custom_components.melitta_barista.coffee_platform.domain import (
    InfoMessage,
    MachineProcess,
    MachineStatus,
    Manipulation,
    SubProcess,
)
from custom_components.melitta_barista.const import (
    AROMA_MAP,
    BLEND_MAP,
    INTENSITY_MAP,
    MachineType,
    PROCESS_MAP,
    RECIPE_NAMES,
    RecipeId,
    SHOTS_MAP,
    TEMPERATURE_MAP,
)
from custom_components.melitta_barista.protocol import MachineRecipe, RecipeComponent
from custom_components.melitta_barista.ui_contract import (
    CONTRACT_VERSION,
    ICON_SPEC_VERSION,
    ContractNotReadyError,
    build_brand_theme,
    build_bridge_attributes,
    build_capabilities_block,
    build_icon_spec,
    build_status_tokens,
    build_ui_contract,
    build_vocabularies,
    component_to_tokens,
    compute_contract_fingerprint,
    icon_spec_for_category,
)


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

class FakeClient:
    """Duck-typed stand-in for CoffeeMachineClient (only contract inputs)."""

    def __init__(
        self,
        brand,
        capabilities,
        machine_type=None,
        connected=True,
        base_recipes=None,
        recipe_cache_generation=0,
        status=None,
        brand_logo_url=None,
    ):
        self.brand = brand
        self.capabilities = capabilities
        self.machine_type = machine_type
        self.connected = connected
        self.status = status
        if base_recipes is not None:
            self.base_recipes = base_recipes
        self.recipe_cache_generation = recipe_cache_generation
        self.brand_logo_url = brand_logo_url

    @property
    def model_name(self):
        if self.capabilities is not None:
            return self.capabilities.model_name
        return "Coffee Machine"


def make_melitta_client(**kwargs):
    profile = MelittaProfile()
    caps = profile.capabilities_for("barista_ts")
    kwargs.setdefault("machine_type", MachineType.BARISTA_TS)
    return FakeClient(profile, caps, **kwargs)


def make_nivona_client(family="700", **kwargs):
    profile = NivonaProfile()
    caps = profile.capabilities_for(family)
    return FakeClient(profile, caps, **kwargs)


def make_entry(entry_id="a1b2c3d4e5f6"):
    return SimpleNamespace(entry_id=entry_id)


# Token-level component dicts used across icon tests (spec §4.10 / §4.11).

ESPRESSO_C1 = {
    "process": "coffee", "intensity": "strong", "aroma": "standard",
    "temperature": "normal", "shots": "one", "portion_ml": 40,
}
LATTE_C1_MILK = {
    "process": "milk", "intensity": "medium", "aroma": "standard",
    "temperature": "normal", "shots": "none", "portion_ml": 160,
}
LATTE_C2_COFFEE = dict(ESPRESSO_C1)
CAPPUCCINO_C2_MILK = {
    "process": "milk", "intensity": "medium", "aroma": "standard",
    "temperature": "normal", "shots": "none", "portion_ml": 140,
}


# ---------------------------------------------------------------------------
# Version constants
# ---------------------------------------------------------------------------

def test_version_constants():
    assert CONTRACT_VERSION == 1
    assert ICON_SPEC_VERSION == 1


# ---------------------------------------------------------------------------
# Vocabulary completeness vs the real enums (§3.2)
# ---------------------------------------------------------------------------

def test_status_vocab_lists_match_enums():
    caps_block = build_capabilities_block(make_melitta_client())
    vocab = build_vocabularies(caps_block)
    assert vocab["status"]["process"] == [m.name for m in MachineProcess]
    assert vocab["status"]["sub_process"] == [m.name for m in SubProcess]
    assert vocab["status"]["manipulation"] == [m.name for m in Manipulation]
    assert vocab["status"]["info_message"] == [m.name for m in InfoMessage]


def test_status_vocab_known_v1_tokens_present():
    """The known-token lists in spec §3.2 are all covered by the enums."""
    caps_block = build_capabilities_block(make_melitta_client())
    vocab = build_vocabularies(caps_block)
    assert set(vocab["status"]["process"]) >= {
        "READY", "PRODUCT", "CLEANING", "DESCALING", "FILTER_INSERT",
        "FILTER_REPLACE", "FILTER_REMOVE", "SWITCH_OFF", "EASY_CLEAN",
        "INTENSIVE_CLEAN", "EVAPORATING", "BUSY",
    }
    assert set(vocab["status"]["sub_process"]) == {
        "GRINDING", "COFFEE", "STEAM", "WATER", "PREPARE",
    }
    assert set(vocab["status"]["manipulation"]) >= {"NONE", "MOVE_CUP_TO_FROTHER"}
    assert set(vocab["status"]["info_message"]) == {
        "FILL_BEANS_1", "FILL_BEANS_2", "EASY_CLEAN",
        "POWDER_FILLED", "PREPARATION_CANCELLED",
    }


def test_freestyle_vocab_tokens_come_from_const_maps():
    """Value tokens are byte-for-byte the const-map keys (§3.1), never invented."""
    caps_block = build_capabilities_block(make_melitta_client())
    vocab = build_vocabularies(caps_block)["freestyle"]
    assert vocab["process"] == sorted(PROCESS_MAP, key=PROCESS_MAP.get)
    assert vocab["intensity"] == sorted(INTENSITY_MAP, key=INTENSITY_MAP.get)
    assert vocab["aroma"] == sorted(AROMA_MAP, key=AROMA_MAP.get)
    assert vocab["temperature"] == sorted(TEMPERATURE_MAP, key=TEMPERATURE_MAP.get)
    assert vocab["shots"] == sorted(SHOTS_MAP, key=SHOTS_MAP.get)
    assert vocab["blend"] == sorted(BLEND_MAP, key=BLEND_MAP.get)
    for tokens in vocab.values():
        assert all(isinstance(t, str) for t in tokens)


def test_freestyle_vocab_nivona_subsets():
    caps_block = build_capabilities_block(make_nivona_client())
    vocab = build_vocabularies(caps_block)["freestyle"]
    all_intensities = sorted(INTENSITY_MAP, key=INTENSITY_MAP.get)
    assert vocab["intensity"] == all_intensities[1:4]  # 3-level middle steps
    assert vocab["blend"] == ["hopper_1"]
    assert vocab["process"] == sorted(PROCESS_MAP, key=PROCESS_MAP.get)


def test_freestyle_vocab_no_aroma_balance():
    caps = MachineCapabilities(
        family_key="x", model_name="X", strength_levels=5, has_aroma_balance=False,
    )
    client = FakeClient(MelittaProfile(), caps, machine_type=MachineType.BARISTA_TS)
    vocab = build_vocabularies(build_capabilities_block(client))["freestyle"]
    assert vocab["aroma"] == ["standard"]


# ---------------------------------------------------------------------------
# Capabilities block (§3.3 / §3.5)
# ---------------------------------------------------------------------------

def test_capabilities_block_melitta_ts():
    block = build_capabilities_block(make_melitta_client())
    assert block == {
        "supports_recipe_writes": True,
        "supports_stats": True,
        "supports_factory_reset": False,
        "supports_brew_overrides": False,
        "supports_freestyle": True,
        "my_coffee_slots": 8,
        "strength_levels": 5,
        "has_aroma_balance": True,
        "hopper_count": 2,
        "has_milk_system": True,
        "tolerated_brew_manipulations": [],
    }


def test_capabilities_block_nivona_700():
    block = build_capabilities_block(make_nivona_client())
    assert block == {
        "supports_recipe_writes": False,
        "supports_stats": True,
        "supports_factory_reset": True,
        "supports_brew_overrides": True,
        "supports_freestyle": False,
        "my_coffee_slots": 4,
        "strength_levels": 3,
        "has_aroma_balance": True,
        "hopper_count": 1,
        "has_milk_system": True,
        "tolerated_brew_manipulations": [],
    }


def test_hopper_count_melitta_unknown_machine_type_is_2():
    """§3.5: unknown/unrefined Melitta machine_type is treated as dual-hopper."""
    client = make_melitta_client(machine_type=None)
    block = build_capabilities_block(client)
    assert block["hopper_count"] == 2
    vocab = build_vocabularies(block)["freestyle"]
    assert vocab["blend"] == ["hopper_1", "hopper_2"]


def test_hopper_count_melitta_confirmed_t_is_1():
    profile = MelittaProfile()
    caps = profile.capabilities_for("barista_t")
    client = FakeClient(profile, caps, machine_type=MachineType.BARISTA_T)
    block = build_capabilities_block(client)
    assert block["hopper_count"] == 1
    assert build_vocabularies(block)["freestyle"]["blend"] == ["hopper_1"]


def test_tolerated_brew_manipulations_900_family():
    """§3.5: Nivona 900 (11,) serializes to ["MOVE_CUP_TO_FROTHER"]."""
    client = make_nivona_client(family="900")
    block = build_capabilities_block(client)
    assert block["tolerated_brew_manipulations"] == ["MOVE_CUP_TO_FROTHER"]


def test_tolerated_brew_manipulations_unknown_ints_omitted():
    caps = MachineCapabilities(
        family_key="x", model_name="X", tolerated_brew_manipulations=(11, 99, 20),
    )
    client = FakeClient(NivonaProfile(), caps)
    block = build_capabilities_block(client)
    assert block["tolerated_brew_manipulations"] == [
        "MOVE_CUP_TO_FROTHER", "FLUSH_REQUIRED",
    ]


def test_capabilities_block_requires_capabilities():
    client = FakeClient(MelittaProfile(), None)
    with pytest.raises(ContractNotReadyError):
        build_capabilities_block(client)


# ---------------------------------------------------------------------------
# Status tokens (§3.4 block B)
# ---------------------------------------------------------------------------

def test_status_tokens_brewing():
    status = MachineStatus(
        process=MachineProcess.PRODUCT,
        sub_process=SubProcess.GRINDING,
        manipulation=Manipulation.NONE,
    )
    tokens = build_status_tokens(status, True)
    assert tokens == {
        "process_token": "PRODUCT",
        "sub_process_token": "GRINDING",
        "manipulation_token": "NONE",
        "is_brewing": True,
        "awaiting_confirmation": False,
    }


def test_status_tokens_ready_idle():
    status = MachineStatus(process=MachineProcess.READY)
    tokens = build_status_tokens(status, True)
    assert tokens["process_token"] == "READY"
    assert tokens["sub_process_token"] is None  # null when idle
    assert tokens["manipulation_token"] == "NONE"
    assert tokens["is_brewing"] is False
    assert tokens["awaiting_confirmation"] is False


def test_status_tokens_null_iff_status_none():
    tokens = build_status_tokens(None, True)
    assert tokens == {
        "process_token": None,
        "sub_process_token": None,
        "manipulation_token": None,
        "is_brewing": False,
        "awaiting_confirmation": False,
    }


def test_status_tokens_unmapped_process_is_null():
    """Raw code unmapped -> process None -> token null; manipulation stays NONE."""
    status = MachineStatus(process=None, manipulation=Manipulation.NONE)
    tokens = build_status_tokens(status, True)
    assert tokens["process_token"] is None
    assert tokens["manipulation_token"] == "NONE"


def test_status_tokens_awaiting_confirmation():
    status = MachineStatus(
        process=MachineProcess.READY,
        manipulation=Manipulation.MOVE_CUP_TO_FROTHER,
    )
    tokens = build_status_tokens(status, True)
    assert tokens["manipulation_token"] == "MOVE_CUP_TO_FROTHER"
    assert tokens["awaiting_confirmation"] is True


def test_status_tokens_disconnected_treated_as_no_status():
    status = MachineStatus(process=MachineProcess.READY)
    tokens = build_status_tokens(status, False)
    assert tokens["process_token"] is None
    assert tokens["manipulation_token"] is None


# ---------------------------------------------------------------------------
# Bridge attributes + fingerprint (§3.4 block A / §5.1)
# ---------------------------------------------------------------------------

def test_bridge_attributes_shape():
    client = make_melitta_client()
    attrs = build_bridge_attributes(make_entry(), client)
    assert attrs["entry_id"] == "a1b2c3d4e5f6"
    assert attrs["contract_version"] == CONTRACT_VERSION
    assert attrs["connected"] is True
    fp = attrs["contract_fingerprint"]
    assert isinstance(fp, str) and len(fp) == 12
    int(fp, 16)  # short hex digest


def test_bridge_attributes_pre_handshake_omits_fingerprint():
    """§3.4: fingerprint may be absent only on a pre-handshake entry."""
    client = FakeClient(MelittaProfile(), None, connected=False)
    attrs = build_bridge_attributes(make_entry(), client)
    assert "contract_fingerprint" not in attrs
    assert attrs["entry_id"] == "a1b2c3d4e5f6"
    assert attrs["contract_version"] == CONTRACT_VERSION
    assert attrs["connected"] is False


def test_fingerprint_stable_for_same_inputs():
    a = compute_contract_fingerprint(make_melitta_client())
    b = compute_contract_fingerprint(make_melitta_client())
    assert a == b


def test_fingerprint_changes_on_machine_type_refinement():
    unknown = make_melitta_client(machine_type=None)
    confirmed = make_melitta_client(machine_type=MachineType.BARISTA_TS)
    assert compute_contract_fingerprint(unknown) != compute_contract_fingerprint(confirmed)


def test_fingerprint_changes_on_recipe_cache_generation():
    gen0 = make_melitta_client(recipe_cache_generation=0)
    gen1 = make_melitta_client(recipe_cache_generation=1)
    assert compute_contract_fingerprint(gen0) != compute_contract_fingerprint(gen1)


def test_fingerprint_none_without_capabilities():
    client = FakeClient(MelittaProfile(), None)
    assert compute_contract_fingerprint(client) is None


# ---------------------------------------------------------------------------
# component_to_tokens (§3.2 / §3.3)
# ---------------------------------------------------------------------------

def test_component_to_tokens_blend_byte_0_omitted():
    """Wire byte 0 (Blend.BARISTA_T) has no token: blend key OMITTED."""
    comp = RecipeComponent(process=1, shots=1, blend=0, intensity=3,
                           aroma=0, temperature=1, portion=8)
    tokens = component_to_tokens(comp)
    assert "blend" not in tokens
    assert tokens["process"] == "coffee"
    assert tokens["portion_ml"] == 40


def test_component_to_tokens_blend_bytes_1_and_2():
    c1 = RecipeComponent(process=1, blend=1, portion=8)
    c2 = RecipeComponent(process=1, blend=2, portion=8)
    assert component_to_tokens(c1)["blend"] == "hopper_1"
    assert component_to_tokens(c2)["blend"] == "hopper_2"


def test_component_to_tokens_unknown_blend_byte_omitted():
    comp = RecipeComponent(process=1, blend=7, portion=8)
    assert "blend" not in component_to_tokens(comp)


def test_component_to_tokens_full_mapping():
    comp = RecipeComponent(process=2, shots=0, blend=1, intensity=2,
                           aroma=1, temperature=2, portion=32)
    tokens = component_to_tokens(comp)
    assert tokens == {
        "process": "milk",
        "intensity": "medium",
        "aroma": "intense",
        "temperature": "high",
        "shots": "none",
        "portion_ml": 160,
        "blend": "hopper_1",
    }


# ---------------------------------------------------------------------------
# build_icon_spec — §4 rules
# ---------------------------------------------------------------------------

def test_icon_spec_worked_example_a_latte_macchiato():
    """§4.10 byte-exact: c1 milk 160 medium, c2 coffee 40 strong/one."""
    spec = build_icon_spec([LATTE_C1_MILK, LATTE_C2_COFFEE])
    assert spec == {
        "spec_version": 1,
        "glass": "tall_glass",
        "total_ml": 200,
        "fill_level": 0.63,
        "layers": [
            {"role": "milk", "ml": 130, "fraction": 0.65, "intensity": 0.0},
            {"role": "coffee", "ml": 40, "fraction": 0.20, "intensity": 0.68},
        ],
        "foam": {"role": "milk_foam", "ml": 30, "fraction": 0.15},
        "steam": True,
    }


def test_icon_spec_worked_example_b_cappuccino():
    """§4.11 byte-exact: c1 coffee 40 strong/one, c2 milk 140 medium."""
    spec = build_icon_spec([ESPRESSO_C1, CAPPUCCINO_C2_MILK])
    assert spec == {
        "spec_version": 1,
        "glass": "cup",
        "total_ml": 180,
        "fill_level": 0.82,
        "layers": [
            {"role": "coffee", "ml": 40, "fraction": 0.22, "intensity": 0.68},
            {"role": "milk", "ml": 110, "fraction": 0.61, "intensity": 0.0},
        ],
        "foam": {"role": "milk_foam", "ml": 30, "fraction": 0.17},
        "steam": True,
    }


def test_icon_spec_espresso_example():
    """§3.7 Espresso icon: crema on topmost coffee, espresso cup, fill 0.67."""
    spec = build_icon_spec([ESPRESSO_C1, None])
    assert spec == {
        "spec_version": 1,
        "glass": "espresso_cup",
        "total_ml": 40,
        "fill_level": 0.67,
        "layers": [
            {"role": "coffee", "ml": 40, "fraction": 1.0, "intensity": 0.68,
             "crema": True},
        ],
        "foam": None,
        "steam": True,
    }


def test_icon_spec_determinism():
    a = build_icon_spec([LATTE_C1_MILK, LATTE_C2_COFFEE])
    b = build_icon_spec([dict(LATTE_C1_MILK), dict(LATTE_C2_COFFEE)])
    assert a == b
    assert a is not b


def test_icon_spec_empty_composition_returns_none():
    assert build_icon_spec([]) is None
    assert build_icon_spec([None, None]) is None
    assert build_icon_spec([
        {"process": "none", "intensity": "medium", "aroma": "standard",
         "temperature": "normal", "shots": "none", "portion_ml": 40},
        {"process": "coffee", "intensity": "medium", "aroma": "standard",
         "temperature": "normal", "shots": "one", "portion_ml": 0},
    ]) is None


def test_icon_spec_fill_levels_partial_fill():
    """§4.2: ristretto 25 ml -> 0.42 espresso cup; lungo 110 ml -> 0.50 cup."""
    ristretto = build_icon_spec([dict(ESPRESSO_C1, portion_ml=25)])
    assert ristretto["glass"] == "espresso_cup"
    assert ristretto["fill_level"] == 0.42
    lungo = build_icon_spec([dict(ESPRESSO_C1, intensity="medium", portion_ml=110)])
    assert lungo["glass"] == "cup"
    assert lungo["fill_level"] == 0.50


def test_icon_spec_fill_level_caps_at_1():
    spec = build_icon_spec([{"process": "water", "intensity": "medium",
                             "aroma": "standard", "temperature": "high",
                             "shots": "none", "portion_ml": 400}])
    assert spec["glass"] == "tall_glass"
    assert spec["fill_level"] == 1.0


def test_icon_spec_coffee_darkness_scale():
    """§4.3 darkness examples: very_mild/1 -> 0.30; medium/1 -> 0.55;
    strong/1 -> 0.68; very_strong/3 -> 1.00; intense aroma +0.05."""
    def darkness(intensity, shots, aroma="standard"):
        spec = build_icon_spec([{
            "process": "coffee", "intensity": intensity, "aroma": aroma,
            "temperature": "normal", "shots": shots, "portion_ml": 40,
        }])
        return spec["layers"][0]["intensity"]

    assert darkness("very_mild", "one") == 0.30
    assert darkness("medium", "one") == 0.55   # const-map token for level 2
    assert darkness("normal", "one") == 0.55   # pre-amendment alias (A.1) for level 2
    assert darkness("strong", "one") == 0.68
    assert darkness("very_strong", "three") == 1.00
    assert darkness("strong", "one", aroma="intense") == 0.73


def test_icon_spec_milk_froth_dominant():
    """§4.4: sole milk component at high temperature -> foam_ratio 0.50."""
    spec = build_icon_spec([{
        "process": "milk", "intensity": "medium", "aroma": "standard",
        "temperature": "high", "shots": "none", "portion_ml": 100,
    }])
    assert spec["foam"] == {"role": "milk_foam", "ml": 50, "fraction": 0.5}
    assert spec["layers"] == [{"role": "milk", "ml": 50, "fraction": 0.5,
                               "intensity": 0.0}]
    assert spec["steam"] is False  # §4.7: no coffee component


def test_icon_spec_milk_foam_minimum_10ml():
    spec = build_icon_spec([{
        "process": "milk", "intensity": "medium", "aroma": "standard",
        "temperature": "normal", "shots": "none", "portion_ml": 30,
    }])
    assert spec["foam"]["ml"] == 10  # max(round5(6), 10)
    assert spec["total_ml"] == 30


def test_icon_spec_water_only_no_steam():
    """§4.5/§4.7: pure hot water -> cup, no foam, no steam."""
    spec = build_icon_spec([{
        "process": "water", "intensity": "medium", "aroma": "standard",
        "temperature": "high", "shots": "none", "portion_ml": 200,
    }])
    assert spec["glass"] == "cup"
    assert spec["foam"] is None
    assert spec["steam"] is False
    assert spec["layers"] == [{"role": "water", "ml": 200, "fraction": 1.0,
                               "intensity": 0.0}]


def test_icon_spec_cold_coffee_no_steam():
    spec = build_icon_spec([dict(ESPRESSO_C1, temperature="cold")])
    assert spec["steam"] is False


def test_icon_spec_fraction_sum_invariant():
    """§4.9: abs(1.0 - sum(fractions)) <= 0.02 for every generated spec."""
    cases = [
        [LATTE_C1_MILK, LATTE_C2_COFFEE],
        [ESPRESSO_C1, CAPPUCCINO_C2_MILK],
        [ESPRESSO_C1],
        [dict(ESPRESSO_C1, portion_ml=25)],
        [{"process": "milk", "intensity": "medium", "aroma": "standard",
          "temperature": "high", "shots": "none", "portion_ml": 100}],
        [{"process": "water", "intensity": "medium", "aroma": "standard",
          "temperature": "high", "shots": "none", "portion_ml": 200}],
        [dict(ESPRESSO_C1, portion_ml=35),
         {"process": "water", "intensity": "medium", "aroma": "standard",
          "temperature": "normal", "shots": "none", "portion_ml": 85}],
    ]
    for comps in cases:
        spec = build_icon_spec(comps)
        total = sum(layer["fraction"] for layer in spec["layers"])
        if spec["foam"] is not None:
            total += spec["foam"]["fraction"]
        assert abs(1.0 - total) <= 0.02, (comps, spec)


def test_icon_spec_additive_layers_and_color_hint():
    """§4.6: additives above components, below foam; color_hint normalized."""
    spec = build_icon_spec(
        [ESPRESSO_C1],
        additives=[
            {"name": "Vanilla Syrup", "ml": None, "color_hint": "#F3E5AB"},
            {"name": "Cinnamon", "ml": 5, "color_hint": "not-a-color"},
        ],
    )
    assert spec["glass"] == "espresso_cup"  # §4.6: glass from component ml only
    assert spec["total_ml"] == 55
    add1, add2 = spec["layers"][1], spec["layers"][2]
    assert add1 == {"role": "additive", "ml": 10, "fraction": 0.18,
                    "intensity": 0.5, "color_hint": "#f3e5ab",
                    "label": "Vanilla Syrup"}
    assert add2["color_hint"] is None
    assert add2["ml"] == 5
    # Coffee is no longer topmost -> no crema.
    assert "crema" not in spec["layers"][0]


def test_icon_spec_additive_below_foam():
    spec = build_icon_spec(
        [ESPRESSO_C1, CAPPUCCINO_C2_MILK],
        additives=[{"name": "Syrup", "ml": 10, "color_hint": None}],
    )
    roles = [layer["role"] for layer in spec["layers"]]
    assert roles == ["coffee", "milk", "additive"]
    assert spec["foam"] is not None  # foam stays topmost


def test_icon_spec_additives_only():
    spec = build_icon_spec([], additives=[{"name": "Syrup", "ml": 10,
                                           "color_hint": None}])
    assert spec is not None
    assert spec["steam"] is False
    assert spec["layers"][0]["role"] == "additive"


# ---------------------------------------------------------------------------
# Category-default compositions (§4.8)
# ---------------------------------------------------------------------------

def test_category_default_espresso():
    assert icon_spec_for_category("espresso") == build_icon_spec([{
        "process": "coffee", "intensity": "strong", "aroma": "standard",
        "temperature": "normal", "shots": "one", "portion_ml": 40,
    }])


def test_category_default_cappuccino_matches_spec_example():
    """§4.11: the milk_drink synthetic composition IS the §3.8 Cappuccino icon."""
    assert icon_spec_for_category("milk_drink") == {
        "spec_version": 1,
        "glass": "cup",
        "total_ml": 180,
        "fill_level": 0.82,
        "layers": [
            {"role": "coffee", "ml": 40, "fraction": 0.22, "intensity": 0.68},
            {"role": "milk", "ml": 110, "fraction": 0.61, "intensity": 0.0},
        ],
        "foam": {"role": "milk_foam", "ml": 30, "fraction": 0.17},
        "steam": True,
    }


def test_category_default_coffee():
    spec = icon_spec_for_category("coffee")
    assert spec["glass"] == "cup"
    assert spec["layers"][0]["intensity"] == 0.55  # normal, one shot
    assert spec["layers"][0]["ml"] == 120


def test_category_default_water():
    spec = icon_spec_for_category("water")
    assert spec["glass"] == "cup"
    assert spec["steam"] is False
    assert spec["foam"] is None


def test_category_default_unknown_returns_none():
    assert icon_spec_for_category("my_coffee") is None
    assert icon_spec_for_category("") is None
    assert icon_spec_for_category("cappuccino") is None  # not in §4.8 table
    assert icon_spec_for_category("something_else") is None


# ---------------------------------------------------------------------------
# build_brand_theme (§3.10)
# ---------------------------------------------------------------------------

def test_brand_theme_melitta_normative_values():
    """§3.10 per-brand table: melitta values byte-exact, logo_url null."""
    theme = build_brand_theme(make_melitta_client(), None)
    assert theme == {
        "brand": "melitta",
        "wordmark": "MELITTA",
        "accent": "#c8102e",
        "accent_soft": "#f6e3e6",
        "logo_url": None,
    }


def test_brand_theme_nivona_normative_values():
    """§3.10 per-brand table: nivona values byte-exact, logo_url null."""
    theme = build_brand_theme(make_nivona_client(), None)
    assert theme == {
        "brand": "nivona",
        "wordmark": "NIVONA",
        "accent": "#00646b",
        "accent_soft": "#e0eeef",
        "logo_url": None,
    }


def test_brand_theme_logo_url_passed_through_verbatim():
    """§3.10: the cached user-supplied logo URL is emitted exactly."""
    theme = build_brand_theme(
        make_melitta_client(), "/local/melitta_barista/melitta.png"
    )
    assert theme["logo_url"] == "/local/melitta_barista/melitta.png"


def test_brand_theme_unknown_brand_falls_back_to_neutral():
    """A slug missing from the §3.10 table gets a neutral, data-only badge."""
    fake_brand = SimpleNamespace(brand_slug="acme", brand_name="Acme")
    client = SimpleNamespace(brand=fake_brand)
    theme = build_brand_theme(client, None)
    assert theme["brand"] == "acme"
    assert theme["wordmark"] == "ACME"
    assert theme["logo_url"] is None
    # Neutral accents are still valid #rrggbb color data, never markup.
    import re
    for key in ("accent", "accent_soft"):
        assert re.fullmatch(r"#[0-9a-f]{6}", theme[key])


def test_fingerprint_changes_when_logo_appears():
    """§5.1: the brand-logo presence flag is a fingerprint input."""
    without = make_melitta_client()
    with_logo = make_melitta_client(
        brand_logo_url="/local/melitta_barista/melitta.png"
    )
    assert (
        compute_contract_fingerprint(without)
        != compute_contract_fingerprint(with_logo)
    )


def test_brand_theme_in_ws_document_after_machine_block():
    """§3.3: brand_theme is a top-level block placed after `machine`."""
    client = make_melitta_client()
    doc = build_ui_contract(make_entry(), client)
    assert doc["brand_theme"] == {
        "brand": "melitta",
        "wordmark": "MELITTA",
        "accent": "#c8102e",
        "accent_soft": "#f6e3e6",
        "logo_url": None,
    }
    keys = list(doc)
    assert keys.index("brand_theme") == keys.index("machine") + 1


def test_brand_theme_in_document_uses_cached_client_logo_url():
    """§3.10: the builder reads the setup-time cached logo_url (sync, no I/O)."""
    client = make_nivona_client(
        brand_logo_url="/local/melitta_barista/nivona.png"
    )
    doc = build_ui_contract(make_entry(), client)
    assert doc["brand_theme"]["logo_url"] == "/local/melitta_barista/nivona.png"


# ---------------------------------------------------------------------------
# build_ui_contract — full document (§3.3)
# ---------------------------------------------------------------------------

def _espresso_machine_recipe() -> MachineRecipe:
    return MachineRecipe(
        recipe_id=int(RecipeId.ESPRESSO),
        recipe_type=0,
        component1=RecipeComponent(process=1, shots=1, blend=1, intensity=3,
                                   aroma=0, temperature=1, portion=8),
        component2=RecipeComponent(process=0, shots=0, blend=0, intensity=2,
                                   aroma=0, temperature=1, portion=0),
    )


def test_build_ui_contract_raises_typed_error_without_capabilities():
    client = FakeClient(MelittaProfile(), None)
    with pytest.raises(ContractNotReadyError):
        build_ui_contract(make_entry(), client)


def test_build_ui_contract_melitta_document():
    client = make_melitta_client(
        base_recipes={int(RecipeId.ESPRESSO): _espresso_machine_recipe()},
        recipe_cache_generation=1,
    )
    doc = build_ui_contract(make_entry(), client)

    assert doc["contract_version"] == 1
    assert doc["contract_fingerprint"] == compute_contract_fingerprint(client)
    assert doc["entry_id"] == "a1b2c3d4e5f6"
    assert doc["source"] == "live"
    assert doc["generated_at"].endswith("Z")
    assert "schema_version" not in doc  # envelope added by _send_versioned

    assert doc["machine"] == {
        "brand": "melitta",
        "brand_name": "Melitta",
        "model_name": "Barista TS Smart",
        "family_key": "barista_ts",
        "machine_type": "BARISTA_TS",
        "connected": True,
    }
    assert doc["capabilities"]["hopper_count"] == 2
    assert doc["limits"] == {
        "portion_ml": {
            "c1": {"min": 5, "max": 250, "step": 5},
            "c2": {"min": 0, "max": 250, "step": 5},
        },
    }
    assert doc["status_attribute_entity"] == "state"
    assert doc["bridge_attribute_entity"] == "connection"

    # TS catalog: every RecipeId present, names from RECIPE_NAMES.
    by_id = {r["recipe_id"]: r for r in doc["recipes"]}
    assert set(by_id) == {int(r) for r in RecipeId}
    for rid, entry in by_id.items():
        assert entry["name"] == RECIPE_NAMES[rid]

    espresso = by_id[int(RecipeId.ESPRESSO)]
    assert espresso["category"] == "espresso"
    assert espresso["components"] == {
        "c1": {"process": "coffee", "intensity": "strong", "aroma": "standard",
               "temperature": "normal", "shots": "one", "portion_ml": 40,
               "blend": "hopper_1"},
        "c2": None,
    }
    assert espresso["icon"] == {
        "spec_version": 1,
        "glass": "espresso_cup",
        "total_ml": 40,
        "fill_level": 0.67,
        "layers": [
            {"role": "coffee", "ml": 40, "fraction": 1.0, "intensity": 0.68,
             "crema": True},
        ],
        "foam": None,
        "steam": True,
    }

    # Uncached recipe (preload incomplete): no components; category-default icon.
    latte = by_id[int(RecipeId.LATTE_MACCHIATO)]
    assert "components" not in latte
    assert latte["category"] == "milk_drink"
    assert latte["icon"] == icon_spec_for_category("milk_drink")


def test_build_ui_contract_melitta_t_catalog_excludes_ts_only():
    profile = MelittaProfile()
    caps = profile.capabilities_for("barista_t")
    client = FakeClient(profile, caps, machine_type=MachineType.BARISTA_T)
    doc = build_ui_contract(make_entry(), client)
    ids = {r["recipe_id"] for r in doc["recipes"]}
    assert int(RecipeId.RED_EYE) not in ids
    assert int(RecipeId.ESPRESSO) in ids


def test_build_ui_contract_nivona_document():
    client = make_nivona_client()
    doc = build_ui_contract(make_entry("f6e5d4c3b2a1"), client)

    assert doc["machine"]["brand"] == "nivona"
    assert doc["machine"]["brand_name"] == "Nivona"
    assert doc["machine"]["family_key"] == "700"
    assert doc["machine"]["machine_type"] is None
    assert doc["capabilities"]["hopper_count"] == 1
    assert doc["vocabularies"]["freestyle"]["blend"] == ["hopper_1"]

    # Descriptor-table catalog: no components blocks (§3.8 note).
    for recipe in doc["recipes"]:
        assert "components" not in recipe

    by_name = {r["name"]: r for r in doc["recipes"]}
    espresso = by_name["Espresso"]
    assert espresso["recipe_id"] == 0
    assert espresso["category"] == "espresso"
    assert espresso["icon"]["glass"] == "espresso_cup"
    assert espresso["icon"]["fill_level"] == 0.67
    water = by_name["Hot Water"]
    assert water["icon"]["steam"] is False
    latte = by_name["Latte Macchiato"]
    assert latte["category"] == "milk_drink"
    assert latte["icon"] == icon_spec_for_category("milk_drink")


def test_build_ui_contract_deterministic_except_timestamp():
    client = make_melitta_client()
    a = build_ui_contract(make_entry(), client)
    b = build_ui_contract(make_entry(), client)
    a.pop("generated_at")
    b.pop("generated_at")
    assert a == b
