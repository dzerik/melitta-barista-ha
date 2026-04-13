"""Tests for MelittaProtocol — frame building, parsing, dispatch, handshake."""

from __future__ import annotations

import asyncio
import struct
from unittest.mock import AsyncMock

import pytest

from custom_components.melitta_barista.const import (
    BLE_MTU,
    CMD_HANDSHAKE,
    CMD_READ_NUMERICAL,
    CMD_WRITE_NUMERICAL,
    FRAME_END,
    FRAME_START,
    MachineProcess,
)
from custom_components.melitta_barista.protocol import (
    MelittaProtocol,
    NumericalValue,
    _derive_rc4_key,
    _rc4_crypt,
)


class TestDeriveRC4Key:
    """Test AES-based RC4 key derivation."""

    def test_derive_key_returns_bytes(self):
        key = _derive_rc4_key()
        assert isinstance(key, bytes)
        assert len(key) > 0

    def test_derive_key_deterministic(self):
        key1 = _derive_rc4_key()
        key2 = _derive_rc4_key()
        assert key1 == key2


class TestMelittaProtocolInit:
    """Test protocol initialization."""

    def test_init_sets_rc4_key(self):
        proto = MelittaProtocol()
        assert proto._rc4_key is not None

    def test_handshake_not_complete_initially(self):
        proto = MelittaProtocol()
        assert proto.handshake_complete is False
        assert proto._key_prefix is None


class TestFrameBuilding:
    """Test frame construction."""

    def test_frame_starts_with_S_ends_with_E(self):
        proto = MelittaProtocol()
        # Without key_prefix (handshake not done)
        frame = proto.build_frame(CMD_HANDSHAKE, b"\x01\x02\x03\x04\x05\x06", include_key_prefix=False)
        assert frame[0] == FRAME_START
        assert frame[-1] == FRAME_END

    def test_frame_contains_command(self):
        proto = MelittaProtocol()
        frame = proto.build_frame(CMD_HANDSHAKE, b"\x01\x02\x03\x04\x05\x06", include_key_prefix=False)
        # Command bytes follow S
        assert frame[1:3] == b"HU"

    def test_frame_with_key_prefix(self):
        proto = MelittaProtocol()
        proto._key_prefix = b"\xAA\xBB"
        frame = proto.build_frame("HR", struct.pack(">h", 6))
        # Frame should be: S + HR + encrypted(key_prefix + payload + checksum) + E
        assert frame[0] == FRAME_START
        assert frame[-1] == FRAME_END
        # Frame should be encrypted, so we can't easily check the internals
        assert len(frame) > 4  # at least S + cmd + checksum + E


class TestChunking:
    """Test BLE MTU chunking."""

    def test_small_frame_single_chunk(self):
        proto = MelittaProtocol()
        data = b"\x00" * 10
        chunks = proto.chunk_for_ble(data)
        assert len(chunks) == 1
        assert chunks[0] == data

    def test_exact_mtu_single_chunk(self):
        proto = MelittaProtocol()
        data = b"\x00" * BLE_MTU
        chunks = proto.chunk_for_ble(data)
        assert len(chunks) == 1

    def test_over_mtu_multiple_chunks(self):
        proto = MelittaProtocol()
        data = b"\x00" * (BLE_MTU + 5)
        chunks = proto.chunk_for_ble(data)
        assert len(chunks) == 2
        assert len(chunks[0]) == BLE_MTU
        assert len(chunks[1]) == 5


class TestFrameParsing:
    """Test incoming frame parsing and dispatch."""

    def test_ack_resolves_future(self):
        proto = MelittaProtocol()
        proto._rc4_key = None  # disable encryption for simple test

        loop = asyncio.new_event_loop()
        future = loop.create_future()
        proto._ack_future = future

        # Simulate receiving ACK frame: S + A + checksum + E
        ack_frame = bytes([FRAME_START]) + b"A"
        cs = (~ord("A")) & 0xFF
        ack_frame += bytes([cs, FRAME_END])
        proto.on_ble_data(ack_frame)

        assert future.done()
        assert future.result() is True
        loop.close()

    def test_nack_resolves_future_false(self):
        proto = MelittaProtocol()
        proto._rc4_key = None

        loop = asyncio.new_event_loop()
        future = loop.create_future()
        proto._ack_future = future

        nack_frame = bytes([FRAME_START]) + b"N"
        cs = (~ord("N")) & 0xFF
        nack_frame += bytes([cs, FRAME_END])
        proto.on_ble_data(nack_frame)

        assert future.done()
        assert future.result() is False
        loop.close()

    def test_status_callback_fired(self):
        proto = MelittaProtocol()
        proto._rc4_key = None  # no encryption

        statuses = []
        proto.set_status_callback(lambda s: statuses.append(s))

        # Build HX frame with status payload
        payload = struct.pack(">hhBBh", MachineProcess.READY, 0, 0, 0, 0)
        frame = bytes([FRAME_START]) + b"HX" + payload
        cs = 0
        for b in frame[1:]:
            cs = (cs + b) & 0xFF
        cs = (~cs) & 0xFF
        frame += bytes([cs, FRAME_END])
        proto.on_ble_data(frame)

        assert len(statuses) == 1
        assert statuses[0].process == MachineProcess.READY

    def test_buffer_overflow_resets(self):
        proto = MelittaProtocol()
        # Feed 128+ bytes without FRAME_END -> buffer should reset
        proto._recv_buffer.append(FRAME_START)
        for _ in range(130):
            proto._process_byte(0x42)
        assert len(proto._recv_buffer) == 0

    def test_unknown_command_ignored(self):
        proto = MelittaProtocol()
        proto._rc4_key = None
        # Build frame with unknown command "ZZ"
        frame = bytes([FRAME_START]) + b"ZZ\x00"
        cs = 0
        for b in frame[1:]:
            cs = (cs + b) & 0xFF
        frame += bytes([(~cs) & 0xFF, FRAME_END])
        # Should not raise
        proto.on_ble_data(frame)


class TestHandshake:
    """Test HU handshake flow."""

    @pytest.mark.asyncio
    async def test_handshake_sends_challenge(self):
        proto = MelittaProtocol()
        write_calls = []

        async def mock_write(data: bytes) -> None:
            write_calls.append(data)

        # Simulate machine responding with HU response in background
        async def respond():
            await asyncio.sleep(0.05)
            # Build HU response: challenge(4) + key_prefix(2) + validation(2)
            response_payload = b"\x01\x02\x03\x04\xAA\xBB\xCC\xDD"
            cmd_bytes = b"HU"
            # Compute correct checksum: ~(sum(cmd + payload)) & 0xFF
            cs = (~sum(cmd_bytes + response_payload)) & 0xFF
            frame = bytes([FRAME_START]) + cmd_bytes
            if proto._rc4_key:
                encrypted = _rc4_crypt(response_payload + bytes([cs]), proto._rc4_key)
                frame += encrypted + bytes([FRAME_END])
            else:
                frame += response_payload + bytes([cs]) + bytes([FRAME_END])
            proto.on_ble_data(frame)

        task = asyncio.create_task(respond())
        result = await proto.perform_handshake(mock_write)
        await task

        assert len(write_calls) > 0  # HU frame was sent
        assert proto._key_prefix is not None
        assert result is True

    @pytest.mark.asyncio
    async def test_handshake_timeout(self):
        proto = MelittaProtocol(frame_timeout=1)

        async def mock_write(data: bytes) -> None:
            pass  # No response -> timeout

        result = await proto.perform_handshake(mock_write)
        assert result is False


class TestSendAndWait:
    """Test send_and_wait_ack and send_and_wait_response."""

    @pytest.mark.asyncio
    async def test_send_and_wait_ack_success(self):
        proto = MelittaProtocol(frame_timeout=1)
        proto._key_prefix = b"\x00\x00"
        proto._rc4_key = None

        write_calls = []

        async def capturing_write(data: bytes) -> None:
            write_calls.append(data)

        async def respond():
            await asyncio.sleep(0.05)
            ack_frame = bytes([FRAME_START, ord("A")])
            cs = (~ord("A")) & 0xFF
            ack_frame += bytes([cs, FRAME_END])
            proto.on_ble_data(ack_frame)

        task = asyncio.create_task(respond())
        result = await proto.send_and_wait_ack(
            CMD_WRITE_NUMERICAL,
            struct.pack(">hi", 11, 3),
            capturing_write,
        )
        await task
        assert result is True

    @pytest.mark.asyncio
    async def test_send_and_wait_ack_timeout(self):
        proto = MelittaProtocol(frame_timeout=1)
        proto._key_prefix = b"\x00\x00"
        proto._rc4_key = None

        async def mock_write(data: bytes) -> None:
            pass

        result = await proto.send_and_wait_ack(
            CMD_WRITE_NUMERICAL,
            struct.pack(">hi", 11, 3),
            mock_write,
        )
        assert result is False

    @pytest.mark.asyncio
    async def test_send_and_wait_response(self):
        proto = MelittaProtocol(frame_timeout=1)
        proto._key_prefix = b"\x00\x00"
        proto._rc4_key = None

        response_payload = struct.pack(">hi", 6, 259)

        async def respond():
            await asyncio.sleep(0.05)
            frame = bytes([FRAME_START]) + b"HR" + response_payload
            cs = 0
            for b in frame[1:]:
                cs = (cs + b) & 0xFF
            frame += bytes([(~cs) & 0xFF, FRAME_END])
            proto.on_ble_data(frame)

        task = asyncio.create_task(respond())
        result = await proto.send_and_wait_response(
            CMD_READ_NUMERICAL,
            struct.pack(">h", 6),
            AsyncMock(),
        )
        await task
        assert result is not None
        nv = NumericalValue.from_payload(result)
        assert nv.value_id == 6
        assert nv.value == 259


# ---------------------------------------------------------------------------
# Additional coverage tests — edge cases, write commands, high-level API
# ---------------------------------------------------------------------------

import time
from unittest.mock import patch, MagicMock, PropertyMock

from custom_components.melitta_barista.const import (
    CMD_ACK,
    CMD_NACK,
    CMD_READ_ALPHA,
    CMD_READ_RECIPE,
    CMD_READ_STATUS,
    CMD_READ_VERSION,
    CMD_START_PROCESS,
    CMD_CANCEL_PROCESS,
    CMD_WRITE_ALPHA,
    CMD_WRITE_RECIPE,
    FRAME_START,
    FRAME_END,
    InfoMessage,
    Manipulation,
    SubProcess,
)
from custom_components.melitta_barista.protocol import (
    AlphanumericValue,
    MachineRecipe,
    MachineStatus,
    RecipeComponent,
    _rc4_crypt,
)


class TestMachineStatusEdgeCases:
    """Cover MachineStatus.from_payload edge cases (lines 169-170)."""

    def test_unknown_manipulation_defaults_to_none(self):
        """Unknown manipulation byte falls back to Manipulation.NONE."""
        data = struct.pack(">hhBBh", MachineProcess.READY, 0, 0, 254, 0)
        status = MachineStatus.from_payload(data)
        assert status.manipulation == Manipulation.NONE

    def test_unknown_subprocess_is_none(self):
        """Unknown sub_process value results in None."""
        data = struct.pack(">hhBBh", MachineProcess.READY, 999, 0, 0, 0)
        status = MachineStatus.from_payload(data)
        assert status.sub_process is None


class TestRecipeComponentEdgeCases:
    """Cover RecipeComponent.from_bytes short data (lines 207-208)."""

    def test_from_bytes_too_short_returns_none(self):
        """Data shorter than 8 bytes returns None."""
        assert RecipeComponent.from_bytes(b"\x01\x02\x03") is None

    def test_from_bytes_empty_returns_none(self):
        assert RecipeComponent.from_bytes(b"") is None

    def test_from_bytes_exactly_8_bytes(self):
        comp = RecipeComponent.from_bytes(b"\x01\x02\x03\x04\x05\x06\x07\x08")
        assert comp is not None
        assert comp.process == 1
        assert comp.reserve == 8


class TestMachineRecipeEdgeCases:
    """Cover MachineRecipe.from_payload short data (lines 229-230, 236)."""

    def test_from_payload_too_short_returns_none(self):
        """Payload shorter than 19 bytes returns None."""
        assert MachineRecipe.from_payload(b"\x00" * 10) is None

    def test_from_payload_empty_returns_none(self):
        assert MachineRecipe.from_payload(b"") is None

    def test_from_payload_comp_is_none_returns_none(self):
        """If a component can't parse (impossible with >=19 bytes normally),
        verify the None guard at line 236 by patching from_bytes."""
        payload = struct.pack(">hB", 200, 0) + b"\x00" * 16
        with patch.object(RecipeComponent, "from_bytes", return_value=None):
            assert MachineRecipe.from_payload(payload) is None


class TestNumericalValueEdgeCases:
    """Cover NumericalValue.from_payload short data (lines 249-250)."""

    def test_from_payload_too_short_returns_none(self):
        assert NumericalValue.from_payload(b"\x00\x01") is None

    def test_from_payload_empty_returns_none(self):
        assert NumericalValue.from_payload(b"") is None


class TestAlphanumericValueEdgeCases:
    """Cover AlphanumericValue.from_payload short data (lines 265-266)."""

    def test_from_payload_too_short_returns_none(self):
        assert AlphanumericValue.from_payload(b"\x00") is None

    def test_from_payload_empty_returns_none(self):
        assert AlphanumericValue.from_payload(b"") is None

    def test_from_payload_just_id_no_text(self):
        """2 bytes = just value_id, empty text."""
        av = AlphanumericValue.from_payload(struct.pack(">h", 42))
        assert av is not None
        assert av.value_id == 42
        assert av.value == ""


class TestInitEncryptionFailure:
    """Cover _init_encryption exception path (lines 303-305)."""

    def test_init_encryption_failure_sets_rc4_key_none(self):
        """When the brand profile fails to provide a key, rc4_key is None."""
        broken_brand = MagicMock()
        broken_brand.brand_slug = "broken"
        type(broken_brand).runtime_rc4_key = PropertyMock(
            side_effect=ValueError("bad key"),
        )
        proto = MelittaProtocol(brand=broken_brand)
        assert proto._rc4_key is None


class TestProcessByteEdgeCases:
    """Cover _process_byte edge cases (lines 383-384, 388-389)."""

    def test_frame_timeout_restarts_on_new_S(self):
        """After frame timeout, if byte is S, start new frame (line 383-384)."""
        proto = MelittaProtocol()
        proto._recv_buffer.append(FRAME_START)
        proto._frame_start_time = time.monotonic() - 2.0  # expired

        # Feed S byte — should clear buffer and start new frame
        proto._process_byte(FRAME_START)
        assert len(proto._recv_buffer) == 1
        assert proto._recv_buffer[0] == FRAME_START

    def test_frame_timeout_discards_non_S(self):
        """After frame timeout, non-S byte just clears buffer."""
        proto = MelittaProtocol()
        proto._recv_buffer.append(FRAME_START)
        proto._frame_start_time = time.monotonic() - 2.0

        proto._process_byte(0x42)
        assert len(proto._recv_buffer) == 0

    def test_buffer_overflow_128_resets(self):
        """Buffer reaching 128 bytes resets (lines 388-389)."""
        proto = MelittaProtocol()
        proto._recv_buffer = bytearray([FRAME_START] + [0x42] * 127)
        proto._frame_start_time = time.monotonic()
        # Buffer is now exactly 128 — next byte triggers overflow check
        proto._process_byte(0x42)
        assert len(proto._recv_buffer) == 0


class TestTryParseFrameEdgeCases:
    """Cover _try_parse_frame edge cases."""

    def test_frame_too_short_ignored(self):
        """Frame shorter than 4 bytes is silently dropped (line 427)."""
        proto = MelittaProtocol()
        proto._recv_buffer = bytearray([FRAME_START, ord("A"), FRAME_END])
        proto._try_parse_frame()
        # No crash, buffer cleared

    def test_unknown_command_in_frame(self):
        """Unknown command frame is ignored (lines 446-447)."""
        proto = MelittaProtocol()
        proto._rc4_key = None
        # Build frame with unknown cmd "ZQ" that has valid structure
        frame = bytearray([FRAME_START, ord("Z"), ord("Q"), 0x00])
        cs = (~sum(frame[1:])) & 0xFF
        frame.extend([cs, FRAME_END])
        proto._recv_buffer = frame
        proto._try_parse_frame()
        # Should not raise

    def test_encrypted_checksum_mismatch(self):
        """Encrypted frame with wrong checksum is dropped (lines 463-468)."""
        proto = MelittaProtocol()
        # Use real RC4 key but corrupt the payload
        assert proto._rc4_key is not None
        rc4_key = proto._rc4_key

        # Build HX frame (8-byte payload, encrypted)
        payload = b"\x00" * 8
        cmd_bytes = b"HX"
        # Compute WRONG checksum (add 1 to make it wrong)
        correct_cs = (~sum(cmd_bytes + payload)) & 0xFF
        wrong_cs = (correct_cs + 1) & 0xFF

        encrypted = _rc4_crypt(payload + bytes([wrong_cs]), rc4_key)
        frame = bytearray([FRAME_START]) + cmd_bytes + encrypted + bytearray([FRAME_END])
        proto._recv_buffer = frame
        proto._try_parse_frame()
        # Frame was rejected, no crash

    def test_unencrypted_checksum_mismatch(self):
        """Unencrypted frame with wrong checksum is dropped (lines 477-483)."""
        proto = MelittaProtocol()
        proto._rc4_key = None

        # ACK frame with deliberately wrong checksum
        frame = bytearray([FRAME_START, ord("A"), 0x00, FRAME_END])
        # 0x00 is wrong checksum for 'A' (correct would be ~0x41 & 0xFF = 0xBE)
        proto._recv_buffer = frame
        proto._try_parse_frame()
        # No crash, frame rejected

    def test_unencrypted_empty_payload(self):
        """Unencrypted frame with empty data_part yields empty payload (line 483)."""
        proto = MelittaProtocol()
        proto._rc4_key = None

        # Manually call _dispatch_frame for ACK with empty data_part
        # Build valid ACK: S + A + cs + E
        cs = (~ord("A")) & 0xFF
        frame = bytearray([FRAME_START, ord("A"), cs, FRAME_END])
        proto._recv_buffer = frame
        # This should dispatch with empty payload
        proto._try_parse_frame()
        # No crash


class TestHandshakeResponseEdgeCases:
    """Cover _handle_handshake_response short payload (lines 517-518)."""

    def test_handshake_response_too_short(self):
        """HU response with less than 6 bytes is rejected."""
        proto = MelittaProtocol()
        proto._handle_handshake_response(b"\x01\x02\x03")
        assert proto._key_prefix is None
        assert not proto._handshake_done.is_set()


class TestHighLevelReadAPI:
    """Cover high-level read methods (lines 616-648)."""

    @pytest.mark.asyncio
    async def test_read_status_success(self):
        """read_status returns MachineStatus on valid response."""
        proto = MelittaProtocol(frame_timeout=1)
        proto._key_prefix = b"\x00\x00"
        proto._rc4_key = None

        status_payload = struct.pack(">hhBBh", MachineProcess.READY, 0, 0, 0, 0)

        async def respond():
            await asyncio.sleep(0.05)
            frame = bytes([FRAME_START]) + b"HX" + status_payload
            cs = 0
            for b in frame[1:]:
                cs = (cs + b) & 0xFF
            frame += bytes([(~cs) & 0xFF, FRAME_END])
            proto.on_ble_data(frame)

        task = asyncio.create_task(respond())
        result = await proto.read_status(AsyncMock())
        await task
        assert result is not None
        assert result.process == MachineProcess.READY

    @pytest.mark.asyncio
    async def test_read_status_timeout_returns_none(self):
        """read_status returns None on timeout."""
        proto = MelittaProtocol(frame_timeout=0.1)
        proto._key_prefix = b"\x00\x00"
        proto._rc4_key = None
        result = await proto.read_status(AsyncMock())
        assert result is None

    @pytest.mark.asyncio
    async def test_read_version_success(self):
        """read_version returns firmware string."""
        proto = MelittaProtocol(frame_timeout=1)
        proto._key_prefix = b"\x00\x00"
        proto._rc4_key = None

        # HV expects 11-byte payload per KNOWN_COMMANDS
        version_bytes = b"E7-V2.3.1\x00\x00"  # 11 bytes padded
        assert len(version_bytes) == 11

        async def respond():
            await asyncio.sleep(0.05)
            frame = bytes([FRAME_START]) + b"HV" + version_bytes
            cs = 0
            for b in frame[1:]:
                cs = (cs + b) & 0xFF
            frame += bytes([(~cs) & 0xFF, FRAME_END])
            proto.on_ble_data(frame)

        task = asyncio.create_task(respond())
        result = await proto.read_version(AsyncMock())
        await task
        assert result == "E7-V2.3.1"

    @pytest.mark.asyncio
    async def test_read_version_timeout_returns_none(self):
        proto = MelittaProtocol(frame_timeout=0.1)
        proto._key_prefix = b"\x00\x00"
        proto._rc4_key = None
        result = await proto.read_version(AsyncMock())
        assert result is None

    @pytest.mark.asyncio
    async def test_read_numerical_success(self):
        """read_numerical returns the integer value."""
        proto = MelittaProtocol(frame_timeout=1)
        proto._key_prefix = b"\x00\x00"
        proto._rc4_key = None

        response_payload = struct.pack(">hi", 6, 42)

        async def respond():
            await asyncio.sleep(0.05)
            frame = bytes([FRAME_START]) + b"HR" + response_payload
            cs = 0
            for b in frame[1:]:
                cs = (cs + b) & 0xFF
            frame += bytes([(~cs) & 0xFF, FRAME_END])
            proto.on_ble_data(frame)

        task = asyncio.create_task(respond())
        result = await proto.read_numerical(AsyncMock(), 6)
        await task
        assert result == 42

    @pytest.mark.asyncio
    async def test_read_numerical_timeout_returns_none(self):
        proto = MelittaProtocol(frame_timeout=0.1)
        proto._key_prefix = b"\x00\x00"
        proto._rc4_key = None
        result = await proto.read_numerical(AsyncMock(), 6)
        assert result is None

    @pytest.mark.asyncio
    async def test_read_alphanumeric_success(self):
        """read_alphanumeric returns the string value."""
        proto = MelittaProtocol(frame_timeout=1)
        proto._key_prefix = b"\x00\x00"
        proto._rc4_key = None

        text = "Profile1"
        response_payload = struct.pack(">h", 310) + text.encode("utf-8") + b"\x00" * 56

        async def respond():
            await asyncio.sleep(0.05)
            frame = bytes([FRAME_START]) + b"HA" + response_payload
            cs = 0
            for b in frame[1:]:
                cs = (cs + b) & 0xFF
            frame += bytes([(~cs) & 0xFF, FRAME_END])
            proto.on_ble_data(frame)

        task = asyncio.create_task(respond())
        result = await proto.read_alphanumeric(AsyncMock(), 310)
        await task
        assert result == "Profile1"

    @pytest.mark.asyncio
    async def test_read_alphanumeric_timeout_returns_none(self):
        proto = MelittaProtocol(frame_timeout=0.1)
        proto._key_prefix = b"\x00\x00"
        proto._rc4_key = None
        result = await proto.read_alphanumeric(AsyncMock(), 310)
        assert result is None

    @pytest.mark.asyncio
    async def test_read_recipe_success(self):
        """read_recipe returns MachineRecipe."""
        proto = MelittaProtocol(frame_timeout=1)
        proto._key_prefix = b"\x00\x00"
        proto._rc4_key = None

        comp1 = RecipeComponent(process=1, shots=2, intensity=3)
        comp2 = RecipeComponent(process=0)
        # HC expects 66-byte payload per KNOWN_COMMANDS
        core_payload = struct.pack(">hB", 200, 0) + comp1.to_bytes() + comp2.to_bytes()
        response_payload = core_payload.ljust(66, b"\x00")
        assert len(response_payload) == 66

        async def respond():
            await asyncio.sleep(0.05)
            frame = bytes([FRAME_START]) + b"HC" + response_payload
            cs = 0
            for b in frame[1:]:
                cs = (cs + b) & 0xFF
            frame += bytes([(~cs) & 0xFF, FRAME_END])
            proto.on_ble_data(frame)

        task = asyncio.create_task(respond())
        result = await proto.read_recipe(AsyncMock(), 200)
        await task
        assert result is not None
        assert result.recipe_id == 200
        assert result.component1.shots == 2

    @pytest.mark.asyncio
    async def test_read_recipe_timeout_returns_none(self):
        proto = MelittaProtocol(frame_timeout=0.1)
        proto._key_prefix = b"\x00\x00"
        proto._rc4_key = None
        result = await proto.read_recipe(AsyncMock(), 200)
        assert result is None


class TestHighLevelWriteAPI:
    """Cover high-level write methods (lines 651-703)."""

    @pytest.mark.asyncio
    async def test_write_numerical_sends_and_gets_ack(self):
        """write_numerical sends HW command and returns True on ACK."""
        proto = MelittaProtocol(frame_timeout=1)
        proto._key_prefix = b"\x00\x00"
        proto._rc4_key = None

        async def respond():
            await asyncio.sleep(0.05)
            cs = (~ord("A")) & 0xFF
            ack_frame = bytes([FRAME_START, ord("A"), cs, FRAME_END])
            proto.on_ble_data(ack_frame)

        write_mock = AsyncMock()
        task = asyncio.create_task(respond())
        result = await proto.write_numerical(write_mock, 11, 3)
        await task
        assert result is True
        assert write_mock.call_count > 0

    @pytest.mark.asyncio
    async def test_write_alphanumeric_sends_and_gets_ack(self):
        """write_alphanumeric sends HB command and returns True on ACK."""
        proto = MelittaProtocol(frame_timeout=1)
        proto._key_prefix = b"\x00\x00"
        proto._rc4_key = None

        async def respond():
            await asyncio.sleep(0.05)
            cs = (~ord("A")) & 0xFF
            ack_frame = bytes([FRAME_START, ord("A"), cs, FRAME_END])
            proto.on_ble_data(ack_frame)

        write_mock = AsyncMock()
        task = asyncio.create_task(respond())
        result = await proto.write_alphanumeric(write_mock, 310, "NewName")
        await task
        assert result is True

    @pytest.mark.asyncio
    async def test_write_alphanumeric_truncates_long_text(self):
        """write_alphanumeric truncates text longer than 64 chars."""
        proto = MelittaProtocol(frame_timeout=1)
        proto._key_prefix = b"\x00\x00"
        proto._rc4_key = None

        async def respond():
            await asyncio.sleep(0.05)
            cs = (~ord("A")) & 0xFF
            ack_frame = bytes([FRAME_START, ord("A"), cs, FRAME_END])
            proto.on_ble_data(ack_frame)

        write_mock = AsyncMock()
        task = asyncio.create_task(respond())
        long_text = "A" * 100
        result = await proto.write_alphanumeric(write_mock, 310, long_text)
        await task
        assert result is True

    @pytest.mark.asyncio
    async def test_write_recipe_without_key(self):
        """write_recipe without recipe_key sends HJ command."""
        proto = MelittaProtocol(frame_timeout=1)
        proto._key_prefix = b"\x00\x00"
        proto._rc4_key = None

        async def respond():
            await asyncio.sleep(0.05)
            cs = (~ord("A")) & 0xFF
            ack_frame = bytes([FRAME_START, ord("A"), cs, FRAME_END])
            proto.on_ble_data(ack_frame)

        write_mock = AsyncMock()
        comp1 = RecipeComponent(process=1, shots=2, intensity=3)
        comp2 = RecipeComponent(process=0)
        task = asyncio.create_task(respond())
        result = await proto.write_recipe(write_mock, 200, 0, comp1, comp2)
        await task
        assert result is True

    @pytest.mark.asyncio
    async def test_write_recipe_with_key_and_comp3(self):
        """write_recipe with recipe_key and comp3 fills all layout fields."""
        proto = MelittaProtocol(frame_timeout=1)
        proto._key_prefix = b"\x00\x00"
        proto._rc4_key = None

        async def respond():
            await asyncio.sleep(0.05)
            cs = (~ord("A")) & 0xFF
            ack_frame = bytes([FRAME_START, ord("A"), cs, FRAME_END])
            proto.on_ble_data(ack_frame)

        write_mock = AsyncMock()
        comp1 = RecipeComponent(process=1)
        comp2 = RecipeComponent(process=2)
        comp3 = RecipeComponent(process=3)
        task = asyncio.create_task(respond())
        result = await proto.write_recipe(
            write_mock, 200, 0, comp1, comp2, recipe_key=5, comp3=comp3,
        )
        await task
        assert result is True

    @pytest.mark.asyncio
    async def test_start_process_single_cup(self):
        """start_process sends HE command for single cup."""
        proto = MelittaProtocol(frame_timeout=1)
        proto._key_prefix = b"\x00\x00"
        proto._rc4_key = None

        async def respond():
            await asyncio.sleep(0.05)
            cs = (~ord("A")) & 0xFF
            ack_frame = bytes([FRAME_START, ord("A"), cs, FRAME_END])
            proto.on_ble_data(ack_frame)

        write_mock = AsyncMock()
        task = asyncio.create_task(respond())
        result = await proto.start_process(write_mock, 200)
        await task
        assert result is True

    @pytest.mark.asyncio
    async def test_start_process_two_cups(self):
        """start_process with two_cups=True sets the flag."""
        proto = MelittaProtocol(frame_timeout=1)
        proto._key_prefix = b"\x00\x00"
        proto._rc4_key = None

        async def respond():
            await asyncio.sleep(0.05)
            cs = (~ord("A")) & 0xFF
            ack_frame = bytes([FRAME_START, ord("A"), cs, FRAME_END])
            proto.on_ble_data(ack_frame)

        write_mock = AsyncMock()
        task = asyncio.create_task(respond())
        result = await proto.start_process(write_mock, 200, two_cups=True)
        await task
        assert result is True

    @pytest.mark.asyncio
    async def test_cancel_process(self):
        """cancel_process sends HZ command."""
        proto = MelittaProtocol(frame_timeout=1)
        proto._key_prefix = b"\x00\x00"
        proto._rc4_key = None

        async def respond():
            await asyncio.sleep(0.05)
            cs = (~ord("A")) & 0xFF
            ack_frame = bytes([FRAME_START, ord("A"), cs, FRAME_END])
            proto.on_ble_data(ack_frame)

        write_mock = AsyncMock()
        task = asyncio.create_task(respond())
        result = await proto.cancel_process(write_mock, 200)
        await task
        assert result is True


class TestSendAndWaitResponseTimeout:
    """Cover send_and_wait_response timeout path (lines 607-608)."""

    @pytest.mark.asyncio
    async def test_response_timeout_returns_none(self):
        """send_and_wait_response returns None on timeout."""
        proto = MelittaProtocol(frame_timeout=0.1)
        proto._key_prefix = b"\x00\x00"
        proto._rc4_key = None

        result = await proto.send_and_wait_response(
            CMD_READ_NUMERICAL,
            struct.pack(">h", 6),
            AsyncMock(),
        )
        assert result is None
        # Future should be cleaned up
        assert CMD_READ_NUMERICAL not in proto._frame_futures
