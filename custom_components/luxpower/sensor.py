"""Sensor platform for the LuxPower integration.

Every entity comes from registers.SENSORS, which is the verified map -- registers
that decode wrongly on this hardware are listed in registers.EXCLUDED and have no
entity here on purpose.

Entity IDs are deterministic and depended on by the dashboard YAML: with
`has_entity_name` and a device named "LuxPower", the entity description's `name`
(the map's label) gives `sensor.luxpower_<slug(label)>`, e.g. "PV total power" ->
sensor.luxpower_pv_total_power. Do not swap `name` for `translation_key` without
renaming the dashboard entities to match.
"""

from __future__ import annotations

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    PERCENTAGE,
    EntityCategory,
    UnitOfElectricCurrent,
    UnitOfElectricPotential,
    UnitOfEnergy,
    UnitOfFrequency,
    UnitOfPower,
    UnitOfTemperature,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_INVERTER_SERIAL, DOMAIN, MANUFACTURER, MODEL
from .coordinator import LuxPowerCoordinator
from .registers import SENSORS, status_text

DEVICE_CLASS_MAP = {
    "voltage": SensorDeviceClass.VOLTAGE,
    "current": SensorDeviceClass.CURRENT,
    "power": SensorDeviceClass.POWER,
    "energy": SensorDeviceClass.ENERGY,
    "frequency": SensorDeviceClass.FREQUENCY,
    "temperature": SensorDeviceClass.TEMPERATURE,
    "battery": SensorDeviceClass.BATTERY,
    "duration": SensorDeviceClass.DURATION,
}

STATE_CLASS_MAP = {
    "measurement": SensorStateClass.MEASUREMENT,
    "total_increasing": SensorStateClass.TOTAL_INCREASING,
}

UNIT_MAP = {
    "V": UnitOfElectricPotential.VOLT,
    "A": UnitOfElectricCurrent.AMPERE,
    "W": UnitOfPower.WATT,
    "kWh": UnitOfEnergy.KILO_WATT_HOUR,
    "Hz": UnitOfFrequency.HERTZ,
    "°C": UnitOfTemperature.CELSIUS,
    "s": UnitOfTime.SECONDS,
    "%": PERCENTAGE,
}

#: Kept out of the main entity list in the UI.
DIAGNOSTIC_KEYS = {
    "fault_code",
    "warning_code",
    "runtime",
    "bus1_voltage",
    "bus2_voltage",
}

#: Rendered as hex, matching how the probe script reports them.
HEX_KEYS = {"fault_code", "warning_code"}

#: Only needed where there is no device class to pick an icon from.
ICONS = {
    "status": "mdi:state-machine",
    "fault_code": "mdi:alert-circle-outline",
    "warning_code": "mdi:alert-outline",
    "runtime": "mdi:timer-outline",
    "pv_power": "mdi:solar-power",
    "pv1_power": "mdi:solar-power-variant",
    "pv2_power": "mdi:solar-power-variant",
    "ac_charge_power": "mdi:transmission-tower-import",
    "eps_power": "mdi:power-plug-battery",
}


def _descriptions() -> list[SensorEntityDescription]:
    """Turn the verified register map into sensor entity descriptions."""
    descriptions: list[SensorEntityDescription] = []
    for key, label, _reg, _kind, _scale, unit, device_class, state_class in SENSORS:
        descriptions.append(
            SensorEntityDescription(
                key=key,
                name=label,
                native_unit_of_measurement=UNIT_MAP.get(unit) if unit else None,
                device_class=DEVICE_CLASS_MAP.get(device_class)
                if device_class
                else None,
                state_class=STATE_CLASS_MAP.get(state_class) if state_class else None,
                entity_category=(
                    EntityCategory.DIAGNOSTIC if key in DIAGNOSTIC_KEYS else None
                ),
                icon=ICONS.get(key),
            )
        )
    return descriptions


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the LuxPower sensors."""
    coordinator: LuxPowerCoordinator = hass.data[DOMAIN][entry.entry_id]
    inverter_serial = entry.data[CONF_INVERTER_SERIAL]

    async_add_entities(
        LuxPowerSensor(coordinator, description, inverter_serial)
        for description in _descriptions()
    )


class LuxPowerSensor(CoordinatorEntity[LuxPowerCoordinator], SensorEntity):
    """One value read from the inverter."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: LuxPowerCoordinator,
        description: SensorEntityDescription,
        inverter_serial: str,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{inverter_serial}_{description.key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, inverter_serial)},
            name="LuxPower",
            manufacturer=MANUFACTURER,
            model=MODEL,
            serial_number=inverter_serial,
        )

    @property
    def available(self) -> bool:
        """Unavailable if the last poll did not carry this value."""
        return super().available and (
            self.entity_description.key in (self.coordinator.data or {})
        )

    @property
    def native_value(self):
        """Scaled value, with the two coded registers rendered for humans."""
        data = self.coordinator.data or {}
        value = data.get(self.entity_description.key)
        if value is None:
            return None

        key = self.entity_description.key
        if key == "status":
            return status_text(int(value))
        if key in HEX_KEYS:
            return f"0x{int(value):X}"
        return value
