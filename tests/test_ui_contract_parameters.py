"""Tests for the v2 parameter catalog (UI Contract §6.1, Zone I-E).

Pins the §6.1.4 example payloads verbatim, the §6.1.2 mirror-and-freeze
invariant, and the §6.1.3 scope gating.
"""

from __future__ import annotations

from types import SimpleNamespace

from custom_components.melitta_barista.brands.base import MachineCapabilities
from custom_components.melitta_barista.brands.melitta import MelittaProfile
from custom_components.melitta_barista.brands.nivona import NivonaProfile
from custom_components.melitta_barista.const import INTENSITY_MAP, MachineType
from custom_components.melitta_barista.ui_contract import (
    build_capabilities_block,
    build_parameters,
    build_ui_contract,
)


class FakeClient:
    """Duck-typed stand-in for CoffeeMachineClient (contract inputs only)."""

    def __init__(self, brand, capabilities, machine_type=None, connected=True):
        self.brand = brand
        self.capabilities = capabilities
        self.machine_type = machine_type
        self.connected = connected
        self.status = None
        self.recipe_cache_generation = 0
        self.brand_logo_url = None
        self.integration_version = "0.92.0"


def make_melitta_client(family="barista_ts", machine_type=MachineType.BARISTA_TS):
    profile = MelittaProfile()
    return FakeClient(profile, profile.capabilities_for(family), machine_type)


def make_nivona_client(family="700"):
    profile = NivonaProfile()
    return FakeClient(profile, profile.capabilities_for(family))


def make_entry(entry_id="a1b2c3d4e5f6"):
    return SimpleNamespace(entry_id=entry_id)


# ---------------------------------------------------------------------------
# §6.1.4 example payloads — pinned verbatim
# ---------------------------------------------------------------------------

def test_parameters_melitta_ts_payload_pinned_verbatim():
    client = make_melitta_client()
    parameters = build_parameters(build_capabilities_block(client))
    assert parameters == {
        "process": {"kind": "enum", "scope": ["freestyle"],
                    "tokens": ["none", "coffee", "milk", "water"]},
        "intensity": {"kind": "enum", "scope": ["freestyle"],
                      "applies_to": ["coffee"],
                      "tokens": ["very_mild", "mild", "medium", "strong",
                                 "very_strong"]},
        "aroma": {"kind": "enum", "scope": ["freestyle"],
                  "applies_to": ["coffee"],
                  "tokens": ["standard", "intense"]},
        "temperature": {"kind": "enum", "scope": ["freestyle"],
                        "tokens": ["cold", "normal", "high"]},
        "shots": {"kind": "enum", "scope": ["freestyle"],
                  "applies_to": ["coffee"],
                  "tokens": ["none", "one", "two", "three"]},
        "blend": {"kind": "enum", "scope": ["freestyle"],
                  "applies_to": ["coffee"],
                  "tokens": ["hopper_1", "hopper_2"]},
        "portion_ml": {"kind": "range", "scope": ["freestyle"], "unit": "ml",
                       "per_component": True,
                       "c1": {"min": 5, "max": 250, "step": 5},
                       "c2": {"min": 0, "max": 250, "step": 5}},
    }


def test_parameters_nivona_700_payload_pinned_verbatim():
    client = make_nivona_client()
    parameters = build_parameters(build_capabilities_block(client))
    assert parameters == {
        "intensity": {"kind": "enum", "scope": ["brew_override"],
                      "applies_to": ["coffee"],
                      "tokens": ["mild", "medium", "strong"]},
        "aroma": {"kind": "enum", "scope": ["brew_override"],
                  "applies_to": ["coffee"],
                  "tokens": ["standard", "intense"]},
        "portion_ml": {"kind": "range", "scope": ["brew_override"],
                       "unit": "ml", "per_component": True,
                       "c1": {"min": 5, "max": 250, "step": 5},
                       "c2": {"min": 0, "max": 250, "step": 5}},
    }


# ---------------------------------------------------------------------------
# §6.1.2 mirror-and-freeze invariant (byte-exact)
# ---------------------------------------------------------------------------

def _assert_mirror(document):
    parameters = document["parameters"]
    freestyle = document["vocabularies"]["freestyle"]
    for family, descriptor in parameters.items():
        if descriptor["kind"] != "enum":
            continue
        assert descriptor["tokens"] == freestyle[family], family
    if "portion_ml" in parameters:
        assert parameters["portion_ml"]["c1"] == document["limits"]["portion_ml"]["c1"]
        assert parameters["portion_ml"]["c2"] == document["limits"]["portion_ml"]["c2"]


def test_mirror_invariant_melitta_document():
    document = build_ui_contract(make_entry(), make_melitta_client())
    assert set(document["parameters"]) == {
        "process", "intensity", "aroma", "temperature", "shots", "blend",
        "portion_ml",
    }
    _assert_mirror(document)


def test_mirror_invariant_nivona_document():
    document = build_ui_contract(make_entry(), make_nivona_client())
    _assert_mirror(document)


def test_mirror_invariant_every_nivona_family():
    profile = NivonaProfile()
    for family_key in profile.families:
        document = build_ui_contract(
            make_entry(), make_nivona_client(family_key),
        )
        _assert_mirror(document)


# ---------------------------------------------------------------------------
# §6.1.3 scope gating
# ---------------------------------------------------------------------------

def test_scope_gating_melitta_freestyle_only():
    """Melitta: supports_freestyle, no brew overrides — freestyle scope only."""
    parameters = build_parameters(build_capabilities_block(make_melitta_client()))
    for descriptor in parameters.values():
        assert descriptor["scope"] == ["freestyle"]


def test_scope_gating_nivona_brew_override_only():
    """Nivona: no freestyle — freestyle-only families are omitted entirely,
    dual-scope families narrow to brew_override."""
    parameters = build_parameters(build_capabilities_block(make_nivona_client()))
    assert set(parameters) == {"intensity", "aroma", "portion_ml"}
    for descriptor in parameters.values():
        assert descriptor["scope"] == ["brew_override"]


def test_scope_gating_neither_capability_empties_catalog():
    """A machine with neither freestyle nor overrides serves no parameters."""
    caps = MachineCapabilities(
        family_key="x", model_name="X",
        supports_brew_overrides=False,
    )
    client = FakeClient(NivonaProfile(), caps)
    parameters = build_parameters(build_capabilities_block(client))
    assert parameters == {}


# ---------------------------------------------------------------------------
# Machine-filtered token subsets (same server filters as v1, §6.1.3)
# ---------------------------------------------------------------------------

def test_three_level_intensity_slice():
    """3-level Nivona machines serve the middle three intensity steps."""
    parameters = build_parameters(build_capabilities_block(make_nivona_client()))
    all_intensities = sorted(INTENSITY_MAP, key=INTENSITY_MAP.get)
    assert parameters["intensity"]["tokens"] == all_intensities[1:4]


def test_single_hopper_blend():
    """A confirmed Barista T serves a single-hopper blend list."""
    client = make_melitta_client("barista_t", MachineType.BARISTA_T)
    parameters = build_parameters(build_capabilities_block(client))
    assert parameters["blend"]["tokens"] == ["hopper_1"]


def test_no_aroma_balance_single_token():
    caps = MachineCapabilities(
        family_key="x", model_name="X",
        strength_levels=5, has_aroma_balance=False,
    )
    client = FakeClient(MelittaProfile(), caps,
                        machine_type=MachineType.BARISTA_TS)
    parameters = build_parameters(build_capabilities_block(client))
    assert parameters["aroma"]["tokens"] == ["standard"]


# ---------------------------------------------------------------------------
# Document-level v2 additions (§6.0 / §6.1.6 / §6.3.2)
# ---------------------------------------------------------------------------

def test_document_carries_forbidden_combinations_empty():
    document = build_ui_contract(make_entry(), make_melitta_client())
    assert document["forbidden_combinations"] == []


def test_document_carries_strings_version_from_client_stash():
    document = build_ui_contract(make_entry(), make_melitta_client())
    assert document["strings_version"] == "0.92.0"


def test_document_parameters_match_builder_output():
    client = make_nivona_client()
    document = build_ui_contract(make_entry(), client)
    assert document["parameters"] == build_parameters(
        build_capabilities_block(client)
    )
