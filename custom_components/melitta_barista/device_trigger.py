"""Device triggers for the coffee machine's lifecycle events.

Exposes the six `lifecycle.EVENT_TYPES` as "when" triggers in the Home Assistant
automation editor, so an automation can react to a finished brew or a raised
prompt without the author knowing which entity carries them.

Why the bus, not the entity state
---------------------------------
Every trigger here is routed through the `melitta_barista_event` bus event that
`event.py` fires alongside each entity event, exactly as core's own
`nanoleaf` device triggers are. An attribute-scoped state trigger on the event
entity would look simpler and would be wrong twice over:

* the state trigger helper returns early when the watched attribute's old and
  new value are equal, so two consecutive `brew_finished` — the normal
  coffee-machine case — would fire only once;
* the event entity restores its last event across a restart or a config-entry
  reload, and a naive state trigger would announce yesterday's cappuccino as it
  is re-added.

Kept deliberately cheap
-----------------------
Home Assistant imports this module on every automation-editor page load, so it
pulls in nothing beyond `.const` and `.lifecycle` (both dependency-free) — no
BLE stack, no client.

All six types are offered for every device, never narrowed by capability: the
machine's capabilities are `None` until the first handshake, so a gated list
would be empty right after a restart and the editor would show nothing.
"""

from __future__ import annotations

import voluptuous as vol
from homeassistant.components.device_automation import (
    DEVICE_TRIGGER_BASE_SCHEMA,
    DeviceNotFound,
)
from homeassistant.components.homeassistant.triggers import event as event_trigger
from homeassistant.const import (
    CONF_DEVICE_ID,
    CONF_DOMAIN,
    CONF_EVENT,
    CONF_PLATFORM,
    CONF_TYPE,
)
from homeassistant.core import CALLBACK_TYPE, HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.trigger import TriggerActionType, TriggerInfo
from homeassistant.helpers.typing import ConfigType

from .const import DOMAIN
from .lifecycle import EVENT_TYPES, MELITTA_LIFECYCLE_EVENT

TRIGGER_TYPES = EVENT_TYPES
"""The trigger vocabulary — `lifecycle.EVENT_TYPES` verbatim, never a copy.

This module is the third consumer of that list (after `event.py` and the
translations) and the one the automation editor reads: a divergence here would
silently offer the user a stale set of triggers.
"""

TRIGGER_SCHEMA = DEVICE_TRIGGER_BASE_SCHEMA.extend(
    {
        vol.Required(CONF_DOMAIN): DOMAIN,
        vol.Required(CONF_DEVICE_ID): str,
        vol.Required(CONF_TYPE): vol.In(TRIGGER_TYPES),
    }
)


async def async_get_triggers(
    hass: HomeAssistant, device_id: str
) -> list[dict[str, str]]:
    """List the device triggers a coffee machine offers.

    Returns all six lifecycle types for any device belonging to this
    integration; see the module docstring for why the list is not narrowed by
    the machine's capabilities.
    """
    device_registry = dr.async_get(hass)
    if device_registry.async_get(device_id) is None:
        raise DeviceNotFound(f"Device ID {device_id} is not valid")
    return [
        {
            CONF_PLATFORM: "device",
            CONF_DOMAIN: DOMAIN,
            CONF_DEVICE_ID: device_id,
            CONF_TYPE: trigger_type,
        }
        for trigger_type in TRIGGER_TYPES
    ]


async def async_attach_trigger(
    hass: HomeAssistant,
    config: ConfigType,
    action: TriggerActionType,
    trigger_info: TriggerInfo,
) -> CALLBACK_TYPE:
    """Attach a device trigger to the lifecycle bus event.

    Delegates to the core event trigger, matching on the event's `type` and
    `device_id`. That match is a subset match, so the rest of the lifecycle
    payload (`source`, `duration_s`, `description`, …) rides along untouched and
    is available to the action as `trigger.event.data`.
    """
    event_config = event_trigger.TRIGGER_SCHEMA(
        {
            event_trigger.CONF_PLATFORM: CONF_EVENT,
            event_trigger.CONF_EVENT_TYPE: MELITTA_LIFECYCLE_EVENT,
            event_trigger.CONF_EVENT_DATA: {
                CONF_TYPE: config[CONF_TYPE],
                CONF_DEVICE_ID: config[CONF_DEVICE_ID],
            },
        }
    )
    return await event_trigger.async_attach_trigger(
        hass, event_config, action, trigger_info, platform_type="device"
    )
