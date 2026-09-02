"""Tests for the `melitta_barista/ui_contract/get` WS endpoint.

Zone I-B of the UI Contract v1 (docs/UI_CONTRACT.md §2.2 / §7.1):
registration inside `async_register_panel_websocket`, the happy path
against a mocked client, the three error codes (`entry_not_found`,
`client_not_ready`, `contract_not_ready`) and non-admin access.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
import voluptuous as vol

from custom_components.melitta_barista import panel_api
from custom_components.melitta_barista.brands.melitta import MelittaProfile
from custom_components.melitta_barista.const import DOMAIN, MachineType
from custom_components.melitta_barista.ui_contract import CONTRACT_VERSION

UI_CONTRACT_TYPE = "melitta_barista/ui_contract/get"


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class FakeClient:
    """Duck-typed stand-in for CoffeeMachineClient (contract inputs only)."""

    def __init__(
        self,
        brand,
        capabilities,
        machine_type=None,
        connected=True,
        recipe_cache_generation=0,
    ):
        self.brand = brand
        self.capabilities = capabilities
        self.machine_type = machine_type
        self.connected = connected
        self.recipe_cache_generation = recipe_cache_generation


def make_melitta_client(**kwargs):
    """Melitta Barista TS client with real profile capabilities."""
    profile = MelittaProfile()
    caps = profile.capabilities_for("barista_ts")
    kwargs.setdefault("machine_type", MachineType.BARISTA_TS)
    return FakeClient(profile, caps, **kwargs)


def make_entry(entry_id="a1b2c3d4e5f6", domain=DOMAIN, runtime_data=None):
    """Fake config entry resolvable through _resolve_entry."""
    return SimpleNamespace(
        entry_id=entry_id, domain=domain, runtime_data=runtime_data
    )


def make_hass(entry=None):
    """MagicMock hass whose config_entries knows exactly one entry."""
    hass = MagicMock()
    hass.data = {}

    def _get_entry(entry_id):
        if entry is not None and entry.entry_id == entry_id:
            return entry
        return None

    hass.config_entries.async_get_entry = MagicMock(side_effect=_get_entry)
    return hass


def make_connection(is_admin=True):
    """MagicMock WS connection with a controllable admin flag."""
    connection = MagicMock()
    connection.send_result = MagicMock()
    connection.send_error = MagicMock()
    connection.user.is_admin = is_admin
    return connection


def make_msg(entry_id="a1b2c3d4e5f6", msg_id=7):
    return {"id": msg_id, "type": UI_CONTRACT_TYPE, "entry_id": entry_id}


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def test_ui_contract_command_registered():
    """async_register_panel_websocket registers ui_contract/get."""
    hass = make_hass()
    panel_api.async_register_panel_websocket(hass)
    assert UI_CONTRACT_TYPE in hass.data["websocket_api"]


def test_ui_contract_schema_requires_entry_id():
    """The registered schema requires entry_id and accepts a valid message."""
    hass = make_hass()
    panel_api.async_register_panel_websocket(hass)
    _handler, schema = hass.data["websocket_api"][UI_CONTRACT_TYPE]
    schema(make_msg())  # valid message passes
    with pytest.raises(vol.Invalid):
        schema({"id": 7, "type": UI_CONTRACT_TYPE})  # entry_id missing


def test_ui_contract_not_admin_gated():
    """A non-admin caller reaches the handler (read-only endpoint, §2.2)."""
    entry = make_entry(runtime_data=make_melitta_client())
    hass = make_hass(entry)
    panel_api.async_register_panel_websocket(hass)
    handler, _schema = hass.data["websocket_api"][UI_CONTRACT_TYPE]
    connection = make_connection(is_admin=False)
    handler(hass, connection, make_msg())  # must not raise Unauthorized
    connection.send_error.assert_not_called()
    connection.send_result.assert_called_once()


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_ui_contract_happy_path():
    """A ready Melitta client yields the full §3.3 versioned document."""
    entry = make_entry(runtime_data=make_melitta_client())
    hass = make_hass(entry)
    connection = make_connection()

    panel_api._ws_ui_contract(hass, connection, make_msg())

    connection.send_error.assert_not_called()
    connection.send_result.assert_called_once()
    msg_id, payload = connection.send_result.call_args.args
    assert msg_id == 7
    # Transport envelope + contract versioning (§2.2/§5.1).
    assert payload["schema_version"] == 1
    assert payload["contract_version"] == CONTRACT_VERSION
    assert isinstance(payload["contract_fingerprint"], str)
    assert payload["entry_id"] == "a1b2c3d4e5f6"
    assert payload["source"] == "live"
    # Document body (§3.3).
    assert payload["machine"]["brand"] == "melitta"
    assert payload["machine"]["machine_type"] == "BARISTA_TS"
    assert payload["capabilities"]["supports_freestyle"] is True
    assert payload["vocabularies"]["freestyle"]["blend"] == [
        "hopper_1", "hopper_2",
    ]
    assert payload["limits"]["portion_ml"]["c1"] == {
        "min": 5, "max": 250, "step": 5,
    }
    assert payload["recipes"], "recipe catalog must not be empty"
    assert payload["status_attribute_entity"] == "state"
    assert payload["bridge_attribute_entity"] == "connection"


# ---------------------------------------------------------------------------
# Error codes (§2.2)
# ---------------------------------------------------------------------------


def test_ui_contract_entry_not_found_for_unknown_id():
    """No such entry → entry_not_found."""
    hass = make_hass()  # knows no entries at all
    connection = make_connection()
    panel_api._ws_ui_contract(hass, connection, make_msg("missing"))
    connection.send_result.assert_not_called()
    msg_id, code, _message = connection.send_error.call_args.args
    assert msg_id == 7
    assert code == "entry_not_found"


def test_ui_contract_entry_not_found_for_foreign_domain():
    """An entry of another integration → entry_not_found."""
    entry = make_entry(domain="other_domain", runtime_data=object())
    hass = make_hass(entry)
    connection = make_connection()
    panel_api._ws_ui_contract(hass, connection, make_msg())
    connection.send_result.assert_not_called()
    assert connection.send_error.call_args.args[1] == "entry_not_found"


def test_ui_contract_client_not_ready():
    """Entry exists but runtime_data has no client → client_not_ready."""
    entry = make_entry(runtime_data=None)
    hass = make_hass(entry)
    connection = make_connection()
    panel_api._ws_ui_contract(hass, connection, make_msg())
    connection.send_result.assert_not_called()
    assert connection.send_error.call_args.args[1] == "client_not_ready"


def test_ui_contract_contract_not_ready_when_capabilities_none():
    """Client exists but capabilities is None → contract_not_ready (§2.2)."""
    client = FakeClient(MelittaProfile(), capabilities=None)
    entry = make_entry(runtime_data=client)
    hass = make_hass(entry)
    connection = make_connection()
    panel_api._ws_ui_contract(hass, connection, make_msg())
    connection.send_result.assert_not_called()
    assert connection.send_error.call_args.args[1] == "contract_not_ready"
