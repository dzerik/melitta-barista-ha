"""Tests for protocol.py — frame building, parsing, data structures."""

import struct

from custom_components.melitta_barista.const import (
    InfoMessage,
    MachineProcess,
    Manipulation,
    SubProcess,
)
from custom_components.melitta_barista.protocol import (
    KNOWN_COMMANDS,
    MachineRecipe,
    MachineStatus,
    NumericalValue,
    AlphanumericValue,
    RecipeComponent,
    _calculate_checksum,
    _compute_handshake_crc,
    _rc4_crypt,
)


class TestRC4:
    def test_encrypt_decrypt_roundtrip(self):
        key = b"testkey123"
        plaintext = b"Hello, Melitta!"
        encrypted = _rc4_crypt(plaintext, key)
        assert encrypted != plaintext
        decrypted = _rc4_crypt(encrypted, key)
        assert decrypted == plaintext

    def test_empty_data(self):
        key = b"key"
        assert _rc4_crypt(b"", key) == b""

    def test_single_byte(self):
        key = b"k"
        enc = _rc4_crypt(b"\x42", key)
        dec = _rc4_crypt(enc, key)
        assert dec == b"\x42"


class TestChecksum:
    def test_basic_checksum(self):
        frame = bytes([0x53, 0x10, 0x20, 0x30])
        cs = _calculate_checksum(frame, 3)
        expected = (~(0x10 + 0x20)) & 0xFF
        assert cs == expected

    def test_single_byte_payload(self):
        frame = bytes([0x53, 0xFF])
        cs = _calculate_checksum(frame, 2)
        assert cs == (~0xFF) & 0xFF
        assert cs == 0x00


class TestHandshakeCRC:
    def test_crc_returns_2_bytes(self):
        data = b"\x01\x02\x03\x04"
        crc = _compute_handshake_crc(len(data), data)
        assert len(crc) == 2
        assert isinstance(crc, bytes)

    def test_crc_deterministic(self):
        data = b"\xAB\xCD\xEF\x01"
        crc1 = _compute_handshake_crc(len(data), data)
        crc2 = _compute_handshake_crc(len(data), data)
        assert crc1 == crc2

    def test_crc_varies_with_data(self):
        crc1 = _compute_handshake_crc(4, b"\x00\x00\x00\x00")
        crc2 = _compute_handshake_crc(4, b"\xFF\xFF\xFF\xFF")
        assert crc1 != crc2


class TestMachineStatus:
    def test_from_payload_ready(self):
        data = struct.pack(">hhBBh", MachineProcess.READY, 0, 0, 0, 0)
        status = MachineStatus.from_payload(data)
        assert status.process == MachineProcess.READY
        assert status.is_ready is True
        assert status.is_brewing is False

    def test_from_payload_brewing(self):
        data = struct.pack(
            ">hhBBh",
            MachineProcess.PRODUCT,
            SubProcess.GRINDING,
            0, 0, 50,
        )
        status = MachineStatus.from_payload(data)
        assert status.process == MachineProcess.PRODUCT
        assert status.sub_process == SubProcess.GRINDING
        assert status.is_brewing is True
        assert status.progress == 50

    def test_from_payload_with_manipulation(self):
        data = struct.pack(
            ">hhBBh",
            MachineProcess.READY, 0, 0, Manipulation.FILL_WATER, 0,
        )
        status = MachineStatus.from_payload(data)
        assert status.manipulation == Manipulation.FILL_WATER
        assert status.is_ready is False  # has manipulation

    def test_from_payload_with_info_messages(self):
        data = struct.pack(
            ">hhBBh",
            MachineProcess.READY, 0,
            InfoMessage.FILL_BEANS_1 | InfoMessage.EASY_CLEAN,
            0, 0,
        )
        status = MachineStatus.from_payload(data)
        assert InfoMessage.FILL_BEANS_1 in status.info_messages
        assert InfoMessage.EASY_CLEAN in status.info_messages

    def test_from_payload_short_data(self):
        status = MachineStatus.from_payload(b"\x00\x00")
        assert status.process is None

    def test_from_payload_unknown_process(self):
        data = struct.pack(">hhBBh", 999, 0, 0, 0, 0)
        status = MachineStatus.from_payload(data)
        assert status.process is None


class TestRecipeComponent:
    def test_roundtrip(self):
        comp = RecipeComponent(
            process=1, shots=2, blend=1, intensity=3,
            aroma=1, temperature=2, portion=20, reserve=0,
        )
        data = comp.to_bytes()
        assert len(data) == 8
        restored = RecipeComponent.from_bytes(data)
        assert restored.process == 1
        assert restored.shots == 2
        assert restored.intensity == 3
        assert restored.portion == 20

    def test_portion_ml(self):
        comp = RecipeComponent(portion=24)
        assert comp.portion_ml == 120

    def test_default_values(self):
        comp = RecipeComponent()
        assert comp.process == 0
        assert comp.shots == 1
        assert comp.blend == 1
        assert comp.intensity == 2


class TestMachineRecipe:
    def test_from_payload(self):
        recipe_id = 200
        recipe_type = 0
        comp1_bytes = RecipeComponent(process=1, shots=1, intensity=3).to_bytes()
        comp2_bytes = RecipeComponent(process=0).to_bytes()
        # HC response: recipe_id(2) + recipe_type(1) + comp1(8) + comp2(8) — no recipe_key
        payload = struct.pack(">hB", recipe_id, recipe_type) + comp1_bytes + comp2_bytes
        recipe = MachineRecipe.from_payload(payload)
        assert recipe.recipe_id == 200
        assert recipe.recipe_type == 0
        assert recipe.component1.intensity == 3
        assert recipe.component2.process == 0


class TestNumericalValue:
    def test_from_payload(self):
        payload = struct.pack(">hi", 11, 3)
        nv = NumericalValue.from_payload(payload)
        assert nv.value_id == 11
        assert nv.value == 3

    def test_negative_value(self):
        payload = struct.pack(">hi", 22, -1)
        nv = NumericalValue.from_payload(payload)
        assert nv.value == -1


class TestAlphanumericValue:
    def test_from_payload(self):
        text = "Hello"
        payload = struct.pack(">h", 310) + text.encode("utf-8") + b"\x00" * 10
        av = AlphanumericValue.from_payload(payload)
        assert av.value_id == 310
        assert av.value == "Hello"

    def test_from_payload_empty(self):
        payload = struct.pack(">h", 310) + b"\x00" * 10
        av = AlphanumericValue.from_payload(payload)
        assert av.value == ""


class TestKnownCommands:
    def test_all_commands_present(self):
        expected = {"A", "N", "HA", "HC", "HR", "HV", "HX", "HU", "HF", "HL", "HQ", "HP"}
        assert set(KNOWN_COMMANDS.keys()) == expected
