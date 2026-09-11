"""Event platform — the machine's lifecycle as discrete Home Assistant events.

One `event` entity per config entry. It subscribes to the BLE client's status
stream, hands each frame to a pure `lifecycle.LifecycleDetector`, and for every
event the detector reports it does three things, in this order:

1. `_trigger_event(type, payload)` — records the event on the entity;
2. `async_write_ha_state()` — `_trigger_event` is `@final` and deliberately does
   **not** write state, so without this the entity would never change;
3. `hass.bus.async_fire(MELITTA_LIFECYCLE_EVENT, …)` — the bus route
   `device_trigger.py` listens on.

State first, bus second, so an automation woken by the bus event reads an entity
state that already reflects it.

Why a bus event as well as the entity
-------------------------------------
An `event` entity's state is a timestamp, and two identical brews in a row
produce two different timestamps but the same attributes. A device trigger built
on attribute state would therefore fire unreliably; the bus event fires once per
occurrence, always.

What this entity deliberately does NOT do
-----------------------------------------
* **No `available` override.** The entity stays available while the machine is
  off. An unavailable entity drops its attributes, which would destroy the
  "what just happened" read exactly when a user goes looking for it — the same
  reasoning `MelittaConnectionSensor` records for itself.
* **No `device_class`.** `EventDeviceClass` offers only DOORBELL / BUTTON /
  MOTION; none of them describes a coffee machine.
* **No narration of its own.** The sentence comes from `narration.render`, from
  string maps preloaded at setup. A cold cache costs the `description` key and
  nothing else: the event still fires.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Final

from homeassistant.components.event import EventEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_NAME
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import narration, panel_api
from .coffee_platform.contract import CoffeeMachineClient
from .coffee_platform.domain import MachineStatus
from .const import DOMAIN
from .entity import MelittaDeviceMixin
from .lifecycle import (
    EVENT_TYPES,
    MELITTA_LIFECYCLE_EVENT,
    BrewIntent,
    LifecycleDetector,
    LifecycleEvent,
    now_monotonic,
)

if TYPE_CHECKING:
    from homeassistant.components.event import EventExtraStoredData

PARALLEL_UPDATES = 0  # BLE: single connection, serialize via locks

_LOGGER = logging.getLogger("melitta_barista")


_BRANDS_WITHOUT_CANCEL_DETECTION: Final[frozenset[str]] = frozenset({"nivona"})
"""Brands whose firmware cannot report a machine-side cancel at all.

Nivona hard-zeroes `info_messages`, so `InfoMessage.PREPARATION_CANCELLED`
never reaches the detector there and a cancelled drink is indistinguishable
from a finished one. Terminal brew events carry that fact as
`cancel_detection`, so a consumer can tell "not cancelled" from "cannot tell"
instead of quietly believing every Nivona brew succeeded. Listed as a
deny-set rather than an allow-set so a brand added later is assumed capable
until its firmware proves otherwise.
"""


def lifecycle_detector_key(entry_id: str) -> str:
    """`hass.data[DOMAIN]` key under which an entry's detector is stashed.

    The stash exists purely so `diagnostics.py` can answer "why did no event
    fire" from a bug report without a live debugger. Defined here, next to the
    only writer, and imported by the reader so the two cannot drift.
    """
    return f"lifecycle_detector_{entry_id}"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the single machine-event entity for the configured machine."""
    client: CoffeeMachineClient = entry.runtime_data
    name = entry.data.get(CONF_NAME) or f"{client.brand.brand_name} Coffee Machine"
    async_add_entities([MelittaMachineEvent(client, entry, name)])


class MelittaMachineEvent(MelittaDeviceMixin, EventEntity):
    """Brew, prompt and maintenance events for one coffee machine.

    Deliberately has NO ``available`` override: an unavailable entity drops its
    attributes, and "the last thing the machine did" must stay readable while
    the machine is off (same reasoning as ``MelittaConnectionSensor``).
    """

    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_icon = "mdi:coffee-maker"
    _attr_translation_key = "machine_event"
    _attr_event_types = EVENT_TYPES

    # Recorder split. `EventEntity.state_attributes` is @final and merges EVERY
    # key of the payload, so without this list a `components` list of dicts
    # would be written to home-assistant_v2.db on every single brew.
    #
    # `description` is deliberately RECORDED — the human-readable logbook line
    # is the entire point of narration, and the "recorder strips
    # _unrecorded_attributes" lesson must not be misapplied to it. Recorded
    # alongside it: `event_type` (core), `source`, `recipe_source`,
    # `recipe_name`, `two_cups`, `duration_s`, `final`, `cancel_source`,
    # `prompt`, `process`, `shape`. `shape` is a short token and the ONLY
    # identity a front-panel brew ever has, so history keeps it. Everything
    # else — bulky, structured or machine-facing — is listed below.
    _unrecorded_attributes = frozenset({
        "components",
        "total_ml",
        "description_key",
        "description_language",
        "recipe_key",
        "profile",
        "profile_name",
        "slot",
        "phase_index",
        "phase_total",
        "restored",
        "soft",
        "auto_confirm",
        "during_brew",
        "cancel_detection",
    })

    def __init__(
        self,
        client: CoffeeMachineClient,
        entry: ConfigEntry,
        machine_name: str,
    ) -> None:
        self._client = client
        self._entry = entry
        self._machine_name = machine_name
        self._detector = LifecycleDetector()
        self._device_id: str | None = None
        self._missing_device_logged = False

    @property
    def unique_id(self) -> str:
        return f"{self._client.address}_machine_event"

    # -- lifecycle -----------------------------------------------------------

    async def async_added_to_hass(self) -> None:
        """Resolve the device id, stash the detector and subscribe to the client.

        `super()` is chained as good practice, not as a restore mechanism:
        restore runs in `async_internal_added_to_hass`, which `EventEntity`
        overrides as `@final` and which the platform awaits *before* this
        method.
        """
        await super().async_added_to_hass()
        self._resolve_device_id(warn=True)
        self.hass.data.setdefault(DOMAIN, {})[
            lifecycle_detector_key(self._entry.entry_id)
        ] = self._detector
        self._client.add_status_callback(self._on_status)
        self._client.add_connection_callback(self._on_connection_change)

    async def async_will_remove_from_hass(self) -> None:
        """Unsubscribe and drop the diagnostics stash."""
        self._client.remove_status_callback(self._on_status)
        self._client.remove_connection_callback(self._on_connection_change)
        domain_data = self.hass.data.get(DOMAIN)
        if isinstance(domain_data, dict):
            domain_data.pop(lifecycle_detector_key(self._entry.entry_id), None)

    async def async_get_last_event_data(self) -> EventExtraStoredData | None:
        """Restore the last event, stamped so nobody re-announces it.

        Returns `None` **iff** `super()` returned `None` — never a fabricated
        event. That matters because `async_internal_added_to_hass` treats any
        truthy return as "restore this", and a stored-data object whose
        `last_event_type` is `None` is still truthy; inventing one would give
        the entity a phantom event at every restart.

        The `restored: True` stamp is what keeps a stale "brew finished" from
        being re-announced or re-spoken: an automation that pipes `description`
        into `tts.speak` filters on it, and the attribute is unrecorded so it
        costs nothing in the database. The class is reconstructed with
        `type(data)` because it is not exported from the `event` component.
        """
        data = await super().async_get_last_event_data()
        if data is None:
            return None
        return type(data)(
            data.last_event_type,
            {**(data.last_event_attributes or {}), "restored": True},
        )

    # -- client callbacks ----------------------------------------------------

    @callback
    def _on_status(self, status: MachineStatus) -> None:
        """Feed one status frame to the detector and emit whatever it produced."""
        try:
            events = self._detector.feed(
                status,
                intent=self._staged_intent(),
                now=now_monotonic(),
                auto_confirm_enabled=bool(
                    getattr(self._client, "auto_confirm_prompts", False),
                ),
                cancel_detection=self._cancel_detection(),
                ha_cancelled=self._take_ha_cancel(),
            )
        except Exception:  # noqa: BLE001 - a detector bug must not kill the BLE callback
            _LOGGER.exception("Lifecycle detection failed for a status frame")
            return

        if self._detector.consumed_intent():
            take = getattr(self._client, "take_brew_intent", None)
            if callable(take):
                take()

        for event in events:
            self._emit(event)

    @callback
    def _on_connection_change(self, connected: bool) -> None:
        """Drop every latch on disconnect (R2).

        The client never clears its last `MachineStatus`, so without this a
        machine switched off mid-brew and back on hours later would present
        PRODUCT→READY and narrate a drink nobody made. The accepted cost,
        documented in the README: a brew that completes while HA is
        disconnected produces no event.
        """
        if not connected:
            self._detector.reset()
            clear = getattr(self._client, "clear_brew_intent", None)
            if callable(clear):
                clear()

    def _staged_intent(self) -> BrewIntent | None:
        """The client's staged `BrewIntent`, or None.

        Type-checked rather than trusted: a client from an older release (or a
        test double) exposes something that is not a `BrewIntent`, and handing
        that to the detector would put junk in a payload that is supposed to be
        the honest record of what HA asked for.
        """
        intent = getattr(self._client, "brew_intent", None)
        return intent if isinstance(intent, BrewIntent) else None

    def _take_ha_cancel(self) -> bool:
        """Pop the client's pending HA-cancel flag, or False when it has none.

        Read once per status frame and handed straight to the detector: the
        Cancel button is only pressable after the PRODUCT frame that already
        popped the staged `BrewIntent`, so this flag — not an annotation on
        that record — is how `cancel_source: "ha"` reaches a terminal event.
        `getattr` rather than a direct call, for the same reason as
        `take_brew_intent`: a test double need not implement it.
        """
        take = getattr(self._client, "take_ha_cancel", None)
        if not callable(take):
            return False
        return bool(take())

    def _cancel_detection(self) -> bool:
        """Whether this brand's firmware can report a machine-side cancel."""
        brand = getattr(self._client, "brand", None)
        slug = getattr(brand, "brand_slug", None)
        return slug not in _BRANDS_WITHOUT_CANCEL_DETECTION

    # -- emission ------------------------------------------------------------

    @callback
    def _emit(self, event: LifecycleEvent) -> None:
        """Record one event, write state, then fire the bus event — in that order."""
        payload = dict(event.payload)
        self._add_description(event.type, payload)

        self._trigger_event(event.type, payload)
        self.async_write_ha_state()

        device_id = self._resolve_device_id()
        if device_id is None:
            return
        self.hass.bus.async_fire(
            MELITTA_LIFECYCLE_EVENT,
            {
                "device_id": device_id,
                "entity_id": self.entity_id,
                "type": event.type,
                **payload,
            },
        )

    def _add_description(self, event_type: str, payload: dict[str, Any]) -> None:
        """Add the narrated sentence to `payload`, or leave it exactly as it was.

        Every string map is a pure dict lookup against caches warmed at setup —
        this runs inside a BLE status callback and may not block. A cold cache,
        a missing asset or a bug in the renderer costs the sentence and nothing
        else; the event still fires. The sentence itself is never logged (it can
        contain a user-authored profile or recipe name).
        """
        try:
            domain_data = self.hass.data.get(DOMAIN) or {}
            language = self.hass.config.language or "en"
            locale = domain_data.get("narration_locale") or "en"
            ui_strings = panel_api.get_cached_ui_strings(self.hass, language)
            if ui_strings is None:
                # The narration loader may have resolved to a different tag
                # than the one ui_strings was preloaded under (`de-DE` -> `de`).
                ui_strings = panel_api.get_cached_ui_strings(self.hass, locale)
            result = narration.render(
                payload,
                event_type=event_type,
                locale=locale,
                narration=domain_data.get("narration_strings"),
                narration_en=domain_data.get("narration_strings_en"),
                ui_strings=ui_strings,
                ui_strings_en=panel_api.get_cached_ui_strings(self.hass, "en"),
            )
        except Exception:  # noqa: BLE001 - narration must never swallow an event
            _LOGGER.exception("Narration failed; the event fires without a description")
            return

        if not result.text:
            return
        payload["description"] = result.text
        if result.key:
            payload["description_key"] = result.key
        if result.language:
            payload["description_language"] = result.language

    # -- device id -----------------------------------------------------------

    def _resolve_device_id(self, warn: bool = False) -> str | None:
        """The device id this entity's bus events are addressed to, or None.

        Two lookups, because either can be the one that works: the registry
        entry (populated before `async_added_to_hass` runs) and, failing that,
        the device registry keyed by the machine's BLE address.

        Retried on every emission while it is still unresolved — a device can
        be registered after the entity was added — but the warning is one-shot
        (`warn=True` only from `async_added_to_hass`). Without that warning a
        missing device entry would leave every device trigger silently dead in
        the field while the test suite stayed green; without the one-shot guard
        it would spam a per-frame status callback.
        """
        if self._device_id is not None:
            return self._device_id

        device_id = getattr(self.registry_entry, "device_id", None)
        if device_id is None:
            device = dr.async_get(self.hass).async_get_device(
                identifiers={(DOMAIN, self._client.address)},
            )
            device_id = device.id if device else None

        if device_id is None:
            if warn and not self._missing_device_logged:
                self._missing_device_logged = True
                # entry_id, never the machine name: it is an opaque token, and
                # the machine name is user-authored free text.
                _LOGGER.warning(
                    "No device registry entry for config entry %s — "
                    "device triggers will not fire",
                    self._entry.entry_id,
                )
            return None

        self._device_id = device_id
        return device_id
