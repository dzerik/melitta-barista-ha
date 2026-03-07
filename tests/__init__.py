"""Test data and helpers for Melitta Barista Smart integration."""

from homeassistant.const import CONF_ADDRESS, CONF_NAME

MOCK_ADDRESS = "AA:BB:CC:DD:EE:FF"
MOCK_NAME = "8601ABCD1234"

MOCK_CONFIG_DATA = {
    CONF_ADDRESS: MOCK_ADDRESS,
    CONF_NAME: MOCK_NAME,
}
