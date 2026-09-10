"""The brew-intent record: staging, annotation, and what each brew path knows.

The machine never reports back *what* it is brewing — `MachineStatus` carries a
process code and nothing else — so the only truthful source for a "cappuccino,
strong, 120 ml" payload is a record written where HA issued the brew. These
tests pin three things:

1. the client's single-producer/single-consumer stage (`brew_intent`,
   `take_brew_intent`, `annotate_brew_intent`, `_note_brew_intent`);
2. what each of the five brew paths records — including the two paths that
   genuinely know nothing (`brew_mycoffee_slot`, `brew_nivona`), which must
   degrade to an honest gap instead of inventing components;
3. that the bookkeeping can never turn a started brew into a failed one.
"""

from __future__ import annotations

import inspect
import sys
import types
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.melitta_barista import sommelier_api
from custom_components.melitta_barista.ble_client import MelittaBleClient
from custom_components.melitta_barista.const import (
    AROMA_MAP,
    DirectKeyCategory,
    INTENSITY_MAP,
    PROCESS_MAP,
    RecipeId,
    SHOTS_MAP,
    TEMPERATURE_MAP,
)
from custom_components.melitta_barista.coffee_platform.domain import (
    MachineProcess,
    MachineStatus,
)
from custom_components.melitta_barista.lifecycle import BrewIntent, now_monotonic
from custom_components.melitta_barista.protocol import MachineRecipe, RecipeComponent

_NARRATION = "custom_components.melitta_barista.narration"


# ── helpers ───────────────────────────────────────────────────────────


def _component(process: str = "coffee", portion_ml: int = 100) -> RecipeComponent:
    """A component with recognisable, non-default tokens."""
    return RecipeComponent(
        process=PROCESS_MAP[process],
        shots=SHOTS_MAP["one"],
        blend=2,  # Blend.BLEND_2 — a token that is actually emitted
        intensity=INTENSITY_MAP["strong"],
        aroma=AROMA_MAP["intense"],
        temperature=TEMPERATURE_MAP["high"],
        portion=portion_ml // 5,
    )


def _client(*, start_ok: bool = True) -> MelittaBleClient:
    """A connected, ready client whose protocol layer always succeeds."""
    client = MelittaBleClient("AA:BB:CC:DD:EE:FF")
    client._connected = True
    client._client = MagicMock(is_connected=True)
    client._status = MachineStatus(process=MachineProcess.READY)
    client.start_polling = MagicMock()
    client._stop_polling = MagicMock()
    recipe = MachineRecipe(
        recipe_id=200,
        recipe_type=0,
        component1=_component("coffee", 100),
        component2=_component("milk", 60),
    )
    client._protocol.read_recipe = AsyncMock(return_value=recipe)
    client._protocol.write_recipe = AsyncMock(return_value=True)
    client._protocol.write_alphanumeric = AsyncMock(return_value=True)
    client._protocol.write_numerical = AsyncMock(return_value=True)
    client._protocol.start_process = AsyncMock(return_value=start_ok)
    client._protocol.start_process_nivona = AsyncMock(return_value=start_ok)
    client._protocol.cancel_process = AsyncMock(return_value=True)
    return client


def _stub_narration(monkeypatch, table: dict[str, str] | None = None) -> None:
    """Install a stand-in `narration` module exporting DIRECTKEY_NAME_KEYS."""
    module = types.ModuleType(_NARRATION)
    module.DIRECTKEY_NAME_KEYS = table if table is not None else {
        "espresso": "espresso", "cafe_creme": "cafe_creme",
        "cappuccino": "cappuccino", "latte_macchiato": "latte_macchiato",
        "milk": "warm_milk", "milk_froth": "milk_froth", "water": "hot_water",
    }
    monkeypatch.setitem(sys.modules, _NARRATION, module)


# ── the stage on the client ───────────────────────────────────────────


def test_stage_is_empty_until_something_is_noted():
    client = _client()
    assert client.brew_intent is None
    assert client.take_brew_intent() is None


def test_peeking_does_not_consume_but_taking_does():
    """The detector peeks on every frame and pops only at the PRODUCT edge."""
    client = _client()
    intent = BrewIntent(noted_at=now_monotonic(), recipe_source="base")
    client._note_brew_intent(intent)

    assert client.brew_intent is intent
    assert client.brew_intent is intent  # a peek never consumes
    assert client.take_brew_intent() is intent
    assert client.take_brew_intent() is None  # a brew is attributed once


def test_noting_a_second_intent_replaces_an_orphaned_first():
    client = _client()
    client._note_brew_intent(BrewIntent(noted_at=1.0, recipe_source="base"))
    client._note_brew_intent(BrewIntent(noted_at=2.0, recipe_source="freestyle"))

    assert client.brew_intent.recipe_source == "freestyle"


def test_annotating_an_empty_stage_is_a_no_op_not_an_error():
    client = _client()
    client.annotate_brew_intent(phase_index=0, phase_total=2)
    assert client.brew_intent is None


def test_annotation_merges_known_fields_and_parks_the_rest_in_extra():
    client = _client()
    client._note_brew_intent(BrewIntent(noted_at=1.0, recipe_source="freestyle"))

    client.annotate_brew_intent(
        recipe_source="sommelier", recipe_name="Layered Latte",
        phase_index=1, phase_total=2,
    )

    intent = client.brew_intent
    assert intent.recipe_source == "sommelier"
    assert intent.recipe_name == "Layered Latte"
    assert intent.extra == {"phase_index": 1, "phase_total": 2}
    assert intent.noted_at == 1.0  # annotation never restarts the TTL clock


# ── brew_recipe (base) ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_brew_recipe_records_key_name_and_tokenised_components():
    client = _client()

    assert await client.brew_recipe(RecipeId.CAPPUCCINO, two_cups=True) is True

    intent = client.brew_intent
    assert intent.recipe_source == "base"
    assert intent.recipe_key == "cappuccino"
    assert intent.recipe_name == "Cappuccino"
    assert intent.two_cups is True
    assert intent.profile is None and intent.slot is None
    first, second = intent.components
    assert first["process"] == "coffee"
    assert first["intensity"] == "strong"
    assert first["portion_ml"] == 100
    assert second["portion_ml"] == 60
    # Tokens, never wire bytes: the raw portion byte (units of 5 ml) must not
    # travel in a payload that automations read.
    assert "portion" not in first


@pytest.mark.asyncio
async def test_brew_recipe_records_nothing_when_the_machine_refuses():
    """Record on ACK only — a NACKed start must leave the stage empty."""
    client = _client(start_ok=False)

    assert await client.brew_recipe(RecipeId.ESPRESSO) is False
    assert client.brew_intent is None


@pytest.mark.asyncio
async def test_brew_recipe_of_an_unknown_id_omits_the_name_rather_than_a_number():
    client = _client()

    await client.brew_recipe(999)

    intent = client.brew_intent
    assert intent.recipe_name is None
    assert intent.recipe_key is None


# ── brew_directkey ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_brew_directkey_records_the_profile_and_the_authored_name_key(monkeypatch):
    """The only path that may name a profile — and MILK is the `warm_milk` drink."""
    _stub_narration(monkeypatch)
    client = _client()
    client.active_profile = 2
    client._profile_names[2] = "Anna"

    assert await client.brew_directkey(DirectKeyCategory.MILK) is True

    intent = client.brew_intent
    assert intent.recipe_source == "directkey"
    assert intent.recipe_key == "warm_milk"
    assert intent.recipe_name == "Milk"
    assert intent.profile == 2
    assert intent.profile_name == "Anna"
    assert intent.components  # a DirectKey recipe IS read, so it has components


def test_the_directkey_name_table_is_the_live_one_from_narration():
    """No local copy of the table: the shipped module is what brews resolve against."""
    from custom_components.melitta_barista._ble_commands import _directkey_name_key

    assert _directkey_name_key(DirectKeyCategory.MILK) == "warm_milk"
    assert _directkey_name_key(DirectKeyCategory.WATER) == "hot_water"
    assert _directkey_name_key(DirectKeyCategory.CAPPUCCINO) == "cappuccino"


@pytest.mark.asyncio
async def test_brew_directkey_omits_the_key_when_the_name_table_is_unavailable(monkeypatch):
    """A missing narration module costs the key, never the brew."""
    monkeypatch.setitem(sys.modules, _NARRATION, None)  # import raises ImportError
    client = _client()

    assert await client.brew_directkey(DirectKeyCategory.CAPPUCCINO) is True
    assert client.brew_intent.recipe_key is None
    assert client.brew_intent.recipe_name == "Cappuccino"


# ── brew_freestyle ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_brew_freestyle_records_its_components_but_no_recipe_key():
    client = _client()

    assert await client.brew_freestyle(
        "Iced oddity", 24, _component("milk", 120), _component("coffee", 40),
        two_cups=False,
    ) is True

    intent = client.brew_intent
    assert intent.recipe_source == "freestyle"
    assert intent.recipe_name == "Iced oddity"
    assert intent.recipe_key is None  # composed on the fly; no authored key
    assert intent.two_cups is False
    assert [c["portion_ml"] for c in intent.components] == [120, 40]


# ── the two paths that know nothing (honest degradation) ──────────────


@pytest.mark.asyncio
async def test_brew_mycoffee_slot_records_only_the_slot():
    """The slot's recipe lives on the machine and is never read — say so."""
    client = _client()
    client._capabilities = MagicMock(
        my_coffee_slots=4, first_mycoffee_selector=40, brew_command_mode=0x0B,
    )

    assert await client.brew_mycoffee_slot(2) is True

    intent = client.brew_intent
    assert intent.recipe_source == "mycoffee"
    assert intent.slot == 2
    assert intent.components is None
    assert intent.recipe_name is None
    assert intent.recipe_key is None


@pytest.mark.asyncio
async def test_brew_nivona_records_the_descriptor_but_never_invents_components():
    """Selector + temp registers, no RecipeComponents — no volume may be guessed."""
    from custom_components.melitta_barista.coffee_platform.domain import (
        RecipeDescriptor,
    )

    client = _client()
    client._capabilities = MagicMock(
        brew_command_mode=0x0B,
        family_key="700",
        recipes=(RecipeDescriptor(recipe_id=7, name="Cappuccino", name_key="cappuccino"),),
    )

    assert await client.brew_nivona(7, {"two_cups": 1}) is True

    intent = client.brew_intent
    assert intent.recipe_source == "nivona"
    assert intent.recipe_key == "cappuccino"
    assert intent.recipe_name == "Cappuccino"
    assert intent.two_cups is True
    assert intent.components is None  # nothing volumetric is knowable here


@pytest.mark.asyncio
async def test_brew_nivona_of_an_unknown_selector_records_only_the_source():
    client = _client()
    client._capabilities = MagicMock(
        brew_command_mode=0x0B, family_key="700", recipes=(),
    )

    assert await client.brew_nivona(99) is True

    intent = client.brew_intent
    assert intent.recipe_source == "nivona"
    assert intent.recipe_key is None
    assert intent.recipe_name is None
    assert intent.two_cups is False


@pytest.mark.asyncio
async def test_brew_nivona_omits_an_unauthored_name_key():
    """`RecipeDescriptor.name_key` is "" when no key was authored — omit it."""
    from custom_components.melitta_barista.coffee_platform.domain import (
        RecipeDescriptor,
    )

    client = _client()
    client._capabilities = MagicMock(
        brew_command_mode=0x0B, family_key="700",
        recipes=(RecipeDescriptor(recipe_id=3, name="Kaffee Crema", name_key=""),),
    )

    await client.brew_nivona(3)

    assert client.brew_intent.recipe_key is None
    assert client.brew_intent.recipe_name == "Kaffee Crema"


@pytest.mark.asyncio
async def test_brew_nivona_survives_a_brand_profile_with_no_recipe_catalogue():
    """No catalogue costs the drink's name, never the drink."""
    client = _client()
    client._capabilities = None

    assert await client.brew_nivona(7) is True

    intent = client.brew_intent
    assert intent.recipe_source == "nivona"
    assert intent.recipe_name is None


# ── the record may never break a brew ─────────────────────────────────


@pytest.mark.asyncio
async def test_a_failing_tokeniser_costs_the_record_not_the_coffee():
    client = _client()

    with patch(
        "custom_components.melitta_barista.ui_contract.component_to_tokens",
        side_effect=RuntimeError("boom"),
    ):
        assert await client.brew_recipe(RecipeId.ESPRESSO) is True

    assert client.brew_intent is None


# ── cancel_process ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_cancelling_a_brew_marks_the_staged_record_ha_cancelled():
    client = _client()
    client._note_brew_intent(BrewIntent(noted_at=now_monotonic(), recipe_source="base"))

    assert await client.cancel_brewing() is True
    assert client.brew_intent.ha_cancelled is True


@pytest.mark.asyncio
async def test_cancelling_a_maintenance_procedure_leaves_the_brew_record_alone():
    client = _client()
    client._note_brew_intent(BrewIntent(noted_at=now_monotonic(), recipe_source="base"))

    await client.cancel_process(MachineProcess.CLEANING)

    assert client.brew_intent.ha_cancelled is False


@pytest.mark.asyncio
async def test_a_refused_cancel_does_not_claim_ha_cancelled_it():
    client = _client()
    client._protocol.cancel_process = AsyncMock(return_value=False)
    client._note_brew_intent(BrewIntent(noted_at=now_monotonic(), recipe_source="base"))

    assert await client.cancel_brewing() is False
    assert client.brew_intent.ha_cancelled is False


@pytest.mark.asyncio
async def test_cancelling_with_an_empty_stage_is_harmless():
    client = _client()
    assert await client.cancel_brewing() is True
    assert client.brew_intent is None


@pytest.mark.asyncio
async def test_cancelling_after_the_detector_took_the_record_still_records_it():
    """The normal ordering — and the one an annotation cannot reach.

    Cancel only becomes pressable once the machine reports PRODUCT, and that
    is the frame at which the detector pops the record. The cancel therefore
    has to be recorded off-stage or it is lost, and the brew gets blamed on
    whoever was standing at the machine.
    """
    client = _client()
    client._note_brew_intent(BrewIntent(noted_at=now_monotonic(), recipe_source="base"))
    assert client.take_brew_intent() is not None  # the detector, at the PRODUCT edge

    assert await client.cancel_brewing() is True

    assert client.ha_cancel_pending is True
    assert client.take_ha_cancel() is True
    assert client.take_ha_cancel() is False  # one cancel, one consumer


@pytest.mark.asyncio
async def test_cancelling_a_maintenance_procedure_records_no_brew_cancel():
    client = _client()
    await client.cancel_process(MachineProcess.CLEANING)
    assert client.ha_cancel_pending is False


@pytest.mark.asyncio
async def test_a_refused_cancel_records_nothing():
    client = _client()
    client._protocol.cancel_process = AsyncMock(return_value=False)

    assert await client.cancel_brewing() is False
    assert client.ha_cancel_pending is False


@pytest.mark.asyncio
async def test_a_disconnected_client_records_nothing():
    client = _client()
    client._connected = False

    assert await client.cancel_brewing() is False
    assert client.ha_cancel_pending is False


# ── the stage does not survive a disconnect ───────────────────────────


@pytest.mark.asyncio
async def test_clearing_the_stage_drops_the_record_and_the_pending_cancel():
    """A record whose PRODUCT frame never arrived must not outlive the link.

    Without this the next brew — from the front panel, by anyone — inherits
    the last HA brew's name, components and narration until the TTL expires.
    """
    client = _client()
    client._note_brew_intent(BrewIntent(noted_at=now_monotonic(), recipe_source="base"))
    assert await client.cancel_brewing() is True

    client.clear_brew_intent()

    assert client.brew_intent is None
    assert client.ha_cancel_pending is False


# ── end to end: the record reaches the event payload ──────────────────


@pytest.mark.asyncio
async def test_the_recorded_intent_becomes_the_brew_started_payload():
    """The whole point: a payload no status frame could have produced."""
    from custom_components.melitta_barista.lifecycle import LifecycleDetector

    client = _client()
    await client.brew_recipe(RecipeId.CAPPUCCINO)

    detector = LifecycleDetector()
    detector.feed(MachineStatus(process=MachineProcess.READY))
    events = detector.feed(
        MachineStatus(process=MachineProcess.PRODUCT), intent=client.brew_intent,
    )
    if detector.consumed_intent():
        client.take_brew_intent()

    payload = events[0].payload
    assert payload["source"] == "ha"
    assert payload["recipe_key"] == "cappuccino"
    assert payload["total_ml"] == 160  # 100 + 60, never doubled
    assert client.brew_intent is None  # consumed exactly once


# ── the three mandatory sommelier annotations ─────────────────────────


def _sommelier_client() -> MagicMock:
    client = MagicMock()
    client.capabilities = None  # skip the supports_recipe_writes gate
    client.brew_freestyle = AsyncMock(return_value=True)
    return client


def _row(row_id: str = "r1", phases: int = 1) -> dict:
    component = {
        "process": "coffee", "intensity": "medium", "temperature": "normal",
        "shots": "one", "portion_ml": 60,
    }
    return {
        "id": row_id,
        "name": "Layered Latte",
        "blend": 1,
        "machine_phases": [
            {"component": component, "user_action_before": []}
            for _ in range(phases)
        ],
    }


async def _call_ws(handler, db, client, msg) -> MagicMock:
    hass = MagicMock()
    connection = MagicMock()
    with patch.object(sommelier_api, "_async_get_db", AsyncMock(return_value=db)), \
         patch.object(sommelier_api, "_find_client", return_value=client):
        await inspect.unwrap(handler)(hass, connection, msg)
    return connection


def _db(recipe=None, favorite=None) -> MagicMock:
    db = MagicMock()
    db.async_get_recipe = AsyncMock(return_value=recipe)
    db.async_get_favorite = AsyncMock(return_value=favorite)
    db.async_mark_recipe_brewed = AsyncMock()
    db.async_increment_favorite_brew = AsyncMock()
    return db


@pytest.mark.asyncio
async def test_ws_brew_annotates_the_intent_as_a_single_sommelier_phase():
    client = _sommelier_client()

    await _call_ws(sommelier_api.ws_brew, _db(recipe=_row(phases=2)), client,
                   {"id": 1, "recipe_id": "r1"})

    client.annotate_brew_intent.assert_called_once_with(
        recipe_source="sommelier", recipe_name="Layered Latte",
        phase_index=0, phase_total=1,
    )


@pytest.mark.asyncio
async def test_ws_brew_phase_annotates_the_phase_numbers_from_the_wizard():
    client = _sommelier_client()

    await _call_ws(sommelier_api.ws_brew_phase, _db(recipe=_row(phases=2)), client,
                   {"id": 2, "recipe_id": "r1", "phase_index": 0})

    client.annotate_brew_intent.assert_called_once_with(
        recipe_source="sommelier", recipe_name="Layered Latte",
        phase_index=0, phase_total=2,
    )


@pytest.mark.asyncio
async def test_ws_favorites_brew_annotates_the_intent():
    client = _sommelier_client()

    await _call_ws(sommelier_api.ws_favorites_brew, _db(favorite=_row("f1")), client,
                   {"id": 3, "favorite_id": "f1"})

    client.annotate_brew_intent.assert_called_once_with(
        recipe_source="sommelier", recipe_name="Layered Latte",
        phase_index=0, phase_total=1,
    )


@pytest.mark.asyncio
async def test_a_refused_brew_annotates_nothing():
    """A failed brew staged no record; annotating one would be a fiction."""
    client = _sommelier_client()
    client.brew_freestyle = AsyncMock(return_value=False)

    await _call_ws(sommelier_api.ws_brew, _db(recipe=_row()), client,
                   {"id": 4, "recipe_id": "r1"})

    client.annotate_brew_intent.assert_not_called()
