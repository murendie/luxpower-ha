"""Constants for the LuxPower integration."""

from __future__ import annotations

DOMAIN = "luxpower"

CONF_DONGLE_SERIAL = "dongle_serial"
CONF_INVERTER_SERIAL = "inverter_serial"
CONF_SCAN_INTERVAL = "scan_interval"

DEFAULT_HOST = "192.168.1.100"
DEFAULT_PORT = 8000
DEFAULT_SCAN_INTERVAL = 30
MIN_SCAN_INTERVAL = 5
MAX_SCAN_INTERVAL = 600

#: How long the config flow listens to the dongle's own traffic for serials.
DISCOVERY_TIMEOUT = 30

#: Shorter cap for the opt-in "detect serials" step in the config flow. The dongle
#: only volunteers heartbeats and push frames on its own schedule, so detection is
#: unreliable by nature -- keep the wait short and let the user type the serials.
DETECT_TIMEOUT = 15

MANUFACTURER = "LuxPower"
MODEL = "Hybrid inverter"
