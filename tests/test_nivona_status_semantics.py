"""Family-specific HX decoding and guarded HY transmission regressions."""

import asyncio
import struct
from unittest.mock import MagicMock

import pytest

from custom_components.melitta_barista.ble_client import MelittaBleClient
from custom_components.melitta_barista.brands import get_profile
from custom_components.melitta_barista.button import MelittaConfirmPromptButton
from custom_components.melitta_barista.binary_sensor import MelittaAwaitingConfirmationBinarySensor
from custom_components.melitta_barista.const import Manipulation, SubProcess
from custom_components.melitta_barista.ui_contract import build_status_tokens
from tests import MOCK_ADDRESS


def _client(model, *, auto=False, brand="nivona", ack=True):
    client = MelittaBleClient(MOCK_ADDRESS, brand=get_profile(brand), auto_confirm_prompts=auto)
    if brand == "nivona":
        client._capabilities = client.brand.capabilities_for_model(f"{model}000000000000")
        caps = client._capabilities
        client._protocol.set_family(caps.family_key, status_profile_key=caps.status_profile_key)
    client._connected = True
    client._client = MagicMock(is_connected=True)
    client._protocol.set_status_callback(client._on_status)
    packets = []

    async def write(characteristic, packet, *, response):
        packets.append(packet)
        if ack:
            client._protocol._dispatch_frame("A", b"")

    client._client.write_gatt_char = write
    return client, packets


def _hx(client, process=8, message=0, sub=0, progress=0):
    client._protocol._dispatch_frame("HX", struct.pack(">hhhh", process, sub, message, progress))
    return client.status


@pytest.mark.parametrize("model,process,message,semantic", [
    ("970", 8, 11, "recipe_not_valid"),
    ("795", 8, 20, "wait"),
    ("795", 8, 40, "process_aborted"),
    ("8103", 3, 254, "process_aborted"),
    ("8107", 3, 255, "recipe_not_valid"),
    ("756", 8, 11, "other"),
    ("756", 8, 16, "wait"),
    ("030", 8, 20, "unknown"),
    ("970", 8, 256, "unknown"),
    ("970", 8, -1, "unknown"),
    ("660", 8, 11, "unknown"),
    ("930", 8, 20, "unknown"),
])
async def test_messages_block_brew_and_all_confirmation_paths(model, process, message, semantic):
    """Errors, waits and unrecognized messages must never become soft prompts."""
    client, packets = _client(model, auto=True)
    status = _hx(client, process, message)
    assert not status.is_ready
    assert not status.is_ready_for_brew((11, 20))
    assert not client._is_brew_ready()
    assert not await client.brew_nivona(0)
    assert not await client.confirm_prompt()
    await asyncio.sleep(0)
    assert packets == []
    assert status.raw_process == process
    assert status.raw_message == message
    assert status.message_key == semantic
    assert status.manipulation == Manipulation.NONE
    assert not build_status_tokens(status, True)["awaiting_confirmation"]


@pytest.mark.parametrize("model,message,manip", [
    ("756", 7, Manipulation.FILL_POWDER),
    ("795", 5, Manipulation.FILL_POWDER),
    ("970", 5, Manipulation.CLOSE_POWDER_LID),
    ("030", 5, Manipulation.CLOSE_POWDER_LID),
    ("8103", 5, Manipulation.FILL_POWDER),
    ("756", 1, Manipulation.BU_REMOVED),
    ("970", 2, Manipulation.TRAYS_MISSING),
    ("795", 3, Manipulation.EMPTY_TRAYS),
    ("030", 4, Manipulation.FILL_WATER),
])
async def test_physical_interventions_are_mapped_without_hy(model, message, manip):
    """A physical intervention remains visible but cannot be acknowledged remotely."""
    client, packets = _client(model, auto=True)
    status = _hx(client, 3 if model.startswith("81") else 8, message)
    assert status.manipulation == manip
    assert not status.is_ready_for_brew(tuple(range(256)))
    assert not MelittaConfirmPromptButton(client, MagicMock(), "Nivona").available
    sensor = MelittaAwaitingConfirmationBinarySensor(client, MagicMock(), "Nivona")
    assert not sensor.is_on
    assert not build_status_tokens(status, True)["awaiting_confirmation"]
    assert not await client.confirm_prompt()
    await asyncio.sleep(0)
    assert packets == []


@pytest.mark.parametrize("model,sub,want", [
    ("8103", 4, SubProcess.STEAM), ("8107", 8, SubProcess.WATER),
    ("8103", 3, None), ("795", 1, None),
    ("970", 3, SubProcess.STEAM), ("756", 4, SubProcess.WATER),
])
def test_subprocess_codes_follow_family(model, sub, want):
    client, _ = _client(model)
    status = _hx(client, 4 if model.startswith("81") else 11, sub=sub)
    assert status.sub_process == want
    assert status.raw_sub_process == sub


@pytest.mark.parametrize("model", ["756", "970"])
@pytest.mark.parametrize("auto", [False, True])
async def test_recognized_rinse_prompt_can_send_exact_hy(model, auto):
    """Only an eligible prompt gets HY, once while the same prompt persists."""
    client, packets = _client(model, auto=auto)
    status = _hx(client, message=20)
    assert status.manipulation == Manipulation.FLUSH_REQUIRED
    assert not status.is_ready_for_brew((20,))
    assert MelittaConfirmPromptButton(client, MagicMock(), "Nivona").available
    assert build_status_tokens(status, True)["awaiting_confirmation"]
    if auto:
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        _hx(client, message=20)
        await asyncio.sleep(0)
    else:
        assert packets == []
        assert await client.confirm_prompt()
    assert packets == [client._protocol.build_frame("HY", b"\0\0\0\0")]


async def test_no_hy_before_first_nivona_status_or_without_capabilities():
    client, packets = _client("970")
    assert not await client.confirm_prompt()
    _hx(client, message=20)
    client._capabilities = None
    assert not await client.confirm_prompt()
    assert packets == []


@pytest.mark.parametrize("auto", [False, True])
@pytest.mark.parametrize("reappear", [False, True])
async def test_queued_hy_does_not_confirm_changed_or_reappeared_prompt(auto, reappear):
    """A queued confirmation belongs to one appearance, even if the code repeats."""
    client, packets = _client("970", auto=auto)
    await client._protocol._lock.acquire()
    _hx(client, message=20)
    task = None if auto else asyncio.create_task(client.confirm_prompt())
    await asyncio.sleep(0)
    _hx(client, message=0)
    if reappear:
        # Do not schedule a new auto action: test only the old queued action.
        client.set_auto_confirm_prompts(False)
        _hx(client, message=20)
    client._protocol._lock.release()
    if task:
        assert not await task
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert packets == []


async def test_repeated_identical_hx_does_not_cancel_queued_confirmation():
    client, packets = _client("970")
    await client._protocol._lock.acquire()
    _hx(client, message=20)
    task = asyncio.create_task(client.confirm_prompt())
    await asyncio.sleep(0)
    _hx(client, message=20, progress=1)
    client._protocol._lock.release()
    assert await task
    assert len(packets) == 1


async def test_retry_rechecks_prompt_after_first_unacknowledged_hy():
    client, packets = _client("970", ack=False)
    client._protocol._frame_timeout = 0.001
    _hx(client, message=20)
    first_write = asyncio.Event()
    original = client._write_ble

    async def write(packet, **kwargs):
        await original(packet, **kwargs)
        _hx(client, message=0)
        first_write.set()

    client._write_ble = write
    task = asyncio.create_task(client.confirm_prompt())
    await first_write.wait()
    assert not await task
    assert len(packets) == 1
    assert client._protocol._ack_future is None


async def test_melitta_confirmation_and_decoding_remain_compatible():
    client, packets = _client(None, brand="melitta")
    assert await client.confirm_prompt()
    status = _hx(client, process=2, message=11, sub=4)
    assert status.manipulation == Manipulation.MOVE_CUP_TO_FROTHER
    assert status.sub_process == SubProcess.WATER
    assert await client.confirm_prompt()
    assert len(packets) == 2


async def test_9101_does_not_inherit_810x_message_or_subprocess_map():
    client, packets = _client("9101", auto=True)
    status = _hx(client, 3, message=5, sub=4)
    assert status.manipulation == Manipulation.NONE
    assert status.sub_process is None
    assert not status.is_ready
    assert not await client.confirm_prompt()
    assert packets == []


async def test_hy_requires_status_from_current_connection():
    client, packets = _client("970")
    _hx(client, message=20)
    writer = client._client.write_gatt_char
    client._client = MagicMock(is_connected=True, write_gatt_char=writer)
    assert not await client.confirm_prompt()
    assert packets == []
    _hx(client, message=20)
    assert await client.confirm_prompt()


async def test_disabling_auto_confirm_cancels_queued_automatic_hy():
    client, packets = _client("970", auto=True)
    await client._protocol._lock.acquire()
    _hx(client, message=20)
    await asyncio.sleep(0)
    client.set_auto_confirm_prompts(False)
    client._protocol._lock.release()
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert packets == []


async def test_progress_and_process_change_do_not_repeat_same_active_prompt():
    client, packets = _client("970", auto=True)
    _hx(client, message=20)
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    _hx(client, process=11, message=20, progress=10)
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert len(packets) == 1


async def test_cleared_prompt_does_not_pass_the_final_ble_write_lock():
    client, packets = _client("970")
    await client._write_lock.acquire()
    _hx(client, message=20)
    task = asyncio.create_task(client.confirm_prompt())
    await asyncio.sleep(0)
    assert client._protocol._ack_future is not None
    _hx(client, message=0)
    client._write_lock.release()
    assert not await task
    assert packets == []
    assert client._protocol._ack_future is None


async def test_off_on_does_not_revive_the_previous_automatic_request():
    client, packets = _client("970", auto=True)
    await client._protocol._lock.acquire()
    _hx(client, message=20)
    await asyncio.sleep(0)
    client.set_auto_confirm_prompts(False)
    client.set_auto_confirm_prompts(True)
    _hx(client, message=20)
    client._protocol._lock.release()
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert len(packets) == 1


@pytest.mark.parametrize("model,ready", [
    ("756", 8), ("795", 8), ("970", 8), ("030", 8),
    ("040", 8), ("8101", 3), ("8107", 3), ("660", 8), ("930", 8),
])
def test_zero_message_retains_basic_ready_decoding(model, ready):
    client, _ = _client(model)
    status = _hx(client, ready)
    assert status.is_ready
    assert client._is_brew_ready()


async def test_unknown_process_and_short_hx_cannot_be_confirmed():
    client, packets = _client("970", auto=True)
    status = _hx(client, process=123, message=20)
    assert not status.is_ready
    assert not await client.confirm_prompt()
    client._protocol._dispatch_frame("HX", b"\0\10")
    assert not client.status.is_ready
    assert not await client.confirm_prompt()
    await asyncio.sleep(0)
    assert packets == []


@pytest.mark.parametrize("family", [None, "unrecognized"])
def test_unknown_family_cannot_infer_ready_from_a_numeric_coincidence(family):
    status = get_profile("nivona").parse_status(family, struct.pack(">hhhh", 8, 0, 0, 0))
    assert not status.is_ready
    assert status.process is None
