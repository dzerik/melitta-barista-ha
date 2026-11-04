"""One bounded statistics refresh stream per config entry."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import datetime, timedelta

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.event import async_track_time_interval

from .coffee_platform.contract import CoffeeMachineClient
from .coffee_platform.domain import StatDescriptor
from .protocol import MachineStatus

_LOGGER = logging.getLogger("melitta_barista")
STATS_REFRESH_INTERVAL = timedelta(minutes=5)


class BrandStatsCoordinator:
    """Read stats sequentially on connect, after activity, and every five minutes.

    Requests share one task. A completed operation during a read queues at most
    one follow-up pass. Busy/disconnected machines are left alone; failed reads
    retain previous values, while successful zeros and resets remain valid.
    """

    def __init__(self, hass: HomeAssistant, client: CoffeeMachineClient) -> None:
        self._hass = hass
        self._client = client
        self._descriptors: tuple[StatDescriptor, ...] = ()
        self.values: dict[int, int] = {}
        self._listeners: set[Callable[[], None]] = set()
        self._task: asyncio.Task | None = None
        self._pending = False
        self._closed = False
        self._initial_requested = False
        self._was_busy = client.status is not None and not client.status.is_ready
        client.add_status_callback(self._on_status)
        client.add_connection_callback(self._on_connection)
        self._cancel_timer = async_track_time_interval(
            hass, self._on_interval, STATS_REFRESH_INTERVAL,
        )

    def set_descriptors(self, descriptors: tuple[StatDescriptor, ...]) -> None:
        """Update the register table after model capabilities resolve."""
        self._descriptors = descriptors

    def subscribe(self, listener: Callable[[], None]) -> Callable[[], None]:
        """Subscribe to updated raw values and return an unsubscribe callback."""
        self._listeners.add(listener)
        return lambda: self._listeners.discard(listener)

    @property
    def _can_read(self) -> bool:
        status = self._client.status
        return self._client.connected and (status is None or status.is_ready)

    @callback
    def request_initial_refresh(self) -> None:
        """Read once for all entities, even if mocked or cached I/O finishes inline."""
        if not self._initial_requested:
            self._initial_requested = True
            self.request_refresh()

    @callback
    def _on_interval(self, now: datetime) -> None:
        self.request_refresh()

    @callback
    def request_refresh(self, *, follow_up: bool = False) -> asyncio.Task | None:
        """Start or join a pass; optionally request fresh values after this pass."""
        if self._closed or not self._descriptors or not self._can_read:
            return None
        if self._task is not None and not self._task.done():
            self._pending |= follow_up
            return self._task
        self._task = self._hass.async_create_task(self._refresh())
        return self._task

    @callback
    def _on_status(self, status: MachineStatus) -> None:
        was_busy = self._was_busy
        self._was_busy = not status.is_ready
        if was_busy and status.is_ready:
            self.request_refresh(follow_up=True)

    @callback
    def _on_connection(self, connected: bool) -> None:
        self._was_busy = (
            connected and self._client.status is not None
            and not self._client.status.is_ready
        )
        if connected:
            self.request_refresh()
        else:
            self._cancel_refresh()

    async def _refresh(self) -> None:
        try:
            while self._can_read:
                self._pending = False
                for descriptor in self._descriptors:
                    if not self._can_read:
                        break
                    try:
                        value = await self._client.read_setting(descriptor.stat_id)
                    except Exception:  # noqa: BLE001
                        _LOGGER.debug("Stat %s read failed", descriptor.key, exc_info=True)
                        continue
                    if value is not None:
                        self.values[descriptor.stat_id] = value
                for listener in tuple(self._listeners):
                    listener()
                if not self._pending:
                    break
        finally:
            if self._task is asyncio.current_task():
                self._task = None

    def _cancel_refresh(self) -> None:
        self._pending = False
        if self._task is not None:
            self._task.cancel()
            self._task = None

    @callback
    def close(self) -> None:
        """Remove entry listeners and cancel outstanding reads on unload."""
        if self._closed:
            return
        self._closed = True
        self._cancel_timer()
        self._client.remove_status_callback(self._on_status)
        self._client.remove_connection_callback(self._on_connection)
        self._cancel_refresh()
        self._listeners.clear()
