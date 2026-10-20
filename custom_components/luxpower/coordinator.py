"""Update coordinator for the LuxPower integration."""

from __future__ import annotations

from datetime import timedelta
import logging
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import DOMAIN
from .protocol import LuxPowerClient, LuxPowerError
from .registers import decode

_LOGGER = logging.getLogger(__name__)


class LuxPowerCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Polls the dongle and hands decoded sensor values to the entities.

    The coordinator owns the one connection to the dongle for the lifetime of the
    config entry, because the dongle only serves one TCP client well at a time.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        client: LuxPowerClient,
        scan_interval: int,
    ) -> None:
        # `config_entry` is deliberately not passed to super(): it only exists on
        # newer Home Assistant versions and the target version here is unknown.
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=scan_interval),
        )
        self.client = client

    async def _async_update_data(self) -> dict[str, Any]:
        """Read every verified register block and decode it."""
        try:
            registers = await self.client.async_read_all()
        except LuxPowerError as err:
            # Drop the socket so the next cycle starts from a clean connection: a
            # wedged dongle keeps answering TCP but stops answering reads, and it
            # will not accept a second client while this one is held open.
            await self.client.disconnect()
            raise UpdateFailed(str(err)) from err

        if not registers:
            raise UpdateFailed("dongle returned no registers")

        values = decode(registers)
        if not values:
            raise UpdateFailed(
                f"no sensor values decoded from {len(registers)} registers"
            )
        return values
