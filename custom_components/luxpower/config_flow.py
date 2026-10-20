"""Config flow for the LuxPower integration.

Both serials end up required in the entry, but detection does real work now.

Verified against the hardware: the dongle ignores the inverter serial in a
request while validating its own, so given the dongle serial (printed on its
sticker) the inverter serial can be read straight out of registers 115-119.
Passive listening is only used as a fallback for the dongle serial, because the
dongle volunteers its heartbeats on its own schedule and once said nothing at all
for 30 seconds.
"""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, OptionsFlow
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.core import callback
from homeassistant.helpers import config_validation as cv

from .const import (
    CONF_DONGLE_SERIAL,
    CONF_INVERTER_SERIAL,
    CONF_SCAN_INTERVAL,
    DEFAULT_HOST,
    DEFAULT_PORT,
    DEFAULT_SCAN_INTERVAL,
    DETECT_TIMEOUT,
    DOMAIN,
    MAX_SCAN_INTERVAL,
    MIN_SCAN_INTERVAL,
)
from .protocol import (
    LuxPowerClient,
    LuxPowerConnectionError,
    LuxPowerError,
    discover_serials,
    read_inverter_serial,
)

_LOGGER = logging.getLogger(__name__)


async def _async_validate(
    host: str, port: int, dongle: str, inverter: str
) -> dict[int, int]:
    """Prove the dongle answers a real read, then let go of the connection.

    The connection is always closed before this returns, so setup can open its
    own without fighting the config flow for the dongle's single client slot.
    """
    client = LuxPowerClient(host, port, dongle, inverter)
    try:
        registers = await client.read_input(0, 40)
    finally:
        await client.disconnect()

    if not registers:
        raise LuxPowerError("the dongle accepted the read but returned no registers")
    return registers


def _detect_schema(defaults: dict[str, Any]) -> vol.Schema:
    """Host, port, and the dongle serial -- optional, but it makes this reliable."""
    return vol.Schema(
        {
            vol.Required(CONF_HOST, default=defaults.get(CONF_HOST, DEFAULT_HOST)): str,
            vol.Required(CONF_PORT, default=defaults.get(CONF_PORT, DEFAULT_PORT)): cv.port,
            vol.Optional(
                CONF_DONGLE_SERIAL, default=defaults.get(CONF_DONGLE_SERIAL, "")
            ): str,
        }
    )


def _manual_schema(defaults: dict[str, Any]) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(CONF_HOST, default=defaults.get(CONF_HOST, DEFAULT_HOST)): str,
            vol.Required(CONF_PORT, default=defaults.get(CONF_PORT, DEFAULT_PORT)): cv.port,
            vol.Required(
                CONF_DONGLE_SERIAL, default=defaults.get(CONF_DONGLE_SERIAL, "")
            ): str,
            vol.Required(
                CONF_INVERTER_SERIAL, default=defaults.get(CONF_INVERTER_SERIAL, "")
            ): str,
        }
    )


class LuxPowerConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for LuxPower."""

    VERSION = 1

    def __init__(self) -> None:
        self._defaults: dict[str, Any] = {}
        self._pending_error: str | None = None

    async def async_step_user(self, user_input: dict[str, Any] | None = None):
        """Let the user choose between typing the serials and trying detection."""
        return self.async_show_menu(
            step_id="user",
            menu_options=["manual", "detect"],
        )

    async def async_step_detect(self, user_input: dict[str, Any] | None = None):
        """Read the inverter serial off the inverter; listen only if we must."""
        errors: dict[str, str] = {}

        if user_input is not None:
            host = user_input[CONF_HOST].strip()
            port = user_input[CONF_PORT]
            dongle = (user_input.get(CONF_DONGLE_SERIAL) or "").strip()
            self._defaults = {CONF_HOST: host, CONF_PORT: port}

            try:
                if not dongle:
                    # The only thing passive listening is good for: the dongle puts
                    # its own serial in every frame header it sends.
                    dongle, _ = await discover_serials(host, port, DETECT_TIMEOUT)

                inverter = (
                    await read_inverter_serial(host, port, dongle) if dongle else None
                )
            except LuxPowerConnectionError as err:
                _LOGGER.debug("Detection could not reach the dongle: %s", err)
                errors["base"] = "cannot_connect"
            else:
                if dongle:
                    self._defaults[CONF_DONGLE_SERIAL] = dongle
                if inverter:
                    self._defaults[CONF_INVERTER_SERIAL] = inverter
                # Go to the manual form either way, with whatever was found filled
                # in. A failed detection is normal, not worth blocking on.
                if not (dongle and inverter):
                    self._pending_error = "detect_failed" if dongle else "no_serials"
                return await self.async_step_manual()

        return self.async_show_form(
            step_id="detect",
            data_schema=_detect_schema(self._defaults),
            errors=errors,
            description_placeholders={"seconds": str(DETECT_TIMEOUT)},
        )

    async def async_step_manual(self, user_input: dict[str, Any] | None = None):
        """Collect host, port and both serials, then validate with a real read."""
        errors: dict[str, str] = {}

        if user_input is None:
            # Carry over a message from the detect step, once.
            if self._pending_error:
                errors["base"] = self._pending_error
                self._pending_error = None
        else:
            host = user_input[CONF_HOST].strip()
            port = user_input[CONF_PORT]
            dongle = user_input[CONF_DONGLE_SERIAL].strip()
            inverter = user_input[CONF_INVERTER_SERIAL].strip()
            self._defaults = {
                CONF_HOST: host,
                CONF_PORT: port,
                CONF_DONGLE_SERIAL: dongle,
                CONF_INVERTER_SERIAL: inverter,
            }

            if not dongle or not inverter:
                errors["base"] = "no_serials"
            else:
                try:
                    await _async_validate(host, port, dongle, inverter)
                except LuxPowerConnectionError as err:
                    _LOGGER.debug("Validation could not reach the dongle: %s", err)
                    errors["base"] = "cannot_connect"
                except LuxPowerError as err:
                    _LOGGER.debug("Validation read failed: %s", err)
                    errors["base"] = "no_data"
                except Exception:  # noqa: BLE001 - surfaced to the user as "unknown"
                    _LOGGER.exception("Unexpected error validating the LuxPower dongle")
                    errors["base"] = "unknown"
                else:
                    await self.async_set_unique_id(inverter)
                    self._abort_if_unique_id_configured()
                    return self.async_create_entry(
                        title=f"LuxPower {inverter}",
                        data={
                            CONF_HOST: host,
                            CONF_PORT: port,
                            CONF_DONGLE_SERIAL: dongle,
                            CONF_INVERTER_SERIAL: inverter,
                        },
                    )

        return self.async_show_form(
            step_id="manual",
            data_schema=_manual_schema(self._defaults),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> LuxPowerOptionsFlow:
        return LuxPowerOptionsFlow(config_entry)


class LuxPowerOptionsFlow(OptionsFlow):
    """Let the user change how often the dongle is polled."""

    def __init__(self, config_entry: ConfigEntry) -> None:
        # Stored under a private name: assigning self.config_entry is deprecated on
        # newer Home Assistant versions, which set it for us.
        self._entry = config_entry

    async def async_step_init(self, user_input: dict[str, Any] | None = None):
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        current = self._entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_SCAN_INTERVAL, default=current): vol.All(
                        vol.Coerce(int),
                        vol.Range(min=MIN_SCAN_INTERVAL, max=MAX_SCAN_INTERVAL),
                    )
                }
            ),
        )
