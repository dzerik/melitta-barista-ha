"""Tests for the v3 settings descriptors block (UI Contract §9.1, Zone I-J).

Pins the §9.1.5 example payloads verbatim, the per-family gated sets,
the §9.1.2.5 predicate-equality invariant (builder vs entity
registration over the shared tables), the §9.1.2.1 naming invariants
(Melitta slugify(name)==token; anchored Nivona entity names slug-equal
across all 29 translations), the structural option-label derivation,
and the shared `nivona_number_range` helper parity.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from homeassistant.util import slugify

from custom_components.melitta_barista.brands.melitta import MelittaProfile
from custom_components.melitta_barista.brands.nivona import NivonaProfile
from custom_components.melitta_barista.brands.nivona._options import (
    nivona_number_range,
    option_tokens,
)
from custom_components.melitta_barista.const import (
    MELITTA_SETTING_TABLES,
    MachineType,
    SETTING_LEVEL_TOKENS,
    TS_ONLY_SETTINGS,
)
from custom_components.melitta_barista.number import (
    BrandSettingNumber,
    SETTING_DEFINITIONS,
)
from custom_components.melitta_barista.switch import SWITCH_DEFINITIONS
from custom_components.melitta_barista.ui_contract import (
    build_bridge_attributes,
    build_settings_block,
    build_ui_contract,
)

COMPONENT_DIR = Path(__file__).parent.parent / "custom_components" / "melitta_barista"
TRANSLATIONS_DIR = COMPONENT_DIR / "translations"

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
    return build_settings_block(
        MELITTA.capabilities_for(family), machine_type, MELITTA,
    )


def nivona_block(family="700"):
    return build_settings_block(NIVONA.capabilities_for(family), None, NIVONA)


def tokens_of(block):
    return [entry["setting"] for entry in block]


# ---------------------------------------------------------------------------
# §9.1.5 example payloads — pinned verbatim
# ---------------------------------------------------------------------------

_MELITTA_TS_SETTINGS = [
    {"setting": "auto_bean_select", "control": "switch", "group": "brew",
     "icon": "mdi:grain",
     "entity": {"domain": "switch", "entity_suffix": "auto_bean_select"},
     "writable": True},
    {"setting": "brew_temperature", "control": "number", "group": "brew",
     "icon": "mdi:thermometer",
     "entity": {"domain": "number", "entity_suffix": "brew_temperature"},
     "writable": True, "min": 0, "max": 2, "step": 1, "display": "slider",
     "levels": [{"value": 0, "token": "low"},
                {"value": 1, "token": "normal"},
                {"value": 2, "token": "high"}]},
    {"setting": "water_hardness", "control": "number", "group": "water",
     "icon": "mdi:water-opacity",
     "entity": {"domain": "number", "entity_suffix": "water_hardness"},
     "writable": True, "min": 1, "max": 4, "step": 1, "display": "slider",
     "levels": [{"value": 1, "token": "soft"},
                {"value": 2, "token": "medium"},
                {"value": 3, "token": "hard"},
                {"value": 4, "token": "very_hard"}]},
    {"setting": "filter", "control": "number", "group": "water",
     "icon": "mdi:filter-outline",
     "entity": {"domain": "number", "entity_suffix": "filter"},
     "writable": True, "min": 0, "max": 1, "step": 1, "display": "slider",
     "levels": [{"value": 0, "token": "off"},
                {"value": 1, "token": "on"}]},
    {"setting": "rinsing_disabled", "control": "switch", "group": "water",
     "icon": "mdi:water-off",
     "entity": {"domain": "switch", "entity_suffix": "rinsing_disabled"},
     "writable": True},
    {"setting": "energy_saving", "control": "switch", "group": "power",
     "icon": "mdi:leaf",
     "entity": {"domain": "switch", "entity_suffix": "energy_saving"},
     "writable": True},
    {"setting": "auto_off_after", "control": "number", "group": "power",
     "icon": "mdi:timer-off-outline",
     "entity": {"domain": "number", "entity_suffix": "auto_off_after"},
     "writable": True, "min": 15, "max": 240, "step": 15,
     "unit": "min", "display": "box"},
    # §9.1.3: register mapping unverified (issue #10 79x precedent) —
    # served numeric-only, no levels.
    {"setting": "language", "control": "number", "group": "system",
     "icon": "mdi:translate",
     "entity": {"domain": "number", "entity_suffix": "language"},
     "writable": True, "min": 0, "max": 15, "step": 1, "display": "box"},
]

_NIVONA_700_SETTINGS = [
    {"setting": "temperature", "control": "select", "group": "brew",
     "icon": "mdi:tune",
     "entity": {"domain": "select", "entity_suffix": "temperature"},
     "writable": True,
     "options": [{"value": 0, "token": None, "label": "normal"},
                 {"value": 1, "token": None, "label": "high"},
                 {"value": 2, "token": None, "label": "max"},
                 {"value": 3, "token": None, "label": "individual"}]},
    {"setting": "profile", "control": "select", "group": "brew",
     "icon": "mdi:tune",
     "entity": {"domain": "select", "entity_suffix": "profile"},
     "writable": True,
     "options": [{"value": 0, "token": None, "label": "dynamic"},
                 {"value": 1, "token": None, "label": "constant"},
                 {"value": 2, "token": None, "label": "intense"},
                 {"value": 3, "token": None, "label": "individual"}]},
    {"setting": "water_hardness", "control": "select", "group": "water",
     "icon": "mdi:tune",
     "entity": {"domain": "select", "entity_suffix": "water_hardness"},
     "writable": True,
     "options": [{"value": 0, "token": "soft", "label": "soft"},
                 {"value": 1, "token": "medium", "label": "medium"},
                 {"value": 2, "token": "hard", "label": "hard"},
                 {"value": 3, "token": "very_hard", "label": "very hard"}]},
    {"setting": "off_rinse", "control": "select", "group": "water",
     "icon": "mdi:tune",
     "entity": {"domain": "select", "entity_suffix": "off_rinse"},
     "writable": True,
     "options": [{"value": 0, "token": "off", "label": "off"},
                 {"value": 1, "token": "on", "label": "on"}]},
    # §9.1.5: elided in the doc example, pinned in full here.
    {"setting": "auto_off", "control": "select", "group": "power",
     "icon": "mdi:tune",
     "entity": {"domain": "select", "entity_suffix": "auto_off"},
     "writable": True,
     "options": [{"value": 0, "token": None, "label": "10 min"},
                 {"value": 1, "token": None, "label": "30 min"},
                 {"value": 2, "token": None, "label": "1 h"},
                 {"value": 3, "token": None, "label": "2 h"},
                 {"value": 4, "token": None, "label": "4 h"},
                 {"value": 5, "token": None, "label": "6 h"},
                 {"value": 6, "token": None, "label": "8 h"},
                 {"value": 7, "token": None, "label": "10 h"},
                 {"value": 8, "token": None, "label": "12 h"},
                 {"value": 9, "token": None, "label": "off"}]},
]


def test_melitta_ts_settings_pinned_verbatim():
    assert melitta_block() == _MELITTA_TS_SETTINGS


def test_nivona_700_settings_pinned_verbatim():
    assert nivona_block("700") == _NIVONA_700_SETTINGS


# ---------------------------------------------------------------------------
# Per-family gated sets (§9.1.2.5/§9.1.2.6, §9.1.3)
# ---------------------------------------------------------------------------

def test_barista_t_drops_auto_bean_select():
    block = melitta_block("barista_t", MachineType.BARISTA_T)
    assert "auto_bean_select" not in tokens_of(block)
    assert len(block) == len(_MELITTA_TS_SETTINGS) - 1


def test_unknown_machine_type_serves_auto_bean_select():
    """§9.1.2.6: pre-refinement follows the assume-TS precedent."""
    assert "auto_bean_select" in tokens_of(melitta_block("barista_ts", None))


def test_79x_drops_off_rinse():
    tokens = tokens_of(nivona_block("79x"))
    assert "off_rinse" not in tokens
    assert tokens == ["temperature", "profile", "water_hardness", "auto_off"]


def test_758_model_drops_profile():
    caps = NIVONA.capabilities_for_model("NIVONA-758253090924023")
    block = build_settings_block(caps, None, NIVONA)
    assert "profile" not in tokens_of(block)
    assert "water_hardness" in tokens_of(block)


def test_language_absent_on_every_nivona_family_present_on_melitta():
    for family_key in NIVONA.families:
        assert "language" not in tokens_of(nivona_block(family_key)), family_key
    assert "language" in tokens_of(melitta_block())


def test_group_render_order_is_normative():
    """Groups appear in brew, water, power, system order (§9.1.3)."""
    order = ["brew", "water", "power", "system"]
    for block in (
        melitta_block(),
        nivona_block("700"),
        nivona_block("900"),
        nivona_block("1030"),
    ):
        groups = []
        for entry in block:
            if entry["group"] not in groups:
                groups.append(entry["group"])
        assert groups == [g for g in order if g in groups]


def test_nivona_options_less_descriptors_become_power_numbers():
    """§9.1.3: auto_on_hours/minutes serve as number entries, group
    power, unit h / min, range from the shared helper."""
    block = {entry["setting"]: entry for entry in nivona_block("900")}
    hours = block["auto_on_hours"]
    assert hours == {
        "setting": "auto_on_hours", "control": "number", "group": "power",
        "icon": "mdi:tune",
        "entity": {"domain": "number", "entity_suffix": "auto_on_hours"},
        "writable": True, "min": 0, "max": 23, "step": 1, "unit": "h",
    }
    minutes = block["auto_on_minutes"]
    assert minutes["min"] == 0
    assert minutes["max"] == 59
    assert minutes["unit"] == "min"
    assert minutes["group"] == "power"


# ---------------------------------------------------------------------------
# §9.1.2.5 predicate-equality invariant — builder vs entity registration
# ---------------------------------------------------------------------------

def _melitta_entity_surface(caps, machine_type):
    """(domain, token) set the entity platforms register, from the same
    shared tables switch.py / number.py consume."""
    surface = set()
    for defn in SWITCH_DEFINITIONS:
        if defn["ts_only"] and machine_type == MachineType.BARISTA_T:
            continue
        surface.add(("switch", slugify(defn["name"])))
    excluded = caps.unsupported_generic_setting_ids
    for defn in SETTING_DEFINITIONS:
        if int(defn["id"]) in excluded:
            continue
        surface.add(("number", slugify(defn["name"])))
    return surface


def _nivona_entity_surface(caps):
    """(domain, key) set select.py / number.py register from descriptors."""
    surface = set()
    for descriptor in caps.settings:
        domain = "select" if descriptor.options else "number"
        surface.add((domain, descriptor.key))
    return surface


def _block_surface(block):
    return {
        (entry["entity"]["domain"], entry["entity"]["entity_suffix"])
        for entry in block
    }


def test_predicate_equality_melitta_all_combinations():
    for family_key in MELITTA.families:
        caps = MELITTA.capabilities_for(family_key)
        for machine_type in (None, MachineType.BARISTA_T,
                             MachineType.BARISTA_TS):
            block = build_settings_block(caps, machine_type, MELITTA)
            assert _block_surface(block) == _melitta_entity_surface(
                caps, machine_type,
            ), (family_key, machine_type)


def test_predicate_equality_nivona_all_families_and_758():
    all_caps = [
        NIVONA.capabilities_for(family_key) for family_key in NIVONA.families
    ]
    all_caps.append(NIVONA.capabilities_for_model("NIVONA-758253090924023"))
    for caps in all_caps:
        block = build_settings_block(caps, None, NIVONA)
        assert _block_surface(block) == _nivona_entity_surface(caps), (
            caps.family_key
        )


# ---------------------------------------------------------------------------
# §9.1.2.1 naming invariants
# ---------------------------------------------------------------------------

def test_melitta_tokens_are_slugified_entity_names():
    for row in MELITTA_SETTING_TABLES:
        assert slugify(row["name"]) == row["setting"], row["setting"]


def test_anchored_nivona_names_slug_equal_across_all_29_locales():
    """Every served Nivona descriptor key must satisfy
    slugify(entity.<domain>.<key>.name) == key in all 29 translation
    files — those name strings anchor the contract and are frozen."""
    anchored: set[tuple[str, str]] = set()
    for family_key in NIVONA.families:
        anchored |= _nivona_entity_surface(NIVONA.capabilities_for(family_key))

    files = sorted(TRANSLATIONS_DIR.glob("*.json"))
    assert len(files) == 29
    for path in files:
        entity = json.loads(path.read_text(encoding="utf-8")).get("entity", {})
        for domain, key in anchored:
            name = entity.get(domain, {}).get(key, {}).get("name")
            assert name is not None, (path.name, domain, key)
            assert slugify(name) == key, (path.name, domain, key, name)


def test_token_keyspace_distinction_brew_temperature_vs_temperature():
    """Deliberate §9.1.2.1 note: Melitta id-22 is `brew_temperature`,
    Nivona id-102 keeps its key `temperature` — two different tokens."""
    assert "brew_temperature" in tokens_of(melitta_block())
    assert "temperature" not in tokens_of(melitta_block())
    assert "temperature" in tokens_of(nivona_block("700"))
    assert "brew_temperature" not in tokens_of(nivona_block("700"))


def test_water_hardness_token_shared_across_brands():
    """§9.1.2.2: intentionally the same token on both brands, with the
    1-based/0-based divergence carried in the values."""
    melitta = {e["setting"]: e for e in melitta_block()}["water_hardness"]
    nivona = {e["setting"]: e for e in nivona_block("700")}["water_hardness"]
    assert [lvl["token"] for lvl in melitta["levels"]] == [
        "soft", "medium", "hard", "very_hard",
    ]
    assert [opt["token"] for opt in nivona["options"]] == [
        "soft", "medium", "hard", "very_hard",
    ]
    assert [lvl["value"] for lvl in melitta["levels"]] == [1, 2, 3, 4]
    assert [opt["value"] for opt in nivona["options"]] == [0, 1, 2, 3]


# ---------------------------------------------------------------------------
# Option labels / level values — structural derivation
# ---------------------------------------------------------------------------

def test_option_labels_byte_equal_to_descriptor_tables():
    """§9.1.1: options[].label mirrors the descriptor tables (single
    source, derived at build time)."""
    for family_key in NIVONA.families:
        caps = NIVONA.capabilities_for(family_key)
        block = {entry["setting"]: entry for entry in nivona_block(family_key)}
        for descriptor in caps.settings:
            if not descriptor.options:
                continue
            served = block[descriptor.key]["options"]
            assert [o["label"] for o in served] == [
                label for _, label in descriptor.options
            ], (family_key, descriptor.key)
            assert [o["value"] for o in served] == [
                value for value, _ in descriptor.options
            ], (family_key, descriptor.key)


def test_shared_off_on_tables_emit_tokens_everywhere():
    """§9.1.1: the one-time annotation of the shared off/on tables
    tokenizes every descriptor that references them, on every family."""
    for family_key in NIVONA.families:
        for entry in nivona_block(family_key):
            if entry["control"] != "select":
                continue
            labels = [o["label"] for o in entry["options"]]
            if labels == ["off", "on"]:
                assert [o["token"] for o in entry["options"]] == ["off", "on"], (
                    family_key, entry["setting"],
                )


def test_unannotated_tables_serve_null_tokens():
    tokens = option_tokens(((0, "dynamic"), (1, "constant")))
    assert tokens == (None, None)


def test_level_values_match_entity_ranges():
    """Level values are exactly the entity's min..max/step ladder."""
    rows = {row["setting"]: row for row in MELITTA_SETTING_TABLES}
    for setting, levels in SETTING_LEVEL_TOKENS.items():
        row = rows[setting]
        assert [value for value, _ in levels] == list(
            range(row["min"], row["max"] + 1, row["step"])
        ), setting


# ---------------------------------------------------------------------------
# nivona_number_range parity — entity vs builder (§9.1.3)
# ---------------------------------------------------------------------------

def test_nivona_number_range_rules():
    hours = SimpleNamespace(key="auto_on_hours")
    minutes = SimpleNamespace(key="auto_on_minutes")
    other = SimpleNamespace(key="something_else")
    assert nivona_number_range(hours) == (0, 23, "h")
    assert nivona_number_range(minutes) == (0, 59, "min")
    assert nivona_number_range(other) == (0, 255, None)


def test_brand_setting_number_entity_uses_shared_helper():
    """Entity range/unit equal the helper output for every options-less
    descriptor of every family — entity and builder cannot diverge."""
    client = SimpleNamespace(address="aa:bb", connected=False)
    entry = SimpleNamespace(entry_id="x")
    for family_key in NIVONA.families:
        caps = NIVONA.capabilities_for(family_key)
        block = {e["setting"]: e for e in nivona_block(family_key)}
        for descriptor in caps.settings:
            if descriptor.options:
                continue
            entity = BrandSettingNumber(client, entry, "Machine", descriptor)
            min_value, max_value, unit = nivona_number_range(descriptor)
            assert entity._attr_native_min_value == min_value
            assert entity._attr_native_max_value == max_value
            served = block[descriptor.key]
            assert served["min"] == min_value
            assert served["max"] == max_value
            if unit is not None:
                assert entity._attr_native_unit_of_measurement == unit
                assert served["unit"] == unit


# ---------------------------------------------------------------------------
# Entity-surface snapshot — the table move changed nothing (§5.2 rule 9)
# ---------------------------------------------------------------------------

def test_switch_definitions_snapshot():
    by_id = {int(d["id"]): d for d in SWITCH_DEFINITIONS}
    assert {
        setting_id: (d["name"], d["icon"]) for setting_id, d in by_id.items()
    } == {
        12: ("Energy Saving", "mdi:leaf"),
        16: ("Auto Bean Select", "mdi:grain"),
        18: ("Rinsing Disabled", "mdi:water-off"),
    }
    assert TS_ONLY_SETTINGS == {16}
    for d in SWITCH_DEFINITIONS:
        assert d["ts_only"] is (int(d["id"]) == 16)
        assert str(d["category"]) == "config"


def test_number_definitions_snapshot():
    from homeassistant.components.number import NumberMode

    by_id = {int(d["id"]): d for d in SETTING_DEFINITIONS}
    assert {
        setting_id: (
            d["name"], d["icon"], d["min"], d["max"], d["step"], d["mode"],
            d.get("unit"),
        )
        for setting_id, d in by_id.items()
    } == {
        11: ("Water Hardness", "mdi:water-opacity", 1, 4, 1,
             NumberMode.SLIDER, None),
        13: ("Auto Off After", "mdi:timer-off-outline", 15, 240, 15,
             NumberMode.BOX, "min"),
        22: ("Brew Temperature", "mdi:thermometer", 0, 2, 1,
             NumberMode.SLIDER, None),
        15: ("Language", "mdi:translate", 0, 15, 1, NumberMode.BOX, None),
        91: ("Filter", "mdi:filter-outline", 0, 1, 1, NumberMode.SLIDER, None),
    }
    for d in SETTING_DEFINITIONS:
        assert str(d["category"]) == "config"


# ---------------------------------------------------------------------------
# Document integration + §9.4 fingerprint invariants
# ---------------------------------------------------------------------------

def test_document_settings_match_builder_both_brands():
    melitta_client = FakeClient(
        MELITTA, MELITTA.capabilities_for("barista_ts"),
        machine_type=MachineType.BARISTA_TS,
    )
    doc = build_ui_contract(make_entry(), melitta_client)
    assert doc["settings"] == _MELITTA_TS_SETTINGS

    nivona_client = FakeClient(NIVONA, NIVONA.capabilities_for("700"))
    doc = build_ui_contract(make_entry(), nivona_client)
    assert doc["settings"] == _NIVONA_700_SETTINGS


def test_bridge_and_document_fingerprints_unaffected_by_v3():
    """§9.4: no new fingerprint inputs — bridge-vs-document equality
    holds with the v3 blocks in the document."""
    for client in (
        FakeClient(MELITTA, MELITTA.capabilities_for("barista_ts"),
                   machine_type=MachineType.BARISTA_TS),
        FakeClient(NIVONA, NIVONA.capabilities_for("700")),
    ):
        entry = make_entry()
        bridge = build_bridge_attributes(entry, client)
        document = build_ui_contract(entry, client)
        assert "settings" in document
        assert bridge["contract_fingerprint"] == document["contract_fingerprint"]
