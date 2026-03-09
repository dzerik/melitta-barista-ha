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
        proto = MelittaProtocol()
        # Patch FRAME_TIMEOUT to be very short
        import custom_components.melitta_barista.protocol as proto_mod
        original_timeout = proto_mod.FRAME_TIMEOUT

        try:
            proto_mod.FRAME_TIMEOUT = 0.1

            async def mock_write(data: bytes) -> None:
                pass  # No response -> timeout

            result = await proto.perform_handshake(mock_write)
            assert result is False
        finally:
            proto_mod.FRAME_TIMEOUT = original_timeout


class TestSendAndWait:
    """Test send_and_wait_ack and send_and_wait_response."""

    @pytest.mark.asyncio
    async def test_send_and_wait_ack_success(self):
        proto = MelittaProtocol()
        proto._key_prefix = b"\x00\x00"

        async def mock_write(data: bytes) -> None:
            # Simulate ACK response
            await asyncio.sleep(0.01)
            ack_frame = bytes([FRAME_START, ord("A")])
            cs = (~ord("A")) & 0xFF
            ack_frame += bytes([cs, FRAME_END])
            # Decrypt is symmetric, but ACK has no encrypted part
            proto._rc4_key = None  # temporarily disable for ACK
            proto.on_ble_data(ack_frame)

        # Need to handle the fact that write is called for chunks
        write_calls = []

        async def capturing_write(data: bytes) -> None:
            write_calls.append(data)

        # We need to simulate ACK arriving after send
        import custom_components.melitta_barista.protocol as proto_mod
        original_timeout = proto_mod.FRAME_TIMEOUT
        try:
            proto_mod.FRAME_TIMEOUT = 0.5
            proto._rc4_key = None  # simplify

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
        finally:
            proto_mod.FRAME_TIMEOUT = original_timeout

    @pytest.mark.asyncio
    async def test_send_and_wait_ack_timeout(self):
        proto = MelittaProtocol()
        proto._key_prefix = b"\x00\x00"
        proto._rc4_key = None

        import custom_components.melitta_barista.protocol as proto_mod
        original_timeout = proto_mod.FRAME_TIMEOUT
        try:
            proto_mod.FRAME_TIMEOUT = 0.1

            async def mock_write(data: bytes) -> None:
                pass

            result = await proto.send_and_wait_ack(
                CMD_WRITE_NUMERICAL,
                struct.pack(">hi", 11, 3),
                mock_write,
            )
            assert result is False
        finally:
            proto_mod.FRAME_TIMEOUT = original_timeout

    @pytest.mark.asyncio
    async def test_send_and_wait_response(self):
        proto = MelittaProtocol()
        proto._key_prefix = b"\x00\x00"
        proto._rc4_key = None

        import custom_components.melitta_barista.protocol as proto_mod
        original_timeout = proto_mod.FRAME_TIMEOUT
        try:
            proto_mod.FRAME_TIMEOUT = 0.5

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
        finally:
            proto_mod.FRAME_TIMEOUT = original_timeout
