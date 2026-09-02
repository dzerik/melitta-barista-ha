"""Machine-type fallback for Melitta capability resolution.

Field case (2026-09-02, live 0.91.0b1 verification): the config-entry device
name was a localized string ("Кофемашина") and the proxy advertisement had no
local_name, so ``detect_family`` never matched a Melitta prefix and
``client.capabilities`` stayed ``None`` forever. Everything legacy tolerated
that, but the UI Contract requires capabilities — the machine was permanently
``contract_not_ready``. The fix: after the HR id=6 machine-type read, a
Melitta-brand client falls back to a machine-type-derived family. Nivona is
deliberately excluded (families differ materially; a wrong guess is harmful —
see the family-700 process-code case).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from custom_components.melitta_barista.ble_client import MelittaBleClient
from custom_components.melitta_barista.brands import get_profile
from custom_components.melitta_barista.const import MachineType
from custom_components.melitta_barista.ui_contract import (
    compute_contract_fingerprint,
)

ADDRESS = "AA:BB:CC:DD:EE:FF"


class TestFallbackHelper:
    def test_barista_ts_machine_type_maps_to_ts_family(self):
        client = MelittaBleClient(ADDRESS)
        client._machine_type = MachineType.BARISTA_TS
        caps = client._fallback_capabilities()
        assert caps is not None
        assert caps.family_key == "barista_ts"

    def test_barista_t_machine_type_maps_to_t_family(self):
        client = MelittaBleClient(ADDRESS)
        client._machine_type = MachineType.BARISTA_T
        caps = client._fallback_capabilities()
        assert caps is not None
        assert caps.family_key == "barista_t"

    def test_unknown_machine_type_defaults_to_ts(self):
        """machine_type None (HR id=6 unanswered) still yields the TS set —
        consistent with get_available_recipes(None) exposing all recipes."""
        client = MelittaBleClient(ADDRESS)
        client._machine_type = None
        caps = client._fallback_capabilities()
        assert caps is not None
        assert caps.family_key == "barista_ts"

    def test_nivona_brand_never_falls_back(self):
        client = MelittaBleClient(ADDRESS, brand=get_profile("nivona"))
        client._machine_type = None
        assert client._fallback_capabilities() is None


class TestConnectWiring:
    async def test_connect_applies_fallback_when_name_detection_fails(self):
        """Full _connect_impl: localized device name, HR answers TS →
        capabilities resolved via fallback and the contract fingerprint
        becomes computable without a reconnect."""
        client = MelittaBleClient(ADDRESS, device_name="Кофемашина")
        with patch.object(client, "_try_connect_and_handshake",
                          new=AsyncMock(return_value=True)), \
                patch.object(client, "_read_dis_service", new=AsyncMock()), \
                patch.object(client, "_protocol") as proto:
            proto.read_version = AsyncMock(return_value="1.0")
            proto.read_serial = AsyncMock(return_value=None)
            proto.read_features = AsyncMock(return_value=None)
            proto.read_numerical = AsyncMock(
                return_value=int(MachineType.BARISTA_TS),
            )
            proto.set_family = MagicMock()
            assert await client._connect_impl() is True
        assert client.capabilities is not None
        assert client.capabilities.family_key == "barista_ts"
        proto.set_family.assert_called_with("barista_ts")
        assert compute_contract_fingerprint(client) is not None

    async def test_connect_prefers_name_detection_over_fallback(self):
        """A properly named machine keeps the prefix-detected family even
        when the HR machine-type answer would suggest another one."""
        client = MelittaBleClient(ADDRESS, device_name="83012345 Barista T")
        with patch.object(client, "_try_connect_and_handshake",
                          new=AsyncMock(return_value=True)), \
                patch.object(client, "_read_dis_service", new=AsyncMock()), \
                patch.object(client, "_protocol") as proto:
            proto.read_version = AsyncMock(return_value="1.0")
            proto.read_serial = AsyncMock(return_value=None)
            proto.read_features = AsyncMock(return_value=None)
            proto.read_numerical = AsyncMock(
                return_value=int(MachineType.BARISTA_TS),
            )
            proto.set_family = MagicMock()
            assert await client._connect_impl() is True
        assert client.capabilities is not None
        assert client.capabilities.family_key == "barista_t"
