from __future__ import annotations

from homeassistant.components.sensor import SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfPower
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import EmsStrategyCoordinator
from .models import LadderResult


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: EmsStrategyCoordinator = hass.data[DOMAIN][entry.entry_id]["strategy_coordinator"]
    async_add_entities([
        EmsCurrentStrategySensor(coordinator),
        EmsBatteryDischargeCap(coordinator),
        EmsEvChargePower(coordinator),
    ])


class _EmsBaseSensor(CoordinatorEntity[EmsStrategyCoordinator], SensorEntity):
    def __init__(self, coordinator: EmsStrategyCoordinator) -> None:
        super().__init__(coordinator)

    @property
    def device_info(self):
        return {"identifiers": {(DOMAIN, "ems")}, "name": "EMS", "manufacturer": "EMS"}

    @property
    def _result(self) -> LadderResult | None:
        return self.coordinator.data


class EmsCurrentStrategySensor(_EmsBaseSensor):
    _attr_unique_id = "ems_current_strategy"
    _attr_name = "EMS Current Strategy"
    _attr_icon = "mdi:strategy"

    @property
    def native_value(self) -> str:
        if self._result is None:
            return "starting_up"
        return self._result.decision.strategy


class EmsBatteryDischargeCap(_EmsBaseSensor):
    _attr_unique_id = "ems_battery_discharge_cap"
    _attr_name = "EMS Battery Discharge Cap"
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:battery-arrow-down"

    @property
    def native_value(self) -> float | None:
        if self._result is None:
            return None
        return self._result.decision.battery_discharge_cap_w


class EmsEvChargePower(_EmsBaseSensor):
    _attr_unique_id = "ems_ev_charge_power"
    _attr_name = "EMS EV Charge Power"
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:ev-station"

    @property
    def native_value(self) -> float | None:
        if self._result is None:
            return None
        return self._result.decision.ev_charge_power_w
