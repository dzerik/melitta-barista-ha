"""Unit tests for the Nivona brew-override pipeline (follow-up to PR #7).

Covers:
- NivonaBrewOverrideNumber.is_user_set / extra_state_attributes / restore
- NivonaBrewOverrideNumber reset-event handling
- NivonaBrewButton._collect_user_overrides — all-or-nothing temp-recipe write
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
        n.hass = MagicMock()
        n.async_on_remove = MagicMock()
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
        n.hass = MagicMock()
        n.async_on_remove = MagicMock()
        last = SimpleNamespace(state="4", attributes={})
        with patch.object(NivonaBrewOverrideNumber, "async_get_last_state",
                          AsyncMock(return_value=last)):
            await n.async_added_to_hass()
        assert n.native_value == 4
        assert n.is_user_set is False

    def test_reset_event_clears_user_set_and_restores_default(self):
        """Reset event handler reverts value to default and clears the flag."""
        n = _make_override(default=3)
        n._user_set = True
        n._attr_native_value = 5
        n.async_write_ha_state = MagicMock()
        n._handle_reset_event(SimpleNamespace())
        assert n.native_value == 3
        assert n.is_user_set is False
        n.async_write_ha_state.assert_called_once()


class TestCollectUserOverrides:
    """NivonaBrewButton sends a complete temp recipe when any field is user-set."""

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

    def test_sends_all_readable_fields_when_any_user_set(self):
        """All-or-nothing: one user-set field → every readable slider is sent.

        The firmware fills any field omitted from a temp recipe with its
        hardware default (not the saved-recipe value), so a partial write
        would brew wrong amounts for the untouched fields.
        """
        states = {
            "number.brew_strength": SimpleNamespace(
                state="5", attributes={"user_set": True}),
            "number.brew_coffee_amount": SimpleNamespace(
                state="120", attributes={"user_set": False}),
            "number.brew_water_amount": SimpleNamespace(
                state="100", attributes={"user_set": False}),
            "number.brew_temperature": SimpleNamespace(
                state="92", attributes={"user_set": True}),
            "number.brew_milk_amount": SimpleNamespace(
                state="60", attributes={"user_set": False}),
        }
        btn, reg = self._make_button(states)
        result = btn._collect_user_overrides(reg)
        assert result == {
            "strength": 5, "coffee_amount": 120, "water_amount": 100,
            "temperature": 92, "milk_amount": 60,
        }

    def test_water_amount_in_override_fields(self):
        """water_amount must be a recognised override field (ported from fork)."""
        assert "water_amount" in NivonaBrewButton._OVERRIDE_FIELDS

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


class TestResetOverridesButton:
    """NivonaResetOverridesButton fires a reset event per override slider."""

    @pytest.mark.asyncio
    async def test_press_fires_reset_event_per_field(self):
        from custom_components.melitta_barista.button import (
            NivonaResetOverridesButton,
        )
        from custom_components.melitta_barista.const import DOMAIN

        client = MagicMock()
        client.address = "AA:BB:CC:DD:EE:FF"
        btn = NivonaResetOverridesButton.__new__(NivonaResetOverridesButton)
        btn._client = client
        btn.hass = MagicMock()

        await btn.async_press()

        fired = [c.args[0] for c in btn.hass.bus.async_fire.call_args_list]
        for field in NivonaResetOverridesButton._OVERRIDE_FIELDS:
            assert (
                f"{DOMAIN}_reset_override_{client.address}_brew_{field}" in fired
            )
        assert btn.available is True
