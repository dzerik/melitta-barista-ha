"""Tests for the UI Contract v1 entity surface on sensor.py (Zone I-C).

Covers spec §3.4:
- block A bridge attributes on ``MelittaConnectionSensor`` — always
  present (including while disconnected and pre-handshake), fingerprint
  updates on machine-type refinement and recipe-cache generation bumps;
- block B live token attributes on ``MelittaStateSensor`` across
  connected / statusless / unknown-code paths (manipulation_token
  null-vs-"NONE" rule);
- regression: both sensors' ``native_value`` and availability behaviour
  is byte-identical to the pre-contract code (frozen per spec §5.2.3).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.melitta_barista.brands import MelittaProfile
from custom_components.melitta_barista.const import (
    DOMAIN,
    InfoMessage,
    MachineProcess,
    MachineType,
    Manipulation,
    SubProcess,
)
from custom_components.melitta_barista.protocol import MachineStatus
from custom_components.melitta_barista.sensor import (
    MelittaConnectionSensor,
    MelittaStateSensor,
    _MelittaSensorBase,
)
from custom_components.melitta_barista.ui_contract import CONTRACT_VERSION

from . import MOCK_ADDRESS, MOCK_CONFIG_DATA


def _client(status=None, connected=True, capabilities="default"):
    """MagicMock client with a real brand profile and real capabilities."""
    client = MagicMock()
    client.address = MOCK_ADDRESS
    client.connected = connected
    client.status = status
    client.machine_type = None
    client.brand = MelittaProfile()
    if capabilities == "default":
        client.capabilities = client.brand.capabilities_for("barista_ts")
    else:
        client.capabilities = capabilities
    client.recipe_cache_generation = 0
    return client


def _entry():
    entry = MagicMock()
    entry.entry_id = "entry-test-0001"
    return entry


def _connection_sensor(client) -> MelittaConnectionSensor:
    return MelittaConnectionSensor(client, _entry(), "Test Machine")


def _state_sensor(client) -> MelittaStateSensor:
    return MelittaStateSensor(client, _entry(), "Test Machine")


# ── Block A: connection sensor bridge attributes ─────────────────────────


class TestBridgeAttributes:
    """§3.4 block A on MelittaConnectionSensor."""

    def test_bridge_attrs_present_when_connected(self):
        sensor = _connection_sensor(_client(connected=True))
        attrs = sensor.extra_state_attributes
        assert attrs["entry_id"] == "entry-test-0001"
        assert attrs["contract_version"] == CONTRACT_VERSION
        assert attrs["connected"] is True
        fingerprint = attrs["contract_fingerprint"]
        assert isinstance(fingerprint, str) and len(fingerprint) == 12
        int(fingerprint, 16)  # 12 hex chars

    def test_bridge_attrs_present_when_disconnected(self):
        """Presence never flickers with machine state (spec §3.4 A)."""
        sensor = _connection_sensor(_client(connected=False))
        attrs = sensor.extra_state_attributes
        assert attrs["entry_id"] == "entry-test-0001"
        assert attrs["contract_version"] == CONTRACT_VERSION
        assert attrs["connected"] is False
        assert "contract_fingerprint" in attrs

    def test_bridge_attrs_pre_handshake_omits_fingerprint_only(self):
        """capabilities None → no fingerprint yet; the rest stays present."""
        sensor = _connection_sensor(_client(capabilities=None))
        attrs = sensor.extra_state_attributes
        assert attrs["entry_id"] == "entry-test-0001"
        assert attrs["contract_version"] == CONTRACT_VERSION
        assert attrs["connected"] is True
        assert "contract_fingerprint" not in attrs

    def test_fingerprint_changes_on_machine_type_refinement(self):
        client = _client()
        sensor = _connection_sensor(client)
        before = sensor.extra_state_attributes["contract_fingerprint"]
        client.machine_type = MachineType.BARISTA_TS
        after = sensor.extra_state_attributes["contract_fingerprint"]
        assert before != after

    def test_fingerprint_changes_on_recipe_cache_generation_bump(self):
        client = _client()
        sensor = _connection_sensor(client)
        before = sensor.extra_state_attributes["contract_fingerprint"]
        client.recipe_cache_generation = 1
        after = sensor.extra_state_attributes["contract_fingerprint"]
        assert before != after

    def test_no_available_override_added(self):
        """The connection sensor stays always-available (spec §2.1)."""
        assert "available" not in MelittaConnectionSensor.__dict__
        assert "available" not in _MelittaSensorBase.__dict__
        sensor = _connection_sensor(_client(connected=False))
        assert sensor.available is True

    def test_native_value_regression(self):
        """Frozen legacy strings (spec §5.2.3)."""
        assert _connection_sensor(_client(connected=True)).native_value == "Connected"
        assert (
            _connection_sensor(_client(connected=False)).native_value
            == "Disconnected"
        )


# ── Block B: state sensor live token attributes ──────────────────────────


class TestStatusTokenAttributes:
    """§3.4 block B on MelittaStateSensor."""

    def test_ready_status_tokens(self):
        status = MachineStatus(process=MachineProcess.READY)
        attrs = _state_sensor(_client(status=status)).extra_state_attributes
        assert attrs["process_token"] == "READY"
        assert attrs["sub_process_token"] is None
        assert attrs["manipulation_token"] == "NONE"
        assert attrs["is_brewing"] is False
        assert attrs["awaiting_confirmation"] is False

    def test_brewing_status_tokens(self):
        status = MachineStatus(
            process=MachineProcess.PRODUCT, sub_process=SubProcess.GRINDING,
        )
        attrs = _state_sensor(_client(status=status)).extra_state_attributes
        assert attrs["process_token"] == "PRODUCT"
        assert attrs["sub_process_token"] == "GRINDING"
        assert attrs["is_brewing"] is True

    def test_statusless_tokens_all_null(self):
        """manipulation_token is null iff status is None (spec §3.4)."""
        attrs = _state_sensor(_client(status=None)).extra_state_attributes
        assert attrs["process_token"] is None
        assert attrs["sub_process_token"] is None
        assert attrs["manipulation_token"] is None
        assert attrs["is_brewing"] is False
        assert attrs["awaiting_confirmation"] is False

    def test_unknown_process_code_gives_null_token(self):
        """Unmapped raw process code parses to process=None → null token."""
        status = MachineStatus(process=None, sub_process=None)
        attrs = _state_sensor(_client(status=status)).extra_state_attributes
        assert attrs["process_token"] is None
        assert attrs["manipulation_token"] == "NONE"

    def test_unknown_manipulation_code_maps_to_none_token(self):
        """Parsed-unknown manipulation codes serialize as "NONE" (§3.4)."""
        status = MachineStatus(process=MachineProcess.READY, manipulation=77)
        attrs = _state_sensor(_client(status=status)).extra_state_attributes
        assert attrs["manipulation_token"] == "NONE"
        assert attrs["awaiting_confirmation"] is False

    def test_prompt_manipulation_sets_awaiting_confirmation(self):
        status = MachineStatus(
            process=MachineProcess.READY, manipulation=Manipulation.FILL_WATER,
        )
        attrs = _state_sensor(_client(status=status)).extra_state_attributes
        assert attrs["manipulation_token"] == "FILL_WATER"
        assert attrs["awaiting_confirmation"] is True

    def test_legacy_attributes_unchanged(self):
        """process_id / info_messages keep their exact legacy content."""
        status = MachineStatus(
            process=MachineProcess.READY,
            info_messages=InfoMessage.FILL_BEANS_1,
        )
        attrs = _state_sensor(_client(status=status)).extra_state_attributes
        assert attrs["process_id"] == MachineProcess.READY.value
        assert attrs["info_messages"] == ["FILL_BEANS_1"]
        # No duplicate alias key was added (spec Appendix A).
        assert "info_message_tokens" not in attrs

    def test_native_value_and_availability_regression(self):
        """Frozen native_value strings and availability gate (spec §5.2.3)."""
        ready = _state_sensor(
            _client(status=MachineStatus(process=MachineProcess.READY))
        )
        assert ready.native_value == "Ready"
        assert ready.available is True

        brewing = _state_sensor(
            _client(status=MachineStatus(process=MachineProcess.PRODUCT))
        )
        assert brewing.native_value == "Brewing"

        statusless = _state_sensor(_client(status=None))
        assert statusless.native_value is None
        assert statusless.available is False

        disconnected = _state_sensor(
            _client(
                status=MachineStatus(process=MachineProcess.READY),
                connected=False,
            )
        )
        assert disconnected.available is False

        no_process = _state_sensor(_client(status=MachineStatus(process=None)))
        assert no_process.native_value is None
        assert no_process.available is True


# ── End-to-end: attributes reach the HA state machine ────────────────────


async def test_bridge_and_token_attrs_in_state_machine(
    hass: HomeAssistant,
) -> None:
    """Full setup: both attribute blocks survive HA state serialization."""
    client = _client(
        status=MachineStatus(
            process=MachineProcess.PRODUCT, sub_process=SubProcess.COFFEE,
        )
    )
    client.set_ble_device = MagicMock()
    client.add_status_callback = MagicMock()
    client.add_connection_callback = MagicMock()
    client.connect = AsyncMock(return_value=True)
    client.disconnect = AsyncMock()
    client.start_polling = MagicMock()
    client.firmware_version = "1.2.3"
    client.serial_number = "0123456789"
    client.model_name = "Melitta Barista"
    client.selected_recipe = None
    client.active_profile = 0
    client.profile_names = {0: "My Coffee"}
    client.directkey_recipes = {}

    entry = MockConfigEntry(
        domain=DOMAIN, data=MOCK_CONFIG_DATA, unique_id="aabbccddeeff",
    )
    entry.add_to_hass(hass)
    with (
        patch(
            "custom_components.melitta_barista.MelittaBleClient",
            return_value=client,
        ),
        patch(
            "custom_components.melitta_barista.bluetooth.async_ble_device_from_address",
            return_value=None,
        ),
        patch(
            "custom_components.melitta_barista.bluetooth.async_register_callback",
            return_value=lambda: None,
        ),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    states = hass.states.async_all("sensor")
    connection = next(s for s in states if s.entity_id.endswith("_connection"))
    assert connection.attributes["entry_id"] == entry.entry_id
    assert connection.attributes["contract_version"] == CONTRACT_VERSION
    assert connection.attributes["connected"] is True
    assert len(connection.attributes["contract_fingerprint"]) == 12

    state = next(s for s in states if s.entity_id.endswith("_state"))
    assert state.attributes["process_token"] == "PRODUCT"
    assert state.attributes["sub_process_token"] == "COFFEE"
    assert state.attributes["manipulation_token"] == "NONE"
    assert state.attributes["is_brewing"] is True
    assert state.attributes["awaiting_confirmation"] is False
