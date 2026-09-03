"""Tests for the v2 action catalog (UI Contract §6.2, Zone I-E).

Pins the §6.2.2 catalog per brand, the §6.2.6 verified-maintenance
gating (issue #36), the schema-derived ActionParams, the §6.3.6
per-family `name_key` sets, and the §5.1 fingerprint invariants
(integration-version input + bridge-vs-document equality).
"""

from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

import voluptuous as vol

from custom_components.melitta_barista import (
    BREW_DIRECTKEY_SCHEMA,
    BREW_FREESTYLE_SCHEMA,
    RESET_RECIPE_SCHEMA,
    SAVE_DIRECTKEY_SCHEMA,
)
from custom_components.melitta_barista.brands.melitta import MelittaProfile
from custom_components.melitta_barista.brands.nivona import NivonaProfile
from custom_components.melitta_barista.brands.nivona._family_8000 import (
    RECIPES_8000_CHILLED,
)
from custom_components.melitta_barista.const import RecipeId, MachineType
from custom_components.melitta_barista.ui_contract import (
    MELITTA_RECIPE_NAME_KEYS,
    build_action_catalog,
    build_bridge_attributes,
    build_capabilities_block,
    build_ui_contract,
    compute_contract_fingerprint,
)


class FakeClient:
    """Duck-typed stand-in for CoffeeMachineClient (contract inputs only)."""

    def __init__(self, brand, capabilities, machine_type=None, connected=True,
                 integration_version="0.92.0"):
        self.brand = brand
        self.capabilities = capabilities
        self.machine_type = machine_type
        self.connected = connected
        self.status = None
        self.recipe_cache_generation = 0
        self.brand_logo_url = None
        self.integration_version = integration_version


def make_melitta_client(**kwargs):
    profile = MelittaProfile()
    return FakeClient(
        profile, profile.capabilities_for("barista_ts"),
        machine_type=MachineType.BARISTA_TS, **kwargs,
    )


def make_nivona_client(family="700", **kwargs):
    profile = NivonaProfile()
    return FakeClient(profile, profile.capabilities_for(family), **kwargs)


def make_entry(entry_id="a1b2c3d4e5f6"):
    return SimpleNamespace(entry_id=entry_id)


def catalog_for(client):
    return build_action_catalog(client, build_capabilities_block(client))


def by_action(catalog):
    return {entry["action"]: entry for entry in catalog}


_DIRECTKEY_TOKENS = [
    "espresso", "cafe_creme", "cappuccino",
    "latte_macchiato", "milk_froth", "milk", "water",
]

_FREESTYLE_PARAMS = [
    {"name": "params", "kind": "params_ref", "required": True,
     "ref": "freestyle"},
]
_DIRECTKEY_PARAMS = [
    {"name": "category", "kind": "enum", "required": True,
     "tokens": _DIRECTKEY_TOKENS},
    {"name": "two_cups", "kind": "bool", "required": False, "default": False},
]
_RESET_RECIPE_PARAMS = [
    {"name": "recipe_id", "kind": "int", "required": False,
     "ranges": [[200, 223], [302, 388]]},
]

_PROCESS_TOKENS = ["none", "coffee", "milk", "water"]
_INTENSITY_TOKENS = ["very_mild", "mild", "medium", "strong", "very_strong"]
_AROMA_TOKENS = ["standard", "intense"]
_TEMPERATURE_TOKENS = ["cold", "normal", "high"]
_SHOTS_TOKENS = ["none", "one", "two", "three"]

# §9.3.5 introspected save_directkey params, exact required/default
# flags: `category` required-no-default, `profile_id` optional-no-default
# (static [[0,8]] mirrors the schema; real slot counts live in
# `directkey.profiles`), `process1` Required-WITH-default (the marker
# asymmetry is mirrored, not normalized), the rest optional-with-default.
_SAVE_DIRECTKEY_PARAMS = [
    {"name": "category", "kind": "enum", "required": True,
     "tokens": _DIRECTKEY_TOKENS},
    {"name": "profile_id", "kind": "int", "required": False,
     "ranges": [[0, 8]]},
    {"name": "process1", "kind": "enum", "required": True,
     "tokens": _PROCESS_TOKENS, "default": "coffee"},
    {"name": "intensity1", "kind": "enum", "required": False,
     "tokens": _INTENSITY_TOKENS, "default": "medium"},
    {"name": "aroma1", "kind": "enum", "required": False,
     "tokens": _AROMA_TOKENS, "default": "standard"},
    {"name": "portion1_ml", "kind": "int", "required": False,
     "ranges": [[5, 250]], "default": 40},
    {"name": "temperature1", "kind": "enum", "required": False,
     "tokens": _TEMPERATURE_TOKENS, "default": "normal"},
    {"name": "shots1", "kind": "enum", "required": False,
     "tokens": _SHOTS_TOKENS, "default": "one"},
    {"name": "process2", "kind": "enum", "required": False,
     "tokens": _PROCESS_TOKENS, "default": "none"},
    {"name": "intensity2", "kind": "enum", "required": False,
     "tokens": _INTENSITY_TOKENS, "default": "medium"},
    {"name": "aroma2", "kind": "enum", "required": False,
     "tokens": _AROMA_TOKENS, "default": "standard"},
    {"name": "portion2_ml", "kind": "int", "required": False,
     "ranges": [[0, 250]], "default": 0},
    {"name": "temperature2", "kind": "enum", "required": False,
     "tokens": _TEMPERATURE_TOKENS, "default": "normal"},
    {"name": "shots2", "kind": "enum", "required": False,
     "tokens": _SHOTS_TOKENS, "default": "none"},
]


def _button(suffix):
    return {"kind": "button", "entity_suffix": suffix}


def _service(name, params):
    return {"kind": "service", "service": name, "entity_suffix": "brew",
            "params": params}


def _expected_catalog(*, freestyle, directkey, recipe_writes, factory_reset,
                      maintenance):
    """§6.2.2 table with per-family `available` truth injected."""
    return [
        {"action": "brew", "group": "brew", "process": "PRODUCT",
         "icon": "mdi:coffee", "confirm": False, "requires": ["ready"],
         "available": True, "invocation": _button("brew")},
        {"action": "brew_freestyle", "group": "brew", "process": "PRODUCT",
         "icon": "mdi:coffee-maker", "confirm": False, "requires": ["ready"],
         "available": freestyle,
         "invocation": _service("brew_freestyle", _FREESTYLE_PARAMS)},
        {"action": "brew_directkey", "group": "brew", "process": "PRODUCT",
         "icon": "mdi:gesture-tap-button", "confirm": False,
         "requires": ["ready"], "available": directkey,
         "invocation": _service("brew_directkey", _DIRECTKEY_PARAMS)},
        {"action": "cancel", "group": "control", "process": None,
         "icon": "mdi:stop", "confirm": False, "requires": ["connected"],
         "available": True, "invocation": _button("cancel")},
        {"action": "confirm_prompt", "group": "control", "process": None,
         "icon": "mdi:check-circle", "confirm": False,
         "requires": ["awaiting_confirmation"], "available": True,
         "invocation": _button("confirm_prompt")},
        {"action": "reset_recipe", "group": "control", "process": None,
         "icon": "mdi:restore", "confirm": True, "requires": ["ready"],
         "available": recipe_writes,
         "invocation": _service("reset_recipe", _RESET_RECIPE_PARAMS)},
        # §9.3.5: the 17th (0.93) entry — group `control`, HC-gated.
        {"action": "save_directkey", "group": "control", "process": None,
         "icon": "mdi:content-save", "confirm": True, "requires": ["ready"],
         "available": directkey,
         "invocation": _service("save_directkey", _SAVE_DIRECTKEY_PARAMS)},
        {"action": "easy_clean", "group": "cleaning", "process": "EASY_CLEAN",
         "icon": "mdi:shimmer", "confirm": True, "requires": ["ready"],
         "available": maintenance, "invocation": _button("easy_clean")},
        {"action": "intensive_clean", "group": "cleaning",
         "process": "INTENSIVE_CLEAN", "icon": "mdi:dishwasher",
         "confirm": True, "requires": ["ready"], "available": maintenance,
         "invocation": _button("intensive_clean")},
        {"action": "descaling", "group": "cleaning", "process": "DESCALING",
         "icon": "mdi:water-sync", "confirm": True, "requires": ["ready"],
         "available": maintenance, "invocation": _button("descaling")},
        {"action": "filter_insert", "group": "filter",
         "process": "FILTER_INSERT", "icon": "mdi:filter-plus",
         "confirm": False, "requires": ["ready"], "available": maintenance,
         "invocation": _button("filter_insert")},
        {"action": "filter_replace", "group": "filter",
         "process": "FILTER_REPLACE", "icon": "mdi:filter-cog",
         "confirm": False, "requires": ["ready"], "available": maintenance,
         "invocation": _button("filter_replace")},
        {"action": "filter_remove", "group": "filter",
         "process": "FILTER_REMOVE", "icon": "mdi:filter-remove",
         "confirm": False, "requires": ["ready"], "available": maintenance,
         "invocation": _button("filter_remove")},
        {"action": "evaporating", "group": "power", "process": "EVAPORATING",
         "icon": "mdi:air-humidifier", "confirm": True, "requires": ["ready"],
         "available": maintenance, "invocation": _button("evaporating")},
        {"action": "switch_off", "group": "power", "process": "SWITCH_OFF",
         "icon": "mdi:power", "confirm": True, "requires": ["connected"],
         "available": maintenance, "invocation": _button("switch_off")},
        {"action": "factory_reset_settings", "group": "danger",
         "process": None, "icon": "mdi:cog-refresh", "confirm": True,
         "destructive": True, "requires": ["ready"],
         "available": factory_reset,
         "invocation": _button("factory_reset_settings")},
        {"action": "factory_reset_recipes", "group": "danger",
         "process": None, "icon": "mdi:book-refresh", "confirm": True,
         "destructive": True, "requires": ["ready"],
         "available": factory_reset,
         "invocation": _button("factory_reset_recipes")},
    ]


# ---------------------------------------------------------------------------
# Full catalog per brand — pinned (§6.2.2)
# ---------------------------------------------------------------------------

def test_action_catalog_melitta_ts_pinned():
    assert catalog_for(make_melitta_client()) == _expected_catalog(
        freestyle=True, directkey=True, recipe_writes=True,
        factory_reset=False, maintenance=True,
    )


def test_action_catalog_nivona_700_pinned():
    """Family 700: every process-starting maintenance entry available:false
    (§6.2.6 — issue #36), HJ/HC/HD paths absent, factory reset present."""
    assert catalog_for(make_nivona_client("700")) == _expected_catalog(
        freestyle=False, directkey=False, recipe_writes=False,
        factory_reset=True, maintenance=False,
    )


def test_action_catalog_nivona_8000_factory_reset_unavailable():
    catalog = by_action(catalog_for(make_nivona_client("8000")))
    assert catalog["factory_reset_settings"]["available"] is False
    assert catalog["factory_reset_recipes"]["available"] is False
    assert catalog["factory_reset_settings"]["destructive"] is True
    assert catalog["factory_reset_recipes"]["destructive"] is True


def test_action_catalog_group_order_matches_spec():
    """§6.2.3 known group render order, in served order."""
    groups = []
    for entry in catalog_for(make_melitta_client()):
        if entry["group"] not in groups:
            groups.append(entry["group"])
    assert groups == ["brew", "control", "cleaning", "filter", "power",
                      "danger"]


# ---------------------------------------------------------------------------
# switch_off / requires semantics
# ---------------------------------------------------------------------------

def test_switch_off_requires_connected_not_ready():
    """PR #42 precedent as data: switch_off requires ["connected"] only."""
    for client in (make_melitta_client(), make_nivona_client()):
        entry = by_action(catalog_for(client))["switch_off"]
        assert entry["requires"] == ["connected"]


def test_only_factory_resets_are_destructive():
    for entry in catalog_for(make_melitta_client()):
        if entry["action"].startswith("factory_reset_"):
            assert entry.get("destructive") is True
            assert entry["confirm"] is True
        else:
            assert "destructive" not in entry


# ---------------------------------------------------------------------------
# §6.2.6 verified_maintenance_processes gating
# ---------------------------------------------------------------------------

_PROCESS_START_ACTIONS = {
    "easy_clean", "intensive_clean", "descaling", "filter_insert",
    "filter_replace", "filter_remove", "evaporating", "switch_off",
}


def test_all_nivona_families_ship_empty_verified_tuple():
    """b1 rule: every Nivona family ships verified_maintenance_processes=()."""
    for family_key, caps in NivonaProfile().families.items():
        assert caps.verified_maintenance_processes == (), family_key


def test_melitta_families_ship_verified_none():
    for family_key, caps in MelittaProfile().families.items():
        assert caps.verified_maintenance_processes is None, family_key


def test_every_nivona_family_serves_maintenance_unavailable():
    profile = NivonaProfile()
    for family_key in profile.families:
        catalog = by_action(catalog_for(make_nivona_client(family_key)))
        for action in _PROCESS_START_ACTIONS:
            assert catalog[action]["available"] is False, (family_key, action)
        # brew is NOT start_process-gated (§6.2.6) — stays available.
        assert catalog["brew"]["available"] is True, family_key


def test_partially_verified_tuple_flips_only_listed_processes():
    """A future #36 audit lands as data: only listed process ids flip."""
    client = make_nivona_client()
    client.capabilities = replace(
        client.capabilities,
        verified_maintenance_processes=(16, 17),  # SWITCH_OFF, EASY_CLEAN
    )
    catalog = by_action(catalog_for(client))
    assert catalog["switch_off"]["available"] is True
    assert catalog["easy_clean"]["available"] is True
    assert catalog["descaling"]["available"] is False
    assert catalog["evaporating"]["available"] is False


# ---------------------------------------------------------------------------
# ActionParams diffed against the live service schemas (§6.2.2)
# ---------------------------------------------------------------------------

def _schema_map(schema):
    return {str(marker): (marker, validator)
            for marker, validator in schema.schema.items()}


def _ranges_of(validator):
    if isinstance(validator, vol.Range):
        return [[int(validator.min), int(validator.max)]]
    ranges = []
    for child in getattr(validator, "validators", ()):
        ranges.extend(_ranges_of(child))
    return ranges


def test_directkey_params_match_live_schema():
    entry = by_action(catalog_for(make_melitta_client()))["brew_directkey"]
    params = {p["name"]: p for p in entry["invocation"]["params"]}
    schema = _schema_map(BREW_DIRECTKEY_SCHEMA)

    category_marker, category_validator = schema["category"]
    assert isinstance(category_marker, vol.Required)
    assert params["category"]["required"] is True
    assert params["category"]["tokens"] == list(category_validator.container)
    assert params["category"]["tokens"] == _DIRECTKEY_TOKENS

    two_cups_marker, _ = schema["two_cups"]
    assert not isinstance(two_cups_marker, vol.Required)
    assert params["two_cups"]["required"] is False
    assert params["two_cups"]["default"] == two_cups_marker.default() is False


def test_reset_recipe_params_match_live_schema():
    entry = by_action(catalog_for(make_melitta_client()))["reset_recipe"]
    params = {p["name"]: p for p in entry["invocation"]["params"]}
    marker, validator = _schema_map(RESET_RECIPE_SCHEMA)["recipe_id"]
    assert not isinstance(marker, vol.Required)
    assert params["recipe_id"]["required"] is False
    assert params["recipe_id"]["ranges"] == _ranges_of(validator)
    assert params["recipe_id"]["ranges"] == [[200, 223], [302, 388]]


def test_freestyle_params_ref_and_referenced_catalog_match_live_schema():
    """The params_ref form is pinned; the referenced `parameters` catalog
    (Melitta TS = the full const-map sets) is byte-equal to the enum sets
    and portion ranges the live BREW_FREESTYLE_SCHEMA accepts."""
    client = make_melitta_client()
    entry = by_action(catalog_for(client))["brew_freestyle"]
    assert entry["invocation"]["params"] == _FREESTYLE_PARAMS

    document = build_ui_contract(make_entry(), client)
    parameters = document["parameters"]
    schema = _schema_map(BREW_FREESTYLE_SCHEMA)

    for family, field in (
        ("process", "process1"), ("intensity", "intensity1"),
        ("aroma", "aroma1"), ("temperature", "temperature1"),
        ("shots", "shots1"), ("blend", "blend1"),
    ):
        container = schema[field][1].container
        assert parameters[family]["tokens"] == sorted(
            container, key=lambda key: container[key],
        ), family

    assert _ranges_of(schema["portion1_ml"][1]) == [[
        parameters["portion_ml"]["c1"]["min"],
        parameters["portion_ml"]["c1"]["max"],
    ]]
    assert _ranges_of(schema["portion2_ml"][1]) == [[
        parameters["portion_ml"]["c2"]["min"],
        parameters["portion_ml"]["c2"]["max"],
    ]]

    # The full freestyle form: 15 fields + entity_id, `name` omittable
    # with the voluptuous default "Custom".
    resolved = BREW_FREESTYLE_SCHEMA({"entity_id": "button.machine_brew"})
    assert resolved == {
        "entity_id": "button.machine_brew",
        "name": "Custom",
        "process1": "coffee", "intensity1": "medium", "aroma1": "standard",
        "portion1_ml": 40, "temperature1": "normal", "shots1": "one",
        "blend1": "hopper_1",
        "process2": "none", "intensity2": "medium", "aroma2": "standard",
        "portion2_ml": 0, "temperature2": "normal", "shots2": "none",
        "blend2": "hopper_1",
        "two_cups": False,
    }


def test_catalog_has_seventeen_entries():
    """§9.3.5: the 0.93 catalog is 17 entries for every brand."""
    for client in (make_melitta_client(), make_nivona_client()):
        assert len(catalog_for(client)) == 17


def test_save_directkey_entry_shape():
    """§9.3.5 table row: group control, process null, confirm yes,
    requires ["ready"], mdi:content-save, service anchored on `brew`."""
    entry = by_action(catalog_for(make_melitta_client()))["save_directkey"]
    assert entry["group"] == "control"
    assert entry["process"] is None
    assert entry["confirm"] is True
    assert "destructive" not in entry
    assert entry["requires"] == ["ready"]
    assert entry["icon"] == "mdi:content-save"
    assert entry["invocation"]["kind"] == "service"
    assert entry["invocation"]["service"] == "save_directkey"
    assert entry["invocation"]["entity_suffix"] == "brew"


def test_save_directkey_hc_gating():
    """`available` iff "HC" in supported_extensions (same gate as
    brew_directkey): Melitta true, Nivona false — entry still served."""
    assert by_action(catalog_for(make_melitta_client()))["save_directkey"][
        "available"] is True
    assert by_action(catalog_for(make_nivona_client()))["save_directkey"][
        "available"] is False


def test_save_directkey_params_match_live_schema():
    """§9.3.5: params byte-equal to SAVE_DIRECTKEY_SCHEMA, exact
    required/default flags — including the `process1`
    Required-with-default marker asymmetry (mirrored, not normalized) —
    and no blend / no two_cups fields anywhere."""
    entry = by_action(catalog_for(make_melitta_client()))["save_directkey"]
    served = entry["invocation"]["params"]
    assert served == _SAVE_DIRECTKEY_PARAMS

    schema = _schema_map(SAVE_DIRECTKEY_SCHEMA)
    schema_fields = [name for name in schema if name != "entity_id"]
    assert [p["name"] for p in served] == schema_fields

    by_name = {p["name"]: p for p in served}
    for name, (marker, validator) in schema.items():
        if name == "entity_id":
            continue
        param = by_name[name]
        assert param["required"] is isinstance(marker, vol.Required), name
        default = getattr(marker, "default", vol.UNDEFINED)
        if default is vol.UNDEFINED:
            assert "default" not in param, name
        else:
            assert param["default"] == default(), name
        if isinstance(validator, vol.In):
            assert param["kind"] == "enum"
            assert param["tokens"] == list(validator.container), name
        else:
            assert param["kind"] == "int"
            assert param["ranges"] == _ranges_of(validator), name

    # Marker asymmetry pinned: category required-no-default; process1
    # required WITH default; profile_id optional-no-default.
    assert by_name["category"] == {
        "name": "category", "kind": "enum", "required": True,
        "tokens": _DIRECTKEY_TOKENS,
    }
    assert by_name["process1"]["required"] is True
    assert by_name["process1"]["default"] == "coffee"
    assert by_name["profile_id"] == {
        "name": "profile_id", "kind": "int", "required": False,
        "ranges": [[0, 8]],
    }

    # No blend and no two_cups — neither in the schema nor served.
    assert not any(name.startswith("blend") for name in schema)
    assert "two_cups" not in schema
    assert not any(p["name"].startswith("blend") for p in served)
    assert not any(p["name"] == "two_cups" for p in served)


def test_every_service_entry_carries_entity_suffix():
    """§6.2.1 multi-machine targeting: service-kind entries MUST carry the
    anchor entity_suffix; button-kind entries carry their own suffix."""
    for client in (make_melitta_client(), make_nivona_client()):
        for entry in catalog_for(client):
            invocation = entry["invocation"]
            assert invocation["entity_suffix"], entry["action"]
            if invocation["kind"] == "service":
                assert invocation["entity_suffix"] == "brew"
            else:
                assert invocation["kind"] == "button"
                assert invocation["entity_suffix"] == entry["action"]


# ---------------------------------------------------------------------------
# Fingerprint invariants (§5.1 amendment)
# ---------------------------------------------------------------------------

def test_fingerprint_changes_on_integration_version_change():
    a = compute_contract_fingerprint(
        make_melitta_client(integration_version="0.92.0")
    )
    b = compute_contract_fingerprint(
        make_melitta_client(integration_version="0.92.1")
    )
    assert a != b


def test_fingerprint_changes_on_verified_maintenance_change():
    """Flipping an `available` flag in a release must churn the content
    revision (a catalog input is a fingerprint input)."""
    baseline = make_nivona_client()
    audited = make_nivona_client()
    audited.capabilities = replace(
        audited.capabilities, verified_maintenance_processes=(16,),
    )
    assert (
        compute_contract_fingerprint(baseline)
        != compute_contract_fingerprint(audited)
    )


def test_bridge_and_document_fingerprints_identical():
    """§5.1 single-source rule: both sync call sites are byte-identical."""
    for client in (make_melitta_client(), make_nivona_client()):
        entry = make_entry()
        bridge = build_bridge_attributes(entry, client)
        document = build_ui_contract(entry, client)
        assert bridge["contract_fingerprint"] == document["contract_fingerprint"]


def test_document_actions_match_builder_output():
    client = make_nivona_client()
    document = build_ui_contract(make_entry(), client)
    assert document["actions"] == build_action_catalog(
        client, build_capabilities_block(client),
    )


# ---------------------------------------------------------------------------
# name_key sets — pinned per family (§6.3.6)
# ---------------------------------------------------------------------------

_NIVONA_NAME_KEYS = {
    "600": {0: "espresso", 1: "coffee", 2: "americano", 3: "cappuccino",
            4: "frothy_milk", 5: "hot_water"},
    "700": {0: "espresso", 1: "cream", 2: "lungo", 3: "americano",
            4: "cappuccino", 5: "latte_macchiato", 6: "milk",
            7: "hot_water"},
    "79x": {0: "espresso", 1: "coffee", 2: "americano", 3: "cappuccino",
            5: "latte_macchiato", 6: "milk", 7: "hot_water"},
    "900": {0: "espresso", 1: "coffee", 2: "americano", 3: "cappuccino",
            4: "caffe_latte", 5: "latte_macchiato", 6: "hot_milk",
            7: "hot_water"},
    "900-light": {0: "espresso", 1: "coffee", 2: "americano",
                  3: "cappuccino", 4: "caffe_latte", 5: "latte_macchiato",
                  6: "hot_milk", 7: "hot_water"},
    "1030": {0: "espresso", 1: "coffee", 2: "americano", 3: "cappuccino",
             4: "caffe_latte", 5: "latte_macchiato", 6: "hot_water",
             7: "warm_milk", 8: "hot_milk", 9: "frothy_milk"},
    "1040": {0: "espresso", 1: "coffee", 2: "americano", 3: "cappuccino",
             4: "caffe_latte", 5: "latte_macchiato", 6: "hot_water",
             7: "warm_milk", 8: "frothy_milk"},
    "8000": {0: "espresso", 1: "coffee", 2: "americano", 3: "cappuccino",
             4: "caffe_latte", 5: "latte_macchiato", 6: "milk",
             7: "hot_water"},
}

_NIVONA_8000_CHILLED_NAME_KEYS = {
    **_NIVONA_NAME_KEYS["8000"],
    8: "chilled_espresso", 9: "chilled_lungo", 10: "chilled_americano",
}


def _assert_ascii_lower_snake(name_key):
    assert name_key
    assert name_key.isascii()
    assert name_key == name_key.lower()
    assert " " not in name_key


def test_nivona_name_key_sets_pinned_per_family():
    profile = NivonaProfile()
    assert set(profile.families) == set(_NIVONA_NAME_KEYS)
    for family_key, caps in profile.families.items():
        seeded = {d.recipe_id: d.name_key for d in caps.recipes}
        assert seeded == _NIVONA_NAME_KEYS[family_key], family_key
        for name_key in seeded.values():
            _assert_ascii_lower_snake(name_key)


def test_nivona_8000_chilled_variant_name_keys_pinned():
    seeded = {d.recipe_id: d.name_key for d in RECIPES_8000_CHILLED}
    assert seeded == _NIVONA_8000_CHILLED_NAME_KEYS
    for name_key in seeded.values():
        _assert_ascii_lower_snake(name_key)


def test_melitta_name_keys_cover_every_recipe_id():
    assert set(MELITTA_RECIPE_NAME_KEYS) == {int(r) for r in RecipeId}
    for name_key in MELITTA_RECIPE_NAME_KEYS.values():
        _assert_ascii_lower_snake(name_key)
    assert MELITTA_RECIPE_NAME_KEYS[int(RecipeId.CAFE_CREME)] == "cafe_creme"
    assert MELITTA_RECIPE_NAME_KEYS[int(RecipeId.WATER)] == "hot_water"


def test_document_recipes_carry_name_key_both_brands():
    melitta_doc = build_ui_contract(make_entry(), make_melitta_client())
    for recipe in melitta_doc["recipes"]:
        assert recipe["name_key"] == MELITTA_RECIPE_NAME_KEYS[recipe["recipe_id"]]

    nivona_doc = build_ui_contract(make_entry(), make_nivona_client())
    for recipe in nivona_doc["recipes"]:
        assert recipe["name_key"] == _NIVONA_NAME_KEYS["700"][recipe["recipe_id"]]
