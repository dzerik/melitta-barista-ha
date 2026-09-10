"""Tests for the coffee machine's device triggers.

Covers the trigger listing, the schema (including the pin that keeps its type
vocabulary identical to `lifecycle.EVENT_TYPES`) and the actual bus routing —
in particular that two identical events in a row both fire, which is the
behaviour an attribute-scoped state trigger could not provide.
"""

from __future__ import annotations

from typing import Any

import pytest
import voluptuous as vol
from homeassistant.components.device_automation import DeviceNotFound
from homeassistant.const import (
    CONF_DEVICE_ID,
    CONF_DOMAIN,
    CONF_PLATFORM,
    CONF_TYPE,
)
from homeassistant.core import CALLBACK_TYPE, Context, HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.trigger import TriggerInfo
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.melitta_barista.const import DOMAIN
from custom_components.melitta_barista.device_trigger import (
    TRIGGER_SCHEMA,
    async_attach_trigger,
    async_get_triggers,
)
from custom_components.melitta_barista.lifecycle import (
    EVENT_BREW_FINISHED,
    EVENT_BREW_STARTED,
    EVENT_TYPES,
    MELITTA_LIFECYCLE_EVENT,
)

from . import MOCK_ADDRESS, MOCK_CONFIG_DATA


def _register_device(hass: HomeAssistant) -> str:
    """Add a config entry plus its device to the registry and return the device id."""
    entry = MockConfigEntry(domain=DOMAIN, data=MOCK_CONFIG_DATA, title="Coffee")
    entry.add_to_hass(hass)
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, MOCK_ADDRESS)},
        name="Coffee Machine",
    )
    return device.id


def _trigger_info() -> TriggerInfo:
    """Minimal TriggerInfo, as the automation helper would build it."""
    return {
        "domain": "automation",
        "name": "test trigger",
        "home_assistant_start": False,
        "variables": None,
        "trigger_data": {"id": "0", "idx": "0", "alias": None},
    }


async def _attach(
    hass: HomeAssistant, device_id: str, trigger_type: str, calls: list[dict[str, Any]]
) -> CALLBACK_TYPE:
    """Attach a device trigger that records every run into `calls`."""

    @callback
    def _action(run_variables: dict[str, Any], context: Context | None = None) -> None:
        calls.append(run_variables["trigger"])

    config = TRIGGER_SCHEMA(
        {
            CONF_PLATFORM: "device",
            CONF_DOMAIN: DOMAIN,
            CONF_DEVICE_ID: device_id,
            CONF_TYPE: trigger_type,
        }
    )
    return await async_attach_trigger(hass, config, _action, _trigger_info())


def _type_validator() -> Any:
    """The validator TRIGGER_SCHEMA applies to the `type` key."""
    for key, validator in TRIGGER_SCHEMA.schema.items():
        if key == CONF_TYPE:
            return validator
    raise AssertionError("TRIGGER_SCHEMA has no `type` key")


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------


def test_trigger_schema_type_set_is_exactly_event_types() -> None:
    """The editor's trigger list must never drift from `lifecycle.EVENT_TYPES`.

    `device_trigger` is the third consumer of that list and the one the
    automation editor reads; a hand-copied set here would silently offer stale
    triggers while every other test stayed green.
    """
    validator = _type_validator()
    assert isinstance(validator, vol.In)
    assert set(validator.container) == set(EVENT_TYPES)
    assert len(list(validator.container)) == len(EVENT_TYPES)


def test_trigger_schema_accepts_every_event_type() -> None:
    """Each lifecycle type validates as a device-trigger config."""
    for trigger_type in EVENT_TYPES:
        config = TRIGGER_SCHEMA(
            {
                CONF_PLATFORM: "device",
                CONF_DOMAIN: DOMAIN,
                CONF_DEVICE_ID: "abc123",
                CONF_TYPE: trigger_type,
            }
        )
        assert config[CONF_TYPE] == trigger_type


def test_trigger_schema_rejects_unknown_type() -> None:
    """An unknown trigger type is refused rather than silently accepted."""
    with pytest.raises(vol.Invalid):
        TRIGGER_SCHEMA(
            {
                CONF_PLATFORM: "device",
                CONF_DOMAIN: DOMAIN,
                CONF_DEVICE_ID: "abc123",
                CONF_TYPE: "coffee_spilled",
            }
        )


# ---------------------------------------------------------------------------
# Listing
# ---------------------------------------------------------------------------


async def test_async_get_triggers_lists_all_event_types(hass: HomeAssistant) -> None:
    """A registered machine offers exactly the six lifecycle triggers."""
    device_id = _register_device(hass)

    triggers = await async_get_triggers(hass, device_id)

    assert [trigger[CONF_TYPE] for trigger in triggers] == EVENT_TYPES
    for trigger in triggers:
        assert trigger[CONF_PLATFORM] == "device"
        assert trigger[CONF_DOMAIN] == DOMAIN
        assert trigger[CONF_DEVICE_ID] == device_id


async def test_async_get_triggers_unknown_device(hass: HomeAssistant) -> None:
    """An unknown device id raises `DeviceNotFound`, as core expects."""
    with pytest.raises(DeviceNotFound):
        await async_get_triggers(hass, "not-a-device")


# ---------------------------------------------------------------------------
# Attaching
# ---------------------------------------------------------------------------


async def test_attach_trigger_fires_on_matching_event(hass: HomeAssistant) -> None:
    """A matching bus event runs the action and hands it the whole payload."""
    device_id = _register_device(hass)
    calls: list[dict[str, Any]] = []
    unsub = await _attach(hass, device_id, EVENT_BREW_FINISHED, calls)

    hass.bus.async_fire(
        MELITTA_LIFECYCLE_EVENT,
        {
            "device_id": device_id,
            "entity_id": "event.coffee_machine_event",
            "type": EVENT_BREW_FINISHED,
            "source": "ha",
            "recipe_name": "Cappuccino",
            "duration_s": 42,
        },
    )
    await hass.async_block_till_done()
    unsub()

    assert len(calls) == 1
    data = calls[0]["event"].data
    assert data["type"] == EVENT_BREW_FINISHED
    # The event_data match is a subset match: the rest of the payload rides along.
    assert data["recipe_name"] == "Cappuccino"
    assert data["duration_s"] == 42


async def test_attach_trigger_ignores_other_device(hass: HomeAssistant) -> None:
    """An event from another machine does not run this device's trigger."""
    device_id = _register_device(hass)
    calls: list[dict[str, Any]] = []
    unsub = await _attach(hass, device_id, EVENT_BREW_FINISHED, calls)

    hass.bus.async_fire(
        MELITTA_LIFECYCLE_EVENT,
        {"device_id": "some-other-device", "type": EVENT_BREW_FINISHED},
    )
    await hass.async_block_till_done()
    unsub()

    assert calls == []


async def test_attach_trigger_ignores_other_type(hass: HomeAssistant) -> None:
    """A different lifecycle type does not run this trigger."""
    device_id = _register_device(hass)
    calls: list[dict[str, Any]] = []
    unsub = await _attach(hass, device_id, EVENT_BREW_FINISHED, calls)

    hass.bus.async_fire(
        MELITTA_LIFECYCLE_EVENT,
        {"device_id": device_id, "type": EVENT_BREW_STARTED},
    )
    await hass.async_block_till_done()
    unsub()

    assert calls == []


async def test_attach_trigger_fires_twice_for_repeated_event(
    hass: HomeAssistant,
) -> None:
    """Two identical events in a row both fire.

    This is the whole reason the trigger is routed through the bus: an
    attribute-scoped state trigger returns early when the attribute's old and
    new value are equal, so back-to-back `brew_finished` — the normal
    coffee-machine case — would be reported only once.
    """
    device_id = _register_device(hass)
    calls: list[dict[str, Any]] = []
    unsub = await _attach(hass, device_id, EVENT_BREW_FINISHED, calls)

    payload = {"device_id": device_id, "type": EVENT_BREW_FINISHED, "source": "machine"}
    hass.bus.async_fire(MELITTA_LIFECYCLE_EVENT, dict(payload))
    await hass.async_block_till_done()
    hass.bus.async_fire(MELITTA_LIFECYCLE_EVENT, dict(payload))
    await hass.async_block_till_done()
    unsub()

    assert len(calls) == 2


async def test_attach_trigger_unsubscribes(hass: HomeAssistant) -> None:
    """After unsubscribing, further events are ignored."""
    device_id = _register_device(hass)
    calls: list[dict[str, Any]] = []
    unsub = await _attach(hass, device_id, EVENT_BREW_FINISHED, calls)
    unsub()

    hass.bus.async_fire(
        MELITTA_LIFECYCLE_EVENT,
        {"device_id": device_id, "type": EVENT_BREW_FINISHED},
    )
    await hass.async_block_till_done()

    assert calls == []
