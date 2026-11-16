"""A start is never retransmitted and may be confirmed by fresh PRODUCT HX."""

import asyncio
import struct
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.melitta_barista.ble_client import MelittaBleClient
from custom_components.melitta_barista.brands import get_profile
from custom_components.melitta_barista.coffee_platform.domain import MachineProcess, MachineStatus
from custom_components.melitta_barista.lifecycle import LifecycleDetector
from custom_components.melitta_barista.protocol import EugsterProtocol
from tests import MOCK_ADDRESS


def _client():
    client = MelittaBleClient(MOCK_ADDRESS, brand=get_profile("nivona"))
    client._capabilities = client.brand.capabilities_for_model("795000000000000")
    client._connected = True
    client._client = MagicMock(is_connected=True)
    client._status = MachineStatus(process=MachineProcess.READY)
    client._protocol.start_process_nivona = AsyncMock(return_value=False)
    client.start_polling = MagicMock()
    client._stop_polling = MagicMock()
    client.poll_status = AsyncMock(return_value=None)
    return client


@pytest.fixture(autouse=True)
def short_confirmation(monkeypatch):
    monkeypatch.setattr(
        "custom_components.melitta_barista._ble_commands.NIVONA_START_CONFIRM_TIMEOUT",
        0.02, raising=False,
    )


@pytest.mark.parametrize("path", ["recipe", "mycoffee"])
async def test_product_after_nack_confirms_one_start_and_preserves_ha_attribution(path):
    client = _client()
    detector = LifecycleDetector()
    detector.feed(client.status)
    events = []

    def observe(status):
        events.extend(detector.feed(status, intent=client.brew_intent))
        if detector.consumed_intent():
            client.take_brew_intent()

    client.add_status_callback(observe)

    async def poll():
        client._on_status(MachineStatus(process=MachineProcess.PRODUCT))
        return client.status

    client.poll_status.side_effect = poll
    result = await (client.brew_nivona(0) if path == "recipe" else client.brew_mycoffee_slot(0))
    assert result is True
    client._protocol.start_process_nivona.assert_awaited_once()
    assert events[0].type == "brew_started"
    assert events[0].payload["source"] == "ha"
    assert events[0].payload["recipe_source"] == ("nivona" if path == "recipe" else "mycoffee")
    assert client.brew_intent is None  # consumed once, never re-staged after HX


async def test_product_can_arrive_before_ack():
    client = _client()
    staged = []

    async def start(*args, **kwargs):
        staged.append(client.brew_intent)
        client._on_status(MachineStatus(process=MachineProcess.PRODUCT))
        client.take_brew_intent()
        return True

    client._protocol.start_process_nivona.side_effect = start
    assert await client.brew_nivona(0)
    assert staged[0] is not None
    assert staged[0].recipe_source == "nivona"
    assert client.brew_intent is None
    client.poll_status.assert_not_awaited()


@pytest.mark.parametrize("process", [MachineProcess.READY, None])
async def test_nack_without_fresh_product_is_failure_and_drops_pending_intent(process):
    client = _client()

    async def poll():
        client._on_status(MachineStatus(process=process))
        return client.status

    client.poll_status.side_effect = poll
    assert not await client.brew_nivona(0)
    assert client.brew_intent is None
    client._protocol.start_process_nivona.assert_awaited_once()
    assert client._status_callbacks == []
    assert not client._brew_lock.locked()


async def test_disconnect_or_replacement_cannot_confirm_start():
    client = _client()

    async def poll():
        client._client = MagicMock(is_connected=True)
        client._on_status(MachineStatus(process=MachineProcess.PRODUCT))
        return client.status

    client.poll_status.side_effect = poll
    assert not await client.brew_nivona(0)
    assert client.brew_intent is None


async def test_cancellation_cleans_pending_intent_and_subscription():
    client = _client()

    async def start(*args, **kwargs):
        raise asyncio.CancelledError

    client._protocol.start_process_nivona.side_effect = start
    with pytest.raises(asyncio.CancelledError):
        await client.brew_nivona(0)
    assert client.brew_intent is None
    assert client._status_callbacks == []


async def test_protocol_does_not_retry_a_start_when_ack_is_lost():
    protocol = EugsterProtocol(frame_timeout=0.001)
    write = AsyncMock()
    assert not await protocol.start_process_nivona(write, 0, use_temp_recipe=False)
    assert write.await_count == len(protocol.chunk_for_ble(protocol.build_frame("HE", bytes.fromhex(
        "000b00000000000000000000000000000000",
    ))))


async def test_real_nack_and_hx_dispatch_confirm_saved_recipe_without_resending():
    """Exercise real serialization, ACK future and family795 HX decoding."""
    client = _client()
    client._protocol = EugsterProtocol(brand=client.brand, frame_timeout=0.001)
    caps = client.capabilities
    client._protocol.set_family(caps.family_key, status_profile_key=caps.status_profile_key)
    client._protocol.set_status_callback(client._on_status)
    client.poll_status = MelittaBleClient.poll_status.__get__(client)
    packets = []

    async def write(_characteristic, packet, *, response):
        packets.append(packet)
        if packet.startswith(b"SHE"):
            client._protocol._dispatch_frame("N", b"")
        elif packet.startswith(b"SHX"):
            client._protocol._dispatch_frame("HX", struct.pack(">hhhh", 11, 0, 0, 0))

    client._client.write_gatt_char = write
    assert await client.brew_nivona(0)
    assert sum(packet.startswith(b"SHE") for packet in packets) == 1
    assert sum(packet.startswith(b"SHX") for packet in packets) == 1
    assert client.status.process == MachineProcess.PRODUCT
    assert client.brew_intent.recipe_source == "nivona"


@pytest.mark.parametrize("failed_write", [0, 1])
async def test_failed_temporary_write_never_starts_partial_recipe(failed_write):
    client = _client()
    client._protocol.write_numerical = AsyncMock(side_effect=[False, True] if failed_write == 0 else [True, False])
    assert not await client.brew_nivona(0, {"strength": 2})
    client._protocol.start_process_nivona.assert_not_awaited()
