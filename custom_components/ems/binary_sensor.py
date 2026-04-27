from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
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
        EmsPeakRiskSensor(coordinator),
        EmsInEvChargingWindowSensor(coordinator),
        EmsCheapPriceNowSensor(coordinator),
        EmsExpensivePriceNowSensor(coordinator),
        EmsBatteryAtReserveSensor(coordinator),
        EmsExportAtLossSensor(coordinator),
        EmsCurtailingExportSensor(coordinator),
    ])


class _EmsBaseBinarySensor(CoordinatorEntity[EmsStrategyCoordinator], BinarySensorEntity):
    def __init__(self, coordinator: EmsStrategyCoordinator) -> None:
        super().__init__(coordinator)

    @property
    def device_info(self):
        return {"identifiers": {(DOMAIN, "ems")}, "name": "EMS", "manufacturer": "EMS"}

    @property
    def _result(self) -> LadderResult | None:
        return self.coordinator.data


class EmsPeakRiskSensor(_EmsBaseBinarySensor):
    _attr_unique_id = "ems_peak_risk"
    _attr_name = "EMS Peak Risk"
    _attr_icon = "mdi:alert"

    @property
    def is_on(self) -> bool:
        return self.coordinator._state.peak_risk_latched


class EmsInEvChargingWindowSensor(_EmsBaseBinarySensor):
    _attr_unique_id = "ems_in_ev_charging_window"
    _attr_name = "EMS In EV Charging Window"
    _attr_icon = "mdi:car-electric"

    @property
    def is_on(self) -> bool:
        window = self.coordinator._state.cached_window
        if window is None or not window.in_window_now:
            return False
        if self._result is None:
            return False
        inp = self._result.inputs
        return inp.ev_plugged and (inp.ev_soc is None or inp.ev_soc < inp.thresholds.ev_target_soc)


class EmsCheapPriceNowSensor(_EmsBaseBinarySensor):
    _attr_unique_id = "ems_cheap_price_now"
    _attr_name = "EMS Cheap Price Now"
    _attr_icon = "mdi:currency-eur"

    @property
    def is_on(self) -> bool:
        if self._result is None:
            return False
        inp = self._result.inputs
        if inp.import_price is None:
            return False
        return inp.import_price < inp.thresholds.proactive_grid_charge_price


class EmsExpensivePriceNowSensor(_EmsBaseBinarySensor):
    _attr_unique_id = "ems_expensive_price_now"
    _attr_name = "EMS Expensive Price Now"
    _attr_icon = "mdi:trending-up"

    @property
    def is_on(self) -> bool:
        if self._result is None:
            return False
        inp = self._result.inputs
        if inp.import_price is None or inp.avg_24h_price is None:
            return False
        threshold = inp.avg_24h_price * inp.thresholds.expensive_price_multiplier
        return inp.import_price > threshold


class EmsBatteryAtReserveSensor(_EmsBaseBinarySensor):
    _attr_unique_id = "ems_battery_at_reserve"
    _attr_name = "EMS Battery at Reserve"
    _attr_device_class = BinarySensorDeviceClass.BATTERY
    _attr_icon = "mdi:battery-alert"

    @property
    def is_on(self) -> bool:
        if self._result is None:
            return False
        inp = self._result.inputs
        return inp.battery_soc <= inp.thresholds.battery_reserve_soc


class EmsExportAtLossSensor(_EmsBaseBinarySensor):
    _attr_unique_id = "ems_export_at_loss"
    _attr_name = "EMS Export at Loss"
    _attr_icon = "mdi:transmission-tower-export"

    @property
    def is_on(self) -> bool:
        if self._result is None:
            return False
        inp = self._result.inputs
        if inp.export_price is None:
            return False
        return inp.export_price <= inp.thresholds.export_curtail_price


class EmsCurtailingExportSensor(_EmsBaseBinarySensor):
    _attr_unique_id = "ems_curtailing_export"
    _attr_name = "EMS Curtailing Export"
    _attr_icon = "mdi:solar-power-variant"

    @property
    def is_on(self) -> bool:
        if self._result is None:
            return False
        return self._result.decision.strategy == "export_curtail"
