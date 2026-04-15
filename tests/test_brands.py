"""Tests for BrandProfile abstraction."""

from __future__ import annotations

import pytest

from custom_components.melitta_barista.brands import (
    BrandProfile,
    MelittaProfile,
    NivonaProfile,
    all_profiles,
    detect_from_advertisement,
    get_profile,
)
from custom_components.melitta_barista.brands.base import (
    FeatureNotSupported,
    MachineCapabilities,
)


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

def test_registry_has_both_brands():
    profiles = all_profiles()
    assert "melitta" in profiles
    assert "nivona" in profiles


def test_get_profile_melitta():
    mp = get_profile("melitta")
    assert isinstance(mp, MelittaProfile)
    assert mp.brand_slug == "melitta"
    assert mp.brand_name == "Melitta"


def test_get_profile_nivona():
    np_ = get_profile("nivona")
    assert isinstance(np_, NivonaProfile)
    assert np_.brand_slug == "nivona"


def test_get_profile_unknown_raises():
    with pytest.raises(KeyError):
        get_profile("gaggenau")


def test_protocol_compliance():
    """Both profiles satisfy the BrandProfile Protocol."""
    assert isinstance(MelittaProfile(), BrandProfile)
    assert isinstance(NivonaProfile(), BrandProfile)


# ---------------------------------------------------------------------------
# Advertisement dispatch
# ---------------------------------------------------------------------------

def test_advertisement_matches_melitta_prefix():
    profile = detect_from_advertisement("860400E250429374203-")
    assert profile is not None and profile.brand_slug == "melitta"


def test_advertisement_matches_melitta_all_prefixes():
    for prefix in ("8301", "8311", "8401", "8501", "8601", "8604"):
        profile = detect_from_advertisement(f"{prefix}00ABCDEF123456-")
        assert profile is not None, f"Melitta {prefix} not matched"
        assert profile.brand_slug == "melitta"


def test_advertisement_matches_nivona_pattern():
    # Prefixed form (legacy captures with "NIVONA-" prefix)
    profile = detect_from_advertisement("NIVONA-7565730710-----")
    assert profile is not None and profile.brand_slug == "nivona"
    # Bare-serial form (real machines + the ESP emulator, required by
    # the official Nivona Android app's Substring(0, 4) model lookup)
    profile = detect_from_advertisement("8107000001-----")
    assert profile is not None and profile.brand_slug == "nivona"
    profile = detect_from_advertisement("7565730710-----")
    assert profile is not None and profile.brand_slug == "nivona"
    # 15-digit no-dash form (observed on real NICR 930, firmware 0254A013A10)
    profile = detect_from_advertisement("930254000000000")
    assert profile is not None and profile.brand_slug == "nivona"


def test_advertisement_no_match():
    assert detect_from_advertisement("Random Device") is None
    assert detect_from_advertisement("") is None
    assert detect_from_advertisement(None) is None
    # Purely numeric names that should NOT match Nivona
    assert detect_from_advertisement("1234567890") is None       # 10 digits, no dashes
    assert detect_from_advertisement("12345678901234") is None   # 14 digits


# ---------------------------------------------------------------------------
# Melitta crypto & capabilities
# ---------------------------------------------------------------------------

def test_melitta_rc4_key_length():
    mp = MelittaProfile()
    assert len(mp.runtime_rc4_key) == 32


def test_melitta_hu_table_length():
    mp = MelittaProfile()
    assert len(mp.hu_table) == 256


def test_melitta_supports_hc_hj():
    mp = MelittaProfile()
    assert "HC" in mp.supported_extensions
    assert "HJ" in mp.supported_extensions


def test_melitta_detect_family():
    mp = MelittaProfile()
    assert mp.detect_family("8604ABCDEF12345-") == "barista_ts"
    assert mp.detect_family("8401XXXX") == "barista_t"
    assert mp.detect_family("unknown") is None


def test_melitta_capabilities():
    mp = MelittaProfile()
    caps = mp.capabilities_for("barista_ts")
    assert caps.model_name == "Barista TS Smart"
    assert caps.supports_recipe_writes is True


# ---------------------------------------------------------------------------
# Nivona crypto, capabilities, and HU vectors
# ---------------------------------------------------------------------------

def test_nivona_rc4_key_is_known_ascii():
    np_ = NivonaProfile()
    assert np_.runtime_rc4_key == b"NIV_060616_V10_1*9#3!4$6+4res-?3"


def test_nivona_hu_table_length():
    np_ = NivonaProfile()
    assert len(np_.hu_table) == 256


def test_nivona_no_recipe_extensions():
    np_ = NivonaProfile()
    assert "HC" not in np_.supported_extensions
    assert "HJ" not in np_.supported_extensions
    assert np_.supported_extensions == frozenset()


def test_nivona_hu_verifier_known_vector():
    """Upstream vector: seed FA 48 D1 7B → verifier 7E 6E.

    Source: mpapierski/esp-coffee-bridge/docs/NIVONA.md and
    src/nivona.cpp verified against real NICR 756 hardware in the
    upstream RE. This test guarantees our Python port produces the same
    output.
    """
    np_ = NivonaProfile()
    seed = bytes.fromhex("FA48D17B")
    assert np_.hu_verifier(seed, 0, 4) == bytes.fromhex("7E6E")


def test_nivona_detect_family_from_serial():
    np_ = NivonaProfile()
    # NICR 756 → family 700 (via 3-char prefix "756")
    assert np_.detect_family("NIVONA-7565730710-----") == "700"
    # NIVO 8101 → family 8000 (via 4-char prefix "8101")
    assert np_.detect_family("NIVONA-8101000000-----") == "8000"
    # NICR 660 → family 600
    assert np_.detect_family("NIVONA-6605730710-----") == "600"
    # Unknown serial
    assert np_.detect_family("NIVONA-0000000000-----") is None


def test_nivona_capabilities_per_family():
    np_ = NivonaProfile()
    f700 = np_.capabilities_for("700")
    assert f700.has_aroma_balance is True
    assert f700.brew_command_mode == 0x0B

    f8000 = np_.capabilities_for("8000")
    assert f8000.brew_command_mode == 0x04   # NIVO8000 quirk

    f900 = np_.capabilities_for("900")
    assert f900.fluid_scale_factor == 10    # 900 writes ml×10


def test_nivona_900_tolerates_stale_frother_flag():
    """NICR 9xx leaves MOVE_CUP_TO_FROTHER (11) set after a brew."""
    np_ = NivonaProfile()
    f900 = np_.capabilities_for("900")
    f900_light = np_.capabilities_for("900-light")
    assert 11 in f900.tolerated_brew_manipulations
    assert 11 in f900_light.tolerated_brew_manipulations


def test_other_nivona_families_default_to_strict_brew_gate():
    """Only 900-series tolerates the stale frother flag; rest are strict."""
    np_ = NivonaProfile()
    for fk in ("600", "700", "79x", "1030", "1040", "8000"):
        caps = np_.capabilities_for(fk)
        assert caps.tolerated_brew_manipulations == (), (
            f"family {fk} should not tolerate any manipulation flags"
        )


def test_nivona_recipes_populated_per_family():
    """Every Nivona family has a non-empty recipe table."""
    np_ = NivonaProfile()
    expected_sizes = {
        "600": 6, "700": 8, "79x": 7,
        "900": 8, "900-light": 8,
        "1030": 10, "1040": 9, "8000": 8,
    }
    for family_key, count in expected_sizes.items():
        caps = np_.capabilities_for(family_key)
        assert len(caps.recipes) == count, (
            f"Nivona family {family_key} expected {count} recipes, "
            f"got {len(caps.recipes)}"
        )


def test_nivona_settings_per_family():
    """Every Nivona family has a settings descriptor table with the
    expected cardinality per authoritative mapping (v0.49.0+).
    """
    np_ = NivonaProfile()
    expected_sizes = {
        "600": 5,       # 101/102/103/104/106
        "700": 5,       # same as 600
        "79x": 4,       # 700 minus 103 (off-rinse absent on 79X)
        "900": 11,      # tank-light accents + AutoOn pair
        "900-light": 6, # strip lights, keep save_energy + AutoOn
        "1030": 14,     # 1000-family with cup_heater + AutoOn +
                        # profile/temps
        "1040": 17,     # 1030 + frother-temp + power-on extras
        "8000": 4,
    }
    for fk, size in expected_sizes.items():
        caps = np_.capabilities_for(fk)
        assert len(caps.settings) == size, (
            f"Nivona family {fk} expected {size} settings, got {len(caps.settings)}"
        )


def test_nivona_stats_for_stats_families():
    """Every Nivona family exposes its authoritative stat ID set
    (v0.49.0+). Sizes are the per-family counts from the canonical
    stats-factory mapping.
    """
    np_ = NivonaProfile()
    expected_sizes = {
        "600": 16,       # 7 recipes + 8 gauges + 105 dep
        "700": 18,       # 9 recipes + 8 gauges + 105 dep
        "79x": 17,       # 700 minus selector 204 (Cappuccino)
        "900": 31,       # 22 counters + 8 gauges + 101 dep
        "900-light": 31,
        "1030": 33,      # 24 counters + 8 gauges + 101 dep
        "1040": 32,      # 1030 minus id 207 (HeisseMilch)
        "8000": 27,
    }
    for fk, size in expected_sizes.items():
        caps = np_.capabilities_for(fk)
        assert len(caps.stats) == size, (
            f"Nivona family {fk} expected {size} stats, got {len(caps.stats)}"
        )


def test_nivona_per_model_capabilities_overrides():
    """capabilities_for_model returns per-model my_coffee_slots / strength."""
    np_ = NivonaProfile()
    cases = [
        # (serial_suffix, expected_slots, expected_strength, expected_family)
        ("756", 1, 3, "700"),    # NICR 756 — 3 strength bands
        ("788", 5, 5, "700"),    # NICR 788 — 5 slots, 5 strength
        ("790", 5, 5, "79x"),
        ("920", 9, 5, "900"),
        ("965", 9, 5, "900-light"),
        ("030", 18, 5, "1030"),
        ("040", 18, 5, "1040"),
        ("8101", 9, 5, "8000"),  # NIVO 8101
        # 600-family: all 5 strength bands (v0.49.0 correction)
        ("670", 5, 5, "600"),
        ("660", 1, 5, "600"),
    ]
    for suffix, slots, strength, fkey in cases:
        caps = np_.capabilities_for_model(f"NIVONA-{suffix}0000000-----")
        assert caps is not None, f"{suffix} not detected"
        assert caps.my_coffee_slots == slots, f"{suffix} slots"
        assert caps.strength_levels == strength, f"{suffix} strength"
        assert caps.family_key == fkey, f"{suffix} family"


def test_nivona_capabilities_for_unknown_model_returns_none():
    np_ = NivonaProfile()
    assert np_.capabilities_for_model("NIVONA-0000000000-----") is None


# ---------------------------------------------------------------------------
# Recipe + MyCoffee layouts (Gap #5, #6)
# ---------------------------------------------------------------------------

def test_nivona_standard_recipe_layout_700():
    """700-family standard recipe layout matches upstream offsets."""
    np_ = NivonaProfile()
    layout = np_.standard_recipe_layout("700")
    assert layout is not None
    assert layout.strength_offset == 1
    assert layout.profile_offset == 2
    assert layout.temperature_offset == 3
    assert layout.two_cups_offset == 4
    assert layout.coffee_amount_offset == 5
    assert layout.water_amount_offset == 6
    assert layout.milk_amount_offset == 7
    assert layout.milk_foam_amount_offset == 8


def test_nivona_standard_recipe_layout_900_extended():
    """900-family uses per-fluid temperatures + overall_temperature."""
    np_ = NivonaProfile()
    layout = np_.standard_recipe_layout("900")
    assert layout.coffee_temperature_offset == 5
    assert layout.milk_foam_temperature_offset == 8
    assert layout.overall_temperature_offset == 13
    # v0.49.0: the *10 fluid scaling flag was reverted to False —
    # the assumption was not confirmed by observed machine behaviour.
    assert layout.fluid_write_scale_10 is False


def test_nivona_standard_recipe_register():
    """Register = 10000 + selector*100 + offset."""
    np_ = NivonaProfile()
    # 700-family Cappuccino (selector=4), strength (offset=1) → 10000+400+1 = 10401
    assert np_.standard_recipe_register(4, 1) == 10401
    # 700-family Hot Water (selector=7), milk_foam_amount (offset=8) → 10000+700+8 = 10708
    assert np_.standard_recipe_register(7, 8) == 10708


def test_nivona_mycoffee_layout_700():
    np_ = NivonaProfile()
    layout = np_.mycoffee_layout("700")
    assert layout.enabled_offset == 0
    assert layout.icon_offset == 1
    assert layout.name_offset == 2
    assert layout.strength_offset == 4


def test_nivona_mycoffee_register():
    """MyCoffee register = 20000 + slot*100 + offset."""
    np_ = NivonaProfile()
    # Slot 3, strength (offset=4) → 20000+300+4 = 20304
    assert np_.mycoffee_register(3, 4) == 20304


def test_nivona_layouts_cover_all_families():
    np_ = NivonaProfile()
    for fk in ("600", "700", "79x", "900", "900-light", "1030", "1040", "8000"):
        assert np_.standard_recipe_layout(fk) is not None, f"{fk} missing recipe layout"
        assert np_.mycoffee_layout(fk) is not None, f"{fk} missing MyCoffee layout"


def test_nivona_unknown_family_layout_returns_none():
    np_ = NivonaProfile()
    assert np_.standard_recipe_layout("1234") is None
    assert np_.mycoffee_layout("1234") is None


def test_nivona_recipe_contains_espresso():
    """Every family has Espresso at selector 0."""
    np_ = NivonaProfile()
    for family_key in ("600", "700", "79x", "900", "1030", "1040", "8000"):
        caps = np_.capabilities_for(family_key)
        first = caps.recipes[0]
        assert first.recipe_id == 0
        assert first.name == "Espresso"


# ---------------------------------------------------------------------------
# Error sugar
# ---------------------------------------------------------------------------

def test_feature_not_supported_exception():
    err = FeatureNotSupported("HJ", "nivona")
    assert "HJ" in str(err)
    assert "nivona" in str(err)
    assert err.opcode == "HJ"
    assert err.brand_slug == "nivona"
