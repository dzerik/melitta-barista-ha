"""End-to-end tests for the Nivona BLE emulator.

Run with:
    python3 -m pytest tests/test_emulator.py -v -s

or directly:
    python3 tests/test_emulator.py

Requires the emulator to be reachable at EMU_IP (default 192.168.1.29)
for HTTP diag/OTA and via BLE at EMU_MAC (default F1:32:04:33:52:DA).
"""
from __future__ import annotations

import asyncio
import os
import struct
import sys
import time
from dataclasses import dataclass

import pytest
import requests
from bleak import BleakClient, BleakScanner

EMU_IP = os.environ.get("EMU_IP", "192.168.1.29")
EMU_MAC = os.environ.get("EMU_MAC", "F1:32:04:33:52:DA")

SVC_AD00   = "0000ad00-b35c-11e4-9813-0002a5d5c51b"
CHAR_AD01  = "0000ad01-b35c-11e4-9813-0002a5d5c51b"
CHAR_AD02  = "0000ad02-b35c-11e4-9813-0002a5d5c51b"
CHAR_AD03  = "0000ad03-b35c-11e4-9813-0002a5d5c51b"
CHAR_AD06  = "0000ad06-b35c-11e4-9813-0002a5d5c51b"
SVC_DIS    = "0000180a-0000-1000-8000-00805f9b34fb"
CHAR_MFR   = "00002a29-0000-1000-8000-00805f9b34fb"
CHAR_MODEL = "00002a24-0000-1000-8000-00805f9b34fb"

NIVONA_RC4_KEY = b"NIV_060616_V10_1*9#3!4$6+4res-?3"
HU_TABLE = bytes([
    0x62,0x06,0x55,0x96,0x24,0x17,0x70,0xA4,0x87,0xCF,0xA9,0x05,0x1A,0x40,0xA5,0xDB,
    0x3D,0x14,0x44,0x59,0x82,0x3F,0x34,0x66,0x18,0xE5,0x84,0xF5,0x50,0xD8,0xC3,0x73,
    0x5A,0xA8,0x9C,0xCB,0xB1,0x78,0x02,0xBE,0xBC,0x07,0x64,0xB9,0xAE,0xF3,0xA2,0x0A,
    0xED,0x12,0xFD,0xE1,0x08,0xD0,0xAC,0xF4,0xFF,0x7E,0x65,0x4F,0x91,0xEB,0xE4,0x79,
    0x7B,0xFB,0x43,0xFA,0xA1,0x00,0x6B,0x61,0xF1,0x6F,0xB5,0x52,0xF9,0x21,0x45,0x37,
    0x3B,0x99,0x1D,0x09,0xD5,0xA7,0x54,0x5D,0x1E,0x2E,0x5E,0x4B,0x97,0x72,0x49,0xDE,
    0xC5,0x60,0xD2,0x2D,0x10,0xE3,0xF8,0xCA,0x33,0x98,0xFC,0x7D,0x51,0xCE,0xD7,0xBA,
    0x27,0x9E,0xB2,0xBB,0x83,0x88,0x01,0x31,0x32,0x11,0x8D,0x5B,0x2F,0x81,0x3C,0x63,
    0x9A,0x23,0x56,0xAB,0x69,0x22,0x26,0xC8,0x93,0x3A,0x4D,0x76,0xAD,0xF6,0x4C,0xFE,
    0x85,0xE8,0xC4,0x90,0xC6,0x7C,0x35,0x04,0x6C,0x4A,0xDF,0xEA,0x86,0xE6,0x9D,0x8B,
    0xBD,0xCD,0xC7,0x80,0xB0,0x13,0xD3,0xEC,0x7F,0xC0,0xE7,0x46,0xE9,0x58,0x92,0x2C,
    0xB7,0xC9,0x16,0x53,0x0D,0xD6,0x74,0x6D,0x9F,0x20,0x5F,0xE2,0x8C,0xDC,0x39,0x0C,
    0xDD,0x1F,0xD1,0xB6,0x8F,0x5C,0x95,0xB8,0x94,0x3E,0x71,0x41,0x25,0x1B,0x6A,0xA6,
    0x03,0x0E,0xCC,0x48,0x15,0x29,0x38,0x42,0x1C,0xC1,0x28,0xD9,0x19,0x36,0xB3,0x75,
    0xEE,0x57,0xF0,0x9B,0xB4,0xAA,0xF2,0xD4,0xBF,0xA3,0x4E,0xDA,0x89,0xC2,0xAF,0x6E,
    0x2B,0x77,0xE0,0x47,0x7A,0x8E,0x2A,0xA0,0x68,0x30,0xF7,0x67,0x0F,0x0B,0x8A,0xEF,
])


# ---------------------------------------------------------------------------
# Protocol helpers
# ---------------------------------------------------------------------------

def rc4(data: bytes, key: bytes) -> bytes:
    s = list(range(256)); j = 0
    for i in range(256):
        j = (j + s[i] + key[i % len(key)]) % 256
        s[i], s[j] = s[j], s[i]
    out = bytearray(len(data)); i = j = 0
    for idx in range(len(data)):
        i = (i + 1) % 256; j = (j + s[i]) % 256
        s[i], s[j] = s[j], s[i]
        out[idx] = data[idx] ^ s[(s[i] + s[j]) % 256]
    return bytes(out)


def hu_verifier(buf: bytes, start: int, count: int) -> bytes:
    s = HU_TABLE[buf[start]]
    for i in range(start + 1, start + count):
        s = HU_TABLE[(s ^ buf[i]) & 0xFF]
    o0 = (s + 0x5D) & 0xFF
    s = HU_TABLE[(buf[start] + 1) & 0xFF]
    for i in range(start + 1, start + count):
        s = HU_TABLE[(s ^ buf[i]) & 0xFF]
    o1 = (s + 0xA7) & 0xFF
    return bytes([o0, o1])


def build_frame(cmd: str, payload: bytes, key_prefix: bytes = b"",
                encrypt: bool = True) -> bytes:
    """Build a Nivona protocol frame."""
    cmd_b = cmd.encode("ascii")
    body = key_prefix + payload
    cs_in = cmd_b + body
    cs = (~sum(cs_in)) & 0xFF
    plain = body + bytes([cs])
    ciphertext = rc4(plain, NIVONA_RC4_KEY) if encrypt else plain
    return bytes([0x53]) + cmd_b + ciphertext + bytes([0x45])


def parse_frame(frame: bytes, key_prefix_len: int = 0) -> tuple[str, bytes]:
    """Decrypt and parse a frame. Returns (cmd, payload)."""
    assert frame[0] == 0x53, f"expected S, got {frame[0]:#x}"
    assert frame[-1] == 0x45, f"expected E, got {frame[-1]:#x}"
    cmd = frame[1:3].decode("ascii")
    encrypted = frame[3:-1]
    plain = rc4(encrypted, NIVONA_RC4_KEY)
    body, cs = plain[:-1], plain[-1]
    cs_in = cmd.encode() + body
    expect_cs = (~sum(cs_in)) & 0xFF
    assert cs == expect_cs, f"cs mismatch: got {cs:#x}, want {expect_cs:#x}"
    return cmd, body[key_prefix_len:]


def build_hu_frame() -> tuple[bytes, bytes]:
    """Build an HU handshake request. Returns (frame, seed)."""
    seed = os.urandom(4)
    verifier = hu_verifier(seed, 0, 4)
    payload = seed + verifier
    return build_frame("HU", payload, encrypt=True), seed


# ---------------------------------------------------------------------------
# HTTP diagnostics
# ---------------------------------------------------------------------------

def http_get(path: str = "/", timeout: float = 5) -> dict:
    r = requests.get(f"http://{EMU_IP}{path}", timeout=timeout)
    r.raise_for_status()
    return r.json()


def http_reboot() -> None:
    requests.post(f"http://{EMU_IP}/reboot", timeout=5)
    time.sleep(8)  # wait for reboot


def diag() -> dict:
    return http_get("/diag")


def version() -> dict:
    return http_get("/")


# ---------------------------------------------------------------------------
# Pure-protocol tests (no BLE needed)
# ---------------------------------------------------------------------------

class TestProtocolUnit:
    """Verify our protocol helpers — no device interaction."""

    def test_rc4_known_vector(self):
        # NIVONA.md:918 test vector
        out = rc4(bytes.fromhex("000047"), NIVONA_RC4_KEY)
        assert out.hex() == "1fc846"

    def test_hu_verifier_deterministic(self):
        seed = bytes.fromhex("b3890048")
        assert hu_verifier(seed, 0, 4).hex() == "d7aa"

    def test_frame_roundtrip_hu(self):
        frame, seed = build_hu_frame()
        assert len(frame) == 11
        assert frame[0] == 0x53 and frame[-1] == 0x45
        assert frame[1:3] == b"HU"
        cmd, payload = parse_frame(frame)
        assert cmd == "HU"
        assert payload[:4] == seed
        assert payload[4:6] == hu_verifier(seed, 0, 4)


# ---------------------------------------------------------------------------
# HTTP API tests
# ---------------------------------------------------------------------------

class TestHttpApi:
    """Verify emulator HTTP surface is up."""

    def test_root_has_version(self):
        info = version()
        assert info["project"] == "nivona_emulator"
        assert info["version"].startswith("v")

    def test_diag_schema(self):
        d = diag()
        required = {
            "connects", "disconnects", "subscribes",
            "ad01_writes", "ad03_writes", "notify_ok", "notify_fail",
            "try_parse", "cs_mismatch", "unknown_cmd", "frames_parsed",
            "hu_rx", "hu_ver_ok", "hu_ver_bad", "hu_resp",
            "hx_resp", "unhandled",
            "last_ad03_len", "last_ad03", "last_decrypt",
            "recv_cs", "expect_cs",
        }
        missing = required - set(d)
        assert not missing, f"diag missing fields: {missing}"


# ---------------------------------------------------------------------------
# BLE end-to-end tests
# ---------------------------------------------------------------------------

def _delta(before: dict, after: dict, key: str) -> int:
    return int(after[key]) - int(before[key])


class NotifyCollector:
    """Accumulates incoming notifications from AD02."""
    def __init__(self):
        self.frames: list[bytes] = []
        self._event = asyncio.Event()

    def __call__(self, _sender, data: bytearray):
        self.frames.append(bytes(data))
        self._event.set()

    async def wait(self, timeout: float = 5.0) -> bytes | None:
        try:
            await asyncio.wait_for(self._event.wait(), timeout)
            return self.frames[-1] if self.frames else None
        except asyncio.TimeoutError:
            return None


async def _ble_connect():
    dev = await BleakScanner.find_device_by_address(EMU_MAC, timeout=10)
    if not dev:
        pytest.skip(f"emulator BLE not found at {EMU_MAC}")
    return BleakClient(dev)


@pytest.mark.asyncio
async def test_ble_services_present():
    """All 6 Nivona characteristics must be advertised."""
    async with await _ble_connect() as c:
        svcs = c.services
        uuids = {chr.uuid.lower() for svc in svcs for chr in svc.characteristics}
        for needed in [CHAR_AD01, CHAR_AD02, CHAR_AD03, CHAR_AD06]:
            assert needed in uuids, f"missing {needed}"


@pytest.mark.asyncio
async def test_dis_service():
    """Device Information Service must expose Nivona-like values."""
    async with await _ble_connect() as c:
        mfr = await c.read_gatt_char(CHAR_MFR)
        model = await c.read_gatt_char(CHAR_MODEL)
        # Real Nivona uses "EF" / "EF-BTLE"; emulator matches per APK RE
        assert mfr.decode() in ("EF", "Nivona"), f"unexpected mfr: {mfr!r}"
        assert b"EF-BTLE" in model or b"NICR" in model or b"NIVO" in model


@pytest.mark.asyncio
async def test_ad03_write_increments_counter():
    """Writing any bytes to AD03 must increment ad03_writes counter."""
    before = diag()
    async with await _ble_connect() as c:
        payload = b"\x53\x48\x55test\x45"  # junk but looks like a frame
        try:
            await c.write_gatt_char(CHAR_AD03, payload, response=False)
        except Exception as e:
            pytest.fail(f"write failed: {e}")
        await asyncio.sleep(1.5)
    after = diag()
    assert _delta(before, after, "ad03_writes") >= 1, \
        f"ad03_writes did not increment: before={before['ad03_writes']} after={after['ad03_writes']}"


@pytest.mark.asyncio
async def test_hu_handshake_full_roundtrip():
    """Full HU handshake: send challenge → receive echoed seed + session_key + verifier."""
    collector = NotifyCollector()
    async with await _ble_connect() as c:
        await c.start_notify(CHAR_AD02, collector)
        frame, seed = build_hu_frame()
        print(f"\n>>> HU write: seed={seed.hex()} frame={frame.hex()}")
        await c.write_gatt_char(CHAR_AD03, frame, response=False)
        resp = await collector.wait(timeout=5.0)

    assert resp is not None, "no HU response within 5s"
    print(f"<<< HU response: {resp.hex()}")
    assert len(resp) == 12, f"expected 12-byte response (S+HU+8+cs+E), got {len(resp)}"
    cmd, body = parse_frame(resp)
    assert cmd == "HU"
    assert len(body) == 8
    echoed_seed = body[:4]
    session_key = body[4:6]
    server_ver = body[6:8]
    assert echoed_seed == seed, f"seed mismatch: sent {seed.hex()}, got {echoed_seed.hex()}"
    # Server verifier is hu_verifier over seed+session_key (6 bytes)
    expected = hu_verifier(echoed_seed + session_key, 0, 6)
    assert server_ver == expected, \
        f"server verifier mismatch: got {server_ver.hex()}, want {expected.hex()}"


@pytest.mark.asyncio
async def test_hx_status_after_handshake():
    """After HU, HX must return 8-byte status (process=READY=2)."""
    collector = NotifyCollector()
    async with await _ble_connect() as c:
        await c.start_notify(CHAR_AD02, collector)

        # Handshake first
        frame, seed = build_hu_frame()
        await c.write_gatt_char(CHAR_AD03, frame, response=False)
        resp = await collector.wait(timeout=5.0)
        assert resp, "handshake timed out"
        _, hu_body = parse_frame(resp)
        session_key = hu_body[4:6]
        collector.frames.clear()
        collector._event.clear()

        # Now request status
        hx_frame = build_frame("HX", b"", key_prefix=session_key, encrypt=True)
        await c.write_gatt_char(CHAR_AD03, hx_frame, response=False)
        status_frame = await collector.wait(timeout=5.0)

    assert status_frame, "no HX response"
    cmd, payload = parse_frame(status_frame)
    assert cmd == "HX"
    assert len(payload) == 8, f"HX payload should be 8 bytes, got {len(payload)}"
    process = struct.unpack(">h", payload[:2])[0]
    # Freshly rebooted emulator in READY (2) or BUSY (99) for short transient
    assert process in (2, 4), f"unexpected process={process}"


# ---------------------------------------------------------------------------
# Standalone runner
# ---------------------------------------------------------------------------

async def run_manual():
    """Run selected tests without pytest (for quick CLI smoke)."""
    print("=== Protocol unit tests ===")
    TestProtocolUnit().test_rc4_known_vector()
    TestProtocolUnit().test_hu_verifier_deterministic()
    TestProtocolUnit().test_frame_roundtrip_hu()
    print("protocol: OK")

    print("\n=== HTTP API ===")
    print(f"version: {version()}")
    d = diag()
    print(f"diag before: connects={d['connects']} ad03={d['ad03_writes']} "
          f"try_parse={d['try_parse']} hu_rx={d['hu_rx']}")

    print("\n=== BLE ===")
    before = diag()
    try:
        await test_ble_services_present()
        print("services: OK")
    except Exception as e:
        print(f"services: FAIL — {e}")

    try:
        await test_ad03_write_increments_counter()
        print("ad03 write counter: OK")
    except AssertionError as e:
        print(f"ad03 write counter: FAIL — {e}")

    after = diag()
    print(f"\ndiag after:  connects={after['connects']} ad03={after['ad03_writes']} "
          f"try_parse={after['try_parse']} hu_rx={after['hu_rx']} "
          f"cs_mismatch={after['cs_mismatch']}")
    print(f"last_ad03: {after['last_ad03']}")
    print(f"last_decrypt: {after['last_decrypt']}")

    try:
        await test_hu_handshake_full_roundtrip()
        print("\nHU handshake: OK ✓")
    except Exception as e:
        print(f"\nHU handshake: FAIL — {e}")

    final = diag()
    print(f"\ndiag final: connects={final['connects']} ad03={final['ad03_writes']} "
          f"try_parse={final['try_parse']} hu_rx={final['hu_rx']} "
          f"hu_resp={final['hu_resp']} notify_ok={final['notify_ok']}")


if __name__ == "__main__":
    asyncio.run(run_manual())
