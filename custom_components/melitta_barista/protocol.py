"""Melitta Barista BLE protocol implementation."""

from __future__ import annotations

import asyncio
import logging
import os
import struct
from dataclasses import dataclass, field
from typing import Callable

from Crypto.Cipher import AES

from .const import (
    AES_IV,
    AES_KEY_PART_A,
    AES_KEY_PART_B,
    BLE_MTU,
    CMD_ACK,
    CMD_CANCEL_PROCESS,
    CMD_HANDSHAKE,
    CMD_NACK,
    CMD_READ_ALPHA,
    CMD_READ_NUMERICAL,
    CMD_READ_RECIPE,
    CMD_READ_STATUS,
    CMD_READ_VERSION,
    CMD_START_PROCESS,
    CMD_WRITE_ALPHA,
    CMD_WRITE_NUMERICAL,
    CMD_WRITE_RECIPE,
    ENCRYPTED_RC4_KEY,
    FRAME_END,
    FRAME_START,
    FRAME_TIMEOUT,
    InfoMessage,
    MachineProcess,
    Manipulation,
    SubProcess,
)

_LOGGER = logging.getLogger("melitta_barista")

# CRC lookup table for handshake validation
_CRC_TABLE = [
    b & 0xFF for b in [
        98, 6, 85, -106, 36, 23, 112, -92, -121, -49, -87, 5, 26, 64, -91,
        -37, 61, 20, 68, 89, -126, 63, 52, 102, 24, -27, -124, -11, 80, -40,
        -61, 115, 90, -88, -100, -53, -79, 120, 2, -66, -68, 7, 100, -71, -82,
        -13, -94, 10, -19, 18, -3, -31, 8, -48, -84, -12, -1, 126, 101, 79,
        -111, -21, -28, 121, 123, -5, 67, -6, -95, 0, 107, 97, -15, 111, -75,
        82, -7, 33, 69, 55, 59, -103, 29, 9, -43, -89, 84, 93, 30, 46, 94,
        75, -105, 114, 73, -34, -59, 96, -46, 45, 16, -29, -8, -54, 51, -104,
        -4, 125, 81, -50, -41, -70, 39, -98, -78, -69, -125, -120, 1, 49, 50,
        17, -115, 91, 47, -127, 60, 99, -102, 35, 86, -85, 105, 34, 38, -56,
        -109, 58, 77, 118, -83, -10, 76, -2, -123, -24, -60, -112, -58, 124,
        53, 4, 108, 74, -33, -22, -122, -26, -99, -117, -67, -51, -57, -128,
        -80, 19, -45, -20, 127, -64, -25, 70, -23, 88, -110, 44, -73, -55, 22,
        83, 13, -42, 116, 109, -97, 32, 95, -30, -116, -36, 57, 12, -35, 31,
        -47, -74, -113, 92, -107, -72, -108, 62, 113, 65, 37, 27, 106, -90, 3,
        14, -52, 72, 21, 41, 56, 66, 28, -63, 40, -39, 25, 54, -77, 117, -18,
        87, -16, -101, -76, -86, -14, -44, -65, -93, 78, -38, -119, -62, -81,
        110, 43, 119, -32, 71, 122, -114, 42, -96, 104, 48, -9, 103, 15, 11,
        -118, -17,
    ]
]

# Known commands with expected response payload sizes
KNOWN_COMMANDS: dict[str, int] = {
    "A": 0, "N": 0, "HA": 66, "HB": 66, "HC": 19, "HE": 18,
    "HJ": 66, "HR": 6, "HV": 64, "HW": 6, "HX": 8, "HZ": 4, "HU": 8,
}


def _derive_rc4_key() -> bytes:
    """Derive RC4 key by decrypting the hardcoded AES blob."""
    aes_key = AES_KEY_PART_B + AES_KEY_PART_A
    cipher = AES.new(aes_key, AES.MODE_CBC, iv=AES_IV)
    decrypted = cipher.decrypt(ENCRYPTED_RC4_KEY)
    # Remove PKCS5 padding
    pad_len = decrypted[-1]
    if 1 <= pad_len <= 16 and all(b == pad_len for b in decrypted[-pad_len:]):
        decrypted = decrypted[:-pad_len]
    return decrypted


def _rc4_crypt(data: bytes, key: bytes) -> bytes:
    """RC4 encrypt/decrypt (symmetric)."""
    s = list(range(256))
    j = 0
    for i in range(256):
        j = (j + s[i] + key[i % len(key)]) % 256
        s[i], s[j] = s[j], s[i]
    out = bytearray(len(data))
    i = j = 0
    for k in range(len(data)):
        i = (i + 1) % 256
        j = (j + s[i]) % 256
        s[i], s[j] = s[j], s[i]
        out[k] = data[k] ^ s[(s[i] + s[j]) % 256]
    return bytes(out)


def _calculate_checksum(frame: bytes, length: int) -> int:
    """Calculate frame checksum: ~(sum of bytes[1..length]) & 0xFF."""
    s = 0
    for i in range(1, length):
        s = (s + frame[i]) & 0xFF
    return (~s) & 0xFF


def _compute_handshake_crc(length: int, data: bytes) -> bytes:
    """Compute 2-byte CRC for HU handshake."""
    b5 = _CRC_TABLE[(data[0] + 256) % 256]
    for i in range(1, length):
        idx = ((b5 ^ data[i]) + 256) % 256
        b5 = _CRC_TABLE[idx]
    byte1 = (b5 + 93) & 0xFF

    b7 = _CRC_TABLE[(data[0] + 257) % 256]
    for i in range(1, length):
        idx = ((b7 ^ data[i]) + 256) % 256
        b7 = _CRC_TABLE[idx]
    byte2 = (b7 + 167) & 0xFF

    return bytes([byte1, byte2])


@dataclass
class MachineStatus:
    """Machine status parsed from HX response."""
    process: MachineProcess | None = None
    sub_process: SubProcess | None = None
    info_messages: InfoMessage = InfoMessage(0)
    manipulation: Manipulation = Manipulation.NONE
    progress: int = 0

    @property
    def is_ready(self) -> bool:
        return self.process == MachineProcess.READY and self.manipulation == Manipulation.NONE

    @property
    def is_brewing(self) -> bool:
        return self.process == MachineProcess.PRODUCT

    @classmethod
    def from_payload(cls, data: bytes) -> MachineStatus:
        if len(data) < 8:
            return cls()
        process_val, sub_val, info_byte, manip_byte, progress = struct.unpack(">hhBBh", data[:8])
        try:
            process = MachineProcess(process_val)
        except ValueError:
            process = None
        try:
            sub_process = SubProcess(sub_val)
        except ValueError:
            sub_process = None
        try:
            manipulation = Manipulation(manip_byte)
        except ValueError:
            manipulation = Manipulation.NONE
        return cls(
            process=process,
            sub_process=sub_process,
            info_messages=InfoMessage(info_byte),
            manipulation=manipulation,
            progress=progress,
        )


@dataclass
class RecipeComponent:
    """Recipe component (8 bytes)."""
    process: int = 0
    shots: int = 1
    blend: int = 1
    intensity: int = 2
    aroma: int = 0
    temperature: int = 1
    portion: int = 5  # *5 = ml
    reserve: int = 0

    @property
    def portion_ml(self) -> int:
        return self.portion * 5

    def to_bytes(self) -> bytes:
        return struct.pack(
            "BBBBBBBB",
            self.process, self.shots, self.blend,
            self.intensity, self.aroma, self.temperature,
            self.portion, self.reserve,
        )

    @classmethod
    def from_bytes(cls, data: bytes) -> RecipeComponent:
        p, s, b, i, a, t, por, r = struct.unpack("BBBBBBBB", data[:8])
        return cls(p, s, b, i, a, t, por, r)


@dataclass
class MachineRecipe:
    """Machine recipe parsed from HC response."""
    recipe_id: int = 0
    recipe_type: int = 0
    component1: RecipeComponent = field(default_factory=RecipeComponent)
    component2: RecipeComponent = field(default_factory=RecipeComponent)

    @classmethod
    def from_payload(cls, data: bytes) -> MachineRecipe:
        recipe_id = struct.unpack(">h", data[0:2])[0]
        recipe_type = data[2]
        comp1 = RecipeComponent.from_bytes(data[3:11])
        comp2 = RecipeComponent.from_bytes(data[11:19])
        return cls(recipe_id, recipe_type, comp1, comp2)


@dataclass
class NumericalValue:
    """Numerical value from HR response."""
    value_id: int = 0
    value: int = 0

    @classmethod
    def from_payload(cls, data: bytes) -> NumericalValue:
        vid = struct.unpack(">h", data[0:2])[0]
        val = struct.unpack(">i", data[2:6])[0]
        return cls(vid, val)


@dataclass
class AlphanumericValue:
    """Alphanumeric value from HA response."""
    value_id: int = 0
    value: str = ""

    @classmethod
    def from_payload(cls, data: bytes) -> AlphanumericValue:
        vid = struct.unpack(">h", data[0:2])[0]
        val = data[2:].rstrip(b"\x00").decode("utf-8", errors="replace")
        return cls(vid, val)


class MelittaProtocol:
    """Protocol handler for Melitta Barista BLE communication.

    Frame format (outgoing):
        S + command + [key_prefix(2)] + [payload] + checksum + E
        Then RC4-encrypt everything after command bytes (key_prefix + payload + checksum).

    Handshake (HU):
        1. App sends HU with 6-byte payload: challenge(4) + crc(2)
        2. Machine responds HU with 8 bytes: challenge(4) + key_prefix(2) + validation(2)
        3. key_prefix is used in all subsequent encrypted frames.
    """

    def __init__(self) -> None:
        self._rc4_key: bytes | None = None
        self._key_prefix: bytes | None = None
        self._recv_buffer = bytearray()
        self._frame_futures: dict[str, asyncio.Future] = {}
        self._ack_future: asyncio.Future | None = None
        self._on_status: Callable[[MachineStatus], None] | None = None
        self._lock = asyncio.Lock()
        self._handshake_done = asyncio.Event()
        self._init_encryption()

    def _init_encryption(self) -> None:
        """Initialize RC4 key from hardcoded AES blob."""
        try:
            self._rc4_key = _derive_rc4_key()
            _LOGGER.debug("RC4 key derived (%d bytes)", len(self._rc4_key))
        except Exception:
            _LOGGER.exception("Failed to derive RC4 key")
            self._rc4_key = None

    @property
    def handshake_complete(self) -> bool:
        return self._key_prefix is not None

    def build_frame(
        self,
        command: str,
        payload: bytes | None = None,
        include_key_prefix: bool = True,
    ) -> bytes:
        """Build a complete frame ready for BLE transmission.

        Structure: S + cmd + [key_prefix] + [payload] + checksum + E
        Then RC4-encrypt from after cmd to before E (inclusive of checksum).
        """
        cmd_bytes = command.encode("ascii")
        kp = self._key_prefix if (include_key_prefix and self._key_prefix) else None

        # Assemble pre-encryption frame
        frame = bytearray([FRAME_START])
        frame.extend(cmd_bytes)
        if kp:
            frame.extend(kp)
        if payload:
            frame.extend(payload)

        # Checksum over bytes [1..len-1]
        cs = _calculate_checksum(bytes(frame) + b"\x00", len(frame))
        frame.append(cs)
        frame.append(FRAME_END)

        # RC4 encrypt everything after command bytes, before E
        encrypt_start = 1 + len(cmd_bytes)
        encrypt_end = len(frame) - 1  # exclude E
        encrypt_len = encrypt_end - encrypt_start
        if encrypt_len > 0 and self._rc4_key:
            plain = bytes(frame[encrypt_start:encrypt_end])
            encrypted = _rc4_crypt(plain, self._rc4_key)
            frame[encrypt_start:encrypt_end] = encrypted

        return bytes(frame)

    def chunk_for_ble(self, data: bytes) -> list[bytes]:
        """Split data into BLE MTU-sized chunks."""
        return [data[i:i + BLE_MTU] for i in range(0, len(data), BLE_MTU)]

    def on_ble_data(self, data: bytes) -> None:
        """Process incoming BLE notification data."""
        for byte_val in data:
            self._process_byte(byte_val)

    def _process_byte(self, byte_val: int) -> None:
        """Process a single byte from BLE stream."""
        if len(self._recv_buffer) == 0:
            if byte_val == FRAME_START:
                self._recv_buffer.append(byte_val)
            return

        if len(self._recv_buffer) >= 128:
            self._recv_buffer.clear()
            return

        self._recv_buffer.append(byte_val)

        if byte_val == FRAME_END and len(self._recv_buffer) >= 4:
            self._try_parse_frame()

    def _try_parse_frame(self) -> None:
        """Try to parse a complete frame from buffer."""
        buf = bytes(self._recv_buffer)
        self._recv_buffer.clear()

        if len(buf) < 4:
            return

        # Identify command (1 or 2 ASCII chars after S)
        cmd_2 = buf[1:3].decode("ascii", errors="replace")
        cmd_1 = buf[1:2].decode("ascii", errors="replace")

        cmd = None
        cmd_len = 0
        if cmd_2 in KNOWN_COMMANDS:
            cmd = cmd_2
            cmd_len = 2
        elif cmd_1 in KNOWN_COMMANDS:
            cmd = cmd_1
            cmd_len = 1

        if cmd is None:
            _LOGGER.debug("Unknown command in frame: %s", buf[1:3].hex())
            return

        # Encrypted part: everything after command bytes, before E
        encrypt_start = 1 + cmd_len
        encrypted_part = buf[encrypt_start:-1]  # exclude E byte

        # Decrypt
        if encrypted_part and self._rc4_key:
            decrypted = _rc4_crypt(encrypted_part, self._rc4_key)
            payload = decrypted[:-1]  # last byte is checksum
        else:
            payload = buf[encrypt_start:-2]

        _LOGGER.debug("Frame: cmd=%s payload=%s (%d bytes)", cmd, payload.hex(), len(payload))
        self._dispatch_frame(cmd, payload)

    def _dispatch_frame(self, command: str, payload: bytes) -> None:
        """Dispatch parsed frame to handler."""
        if command == CMD_ACK:
            if self._ack_future and not self._ack_future.done():
                self._ack_future.set_result(True)
            return

        if command == CMD_NACK:
            if self._ack_future and not self._ack_future.done():
                self._ack_future.set_result(False)
            return

        if command == CMD_READ_STATUS:
            status = MachineStatus.from_payload(payload)
            if self._on_status:
                self._on_status(status)

        if command == CMD_HANDSHAKE:
            self._handle_handshake_response(payload)

        # Resolve waiting futures
        if command in self._frame_futures:
            future = self._frame_futures.pop(command)
            if not future.done():
                future.set_result(payload)

    def _handle_handshake_response(self, payload: bytes) -> None:
        """Process HU handshake response: challenge(4) + key_prefix(2) + validation(2)."""
        if len(payload) < 6:
            _LOGGER.error("HU response too short: %d bytes", len(payload))
            return

        key_prefix = payload[4:6]
        self._key_prefix = key_prefix
        self._handshake_done.set()
        _LOGGER.info("Handshake complete, key_prefix=%s", key_prefix.hex())

    def set_status_callback(self, callback: Callable[[MachineStatus], None]) -> None:
        self._on_status = callback

    async def perform_handshake(
        self,
        write_func: Callable[[bytes], asyncio.coroutine],
    ) -> bool:
        """Perform HU challenge-response handshake."""
        self._handshake_done.clear()
        self._key_prefix = None

        challenge = os.urandom(4)
        crc = _compute_handshake_crc(len(challenge), challenge)
        hu_payload = challenge + crc  # 6 bytes

        frame = self.build_frame(CMD_HANDSHAKE, hu_payload, include_key_prefix=False)
        chunks = self.chunk_for_ble(frame)
        for chunk in chunks:
            await write_func(chunk)

        _LOGGER.debug("HU challenge sent: %s", challenge.hex())

        try:
            await asyncio.wait_for(self._handshake_done.wait(), timeout=FRAME_TIMEOUT)
            return self._key_prefix is not None
        except asyncio.TimeoutError:
            _LOGGER.error("HU handshake timeout")
            return False

    async def send_and_wait_ack(
        self,
        command: str,
        payload: bytes | None,
        write_func: Callable[[bytes], asyncio.coroutine],
    ) -> bool:
        """Send a write command and wait for ACK."""
        async with self._lock:
            loop = asyncio.get_running_loop()
            self._ack_future = loop.create_future()

            frame = self.build_frame(command, payload)
            chunks = self.chunk_for_ble(frame)
            for chunk in chunks:
                await write_func(chunk)

            try:
                return await asyncio.wait_for(self._ack_future, timeout=FRAME_TIMEOUT)
            except asyncio.TimeoutError:
                _LOGGER.warning("ACK timeout for %s", command)
                return False
            finally:
                self._ack_future = None

    async def send_and_wait_response(
        self,
        command: str,
        payload: bytes | None,
        write_func: Callable[[bytes], asyncio.coroutine],
    ) -> bytes | None:
        """Send a read command and wait for response frame."""
        async with self._lock:
            loop = asyncio.get_running_loop()
            future = loop.create_future()
            self._frame_futures[command] = future

            frame = self.build_frame(command, payload)
            chunks = self.chunk_for_ble(frame)
            for chunk in chunks:
                await write_func(chunk)

            try:
                return await asyncio.wait_for(future, timeout=FRAME_TIMEOUT)
            except asyncio.TimeoutError:
                _LOGGER.debug("Response timeout for %s", command)
                self._frame_futures.pop(command, None)
                return None

    # High-level API

    async def read_status(self, write_func) -> MachineStatus | None:
        data = await self.send_and_wait_response(CMD_READ_STATUS, None, write_func)
        if data:
            return MachineStatus.from_payload(data)
        return None

    async def read_version(self, write_func) -> str | None:
        data = await self.send_and_wait_response(CMD_READ_VERSION, None, write_func)
        if data:
            return data.rstrip(b"\x00").decode("utf-8", errors="replace")
        return None

    async def read_numerical(self, write_func, value_id: int) -> int | None:
        payload = struct.pack(">h", value_id)
        data = await self.send_and_wait_response(CMD_READ_NUMERICAL, payload, write_func)
        if data:
            return NumericalValue.from_payload(data).value
        return None

    async def read_alphanumeric(self, write_func, value_id: int) -> str | None:
        payload = struct.pack(">h", value_id)
        data = await self.send_and_wait_response(CMD_READ_ALPHA, payload, write_func)
        if data:
            return AlphanumericValue.from_payload(data).value
        return None

    async def read_recipe(self, write_func, recipe_id: int) -> MachineRecipe | None:
        payload = struct.pack(">h", recipe_id)
        data = await self.send_and_wait_response(CMD_READ_RECIPE, payload, write_func)
        if data:
            return MachineRecipe.from_payload(data)
        return None

    async def write_numerical(self, write_func, value_id: int, value: int) -> bool:
        payload = struct.pack(">hi", value_id, value)
        return await self.send_and_wait_ack(CMD_WRITE_NUMERICAL, payload, write_func)

    async def write_alphanumeric(self, write_func, value_id: int, value: str) -> bool:
        text = value[:64].encode("utf-8")
        payload = struct.pack(">h", value_id) + text.ljust(64, b"\x00")
        return await self.send_and_wait_ack(CMD_WRITE_ALPHA, payload, write_func)

    async def write_recipe(
        self, write_func, recipe_id: int, recipe_type: int,
        recipe_key: int, comp1: RecipeComponent, comp2: RecipeComponent,
    ) -> bool:
        """Write recipe via HJ command (66-byte payload)."""
        payload = bytearray(66)
        struct.pack_into(">h", payload, 0, recipe_id)
        payload[2] = recipe_type & 0xFF
        payload[3] = recipe_key & 0xFF
        payload[4:12] = comp1.to_bytes()
        payload[12:20] = comp2.to_bytes()
        return await self.send_and_wait_ack(CMD_WRITE_RECIPE, bytes(payload), write_func)

    async def start_process(self, write_func, process_value: int) -> bool:
        """Start process via HE command."""
        he_data = bytearray(16)
        struct.pack_into(">h", he_data, 0, 2)  # fixed value 2
        payload = struct.pack(">h", process_value) + bytes(he_data)
        return await self.send_and_wait_ack(CMD_START_PROCESS, payload, write_func)

    async def start_recipe(self, write_func, process_value: int, data: bytes) -> bool:
        """Legacy: start process with raw data."""
        payload = struct.pack(">h", process_value) + data.ljust(16, b"\x00")
        return await self.send_and_wait_ack(CMD_START_PROCESS, payload, write_func)

    async def cancel_process(self, write_func, process_value: int) -> bool:
        payload = struct.pack(">h", process_value) + b"\x00\x00"
        return await self.send_and_wait_ack(CMD_CANCEL_PROCESS, payload, write_func)
