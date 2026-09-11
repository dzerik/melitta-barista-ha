"""Tests for the pure lifecycle detector (SPEC §2.2-2.5, §2.8).

No `hass`, no BLE, no I/O — every rule of §2.3 is driven by feeding hand-built
`MachineStatus` frames and an explicit monotonic clock.
"""

from __future__ import annotations

import json

import pytest

from custom_components.melitta_barista.coffee_platform.domain import (
    InfoMessage,
    MachineProcess,
    MachineStatus,
    Manipulation,
    SubProcess,
)
from custom_components.melitta_barista.const import (
    DOMAIN,
    PROMPT_MANIPULATIONS,
    SOFT_AUTO_CONFIRM_MANIPULATIONS,
)
from custom_components.melitta_barista.lifecycle import (
    BREW_INTENT_TTL_S,
    BREW_MAX_DURATION_S,
    BREW_SHAPE_TOKENS,
    EVENT_BREW_CANCELLED,
    EVENT_BREW_FINISHED,
    EVENT_BREW_STARTED,
    EVENT_MAINTENANCE_FINISHED,
    EVENT_PROMPT_CLEARED,
    EVENT_PROMPT_RAISED,
    EVENT_TYPES,
    MAINTENANCE_PROCESSES,
    MELITTA_LIFECYCLE_EVENT,
    BrewIntent,
    LifecycleDetector,
    LifecycleEvent,
    classify_brew_shape,
    now_monotonic,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _status(
    process: MachineProcess | None = MachineProcess.READY,
    *,
    manipulation: Manipulation = Manipulation.NONE,
    info: InfoMessage = InfoMessage(0),
    sub_process: SubProcess | None = None,
    progress: int = 0,
) -> MachineStatus:
    """Build a status frame the way the brand parsers do."""
    return MachineStatus(
        process=process,
        sub_process=sub_process,
        info_messages=info,
        manipulation=manipulation,
        progress=progress,
    )


def _detector(*, at: float = 100.0) -> LifecycleDetector:
    """A detector that has already seen its first (silent) READY frame."""
    detector = LifecycleDetector()
    assert detector.feed(_status(MachineProcess.READY), now=at) == []
    return detector


def _intent(**kwargs) -> BrewIntent:
    """A freshly staged intent at t=100 unless told otherwise."""
    kwargs.setdefault("noted_at", 100.0)
    return BrewIntent(**kwargs)


def _types(events: list[LifecycleEvent]) -> list[str]:
    return [event.type for event in events]


def _only(events: list[LifecycleEvent], event_type: str) -> LifecycleEvent:
    assert _types(events) == [event_type], _types(events)
    return events[0]


_COMPONENTS = [
    {"process": "coffee", "intensity": "strong", "aroma": "standard",
     "temperature": "normal", "shots": "one", "portion_ml": 100},
    {"process": "milk", "intensity": "medium", "aroma": "standard",
     "temperature": "normal", "shots": "none", "portion_ml": 150,
     "blend": "hopper_1"},
]


# ---------------------------------------------------------------------------
# Module surface
# ---------------------------------------------------------------------------

class TestModuleSurface:
    """The vocabulary other modules import."""

    def test_event_types_are_the_six_in_order(self):
        assert EVENT_TYPES == [
            "brew_started",
            "brew_finished",
            "brew_cancelled",
            "prompt_raised",
            "prompt_cleared",
            "maintenance_finished",
        ]

    def test_bus_event_name_is_domain_scoped(self):
        assert MELITTA_LIFECYCLE_EVENT == f"{DOMAIN}_event" == "melitta_barista_event"

    def test_maintenance_processes_is_the_enum_minus_four(self):
        assert MAINTENANCE_PROCESSES == frozenset(MachineProcess) - {
            MachineProcess.READY,
            MachineProcess.PRODUCT,
            MachineProcess.BUSY,
            MachineProcess.SWITCH_OFF,
        }
        assert {process.name for process in MAINTENANCE_PROCESSES} == {
            "CLEANING", "DESCALING", "FILTER_INSERT", "FILTER_REPLACE",
            "FILTER_REMOVE", "EASY_CLEAN", "INTENSIVE_CLEAN", "EVAPORATING",
        }

    def test_module_imports_no_home_assistant_and_no_ble(self):
        """device_trigger.py imports this on every automation-editor page load."""
        import ast

        from custom_components.melitta_barista import lifecycle

        with open(lifecycle.__file__, encoding="utf-8") as handle:
            tree = ast.parse(handle.read())
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imported.add("." * node.level + (node.module or ""))
        assert imported == {
            "__future__", "logging", "time", "collections.abc", "dataclasses", "typing",
            ".coffee_platform.domain", ".const",
        }

    def test_now_monotonic_is_monotonic(self):
        assert now_monotonic() <= now_monotonic()


class TestBrewIntent:
    """The record itself: freshness and the annotate merge."""

    def test_fresh_within_ttl_stale_beyond(self):
        intent = _intent()
        assert intent.is_fresh(100.0 + BREW_INTENT_TTL_S)
        assert not intent.is_fresh(100.0 + BREW_INTENT_TTL_S + 0.1)
        assert intent.age_s(130.0) == pytest.approx(30.0)

    def test_annotated_replaces_known_fields(self):
        annotated = _intent(recipe_name="Espresso").annotated(recipe_source="sommelier")
        assert annotated.recipe_source == "sommelier"
        assert annotated.recipe_name == "Espresso"
        assert annotated.noted_at == 100.0

    def test_annotated_routes_unknown_fields_into_extra(self):
        annotated = _intent().annotated(phase_index=1, phase_total=3)
        assert dict(annotated.extra) == {"phase_index": 1, "phase_total": 3}

    def test_annotated_merges_extra_and_leaves_the_original_alone(self):
        original = _intent().annotated(phase_index=0, phase_total=2)
        merged = original.annotated(phase_index=1)
        assert dict(merged.extra) == {"phase_index": 1, "phase_total": 2}
        assert dict(original.extra) == {"phase_index": 0, "phase_total": 2}


# ---------------------------------------------------------------------------
# R0 / R1 / R2
# ---------------------------------------------------------------------------

class TestFrameBookkeeping:
    """R0 (unknown process), R1 (first frame), R2 (reset)."""

    def test_first_frame_emits_nothing(self):
        detector = LifecycleDetector()
        assert detector.feed(_status(MachineProcess.PRODUCT), now=100.0) == []

    def test_first_frame_with_a_standing_prompt_emits_nothing(self):
        detector = LifecycleDetector()
        events = detector.feed(
            _status(MachineProcess.READY, manipulation=Manipulation.FILL_WATER), now=100.0,
        )
        assert events == []

    def test_first_frame_with_unknown_process_emits_nothing(self):
        detector = LifecycleDetector()
        assert detector.feed(_status(None), now=100.0) == []
        # ...and still does not count as "seen", so the next READY is the first frame.
        assert detector.feed(_status(MachineProcess.READY), now=101.0) == []
        assert _types(detector.feed(_status(MachineProcess.PRODUCT), now=102.0)) == [
            EVENT_BREW_STARTED,
        ]

    def test_unknown_process_is_not_a_transition_and_does_not_clear_brewing(self):
        detector = _detector()
        detector.feed(_status(MachineProcess.PRODUCT), now=101.0)
        assert detector.feed(_status(None), now=102.0) == []
        assert detector.state_snapshot()["brewing"] is True
        assert detector.state_snapshot()["prev_process"] == "PRODUCT"
        # The brew still finishes on the next known code.
        assert _types(detector.feed(_status(MachineProcess.READY), now=103.0)) == [
            EVENT_BREW_FINISHED,
        ]

    def test_prompt_edges_still_evaluated_on_an_unknown_process_frame(self):
        detector = _detector()
        events = detector.feed(
            _status(None, manipulation=Manipulation.FILL_WATER), now=101.0,
        )
        assert _types(events) == [EVENT_PROMPT_RAISED]

    def test_reset_between_product_and_ready_suppresses_the_finish(self):
        detector = _detector()
        detector.feed(_status(MachineProcess.PRODUCT), now=101.0)
        detector.reset()
        assert detector.feed(_status(MachineProcess.READY), now=102.0) == []
        assert detector.state_snapshot()["brewing"] is False

    def test_reset_clears_every_latch(self):
        detector = _detector()
        detector.feed(
            _status(MachineProcess.PRODUCT, manipulation=Manipulation.FILL_WATER), now=101.0,
        )
        detector.reset()
        assert detector.state_snapshot() == {
            "prev_process": None,
            "brewing": False,
            "brew_started_at": None,
            "sub_processes": [],
            "cancel_latched": False,
            "ha_cancel_latched": False,
            "has_intent": False,
            "prompt": None,
            "maintenance": None,
            "maintenance_started_at": None,
        }


# ---------------------------------------------------------------------------
# R3 — brew start and the intent
# ---------------------------------------------------------------------------

class TestBrewStarted:
    """R3 and the correlation rule of §2.5."""

    def test_ready_to_product_without_intent_is_a_machine_brew(self):
        detector = _detector()
        event = _only(detector.feed(_status(MachineProcess.PRODUCT), now=101.0),
                      EVENT_BREW_STARTED)
        assert event.payload == {"source": "machine"}
        assert detector.consumed_intent() is False

    def test_fresh_intent_produces_the_full_payload(self):
        detector = _detector()
        intent = _intent(
            recipe_source="base",
            recipe_key="cappuccino",
            recipe_name="Cappuccino",
            two_cups=False,
            components=_COMPONENTS,
        )
        event = _only(
            detector.feed(_status(MachineProcess.PRODUCT), intent=intent, now=110.0),
            EVENT_BREW_STARTED,
        )
        assert event.payload == {
            "source": "ha",
            "recipe_source": "base",
            "recipe_key": "cappuccino",
            "recipe_name": "Cappuccino",
            "two_cups": False,
            "components": _COMPONENTS,
            "total_ml": 250,
        }
        assert detector.consumed_intent() is True

    def test_directkey_intent_carries_profile_and_slot_paths_do_not(self):
        detector = _detector()
        event = _only(
            detector.feed(
                _status(MachineProcess.PRODUCT),
                intent=_intent(recipe_source="directkey", profile=2, profile_name="Anna"),
                now=101.0,
            ),
            EVENT_BREW_STARTED,
        )
        assert event.payload["profile"] == 2
        assert event.payload["profile_name"] == "Anna"
        assert "slot" not in event.payload

    def test_mycoffee_intent_carries_slot_and_no_components(self):
        detector = _detector()
        event = _only(
            detector.feed(
                _status(MachineProcess.PRODUCT),
                intent=_intent(recipe_source="mycoffee", slot=3),
                now=101.0,
            ),
            EVENT_BREW_STARTED,
        )
        assert event.payload["slot"] == 3
        assert "components" not in event.payload
        assert "total_ml" not in event.payload

    def test_slot_zero_survives_the_absence_check(self):
        detector = _detector()
        event = _only(
            detector.feed(
                _status(MachineProcess.PRODUCT),
                intent=_intent(recipe_source="mycoffee", slot=0),
                now=101.0,
            ),
            EVENT_BREW_STARTED,
        )
        assert event.payload["slot"] == 0

    def test_stale_intent_is_discarded_but_still_consumed(self):
        detector = _detector()
        stale = _intent(recipe_name="Yesterday", noted_at=0.0)
        event = _only(
            detector.feed(_status(MachineProcess.PRODUCT), intent=stale,
                          now=BREW_INTENT_TTL_S + 1.0),
            EVENT_BREW_STARTED,
        )
        assert event.payload == {"source": "machine"}
        # Consumed anyway: leaving it staged would let the NEXT brew inherit it.
        assert detector.consumed_intent() is True

    def test_intent_exactly_at_the_ttl_is_accepted(self):
        detector = _detector()
        event = _only(
            detector.feed(_status(MachineProcess.PRODUCT), intent=_intent(recipe_name="X"),
                          now=100.0 + BREW_INTENT_TTL_S),
            EVENT_BREW_STARTED,
        )
        assert event.payload["source"] == "ha"

    def test_consumed_intent_is_reset_by_the_next_feed(self):
        detector = _detector()
        detector.feed(_status(MachineProcess.PRODUCT), intent=_intent(), now=101.0)
        assert detector.consumed_intent() is True
        detector.feed(_status(MachineProcess.PRODUCT), now=102.0)
        assert detector.consumed_intent() is False

    def test_product_to_product_does_not_restart(self):
        detector = _detector()
        detector.feed(_status(MachineProcess.PRODUCT), now=101.0)
        assert detector.feed(_status(MachineProcess.PRODUCT), now=102.0) == []

    def test_none_process_components_are_dropped_from_payload_and_total(self):
        detector = _detector()
        components = [
            {"process": "coffee", "portion_ml": 40},
            {"process": "none", "portion_ml": 0},
        ]
        event = _only(
            detector.feed(_status(MachineProcess.PRODUCT),
                          intent=_intent(components=components), now=101.0),
            EVENT_BREW_STARTED,
        )
        assert event.payload["components"] == [{"process": "coffee", "portion_ml": 40}]
        assert event.payload["total_ml"] == 40

    def test_all_none_components_omit_components_and_total(self):
        detector = _detector()
        event = _only(
            detector.feed(_status(MachineProcess.PRODUCT),
                          intent=_intent(components=[{"process": "none", "portion_ml": 0}]),
                          now=101.0),
            EVENT_BREW_STARTED,
        )
        assert "components" not in event.payload
        assert "total_ml" not in event.payload

    def test_total_ml_is_not_doubled_for_two_cups(self):
        detector = _detector()
        event = _only(
            detector.feed(_status(MachineProcess.PRODUCT),
                          intent=_intent(components=_COMPONENTS, two_cups=True), now=101.0),
            EVENT_BREW_STARTED,
        )
        assert event.payload["total_ml"] == 250
        assert event.payload["two_cups"] is True

    def test_components_are_copied_not_referenced(self):
        detector = _detector()
        source = [{"process": "coffee", "portion_ml": 40}]
        event = _only(
            detector.feed(_status(MachineProcess.PRODUCT),
                          intent=_intent(components=source), now=101.0),
            EVENT_BREW_STARTED,
        )
        source[0]["portion_ml"] = 999
        assert event.payload["components"][0]["portion_ml"] == 40

    def test_phase_annotations_reach_the_payload(self):
        detector = _detector()
        intent = _intent(recipe_source="sommelier", recipe_name="Flat White").annotated(
            phase_index=0, phase_total=2,
        )
        event = _only(
            detector.feed(_status(MachineProcess.PRODUCT), intent=intent, now=101.0),
            EVENT_BREW_STARTED,
        )
        assert event.payload["phase_index"] == 0
        assert event.payload["phase_total"] == 2
        assert event.payload["recipe_source"] == "sommelier"

    def test_non_scalar_extra_values_never_reach_the_payload(self):
        detector = _detector()
        intent = _intent().annotated(junk={"a": 1}, phase_index=1)
        event = _only(
            detector.feed(_status(MachineProcess.PRODUCT), intent=intent, now=101.0),
            EVENT_BREW_STARTED,
        )
        assert "junk" not in event.payload
        assert event.payload["phase_index"] == 1

    def test_extra_cannot_shadow_a_recorded_fact(self):
        detector = _detector()
        intent = BrewIntent(
            noted_at=100.0, recipe_name="Real", extra={"source": "spoofed"},
        )
        event = _only(
            detector.feed(_status(MachineProcess.PRODUCT), intent=intent, now=101.0),
            EVENT_BREW_STARTED,
        )
        assert event.payload["source"] == "ha"


# ---------------------------------------------------------------------------
# R4 / R5 / terminal classification
# ---------------------------------------------------------------------------

class TestBrewTerminal:
    """R4 (terminal), R5 (cancel latch) and the cancel_source ladder."""

    def _brewing(self, *, intent: BrewIntent | None = None, at: float = 101.0):
        detector = _detector()
        detector.feed(_status(MachineProcess.PRODUCT), intent=intent, now=at)
        return detector

    def test_product_to_ready_finishes_with_duration(self):
        detector = self._brewing()
        event = _only(detector.feed(_status(MachineProcess.READY), now=131.0),
                      EVENT_BREW_FINISHED)
        assert event.payload["duration_s"] == 30
        assert event.payload["cancel_detection"] is True
        assert event.payload["final"] is True
        assert event.payload["source"] == "machine"
        assert "cancel_source" not in event.payload

    def test_terminal_carries_every_started_field(self):
        intent = _intent(recipe_source="base", recipe_key="lungo", recipe_name="Lungo",
                         two_cups=True, components=_COMPONENTS)
        detector = self._brewing(intent=intent)
        started = _detector()
        started_payload = _only(
            started.feed(_status(MachineProcess.PRODUCT), intent=intent, now=101.0),
            EVENT_BREW_STARTED,
        ).payload
        finished = _only(detector.feed(_status(MachineProcess.READY), now=120.0),
                         EVENT_BREW_FINISHED).payload
        for key, value in started_payload.items():
            assert finished[key] == value

    def test_busy_between_product_and_ready_finishes_exactly_once(self):
        detector = self._brewing()
        assert _types(detector.feed(_status(MachineProcess.BUSY), now=110.0)) == [
            EVENT_BREW_FINISHED,
        ]
        assert detector.feed(_status(MachineProcess.READY), now=111.0) == []

    def test_cancel_bit_on_the_terminal_frame(self):
        detector = self._brewing()
        event = _only(
            detector.feed(
                _status(MachineProcess.READY, info=InfoMessage.PREPARATION_CANCELLED),
                now=110.0,
            ),
            EVENT_BREW_CANCELLED,
        )
        assert event.payload["cancel_source"] == "machine"

    def test_cancel_bit_latched_on_an_earlier_frame(self):
        detector = self._brewing()
        assert detector.feed(
            _status(MachineProcess.PRODUCT, info=InfoMessage.PREPARATION_CANCELLED), now=105.0,
        ) == []
        assert detector.state_snapshot()["cancel_latched"] is True
        event = _only(detector.feed(_status(MachineProcess.READY), now=110.0),
                      EVENT_BREW_CANCELLED)
        assert event.payload["cancel_source"] == "machine"

    def test_cancel_bit_before_the_brew_does_not_latch(self):
        detector = _detector()
        detector.feed(
            _status(MachineProcess.READY, info=InfoMessage.PREPARATION_CANCELLED), now=101.0,
        )
        detector.feed(_status(MachineProcess.PRODUCT), now=102.0)
        assert detector.state_snapshot()["cancel_latched"] is False
        assert _types(detector.feed(_status(MachineProcess.READY), now=110.0)) == [
            EVENT_BREW_FINISHED,
        ]

    def test_a_cancel_marked_on_the_intent_wins_over_the_machine_latch(self):
        # The narrow case: HA cancelled between the brew ACK and the PRODUCT
        # frame, so the intent the detector captures already carries the flag.
        detector = self._brewing(intent=_intent(ha_cancelled=True))
        event = _only(
            detector.feed(
                _status(MachineProcess.READY, info=InfoMessage.PREPARATION_CANCELLED),
                now=110.0,
            ),
            EVENT_BREW_CANCELLED,
        )
        assert event.payload["cancel_source"] == "ha"

    def test_a_cancel_reported_on_the_terminal_frame_is_attributed_to_ha(self):
        """The real ordering: the intent was popped at the PRODUCT edge.

        By the time the Cancel button is pressable the machine has reported
        PRODUCT, which is exactly the frame that consumed the intent — so the
        flag arrives on `feed()`, not on the captured record.
        """
        detector = self._brewing(intent=_intent(recipe_name="Espresso"))
        event = _only(
            detector.feed(
                _status(MachineProcess.READY, info=InfoMessage.PREPARATION_CANCELLED),
                now=110.0,
                ha_cancelled=True,
            ),
            EVENT_BREW_CANCELLED,
        )
        assert event.payload["cancel_source"] == "ha"

    def test_a_cancel_reported_mid_brew_is_latched_until_the_terminal_frame(self):
        detector = self._brewing()
        assert detector.feed(
            _status(MachineProcess.PRODUCT), now=105.0, ha_cancelled=True,
        ) == []
        assert detector.state_snapshot()["ha_cancel_latched"] is True
        event = _only(detector.feed(_status(MachineProcess.READY), now=110.0),
                      EVENT_BREW_CANCELLED)
        assert event.payload["cancel_source"] == "ha"

    def test_a_cancel_ha_reported_without_the_machine_bit_is_still_a_cancel(self):
        """Nivona shape: no `PREPARATION_CANCELLED` bit exists to fall back on."""
        detector = self._brewing()
        event = _only(
            detector.feed(_status(MachineProcess.READY), now=110.0,
                          cancel_detection=False, ha_cancelled=True),
            EVENT_BREW_CANCELLED,
        )
        assert event.payload["cancel_source"] == "ha"
        assert event.payload["cancel_detection"] is False

    def test_a_cancel_with_no_brew_in_flight_is_dropped(self):
        """It has nothing to attribute — and must not blame the next brew."""
        detector = _detector()
        detector.feed(_status(MachineProcess.READY), now=101.0, ha_cancelled=True)
        assert detector.state_snapshot()["ha_cancel_latched"] is False
        detector.feed(_status(MachineProcess.PRODUCT), now=102.0)
        assert _types(detector.feed(_status(MachineProcess.READY), now=110.0)) == [
            EVENT_BREW_FINISHED,
        ]

    def test_the_ha_cancel_latch_does_not_survive_into_the_next_brew(self):
        detector = self._brewing()
        detector.feed(_status(MachineProcess.READY), now=110.0, ha_cancelled=True)
        detector.feed(_status(MachineProcess.PRODUCT), now=120.0)
        assert detector.state_snapshot()["ha_cancel_latched"] is False
        event = _only(detector.feed(_status(MachineProcess.READY), now=130.0),
                      EVENT_BREW_FINISHED)
        assert "cancel_source" not in event.payload

    def test_switch_off_is_a_power_off_cancel(self):
        detector = self._brewing()
        event = _only(detector.feed(_status(MachineProcess.SWITCH_OFF), now=110.0),
                      EVENT_BREW_CANCELLED)
        assert event.payload["cancel_source"] == "power_off"

    def test_nivona_shaped_terminal_reports_cancel_detection_false(self):
        detector = _detector()
        detector.feed(_status(MachineProcess.PRODUCT), now=101.0, cancel_detection=False)
        event = _only(
            detector.feed(_status(MachineProcess.READY), now=110.0, cancel_detection=False),
            EVENT_BREW_FINISHED,
        )
        assert event.payload["cancel_detection"] is False
        assert "cancel_source" not in event.payload

    def test_final_is_false_for_a_non_last_phase(self):
        intent = _intent(recipe_source="sommelier").annotated(phase_index=0, phase_total=2)
        detector = self._brewing(intent=intent)
        event = _only(detector.feed(_status(MachineProcess.READY), now=110.0),
                      EVENT_BREW_FINISHED)
        assert event.payload["final"] is False

    def test_final_is_true_for_the_last_phase(self):
        intent = _intent(recipe_source="sommelier").annotated(phase_index=1, phase_total=2)
        detector = self._brewing(intent=intent)
        event = _only(detector.feed(_status(MachineProcess.READY), now=110.0),
                      EVENT_BREW_FINISHED)
        assert event.payload["final"] is True

    def test_latches_are_cleared_after_a_terminal_event(self):
        detector = self._brewing(intent=_intent())
        detector.feed(_status(MachineProcess.READY), now=110.0)
        snapshot = detector.state_snapshot()
        assert snapshot["brewing"] is False
        assert snapshot["brew_started_at"] is None
        assert snapshot["cancel_latched"] is False
        assert snapshot["ha_cancel_latched"] is False
        assert snapshot["has_intent"] is False

    def test_a_second_brew_reuses_nothing_from_the_first(self):
        detector = self._brewing(intent=_intent(recipe_name="First"))
        detector.feed(_status(MachineProcess.READY), now=110.0)
        event = _only(detector.feed(_status(MachineProcess.PRODUCT), now=120.0),
                      EVENT_BREW_STARTED)
        assert event.payload == {"source": "machine"}


# ---------------------------------------------------------------------------
# R8 — the max-brew-age guard (M6)
# ---------------------------------------------------------------------------

class TestMaxBrewAge:
    """R8: an orphaned brew latch self-clears silently."""

    def test_expired_latch_emits_nothing_and_a_later_ready_is_silent(self):
        detector = _detector()
        detector.feed(_status(MachineProcess.PRODUCT), now=101.0)
        expired = 101.0 + BREW_MAX_DURATION_S + 1.0
        assert detector.feed(_status(MachineProcess.PRODUCT), now=expired) == []
        assert detector.state_snapshot()["brewing"] is False
        assert detector.feed(_status(MachineProcess.READY), now=expired + 5.0) == []

    def test_expiry_also_fires_on_an_unknown_process_frame(self):
        detector = _detector()
        detector.feed(_status(MachineProcess.PRODUCT), now=101.0)
        expired = 101.0 + BREW_MAX_DURATION_S + 1.0
        assert detector.feed(_status(None), now=expired) == []
        assert detector.state_snapshot()["brewing"] is False
        assert detector.feed(_status(MachineProcess.READY), now=expired + 1.0) == []

    def test_expiry_drops_the_captured_intent(self):
        detector = _detector()
        detector.feed(_status(MachineProcess.PRODUCT), intent=_intent(recipe_name="Stale"),
                      now=101.0)
        expired = 101.0 + BREW_MAX_DURATION_S + 1.0
        detector.feed(_status(None), now=expired)
        assert detector.state_snapshot()["has_intent"] is False

    def test_a_brew_exactly_at_the_limit_still_finishes(self):
        detector = _detector()
        detector.feed(_status(MachineProcess.PRODUCT), now=101.0)
        at_limit = 101.0 + BREW_MAX_DURATION_S
        event = _only(detector.feed(_status(MachineProcess.READY), now=at_limit),
                      EVENT_BREW_FINISHED)
        assert event.payload["duration_s"] == int(BREW_MAX_DURATION_S)

    def test_expiry_logs_at_debug_and_never_warns(self, caplog):
        detector = _detector()
        detector.feed(_status(MachineProcess.PRODUCT), now=101.0)
        with caplog.at_level("DEBUG", logger="melitta_barista"):
            detector.feed(_status(None), now=101.0 + BREW_MAX_DURATION_S + 1.0)
        assert [record.levelname for record in caplog.records] == ["DEBUG"]


# ---------------------------------------------------------------------------
# R6 — prompts
# ---------------------------------------------------------------------------

class TestPrompts:
    """R6: prompt_raised / prompt_cleared edges and their flags."""

    def test_none_to_prompt_raises_once_and_repeats_are_silent(self):
        detector = _detector()
        event = _only(
            detector.feed(_status(MachineProcess.READY,
                                  manipulation=Manipulation.FILL_WATER), now=101.0),
            EVENT_PROMPT_RAISED,
        )
        assert event.payload["prompt"] == "FILL_WATER"
        for tick in range(3):
            assert detector.feed(
                _status(MachineProcess.READY, manipulation=Manipulation.FILL_WATER),
                now=102.0 + tick,
            ) == []

    def test_prompt_to_none_clears_with_duration(self):
        detector = _detector()
        detector.feed(_status(MachineProcess.READY, manipulation=Manipulation.FILL_WATER),
                      now=101.0)
        event = _only(detector.feed(_status(MachineProcess.READY), now=113.0),
                      EVENT_PROMPT_CLEARED)
        assert event.payload == {"prompt": "FILL_WATER", "duration_s": 12}

    def test_prompt_swap_clears_then_raises_in_that_order(self):
        detector = _detector()
        detector.feed(_status(MachineProcess.READY, manipulation=Manipulation.FILL_WATER),
                      now=101.0)
        events = detector.feed(
            _status(MachineProcess.READY, manipulation=Manipulation.EMPTY_TRAYS), now=105.0,
        )
        assert _types(events) == [EVENT_PROMPT_CLEARED, EVENT_PROMPT_RAISED]
        assert events[0].payload["prompt"] == "FILL_WATER"
        assert events[0].payload["duration_s"] == 4
        assert events[1].payload["prompt"] == "EMPTY_TRAYS"

    def test_standing_prompt_at_connect_clears_with_an_honest_duration(self):
        detector = LifecycleDetector()
        detector.feed(_status(MachineProcess.READY, manipulation=Manipulation.FILL_WATER),
                      now=100.0)
        event = _only(detector.feed(_status(MachineProcess.READY), now=110.0),
                      EVENT_PROMPT_CLEARED)
        assert event.payload["duration_s"] == 10

    def test_manipulation_none_is_never_narrated(self):
        detector = _detector()
        assert Manipulation.NONE not in PROMPT_MANIPULATIONS
        assert detector.feed(_status(MachineProcess.READY), now=101.0) == []

    @pytest.mark.parametrize("prompt", sorted(SOFT_AUTO_CONFIRM_MANIPULATIONS))
    def test_soft_prompts_report_soft_and_auto_confirm(self, prompt):
        detector = _detector()
        event = _only(
            detector.feed(_status(MachineProcess.READY, manipulation=prompt), now=101.0,
                          auto_confirm_enabled=True),
            EVENT_PROMPT_RAISED,
        )
        assert event.payload["soft"] is True
        assert event.payload["auto_confirm"] is True

    def test_soft_prompt_without_the_option_is_not_auto_confirmed(self):
        detector = _detector()
        event = _only(
            detector.feed(
                _status(MachineProcess.READY, manipulation=Manipulation.FLUSH_REQUIRED),
                now=101.0, auto_confirm_enabled=False,
            ),
            EVENT_PROMPT_RAISED,
        )
        assert event.payload["soft"] is True
        assert event.payload["auto_confirm"] is False

    def test_hard_prompt_is_never_auto_confirmed_even_with_the_option_on(self):
        detector = _detector()
        event = _only(
            detector.feed(_status(MachineProcess.READY, manipulation=Manipulation.FILL_WATER),
                          now=101.0, auto_confirm_enabled=True),
            EVENT_PROMPT_RAISED,
        )
        assert event.payload["soft"] is False
        assert event.payload["auto_confirm"] is False

    def test_during_brew_is_true_while_brewing(self):
        detector = _detector()
        detector.feed(_status(MachineProcess.PRODUCT), now=101.0)
        event = _only(
            detector.feed(_status(MachineProcess.PRODUCT,
                                  manipulation=Manipulation.MOVE_CUP_TO_FROTHER), now=102.0),
            EVENT_PROMPT_RAISED,
        )
        assert event.payload["during_brew"] is True

    def test_during_brew_is_false_after_the_brew_ended_on_the_same_frame(self):
        detector = _detector()
        detector.feed(_status(MachineProcess.PRODUCT), now=101.0)
        events = detector.feed(
            _status(MachineProcess.READY, manipulation=Manipulation.FLUSH_REQUIRED), now=110.0,
        )
        assert _types(events) == [EVENT_BREW_FINISHED, EVENT_PROMPT_RAISED]
        assert events[1].payload["during_brew"] is False

    def test_prompt_raised_on_the_frame_that_starts_a_brew_is_during_brew(self):
        detector = _detector()
        events = detector.feed(
            _status(MachineProcess.PRODUCT, manipulation=Manipulation.MOVE_CUP_TO_FROTHER),
            now=101.0,
        )
        assert _types(events) == [EVENT_BREW_STARTED, EVENT_PROMPT_RAISED]
        assert events[1].payload["during_brew"] is True

    @pytest.mark.parametrize("prompt", sorted(PROMPT_MANIPULATIONS))
    def test_every_prompt_code_raises(self, prompt):
        detector = _detector()
        event = _only(
            detector.feed(_status(MachineProcess.READY, manipulation=prompt), now=101.0),
            EVENT_PROMPT_RAISED,
        )
        assert event.payload["prompt"] == prompt.name


# ---------------------------------------------------------------------------
# R7 — maintenance
# ---------------------------------------------------------------------------

class TestMaintenance:
    """R7: one maintenance_finished per episode, brew events untouched."""

    def test_ready_descaling_ready_finishes_maintenance(self):
        detector = _detector()
        assert detector.feed(_status(MachineProcess.DESCALING), now=101.0) == []
        event = _only(detector.feed(_status(MachineProcess.READY), now=161.0),
                      EVENT_MAINTENANCE_FINISHED)
        assert event.payload == {"process": "DESCALING", "duration_s": 60}

    def test_maintenance_never_produces_brew_events(self):
        detector = _detector()
        for process in sorted(MAINTENANCE_PROCESSES, key=lambda p: p.value):
            detector.reset()
            detector.feed(_status(MachineProcess.READY), now=100.0)
            assert detector.feed(_status(process), now=101.0) == []
            events = detector.feed(_status(MachineProcess.READY), now=105.0)
            assert _types(events) == [EVENT_MAINTENANCE_FINISHED]
            assert events[0].payload["process"] == process.name

    def test_a_run_of_maintenance_codes_is_one_episode(self):
        detector = _detector()
        detector.feed(_status(MachineProcess.EASY_CLEAN), now=101.0)
        assert detector.feed(_status(MachineProcess.INTENSIVE_CLEAN), now=105.0) == []
        event = _only(detector.feed(_status(MachineProcess.READY), now=110.0),
                      EVENT_MAINTENANCE_FINISHED)
        assert event.payload["process"] == "EASY_CLEAN"
        assert event.payload["duration_s"] == 9

    def test_unknown_process_does_not_end_a_maintenance_episode(self):
        detector = _detector()
        detector.feed(_status(MachineProcess.CLEANING), now=101.0)
        assert detector.feed(_status(None), now=102.0) == []
        assert detector.state_snapshot()["maintenance"] == "CLEANING"
        assert _types(detector.feed(_status(MachineProcess.READY), now=103.0)) == [
            EVENT_MAINTENANCE_FINISHED,
        ]

    def test_brew_ending_into_maintenance_reports_both(self):
        detector = _detector()
        detector.feed(_status(MachineProcess.PRODUCT), now=101.0)
        events = detector.feed(_status(MachineProcess.EASY_CLEAN), now=110.0)
        assert _types(events) == [EVENT_BREW_FINISHED]
        assert _types(detector.feed(_status(MachineProcess.READY), now=120.0)) == [
            EVENT_MAINTENANCE_FINISHED,
        ]

    def test_maintenance_into_product_reports_finish_then_start(self):
        detector = _detector()
        detector.feed(_status(MachineProcess.EASY_CLEAN), now=101.0)
        events = detector.feed(_status(MachineProcess.PRODUCT), now=110.0)
        assert _types(events) == [EVENT_MAINTENANCE_FINISHED, EVENT_BREW_STARTED]

    def test_busy_ends_a_maintenance_episode(self):
        detector = _detector()
        detector.feed(_status(MachineProcess.FILTER_REPLACE), now=101.0)
        assert _types(detector.feed(_status(MachineProcess.BUSY), now=105.0)) == [
            EVENT_MAINTENANCE_FINISHED,
        ]


# ---------------------------------------------------------------------------
# Brew shape — what the sub-process legs say about the drink
# ---------------------------------------------------------------------------

class TestBrewShape:
    """The `shape` token on `brew_finished` (sub_process accumulation).

    The machine never reports what it is MAKING, so a brew started at the front
    panel has no name at all. The legs it runs through do say whether there was
    coffee, milk or only water in it, and that is what `shape` carries.
    """

    def _finish(
        self,
        legs,
        *,
        intent: BrewIntent | None = None,
        info: InfoMessage = InfoMessage(0),
        terminal: MachineProcess = MachineProcess.READY,
    ) -> LifecycleEvent:
        """Run one whole brew, one PRODUCT frame per leg, and return its end."""
        detector = _detector()
        first = legs[0] if legs else None
        detector.feed(_status(MachineProcess.PRODUCT, sub_process=first),
                      intent=intent, now=101.0)
        for offset, leg in enumerate(legs[1:], start=2):
            assert detector.feed(
                _status(MachineProcess.PRODUCT, sub_process=leg), now=100.0 + offset,
            ) == []
        events = detector.feed(_status(terminal, info=info), now=150.0)
        assert len(events) == 1
        return events[0]

    # -- the classifier itself, as a truth table -----------------------------

    @pytest.mark.parametrize("legs, expected", [
        ((SubProcess.COFFEE,), "coffee"),
        ((SubProcess.GRINDING,), "coffee"),
        ((SubProcess.GRINDING, SubProcess.COFFEE), "coffee"),
        ((SubProcess.STEAM,), "milk"),
        ((SubProcess.STEAM, SubProcess.COFFEE), "coffee_with_milk"),
        ((SubProcess.COFFEE, SubProcess.STEAM), "coffee_with_milk"),
        ((SubProcess.GRINDING, SubProcess.STEAM), "coffee_with_milk"),
        ((SubProcess.WATER,), "water"),
        # An americano grinds, brews and tops up with water: still coffee.
        ((SubProcess.COFFEE, SubProcess.WATER), "coffee"),
        # Water alongside steam is the frother's own rinse water, not a drink.
        ((SubProcess.WATER, SubProcess.STEAM), "milk"),
        ((), None),
    ])
    def test_classification_truth_table(self, legs, expected):
        assert classify_brew_shape(legs) == expected

    @pytest.mark.parametrize("legs, expected", [
        ((SubProcess.PREPARE,), None),
        ((SubProcess.PREPARE, SubProcess.WATER), "water"),
        ((SubProcess.PREPARE, SubProcess.COFFEE), "coffee"),
        ((SubProcess.PREPARE, SubProcess.STEAM), "milk"),
    ])
    def test_prepare_is_noise_and_classifies_nothing(self, legs, expected):
        """PREPARE is the rinse leg every product runs, so it says nothing."""
        assert classify_brew_shape(legs) == expected

    def test_every_classifier_output_is_in_the_vocabulary(self):
        """A token the narrator has no sentence for must be unreachable."""
        for legs in (
            (SubProcess.COFFEE,), (SubProcess.GRINDING,), (SubProcess.STEAM,),
            (SubProcess.WATER,), (SubProcess.STEAM, SubProcess.COFFEE),
        ):
            assert classify_brew_shape(legs) in BREW_SHAPE_TOKENS

    # -- the detector --------------------------------------------------------

    def test_a_front_panel_brew_reports_its_shape_and_nothing_else(self):
        event = self._finish((SubProcess.GRINDING, SubProcess.COFFEE, SubProcess.STEAM))
        assert event.type == EVENT_BREW_FINISHED
        assert event.payload["source"] == "machine"
        assert event.payload["shape"] == "coffee_with_milk"
        assert "recipe_name" not in event.payload

    def test_shape_rides_along_even_when_the_recipe_is_known(self):
        """It is machine-readable information; only the NARRATION hides it."""
        event = self._finish(
            (SubProcess.COFFEE, SubProcess.STEAM),
            intent=_intent(recipe_source="directkey", recipe_key="cappuccino",
                           recipe_name="Cappuccino"),
        )
        assert event.payload["recipe_name"] == "Cappuccino"
        assert event.payload["shape"] == "coffee_with_milk"

    def test_an_unclassifiable_brew_has_no_shape_key_at_all(self):
        """An absent fact is an absent key — never a default."""
        assert "shape" not in self._finish(()).payload
        assert "shape" not in self._finish((SubProcess.PREPARE,)).payload

    def test_brew_started_never_carries_a_shape(self):
        """Nothing has been observed yet when the brew starts."""
        detector = _detector()
        started = _only(
            detector.feed(_status(MachineProcess.PRODUCT, sub_process=SubProcess.COFFEE),
                          now=101.0),
            EVENT_BREW_STARTED,
        )
        assert "shape" not in started.payload

    def test_a_cancelled_brew_never_carries_a_shape(self):
        """The legs say how far it got, not what was made — scope stays tight."""
        event = self._finish(
            (SubProcess.COFFEE,), info=InfoMessage.PREPARATION_CANCELLED,
        )
        assert event.type == EVENT_BREW_CANCELLED
        assert "shape" not in event.payload

    def test_the_terminal_frames_own_sub_process_is_not_counted(self):
        """READY's sub-process belongs to no drink — the latch is already down."""
        detector = _detector()
        detector.feed(_status(MachineProcess.PRODUCT), now=101.0)
        event = _only(
            detector.feed(_status(MachineProcess.READY, sub_process=SubProcess.STEAM),
                          now=110.0),
            EVENT_BREW_FINISHED,
        )
        assert "shape" not in event.payload

    def test_legs_seen_on_an_unknown_process_frame_still_count(self):
        """The Nivona case: an unmapped process code still carries a sub-process."""
        detector = _detector()
        detector.feed(_status(MachineProcess.PRODUCT), now=101.0)
        assert detector.feed(_status(None, sub_process=SubProcess.STEAM), now=102.0) == []
        event = _only(detector.feed(_status(MachineProcess.READY), now=110.0),
                      EVENT_BREW_FINISHED)
        assert event.payload["shape"] == "milk"

    def test_legs_never_leak_from_one_brew_into_the_next(self):
        detector = _detector()
        detector.feed(_status(MachineProcess.PRODUCT, sub_process=SubProcess.STEAM),
                      now=101.0)
        first = _only(detector.feed(_status(MachineProcess.READY), now=110.0),
                      EVENT_BREW_FINISHED)
        assert first.payload["shape"] == "milk"

        detector.feed(_status(MachineProcess.PRODUCT, sub_process=SubProcess.COFFEE),
                      now=120.0)
        second = _only(detector.feed(_status(MachineProcess.READY), now=130.0),
                       EVENT_BREW_FINISHED)
        assert second.payload["shape"] == "coffee"

    def test_a_disconnect_reset_drops_the_observed_legs(self):
        """R2: nothing observed before a disconnect may describe a later brew."""
        detector = _detector()
        detector.feed(_status(MachineProcess.PRODUCT, sub_process=SubProcess.STEAM),
                      now=101.0)
        detector.reset()
        assert detector.state_snapshot()["sub_processes"] == []

        detector.feed(_status(MachineProcess.READY), now=200.0)
        detector.feed(_status(MachineProcess.PRODUCT, sub_process=SubProcess.COFFEE),
                      now=201.0)
        event = _only(detector.feed(_status(MachineProcess.READY), now=210.0),
                      EVENT_BREW_FINISHED)
        assert event.payload["shape"] == "coffee"

    def test_an_expired_brew_latch_drops_the_observed_legs(self):
        """R8: the silently dropped brew must not lend its legs to the next one."""
        detector = _detector()
        detector.feed(_status(MachineProcess.PRODUCT, sub_process=SubProcess.STEAM),
                      now=101.0)
        assert detector.feed(
            _status(MachineProcess.PRODUCT), now=101.0 + BREW_MAX_DURATION_S + 1.0,
        ) == []
        assert detector.state_snapshot()["sub_processes"] == []

    def test_state_snapshot_reports_the_legs_seen_so_far(self):
        detector = _detector()
        detector.feed(_status(MachineProcess.PRODUCT, sub_process=SubProcess.GRINDING),
                      now=101.0)
        detector.feed(_status(MachineProcess.PRODUCT, sub_process=SubProcess.COFFEE),
                      now=102.0)
        snapshot = detector.state_snapshot()
        assert snapshot["sub_processes"] == ["COFFEE", "GRINDING"]
        json.dumps(snapshot)

# ---------------------------------------------------------------------------
# Cross-cutting invariants
# ---------------------------------------------------------------------------

class TestPayloadInvariants:
    """Every emitted event is well-formed, whatever produced it."""

    def _all_events(self) -> list[LifecycleEvent]:
        events: list[LifecycleEvent] = []
        detector = _detector()
        intent = _intent(
            recipe_source="directkey", recipe_key="cappuccino", recipe_name="Cappuccino",
            profile=2, profile_name="Anna", two_cups=True, slot=1, components=_COMPONENTS,
        ).annotated(phase_index=0, phase_total=2)
        events += detector.feed(
            _status(MachineProcess.PRODUCT, manipulation=Manipulation.MOVE_CUP_TO_FROTHER),
            intent=intent, now=101.0, auto_confirm_enabled=True,
        )
        events += detector.feed(
            _status(MachineProcess.READY, info=InfoMessage.PREPARATION_CANCELLED), now=110.0,
        )
        detector.feed(_status(MachineProcess.PRODUCT), now=120.0)
        events += detector.feed(_status(MachineProcess.SWITCH_OFF), now=130.0)
        detector.reset()
        detector.feed(_status(MachineProcess.READY), now=200.0)
        detector.feed(_status(MachineProcess.PRODUCT), now=201.0)
        events += detector.feed(_status(MachineProcess.READY), now=210.0)
        events += detector.feed(_status(MachineProcess.DESCALING), now=211.0)
        events += detector.feed(_status(MachineProcess.READY), now=260.0)
        return events

    def test_every_emitted_type_is_in_event_types(self):
        events = self._all_events()
        assert events
        for event in events:
            assert event.type in EVENT_TYPES

    def test_every_payload_survives_json_dumps(self):
        for event in self._all_events():
            json.dumps(event.payload)

    def test_all_six_types_are_reachable(self):
        produced = set(_types(self._all_events()))
        detector = _detector()
        detector.feed(_status(MachineProcess.READY, manipulation=Manipulation.FILL_WATER),
                      now=101.0)
        produced |= set(_types(detector.feed(_status(MachineProcess.READY), now=102.0)))
        assert produced == set(EVENT_TYPES)

    def test_state_snapshot_is_json_safe_and_never_raises(self):
        detector = _detector()
        detector.feed(_status(MachineProcess.PRODUCT), intent=_intent(recipe_name="Anna's"),
                      now=101.0)
        detector.feed(_status(MachineProcess.PRODUCT, manipulation=Manipulation.FILL_WATER),
                      now=102.0)
        snapshot = detector.state_snapshot()
        json.dumps(snapshot)
        assert snapshot["brewing"] is True
        assert snapshot["prompt"] == "FILL_WATER"
        # No user-authored free text anywhere in the snapshot (M12).
        assert "Anna" not in json.dumps(snapshot)

    def test_state_snapshot_reports_maintenance(self):
        detector = _detector()
        detector.feed(_status(MachineProcess.DESCALING), now=101.0)
        snapshot = detector.state_snapshot()
        assert snapshot["maintenance"] == "DESCALING"
        assert snapshot["maintenance_started_at"] == 101.0

    def test_feed_without_now_uses_the_monotonic_clock(self):
        detector = LifecycleDetector()
        detector.feed(_status(MachineProcess.READY))
        events = detector.feed(_status(MachineProcess.PRODUCT))
        assert _types(events) == [EVENT_BREW_STARTED]
        assert detector.state_snapshot()["brew_started_at"] is not None
