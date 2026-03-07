"""Configure test environment — mock homeassistant and bleak modules."""

import sys
from types import ModuleType
from unittest.mock import MagicMock

# Mock homeassistant and its submodules so const.py / protocol.py can be imported
# without a full HA installation.
_HA_MODULES = [
    "homeassistant",
    "homeassistant.config_entries",
    "homeassistant.const",
    "homeassistant.core",
    "homeassistant.components",
    "homeassistant.components.bluetooth",
    "homeassistant.components.sensor",
    "homeassistant.components.button",
    "homeassistant.components.select",
    "homeassistant.components.number",
    "homeassistant.components.switch",
    "homeassistant.components.text",
    "homeassistant.data_entry_flow",
    "homeassistant.helpers",
    "homeassistant.helpers.entity",
    "homeassistant.helpers.entity_platform",
]

for mod_name in _HA_MODULES:
    if mod_name not in sys.modules:
        mock_mod = ModuleType(mod_name)
        # Add common attributes that imports expect
        mock_mod.__dict__.setdefault("ConfigEntry", MagicMock())
        mock_mod.__dict__.setdefault("ConfigFlow", MagicMock())
        mock_mod.__dict__.setdefault("FlowResult", MagicMock())
        mock_mod.__dict__.setdefault("HomeAssistant", MagicMock())
        mock_mod.__dict__.setdefault("callback", lambda f: f)
        mock_mod.__dict__.setdefault("Platform", MagicMock())
        mock_mod.__dict__.setdefault("SensorEntity", MagicMock())
        mock_mod.__dict__.setdefault("ButtonEntity", MagicMock())
        mock_mod.__dict__.setdefault("SelectEntity", MagicMock())
        mock_mod.__dict__.setdefault("NumberEntity", MagicMock())
        mock_mod.__dict__.setdefault("NumberMode", MagicMock())
        mock_mod.__dict__.setdefault("SwitchEntity", MagicMock())
        mock_mod.__dict__.setdefault("TextEntity", MagicMock())
        mock_mod.__dict__.setdefault("DeviceInfo", MagicMock())
        mock_mod.__dict__.setdefault("EntityCategory", MagicMock())
        mock_mod.__dict__.setdefault("AddEntitiesCallback", MagicMock())
        mock_mod.__dict__.setdefault("BleakClient", MagicMock())
        mock_mod.__dict__.setdefault("BleakScanner", MagicMock())
        mock_mod.__dict__.setdefault("async_discovered_service_info", MagicMock(return_value=[]))
        mock_mod.__dict__.setdefault("async_ble_device_from_address", MagicMock(return_value=None))
        mock_mod.__dict__.setdefault("async_register_callback", MagicMock(return_value=lambda: None))
        mock_mod.__dict__.setdefault("BluetoothCallbackMatcher", MagicMock())
        mock_mod.__dict__.setdefault("BluetoothScanningMode", MagicMock())
        mock_mod.__dict__.setdefault("BluetoothServiceInfoBleak", MagicMock())
        mock_mod.__dict__.setdefault("BluetoothChange", MagicMock())
        mock_mod.__dict__.setdefault("UnitOfTime", MagicMock())
        mock_mod.__dict__.setdefault("CONF_ADDRESS", "address")
        mock_mod.__dict__.setdefault("CONF_NAME", "name")
        mock_mod.__dict__.setdefault("vol", MagicMock())
        sys.modules[mod_name] = mock_mod

# Mock voluptuous
if "voluptuous" not in sys.modules:
    sys.modules["voluptuous"] = ModuleType("voluptuous")
