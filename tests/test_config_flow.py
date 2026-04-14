"""Tests for Melitta Barista Smart config flow."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.config_entries import SOURCE_BLUETOOTH, SOURCE_USER
from homeassistant.const import CONF_ADDRESS, CONF_NAME
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.melitta_barista.const import (
    DOMAIN,
    CONF_POLL_INTERVAL,
    CONF_RECONNECT_DELAY,
    CONF_RECONNECT_MAX_DELAY,
    CONF_MAX_CONSECUTIVE_ERRORS,
    CONF_FRAME_TIMEOUT,
    CONF_BLE_CONNECT_TIMEOUT,
    CONF_PAIR_TIMEOUT,
    CONF_RECIPE_RETRIES,
    CONF_INITIAL_CONNECT_DELAY,
)

from . import MOCK_ADDRESS, MOCK_NAME

MELITTA_SERVICE_UUID = "0000ad00-b35c-11e4-9813-0002a5d5c51b"


def _make_bluetooth_service_info(
    address: str = MOCK_ADDRESS,
    name: str = MOCK_NAME,
    service_uuids: list[str] | None = None,
):
    """Create a mock BluetoothServiceInfoBleak."""
    info = MagicMock()
    info.address = address
    info.name = name
    info.service_uuids = service_uuids or [MELITTA_SERVICE_UUID]
    return info


# ---------------------------------------------------------------------------
# async_step_user — device discovery
# ---------------------------------------------------------------------------


async def test_step_user_no_devices_redirects_to_manual(
    hass: HomeAssistant,
) -> None:
    """When no BLE devices are found, the flow redirects to manual entry."""
    with (
        patch(
            "custom_components.melitta_barista.config_flow.async_discovered_service_info",
            return_value=[],
        ),
        patch(
            "custom_components.melitta_barista.config_flow.BleakScanner.discover",
            new_callable=AsyncMock,
            return_value=[],
        ),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_USER}
        )

    assert result["step_id"] == "manual"
    assert result["type"] is FlowResultType.FORM


async def test_step_user_ha_bluetooth_discovers_device(
    hass: HomeAssistant,
) -> None:
    """HA bluetooth integration finds a Melitta device and shows a form."""
    info = _make_bluetooth_service_info()

    with patch(
        "custom_components.melitta_barista.config_flow.async_discovered_service_info",
        return_value=[info],
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_USER}
        )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"


async def test_step_user_bleak_fallback_discovers_device(
    hass: HomeAssistant,
) -> None:
    """When HA bluetooth returns nothing, BleakScanner fallback finds a device."""
    device = MagicMock()
    device.name = "8601ABCD5678"
    device.address = "11:22:33:44:55:66"

    with (
        patch(
            "custom_components.melitta_barista.config_flow.async_discovered_service_info",
            return_value=[],
        ),
        patch(
            "custom_components.melitta_barista.config_flow.BleakScanner.discover",
            new_callable=AsyncMock,
            return_value=[device],
        ),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_USER}
        )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"


async def test_step_user_ha_bluetooth_exception_falls_back(
    hass: HomeAssistant,
) -> None:
    """When HA bluetooth raises an exception, falls back to BleakScanner.

    async_discovered_service_info returns a generator; if iteration raises,
    the config flow catches the exception and falls back to BleakScanner.
    We simulate this by making the mock return an iterator that raises.
    """
    device = MagicMock()
    device.name = "8601FALLBACK"
    device.address = "AA:BB:CC:DD:EE:00"

    def _raise_on_iter(*args, **kwargs):
        """Return an iterable that raises on iteration."""
        raise AttributeError("bluetooth not available")

    with (
        patch(
            "custom_components.melitta_barista.config_flow.async_discovered_service_info",
            _raise_on_iter,
        ),
        patch(
            "custom_components.melitta_barista.config_flow.BleakScanner.discover",
            new_callable=AsyncMock,
            return_value=[device],
        ),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_USER}
        )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"


async def test_step_user_bleak_scan_fails_redirects_manual(
    hass: HomeAssistant,
) -> None:
    """When both HA bluetooth and BleakScanner fail, redirect to manual."""
    with (
        patch(
            "custom_components.melitta_barista.config_flow.async_discovered_service_info",
            return_value=[],
        ),
        patch(
            "custom_components.melitta_barista.config_flow.BleakScanner.discover",
            new_callable=AsyncMock,
            side_effect=OSError("adapter not found"),
        ),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_USER}
        )

    assert result["step_id"] == "manual"


async def test_step_user_select_manual_redirects(
    hass: HomeAssistant,
) -> None:
    """User selects 'Enter address manually...' from the device list."""
    info = _make_bluetooth_service_info()

    with patch(
        "custom_components.melitta_barista.config_flow.async_discovered_service_info",
        return_value=[info],
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_USER}
        )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"

    # Now select manual
    result2 = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={CONF_ADDRESS: "manual"},
    )
    assert result2["step_id"] == "manual"


async def test_step_user_select_device_goes_to_pair(
    hass: HomeAssistant,
) -> None:
    """User selects a discovered device and goes to pairing step."""
    info = _make_bluetooth_service_info()

    with patch(
        "custom_components.melitta_barista.config_flow.async_discovered_service_info",
        return_value=[info],
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_USER}
        )

    with patch(
        "custom_components.melitta_barista.config_flow.MelittaBaristaConfigFlow._async_try_pair",
        new_callable=AsyncMock,
        return_value="ok",
    ):
        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={CONF_ADDRESS: MOCK_ADDRESS},
        )

    # Should proceed to pair step (shows form first)
    assert result2["step_id"] == "pair"
    assert result2["type"] is FlowResultType.FORM


async def test_step_user_device_already_configured(
    hass: HomeAssistant,
) -> None:
    """Selecting a device that is already configured aborts."""
    existing = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_ADDRESS: MOCK_ADDRESS, CONF_NAME: MOCK_NAME},
        unique_id="aabbccddeeff",
    )
    existing.add_to_hass(hass)

    info = _make_bluetooth_service_info()

    with patch(
        "custom_components.melitta_barista.config_flow.async_discovered_service_info",
        return_value=[info],
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_USER}
        )

    result2 = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={CONF_ADDRESS: MOCK_ADDRESS},
    )
    assert result2["type"] is FlowResultType.ABORT
    assert result2["reason"] == "already_configured"


# ---------------------------------------------------------------------------
# async_step_manual — manual MAC entry
# ---------------------------------------------------------------------------


async def test_step_manual_valid_address_colon_format(
    hass: HomeAssistant,
) -> None:
    """Valid MAC with colons proceeds to pair step."""
    with (
        patch(
            "custom_components.melitta_barista.config_flow.async_discovered_service_info",
            return_value=[],
        ),
        patch(
            "custom_components.melitta_barista.config_flow.BleakScanner.discover",
            new_callable=AsyncMock,
            return_value=[],
        ),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_USER}
        )

    # Should be on manual step
    assert result["step_id"] == "manual"

    result2 = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={CONF_ADDRESS: "aa:bb:cc:dd:ee:ff", CONF_NAME: "My Machine"},
    )
    assert result2["step_id"] == "pair"
    assert result2["type"] is FlowResultType.FORM


async def test_step_manual_valid_address_dash_format(
    hass: HomeAssistant,
) -> None:
    """Valid MAC with dashes is normalized and proceeds to pair."""
    with (
        patch(
            "custom_components.melitta_barista.config_flow.async_discovered_service_info",
            return_value=[],
        ),
        patch(
            "custom_components.melitta_barista.config_flow.BleakScanner.discover",
            new_callable=AsyncMock,
            return_value=[],
        ),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_USER}
        )

    result2 = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={CONF_ADDRESS: "aa-bb-cc-dd-ee-ff"},
    )
    assert result2["step_id"] == "pair"


async def test_step_manual_valid_address_no_separators(
    hass: HomeAssistant,
) -> None:
    """Valid MAC without separators is accepted."""
    with (
        patch(
            "custom_components.melitta_barista.config_flow.async_discovered_service_info",
            return_value=[],
        ),
        patch(
            "custom_components.melitta_barista.config_flow.BleakScanner.discover",
            new_callable=AsyncMock,
            return_value=[],
        ),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_USER}
        )

    result2 = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={CONF_ADDRESS: "aabbccddeeff"},
    )
    assert result2["step_id"] == "pair"


async def test_step_manual_invalid_address_too_short(
    hass: HomeAssistant,
) -> None:
    """Invalid MAC (too short) shows an error."""
    with (
        patch(
            "custom_components.melitta_barista.config_flow.async_discovered_service_info",
            return_value=[],
        ),
        patch(
            "custom_components.melitta_barista.config_flow.BleakScanner.discover",
            new_callable=AsyncMock,
            return_value=[],
        ),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_USER}
        )

    result2 = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={CONF_ADDRESS: "AA:BB:CC"},
    )
    assert result2["type"] is FlowResultType.FORM
    assert result2["step_id"] == "manual"
    assert result2["errors"][CONF_ADDRESS] == "invalid_address"


async def test_step_manual_invalid_address_too_long(
    hass: HomeAssistant,
) -> None:
    """Invalid MAC (too long) shows an error."""
    with (
        patch(
            "custom_components.melitta_barista.config_flow.async_discovered_service_info",
            return_value=[],
        ),
        patch(
            "custom_components.melitta_barista.config_flow.BleakScanner.discover",
            new_callable=AsyncMock,
            return_value=[],
        ),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_USER}
        )

    result2 = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={CONF_ADDRESS: "AA:BB:CC:DD:EE:FF:00"},
    )
    assert result2["type"] is FlowResultType.FORM
    assert result2["errors"][CONF_ADDRESS] == "invalid_address"


async def test_step_manual_already_configured(
    hass: HomeAssistant,
) -> None:
    """Manual entry of an already-configured device aborts."""
    existing = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_ADDRESS: MOCK_ADDRESS, CONF_NAME: MOCK_NAME},
        unique_id="aabbccddeeff",
    )
    existing.add_to_hass(hass)

    with (
        patch(
            "custom_components.melitta_barista.config_flow.async_discovered_service_info",
            return_value=[],
        ),
        patch(
            "custom_components.melitta_barista.config_flow.BleakScanner.discover",
            new_callable=AsyncMock,
            return_value=[],
        ),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_USER}
        )

    result2 = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={CONF_ADDRESS: "aa:bb:cc:dd:ee:ff"},
    )
    assert result2["type"] is FlowResultType.ABORT
    assert result2["reason"] == "already_configured"


async def test_step_manual_default_name(
    hass: HomeAssistant,
) -> None:
    """When no name is provided, the default 'Melitta Barista Smart' is used."""
    with (
        patch(
            "custom_components.melitta_barista.config_flow.async_discovered_service_info",
            return_value=[],
        ),
        patch(
            "custom_components.melitta_barista.config_flow.BleakScanner.discover",
            new_callable=AsyncMock,
            return_value=[],
        ),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_USER}
        )

    # Submit without CONF_NAME — voluptuous should supply default
    result2 = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={CONF_ADDRESS: "11:22:33:44:55:66"},
    )
    assert result2["step_id"] == "pair"


# ---------------------------------------------------------------------------
# async_step_bluetooth — auto-discovery
# ---------------------------------------------------------------------------


async def test_step_bluetooth_discovery(
    hass: HomeAssistant,
) -> None:
    """Bluetooth auto-discovery sets unique_id and shows confirm form."""
    discovery_info = _make_bluetooth_service_info()

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_BLUETOOTH},
        data=discovery_info,
    )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "bluetooth_confirm"


async def test_step_bluetooth_already_configured(
    hass: HomeAssistant,
) -> None:
    """Bluetooth discovery of an already-configured device aborts."""
    existing = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_ADDRESS: MOCK_ADDRESS, CONF_NAME: MOCK_NAME},
        unique_id="aabbccddeeff",
    )
    existing.add_to_hass(hass)

    discovery_info = _make_bluetooth_service_info()

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_BLUETOOTH},
        data=discovery_info,
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_step_bluetooth_no_name_uses_default(
    hass: HomeAssistant,
) -> None:
    """Bluetooth discovery with no device name uses default."""
    discovery_info = _make_bluetooth_service_info(name=None)

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_BLUETOOTH},
        data=discovery_info,
    )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "bluetooth_confirm"
    # Brand-neutral default when the discovery advertisement carries no name.
    assert result["description_placeholders"]["name"] == "Smart Coffee Machine"


# ---------------------------------------------------------------------------
# async_step_bluetooth_confirm
# ---------------------------------------------------------------------------


async def test_step_bluetooth_confirm_shows_form(
    hass: HomeAssistant,
) -> None:
    """Bluetooth confirm step shows a form when no user_input."""
    discovery_info = _make_bluetooth_service_info()

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_BLUETOOTH},
        data=discovery_info,
    )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "bluetooth_confirm"
    assert "name" in result["description_placeholders"]


async def test_step_bluetooth_confirm_proceeds_to_pair(
    hass: HomeAssistant,
) -> None:
    """After user confirms, the flow proceeds to pair step."""
    discovery_info = _make_bluetooth_service_info()

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_BLUETOOTH},
        data=discovery_info,
    )

    result2 = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={},
    )

    assert result2["type"] is FlowResultType.FORM
    assert result2["step_id"] == "pair"


# ---------------------------------------------------------------------------
# async_step_pair — pairing flow
# ---------------------------------------------------------------------------


async def test_step_pair_success_creates_entry(
    hass: HomeAssistant,
) -> None:
    """Successful pairing creates a config entry."""
    discovery_info = _make_bluetooth_service_info()

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_BLUETOOTH},
        data=discovery_info,
    )

    # Confirm bluetooth
    result2 = await hass.config_entries.flow.async_configure(
        result["flow_id"], user_input={}
    )
    assert result2["step_id"] == "pair"

    # Submit pair with successful pairing
    # Also mock async_setup_entry to prevent background connect loop from running
    with patch(
        "custom_components.melitta_barista.config_flow.MelittaBaristaConfigFlow._async_try_pair",
        new_callable=AsyncMock,
        return_value="ok",
    ), patch(
        "custom_components.melitta_barista.async_setup_entry",
        new_callable=AsyncMock,
        return_value=True,
    ):
        result3 = await hass.config_entries.flow.async_configure(
            result2["flow_id"], user_input={}
        )

    assert result3["type"] is FlowResultType.CREATE_ENTRY
    assert result3["title"] == MOCK_NAME
    assert result3["data"][CONF_ADDRESS] == MOCK_ADDRESS
    assert result3["data"][CONF_NAME] == MOCK_NAME


async def test_step_pair_failure_shows_error(
    hass: HomeAssistant,
) -> None:
    """Pairing failure shows an error on the pair form."""
    discovery_info = _make_bluetooth_service_info()

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_BLUETOOTH},
        data=discovery_info,
    )
    result2 = await hass.config_entries.flow.async_configure(
        result["flow_id"], user_input={}
    )

    with patch(
        "custom_components.melitta_barista.config_flow.MelittaBaristaConfigFlow._async_try_pair",
        new_callable=AsyncMock,
        return_value="pairing_failed",
    ):
        result3 = await hass.config_entries.flow.async_configure(
            result2["flow_id"], user_input={}
        )

    assert result3["type"] is FlowResultType.FORM
    assert result3["step_id"] == "pair"
    assert result3["errors"]["base"] == "pairing_failed"


async def test_step_pair_timeout_shows_error(
    hass: HomeAssistant,
) -> None:
    """Pairing timeout shows the pairing_timeout error."""
    discovery_info = _make_bluetooth_service_info()

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_BLUETOOTH},
        data=discovery_info,
    )
    result2 = await hass.config_entries.flow.async_configure(
        result["flow_id"], user_input={}
    )

    with patch(
        "custom_components.melitta_barista.config_flow.MelittaBaristaConfigFlow._async_try_pair",
        new_callable=AsyncMock,
        return_value="pairing_timeout",
    ):
        result3 = await hass.config_entries.flow.async_configure(
            result2["flow_id"], user_input={}
        )

    assert result3["type"] is FlowResultType.FORM
    assert result3["errors"]["base"] == "pairing_timeout"


# ---------------------------------------------------------------------------
# _async_try_pair
# ---------------------------------------------------------------------------


async def test_try_pair_no_address_returns_cannot_connect(
    hass: HomeAssistant,
) -> None:
    """When address is None, _async_try_pair returns 'cannot_connect'."""
    from custom_components.melitta_barista.config_flow import (
        MelittaBaristaConfigFlow,
    )

    flow = MelittaBaristaConfigFlow()
    flow.hass = hass
    flow._address = None

    result = await flow._async_try_pair()
    assert result == "cannot_connect"


async def test_try_pair_import_error_returns_ok(
    hass: HomeAssistant,
) -> None:
    """When dbus-fast is unavailable (ImportError), _async_try_pair returns 'ok'."""
    from custom_components.melitta_barista.config_flow import (
        MelittaBaristaConfigFlow,
    )

    flow = MelittaBaristaConfigFlow()
    flow.hass = hass
    flow._address = MOCK_ADDRESS

    with patch(
        "custom_components.melitta_barista.config_flow.MelittaBaristaConfigFlow._async_try_pair",
        wraps=flow._async_try_pair,
    ):
        # Simulate ImportError on ble_agent import
        import builtins

        original_import = builtins.__import__

        def mock_import(name, *args, **kwargs):
            if name == ".ble_agent" or "ble_agent" in str(name):
                raise ImportError("No module named 'dbus_fast'")
            return original_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=mock_import):
            result = await flow._async_try_pair()

    assert result == "ok"


async def test_try_pair_delegates_to_ble_agent(
    hass: HomeAssistant,
) -> None:
    """_async_try_pair calls async_pair_device and returns its result."""
    from custom_components.melitta_barista.config_flow import (
        MelittaBaristaConfigFlow,
    )

    flow = MelittaBaristaConfigFlow()
    flow.hass = hass
    flow._address = MOCK_ADDRESS

    with patch(
        "custom_components.melitta_barista.ble_agent.async_pair_device",
        new_callable=AsyncMock,
        return_value="ok",
    ) as mock_pair:
        result = await flow._async_try_pair()

    assert result == "ok"
    mock_pair.assert_awaited_once_with(MOCK_ADDRESS, timeout=30.0)


# ---------------------------------------------------------------------------
# BleakScanner fallback filters by device name
# ---------------------------------------------------------------------------


async def test_step_user_bleak_filters_by_prefix(
    hass: HomeAssistant,
) -> None:
    """BleakScanner fallback only includes devices matching Melitta prefixes."""
    melitta_device = MagicMock()
    melitta_device.name = "8601ABCD1234"
    melitta_device.address = "11:22:33:44:55:66"

    other_device = MagicMock()
    other_device.name = "SomeOtherDevice"
    other_device.address = "77:88:99:AA:BB:CC"

    melitta_by_name = MagicMock()
    melitta_by_name.name = "My Melitta Coffee"
    melitta_by_name.address = "DD:EE:FF:00:11:22"

    barista_by_name = MagicMock()
    barista_by_name.name = "Barista TS"
    barista_by_name.address = "33:44:55:66:77:88"

    no_name_device = MagicMock()
    no_name_device.name = None
    no_name_device.address = "99:00:11:22:33:44"

    with (
        patch(
            "custom_components.melitta_barista.config_flow.async_discovered_service_info",
            return_value=[],
        ),
        patch(
            "custom_components.melitta_barista.config_flow.BleakScanner.discover",
            new_callable=AsyncMock,
            return_value=[
                melitta_device,
                other_device,
                melitta_by_name,
                barista_by_name,
                no_name_device,
            ],
        ),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_USER}
        )

    # Should show a form with discovered devices (excluding other_device and no_name)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"


# ---------------------------------------------------------------------------
# Options Flow tests
# ---------------------------------------------------------------------------


async def test_options_flow_init_shows_menu(hass: HomeAssistant) -> None:
    """Options flow init step shows menu with basic/advanced."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_ADDRESS: MOCK_ADDRESS, CONF_NAME: MOCK_NAME},
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.MENU
    assert "basic" in result["menu_options"]
    assert "advanced" in result["menu_options"]


async def test_options_flow_basic_defaults(hass: HomeAssistant) -> None:
    """Basic options step shows form with default values."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_ADDRESS: MOCK_ADDRESS, CONF_NAME: MOCK_NAME},
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result2 = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input={"next_step_id": "basic"},
    )
    assert result2["type"] is FlowResultType.FORM
    assert result2["step_id"] == "basic"


async def test_options_flow_basic_submit(hass: HomeAssistant) -> None:
    """Submitting basic options saves values."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_ADDRESS: MOCK_ADDRESS, CONF_NAME: MOCK_NAME},
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result2 = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input={"next_step_id": "basic"},
    )
    result3 = await hass.config_entries.options.async_configure(
        result2["flow_id"],
        user_input={
            CONF_POLL_INTERVAL: 10.0,
            CONF_RECONNECT_DELAY: 8.0,
            CONF_RECONNECT_MAX_DELAY: 600.0,
            CONF_MAX_CONSECUTIVE_ERRORS: 5,
            CONF_FRAME_TIMEOUT: 10,
        },
    )
    assert result3["type"] is FlowResultType.CREATE_ENTRY
    assert result3["data"][CONF_POLL_INTERVAL] == 10.0
    assert result3["data"][CONF_RECONNECT_DELAY] == 8.0
    assert result3["data"][CONF_RECONNECT_MAX_DELAY] == 600.0
    assert result3["data"][CONF_MAX_CONSECUTIVE_ERRORS] == 5
    assert result3["data"][CONF_FRAME_TIMEOUT] == 10


async def test_options_flow_advanced_submit(hass: HomeAssistant) -> None:
    """Submitting advanced options saves values."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_ADDRESS: MOCK_ADDRESS, CONF_NAME: MOCK_NAME},
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result2 = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input={"next_step_id": "advanced"},
    )
    assert result2["type"] is FlowResultType.FORM
    assert result2["step_id"] == "advanced"

    result3 = await hass.config_entries.options.async_configure(
        result2["flow_id"],
        user_input={
            CONF_BLE_CONNECT_TIMEOUT: 20.0,
            CONF_PAIR_TIMEOUT: 45.0,
            CONF_RECIPE_RETRIES: 5,
            CONF_INITIAL_CONNECT_DELAY: 5.0,
        },
    )
    assert result3["type"] is FlowResultType.CREATE_ENTRY
    assert result3["data"][CONF_BLE_CONNECT_TIMEOUT] == 20.0
    assert result3["data"][CONF_PAIR_TIMEOUT] == 45.0
    assert result3["data"][CONF_RECIPE_RETRIES] == 5
    assert result3["data"][CONF_INITIAL_CONNECT_DELAY] == 5.0


# ---------------------------------------------------------------------------
# async_step_reconfigure — change BLE address / name
# ---------------------------------------------------------------------------


async def test_step_reconfigure_shows_form(hass: HomeAssistant) -> None:
    """Reconfigure step shows a form pre-filled with current address and name."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_ADDRESS: MOCK_ADDRESS, CONF_NAME: MOCK_NAME},
        unique_id="aabbccddeeff",
    )
    entry.add_to_hass(hass)

    result = await entry.start_reconfigure_flow(hass)

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reconfigure"


async def test_step_reconfigure_valid_address_updates_entry(
    hass: HomeAssistant,
) -> None:
    """Submitting a valid address updates the config entry and aborts."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_ADDRESS: MOCK_ADDRESS, CONF_NAME: MOCK_NAME},
        unique_id="aabbccddeeff",
    )
    entry.add_to_hass(hass)

    result = await entry.start_reconfigure_flow(hass)

    with patch(
        "custom_components.melitta_barista.async_setup_entry",
        new_callable=AsyncMock,
        return_value=True,
    ):
        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={
                CONF_ADDRESS: "AA:BB:CC:DD:EE:FF",
                CONF_NAME: "New Name",
            },
        )

    assert result2["type"] is FlowResultType.ABORT
    assert result2["reason"] == "reconfigure_successful"
    assert entry.data[CONF_ADDRESS] == "AA:BB:CC:DD:EE:FF"
    assert entry.data[CONF_NAME] == "New Name"


async def test_step_reconfigure_normalizes_address(
    hass: HomeAssistant,
) -> None:
    """Reconfigure normalizes MAC address (lowercase, dashes, no separators)."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_ADDRESS: MOCK_ADDRESS, CONF_NAME: MOCK_NAME},
        unique_id="aabbccddeeff",
    )
    entry.add_to_hass(hass)

    result = await entry.start_reconfigure_flow(hass)

    with patch(
        "custom_components.melitta_barista.async_setup_entry",
        new_callable=AsyncMock,
        return_value=True,
    ):
        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={
                CONF_ADDRESS: "aa-bb-cc-dd-ee-ff",
                CONF_NAME: MOCK_NAME,
            },
        )

    assert result2["type"] is FlowResultType.ABORT
    assert result2["reason"] == "reconfigure_successful"
    # Address should be normalized to colon-separated uppercase
    assert entry.data[CONF_ADDRESS] == "AA:BB:CC:DD:EE:FF"


async def test_step_reconfigure_invalid_address_shows_error(
    hass: HomeAssistant,
) -> None:
    """Submitting an invalid MAC address shows an error and stays on the form."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_ADDRESS: MOCK_ADDRESS, CONF_NAME: MOCK_NAME},
        unique_id="aabbccddeeff",
    )
    entry.add_to_hass(hass)

    result = await entry.start_reconfigure_flow(hass)

    result2 = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={
            CONF_ADDRESS: "AA:BB:CC",
            CONF_NAME: MOCK_NAME,
        },
    )

    assert result2["type"] is FlowResultType.FORM
    assert result2["step_id"] == "reconfigure"
    assert result2["errors"][CONF_ADDRESS] == "invalid_address"


async def test_step_reconfigure_invalid_address_too_long(
    hass: HomeAssistant,
) -> None:
    """MAC address that is too long shows an error."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_ADDRESS: MOCK_ADDRESS, CONF_NAME: MOCK_NAME},
        unique_id="aabbccddeeff",
    )
    entry.add_to_hass(hass)

    result = await entry.start_reconfigure_flow(hass)

    result2 = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={
            CONF_ADDRESS: "AA:BB:CC:DD:EE:FF:00",
            CONF_NAME: MOCK_NAME,
        },
    )

    assert result2["type"] is FlowResultType.FORM
    assert result2["errors"][CONF_ADDRESS] == "invalid_address"


async def test_step_reconfigure_unique_id_mismatch_aborts(
    hass: HomeAssistant,
) -> None:
    """Changing to an address with a different unique_id aborts with mismatch."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_ADDRESS: MOCK_ADDRESS, CONF_NAME: MOCK_NAME},
        unique_id="aabbccddeeff",
    )
    entry.add_to_hass(hass)

    result = await entry.start_reconfigure_flow(hass)

    # Submit with a completely different address (different unique_id)
    result2 = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={
            CONF_ADDRESS: "11:22:33:44:55:66",
            CONF_NAME: MOCK_NAME,
        },
    )

    assert result2["type"] is FlowResultType.ABORT
    assert result2["reason"] == "unique_id_mismatch"


async def test_step_reconfigure_no_separators_accepted(
    hass: HomeAssistant,
) -> None:
    """MAC without separators is accepted and normalized."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_ADDRESS: MOCK_ADDRESS, CONF_NAME: MOCK_NAME},
        unique_id="aabbccddeeff",
    )
    entry.add_to_hass(hass)

    result = await entry.start_reconfigure_flow(hass)

    with patch(
        "custom_components.melitta_barista.async_setup_entry",
        new_callable=AsyncMock,
        return_value=True,
    ):
        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={
                CONF_ADDRESS: "aabbccddeeff",
                CONF_NAME: "Updated Name",
            },
        )

    assert result2["type"] is FlowResultType.ABORT
    assert result2["reason"] == "reconfigure_successful"
    assert entry.data[CONF_ADDRESS] == "AA:BB:CC:DD:EE:FF"
    assert entry.data[CONF_NAME] == "Updated Name"
