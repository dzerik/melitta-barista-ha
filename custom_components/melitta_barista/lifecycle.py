"""Machine lifecycle detection — pure, HA-free, BLE-free.

This module turns the stream of `MachineStatus` frames the machine emits into a
small set of discrete lifecycle events (brew started / finished / cancelled,
prompt raised / cleared, maintenance finished). It is the single source of truth
for the event-type vocabulary: `event.py` uses `EVENT_TYPES` for the entity's
`_attr_event_types` and `device_trigger.py` uses it for `vol.In(...)`. Never
duplicate that list.

Deliberately dependency-free
----------------------------
Nothing here imports `homeassistant`, `bleak` or anything that performs I/O:
`device_trigger.py` is imported by HA on every automation-editor page load and
must stay cheap, and the detector has to be testable without a `hass` fixture.
The only imports are `.const` and `.coffee_platform.domain`.

How the caller drives it
------------------------
The caller (`event.py`) owns all I/O and all state that is not a latch::

    events = detector.feed(
        status,
        intent=getattr(client, "brew_intent", None),
        now=now_monotonic(),
        auto_confirm_enabled=client.auto_confirm_prompts,
        cancel_detection=<brand supports InfoMessage.PREPARATION_CANCELLED>,
        ha_cancelled=client.take_ha_cancel(),
    )
    if detector.consumed_intent():
        client.take_brew_intent()

The detector never touches the client: it is *shown* the staged `BrewIntent` and
reports afterwards whether that intent must now be popped. `consumed_intent()`
is `True` whenever a brew-start edge took the staged intent — including when the
intent was too old and was therefore discarded, because a stale intent left on
the client would otherwise be inherited by the next front-panel brew and narrate
a lie.

Both `BrewIntent.noted_at` and `feed(now=...)` MUST come from the *same
monotonic* clock. Use `now_monotonic()` on both sides; `time.time()` would jump
under an NTP correction and turn a fresh intent stale (or a stale one fresh).

The intent is captured at the PRODUCT edge, and that captured copy — not the
client's later state — is what the terminal event is built from. A recorder that
wants an annotation to be visible (`annotate_brew_intent`) must therefore
annotate in the same synchronous block in which the brew call returned, with no
`await` in between: once a status frame has been processed the intent has been
popped, and annotating an empty stage is a no-op by design.

An HA-initiated cancel is the one fact that *cannot* travel on the intent, and
that is why `feed()` takes it as its own `ha_cancelled` argument: a cancel is
only possible after the machine has reported PRODUCT, which is precisely the
frame that empties the stage. The caller pops the client's flag and hands it in;
the detector latches it for the brew in flight and drops it when there is none.

Brand reality — what Nivona structurally cannot produce
------------------------------------------------------
Nivona's `parse_status` maps exactly two process codes (READY and PRODUCT, per
family; every other code parses to `process=None`) and hard-zeroes
`info_messages`. Consequently, on Nivona:

* `maintenance_finished` **can never fire** — no CLEANING/DESCALING/EASY_CLEAN/…
  code ever reaches the detector;
* `cancel_source: "power_off"` **can never occur** — there is no SWITCH_OFF code;
* `cancel_source: "machine"` **can never occur** — `InfoMessage` is always 0,
  which is also why terminal events carry `cancel_detection: False` there.

These are structural facts about the firmware's process table, not bugs. A
Nivona user reporting "the maintenance trigger never fires" is describing
expected behaviour.

`sub_process` is the one brew fact that does *not* depend on that process table:
Nivona's `parse_status` reads it out of the HX frame and maps it through
`SubProcess` on every family, exactly as Melitta does. The `shape` token on
`brew_finished` (see `classify_brew_shape`) therefore works on both brands —
which matters most on Nivona, where an unmapped code can leave the brew latch to
be closed by a later READY and `shape` is then the only thing describing what was
actually made.

That same two-code table is why the max-brew-age guard (R8) is mandatory: a brew
whose end is reported with an unmapped code parses to `process=None`, which is
not a transition, so without the guard the brew latch would survive for the
whole connected session and the next READY would narrate yesterday's drink with
a duration measured in days.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, fields, replace
from typing import Any, Final

from .coffee_platform.domain import (
    InfoMessage,
    MachineProcess,
    MachineStatus,
    Manipulation,
    SubProcess,
)
from .const import DOMAIN, PROMPT_MANIPULATIONS, SOFT_AUTO_CONFIRM_MANIPULATIONS

_LOGGER = logging.getLogger("melitta_barista")


# ---------------------------------------------------------------------------
# Event vocabulary
# ---------------------------------------------------------------------------

EVENT_BREW_STARTED: Final = "brew_started"
EVENT_BREW_FINISHED: Final = "brew_finished"
EVENT_BREW_CANCELLED: Final = "brew_cancelled"
EVENT_PROMPT_RAISED: Final = "prompt_raised"
EVENT_PROMPT_CLEARED: Final = "prompt_cleared"
EVENT_MAINTENANCE_FINISHED: Final = "maintenance_finished"

EVENT_TYPES: Final[list[str]] = [
    EVENT_BREW_STARTED,
    EVENT_BREW_FINISHED,
    EVENT_BREW_CANCELLED,
    EVENT_PROMPT_RAISED,
    EVENT_PROMPT_CLEARED,
    EVENT_MAINTENANCE_FINISHED,
]
"""The six lifecycle event types, in narration/documentation order.

Consumed by `event.py` (`_attr_event_types`), `device_trigger.py`
(`vol.In(EVENT_TYPES)`) and the tests — nowhere else, and never re-listed.
"""

MELITTA_LIFECYCLE_EVENT: Final = f"{DOMAIN}_event"
"""Name of the HA bus event `event.py` fires alongside each entity event."""

BREW_INTENT_TTL_S: Final = 60.0
"""How long a staged `BrewIntent` may wait for its PRODUCT frame.

Beyond this the intent is discarded and the brew is reported as
`source: "machine"`: without the TTL a front-panel brew an hour later would
inherit the last HA-initiated brew's payload and narrate a drink nobody made.
"""

BREW_MAX_DURATION_S: Final = 1800.0
"""Max age of the brew latch before it self-clears without an event (R8)."""


MAINTENANCE_PROCESSES: Final[frozenset[MachineProcess]] = frozenset(MachineProcess) - {
    MachineProcess.READY,
    MachineProcess.PRODUCT,
    MachineProcess.BUSY,
    MachineProcess.SWITCH_OFF,
}
"""Process codes that count as a maintenance procedure.

Derived from the enum by subtraction rather than hand-listed, so a process code
added to `coffee_platform.domain.MachineProcess` is classified automatically:
everything that is not idle (READY), brewing (PRODUCT), transient (BUSY) or
powering down (SWITCH_OFF) is a procedure the user is waiting on.
"""


BREW_SHAPE_COFFEE: Final = "coffee"
BREW_SHAPE_COFFEE_WITH_MILK: Final = "coffee_with_milk"
BREW_SHAPE_MILK: Final = "milk"
BREW_SHAPE_WATER: Final = "water"

BREW_SHAPE_TOKENS: Final[tuple[str, ...]] = (
    BREW_SHAPE_COFFEE,
    BREW_SHAPE_COFFEE_WITH_MILK,
    BREW_SHAPE_MILK,
    BREW_SHAPE_WATER,
)
"""The four `shape` tokens a finished brew may report, in vocabulary order.

`narration.py` derives its four `narration.event.brew_finished.shape.*` keys from
this tuple, so a fifth shape can never ship without an authored sentence.
"""

SHAPE_IGNORED_SUB_PROCESSES: Final[frozenset[SubProcess]] = frozenset({
    SubProcess.PREPARE,
})
"""Sub-processes that say nothing about what was made.

PREPARE is the rinse/warm-up leg the machine runs around a product; it appears in
front of a plain espresso just as it does in front of a latte macchiato, so
letting it classify anything would only produce a shape for brews that have
none.
"""


def classify_brew_shape(observed: Iterable[SubProcess]) -> str | None:
    """Name the SHAPE of a drink from the sub-processes seen while it brewed.

    The machine never reports *what* it is making — `MachineStatus` carries no
    recipe identity at all — so a brew started at the front panel arrives with no
    name and, before this, narrated as "your drink is ready". It does report
    which leg of the preparation it is on, and the set of legs is enough to tell
    a milk coffee from a cup of hot water.

    Deliberately coarse, and evaluated in a fixed order: steam plus a coffee leg
    is a milk coffee, steam alone is milk (froth or warm milk), any coffee leg
    alone is coffee, and water on its own is hot water. GRINDING counts as a
    coffee leg because a ground-coffee brew that is never observed mid-COFFEE
    still ground beans; PREPARE is excluded (see `SHAPE_IGNORED_SUB_PROCESSES`).
    Returns None when nothing usable was observed, and the caller then omits the
    key rather than guessing — an absent fact is an absent key (§2.4).
    """
    seen = {item for item in observed if item not in SHAPE_IGNORED_SUB_PROCESSES}
    steam = SubProcess.STEAM in seen
    coffee = SubProcess.COFFEE in seen or SubProcess.GRINDING in seen
    if steam and coffee:
        return BREW_SHAPE_COFFEE_WITH_MILK
    if steam:
        return BREW_SHAPE_MILK
    if coffee:
        return BREW_SHAPE_COFFEE
    if SubProcess.WATER in seen:
        return BREW_SHAPE_WATER
    return None


# ---------------------------------------------------------------------------
# Brew intent
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class BrewIntent:
    """What HA knew at the instant it asked the machine to brew.

    The machine reports none of this back — `MachineStatus` carries a process
    code and nothing else — so a truthful "here is what it was" payload can only
    come from a record written at the brew call site and correlated with the
    later PRODUCT frame. Frozen because it is handed around and stashed; use
    `annotated()` to derive a modified copy.

    `components` holds already-tokenised dicts (`ui_contract.component_to_tokens`),
    never raw `RecipeComponent` objects — the payload must be JSON-serialisable
    and must never expose the raw portion byte (units of 5 ml).
    """

    noted_at: float
    recipe_source: str | None = None
    recipe_key: str | None = None
    recipe_name: str | None = None
    profile: int | None = None
    profile_name: str | None = None
    two_cups: bool | None = None
    slot: int | None = None
    components: Sequence[Mapping[str, Any]] | None = None
    extra: Mapping[str, Any] = field(default_factory=dict)
    ha_cancelled: bool = False

    def annotated(self, **updates: Any) -> BrewIntent:
        """Return a copy with known fields replaced and unknown ones put in `extra`.

        Keeps the "where does `phase_index` live?" decision in one place: callers
        (`client.annotate_brew_intent`, the sommelier handlers) pass whatever they
        know by keyword and never have to know which names are real fields.
        """
        known: dict[str, Any] = {}
        unknown: dict[str, Any] = {}
        for key, value in updates.items():
            if key in _BREW_INTENT_FIELDS:
                known[key] = value
            else:
                unknown[key] = value
        if unknown:
            known["extra"] = {**dict(self.extra), **unknown}
        return replace(self, **known)

    def age_s(self, now: float | None = None) -> float:
        """Seconds since the intent was staged, on the monotonic clock."""
        return (now_monotonic() if now is None else now) - self.noted_at

    def is_fresh(self, now: float | None = None, ttl: float = BREW_INTENT_TTL_S) -> bool:
        """True while the intent may still be attributed to an incoming brew."""
        return self.age_s(now) <= ttl


_BREW_INTENT_FIELDS: Final[frozenset[str]] = frozenset(f.name for f in fields(BrewIntent))

# Extra-payload values that are safe to copy verbatim into an event payload.
_JSON_SCALARS: Final = (str, int, float, bool)


@dataclass(frozen=True, slots=True)
class LifecycleEvent:
    """One detected event: its type (a member of `EVENT_TYPES`) and its payload.

    The payload is JSON-serialisable and complete except for the narration keys
    (`description`, `description_key`, `description_language`), which `event.py`
    adds after rendering.
    """

    type: str
    payload: dict[str, Any]


def now_monotonic() -> float:
    """The clock both `BrewIntent.noted_at` and `feed(now=...)` must use."""
    return time.monotonic()


# ---------------------------------------------------------------------------
# Detector
# ---------------------------------------------------------------------------

class LifecycleDetector:
    """Turns consecutive `MachineStatus` frames into lifecycle events.

    One instance per config entry, owned by the event entity. Pure: it holds
    latches, takes snapshots and returns events, and performs no I/O of any kind
    (see the module docstring for the calling contract, the intent-consumption
    protocol and the Nivona limitations).
    """

    def __init__(self) -> None:
        self._prev_process: MachineProcess | None = None
        self._prev_manipulation: Manipulation | None = None
        self._brewing: bool = False
        self._brew_started_at: float | None = None
        self._sub_processes: set[SubProcess] = set()
        self._cancel_latched: bool = False
        self._ha_cancel_latched: bool = False
        self._intent: BrewIntent | None = None
        self._consumed_intent: bool = False
        self._prompt: Manipulation | None = None
        self._prompt_started_at: float | None = None
        self._maintenance: MachineProcess | None = None
        self._maintenance_started_at: float | None = None
        self.reset()

    # -- lifecycle -----------------------------------------------------------

    def reset(self) -> None:
        """Drop every latch — called on each disconnect (R2).

        The BLE client never clears its own last `MachineStatus` on disconnect,
        so without this a machine powered off mid-brew and back on hours later
        would present PRODUCT→READY and look like a completed drink. The
        deliberate consequence, documented in the README: a brew that completes
        while HA is disconnected produces no event.
        """
        self._prev_process = None
        self._prev_manipulation = None
        self._brewing = False
        self._brew_started_at = None
        self._sub_processes = set()
        self._cancel_latched = False
        self._ha_cancel_latched = False
        self._intent = None
        self._consumed_intent = False
        self._prompt = None
        self._prompt_started_at = None
        self._maintenance = None
        self._maintenance_started_at = None

    def consumed_intent(self) -> bool:
        """True when the last `feed()` took the staged intent — pop it now.

        True for a discarded stale intent as well as an accepted one: leaving a
        stale intent staged would let the next front-panel brew inherit it.
        """
        return self._consumed_intent

    def state_snapshot(self) -> dict[str, Any]:
        """Flat JSON-safe view of the latches, for the diagnostics endpoint.

        Answers "why did no event fire" in a bug report. Never raises and never
        contains user-authored free text (no recipe or profile names).
        """
        return {
            "prev_process": self._prev_process.name if self._prev_process is not None else None,
            "brewing": self._brewing,
            "brew_started_at": self._brew_started_at,
            "sub_processes": sorted(item.name for item in self._sub_processes),
            "cancel_latched": self._cancel_latched,
            "ha_cancel_latched": self._ha_cancel_latched,
            "has_intent": self._intent is not None,
            "prompt": self._prompt.name if self._prompt is not None else None,
            "maintenance": self._maintenance.name if self._maintenance is not None else None,
            "maintenance_started_at": self._maintenance_started_at,
        }

    # -- the one entry point -------------------------------------------------

    def feed(
        self,
        status: MachineStatus,
        *,
        intent: BrewIntent | None = None,
        now: float | None = None,
        auto_confirm_enabled: bool = False,
        cancel_detection: bool = True,
        ha_cancelled: bool = False,
    ) -> list[LifecycleEvent]:
        """Feed one status frame and return the events it produced (possibly none).

        `intent` is the currently staged `BrewIntent` (or `None`);
        `auto_confirm_enabled` is the client option, which only decorates a
        `prompt_raised` payload; `cancel_detection` states whether the brand can
        report a machine-side cancel at all (False on Nivona, whose
        `info_messages` is hard-zeroed) and is reported on terminal brew events
        so a consumer can tell "not cancelled" from "cannot tell".

        Evaluation order within a frame is fixed: the max-age guard, the cancel
        latch, the process edges, the sub-process observation, then the
        manipulation edges — so a prompt raised on the same frame that starts a
        brew reports `during_brew: True`, one raised on the frame that ends it
        does not, and the PRODUCT frame that opens a brew still contributes its
        sub-process to that brew's `shape`.
        """
        if now is None:
            now = now_monotonic()
        self._consumed_intent = False
        events: list[LifecycleEvent] = []

        self._expire_stale_brew(now)
        self._latch_cancel(status)
        self._latch_ha_cancel(ha_cancelled)
        if status.process is not None:
            self._feed_process(status.process, intent=intent, now=now,
                               cancel_detection=cancel_detection, events=events)
        self._observe_sub_process(status.sub_process)
        self._feed_manipulation(status, now=now,
                                auto_confirm_enabled=auto_confirm_enabled, events=events)
        return events

    # -- R8 ------------------------------------------------------------------

    def _expire_stale_brew(self, now: float) -> None:
        """Self-clear a brew latch older than `BREW_MAX_DURATION_S`, silently (R8).

        Evaluated on every frame, `process is None` frames included — that is the
        whole point, since an unmapped Nivona code is exactly how a brew latch
        gets orphaned. No event is emitted: an hours-old latch has no honest
        finish time, and inventing one would narrate a stale drink.
        """
        if not self._brewing or self._brew_started_at is None:
            return
        if now - self._brew_started_at <= BREW_MAX_DURATION_S:
            return
        _LOGGER.debug(
            "Brew latch older than %.0fs — clearing without an event", BREW_MAX_DURATION_S,
        )
        self._brewing = False
        self._brew_started_at = None
        self._sub_processes = set()
        self._cancel_latched = False
        self._ha_cancel_latched = False
        self._intent = None

    # -- R5 ------------------------------------------------------------------

    def _latch_cancel(self, status: MachineStatus) -> None:
        """Latch a machine-side cancel bit seen at any point during the brew (R5).

        Checking every frame — the terminal one included — makes both wire
        orderings work (the bit on the same frame as the terminal process code,
        or one frame earlier) without needing to know which the firmware does.
        """
        if not self._brewing:
            return
        if status.info_messages & InfoMessage.PREPARATION_CANCELLED:
            self._cancel_latched = True

    def _latch_ha_cancel(self, ha_cancelled: bool) -> None:
        """Latch an HA-initiated cancel the caller reports for this frame (R5).

        Dropped when no brew is latched: a cancel HA issued for a brew this
        detector never saw start (or one it has already reported terminal) has
        nothing to attribute, and holding it would let the *next* brew — quite
        possibly one made at the front panel — be blamed on HA.
        """
        if ha_cancelled and self._brewing:
            self._ha_cancel_latched = True

    # -- R0/R1/R3/R4/R7 ------------------------------------------------------

    def _feed_process(
        self,
        process: MachineProcess,
        *,
        intent: BrewIntent | None,
        now: float,
        cancel_detection: bool,
        events: list[LifecycleEvent],
    ) -> None:
        """Evaluate the process-derived edges for a frame with a known process.

        Only reached for `process is not None` (R0): an unrecognised code —
        every non-READY, non-PRODUCT code on Nivona — is not a transition and
        must leave `_prev_process` and the brew latch exactly as they were.
        """
        prev = self._prev_process
        if prev is None:
            # R1 — the first frame after a reset records, never emits.
            self._prev_process = process
            return

        # R7 — leaving maintenance for any known non-maintenance process.
        if self._maintenance is not None and process not in MAINTENANCE_PROCESSES:
            events.append(LifecycleEvent(
                EVENT_MAINTENANCE_FINISHED,
                {
                    "process": self._maintenance.name,
                    "duration_s": _duration_s(self._maintenance_started_at, now),
                },
            ))
            self._maintenance = None
            self._maintenance_started_at = None

        # R4 — brew terminal. Looser than the cup-counter rule in ble_client
        # (which requires literally READY), because BUSY and SWITCH_OFF can sit
        # between PRODUCT and READY and would otherwise swallow the event.
        if self._brewing and process != MachineProcess.PRODUCT:
            events.append(self._terminal_event(
                process, now=now, cancel_detection=cancel_detection,
            ))

        # R3 — brew start.
        elif prev != MachineProcess.PRODUCT and process == MachineProcess.PRODUCT:
            self._brewing = True
            self._brew_started_at = now
            self._sub_processes = set()
            self._cancel_latched = False
            self._ha_cancel_latched = False
            self._intent = self._take_intent(intent, now)
            events.append(LifecycleEvent(
                EVENT_BREW_STARTED, self._intent_payload(self._intent),
            ))

        # R7 — entering maintenance. The first code of an episode is the one
        # reported; a machine that walks through several maintenance codes in a
        # row is one procedure from the user's point of view.
        if process in MAINTENANCE_PROCESSES and self._maintenance is None:
            self._maintenance = process
            self._maintenance_started_at = now

        self._prev_process = process

    def _take_intent(self, intent: BrewIntent | None, now: float) -> BrewIntent | None:
        """Consume the staged intent at a brew-start edge, honouring the TTL."""
        if intent is None:
            return None
        self._consumed_intent = True
        if not intent.is_fresh(now):
            _LOGGER.debug("Discarding a brew intent older than %.0fs", BREW_INTENT_TTL_S)
            return None
        return intent

    def _terminal_event(
        self, process: MachineProcess, *, now: float, cancel_detection: bool,
    ) -> LifecycleEvent:
        """Build `brew_finished` / `brew_cancelled` and clear the brew latches.

        A classifiable `shape` is attached to `brew_finished` only. A cancelled
        brew ran partway through its legs, so the set of sub-processes it
        observed describes how far it got rather than what was made, and naming
        that "coffee" would be a small lie in the one payload a user reads to
        find out what went wrong.
        """
        intent = self._intent
        shape = classify_brew_shape(self._sub_processes)
        payload = self._intent_payload(intent)
        payload["duration_s"] = _duration_s(self._brew_started_at, now)
        payload["cancel_detection"] = bool(cancel_detection)
        payload["final"] = _is_final(intent)

        cancel_source: str | None = None
        if self._ha_cancel_latched or (intent is not None and intent.ha_cancelled):
            cancel_source = "ha"
        elif self._cancel_latched:
            cancel_source = "machine"
        elif process == MachineProcess.SWITCH_OFF:
            cancel_source = "power_off"

        self._brewing = False
        self._brew_started_at = None
        self._sub_processes = set()
        self._cancel_latched = False
        self._ha_cancel_latched = False
        self._intent = None

        if cancel_source is None:
            # `shape` rides along even when the recipe name is known: it is
            # machine-readable and cheap, and an automation may well want to
            # branch on "was there milk in it". The narrator is the one place
            # that treats it as a fallback (`narration._compose`).
            if shape is not None:
                payload["shape"] = shape
            return LifecycleEvent(EVENT_BREW_FINISHED, payload)
        payload["cancel_source"] = cancel_source
        return LifecycleEvent(EVENT_BREW_CANCELLED, payload)

    # -- shape ---------------------------------------------------------------

    def _observe_sub_process(self, sub_process: SubProcess | None) -> None:
        """Accumulate the sub-processes seen while a brew is latched.

        Fed from every frame, `process is None` frames included, because on
        Nivona those are a normal part of a brew and still carry a usable
        sub-process. Called *after* the process edges on purpose: the PRODUCT
        frame that opens a brew contributes its own leg, while the frame that
        closes one does not — by then the latch is down, and the sub-process
        reported alongside READY belongs to no drink.
        """
        if not self._brewing or sub_process is None:
            return
        self._sub_processes.add(sub_process)

    # -- R6 ------------------------------------------------------------------

    def _feed_manipulation(
        self,
        status: MachineStatus,
        *,
        now: float,
        auto_confirm_enabled: bool,
        events: list[LifecycleEvent],
    ) -> None:
        """Evaluate the prompt edges (R6), independently of the process code.

        A prompt replacing a different prompt is reported as `prompt_cleared`
        followed by `prompt_raised`, in that order — one event per prompt, so a
        consumer counting raises against clears stays balanced. Identical
        repeats, which arrive on every poll while the prompt stands, emit
        nothing.
        """
        manipulation = status.manipulation
        prev = self._prev_manipulation
        self._prev_manipulation = manipulation

        if prev is None:
            # First frame after a reset: adopt the level, emit nothing. A prompt
            # already standing at connect time gets its clock started here so a
            # later prompt_cleared can still report an honest duration.
            if manipulation in PROMPT_MANIPULATIONS:
                self._prompt = manipulation
                self._prompt_started_at = now
            return
        if manipulation == prev:
            return

        if prev in PROMPT_MANIPULATIONS:
            events.append(LifecycleEvent(
                EVENT_PROMPT_CLEARED,
                {
                    "prompt": prev.name,
                    "duration_s": _duration_s(self._prompt_started_at, now),
                },
            ))
            self._prompt = None
            self._prompt_started_at = None

        if manipulation in PROMPT_MANIPULATIONS:
            soft = manipulation in SOFT_AUTO_CONFIRM_MANIPULATIONS
            self._prompt = manipulation
            self._prompt_started_at = now
            events.append(LifecycleEvent(
                EVENT_PROMPT_RAISED,
                {
                    "prompt": manipulation.name,
                    "soft": soft,
                    "auto_confirm": soft and bool(auto_confirm_enabled),
                    "during_brew": self._brewing,
                },
            ))

    # -- payload -------------------------------------------------------------

    def _intent_payload(self, intent: BrewIntent | None) -> dict[str, Any]:
        """Render a `BrewIntent` into the brew-event payload (§2.4).

        Absent facts are absent keys, never defaults: a front-panel brew reports
        `source: "machine"` and nothing else, because nothing else is known.
        """
        if intent is None:
            return {"source": "machine"}

        payload: dict[str, Any] = {}
        # Annotations first, so a stray key can never shadow a recorded fact.
        for key, value in dict(intent.extra).items():
            if isinstance(value, _JSON_SCALARS):
                payload[key] = value

        payload["source"] = "ha"
        if intent.recipe_source:
            payload["recipe_source"] = intent.recipe_source
        if intent.recipe_key:
            payload["recipe_key"] = intent.recipe_key
        if intent.recipe_name:
            payload["recipe_name"] = intent.recipe_name
        if intent.profile is not None:
            payload["profile"] = intent.profile
        if intent.profile_name:
            payload["profile_name"] = intent.profile_name
        if intent.two_cups is not None:
            payload["two_cups"] = bool(intent.two_cups)
        if intent.slot is not None:
            payload["slot"] = intent.slot

        components = _components_payload(intent.components)
        if components:
            payload["components"] = components
            total_ml = _total_ml(components)
            if total_ml is not None:
                payload["total_ml"] = total_ml
        return payload


# ---------------------------------------------------------------------------
# Payload helpers
# ---------------------------------------------------------------------------

def _duration_s(started_at: float | None, now: float) -> int:
    """Whole seconds since `started_at`, clamped at 0 when it is unknown."""
    if started_at is None:
        return 0
    return max(0, int(now - started_at))


def _is_final(intent: BrewIntent | None) -> bool:
    """False only while a multi-phase sommelier drink has phases left to brew."""
    if intent is None:
        return True
    extra = dict(intent.extra)
    index = extra.get("phase_index")
    total = extra.get("phase_total")
    if isinstance(index, int) and isinstance(total, int) and not isinstance(index, bool):
        return index >= total - 1
    return True


def _components_payload(
    components: Sequence[Mapping[str, Any]] | None,
) -> list[dict[str, Any]]:
    """Copy the tokenised components, dropping the `process == "none"` filler.

    A recipe always carries two component slots; an unused one is wire-encoded as
    `ComponentProcess.NONE` and is not something a person would ever say out
    loud. Copied rather than referenced so a later annotation cannot mutate an
    already-emitted payload.
    """
    if not components:
        return []
    return [
        dict(component)
        for component in components
        if isinstance(component, Mapping) and component.get("process") != "none"
    ]


def _total_ml(components: Sequence[Mapping[str, Any]]) -> int | None:
    """Sum of the components' `portion_ml`, or None when none of them state one.

    Never doubled for `two_cups`: the payload reports what one cup was composed
    of, and the consumer that cares has `two_cups` right there.
    """
    values = [
        component["portion_ml"]
        for component in components
        if isinstance(component.get("portion_ml"), (int, float))
        and not isinstance(component.get("portion_ml"), bool)
    ]
    if not values:
        return None
    return int(round(sum(values)))
