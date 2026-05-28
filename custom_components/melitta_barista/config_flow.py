"""Config flow for the multi-brand coffee-machine integration (Melitta / Nivona)."""

import logging
from typing import Any

import voluptuous as vol
from bleak import BleakScanner
from bleak.exc import BleakError
from homeassistant.components.bluetooth import (
    BluetoothServiceInfoBleak,
    async_discovered_service_info,
)
from homeassistant.config_entries import ConfigFlow, OptionsFlow, ConfigEntry
from homeassistant.const import CONF_ADDRESS, CONF_NAME
from homeassistant.core import callback
from homeassistant.config_entries import ConfigFlowResult

from .ble_client import MELITTA_SERVICE_UUID
from .const import (
    BLE_PREFIXES_ALL,
    DOMAIN,
    CONF_POLL_INTERVAL,
    CONF_RECONNECT_DELAY,
    CONF_RECONNECT_MAX_DELAY,
    CONF_MAX_CONSECUTIVE_ERRORS,
    CONF_FRAME_TIMEOUT,
    CONF_BLE_CONNECT_TIMEOUT,
    CONF_PAIR_TIMEOUT,
    CONF_RECIPE_RETRIES,
    CONF_INITIAL_CONNECT_DELAY,
    CONF_AUTO_CONFIRM_PROMPTS,
    CONF_BRAND,
    CONF_FAMILY_OVERRIDE,
    CONF_AUTO_SYNC_CLOCK,
    CONF_AUTO_SYNC_DRIFT_MINUTES,
    CONF_AUTO_SYNC_DAILY_TIME,
    DEFAULT_POLL_INTERVAL,
    DEFAULT_RECONNECT_DELAY,
    DEFAULT_RECONNECT_MAX_DELAY,
    DEFAULT_MAX_CONSECUTIVE_ERRORS,
    DEFAULT_FRAME_TIMEOUT,
    DEFAULT_BLE_CONNECT_TIMEOUT,
    DEFAULT_PAIR_TIMEOUT,
    DEFAULT_RECIPE_RETRIES,
    DEFAULT_INITIAL_CONNECT_DELAY,
    DEFAULT_AUTO_CONFIRM_PROMPTS,
    DEFAULT_AUTO_SYNC_CLOCK,
    DEFAULT_AUTO_SYNC_DRIFT_MINUTES,
    DEFAULT_AUTO_SYNC_DAILY_TIME,
)

_LOGGER = logging.getLogger("melitta_barista")

PAIR_TIMEOUT = DEFAULT_PAIR_TIMEOUT


def _validate_hhmm(value: object) -> str:
    """Voluptuous validator for HH:MM strings.

    Accepts any HH:MM where 00 <= HH <= 23 and 00 <= MM <= 59.
    Canonicalises single-digit inputs: ``"9:5"`` → ``"09:05"``.
    Raises ``vol.Invalid`` for anything outside that range or wrong shape.
    """
    if not isinstance(value, str):
        raise vol.Invalid("must be a string")
    parts = value.split(":")
    if len(parts) != 2 or not all(p.isdigit() for p in parts):
        raise vol.Invalid("must be HH:MM")
    h, m = int(parts[0]), int(parts[1])
    if not (0 <= h <= 23 and 0 <= m <= 59):
        raise vol.Invalid("must be in 00:00..23:59")
    return f"{h:02d}:{m:02d}"

# Neutral fallback shown when brand cannot be inferred from the BLE
# advertisement (e.g. manual MAC entry before connection).
_FALLBACK_NAME = "Smart Coffee Machine"


def _suggested_name(local_name: str | None) -> str:
    """Derive a user-facing default name from the BLE advertisement.

    If the local_name matches a registered brand profile, use the brand
    name prefix (``"Melitta Smart Coffee Machine"`` /
    ``"Nivona Smart Coffee Machine"``); otherwise fall back to a neutral
    label so we never mis-brand a device we have not identified.
    """
    from .brands import detect_from_advertisement  # noqa: PLC0415
    profile = detect_from_advertisement(local_name) if local_name else None
    if profile is None:
        return _FALLBACK_NAME
    return f"{profile.brand_name} {_FALLBACK_NAME}"


def _describe_advertisement(
    local_name: str | None,
) -> dict[str, str]:
    """Extract everything we can know about a device before connecting.

    Returns a dict with ``brand`` / ``model`` / ``family`` / ``display``
    keys — all strings (possibly empty). ``display`` is the compact
    human label used in the discovery picker and form placeholders
    (e.g. ``"Nivona NICR 8107"`` or ``"Unknown"``).
    """
    from .brands import detect_from_advertisement  # noqa: PLC0415

    if not local_name:
        return {"brand": "", "model": "", "family": "", "display": "Unknown"}

    profile = detect_from_advertisement(local_name)
    if profile is None:
        return {
            "brand": "",
            "model": "",
            "family": "",
            "display": local_name,
        }

    family = profile.detect_family(local_name) or ""
    try:
        caps = profile.capabilities_for(family) if family else None
    except KeyError:
        caps = None

    model = caps.model_name if caps is not None else ""
    display = (
        f"{profile.brand_name} {model}".strip() if model
        else profile.brand_name
    )
    return {
        "brand": profile.brand_name,
        "model": model,
        "family": family,
        "display": display,
    }


class MelittaBaristaConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the config flow for Melitta and Nivona coffee machines."""

    VERSION = 3

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        """Get the options flow handler."""
        return MelittaOptionsFlow(config_entry)

    def __init__(self) -> None:
        self._discovered_devices: dict[str, str] = {}
        self._address: str | None = None
        self._name: str | None = None

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle user-initiated setup."""
        errors: dict[str, str] = {}

        if user_input is not None:
            if user_input.get(CONF_ADDRESS) == "manual":
                return await self.async_step_manual()

            address = user_input[CONF_ADDRESS]
            # _discovered_devices values are the raw advertisement
            # local_names — if the entry is missing, fall back to a
            # brand-neutral label.
            name = self._discovered_devices.get(address, _FALLBACK_NAME)

            await self.async_set_unique_id(address.replace(":", "").lower())
            self._abort_if_unique_id_configured()

            self._address = address
            self._name = name
            return await self.async_step_pair()

        # Scan for devices
        await self._async_discover_devices()

        if not self._discovered_devices:
            return await self.async_step_manual()

        # Picker labels: "Nivona NICR 8107 · 8107000001----- · MAC".
        # Falls back to "name (MAC)" when the advertisement can't be
        # resolved to a known brand/model.
        options: dict[str, str] = {}
        for addr, name in self._discovered_devices.items():
            desc = _describe_advertisement(name)
            if desc["display"] and desc["display"] != name:
                options[addr] = f"{desc['display']} · {name} · {addr}"
            else:
                options[addr] = f"{name} ({addr})"
        options["manual"] = "Enter address manually..."

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({
                vol.Required(CONF_ADDRESS): vol.In(options),
            }),
            errors=errors,
        )

    async def _async_discover_devices(self) -> None:
        """Discover supported coffee machines via HA bluetooth and BLE scan."""
        self._discovered_devices = {}

        # Try HA bluetooth integration first
        try:
            for info in async_discovered_service_info(self.hass):
                for uuid in info.service_uuids:
                    if MELITTA_SERVICE_UUID in uuid.lower():
                        # If the peripheral didn't advertise a local_name,
                        # label it with the detected brand when possible,
                        # else a neutral placeholder with the MAC.
                        fallback = f"{_suggested_name(None)} ({info.address})"
                        self._discovered_devices[info.address] = (
                            info.name or fallback
                        )
        except (AttributeError, ValueError):
            _LOGGER.debug("HA bluetooth not available, falling back to direct scan")

        # Fallback: direct BLE scan. This bypasses HA's bluetooth integration
        # and therefore the ESPHome BLE proxy too — in a proxy-only setup it
        # will fail with OSError/BleakError because there is no local
        # adapter. We log loudly so the empty-discovery case is debuggable
        # but keep the call as a last resort for hosts where HA's bluetooth
        # integration hasn't picked up the adapter yet.
        if not self._discovered_devices:
            _LOGGER.warning(
                "HA bluetooth returned no matching devices; falling back to a "
                "direct BleakScanner. This bypasses HA's bluetooth integration "
                "(including ESPHome BLE proxies) and may fail without a local "
                "Bluetooth adapter."
            )
            from .brands import detect_from_advertisement  # noqa: PLC0415
            try:
                devices = await BleakScanner.discover(timeout=10.0)
                for device in devices:
                    if not device.name:
                        continue
                    # Match either brand's advertisement regex, or the
                    # legacy Melitta prefix set / "melitta"/"barista"
                    # substrings (pre-regex discovery).
                    if (
                        detect_from_advertisement(device.name) is not None
                        or any(device.name.startswith(p) for p in BLE_PREFIXES_ALL)
                        or "melitta" in device.name.lower()
                        or "barista" in device.name.lower()
                        or "nivona" in device.name.lower()
                    ):
                        self._discovered_devices[device.address] = device.name
            except (OSError, BleakError):
                _LOGGER.exception(
                    "Fallback BLE scan failed (likely no local adapter); "
                    "use Manual entry to add the machine by MAC"
                )

    async def async_step_manual(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle manual address entry."""
        errors: dict[str, str] = {}

        if user_input is not None:
            address = user_input[CONF_ADDRESS].strip().upper()
            name = user_input.get(CONF_NAME, _FALLBACK_NAME)

            # Basic MAC address validation
            if len(address.replace(":", "").replace("-", "")) != 12:
                errors[CONF_ADDRESS] = "invalid_address"
            else:
                # Normalize address format
                clean = address.replace(":", "").replace("-", "")
                address = ":".join(clean[i:i+2] for i in range(0, 12, 2))

                await self.async_set_unique_id(clean.lower())
                self._abort_if_unique_id_configured()

                self._address = address
                self._name = name
                return await self.async_step_pair()

        return self.async_show_form(
            step_id="manual",
            data_schema=vol.Schema({
                vol.Required(CONF_ADDRESS): str,
                vol.Optional(CONF_NAME, default=_FALLBACK_NAME): str,
            }),
            errors=errors,
        )

    async def async_step_bluetooth(
        self, discovery_info: BluetoothServiceInfoBleak
    ) -> ConfigFlowResult:
        """Handle bluetooth discovery."""
        address = discovery_info.address
        name = discovery_info.name or _suggested_name(None)

        await self.async_set_unique_id(address.replace(":", "").lower())
        self._abort_if_unique_id_configured()

        self._address = address
        self._name = name

        return await self.async_step_bluetooth_confirm()

    async def async_step_bluetooth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm bluetooth discovery."""
        if user_input is not None:
            return await self.async_step_pair()

        desc = _describe_advertisement(self._name)
        return self.async_show_form(
            step_id="bluetooth_confirm",
            description_placeholders={
                "name": self._name or _FALLBACK_NAME,
                "brand": desc["brand"] or _FALLBACK_NAME,
                "model": desc["model"] or "—",
                "address": self._address or "",
            },
        )

    async def async_step_pair(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Pair with the coffee machine via BLE."""
        errors: dict[str, str] = {}

        if user_input is not None:
            # User pressed submit — attempt pairing
            pair_result = await self._async_try_pair()

            if pair_result == "ok":
                from .brands import detect_from_advertisement  # noqa: PLC0415
                from .const import CONF_BRAND, DEFAULT_BRAND  # noqa: PLC0415

                profile = detect_from_advertisement(self._name)
                brand_slug = profile.brand_slug if profile else DEFAULT_BRAND
                # Prefer a human title like "Nivona NICR 8107" over the
                # raw advertisement local_name "8107000001-----"; fall
                # back to the suggested neutral label if brand/model
                # detection failed.
                desc = _describe_advertisement(self._name)
                friendly = desc["display"] or _suggested_name(self._name)
                if not desc["brand"]:
                    friendly = _suggested_name(self._name)
                return self.async_create_entry(
                    title=friendly,
                    data={
                        CONF_ADDRESS: self._address,
                        CONF_NAME: friendly,
                        CONF_BRAND: brand_slug,
                    },
                )
            else:
                errors["base"] = pair_result

        desc = _describe_advertisement(self._name)
        return self.async_show_form(
            step_id="pair",
            description_placeholders={
                "name": self._name or _FALLBACK_NAME,
                "brand": desc["brand"] or _FALLBACK_NAME,
                "model": desc["model"] or "—",
                "address": self._address or "",
            },
            errors=errors,
        )

    async def _async_try_pair(self) -> str:
        """Attempt pre-pairing with the device.

        Tries D-Bus BlueZ Agent1 pairing first (works with local BT adapter).
        If D-Bus is unavailable (ESPHome proxy, non-Linux, container),
        returns "ok" — pairing will be handled automatically by Bleak's
        pair=True parameter during the actual BLE connection.
        """
        if not self._address:
            return "cannot_connect"

        try:
            from .ble_agent import async_pair_device
        except ImportError:
            _LOGGER.info(
                "dbus-fast not available — pairing will be handled "
                "automatically by Bleak on connect (pair=True)"
            )
            return "ok"

        return await async_pair_device(self._address, timeout=PAIR_TIMEOUT)

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle reconfiguration (change BLE address or device name)."""
        errors: dict[str, str] = {}
        entry = self._get_reconfigure_entry()

        if user_input is not None:
            address = user_input[CONF_ADDRESS].strip().upper()
            name = user_input.get(CONF_NAME, entry.data.get(CONF_NAME, ""))

            if len(address.replace(":", "").replace("-", "")) != 12:
                errors[CONF_ADDRESS] = "invalid_address"
            else:
                clean = address.replace(":", "").replace("-", "")
                address = ":".join(clean[i:i+2] for i in range(0, 12, 2))

                await self.async_set_unique_id(clean.lower())
                self._abort_if_unique_id_mismatch()

                return self.async_update_reload_and_abort(
                    entry,
                    data_updates={CONF_ADDRESS: address, CONF_NAME: name},
                )

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=vol.Schema({
                vol.Required(
                    CONF_ADDRESS,
                    default=entry.data.get(CONF_ADDRESS, ""),
                ): str,
                vol.Optional(
                    CONF_NAME,
                    default=entry.data.get(CONF_NAME, _FALLBACK_NAME),
                ): str,
            }),
            errors=errors,
        )


class MelittaOptionsFlow(OptionsFlow):
    """Handle the options flow for the coffee-machine integration."""

    def __init__(self, config_entry: ConfigEntry) -> None:
        self._config_entry = config_entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show options menu."""
        return self.async_show_menu(
            step_id="init",
            menu_options=["basic", "advanced", "repair", "full_pair"],
        )

    async def async_step_full_pair(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Hard pairing recovery — wipe ESP bond table AND reload proxy.

        Use this when soft "Repair connection" doesn't recover the
        wedge — the proxy is holding a stale LTK that the machine
        refuses, and only a fresh SMP exchange will accept it.

        Requires the `clear_ble_bonds` action wired in the user's
        ESPHome YAML (see esphome/ble-proxy-xiao-c6.yaml in the repo).
        Without it we still do the soft path and show an instructive
        message telling the user to add the action.
        """
        if user_input is not None:
            from . import _async_force_repair  # noqa: PLC0415

            try:
                result = await _async_force_repair(self.hass, self._config_entry)
            except Exception:
                _LOGGER.exception("Manual full_pair failed")
                return self.async_abort(reason="full_pair_failed")

            if not result["proxy_reloaded"]:
                # No proxy found — local-adapter fallback.
                return self.async_abort(reason="full_pair_local_only")
            if result["service_missing"]:
                return self.async_abort(
                    reason="full_pair_no_action",
                    description_placeholders={
                        "service": result["service_name"] or "unknown",
                    },
                )
            if result["bond_cleared"]:
                return self.async_abort(reason="full_pair_done")
            return self.async_abort(reason="full_pair_partial")

        return self.async_show_form(
            step_id="full_pair",
            data_schema=vol.Schema({}),
        )

    async def async_step_repair(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manual pairing recovery — same routine as the auto-trigger.

        See `_async_repair_pairing` in __init__.py for the full mechanism.
        TL;DR: reloads the ESPHome BLE proxy config entry that owns the
        scanner for this machine, which evicts the cached BLEDevice from
        habluetooth's `_previous_service_info`. Next advertisement
        rebuilds it with fresh `details["source"]` / `details["address_type"]`
        and the next `pair=True` succeeds.
        """
        if user_input is not None:
            # Run the same routine the reconnect loop calls automatically.
            # Importing lazily to avoid a circular import at module-load.
            from . import _async_repair_pairing  # noqa: PLC0415

            try:
                proxy_reloaded = await _async_repair_pairing(
                    self.hass, self._config_entry,
                )
            except Exception:
                _LOGGER.exception("Manual repair_connection failed")
                return self.async_abort(reason="repair_failed")
            return self.async_abort(
                reason=(
                    "repair_proxy_reloaded" if proxy_reloaded
                    else "repair_local_reconnect"
                ),
            )

        # First time through: show a confirmation form so the user
        # explicitly opts in (the routine briefly disconnects + reloads
        # the ESPHome entry which can affect other peers on the same
        # proxy).
        return self.async_show_form(
            step_id="repair",
            data_schema=vol.Schema({}),
        )

    async def async_step_basic(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle basic options."""
        if user_input is not None:
            new_options = {**self._config_entry.options, **user_input}
            return self.async_create_entry(title="", data=new_options)

        options = self._config_entry.options
        return self.async_show_form(
            step_id="basic",
            data_schema=vol.Schema({
                vol.Optional(
                    CONF_POLL_INTERVAL,
                    default=options.get(CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL),
                ): vol.All(vol.Coerce(float), vol.Range(min=1.0, max=60.0)),
                vol.Optional(
                    CONF_RECONNECT_DELAY,
                    default=options.get(CONF_RECONNECT_DELAY, DEFAULT_RECONNECT_DELAY),
                ): vol.All(vol.Coerce(float), vol.Range(min=1.0, max=60.0)),
                vol.Optional(
                    CONF_RECONNECT_MAX_DELAY,
                    default=options.get(CONF_RECONNECT_MAX_DELAY, DEFAULT_RECONNECT_MAX_DELAY),
                ): vol.All(vol.Coerce(float), vol.Range(min=30.0, max=3600.0)),
                vol.Optional(
                    CONF_MAX_CONSECUTIVE_ERRORS,
                    default=options.get(CONF_MAX_CONSECUTIVE_ERRORS, DEFAULT_MAX_CONSECUTIVE_ERRORS),
                ): vol.All(int, vol.Range(min=1, max=20)),
                vol.Optional(
                    CONF_FRAME_TIMEOUT,
                    default=options.get(CONF_FRAME_TIMEOUT, DEFAULT_FRAME_TIMEOUT),
                ): vol.All(int, vol.Range(min=2, max=30)),
                vol.Optional(
                    CONF_AUTO_CONFIRM_PROMPTS,
                    default=options.get(
                        CONF_AUTO_CONFIRM_PROMPTS, DEFAULT_AUTO_CONFIRM_PROMPTS,
                    ),
                ): bool,
                vol.Optional(
                    CONF_FAMILY_OVERRIDE,
                    default=options.get(CONF_FAMILY_OVERRIDE, ""),
                ): self._family_override_selector(),
            }),
        )

    def _family_override_selector(self):
        """Build a brand-aware family-key dropdown (empty = auto-detect)."""
        from .brands import get_profile  # noqa: PLC0415
        brand_slug = self._config_entry.data.get(CONF_BRAND, "melitta")
        try:
            profile = get_profile(brand_slug)
            families = sorted(profile.families.keys())
        except Exception:
            families = []
        return vol.In([""] + families)

    async def async_step_advanced(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle advanced options."""
        if user_input is not None:
            new_options = {**self._config_entry.options, **user_input}
            return self.async_create_entry(title="", data=new_options)

        options = self._config_entry.options
        return self.async_show_form(
            step_id="advanced",
            data_schema=vol.Schema({
                vol.Optional(
                    CONF_BLE_CONNECT_TIMEOUT,
                    default=options.get(CONF_BLE_CONNECT_TIMEOUT, DEFAULT_BLE_CONNECT_TIMEOUT),
                ): vol.All(vol.Coerce(float), vol.Range(min=5.0, max=60.0)),
                vol.Optional(
                    CONF_PAIR_TIMEOUT,
                    default=options.get(CONF_PAIR_TIMEOUT, DEFAULT_PAIR_TIMEOUT),
                ): vol.All(vol.Coerce(float), vol.Range(min=10.0, max=120.0)),
                vol.Optional(
                    CONF_RECIPE_RETRIES,
                    default=options.get(CONF_RECIPE_RETRIES, DEFAULT_RECIPE_RETRIES),
                ): vol.All(int, vol.Range(min=1, max=10)),
                vol.Optional(
                    CONF_INITIAL_CONNECT_DELAY,
                    default=options.get(CONF_INITIAL_CONNECT_DELAY, DEFAULT_INITIAL_CONNECT_DELAY),
                ): vol.All(vol.Coerce(float), vol.Range(min=0.0, max=30.0)),
                vol.Optional(
                    CONF_AUTO_SYNC_CLOCK,
                    default=options.get(CONF_AUTO_SYNC_CLOCK, DEFAULT_AUTO_SYNC_CLOCK),
                ): bool,
                vol.Optional(
                    CONF_AUTO_SYNC_DRIFT_MINUTES,
                    default=options.get(
                        CONF_AUTO_SYNC_DRIFT_MINUTES, DEFAULT_AUTO_SYNC_DRIFT_MINUTES,
                    ),
                ): vol.All(vol.Coerce(int), vol.Range(min=0, max=60)),
                vol.Optional(
                    CONF_AUTO_SYNC_DAILY_TIME,
                    default=options.get(
                        CONF_AUTO_SYNC_DAILY_TIME, DEFAULT_AUTO_SYNC_DAILY_TIME,
                    ),
                ): vol.All(str, _validate_hhmm),
            }),
        )
