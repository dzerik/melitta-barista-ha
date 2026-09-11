"""Tests for the `event` platform — the machine-lifecycle entity.

Two shapes of test live here, deliberately:

* full-setup tests (`_setup_integration`) prove the entity exists on the real
  device, that the bus event carries a real `device_id`, and that setup itself
  emits nothing;
* standalone-entity tests drive `_on_status` directly with
  `async_write_ha_state` stubbed, because ordering and one-shot-warning
  assertions need to see individual calls rather than their side effects.
"""

from __future__ import annotations

import json
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.components.event import EventEntity, EventExtraStoredData
from homeassistant.const import Platform
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers import device_registry as dr, entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.melitta_barista import PLATFORMS, narration, panel_api
from custom_components.melitta_barista.ble_client import MelittaBleClient
from custom_components.melitta_barista.brands import MelittaProfile
from custom_components.melitta_barista.coffee_platform.domain import InfoMessage
from custom_components.melitta_barista.const import (
    DOMAIN,
    MachineProcess,
    Manipulation,
    RecipeId,
)
from custom_components.melitta_barista.diagnostics import (
    async_get_config_entry_diagnostics,
)
from custom_components.melitta_barista.event import (
    MelittaMachineEvent,
    lifecycle_detector_key,
)
from custom_components.melitta_barista.lifecycle import (
    EVENT_TYPES,
    MELITTA_LIFECYCLE_EVENT,
    BrewIntent,
    now_monotonic,
)
from custom_components.melitta_barista.protocol import MachineRecipe, MachineStatus

from . import MOCK_ADDRESS, MOCK_CONFIG_DATA


def _mock_client(status=None):
    """A MelittaBleClient double, with the real brand/capabilities objects.

    `brand`, `capabilities` and `recipe_cache_generation` are real values, not
    MagicMock placeholders — the capability-driven entity factories compare
    against them during setup (documented in `tests/test_sensor.py`).
    """
    client = MagicMock()
    client.address = MOCK_ADDRESS
    client.connected = True
    client.status = status or MachineStatus(process=MachineProcess.READY)
    client.firmware_version = "1.0.0"
    client.machine_type = None
    client.model_name = "Melitta Barista"
    client.selected_recipe = None
    client.auto_confirm_prompts = False
    client.brew_intent = None
    client.take_brew_intent = MagicMock(return_value=None)
    # Explicit: an auto-created MagicMock is truthy, and a truthy
    # `take_ha_cancel()` would turn every brew in this file into an
    # HA-cancelled one.
    client.take_ha_cancel = MagicMock(return_value=False)
    client.clear_brew_intent = MagicMock()
    client.set_ble_device = MagicMock()
    client.add_status_callback = MagicMock()
    client.remove_status_callback = MagicMock()
    client.add_connection_callback = MagicMock()
    client.remove_connection_callback = MagicMock()
    client.connect = AsyncMock(return_value=True)
    client.disconnect = AsyncMock()
    client.start_polling = MagicMock()
    client.read_setting = AsyncMock(return_value=None)
    client.write_setting = AsyncMock(return_value=True)
    client.read_recipe = AsyncMock(return_value=None)
    client.write_recipe = AsyncMock(return_value=True)
    client.my_coffee_slots = None
    client.profile_names = {0: "My Coffee"}
    client.directkey_recipes = {}
    client.brand = MelittaProfile()
    client.capabilities = client.brand.capabilities_for("barista_ts")
    client.recipe_cache_generation = 0
    return client


def _real_client() -> MelittaBleClient:
    """A real, connected client — the stage and the cancel path are the point.

    The mock double cannot show the bug this guards: the staged record is
    popped by the detector at the PRODUCT edge, so only a client that really
    stages, pops and records can prove that a later cancel is still
    attributed to HA.
    """
    client = MelittaBleClient(MOCK_ADDRESS)
    client._connected = True
    client._client = MagicMock(is_connected=True)
    client._status = MachineStatus(process=MachineProcess.READY)
    client.start_polling = MagicMock()
    client._stop_polling = MagicMock()
    client._protocol.read_recipe = AsyncMock(
        return_value=MachineRecipe(
            recipe_id=int(RecipeId.ESPRESSO), recipe_type=0,
            component1=None, component2=None,
        ),
    )
    client._protocol.write_recipe = AsyncMock(return_value=True)
    client._protocol.write_alphanumeric = AsyncMock(return_value=True)
    client._protocol.start_process = AsyncMock(return_value=True)
    client._protocol.cancel_process = AsyncMock(return_value=True)
    return client


@pytest.fixture
def mock_entry() -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        data=MOCK_CONFIG_DATA,
        unique_id="aabbccddeeff",
    )


async def _setup_integration(hass, mock_entry, client):
    mock_entry.add_to_hass(hass)
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
        assert await hass.config_entries.async_setup(mock_entry.entry_id)
        await hass.async_block_till_done()


def _entity_of(client) -> MelittaMachineEvent:
    """The event entity, selected by identity rather than callback index.

    Existing platform tests pick `add_status_callback.call_args_list[0]`; this
    platform registers last, and copying a literal index into a new test is
    exactly how those tests would start testing each other.
    """
    for call in client.add_status_callback.call_args_list:
        owner = getattr(call.args[0], "__self__", None)
        if isinstance(owner, MelittaMachineEvent):
            return owner
    raise AssertionError("the event entity registered no status callback")


def _status_callback(client):
    """The event entity's own registered status callback."""
    return getattr(_entity_of(client), "_on_status")


def _connection_callbacks(client):
    """Every connection callback belonging to the event entity."""
    return [
        call.args[0]
        for call in client.add_connection_callback.call_args_list
        if isinstance(getattr(call.args[0], "__self__", None), MelittaMachineEvent)
    ]


def _standalone(hass, client, entry) -> MelittaMachineEvent:
    """An entity wired to `hass` but never added to a platform.

    `async_write_ha_state` is stubbed because there is no platform behind it;
    tests that care about the write assert on the stub.
    """
    entity = MelittaMachineEvent(client, entry, "Coffee Machine")
    entity.hass = hass
    entity.entity_id = "event.coffee_machine_machine_event"
    entity.async_write_ha_state = MagicMock()
    return entity


def _bus_recorder(hass: HomeAssistant) -> list[Event]:
    """Collect lifecycle bus events synchronously, in order.

    The listener MUST be a `@callback`: a plain function is dispatched to the
    executor, which both reorders the events and hides them from an assertion
    made before the next `async_block_till_done()`.
    """
    fired: list[Event] = []

    @callback
    def _remember(event: Event) -> None:
        fired.append(event)

    hass.bus.async_listen(MELITTA_LIFECYCLE_EVENT, _remember)
    return fired


def _ready() -> MachineStatus:
    return MachineStatus(process=MachineProcess.READY)


def _brewing() -> MachineStatus:
    return MachineStatus(process=MachineProcess.PRODUCT)


def _drive_brew(callback) -> None:
    """READY → PRODUCT → READY: one complete brew, as the wire delivers it."""
    callback(_ready())
    callback(_brewing())
    callback(_ready())


async def _seed_strings(
    hass: HomeAssistant, locale: str = "en", *, ui_locale: str | None = None,
) -> None:
    """Warm the narration + ui_strings caches the renderer reads.

    `ui_locale` defaults to `locale`. Pass it separately to reproduce the state
    the `de-DE` → `de` re-lookup in `_add_description` exists for, where
    ui_strings is only cached under the *resolved* tag and a lookup by
    `hass.config.language` misses.
    """
    resolved, locale_map, en_map = await hass.async_add_executor_job(
        narration.load_narration_strings, locale,
    )
    await panel_api.async_preload_ui_strings(hass, "en")
    await panel_api.async_preload_ui_strings(hass, ui_locale or locale)
    hass.data.setdefault(DOMAIN, {}).update({
        "narration_locale": resolved,
        "narration_strings": locale_map,
        "narration_strings_en": en_map,
    })


def _clear_strings(hass: HomeAssistant) -> None:
    """Make every string cache cold again, as it is before the setup preload."""
    domain_data = hass.data.setdefault(DOMAIN, {})
    for key in (
        "narration_locale", "narration_strings", "narration_strings_en",
        "ui_strings_cache", "ui_strings_resolution",
    ):
        domain_data.pop(key, None)


# ---------------------------------------------------------------------------
# Registration and identity
# ---------------------------------------------------------------------------


def test_event_platform_included_in_platforms() -> None:
    """Platform.EVENT must be in PLATFORMS, and last (callback-index safety)."""
    assert Platform.EVENT in PLATFORMS
    assert PLATFORMS[-1] is Platform.EVENT


async def test_single_event_entity_created(
    hass: HomeAssistant, mock_entry: MockConfigEntry,
) -> None:
    """Exactly one event entity, with the house-convention unique_id."""
    client = _mock_client()
    await _setup_integration(hass, mock_entry, client)

    states = hass.states.async_all("event")
    assert len(states) == 1

    entity = _entity_of(client)
    assert entity.unique_id == f"{MOCK_ADDRESS}_machine_event"
    assert entity.event_types == EVENT_TYPES
    assert entity.translation_key == "machine_event"
    assert entity.device_class is None

    registry_entry = er.async_get(hass).async_get(states[0].entity_id)
    assert registry_entry is not None
    assert registry_entry.unique_id == f"{MOCK_ADDRESS}_machine_event"


async def test_entity_stays_available_while_disconnected(
    hass: HomeAssistant, mock_entry: MockConfigEntry,
) -> None:
    """No `available` override: the last event must stay readable when off."""
    client = _mock_client()
    await _setup_integration(hass, mock_entry, client)

    entity = _entity_of(client)
    client.connected = False
    assert entity.available is True


async def test_adding_the_entity_emits_nothing(
    hass: HomeAssistant, mock_entry: MockConfigEntry,
) -> None:
    """Setup must not fabricate an event out of the current machine state."""
    client = _mock_client(status=_brewing())
    await _setup_integration(hass, mock_entry, client)

    state = hass.states.async_all("event")[0]
    assert state.state in ("unknown", "unavailable")
    assert state.attributes.get("event_type") is None


async def test_callbacks_registered_and_removed(
    hass: HomeAssistant, mock_entry: MockConfigEntry,
) -> None:
    """Both subscriptions are added on add and dropped on unload."""
    client = _mock_client()
    await _setup_integration(hass, mock_entry, client)

    entity = _entity_of(client)
    assert entity._on_status in [
        call.args[0] for call in client.add_status_callback.call_args_list
    ]
    assert _connection_callbacks(client) == [entity._on_connection_change]
    assert hass.data[DOMAIN][lifecycle_detector_key(mock_entry.entry_id)] is (
        entity._detector
    )

    assert await hass.config_entries.async_unload(mock_entry.entry_id)
    await hass.async_block_till_done()

    assert entity._on_status in [
        call.args[0] for call in client.remove_status_callback.call_args_list
    ]
    assert entity._on_connection_change in [
        call.args[0] for call in client.remove_connection_callback.call_args_list
    ]
    assert lifecycle_detector_key(mock_entry.entry_id) not in hass.data.get(DOMAIN, {})


# ---------------------------------------------------------------------------
# Emission
# ---------------------------------------------------------------------------


async def test_brew_cycle_triggers_event_then_writes_state(
    hass: HomeAssistant, mock_entry: MockConfigEntry,
) -> None:
    """`_trigger_event` does not write state, so the write must follow it."""
    client = _mock_client()
    mock_entry.add_to_hass(hass)
    entity = _standalone(hass, client, mock_entry)

    calls: list[tuple] = []
    entity._trigger_event = MagicMock(
        side_effect=lambda event_type, attrs=None: calls.append(("trigger", event_type)),
    )
    entity.async_write_ha_state = MagicMock(
        side_effect=lambda: calls.append(("write", None)),
    )

    _drive_brew(entity._on_status)

    assert calls == [
        ("trigger", "brew_started"),
        ("write", None),
        ("trigger", "brew_finished"),
        ("write", None),
    ]


async def test_brew_finished_fires_the_bus_event_with_device_id(
    hass: HomeAssistant, mock_entry: MockConfigEntry,
) -> None:
    """A device-registered entity addresses its bus event to that device."""
    client = _mock_client()
    await _setup_integration(hass, mock_entry, client)

    fired = _bus_recorder(hass)

    _drive_brew(_status_callback(client))
    await hass.async_block_till_done()

    assert [event.data["type"] for event in fired] == [
        "brew_started", "brew_finished",
    ]
    device = dr.async_get(hass).async_get_device(identifiers={(DOMAIN, MOCK_ADDRESS)})
    assert device is not None
    assert fired[0].data["device_id"] == device.id
    assert fired[0].data["entity_id"] == hass.states.async_all("event")[0].entity_id
    assert fired[1].data["source"] == "machine"
    assert isinstance(fired[1].data["duration_s"], int)


async def test_state_is_written_before_the_bus_event_is_fired(
    hass: HomeAssistant, mock_entry: MockConfigEntry,
) -> None:
    """A bus-woken automation must read an entity state that already agrees."""
    client = _mock_client()
    await _setup_integration(hass, mock_entry, client)

    entity = _entity_of(client)
    order: list[str] = []

    @callback
    def _remember(event: Event) -> None:
        order.append("fire")

    hass.bus.async_listen(MELITTA_LIFECYCLE_EVENT, _remember)
    with patch.object(
        entity, "async_write_ha_state", side_effect=lambda: order.append("write"),
    ):
        _drive_brew(entity._on_status)

    assert order == ["write", "fire", "write", "fire"]


async def test_missing_device_entry_warns_once_and_fires_no_bus_event(
    hass: HomeAssistant,
    mock_entry: MockConfigEntry,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Without a device the triggers are dead — say so exactly once."""
    client = _mock_client()
    mock_entry.add_to_hass(hass)
    entity = _standalone(hass, client, mock_entry)

    fired = _bus_recorder(hass)
    with caplog.at_level(logging.WARNING, logger="melitta_barista"):
        await entity.async_added_to_hass()
        _drive_brew(entity._on_status)
        _drive_brew(entity._on_status)

    assert fired == []
    warnings = [
        record for record in caplog.records
        if record.levelno == logging.WARNING
        and "device triggers will not fire" in record.getMessage()
    ]
    assert len(warnings) == 1
    assert mock_entry.entry_id in warnings[0].getMessage()


async def test_disconnect_resets_the_detector(
    hass: HomeAssistant, mock_entry: MockConfigEntry,
) -> None:
    """A brew that finishes while HA is away must not surface on reconnect."""
    client = _mock_client()
    mock_entry.add_to_hass(hass)
    entity = _standalone(hass, client, mock_entry)
    entity._trigger_event = MagicMock()

    entity._on_status(_ready())
    entity._on_status(_brewing())
    assert entity._detector.state_snapshot()["brewing"] is True

    entity._on_connection_change(False)
    assert entity._detector.state_snapshot()["brewing"] is False
    assert entity._detector.state_snapshot()["prev_process"] is None

    entity._trigger_event.reset_mock()
    entity._on_status(_ready())
    assert entity._trigger_event.call_count == 0


async def test_ha_cancel_after_the_product_edge_is_attributed_to_ha(
    hass: HomeAssistant, mock_entry: MockConfigEntry,
) -> None:
    """The whole cancel path, in wire order, with a real client.

    HA brews, the machine reports PRODUCT (which pops the staged record), the
    user presses Cancel in HA, and the machine reports READY with its cancel
    bit set — exactly as it would for a cancel at the front panel. The event
    must still say HA did it; blaming the user for HA's own cancel is what
    `cancel_source` exists to prevent.
    """
    client = _real_client()
    mock_entry.add_to_hass(hass)
    entity = _standalone(hass, client, mock_entry)

    fired: list[tuple[str, dict]] = []
    entity._trigger_event = MagicMock(
        side_effect=lambda event_type, attrs=None: fired.append((event_type, attrs or {})),
    )

    assert await client.brew_recipe(RecipeId.ESPRESSO) is True
    entity._on_status(_ready())
    entity._on_status(_brewing())
    # The PRODUCT frame consumed the record — this is why an annotation on the
    # stage can never reach the terminal event.
    assert client.brew_intent is None

    assert await client.cancel_brewing() is True
    entity._on_status(MachineStatus(
        process=MachineProcess.READY,
        info_messages=InfoMessage.PREPARATION_CANCELLED,
    ))

    assert [event_type for event_type, _ in fired] == ["brew_started", "brew_cancelled"]
    started, cancelled = fired[0][1], fired[1][1]
    assert started["source"] == "ha"
    assert cancelled["cancel_source"] == "ha"
    assert cancelled["recipe_name"] == "Espresso"


async def test_a_finished_brew_is_not_reported_as_an_ha_cancel(
    hass: HomeAssistant, mock_entry: MockConfigEntry,
) -> None:
    """The flag is only read once: no cancel, no `brew_cancelled`."""
    client = _real_client()
    mock_entry.add_to_hass(hass)
    entity = _standalone(hass, client, mock_entry)

    fired: list[tuple[str, dict]] = []
    entity._trigger_event = MagicMock(
        side_effect=lambda event_type, attrs=None: fired.append((event_type, attrs or {})),
    )

    assert await client.brew_recipe(RecipeId.ESPRESSO) is True
    _drive_brew(entity._on_status)

    assert [event_type for event_type, _ in fired] == ["brew_started", "brew_finished"]
    assert "cancel_source" not in fired[1][1]


async def test_disconnect_clears_the_staged_record(
    hass: HomeAssistant, mock_entry: MockConfigEntry,
) -> None:
    """A record whose PRODUCT frame never arrived dies with the link.

    Otherwise the next brew — a Latte pressed on the machine's front panel —
    inherits the Espresso HA asked for and is narrated as one.
    """
    client = _real_client()
    mock_entry.add_to_hass(hass)
    entity = _standalone(hass, client, mock_entry)
    entity._trigger_event = MagicMock()

    assert await client.brew_recipe(RecipeId.ESPRESSO) is True
    assert client.brew_intent is not None  # no PRODUCT frame ever arrived

    entity._on_connection_change(False)

    assert client.brew_intent is None
    entity._on_status(_ready())
    entity._on_status(_brewing())
    started = entity._trigger_event.call_args_list[0]
    assert started.args[1]["source"] == "machine"
    assert "recipe_name" not in started.args[1]


async def test_unknown_process_frame_does_not_raise(
    hass: HomeAssistant, mock_entry: MockConfigEntry,
) -> None:
    """Nivona reports unmapped codes as `process=None` on every other frame."""
    client = _mock_client()
    mock_entry.add_to_hass(hass)
    entity = _standalone(hass, client, mock_entry)
    entity._trigger_event = MagicMock()

    entity._on_status(MachineStatus(process=None, manipulation=Manipulation.NONE))
    entity._on_status(_ready())

    assert entity._trigger_event.call_count == 0


# ---------------------------------------------------------------------------
# Narration
# ---------------------------------------------------------------------------


async def test_description_present_when_the_string_cache_is_warm(
    hass: HomeAssistant, mock_entry: MockConfigEntry,
) -> None:
    """The rendered sentence rides in the payload, with its key and language."""
    client = _mock_client()
    mock_entry.add_to_hass(hass)
    await _seed_strings(hass)
    entity = _standalone(hass, client, mock_entry)

    _drive_brew(entity._on_status)

    attributes = entity.state_attributes
    assert attributes["event_type"] == "brew_finished"
    assert attributes["description"] == "Your drink is ready."
    assert attributes["description_key"] == "narration.event.brew_finished.unnamed"
    assert attributes["description_language"] == "en"


async def test_cold_cache_fires_without_description_and_reads_no_file(
    hass: HomeAssistant, mock_entry: MockConfigEntry,
) -> None:
    """Narration is preloaded or absent — never loaded from a BLE callback."""
    client = _mock_client()
    mock_entry.add_to_hass(hass)
    _clear_strings(hass)
    entity = _standalone(hass, client, mock_entry)

    with (
        patch.object(narration, "_read_narration_file") as read_narration,
        patch.object(panel_api, "_load_ui_strings") as load_ui,
    ):
        _drive_brew(entity._on_status)

    assert read_narration.call_count == 0
    assert load_ui.call_count == 0
    attributes = entity.state_attributes
    assert attributes["event_type"] == "brew_finished"
    assert "description" not in attributes
    assert "description_key" not in attributes


async def test_a_raising_narrator_does_not_swallow_the_event(
    hass: HomeAssistant, mock_entry: MockConfigEntry,
) -> None:
    """The sentence is a garnish; losing it must not lose the event."""
    client = _mock_client()
    mock_entry.add_to_hass(hass)
    await _seed_strings(hass)
    entity = _standalone(hass, client, mock_entry)

    fired = _bus_recorder(hass)
    with patch.object(narration, "render", side_effect=RuntimeError("boom")):
        _drive_brew(entity._on_status)

    attributes = entity.state_attributes
    assert attributes["event_type"] == "brew_finished"
    assert "description" not in attributes
    # No device is registered for a standalone entity, so the bus stays quiet;
    # what matters is that the entity-side event survived the exception.
    assert fired == []
    assert entity.async_write_ha_state.call_count == 2


async def test_a_russian_household_hears_a_russian_sentence(
    hass: HomeAssistant, mock_entry: MockConfigEntry,
) -> None:
    """The locale wiring end to end: HA language → preload → payload.

    The renderer's own locale coverage is pinned by the `narration` unit tests;
    what this one pins is the seam between them — that `_add_description` reads
    `narration_locale`/`narration_strings` rather than the English maps sitting
    next to them. Hard-wiring English there passes every other test in the wave
    while a Russian install is spoken to in English by its voice assistant.
    """
    hass.config.language = "ru"
    client = _mock_client()
    client.brew_intent = BrewIntent(
        noted_at=now_monotonic(),
        recipe_source="base",
        recipe_key="cappuccino",
        recipe_name="Cappuccino",
    )
    await _setup_integration(hass, mock_entry, client)

    _drive_brew(_status_callback(client))
    await hass.async_block_till_done()

    attributes = _entity_of(client).state_attributes
    assert attributes["event_type"] == "brew_finished"
    assert attributes["description"] == "Сварено: капучино."
    assert attributes["description_key"] == "narration.event.brew_finished.named"
    assert attributes["description_language"] == "ru"


async def test_a_regional_language_tag_still_finds_its_ui_strings(
    hass: HomeAssistant, mock_entry: MockConfigEntry,
) -> None:
    """`de-DE` must re-look-up ui_strings under the resolved `de`.

    `hot_water` ships no `narration.drink.*` override in any locale, so its
    spoken name can only come from `recipes.name.hot_water` in the *locale's*
    ui_strings map. Miss that map and the sentence quietly degrades to the
    free-text English name inside a German sentence — the code-switching the
    renderer's overlay guard exists to prevent, sneaking in through the seam.
    """
    hass.config.language = "de-DE"
    client = _mock_client()
    client.brew_intent = BrewIntent(
        noted_at=now_monotonic(),
        recipe_source="base",
        recipe_key="hot_water",
        recipe_name="Hot Water",
    )
    mock_entry.add_to_hass(hass)
    await _seed_strings(hass, "de")
    assert panel_api.get_cached_ui_strings(hass, "de-DE") is None
    entity = _standalone(hass, client, mock_entry)

    _drive_brew(entity._on_status)

    attributes = entity.state_attributes
    assert attributes["description"] == "Heißes Wasser ist fertig."
    assert attributes["description_language"] == "de"


# ---------------------------------------------------------------------------
# Payload shape, restore and the recorder split
# ---------------------------------------------------------------------------


async def test_full_payload_survives_json_dumps(
    hass: HomeAssistant, mock_entry: MockConfigEntry,
) -> None:
    """Every value that reaches `state_attributes` must be JSON-serialisable."""
    client = _mock_client()
    mock_entry.add_to_hass(hass)
    await _seed_strings(hass)
    intent = BrewIntent(
        noted_at=now_monotonic(),
        recipe_source="base",
        recipe_key="cappuccino",
        recipe_name="Cappuccino",
        profile=2,
        profile_name="Anna",
        two_cups=True,
        components=[
            {"process": "coffee", "intensity": "strong", "portion_ml": 100},
            {"process": "milk", "portion_ml": 150},
        ],
        extra={"phase_index": 0, "phase_total": 2},
    )
    client.brew_intent = intent
    entity = _standalone(hass, client, mock_entry)

    _drive_brew(entity._on_status)

    assert client.take_brew_intent.call_count == 1
    attributes = entity.state_attributes
    assert json.loads(json.dumps(attributes)) == attributes
    assert attributes["event_type"] == "brew_finished"
    assert attributes["source"] == "ha"
    assert attributes["recipe_key"] == "cappuccino"
    assert attributes["total_ml"] == 250
    assert attributes["final"] is False
    assert attributes["description"].startswith("Ready: Cappuccino")


async def test_recorder_split_keeps_description_and_drops_the_bulk(
    hass: HomeAssistant, mock_entry: MockConfigEntry,
) -> None:
    """`components` must never reach the database; `description` must."""
    unrecorded = MelittaMachineEvent._unrecorded_attributes
    assert "components" in unrecorded
    assert "total_ml" in unrecorded
    assert "phase_index" in unrecorded
    assert "restored" in unrecorded
    assert "description" not in unrecorded
    for recorded in (
        "source", "recipe_source", "recipe_name", "two_cups", "duration_s",
        "final", "cancel_source", "prompt", "process",
    ):
        assert recorded not in unrecorded


async def test_restored_event_is_stamped(
    hass: HomeAssistant, mock_entry: MockConfigEntry,
) -> None:
    """A restored "brew finished" must be recognisable as history, not news."""
    client = _mock_client()
    mock_entry.add_to_hass(hass)
    entity = _standalone(hass, client, mock_entry)

    stored = EventExtraStoredData("brew_finished", {"source": "ha"})
    with patch.object(
        EventEntity, "async_get_last_event_data", AsyncMock(return_value=stored),
    ):
        restored = await entity.async_get_last_event_data()

    assert restored is not None
    assert restored.last_event_type == "brew_finished"
    assert restored.last_event_attributes == {"source": "ha", "restored": True}
    # The stored object itself is left alone.
    assert stored.last_event_attributes == {"source": "ha"}


async def test_restore_returns_none_when_nothing_was_stored(
    hass: HomeAssistant, mock_entry: MockConfigEntry,
) -> None:
    """Never fabricate: any truthy return would restore a phantom event."""
    client = _mock_client()
    mock_entry.add_to_hass(hass)
    entity = _standalone(hass, client, mock_entry)

    with patch.object(
        EventEntity, "async_get_last_event_data", AsyncMock(return_value=None),
    ):
        assert await entity.async_get_last_event_data() is None


async def test_restore_stamps_even_a_typeless_stored_event(
    hass: HomeAssistant, mock_entry: MockConfigEntry,
) -> None:
    """`last_event_type=None` is still a stored event — and still truthy."""
    client = _mock_client()
    mock_entry.add_to_hass(hass)
    entity = _standalone(hass, client, mock_entry)

    with patch.object(
        EventEntity, "async_get_last_event_data",
        AsyncMock(return_value=EventExtraStoredData(None, None)),
    ):
        restored = await entity.async_get_last_event_data()

    assert restored is not None
    assert restored.last_event_attributes == {"restored": True}


# ---------------------------------------------------------------------------
# Diagnostics (M11) — the only lens the maintainer has on a bug report
# ---------------------------------------------------------------------------


async def test_diagnostics_narration_block_is_all_null_on_a_cold_install(
    hass: HomeAssistant, mock_entry: MockConfigEntry,
) -> None:
    """A diagnostics download must work when narration is exactly what broke."""
    client = _mock_client()
    mock_entry.add_to_hass(hass)
    mock_entry.runtime_data = client
    _clear_strings(hass)

    result = await async_get_config_entry_diagnostics(hass, mock_entry)

    block = result["narration"]
    assert block["locale"] is None
    assert block["narration_keys"] is None
    assert block["ui_strings_resolution"] == {}
    assert block["ui_strings_cached_locales"] == []
    # A MagicMock client exposes a MagicMock `brew_intent` and no detector is
    # stashed — neither may leak a non-serialisable object into the download.
    assert block["brew_intent"] is None
    assert block["detector"] is None
    assert json.loads(json.dumps(block)) == block


async def test_diagnostics_reports_the_staged_intent_without_free_text(
    hass: HomeAssistant, mock_entry: MockConfigEntry,
) -> None:
    """Names are reported as booleans: they are user-authored free text."""
    client = _mock_client()
    await _setup_integration(hass, mock_entry, client)
    await _seed_strings(hass)
    client.brew_intent = BrewIntent(
        noted_at=now_monotonic(),
        recipe_source="directkey",
        recipe_key="cappuccino",
        recipe_name="Cappuccino",
        profile=2,
        profile_name="Anna",
        two_cups=True,
        components=[{"process": "coffee", "portion_ml": 100}],
    )

    result = await async_get_config_entry_diagnostics(hass, mock_entry)
    block = result["narration"]

    assert block["locale"] == "en"
    # 45 mandatory sentence keys (41 + the four shape sentences) + the 22
    # optional spoken drink names (M18).
    assert block["narration_keys"] == 67
    assert block["ui_strings_resolution"]["en"] == "en"
    assert "en" in block["ui_strings_cached_locales"]
    assert block["brew_intent"]["recipe_source"] == "directkey"
    assert block["brew_intent"]["component_count"] == 1
    assert block["brew_intent"]["has_recipe_name"] is True
    assert block["brew_intent"]["has_profile_name"] is True
    assert "Anna" not in json.dumps(block)
    assert "Cappuccino" not in json.dumps(block)
    # The detector is stashed by the live entity, so its latches are visible.
    assert block["detector"]["brewing"] is False
    assert json.loads(json.dumps(result["narration"])) == result["narration"]
