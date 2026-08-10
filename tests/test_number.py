"""Tests for number platform setup — capability-driven exclusion of
generic Eugster settings (issue #10: the Language register is not
implemented by Nivona firmware; the entity was dead on a live NICR 790).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.melitta_barista import number
from custom_components.melitta_barista.brands import get_profile
from custom_components.melitta_barista.brands.nivona._family_700 import (
    CAPABILITIES_79X,
)
from custom_components.melitta_barista.coffee_platform.domain import (
    MachineCapabilities,
)
from custom_components.melitta_barista.const import DOMAIN, MachineSettingId

from . import MOCK_ADDRESS, MOCK_CONFIG_DATA


def _mock_client(brand_slug: str = "melitta", capabilities=None):
    client = MagicMock()
    client.address = MOCK_ADDRESS
    client.connected = True
    client.brand = get_profile(brand_slug)
    client.capabilities = capabilities
    client.add_connection_callback = MagicMock()
    client.remove_connection_callback = MagicMock()
    client.read_setting = AsyncMock(return_value=None)
    client.write_setting = AsyncMock(return_value=True)
    return client


async def _run_setup(hass: HomeAssistant, client) -> list:
    entry = MockConfigEntry(domain=DOMAIN, data=MOCK_CONFIG_DATA)
    entry.add_to_hass(hass)
    entry.runtime_data = client
    added: list = []

    def _capture(entities, update_before_add=False):
        added.extend(entities)

    await number.async_setup_entry(hass, entry, _capture)
    return added


def _setting_unique_ids(entities) -> set[str]:
    ids: set[str] = set()
    for e in entities:
        uid = getattr(e, "unique_id", None)
        if uid and "_setting_" in uid:
            ids.add(uid)
    return ids


def test_79x_capabilities_exclude_language() -> None:
    """The 79x family declares the Language register as unsupported."""
    assert CAPABILITIES_79X.unsupported_generic_setting_ids == frozenset(
        {int(MachineSettingId.LANGUAGE)}
    )


def test_all_nivona_families_exclude_language() -> None:
    profile = get_profile("nivona")
    for family_key, caps in profile.families.items():
        assert int(MachineSettingId.LANGUAGE) in (
            caps.unsupported_generic_setting_ids
        ), f"family {family_key} must exclude LANGUAGE"


def test_melitta_caps_have_no_generic_exclusions() -> None:
    profile = get_profile("melitta")
    for family_key, caps in profile.families.items():
        assert caps.unsupported_generic_setting_ids == frozenset(), family_key
    # Backwards-compat guard: the field must stay keyword-defaulted.
    caps = MachineCapabilities(family_key="x", model_name="y")
    assert caps.unsupported_generic_setting_ids == frozenset()


async def test_setup_skips_language_for_nivona_79x(hass: HomeAssistant) -> None:
    client = _mock_client("nivona", capabilities=CAPABILITIES_79X)
    entities = _setting_unique_ids(await _run_setup(hass, client))
    assert f"{MOCK_ADDRESS}_setting_{int(MachineSettingId.LANGUAGE)}" not in entities
    # The other generic settings survive.
    assert any(uid.endswith("_setting_11") for uid in entities)


async def test_setup_keeps_language_for_melitta(hass: HomeAssistant) -> None:
    client = _mock_client("melitta", capabilities=None)
    entities = _setting_unique_ids(await _run_setup(hass, client))
    assert f"{MOCK_ADDRESS}_setting_{int(MachineSettingId.LANGUAGE)}" in entities


async def test_setup_uses_scanner_fallback_for_exclusion(
    hass: HomeAssistant,
) -> None:
    """caps None at setup + scanner cache resolving to 79x → still excluded."""
    client = _mock_client("nivona", capabilities=None)
    with patch.object(
        number, "resolve_caps_from_scanner", return_value=CAPABILITIES_79X,
    ):
        entities = _setting_unique_ids(await _run_setup(hass, client))
    assert (
        f"{MOCK_ADDRESS}_setting_{int(MachineSettingId.LANGUAGE)}"
        not in entities
    )


async def test_setup_no_caps_falls_back_to_no_exclusion(
    hass: HomeAssistant,
) -> None:
    """Documented degradation: with no capability info at all, the full
    generic set is registered (cleaned up on a later successful setup)."""
    client = _mock_client("nivona", capabilities=None)
    with patch.object(number, "resolve_caps_from_scanner", return_value=None):
        entities = _setting_unique_ids(await _run_setup(hass, client))
    assert f"{MOCK_ADDRESS}_setting_{int(MachineSettingId.LANGUAGE)}" in entities
