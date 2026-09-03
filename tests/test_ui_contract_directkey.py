"""Tests for the v3 DirectKey/profile model block (UI Contract §9.3, Zone I-J).

Pins the §9.3.3 example payload verbatim, the HC presence gate, the
§9.3.1 machine-button truth per machine type (unknown follows the TS
row), the profile-slot model, and the three-way category-token
consistency (contract == service schema == i18n key set).
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from custom_components.melitta_barista import _DIRECTKEY_CATEGORIES
from custom_components.melitta_barista.brands.melitta import MelittaProfile
from custom_components.melitta_barista.brands.nivona import NivonaProfile
from custom_components.melitta_barista.const import (
    DIRECTKEY_CATEGORY_ICONS,
    DIRECTKEY_NO_BUTTON_CATEGORIES,
    DirectKeyCategory,
    MachineType,
)
from custom_components.melitta_barista.ui_contract import (
    build_directkey_block,
    build_ui_contract,
)

COMPONENT_DIR = Path(__file__).parent.parent / "custom_components" / "melitta_barista"
UI_STRINGS_EN = COMPONENT_DIR / "ui_strings" / "en.json"

MELITTA = MelittaProfile()
NIVONA = NivonaProfile()


class FakeClient:
    """Duck-typed stand-in for CoffeeMachineClient (contract inputs only)."""

    def __init__(self, brand, capabilities, machine_type=None, connected=True,
                 integration_version="0.93.0"):
        self.brand = brand
        self.capabilities = capabilities
        self.machine_type = machine_type
        self.connected = connected
        self.status = None
        self.recipe_cache_generation = 0
        self.brand_logo_url = None
        self.integration_version = integration_version


def make_entry(entry_id="a1b2c3d4e5f6"):
    return SimpleNamespace(entry_id=entry_id)


def melitta_block(family="barista_ts", machine_type=MachineType.BARISTA_TS):
    return build_directkey_block(
        MELITTA.capabilities_for(family), machine_type, MELITTA,
    )


# ---------------------------------------------------------------------------
# §9.3.3 example payload — pinned verbatim (Melitta Barista TS)
# ---------------------------------------------------------------------------

_TS_DIRECTKEY = {
    "categories": [
        {"category": "espresso", "id": 0, "machine_button": True,
         "icon": "mdi:coffee"},
        {"category": "cafe_creme", "id": 1, "machine_button": True,
         "icon": "mdi:coffee-outline"},
        {"category": "cappuccino", "id": 2, "machine_button": True,
         "icon": "mdi:coffee"},
        {"category": "latte_macchiato", "id": 3, "machine_button": True,
         "icon": "mdi:glass-mug-variant"},
        {"category": "milk_froth", "id": 4, "machine_button": True,
         "icon": "mdi:cup"},
        {"category": "milk", "id": 5, "machine_button": False,
         "icon": "mdi:cup-outline"},
        {"category": "water", "id": 6, "machine_button": True,
         "icon": "mdi:cup-water"},
    ],
    "profiles": [
        {"slot": 0, "fixed": True, "name_key": "my_coffee"},
        {"slot": 1, "name_entity_suffix": "profile_1_name",
         "active_entity_suffix": "profile_1_active"},
        {"slot": 2, "name_entity_suffix": "profile_2_name",
         "active_entity_suffix": "profile_2_active"},
        {"slot": 3, "name_entity_suffix": "profile_3_name",
         "active_entity_suffix": "profile_3_active"},
        {"slot": 4, "name_entity_suffix": "profile_4_name",
         "active_entity_suffix": "profile_4_active"},
        {"slot": 5, "name_entity_suffix": "profile_5_name",
         "active_entity_suffix": "profile_5_active"},
        {"slot": 6, "name_entity_suffix": "profile_6_name",
         "active_entity_suffix": "profile_6_active"},
        {"slot": 7, "name_entity_suffix": "profile_7_name",
         "active_entity_suffix": "profile_7_active"},
        {"slot": 8, "name_entity_suffix": "profile_8_name",
         "active_entity_suffix": "profile_8_active"},
    ],
    "profile_select_entity_suffix": "profile",
    "active_profile_attribute": "active_profile",
}


def test_barista_ts_block_pinned_verbatim():
    assert melitta_block() == _TS_DIRECTKEY


# ---------------------------------------------------------------------------
# Presence gate (§9.3.2): iff "HC" in supported_extensions
# ---------------------------------------------------------------------------

def test_block_absent_for_nivona():
    for family_key in NIVONA.families:
        caps = NIVONA.capabilities_for(family_key)
        assert build_directkey_block(caps, None, NIVONA) is None, family_key


def test_document_omits_directkey_for_nivona():
    doc = build_ui_contract(make_entry(), FakeClient(
        NIVONA, NIVONA.capabilities_for("700"),
    ))
    assert "directkey" not in doc


def test_document_carries_directkey_for_melitta():
    doc = build_ui_contract(make_entry(), FakeClient(
        MELITTA, MELITTA.capabilities_for("barista_ts"),
        machine_type=MachineType.BARISTA_TS,
    ))
    assert doc["directkey"] == _TS_DIRECTKEY


# ---------------------------------------------------------------------------
# machine_button truth (§9.3.1)
# ---------------------------------------------------------------------------

def _buttons(block):
    return {c["category"]: c["machine_button"] for c in block["categories"]}


def test_ts_milk_has_no_machine_button():
    assert _buttons(melitta_block())["milk"] is False


def test_barista_t_all_buttons_true():
    """Confirmed BARISTA_T: no exclusion row — all 7 buttons physical."""
    block = melitta_block("barista_t", MachineType.BARISTA_T)
    assert all(_buttons(block).values())


def test_unknown_machine_type_follows_ts_row():
    block = melitta_block("barista_ts", None)
    buttons = _buttons(block)
    assert buttons["milk"] is False
    assert sum(1 for v in buttons.values() if not v) == 1


def test_no_button_table_is_ts_milk_only():
    """§9.3.1 table pinned byte-exact."""
    assert DIRECTKEY_NO_BUTTON_CATEGORIES == {
        MachineType.BARISTA_TS: frozenset({DirectKeyCategory.MILK}),
    }


def test_no_button_category_still_served():
    """§9.3.1: machine_button False does not remove the category — all 7
    entries are always served."""
    for machine_type in (None, MachineType.BARISTA_T, MachineType.BARISTA_TS):
        block = melitta_block("barista_ts", machine_type)
        assert len(block["categories"]) == 7


# ---------------------------------------------------------------------------
# Category order, ids, icons (§9.3.2)
# ---------------------------------------------------------------------------

def test_categories_in_enum_order_with_wire_ids():
    block = melitta_block()
    assert [c["id"] for c in block["categories"]] == list(range(7))
    assert [c["category"] for c in block["categories"]] == [
        member.name.lower() for member in DirectKeyCategory
    ]


def test_category_icons_match_normative_table():
    block = melitta_block()
    for entry in block["categories"]:
        category = DirectKeyCategory(entry["id"])
        assert entry["icon"] == DIRECTKEY_CATEGORY_ICONS[category]


def test_three_way_category_token_consistency():
    """§9.3.4 pin: contract categories == _DIRECTKEY_CATEGORIES == the
    values.directkey_category.* i18n key set."""
    served = [c["category"] for c in melitta_block()["categories"]]
    assert served == _DIRECTKEY_CATEGORIES

    strings = json.loads(UI_STRINGS_EN.read_text(encoding="utf-8"))
    i18n_tokens = {
        key.split(".", 2)[2]
        for key in strings
        if key.startswith("values.directkey_category.")
    }
    assert i18n_tokens == set(_DIRECTKEY_CATEGORIES)


# ---------------------------------------------------------------------------
# Profile slot model (§9.3.2)
# ---------------------------------------------------------------------------

def test_profile_count_is_my_coffee_slots_plus_one():
    for family, machine_type, expected in (
        ("barista_ts", MachineType.BARISTA_TS, 9),
        ("barista_t", MachineType.BARISTA_T, 5),
    ):
        caps = MELITTA.capabilities_for(family)
        block = build_directkey_block(caps, machine_type, MELITTA)
        assert len(block["profiles"]) == caps.my_coffee_slots + 1
        assert len(block["profiles"]) == expected
        assert [p["slot"] for p in block["profiles"]] == list(range(expected))


def test_slot_zero_fixed_with_name_key():
    slot0 = melitta_block()["profiles"][0]
    assert slot0 == {"slot": 0, "fixed": True, "name_key": "my_coffee"}
    assert "name_entity_suffix" not in slot0
    assert "active_entity_suffix" not in slot0


def test_user_slots_carry_entity_bindings_only():
    for profile in melitta_block()["profiles"][1:]:
        slot = profile["slot"]
        assert profile == {
            "slot": slot,
            "name_entity_suffix": f"profile_{slot}_name",
            "active_entity_suffix": f"profile_{slot}_active",
        }


def test_select_anchor_and_active_profile_attribute():
    block = melitta_block()
    assert block["profile_select_entity_suffix"] == "profile"
    assert block["active_profile_attribute"] == "active_profile"
