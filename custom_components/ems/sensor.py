from __future__ import annotations

from homeassistant.components.sensor import SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
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
        EmsSolarSurplusSensor(coordinator),
        EmsEvKwhNeededSensor(coordinator),
        EmsCheapestWindowStartSensor(coordinator),
        EmsCheapestWindowEndSensor(coordinator),
        EmsExpectedBatteryDeficitSensor(coordinator),
        Ems24hAvgPriceSensor(coordinator),
        EmsInverterTargetVerbSensor(coordinator),
        EmsInverterPowerTargetSensor(coordinator),
        EmsEvChargeTargetPowerSensor(coordinator),
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


class EmsSolarSurplusSensor(_EmsBaseSensor):
    _attr_unique_id = "ems_solar_surplus_w"
    _attr_name = "EMS Solar Surplus"
    _attr_native_unit_of_measurement = "W"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:solar-power"

    @property
    def native_value(self) -> float | None:
        if self._result is None:
            return None
        inp = self._result.inputs
        return max(0.0, inp.pv_w - inp.house_load_w)


class EmsEvKwhNeededSensor(_EmsBaseSensor):
    _attr_unique_id = "ems_ev_kwh_needed"
    _attr_name = "EMS EV kWh Needed"
    _attr_native_unit_of_measurement = "kWh"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:car-electric"

    @property
    def native_value(self) -> float | None:
        if self._result is None:
            return None
        inp = self._result.inputs
        if inp.ev_soc is None:
            return None
        return max(0.0, (inp.thresholds.ev_target_soc - inp.ev_soc) / 100.0 * inp.ev_capacity_kwh)


class EmsCheapestWindowStartSensor(_EmsBaseSensor):
    _attr_unique_id = "ems_cheapest_window_start"
    _attr_name = "EMS Cheapest Window Start"
    _attr_device_class = "timestamp"
    _attr_icon = "mdi:clock-start"

    @property
    def native_value(self):
        if self.coordinator._state.cached_window is None:
            return None
        return self.coordinator._state.cached_window.start


class EmsCheapestWindowEndSensor(_EmsBaseSensor):
    _attr_unique_id = "ems_cheapest_window_end"
    _attr_name = "EMS Cheapest Window End"
    _attr_device_class = "timestamp"
    _attr_icon = "mdi:clock-end"

    @property
    def native_value(self):
        if self.coordinator._state.cached_window is None:
            return None
        return self.coordinator._state.cached_window.end


class EmsExpectedBatteryDeficitSensor(_EmsBaseSensor):
    _attr_unique_id = "ems_expected_battery_deficit_evening_kwh"
    _attr_name = "EMS Expected Battery Deficit Evening"
    _attr_native_unit_of_measurement = "kWh"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:battery-alert"

    @property
    def native_value(self) -> float | None:
        if self._result is None:
            return None
        inp = self._result.inputs
        if inp.pv_forecast_remaining_kwh is None or inp.house_load_forecast_kwh is None:
            return None
        battery_kwh = inp.battery_soc / 100.0 * inp.thresholds.battery_capacity_kwh
        net = battery_kwh + inp.pv_forecast_remaining_kwh - inp.house_load_forecast_kwh
        return max(0.0, -net)


class Ems24hAvgPriceSensor(_EmsBaseSensor):
    _attr_unique_id = "ems_24h_avg_price"
    _attr_name = "EMS 24h Average Price"
    _attr_native_unit_of_measurement = "EUR/kWh"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:currency-eur"

    @property
    def native_value(self) -> float | None:
        if self._result is None:
            return None
        return self._result.inputs.avg_24h_price


class EmsInverterTargetVerbSensor(_EmsBaseSensor):
    _attr_unique_id = "ems_inverter_target_verb"
    _attr_name = "EMS Inverter Target Verb"
    _attr_icon = "mdi:lightning-bolt"

    @property
    def native_value(self) -> str | None:
        if self._result is None:
            return None
        verb = self._result.decision.inverter_verb
        return verb.value if verb else None


class EmsInverterPowerTargetSensor(_EmsBaseSensor):
    _attr_unique_id = "ems_inverter_power_target_w"
    _attr_name = "EMS Inverter Power Target"
    _attr_native_unit_of_measurement = "W"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:gauge"

    @property
    def native_value(self) -> float | None:
        if self._result is None:
            return None
        return self._result.decision.inverter_power_target_w


class EmsEvChargeTargetPowerSensor(_EmsBaseSensor):
    _attr_unique_id = "ems_ev_charge_target_power"
    _attr_name = "EMS EV Charge Target Power"
    _attr_native_unit_of_measurement = "W"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:ev-station"

    @property
    def native_value(self) -> float:
        if self._result is None:
            return 0.0
        return self._result.decision.ev_charge_power_w
