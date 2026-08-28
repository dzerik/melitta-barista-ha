"""Step-machine wizard backend: per-phase brew WS command + status fields.

Covers two additions:
1. `melitta_barista/sommelier/brew_phase` — brews exactly ONE machine
   phase of a recipe or favorite (component1 = the requested phase,
   component2 = synthesized "none"), returns phase bookkeeping and the
   next phase's user_action_before list, honors brew_freestyle's bool,
   and only marks brewed / increments the favorite counter on the LAST
   phase's successful start.
2. `melitta_barista/status` payload gains wizard-consumed fields inside
   the nested `status` dict: `is_brewing` (process == PRODUCT) and
   `awaiting_confirmation` (manipulation in PROMPT_MANIPULATIONS),
   while keeping the existing keys backward-compatible.
"""

from __future__ import annotations

import inspect
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.melitta_barista import panel_api, sommelier_api
from custom_components.melitta_barista.coffee_platform.domain import (
    MachineProcess,
    MachineStatus,
    Manipulation,
)
from custom_components.melitta_barista.const import PROCESS_MAP


def _handler():
    """Resolve the (unwrapped) ws_brew_phase handler lazily for TDD."""
    return inspect.unwrap(sommelier_api.ws_brew_phase)


def _client(brew_result: bool = True) -> MagicMock:
    """Machine client mock with no capability gate and a stubbed brew."""
    client = MagicMock()
    client.capabilities = None  # skip the supports_recipe_writes gate
    client.brew_freestyle = AsyncMock(return_value=brew_result)
    return client


def _phase(actions: list | None = None, **component) -> dict:
    return {"component": component, "user_action_before": actions or []}


_MILK_ACTIONS = [{"order": 1, "action": "Fill the glass with ice", "phase": "during"}]
_COFFEE_ACTIONS = [{"order": 2, "action": "Swirl gently", "phase": "during"}]


def _two_phase_row(row_id: str = "r1", blend: int = 1) -> dict:
    return {
        "id": row_id,
        "name": "Layered Latte",
        "description": "d",
        "blend": blend,
        "component1": {},
        "component2": {},
        "machine_phases": [
            _phase(
                _MILK_ACTIONS,
                process="milk", intensity="medium", temperature="normal",
                shots="none", portion_ml=120,
            ),
            _phase(
                _COFFEE_ACTIONS,
                process="coffee", intensity="strong", temperature="normal",
                shots="one", portion_ml=40,
            ),
        ],
    }


def _db_with_recipe(row) -> MagicMock:
    db = MagicMock()
    db.async_get_recipe = AsyncMock(return_value=row)
    db.async_get_favorite = AsyncMock(return_value=None)
    db.async_mark_recipe_brewed = AsyncMock()
    db.async_increment_favorite_brew = AsyncMock()
    return db


def _db_with_favorite(row) -> MagicMock:
    db = MagicMock()
    db.async_get_recipe = AsyncMock(return_value=None)
    db.async_get_favorite = AsyncMock(return_value=row)
    db.async_mark_recipe_brewed = AsyncMock()
    db.async_increment_favorite_brew = AsyncMock()
    return db


async def _call(db, client, msg) -> MagicMock:
    """Run ws_brew_phase with patched DB/client; returns the connection mock."""
    hass = MagicMock()
    connection = MagicMock()
    with patch.object(sommelier_api, "_async_get_db", AsyncMock(return_value=db)), \
         patch.object(sommelier_api, "_find_client", return_value=client):
        await _handler()(hass, connection, msg)
    return connection


# ── phase selection ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_brew_phase_0_brews_only_first_phase_component():
    client = _client()
    db = _db_with_recipe(_two_phase_row())
    await _call(db, client, {"id": 1, "recipe_id": "r1", "phase_index": 0})

    client.brew_freestyle.assert_awaited_once()
    kwargs = client.brew_freestyle.call_args.kwargs
    assert kwargs["component1"].process == PROCESS_MAP["milk"]
    assert kwargs["component1"].portion == 24  # 120 ml / 5
    # No second pour in a single-phase call.
    assert kwargs["component2"].process == PROCESS_MAP["none"]
    assert kwargs["component2"].portion == 0


@pytest.mark.asyncio
async def test_brew_phase_1_brews_only_second_phase_component():
    client = _client()
    db = _db_with_recipe(_two_phase_row())
    await _call(db, client, {"id": 2, "recipe_id": "r1", "phase_index": 1})

    kwargs = client.brew_freestyle.call_args.kwargs
    assert kwargs["component1"].process == PROCESS_MAP["coffee"]
    assert kwargs["component1"].portion == 8  # 40 ml / 5
    assert kwargs["component2"].process == PROCESS_MAP["none"]


@pytest.mark.asyncio
async def test_brew_phase_translates_llm_blend_0_to_ble_byte_2():
    client = _client()
    db = _db_with_recipe(_two_phase_row(blend=0))
    await _call(db, client, {"id": 3, "recipe_id": "r1", "phase_index": 0})

    kwargs = client.brew_freestyle.call_args.kwargs
    assert kwargs["component1"].blend == 2  # Blend.BLEND_2, never raw 0


# ── success payload ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_brew_phase_success_payload_exposes_next_manual_actions():
    client = _client()
    db = _db_with_recipe(_two_phase_row())
    connection = await _call(db, client, {"id": 4, "recipe_id": "r1", "phase_index": 0})

    connection.send_error.assert_not_called()
    msg_id, result = connection.send_result.call_args.args
    assert msg_id == 4
    assert result["success"] is True
    assert result["phase_index"] == 0
    assert result["phase_count"] == 2
    assert result["manual_actions_next"] == _COFFEE_ACTIONS
    assert "schema_version" in result


@pytest.mark.asyncio
async def test_brew_phase_last_phase_has_no_next_manual_actions():
    client = _client()
    db = _db_with_recipe(_two_phase_row())
    connection = await _call(db, client, {"id": 5, "recipe_id": "r1", "phase_index": 1})

    result = connection.send_result.call_args.args[1]
    assert result["phase_index"] == 1
    assert result["manual_actions_next"] == []


# ── brewed marker only on the last phase ─────────────────────────────


@pytest.mark.asyncio
async def test_brew_phase_intermediate_success_does_not_mark_recipe_brewed():
    client = _client()
    db = _db_with_recipe(_two_phase_row())
    await _call(db, client, {"id": 6, "recipe_id": "r1", "phase_index": 0})
    db.async_mark_recipe_brewed.assert_not_awaited()


@pytest.mark.asyncio
async def test_brew_phase_last_phase_success_marks_recipe_brewed():
    client = _client()
    db = _db_with_recipe(_two_phase_row())
    await _call(db, client, {"id": 7, "recipe_id": "r1", "phase_index": 1})
    db.async_mark_recipe_brewed.assert_awaited_once_with("r1")


@pytest.mark.asyncio
async def test_brew_phase_favorite_last_phase_increments_counter():
    client = _client()
    db = _db_with_favorite(_two_phase_row(row_id="f1"))
    connection = await _call(db, client, {"id": 8, "favorite_id": "f1", "phase_index": 1})

    connection.send_error.assert_not_called()
    db.async_increment_favorite_brew.assert_awaited_once_with("f1")
    db.async_mark_recipe_brewed.assert_not_awaited()


@pytest.mark.asyncio
async def test_brew_phase_favorite_intermediate_phase_does_not_increment():
    client = _client()
    db = _db_with_favorite(_two_phase_row(row_id="f1"))
    await _call(db, client, {"id": 9, "favorite_id": "f1", "phase_index": 0})
    db.async_increment_favorite_brew.assert_not_awaited()


# ── honest bool handling ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_brew_phase_refusal_sends_brew_failed_and_skips_marking():
    client = _client(brew_result=False)
    db = _db_with_recipe(_two_phase_row())
    connection = await _call(db, client, {"id": 10, "recipe_id": "r1", "phase_index": 1})

    args = connection.send_error.call_args.args
    assert args[0] == 10
    assert args[1] == "brew_failed"
    assert args[2]  # human-readable message present
    db.async_mark_recipe_brewed.assert_not_awaited()
    connection.send_result.assert_not_called()


@pytest.mark.asyncio
async def test_brew_phase_validation_error_message_reaches_client():
    client = _client()
    row = _two_phase_row()
    row["machine_phases"][0]["component"]["process"] = "espresso"
    db = _db_with_recipe(row)
    connection = await _call(db, client, {"id": 11, "recipe_id": "r1", "phase_index": 0})

    args = connection.send_error.call_args.args
    assert args[1] == "brew_failed"
    assert "espresso" in args[2]
    client.brew_freestyle.assert_not_awaited()


@pytest.mark.asyncio
async def test_brew_phase_recipe_writes_unsupported_is_distinct_error():
    client = _client()
    caps = MagicMock()
    caps.supports_recipe_writes = False
    caps.family_key = "nivona_7xx"
    client.capabilities = caps
    db = _db_with_recipe(_two_phase_row())
    connection = await _call(db, client, {"id": 12, "recipe_id": "r1", "phase_index": 0})

    args = connection.send_error.call_args.args
    assert args[1] == "recipe_writes_unsupported"
    client.brew_freestyle.assert_not_awaited()


# ── validation of the request itself ─────────────────────────────────


@pytest.mark.asyncio
async def test_brew_phase_out_of_range_index_is_invalid_phase():
    client = _client()
    db = _db_with_recipe(_two_phase_row())
    connection = await _call(db, client, {"id": 13, "recipe_id": "r1", "phase_index": 2})

    args = connection.send_error.call_args.args
    assert args[1] == "invalid_phase"
    client.brew_freestyle.assert_not_awaited()
    connection.send_result.assert_not_called()


@pytest.mark.asyncio
async def test_brew_phase_requires_exactly_one_target_neither():
    client = _client()
    db = _db_with_recipe(_two_phase_row())
    connection = await _call(db, client, {"id": 14, "phase_index": 0})

    assert connection.send_error.call_args.args[1] == "invalid_target"
    client.brew_freestyle.assert_not_awaited()


@pytest.mark.asyncio
async def test_brew_phase_requires_exactly_one_target_both():
    client = _client()
    db = _db_with_recipe(_two_phase_row())
    connection = await _call(
        db, client, {"id": 15, "recipe_id": "r1", "favorite_id": "f1", "phase_index": 0}
    )

    assert connection.send_error.call_args.args[1] == "invalid_target"
    client.brew_freestyle.assert_not_awaited()


@pytest.mark.asyncio
async def test_brew_phase_recipe_not_found():
    client = _client()
    db = _db_with_recipe(None)
    connection = await _call(db, client, {"id": 16, "recipe_id": "nope", "phase_index": 0})
    assert connection.send_error.call_args.args[1] == "not_found"


@pytest.mark.asyncio
async def test_brew_phase_no_device():
    db = _db_with_recipe(_two_phase_row())
    connection = await _call(db, None, {"id": 17, "recipe_id": "r1", "phase_index": 0})
    assert connection.send_error.call_args.args[1] == "no_device"


def test_brew_phase_command_is_registered():
    """async_register_websocket_handlers must register ws_brew_phase."""
    hass = MagicMock()
    registered = []
    with patch.object(
        sommelier_api.websocket_api,
        "async_register_command",
        side_effect=lambda _hass, handler: registered.append(handler),
    ):
        sommelier_api.async_register_websocket_handlers(hass)
    assert sommelier_api.ws_brew_phase in registered


# ── status payload: wizard completion-detection fields ───────────────


def _status_client(status) -> MagicMock:
    """Client mock exposing exactly the attrs _build_status_payload reads."""
    client = MagicMock()
    client.status = status
    client.capabilities = None
    client.machine_type = None
    client.address = "F1:22:33:44:55:66"
    client.connected = True
    client.firmware_version = "1.0"
    client.features = None
    client.dis_info = None
    client._last_handshake_at = None
    client.active_profile = 1
    client.selected_recipe = None
    client.total_cups = 10
    client.cup_counters = {}
    return client


def test_status_payload_is_brewing_true_while_product():
    status = MachineStatus(
        process=MachineProcess.PRODUCT, manipulation=Manipulation.NONE, progress=42
    )
    payload = panel_api._build_status_payload(_status_client(status))
    assert payload["status"]["is_brewing"] is True
    assert payload["status"]["progress"] == 42


def test_status_payload_is_brewing_false_when_ready():
    status = MachineStatus(process=MachineProcess.READY, manipulation=Manipulation.NONE)
    payload = panel_api._build_status_payload(_status_client(status))
    assert payload["status"]["is_brewing"] is False


def test_status_payload_awaiting_confirmation_true_for_prompt_manipulation():
    status = MachineStatus(
        process=MachineProcess.PRODUCT, manipulation=Manipulation.FILL_WATER
    )
    payload = panel_api._build_status_payload(_status_client(status))
    assert payload["status"]["awaiting_confirmation"] is True
    assert payload["status"]["manipulation"] == "FILL_WATER"


def test_status_payload_awaiting_confirmation_false_without_prompt():
    status = MachineStatus(process=MachineProcess.READY, manipulation=Manipulation.NONE)
    payload = panel_api._build_status_payload(_status_client(status))
    assert payload["status"]["awaiting_confirmation"] is False


def test_status_payload_keeps_backward_compatible_keys():
    status = MachineStatus(process=MachineProcess.READY, manipulation=Manipulation.NONE)
    payload = panel_api._build_status_payload(_status_client(status))
    for key in ("process", "sub_process", "manipulation", "info_messages", "progress"):
        assert key in payload["status"]


def test_status_payload_none_status_stays_none():
    payload = panel_api._build_status_payload(_status_client(None))
    assert payload["status"] is None
