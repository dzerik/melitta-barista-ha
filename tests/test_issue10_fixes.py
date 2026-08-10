"""Tests for the issue-#10 backlog fixes (Tepliuk case, Nivona NICR 790).

Covers:
- title fix: no more "Nivona Nivona NICR 79x" (model_name already carries
  the brand prefix);
- brand picker: when the brand cannot be inferred from the advertisement,
  the flow ASKS instead of silently defaulting to Melitta;
- duplicate-entry guard: uniqueness re-checked at entry-creation time, not
  only at step-entry time (two parallel flows produced two entries for one
  machine in the field).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.config_entries import SOURCE_BLUETOOTH, SOURCE_USER
from homeassistant.const import CONF_ADDRESS, CONF_NAME
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.melitta_barista.config_flow import _describe_advertisement
from custom_components.melitta_barista.const import CONF_BRAND, DOMAIN

from . import MOCK_ADDRESS, MOCK_NAME

MELITTA_SERVICE_UUID = "0000ad00-b35c-11e4-9813-0002a5d5c51b"
# Prefix 790 → NICR 79x family; matches the Nivona advertisement regex.
NIVONA_79X_NAME = "7902660001-----"
NIVONA_ADDRESS = "C8:F4:5B:5B:D9:43"


def _make_info(address: str = MOCK_ADDRESS, name: str | None = MOCK_NAME):
    info = MagicMock()
    info.address = address
    info.name = name
    info.service_uuids = [MELITTA_SERVICE_UUID]
    return info


def _patch_pair_ok():
    return patch(
        "custom_components.melitta_barista.config_flow."
        "MelittaBaristaConfigFlow._async_try_pair",
        new_callable=AsyncMock,
        return_value="ok",
    )


def _patch_setup_entry():
    return patch(
        "custom_components.melitta_barista.async_setup_entry",
        new_callable=AsyncMock,
        return_value=True,
    )


# ---------------------------------------------------------------------------
# Title fix (item c)
# ---------------------------------------------------------------------------


def test_describe_advertisement_nivona_display_not_doubled() -> None:
    """model_name already contains 'Nivona' — display must not repeat it."""
    desc = _describe_advertisement(NIVONA_79X_NAME)
    assert desc["brand"] == "Nivona"
    assert desc["display"] == "Nivona NICR 79x"
    assert "Nivona Nivona" not in desc["display"]
    assert not desc["model"].startswith("Nivona")


def test_describe_advertisement_melitta_unchanged() -> None:
    """Melitta model names carry no brand prefix — display unchanged."""
    desc = _describe_advertisement(MOCK_NAME)
    assert desc["brand"] == "Melitta"
    assert desc["display"] == "Melitta Barista TS Smart"
    assert desc["model"] == "Barista TS Smart"


async def test_step_pair_nivona_title_not_doubled(hass: HomeAssistant) -> None:
    """Full discovery flow for a Nivona creates a clean title."""
    info = _make_info(address=NIVONA_ADDRESS, name=NIVONA_79X_NAME)

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_BLUETOOTH}, data=info,
    )
    assert result["step_id"] == "bluetooth_confirm"

    with _patch_pair_ok(), _patch_setup_entry():
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input={},
        )
        assert result["step_id"] == "pair"
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input={},
        )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Nivona NICR 79x"
    assert result["data"][CONF_BRAND] == "nivona"
    assert "Nivona Nivona" not in result["title"]


# ---------------------------------------------------------------------------
# Brand picker (item b)
# ---------------------------------------------------------------------------


async def test_unknown_brand_shows_brand_step(hass: HomeAssistant) -> None:
    """No local_name in the advertisement → the flow asks for the brand."""
    info = _make_info(address=NIVONA_ADDRESS, name=None)

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_BLUETOOTH}, data=info,
    )
    assert result["step_id"] == "bluetooth_confirm"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], user_input={},
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "brand"


async def test_brand_pick_nivona_creates_nivona_entry(
    hass: HomeAssistant,
) -> None:
    """Picking Nivona in the brand step lands in the entry data (the exact
    regression: a Nivona used to be silently created as brand=melitta)."""
    info = _make_info(address=NIVONA_ADDRESS, name=None)

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_BLUETOOTH}, data=info,
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], user_input={},
    )
    assert result["step_id"] == "brand"

    with _patch_pair_ok(), _patch_setup_entry():
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input={CONF_BRAND: "nivona"},
        )
        assert result["step_id"] == "pair"
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input={},
        )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_BRAND] == "nivona"
    assert "Nivona" in result["title"]


async def test_brand_pick_melitta_creates_melitta_entry(
    hass: HomeAssistant,
) -> None:
    info = _make_info(name=None)

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_BLUETOOTH}, data=info,
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], user_input={},
    )
    assert result["step_id"] == "brand"

    with _patch_pair_ok(), _patch_setup_entry():
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input={CONF_BRAND: "melitta"},
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input={},
        )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_BRAND] == "melitta"
    assert "Melitta" in result["title"]


async def test_brand_step_skipped_when_brand_detected(
    hass: HomeAssistant,
) -> None:
    """Known-brand advertisements never see the brand step (no UX change)."""
    info = _make_info()  # MOCK_NAME → Melitta

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_BLUETOOTH}, data=info,
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], user_input={},
    )
    assert result["step_id"] == "pair"


# ---------------------------------------------------------------------------
# Duplicate-entry guard (item a)
# ---------------------------------------------------------------------------


async def test_duplicate_entry_blocked_at_create(hass: HomeAssistant) -> None:
    """An entry appearing while the pair form is open must block creation.

    The step-entry uniqueness check can be arbitrarily stale: the pair form
    sits open indefinitely, and an entry for the same address can appear in
    the meantime (a second flow path, an HA-restart-restored flow, etc. —
    two entries for one machine were observed in the field, issue #10).
    Uniqueness must be re-verified at entry-creation time.
    """
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    info = _make_info()

    # Flow reaches the pair form while no entry exists yet.
    flow = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_BLUETOOTH}, data=info,
    )
    with _patch_pair_ok(), _patch_setup_entry():
        flow = await hass.config_entries.flow.async_configure(
            flow["flow_id"], user_input={},
        )
        assert flow["step_id"] == "pair"

        # An entry for the same address materializes behind the flow's back.
        MockConfigEntry(
            domain=DOMAIN,
            data={CONF_ADDRESS: MOCK_ADDRESS, CONF_NAME: MOCK_NAME},
            unique_id=MOCK_ADDRESS.replace(":", "").lower(),
        ).add_to_hass(hass)

        # Submitting the stale pair form must abort, not create a duplicate.
        done = await hass.config_entries.flow.async_configure(
            flow["flow_id"], user_input={},
        )

    assert done["type"] is FlowResultType.ABORT
    assert done["reason"] == "already_configured"
    assert len(hass.config_entries.async_entries(DOMAIN)) == 1
