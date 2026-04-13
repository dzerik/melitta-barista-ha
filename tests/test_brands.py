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
    profile = detect_from_advertisement("NIVONA-7565730710-----")
    assert profile is not None and profile.brand_slug == "nivona"


def test_advertisement_no_match():
    assert detect_from_advertisement("Random Device") is None
    assert detect_from_advertisement("") is None
    assert detect_from_advertisement(None) is None


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


# ---------------------------------------------------------------------------
# Error sugar
# ---------------------------------------------------------------------------

def test_feature_not_supported_exception():
    err = FeatureNotSupported("HJ", "nivona")
    assert "HJ" in str(err)
    assert "nivona" in str(err)
    assert err.opcode == "HJ"
    assert err.brand_slug == "nivona"
