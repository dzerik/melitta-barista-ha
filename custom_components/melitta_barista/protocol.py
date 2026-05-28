"""Eugster/EFLibrary BLE protocol — shared by Melitta Barista and Nivona NICR/NIVO lines."""

from __future__ import annotations

import asyncio
import logging
import os
import struct
import time
from collections import deque
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .brands.base import BrandProfile
from dataclasses import dataclass, field

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from .const import (
    AES_IV,
    AES_KEY_PART_A,
    AES_KEY_PART_B,
    BLE_MTU,
    CMD_ACK,
    CMD_CANCEL_PROCESS,
    CMD_CONFIRM_PROMPT,
    CMD_HANDSHAKE,
    CMD_NACK,
    CMD_READ_ALPHA,
    CMD_READ_FEATURES,
    CMD_READ_NUMERICAL,
    CMD_READ_RECIPE,
    CMD_READ_SERIAL,
    CMD_READ_STATUS,
    CMD_READ_VERSION,
    CMD_RESET_DEFAULT,
    CMD_START_PROCESS,
    CMD_WRITE_ALPHA,
    CMD_WRITE_NUMERICAL,
    CMD_WRITE_RECIPE,
    ENCRYPTED_RC4_KEY,
    FRAME_END,
    FRAME_START,
    FRAME_TIMEOUT,
    FeatureFlags,
    InfoMessage,
    MachineProcess,
    Manipulation,
    SubProcess,
)

_LOGGER = logging.getLogger("melitta_barista")

# Known commands with expected RECEIVE payload sizes and encryption flag
# From Melitta BLE protocol analysis.
# Only commands the machine sends TO us are registered here.
# Write-only commands (HB, HE, HJ, HW, HZ) are not listed — machine responds
# with A/N (ACK/NACK) to those.
# Format: {cmd: (payload_size, encrypted)}
KNOWN_COMMANDS: dict[str, tuple[int, bool]] = {
    "A": (0, False), "N": (0, False),
    "HA": (66, True), "HC": (66, True), "HR": (6, True), "HV": (11, True),
    "HX": (8, True), "HU": (8, True), "HI": (10, True),
    "HF": (16, True), "HL": (20, True), "HQ": (15, True), "HP": (14, True),
}


def _derive_rc4_key() -> bytes:
    """Derive RC4 key by decrypting the hardcoded AES blob."""
    aes_key = AES_KEY_PART_B + AES_KEY_PART_A
    decryptor = Cipher(algorithms.AES(aes_key), modes.CBC(AES_IV)).decryptor()
    decrypted = decryptor.update(ENCRYPTED_RC4_KEY) + decryptor.finalize()
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


def _build_he_command_payload(command_id: int) -> bytes:
    """Build the 18-byte HE-with-commandId payload.

    Layout:

        payload[0:2]  command_id (BE uint16)
        payload[2:18] zero padding

    Sent under the same ``HE`` opcode that brew uses; the firmware
    distinguishes brew from "execute command" by the payload shape.
    See ``EugsterProtocol.execute_command``.
    """
    payload = bytearray(18)
    payload[0] = (command_id >> 8) & 0xFF
    payload[1] = command_id & 0xFF
    return bytes(payload)


def _calculate_checksum(frame: bytes, length: int) -> int:
    """Calculate frame checksum: ~(sum of bytes[1..length]) & 0xFF."""
    s = 0
    for i in range(1, length):
        s = (s + frame[i]) & 0xFF
    return (~s) & 0xFF


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

    def is_ready_for_brew(self, tolerated: tuple[int, ...] = ()) -> bool:
        """Like is_ready but accepts brand-tolerated manipulation flags.

        Some Nivona models leave MOVE_CUP_TO_FROTHER set after a
        completed brew until the next status frame; the brand profile
        declares such flags via tolerated_brew_manipulations so the
        brew gate doesn't reject the next brew falsely.
        """
        if self.process != MachineProcess.READY:
            return False
        if self.manipulation == Manipulation.NONE:
            return True
        return self.manipulation.value in tolerated

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
    def from_bytes(cls, data: bytes) -> RecipeComponent | None:
        if len(data) < 8:
            _LOGGER.debug("RecipeComponent data too short: %d bytes", len(data))
            return None
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
    def from_payload(cls, data: bytes) -> MachineRecipe | None:
        """Parse HC response: recipe_id(2) + recipe_type(1) + comp1(8) + comp2(8).

        Note: HC response does NOT contain recipe_key byte.
        HJ write payload has recipe_key at offset 3, but read response skips it.
        """
        if len(data) < 19:
            _LOGGER.debug("MachineRecipe payload too short: %d bytes", len(data))
            return None
        recipe_id = struct.unpack(">h", data[0:2])[0]
        recipe_type = data[2]
        comp1 = RecipeComponent.from_bytes(data[3:11])
        comp2 = RecipeComponent.from_bytes(data[11:19])
        if comp1 is None or comp2 is None:
            return None
        return cls(recipe_id, recipe_type, comp1, comp2)


@dataclass
class NumericalValue:
    """Numerical value from HR response."""
    value_id: int = 0
    value: int = 0

    @classmethod
    def from_payload(cls, data: bytes) -> NumericalValue | None:
        if len(data) < 6:
            _LOGGER.debug("NumericalValue payload too short: %d bytes", len(data))
            return None
        vid = struct.unpack(">h", data[0:2])[0]
        val = struct.unpack(">i", data[2:6])[0]
        return cls(vid, val)


@dataclass
class AlphanumericValue:
    """Alphanumeric value from HA response."""
    value_id: int = 0
    value: str = ""

    @classmethod
    def from_payload(cls, data: bytes) -> AlphanumericValue | None:
        if len(data) < 2:
            _LOGGER.debug("AlphanumericValue payload too short: %d bytes", len(data))
            return None
        vid = struct.unpack(">h", data[0:2])[0]
        val = data[2:].rstrip(b"\x00").decode("utf-8", errors="replace")
        return cls(vid, val)


class EugsterProtocol:
    """Generic Eugster/EFLibrary protocol handler shared across brands.

    Brand-specific behaviour (RC4 key, HU verifier table, supported
    optional opcodes) is provided by a ``BrandProfile`` instance passed
    at construction. With no profile specified, falls back to
    :class:`MelittaProfile` for backward compatibility.

    Frame format (outgoing):
        S + command + [key_prefix(2)] + [payload] + checksum + E
        Then RC4-encrypt everything after command bytes (key_prefix +
        payload + checksum).

    Handshake (HU):
        1. App sends HU with 6-byte payload: challenge(4) + verifier(2)
        2. Machine responds HU with 8 bytes: challenge(4) + key_prefix(2) + verifier(2)
        3. ``key_prefix`` is used in all subsequent encrypted frames.
    """

    def __init__(
        self,
        *,
        frame_timeout: int = FRAME_TIMEOUT,
        brand: "BrandProfile | None" = None,
    ) -> None:
        # Lazy import to avoid circular dependency: brands → base does
        # not need protocol, but protocol needs brands at runtime.
        if brand is None:
            from .brands import get_profile  # noqa: PLC0415
            brand = get_profile("melitta")
        self._brand: BrandProfile = brand
        self._family: str | None = None

        self._rc4_key: bytes | None = None
        self._key_prefix: bytes | None = None
        # Stored across the request/response of a single HU handshake so
        # the response handler can verify the echoed seed. Cleared on
        # entry to `perform_handshake`, set after the challenge is
        # generated, and remains set after handshake completes — that
        # way a duplicate / late HU notify is still bound to the right
        # challenge.
        self._pending_challenge: bytes | None = None
        self._recv_buffer = bytearray()
        self._frame_start_time: float = 0.0
        self._frame_futures: dict[str, asyncio.Future] = {}
        self._ack_future: asyncio.Future | None = None
        self._on_status: Callable[[MachineStatus], None] | None = None
        self._lock = asyncio.Lock()
        self._handshake_done = asyncio.Event()
        self._frame_timeout = frame_timeout
        # Decoded-frame ring buffer for diagnostics (post-RC4 payloads).
        # Bounded at 200 entries to keep memory predictable.
        self._frame_log: deque[dict] = deque(maxlen=200)
        self._init_encryption()

    @property
    def brand(self) -> "BrandProfile":
        return self._brand

    def set_family(self, family_key: str | None) -> None:
        """Set the detected machine family — enables brand-aware HX parsing."""
        self._family = family_key

    def _init_encryption(self) -> None:
        """Initialise RC4 key from the active brand profile."""
        try:
            self._rc4_key = self._brand.runtime_rc4_key
            _LOGGER.debug(
                "RC4 key loaded for brand %s (%d bytes)",
                self._brand.brand_slug, len(self._rc4_key),
            )
        except Exception:  # noqa: BLE001 — defensive; brand init shouldn't fail
            _LOGGER.exception(
                "Failed to load RC4 key for brand %s", self._brand.brand_slug,
            )
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
        _LOGGER.debug("BLE RX [%d]: %s", len(data), data.hex())
        for byte_val in data:
            self._process_byte(byte_val)

    def _process_byte(self, byte_val: int) -> None:
        """Process a single byte from BLE stream.

        Matches the original Melitta app algorithm (Q3/q.java):
        - S (0x53) only starts a frame when buffer is empty
        - S inside a frame is treated as regular data (can appear in RC4 ciphertext)
        - E (0x45) triggers a length check against known commands
        - If E arrives but length doesn't match any command, continue collecting
        - 1-second timeout clears stale buffer (original uses Timer in q.java)
        - Buffer overflow at 128 bytes resets
        """
        if len(self._recv_buffer) == 0:
            if byte_val == FRAME_START:
                self._recv_buffer.append(byte_val)
                self._frame_start_time = time.monotonic()
            return

        # 1-second frame timeout (matches original Timer in Q3/q.java)
        if time.monotonic() - self._frame_start_time > 1.0:
            _LOGGER.debug(
                "Frame timeout: discarding stale buffer (%d bytes)", len(self._recv_buffer)
            )
            self._recv_buffer.clear()
            if byte_val == FRAME_START:
                self._recv_buffer.append(byte_val)
                self._frame_start_time = time.monotonic()
            return

        if len(self._recv_buffer) >= 128:
            self._recv_buffer.clear()
            return

        self._recv_buffer.append(byte_val)

        # Only check for frame completion on E byte with minimum frame size
        if byte_val != FRAME_END or len(self._recv_buffer) < 4:
            return

        # Check if buffer length matches any known command's expected frame size
        buf = self._recv_buffer
        buf_len = len(buf)
        cmd_2 = chr(buf[1]) + chr(buf[2]) if buf_len >= 3 else ""
        cmd_1 = chr(buf[1])

        matched = False
        if cmd_2 in KNOWN_COMMANDS:
            payload_size, encrypted = KNOWN_COMMANDS[cmd_2]
            # S(1) + cmd(2) + [encrypted](payload + checksum) + E(1)
            expected = 1 + 2 + payload_size + 1 + 1
            if expected == buf_len:
                matched = True
        if not matched and cmd_1 in KNOWN_COMMANDS:
            payload_size, encrypted = KNOWN_COMMANDS[cmd_1]
            # S(1) + cmd(1) + [encrypted](payload + checksum) + E(1)
            expected = 1 + 1 + payload_size + 1 + 1
            if expected == buf_len:
                matched = True

        if matched:
            self._try_parse_frame()
        # else: E byte but wrong length — continue collecting bytes

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
        is_encrypted = False
        if cmd_2 in KNOWN_COMMANDS:
            cmd = cmd_2
            cmd_len = 2
            is_encrypted = KNOWN_COMMANDS[cmd_2][1]
        elif cmd_1 in KNOWN_COMMANDS:
            cmd = cmd_1
            cmd_len = 1
            is_encrypted = KNOWN_COMMANDS[cmd_1][1]

        if cmd is None:
            _LOGGER.debug("Unknown command in frame: %s", buf[1:3].hex())
            return

        # Data part: everything after command bytes, before E
        data_start = 1 + cmd_len
        data_part = buf[data_start:-1]  # exclude E byte

        if is_encrypted and data_part and self._rc4_key:
            # Encrypted frame: decrypt, then verify checksum
            decrypted = _rc4_crypt(data_part, self._rc4_key)
            payload = decrypted[:-1]  # last byte is checksum
            received_cs = decrypted[-1]

            # Verify checksum: ~(sum of cmd_bytes + payload) & 0xFF
            cs_data = buf[1:1 + cmd_len] + payload
            expected_cs = (~sum(cs_data) & 0xFF)
            if received_cs != expected_cs:
                _LOGGER.warning(
                    "Checksum mismatch for cmd=%s: got 0x%02X, expected 0x%02X, "
                    "raw_frame=%s, decrypted_part=%s",
                    cmd, received_cs, expected_cs, buf.hex(), decrypted.hex(),
                )
                return
        else:
            # Unencrypted frame (A/N): payload + checksum in plaintext
            if data_part:
                payload = data_part[:-1]
                received_cs = data_part[-1]
                cs_data = buf[1:1 + cmd_len] + payload
                expected_cs = (~sum(cs_data) & 0xFF)
                if received_cs != expected_cs:
                    _LOGGER.warning(
                        "Checksum mismatch for unencrypted cmd=%s: got 0x%02X, expected 0x%02X",
                        cmd, received_cs, expected_cs,
                    )
                    return
            else:
                payload = b""

        _LOGGER.debug("Frame: cmd=%s payload=%s (%d bytes)", cmd, payload.hex(), len(payload))
        self._dispatch_frame(cmd, payload)

    def _dispatch_frame(self, command: str, payload: bytes) -> None:
        """Dispatch parsed frame to handler."""
        # Record every decoded frame in the ring buffer so the diagnostics
        # download captures a recent trace. ACK / NACK have empty payloads
        # and are skipped to keep the buffer focused on data frames.
        if command not in (CMD_ACK, CMD_NACK) and payload:
            self._frame_log.append({
                "ts": time.time(),
                "cmd": command,
                "len": len(payload),
                "hex": payload.hex(),
            })
            # Surface a live-greppable line for frames we do not actively
            # decode (HF / HQ / HP / HL ...). HX / HR / HA etc. are noisy
            # and already understood, so they stay out of the log unless
            # the wider HA debug filter is on.
            if command not in (CMD_READ_STATUS, CMD_HANDSHAKE) and command not in self._frame_futures:
                _LOGGER.debug(
                    "[FRAME-UNH] cmd=%s len=%d hex=%s",
                    command, len(payload), payload.hex(),
                )

        if command == CMD_ACK:
            if self._ack_future and not self._ack_future.done():
                self._ack_future.set_result(True)
            return

        if command == CMD_NACK:
            if self._ack_future and not self._ack_future.done():
                self._ack_future.set_result(False)
            return

        if command == CMD_READ_STATUS:
            status = self._brand.parse_status(self._family, payload)
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
        """Process HU handshake response and validate it end-to-end.

        Wire layout (8 bytes, post-RC4-decrypt):
            payload[0:4]  echoed seed — must equal the challenge we sent
            payload[4:6]  session_key (key_prefix in our terminology)
            payload[6:8]  verifier — must equal hu_verifier(payload[0:6])

        On any mismatch the method records the failure, leaves
        ``_key_prefix`` as ``None`` and still sets ``_handshake_done`` so
        ``perform_handshake`` returns ``False`` instead of hanging. This
        mirrors upstream ``parseHuResponsePayload`` (esp-coffee-bridge
        ``src/nivona.cpp``) — without these checks an invalid response
        would silently install a junk session key and every subsequent
        RC4-encrypted frame would be undecryptable.
        """
        # Length gate. Upstream demands exactly 8; we accept >= 8 to
        # tolerate a hypothetical trailing-byte firmware variant, then
        # only consume the first 8.
        if len(payload) < 8:
            _LOGGER.warning(
                "HU response too short: %d bytes (need >= 8); rejecting",
                len(payload),
            )
            self._handshake_done.set()
            return

        echoed_seed = payload[0:4]
        key_prefix = payload[4:6]
        response_verifier = payload[6:8]

        if self._pending_challenge is None:
            _LOGGER.warning(
                "HU response received without a pending challenge; rejecting"
            )
            self._handshake_done.set()
            return

        if echoed_seed != self._pending_challenge:
            _LOGGER.warning(
                "HU echoed seed mismatch: sent %s, got %s; rejecting",
                self._pending_challenge.hex(), echoed_seed.hex(),
            )
            self._handshake_done.set()
            return

        expected_verifier = self._brand.hu_verifier(payload, 0, 6)
        if response_verifier != expected_verifier:
            _LOGGER.warning(
                "HU verifier mismatch: expected %s, got %s; rejecting",
                expected_verifier.hex(), response_verifier.hex(),
            )
            self._handshake_done.set()
            return

        self._key_prefix = key_prefix
        self._handshake_done.set()
        _LOGGER.info("Handshake complete, key_prefix=%s", key_prefix.hex())

    def set_status_callback(self, callback: Callable[[MachineStatus], None]) -> None:
        self._on_status = callback

    async def perform_handshake(
        self,
        write_func: Callable[[bytes], Awaitable[None]],
    ) -> bool:
        """Perform HU challenge-response handshake."""
        self._handshake_done.clear()
        self._key_prefix = None
        self._pending_challenge = None

        challenge = os.urandom(4)
        verifier = self._brand.hu_verifier(challenge, 0, len(challenge))
        hu_payload = challenge + verifier  # 6 bytes
        # Stored BEFORE we write — the response is verified against it.
        self._pending_challenge = challenge

        frame = self.build_frame(CMD_HANDSHAKE, hu_payload, include_key_prefix=False)
        chunks = self.chunk_for_ble(frame)
        for chunk in chunks:
            await write_func(chunk)

        _LOGGER.debug("HU challenge sent: %s", challenge.hex())

        try:
            await asyncio.wait_for(self._handshake_done.wait(), timeout=self._frame_timeout)
            return self._key_prefix is not None
        except asyncio.TimeoutError:
            _LOGGER.error("HU handshake timeout")
            return False

    async def send_and_wait_ack(
        self,
        command: str,
        payload: bytes | None,
        write_func: Callable[[bytes], Awaitable[None]],
        retries: int = 2,
    ) -> bool:
        """Send a write command and wait for ACK, with retry on timeout."""
        frame = self.build_frame(command, payload)
        chunks = self.chunk_for_ble(frame)

        for attempt in range(retries):
            async with self._lock:
                loop = asyncio.get_running_loop()
                self._ack_future = loop.create_future()

                for chunk in chunks:
                    await write_func(chunk)

                try:
                    return await asyncio.wait_for(self._ack_future, timeout=self._frame_timeout)
                except asyncio.TimeoutError:
                    pass
                finally:
                    self._ack_future = None

            if attempt < retries - 1:
                _LOGGER.debug("ACK timeout for %s, retrying (%d/%d)", command, attempt + 1, retries)
                await asyncio.sleep(0.3)

        _LOGGER.warning("ACK timeout for %s after %d attempts", command, retries)
        return False

    async def send_and_wait_response(
        self,
        command: str,
        payload: bytes | None,
        write_func: Callable[[bytes], Awaitable[None]],
        timeout: float | None = None,
    ) -> bytes | None:
        """Send a read command and wait for response frame.

        ``timeout`` overrides the default ``self._frame_timeout`` for this
        call only — used by optional commands (e.g. ``HI``) that may not
        be answered on some firmwares.
        """
        effective_timeout = timeout if timeout is not None else self._frame_timeout
        async with self._lock:
            loop = asyncio.get_running_loop()
            future = loop.create_future()
            self._frame_futures[command] = future

            try:
                frame = self.build_frame(command, payload)
                chunks = self.chunk_for_ble(frame)
                for chunk in chunks:
                    await write_func(chunk)

                return await asyncio.wait_for(future, timeout=effective_timeout)
            except asyncio.TimeoutError:
                _LOGGER.debug("Response timeout for %s", command)
                return None
            finally:
                self._frame_futures.pop(command, None)

    # High-level API

    async def read_status(self, write_func) -> MachineStatus | None:
        data = await self.send_and_wait_response(CMD_READ_STATUS, None, write_func)
        if data:
            return self._brand.parse_status(self._family, data)
        return None

    async def read_version(self, write_func) -> str | None:
        data = await self.send_and_wait_response(CMD_READ_VERSION, None, write_func)
        if data:
            return data.rstrip(b"\x00").decode("utf-8", errors="replace")
        return None

    async def read_serial(self, write_func) -> str | None:
        """Read machine serial number via HL (20-byte ASCII response).

        Empty request payload, response is a 20-byte ASCII string commonly
        zero-padded. Returns ``None`` if the machine does not answer (some
        firmwares may not respond to HL even though the frame size is
        registered).
        """
        data = await self.send_and_wait_response(CMD_READ_SERIAL, None, write_func)
        if data:
            return data.rstrip(b"\x00").decode("utf-8", errors="replace").strip()
        return None

    async def read_features(self, write_func) -> FeatureFlags | None:
        """Read HI capability bits (10 bytes).

        Returns ``None`` on timeout — some firmwares simply do not answer
        this command (observed on Nivona NICR 756).

        Only byte 0 is currently decoded into known flags; bytes [1..9]
        are logged as raw hex for future decoding.
        """
        data = await self.send_and_wait_response(
            CMD_READ_FEATURES, None, write_func, timeout=3.0,
        )
        if not data:
            return None
        if len(data) >= 10:
            _LOGGER.debug("HI raw payload: %s", data.hex())
        return FeatureFlags(data[0])

    async def read_numerical(self, write_func, value_id: int) -> int | None:
        payload = struct.pack(">h", value_id)
        data = await self.send_and_wait_response(CMD_READ_NUMERICAL, payload, write_func)
        if data:
            nv = NumericalValue.from_payload(data)
            return nv.value if nv else None
        return None

    async def read_alphanumeric(self, write_func, value_id: int) -> str | None:
        payload = struct.pack(">h", value_id)
        data = await self.send_and_wait_response(CMD_READ_ALPHA, payload, write_func)
        if data:
            av = AlphanumericValue.from_payload(data)
            return av.value if av else None
        return None

    async def read_recipe(self, write_func, recipe_id: int) -> MachineRecipe | None:
        if "HC" not in self._brand.supported_extensions:
            from .brands.base import FeatureNotSupported  # noqa: PLC0415
            raise FeatureNotSupported("HC", self._brand.brand_slug)
        payload = struct.pack(">h", recipe_id)
        data = await self.send_and_wait_response(CMD_READ_RECIPE, payload, write_func)
        if data:
            result = MachineRecipe.from_payload(data)
            return result
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
        comp1: RecipeComponent, comp2: RecipeComponent,
        recipe_key: int | None = None,
        comp3: RecipeComponent | None = None,
    ) -> bool:
        """Write recipe via HJ command (66-byte payload).

        Raises ``FeatureNotSupported`` when the active brand does not
        advertise the ``HJ`` opcode (e.g. Nivona).

        Layout: recipe_id(2) + recipe_type(1) [+ recipe_key(1)] + comp1(8) + comp2(8) [+ comp3(8)].
        When recipe_key is None, the byte is omitted (used for DirectKey slots).
        When recipe_key is set, it's included (used for TEMP_RECIPE brewing).
        """
        if "HJ" not in self._brand.supported_extensions:
            from .brands.base import FeatureNotSupported  # noqa: PLC0415
            raise FeatureNotSupported("HJ", self._brand.brand_slug)
        payload = bytearray(66)
        struct.pack_into(">h", payload, 0, recipe_id)
        payload[2] = recipe_type & 0xFF
        offset = 3
        if recipe_key is not None:
            payload[offset] = recipe_key & 0xFF
            offset += 1
        payload[offset:offset + 8] = comp1.to_bytes()
        offset += 8
        payload[offset:offset + 8] = comp2.to_bytes()
        offset += 8
        if comp3 is not None:
            payload[offset:offset + 8] = comp3.to_bytes()
        return await self.send_and_wait_ack(CMD_WRITE_RECIPE, bytes(payload), write_func)

    async def start_process(
        self, write_func, process_value: int, *, two_cups: bool = False,
    ) -> bool:
        """Start process via HE command.

        Layout: process_value(2) + fixed(2) + zeros(4) + two_cups_flag(2) + zeros(8) = 18 bytes.
        two_cups flag at he_data offset 6 tells the machine to brew twice.
        """
        he_data = bytearray(16)
        struct.pack_into(">h", he_data, 0, 2)  # fixed value 2
        if two_cups:
            struct.pack_into(">h", he_data, 6, 1)  # two cups flag
        payload = struct.pack(">h", process_value) + bytes(he_data)
        return await self.send_and_wait_ack(CMD_START_PROCESS, payload, write_func)

    async def start_process_nivona(
        self, write_func, recipe_selector: int, brew_mode: int = 0x0B,
        use_temp_recipe: bool = True, chilled: bool = False,
    ) -> bool:
        """Start brewing on Nivona via HE with the 18-byte payload shape.

        Layout:

            payload[0] = 0
            payload[1] = brew_mode (0x04 for NIVO 8000, 0x0B for all others)
            payload[2] = 0
            payload[3] = recipe_selector
            payload[4] = 0
            payload[5] = mode byte:
                         - 0x01 = read brew parameters from the
                           temp-recipe registers (caller must HW-write
                           them via TEMP_RECIPE_TYPE_REGISTER + per-
                           field offsets BEFORE this call).
                         - 0x00 = use the machine's saved recipe
                           defaults. Also the value used for chilled-
                           brew on NICR 8107 (selectors 8/9/10), which
                           the machine interprets as "cold-prepare".
            payload[6..17] = 0
        """
        payload = bytearray(18)
        payload[1] = brew_mode & 0xFF
        payload[3] = recipe_selector & 0xFF
        payload[5] = 0x00 if chilled or not use_temp_recipe else 0x01
        return await self.send_and_wait_ack(CMD_START_PROCESS, bytes(payload), write_func)

    async def execute_command(self, write_func, command_id: int) -> bool:
        """Send an HE-with-commandId action (factory reset etc.).

        The same opcode (`HE`, `CMD_START_PROCESS`) is used for brewing
        and for "execute command" admin actions; the firmware
        distinguishes them by the 18-byte payload shape. See
        `_build_he_command_payload` for the layout.

        Used by `melitta_barista.button` factory-reset entities for
        Nivona families that expose the corresponding menu in the
        vendor app (600 / 700 / 79x / 900 / 900-light / 1030 / 1040 —
        NOT NIVO 8000).
        """
        payload = _build_he_command_payload(command_id)
        return await self.send_and_wait_ack(CMD_START_PROCESS, payload, write_func)

    async def cancel_process(self, write_func, process_value: int) -> bool:
        payload = struct.pack(">h", process_value) + b"\x00\x00"
        return await self.send_and_wait_ack(CMD_CANCEL_PROCESS, payload, write_func)

    async def reset_default(self, write_func, value_id: int) -> bool:
        """Send HD command to reset a register to its factory default.

        Payload: 2-byte big-endian register ID. Machine responds with
        ``A`` (success) or ``N`` (not supported / invalid id).
        """
        payload = struct.pack(">h", value_id)
        return await self.send_and_wait_ack(CMD_RESET_DEFAULT, payload, write_func)

    async def confirm_prompt(self, write_func) -> bool:
        """Send HY command to confirm an active machine prompt.

        Payload: 4 zero bytes. Machine responds ``A``/``N``. Used to
        acknowledge prompts like "move cup to frother" or "flush
        required" so the brew flow can proceed.
        """
        return await self.send_and_wait_ack(
            CMD_CONFIRM_PROMPT, b"\x00\x00\x00\x00", write_func,
        )


# Backward-compatibility alias — existing imports continue to work.
# New code should import EugsterProtocol and pass an explicit brand.
MelittaProtocol = EugsterProtocol
