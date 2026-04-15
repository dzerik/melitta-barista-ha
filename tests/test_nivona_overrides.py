"""Unit tests for the Nivona brew-override pipeline (follow-up to PR #7).

Covers:
- NivonaBrewOverrideNumber.is_user_set / extra_state_attributes / restore
- NivonaBrewButton._collect_user_overrides — only forwards user-set values
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.melitta_barista.button import NivonaBrewButton
from custom_components.melitta_barista.number import NivonaBrewOverrideNumber


def _make_override(field: str = "strength", default: float = 3) -> NivonaBrewOverrideNumber:
    client = MagicMock()
    client.address = "AA:BB:CC:DD:EE:FF"
    entry = MagicMock()
    return NivonaBrewOverrideNumber(
        client=client, entry=entry, machine_name="Nivona",
        field=field, label="Strength", icon="mdi:coffee",
        min_v=1, max_v=5, step=1, default=default, unit=None,
    )


class TestNivonaBrewOverrideNumber:
    def test_default_state_is_not_user_set(self):
        n = _make_override(default=3)
        assert n.native_value == 3
        assert n.is_user_set is False
        assert n.extra_state_attributes == {"user_set": False}

    @pytest.mark.asyncio
    async def test_set_native_value_marks_user_set(self):
        n = _make_override(default=3)
        n.async_write_ha_state = MagicMock()
        await n.async_set_native_value(5)
        assert n.native_value == 5
        assert n.is_user_set is True
        assert n.extra_state_attributes == {"user_set": True}

    @pytest.mark.asyncio
    async def test_restore_user_set_flag_from_last_state(self):
        n = _make_override(default=3)
        last = SimpleNamespace(state="4", attributes={"user_set": True})
        with patch.object(NivonaBrewOverrideNumber, "async_get_last_state",
                          AsyncMock(return_value=last)):
            await n.async_added_to_hass()
        assert n.native_value == 4
        assert n.is_user_set is True

    @pytest.mark.asyncio
    async def test_restore_without_user_set_attr_stays_default(self):
        """Pre-0.49.2 restore (no attr) must not flip the flag on."""
        n = _make_override(default=3)
        last = SimpleNamespace(state="4", attributes={})
        with patch.object(NivonaBrewOverrideNumber, "async_get_last_state",
                          AsyncMock(return_value=last)):
            await n.async_added_to_hass()
        assert n.native_value == 4
        assert n.is_user_set is False


class TestCollectUserOverrides:
    """NivonaBrewButton only forwards values flagged user_set=True."""

    def _make_button(self, states: dict[str, SimpleNamespace]):
        client = MagicMock()
        client.address = "AA:BB:CC:DD:EE:FF"
        # Bypass __init__ — only need address + hass + a registry-shaped obj
        btn = NivonaBrewButton.__new__(NivonaBrewButton)
        btn._client = client
        btn.hass = MagicMock()
        btn.hass.states.get = lambda eid: states.get(eid)
        registry = MagicMock()
        registry.entities = {
            f"number.brew_{f}": SimpleNamespace(
                unique_id=f"{client.address}_brew_{f}",
            )
            for f in NivonaBrewButton._OVERRIDE_FIELDS
        }
        return btn, registry

    def test_skips_fields_without_user_set_flag(self):
        states = {
            f"number.brew_{f}": SimpleNamespace(
                state="3", attributes={"user_set": False},
            )
            for f in NivonaBrewButton._OVERRIDE_FIELDS
        }
        btn, reg = self._make_button(states)
        assert btn._collect_user_overrides(reg) == {}

    def test_includes_only_user_set_fields(self):
        states = {
            "number.brew_strength": SimpleNamespace(
                state="5", attributes={"user_set": True}),
            "number.brew_coffee_amount": SimpleNamespace(
                state="120", attributes={"user_set": False}),
            "number.brew_temperature": SimpleNamespace(
                state="92", attributes={"user_set": True}),
            "number.brew_milk_amount": SimpleNamespace(
                state="60", attributes={"user_set": False}),
        }
        btn, reg = self._make_button(states)
        result = btn._collect_user_overrides(reg)
        assert result == {"strength": 5, "temperature": 92}

    def test_skips_unavailable_states(self):
        states = {
            "number.brew_strength": SimpleNamespace(
                state="unavailable", attributes={"user_set": True}),
        }
        btn, reg = self._make_button(states)
        assert btn._collect_user_overrides(reg) == {}

    def test_handles_invalid_numeric_string(self):
        states = {
            "number.brew_strength": SimpleNamespace(
                state="not_a_number", attributes={"user_set": True}),
        }
        btn, reg = self._make_button(states)
        assert btn._collect_user_overrides(reg) == {}
