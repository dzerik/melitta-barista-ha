"""CoffeeMachineClient — transport-agnostic contract for a connected machine.

Any brand provider (Eugster/BLE today; De'Longhi/Jura/Nespresso later) returns
an object satisfying this Protocol. Consumers (HA entities, Sommelier) depend
ONLY on this surface — never on a concrete client class or its private
attributes.

Deliberately EXCLUDED (transport-specific, belong to the BLE provider, not the
contract): `set_ble_device`, `set_repair_callback`, `set_presence_callback`,
`consecutive_connect_failures`, `record_error`, `detection_callback`.
"""

from __future__ import annotations

from typing import Any, Callable, Protocol, runtime_checkable

from .domain import BrandProfile, MachineCapabilities, MachineStatus


@runtime_checkable
class CoffeeMachineClient(Protocol):
    """High-level, transport-agnostic interface to one coffee machine."""

    # --- Identity / state (properties) ---
    address: str
    connected: bool
    status: MachineStatus | None
    firmware_version: str | None
    serial_number: str | None
    model_name: str
    brand: BrandProfile
    capabilities: MachineCapabilities | None
    dis_info: dict[str, str]
    total_cups: int | None
    cup_counters: dict[str, int]
    my_coffee_slots: list[dict[str, int]] | None
    profile_names: dict[int, str]

    # --- Lifecycle ---
    async def connect(self) -> bool: ...
    async def disconnect(self) -> None: ...
    async def poll_status(self) -> MachineStatus | None: ...

    # --- Callbacks (subscribe to state changes) ---
    def add_status_callback(self, callback: Callable[[MachineStatus], None]) -> None: ...
    def remove_status_callback(self, callback: Callable[[MachineStatus], None]) -> None: ...
    def add_connection_callback(self, callback: Callable[[bool], None]) -> None: ...
    def remove_connection_callback(self, callback: Callable[[bool], None]) -> None: ...

    # --- Brewing ---
    async def cancel_process(self, *args: Any, **kwargs: Any) -> bool: ...

    # --- Settings ---
    async def read_setting(self, setting_id: int) -> int | None: ...
    async def write_setting(self, setting_id: int, value: int) -> bool: ...

    # --- Maintenance ---
    async def start_easy_clean(self) -> bool: ...
    async def start_intensive_clean(self) -> bool: ...
    async def start_descaling(self) -> bool: ...
    async def confirm_prompt(self) -> bool: ...
