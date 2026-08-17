"""Explicit bond-health state machine for the BLE recovery layer.

Born from the 0.87.x recovery-layer audit: three regressions in a row came
from inferring bond health per-cycle out of transient local signals. This
module makes the health explicit and persistent, with two hard invariants:

- transitions towards MISMATCH are driven ONLY by classified AUTH failures
  (the machine actively rejecting the SMP exchange); timeouts, link drops
  and handshake failures never degrade a TRUSTED bond;
- the destructive unpair is legal only with MISMATCH-grade evidence
  (>= 2 auth-fail cycles), and destroying a bond lands in PAIRING_REQUIRED
  where further destruction is blocked until a successful handshake.

Every transition is recorded into a bounded ``bond_ops`` history exported
by diagnostics — the audit trail whose absence made the earlier bond-wipe
regressions invisible.
"""

from __future__ import annotations

import time
from collections import deque
from enum import Enum
from typing import Any, Callable

from .const import FAILURE_AUTH

# Distinct connect cycles with an SMP rejection required before the bond is
# considered mismatched and destruction becomes legal.
AUTH_CYCLES_FOR_MISMATCH = 2

_HISTORY_LEN = 20


class BondState(str, Enum):
    """Health of the proxy<->machine bond as far as HA can know it."""

    UNKNOWN = "unknown"              # never connected in this install
    TRUSTED = "trusted"              # last encrypted handshake succeeded
    SUSPECT = "suspect"              # one auth-fail cycle seen
    MISMATCH = "mismatch"            # repeated SMP rejections — bond broken
    PAIRING_REQUIRED = "pairing_required"  # bond destroyed; user action likely


class BondStateMachine:
    """Tracks bond health; the single authority for destructive decisions."""

    def __init__(
        self,
        initial: dict[str, Any] | None = None,
        on_change: Callable[["BondStateMachine", dict[str, Any]], None] | None = None,
    ) -> None:
        self._state = BondState.UNKNOWN
        self._auth_fail_cycles = 0
        self._last_handshake_at: float | None = None
        self._last_auth_fail_at: float | None = None
        self._bond_destroyed_at: float | None = None
        self._history: deque[dict[str, Any]] = deque(maxlen=_HISTORY_LEN)
        self._on_change = on_change
        if initial:
            self._restore(initial)

    # ------------------------------------------------------------------ #
    # Introspection

    @property
    def state(self) -> BondState:
        """Current bond-health state."""
        return self._state

    @property
    def auth_fail_cycles(self) -> int:
        """Distinct connect cycles that ended in an SMP rejection."""
        return self._auth_fail_cycles

    @property
    def bond_ops(self) -> list[dict[str, Any]]:
        """Bounded audit trail of transitions and destructive operations."""
        return list(self._history)

    # ------------------------------------------------------------------ #
    # Transitions

    def on_handshake_success(self) -> None:
        """A successful encrypted handshake — the bond is proven good."""
        self._last_handshake_at = time.time()
        self._auth_fail_cycles = 0
        self._transition(BondState.TRUSTED, "handshake_success")

    def on_cycle_failure(self, failure_class: str | None) -> None:
        """Record the outcome of a failed connect cycle.

        Only AUTH-class failures move the machine towards MISMATCH; every
        other class is treated as environmental noise and does not touch
        the state (a TRUSTED bond stays trusted through any number of
        timeouts — the core lesson of the Jay regression).
        """
        if failure_class != FAILURE_AUTH:
            return
        self._auth_fail_cycles += 1
        self._last_auth_fail_at = time.time()
        if self._state is BondState.PAIRING_REQUIRED:
            # Already destroyed our side; keep counting for diagnostics but
            # stay put — only a successful handshake leaves this state.
            self._record("auth_fail_while_pairing_required")
            return
        if self._auth_fail_cycles >= AUTH_CYCLES_FOR_MISMATCH:
            self._transition(BondState.MISMATCH, "auth_fail")
        else:
            self._transition(BondState.SUSPECT, "auth_fail")

    def on_bond_destroyed(self, *, op: str, trigger: str) -> None:
        """A destructive bond operation ran (unpair / clear_ble_bonds)."""
        self._bond_destroyed_at = time.time()
        self._transition(
            BondState.PAIRING_REQUIRED, "bond_destroyed", op=op, trigger=trigger,
        )

    def allow_unpair(self, *, current_cycle_auth: bool) -> bool:
        """True when destroying the proxy-side bond is justified.

        Requires MISMATCH-grade evidence: either the machine is already in
        MISMATCH, or it is SUSPECT and the cycle currently in flight
        produced another SMP rejection (the second distinct auth cycle).
        PAIRING_REQUIRED always refuses — the bond is already gone.
        """
        if self._state is BondState.MISMATCH:
            return True
        return self._state is BondState.SUSPECT and current_cycle_auth

    # ------------------------------------------------------------------ #
    # Persistence

    def as_dict(self) -> dict[str, Any]:
        """Serializable snapshot (hass Store payload)."""
        return {
            "state": self._state.value,
            "auth_fail_cycles": self._auth_fail_cycles,
            "last_handshake_at": self._last_handshake_at,
            "last_auth_fail_at": self._last_auth_fail_at,
            "bond_destroyed_at": self._bond_destroyed_at,
            "history": list(self._history),
        }

    def _restore(self, data: dict[str, Any]) -> None:
        """Best-effort restore; malformed fields fall back to defaults."""
        try:
            self._state = BondState(data.get("state"))
        except ValueError:
            self._state = BondState.UNKNOWN
        cycles = data.get("auth_fail_cycles")
        self._auth_fail_cycles = cycles if isinstance(cycles, int) else 0
        for attr, key in (
            ("_last_handshake_at", "last_handshake_at"),
            ("_last_auth_fail_at", "last_auth_fail_at"),
            ("_bond_destroyed_at", "bond_destroyed_at"),
        ):
            value = data.get(key)
            setattr(self, attr, value if isinstance(value, (int, float)) else None)
        history = data.get("history")
        if isinstance(history, list):
            self._history.extend(
                op for op in history[-_HISTORY_LEN:] if isinstance(op, dict)
            )

    # ------------------------------------------------------------------ #
    # Internals

    def _transition(self, new_state: BondState, event: str, **extra: Any) -> None:
        self._state = new_state
        self._record(event, **extra)

    def _record(self, event: str, **extra: Any) -> None:
        entry: dict[str, Any] = {
            "ts": time.time(),
            "event": event,
            "state": self._state.value,
            "auth_fail_cycles": self._auth_fail_cycles,
            **extra,
        }
        self._history.append(entry)
        if self._on_change is not None:
            try:
                self._on_change(self, entry)
            except Exception:  # noqa: BLE001 — listeners must not break transitions
                pass
