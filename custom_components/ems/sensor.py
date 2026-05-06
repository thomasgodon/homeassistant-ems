from __future__ import annotations

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import EmsFastLoopCoordinator

_CHARGING_STATE_LABELS: dict[str, str] = {
    "charging": "Charging",
    "ev_not_connected": "EV not connected",
    "charging_disabled": "Charging disabled",
    "target_soc_reached": "Target SoC reached",
    "not_enough_power": "Not enough power",
    "starting_up": "Starting up",
    "outside_cheap_hours": "Outside cheap hours",
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: EmsFastLoopCoordinator = hass.data[DOMAIN][entry.entry_id]["fast_loop_coordinator"]
    async_add_entities([
        EmsInverterModeSensor(coordinator),
        EmsEvCurrentSensor(coordinator),
        EmsEvChargingStateSensor(coordinator),
        EmsInverterActivePowerSensor(coordinator),
    ])


class EmsEvCurrentSensor(CoordinatorEntity[EmsFastLoopCoordinator], SensorEntity):
    _attr_unique_id = "ems_ev_current_w"
    _attr_name = "EMS EV Current"
    _attr_native_unit_of_measurement = "W"
    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator: EmsFastLoopCoordinator) -> None:
        super().__init__(coordinator)

    @property
    def device_info(self):
        return {"identifiers": {(DOMAIN, "ems")}, "name": "EMS", "manufacturer": "EMS"}

    @property
    def native_value(self) -> float:
        if self.coordinator.data is None:
            return 0.0
        return self.coordinator.data.ev_decision.ev_charge_power_w

    @property
    def available(self) -> bool:
        return True


class EmsInverterModeSensor(CoordinatorEntity[EmsFastLoopCoordinator], SensorEntity):
    _attr_unique_id = "ems_inverter_mode"
    _attr_name = "EMS Inverter Mode"

    def __init__(self, coordinator: EmsFastLoopCoordinator) -> None:
        super().__init__(coordinator)

    @property
    def device_info(self):
        return {"identifiers": {(DOMAIN, "ems")}, "name": "EMS", "manufacturer": "EMS"}

    @property
    def native_value(self) -> str | None:
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.inverter_decision.inverter_verb or "disabled"

    @property
    def available(self) -> bool:
        return self.coordinator.data is not None


class EmsInverterActivePowerSensor(CoordinatorEntity[EmsFastLoopCoordinator], SensorEntity):
    _attr_unique_id = "ems_inverter_active_power"
    _attr_name = "EMS Inverter Active Power"
    _attr_native_unit_of_measurement = "W"
    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator: EmsFastLoopCoordinator) -> None:
        super().__init__(coordinator)

    @property
    def device_info(self):
        return {"identifiers": {(DOMAIN, "ems")}, "name": "EMS", "manufacturer": "EMS"}

    @property
    def native_value(self) -> float:
        if self.coordinator.data is None:
            return 0.0
        return self.coordinator.data.inverter_decision.active_power_w or 0.0

    @property
    def available(self) -> bool:
        return True


class EmsEvChargingStateSensor(CoordinatorEntity[EmsFastLoopCoordinator], SensorEntity):
    _attr_unique_id = "ems_ev_charging_state"
    _attr_name = "EMS EV Charging State"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = list(_CHARGING_STATE_LABELS.values())

    def __init__(self, coordinator: EmsFastLoopCoordinator) -> None:
        super().__init__(coordinator)

    @property
    def device_info(self):
        return {"identifiers": {(DOMAIN, "ems")}, "name": "EMS", "manufacturer": "EMS"}

    @property
    def native_value(self) -> str | None:
        if self.coordinator.data is None:
            return None
        return _CHARGING_STATE_LABELS.get(self.coordinator.data.ev_decision.charging_state)

    @property
    def available(self) -> bool:
        return self.coordinator.data is not None
