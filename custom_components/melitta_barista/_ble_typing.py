"""Type stubs for BLE client mixin protocol (TYPE_CHECKING only)."""

from __future__ import annotations

import asyncio
from typing import Callable, Protocol

from .protocol import MachineStatus, MelittaProtocol, MachineRecipe


class BleClientProtocol(Protocol):
    """Protocol defining attributes available to all BLE client mixins.

    This exists solely for mypy — mixins reference self._protocol, self.connected,
    etc. which are defined in MelittaBleClient.__init__. Without this protocol,
    mypy reports attr-defined errors on every mixin method.
    """

    _protocol: MelittaProtocol
    _connected: bool
    _client: object | None
    _brew_lock: asyncio.Lock
    _write_lock: asyncio.Lock
    _poll_task: asyncio.Task | None
    _poll_interval: float
    _recipe_retries: int
    _machine_type: object | None
    _profile_names: dict[int, str]
    _directkey_recipes: dict[int, dict[int, MachineRecipe]]
    _cups_callbacks: list[Callable[[], None]]
    _cup_counters: dict[str, int]
    _total_cups: int | None
    active_profile: int

    @property
    def connected(self) -> bool: ...
    async def _write_ble(self, data: bytes) -> None: ...
    def _stop_polling(self) -> None: ...
    def start_polling(self, interval: float = ...) -> None: ...
    def _notify_profile_callbacks(self) -> None: ...
