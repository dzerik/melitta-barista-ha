"""Tests for the explicit bond-health state machine (0.88 refactoring).

Design invariants (from the recovery-layer audit):
- Only classified AUTH failures drive transitions towards MISMATCH;
  timeouts / link / handshake failures never leave TRUSTED.
- Destructive unpair is legal only with MISMATCH-grade evidence
  (>=2 auth-fail cycles), and destroying a bond lands in PAIRING_REQUIRED.
- A successful encrypted handshake always returns to TRUSTED and clears
  the episode.
- Every transition is recorded in a bounded history (bond_ops audit trail).
"""

from __future__ import annotations

from custom_components.melitta_barista.bond_state import (
    BondState,
    BondStateMachine,
)
from custom_components.melitta_barista.ble_client import (
    FAILURE_AUTH,
    FAILURE_LINK,
    FAILURE_TIMEOUT,
)


def test_initial_state_unknown():
    m = BondStateMachine()
    assert m.state is BondState.UNKNOWN
    assert m.auth_fail_cycles == 0


def test_transient_failures_never_leave_trusted():
    m = BondStateMachine()
    m.on_handshake_success()
    assert m.state is BondState.TRUSTED
    for cls in (FAILURE_TIMEOUT, FAILURE_LINK, "handshake_fail"):
        for _ in range(5):
            m.on_cycle_failure(cls)
    assert m.state is BondState.TRUSTED
    assert m.auth_fail_cycles == 0


def test_single_auth_cycle_is_suspect_not_mismatch():
    m = BondStateMachine()
    m.on_handshake_success()
    m.on_cycle_failure(FAILURE_AUTH)
    assert m.state is BondState.SUSPECT
    assert m.allow_unpair(current_cycle_auth=False) is False


def test_two_auth_cycles_reach_mismatch():
    m = BondStateMachine()
    m.on_cycle_failure(FAILURE_AUTH)
    m.on_cycle_failure(FAILURE_AUTH)
    assert m.state is BondState.MISMATCH
    assert m.allow_unpair(current_cycle_auth=False) is True


def test_suspect_plus_current_cycle_auth_allows_unpair():
    """The second auth evidence may be the cycle currently in flight."""
    m = BondStateMachine()
    m.on_cycle_failure(FAILURE_AUTH)
    assert m.state is BondState.SUSPECT
    assert m.allow_unpair(current_cycle_auth=True) is True


def test_unknown_with_current_auth_does_not_allow_unpair():
    m = BondStateMachine()
    assert m.allow_unpair(current_cycle_auth=True) is False


def test_bond_destroyed_lands_in_pairing_required_and_blocks_unpair():
    m = BondStateMachine()
    m.on_cycle_failure(FAILURE_AUTH)
    m.on_cycle_failure(FAILURE_AUTH)
    m.on_bond_destroyed(op="proxy_unpair", trigger="rung3")
    assert m.state is BondState.PAIRING_REQUIRED
    assert m.allow_unpair(current_cycle_auth=True) is False


def test_handshake_success_restores_trusted_from_any_state():
    m = BondStateMachine()
    m.on_cycle_failure(FAILURE_AUTH)
    m.on_cycle_failure(FAILURE_AUTH)
    m.on_bond_destroyed(op="proxy_unpair", trigger="rung3")
    m.on_handshake_success()
    assert m.state is BondState.TRUSTED
    assert m.auth_fail_cycles == 0


def test_history_records_transitions_bounded():
    m = BondStateMachine()
    for _ in range(50):
        m.on_cycle_failure(FAILURE_AUTH)
    ops = m.bond_ops
    assert len(ops) <= 20
    assert all("ts" in op and "event" in op and "state" in op for op in ops)


def test_on_change_callback_fires_on_state_change():
    events: list[tuple[str, str]] = []
    m = BondStateMachine(
        on_change=lambda machine, event: events.append(
            (machine.state.value, event["event"])
        ),
    )
    m.on_cycle_failure(FAILURE_AUTH)
    m.on_cycle_failure(FAILURE_AUTH)
    m.on_bond_destroyed(op="proxy_unpair", trigger="rung3")
    m.on_handshake_success()
    states = [s for s, _ in events]
    assert "suspect" in states
    assert "mismatch" in states
    assert "pairing_required" in states
    assert states[-1] == "trusted"


def test_roundtrip_persistence():
    m = BondStateMachine()
    m.on_cycle_failure(FAILURE_AUTH)
    m.on_cycle_failure(FAILURE_AUTH)
    data = m.as_dict()
    restored = BondStateMachine(initial=data)
    assert restored.state is BondState.MISMATCH
    assert restored.auth_fail_cycles == 2
    assert len(restored.bond_ops) == len(m.bond_ops)


def test_restore_from_garbage_is_safe():
    restored = BondStateMachine(initial={"state": "bogus", "auth_fail_cycles": "x"})
    assert restored.state is BondState.UNKNOWN
    assert restored.auth_fail_cycles == 0
