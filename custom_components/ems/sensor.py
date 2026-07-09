from __future__ import annotations

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import EmsFastLoopCoordinator, EmsForecastCoordinator
from .strategy import (
    battery_focus,
    battery_gap_kwh,
    cheap_hold_rank,
    effective_solar_remaining_kwh,
    expected_eod_soc,
    grid_focus,
    house_load_in_solar_window_kwh,
    pv_destinations,
    pv_focus,
    solar_production_end_time,
    solar_production_start_time,
    solar_remaining_wh,
    solar_seconds_until_zero,
    solar_shortfall_kwh,
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: EmsFastLoopCoordinator = hass.data[DOMAIN][entry.entry_id]["fast_loop_coordinator"]
    forecast_coordinator: EmsForecastCoordinator = (
        hass.data[DOMAIN][entry.entry_id]["forecast_coordinator"]
    )
    async_add_entities([
        EmsInverterModeSensor(coordinator),
        EmsEvCurrentSensor(coordinator),
        EmsEvStatusSensor(coordinator),
        EmsInverterActivePowerSensor(coordinator),
        EmsBatteryStatusSensor(coordinator),
        EmsPvStatusSensor(coordinator),
        EmsGridStatusSensor(coordinator),
        EmsBatteryEnergyToTargetSensor(forecast_coordinator),
        EmsNetSolarRemainingSensor(forecast_coordinator),
        EmsHouseLoadInSolarWindowSensor(forecast_coordinator),
        EmsBatterySolarDeficitSensor(forecast_coordinator),
        EmsExpectedEodBatterySensor(forecast_coordinator),
        EmsSolarRemainingSensor(forecast_coordinator),
        EmsSolarTimeRemainingSensor(forecast_coordinator),
        EmsSolarProductionStartSensor(forecast_coordinator),
        EmsSolarProductionEndSensor(forecast_coordinator),
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


class EmsEvStatusSensor(CoordinatorEntity[EmsFastLoopCoordinator], SensorEntity):
    """Prose status for EV charging — priority-ordered, human-readable."""

    _attr_unique_id = "ems_ev_status"
    _attr_name = "EMS EV Status"

    def __init__(self, coordinator: EmsFastLoopCoordinator) -> None:
        super().__init__(coordinator)

    @property
    def device_info(self):
        return {"identifiers": {(DOMAIN, "ems")}, "name": "EMS", "manufacturer": "EMS"}

    @property
    def native_value(self) -> str | None:
        if self.coordinator.data is None:
            return None
        ev = self.coordinator.data.ev_decision
        inp = self.coordinator.data.inputs
        state = ev.charging_state

        if state == "starting_up":
            return "Starting up…"
        if state == "charging_disabled":
            return "EV charging disabled"
        if state == "ev_not_connected":
            return "EV not connected"
        if state == "target_soc_reached":
            if inp.ev_soc is not None:
                return f"EV battery full ({round(inp.ev_soc)}% reached)"
            return "EV battery full"
        if state == "outside_cheap_hours":
            return "Waiting for cheap hours"
        if state == "battery_short":
            return "Solar reserved for home battery"
        if state == "not_enough_power":
            return "Not enough surplus to charge"
        if state == "holding_through_dip":
            return "Holding charge through brief dip"
        if state == "grid_overloaded":
            return "Stopped — home load exceeds inverter capacity"
        # state == "charging"
        kw = round(ev.ev_charge_power_w / 1000, 1)
        if inp.grid_import_w > 0:
            return f"Charging EV at {kw} kW with grid headroom"
        return f"Charging EV at {kw} kW from solar surplus"

    @property
    def available(self) -> bool:
        return self.coordinator.data is not None


class EmsBatteryStatusSensor(CoordinatorEntity[EmsFastLoopCoordinator], SensorEntity):
    """Prose status for the battery — focus, EOD forecast, shortfall."""

    _attr_unique_id = "ems_battery_status"
    _attr_name = "EMS Battery Status"

    def __init__(self, coordinator: EmsFastLoopCoordinator) -> None:
        super().__init__(coordinator)

    @property
    def device_info(self):
        return {"identifiers": {(DOMAIN, "ems")}, "name": "EMS", "manufacturer": "EMS"}

    @property
    def native_value(self) -> str | None:
        if self.coordinator.data is None:
            return None
        inv = self.coordinator.data.inverter_decision
        inp = self.coordinator.data.inputs

        focus = battery_focus(inp, inv)
        battery_w = inp.pv_w + inp.grid_import_w - inp.house_load_w
        soc = inp.battery_soc
        eod = expected_eod_soc(inp)
        shortfall = solar_shortfall_kwh(inp)

        soc_str = f" · {round(soc)}%" if soc is not None else ""
        if eod is not None and shortfall is not None:
            eod_str = (
                f" · forecast EOD {round(eod)}%, short {shortfall:.1f} kWh"
                if shortfall > 0.0
                else f" · forecast EOD {round(eod)}%"
            )
        else:
            eod_str = ""

        if focus == "starting_up":
            return "Starting up…"
        if focus == "unavailable":
            return "Battery SoC unavailable"
        if focus == "peak_shaving":
            return f"Discharging to limit grid import{soc_str}"
        if focus == "holding_for_evening":
            rank_info = cheap_hold_rank(inp)
            if rank_info is not None:
                r, n = rank_info
                return f"Holding for evening{soc_str} ({r} of {n} cheap hours today){eod_str}"
            return f"Holding for evening{soc_str}{eod_str}"
        if focus == "prioritising_battery":
            return f"Prioritising battery — EV charging from grid{soc_str}{eod_str}"
        if focus == "full":
            return f"Battery full — {round(soc)}% reached"
        if focus == "charging_from_solar":
            kw = round(abs(battery_w) / 1000, 1)
            return f"Charging from solar at {kw} kW{soc_str}{eod_str}"
        if focus == "discharging_to_house":
            kw = round(abs(battery_w) / 1000, 1)
            return f"Powering house at {kw} kW{soc_str}{eod_str}"
        return f"Idle{soc_str}{eod_str}"

    @property
    def extra_state_attributes(self) -> dict | None:
        if self.coordinator.data is None:
            return None
        inv = self.coordinator.data.inverter_decision
        inp = self.coordinator.data.inputs
        battery_w = inp.pv_w + inp.grid_import_w - inp.house_load_w
        eod = expected_eod_soc(inp)
        shortfall = solar_shortfall_kwh(inp)
        focus = battery_focus(inp, inv)
        will_reach = (eod >= inp.battery_target_soc) if eod is not None else None
        return {
            "focus": focus,
            "power_w": battery_w,
            "soc": inp.battery_soc,
            "target_soc": inp.battery_target_soc,
            "expected_eod_soc": eod,
            "shortfall_kwh": shortfall,
            "will_reach_target": will_reach,
        }

    @property
    def available(self) -> bool:
        return self.coordinator.data is not None


class EmsPvStatusSensor(CoordinatorEntity[EmsFastLoopCoordinator], SensorEntity):
    """Prose status for solar PV — where it's going and why."""

    _attr_unique_id = "ems_pv_status"
    _attr_name = "EMS PV Status"

    def __init__(self, coordinator: EmsFastLoopCoordinator) -> None:
        super().__init__(coordinator)

    @property
    def device_info(self):
        return {"identifiers": {(DOMAIN, "ems")}, "name": "EMS", "manufacturer": "EMS"}

    @property
    def native_value(self) -> str | None:
        if self.coordinator.data is None:
            return None
        inv = self.coordinator.data.inverter_decision
        inp = self.coordinator.data.inputs

        dest = pv_destinations(inp, inv)
        focus = pv_focus(dest, inv)
        pv_kw = round(inp.pv_w / 1000, 1)

        if focus == "starting_up":
            return "Starting up…"
        if focus == "no_production":
            return "No solar production"
        if focus == "to_house":
            return f"Powering the house at {pv_kw} kW"
        if focus == "to_ev":
            return f"Charging the EV at {pv_kw} kW"
        if focus == "to_battery":
            return f"Charging the battery at {pv_kw} kW"
        if focus == "exporting":
            return f"Exporting {pv_kw} kW to grid"
        # mixed
        parts = []
        threshold_kw = 0.1
        if dest.to_house_w / 1000 >= threshold_kw:
            parts.append(f"house {round(dest.to_house_w / 1000, 1)} kW")
        if dest.to_battery_w / 1000 >= threshold_kw:
            parts.append(f"battery {round(dest.to_battery_w / 1000, 1)} kW")
        if dest.to_ev_w / 1000 >= threshold_kw:
            parts.append(f"EV {round(dest.to_ev_w / 1000, 1)} kW")
        if dest.to_grid_w / 1000 >= threshold_kw:
            parts.append(f"grid {round(dest.to_grid_w / 1000, 1)} kW")
        detail = ", ".join(parts) if parts else "all below threshold"
        return f"Producing {pv_kw} kW: {detail}"

    @property
    def extra_state_attributes(self) -> dict | None:
        if self.coordinator.data is None:
            return None
        inv = self.coordinator.data.inverter_decision
        inp = self.coordinator.data.inputs
        dest = pv_destinations(inp, inv)
        focus = pv_focus(dest, inv)
        return {
            "focus": focus,
            "pv_w": inp.pv_w,
            "to_house_w": dest.to_house_w,
            "to_ev_w": dest.to_ev_w,
            "to_battery_w": dest.to_battery_w,
            "to_grid_w": dest.to_grid_w,
        }

    @property
    def available(self) -> bool:
        return self.coordinator.data is not None


class EmsGridStatusSensor(CoordinatorEntity[EmsFastLoopCoordinator], SensorEntity):
    """Prose status for the grid connection — import, export, peak shaving."""

    _attr_unique_id = "ems_grid_status"
    _attr_name = "EMS Grid Status"

    def __init__(self, coordinator: EmsFastLoopCoordinator) -> None:
        super().__init__(coordinator)

    @property
    def device_info(self):
        return {"identifiers": {(DOMAIN, "ems")}, "name": "EMS", "manufacturer": "EMS"}

    @property
    def native_value(self) -> str | None:
        if self.coordinator.data is None:
            return None
        inv = self.coordinator.data.inverter_decision
        inp = self.coordinator.data.inputs

        focus = grid_focus(inp, inv)
        g = inp.grid_import_w
        cap = inp.thresholds.max_grid_import_w
        kw = round(abs(g) / 1000, 1)
        cap_kw = round(cap / 1000, 1)
        headroom_kw = round((cap - g) / 1000, 1)

        if focus == "starting_up":
            return "Starting up…"
        if focus == "exporting":
            return f"Exporting {kw} kW to grid"
        if focus == "balanced":
            return "Balanced (neither importing nor exporting)"
        if focus == "peak_shaving_active":
            return f"At cap — battery discharging to limit import ({kw} kW)"
        if focus == "at_peak_cap":
            return f"At import cap of {cap_kw} kW"
        return f"Importing {kw} kW (cap {cap_kw} kW, headroom {headroom_kw} kW)"

    @property
    def extra_state_attributes(self) -> dict | None:
        if self.coordinator.data is None:
            return None
        inv = self.coordinator.data.inverter_decision
        inp = self.coordinator.data.inputs
        focus = grid_focus(inp, inv)
        cap = inp.thresholds.max_grid_import_w
        return {
            "focus": focus,
            "import_w": inp.grid_import_w,
            "max_import_w": cap,
            "headroom_w": cap - inp.grid_import_w,
        }

    @property
    def available(self) -> bool:
        return self.coordinator.data is not None


class EmsBatteryEnergyToTargetSensor(CoordinatorEntity[EmsForecastCoordinator], SensorEntity):
    """Wh still needed to bring the battery from current SoC to its target SoC."""

    _attr_unique_id = "ems_battery_energy_to_target"
    _attr_name = "EMS Battery Energy To Target"
    _attr_native_unit_of_measurement = "Wh"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:battery-arrow-up"

    def __init__(self, coordinator: EmsForecastCoordinator) -> None:
        super().__init__(coordinator)

    @property
    def device_info(self):
        return {"identifiers": {(DOMAIN, "ems")}, "name": "EMS", "manufacturer": "EMS"}

    @property
    def native_value(self) -> int | None:
        if self.coordinator.data is None:
            return None
        kwh = battery_gap_kwh(self.coordinator.data)
        return None if kwh is None else round(kwh * 1000)

    @property
    def available(self) -> bool:
        if self.coordinator.data is None:
            return False
        return battery_gap_kwh(self.coordinator.data) is not None


class EmsNetSolarRemainingSensor(CoordinatorEntity[EmsForecastCoordinator], SensorEntity):
    """Solar forecast remaining today minus house load forecast remaining, clamped to ≥ 0."""

    _attr_unique_id = "ems_net_solar_remaining"
    _attr_name = "EMS Net Solar Remaining"
    _attr_native_unit_of_measurement = "Wh"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:solar-power-variant"

    def __init__(self, coordinator: EmsForecastCoordinator) -> None:
        super().__init__(coordinator)

    @property
    def device_info(self):
        return {"identifiers": {(DOMAIN, "ems")}, "name": "EMS", "manufacturer": "EMS"}

    @property
    def native_value(self) -> int | None:
        if self.coordinator.data is None:
            return None
        kwh = effective_solar_remaining_kwh(self.coordinator.data)
        return None if kwh is None else round(kwh * 1000)

    @property
    def available(self) -> bool:
        if self.coordinator.data is None:
            return False
        return effective_solar_remaining_kwh(self.coordinator.data) is not None


class EmsHouseLoadInSolarWindowSensor(CoordinatorEntity[EmsForecastCoordinator], SensorEntity):
    """Forecasted house load remaining within the solar production window (now → solar end)."""

    _attr_unique_id = "ems_house_load_in_solar_window"
    _attr_name = "EMS House Load In Solar Window"
    _attr_native_unit_of_measurement = "Wh"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:home-lightning-bolt"

    def __init__(self, coordinator: EmsForecastCoordinator) -> None:
        super().__init__(coordinator)

    @property
    def device_info(self):
        return {"identifiers": {(DOMAIN, "ems")}, "name": "EMS", "manufacturer": "EMS"}

    @property
    def native_value(self) -> int | None:
        if self.coordinator.data is None:
            return None
        kwh = house_load_in_solar_window_kwh(
            self.coordinator.data.solar_forecast_today,
            self.coordinator.data.house_load_forecast_today,
            self.coordinator.data.now,
        )
        return None if kwh is None else round(kwh * 1000)

    @property
    def available(self) -> bool:
        if self.coordinator.data is None:
            return False
        return house_load_in_solar_window_kwh(
            self.coordinator.data.solar_forecast_today,
            self.coordinator.data.house_load_forecast_today,
            self.coordinator.data.now,
        ) is not None


class EmsBatterySolarDeficitSensor(CoordinatorEntity[EmsForecastCoordinator], SensorEntity):
    """Signed energy balance: positive = solar short of filling battery to target, negative = surplus."""  # noqa: E501

    _attr_unique_id = "ems_battery_solar_deficit"
    _attr_name = "EMS Battery Solar Deficit"
    _attr_native_unit_of_measurement = "Wh"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:scale-balance"

    def __init__(self, coordinator: EmsForecastCoordinator) -> None:
        super().__init__(coordinator)

    @property
    def device_info(self):
        return {"identifiers": {(DOMAIN, "ems")}, "name": "EMS", "manufacturer": "EMS"}

    @property
    def native_value(self) -> int | None:
        if self.coordinator.data is None:
            return None
        kwh = solar_shortfall_kwh(self.coordinator.data)
        return None if kwh is None else round(-kwh * 1000)

    @property
    def available(self) -> bool:
        if self.coordinator.data is None:
            return False
        return solar_shortfall_kwh(self.coordinator.data) is not None


class EmsExpectedEodBatterySensor(CoordinatorEntity[EmsForecastCoordinator], SensorEntity):
    """Projected battery SoC (%) at end of day (00:00) given solar and house-load forecasts."""

    _attr_unique_id = "ems_expected_eod_battery"
    _attr_name = "EMS Expected End Of Day Battery"
    _attr_native_unit_of_measurement = "%"
    _attr_device_class = SensorDeviceClass.BATTERY
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:battery-clock"
    _attr_suggested_display_precision = 0

    def __init__(self, coordinator: EmsForecastCoordinator) -> None:
        super().__init__(coordinator)

    @property
    def device_info(self):
        return {"identifiers": {(DOMAIN, "ems")}, "name": "EMS", "manufacturer": "EMS"}

    @property
    def native_value(self) -> float | None:
        if self.coordinator.data is None:
            return None
        value = expected_eod_soc(self.coordinator.data)
        return None if value is None else round(value, 1)

    @property
    def available(self) -> bool:
        if self.coordinator.data is None:
            return False
        return expected_eod_soc(self.coordinator.data) is not None


class EmsSolarRemainingSensor(CoordinatorEntity[EmsForecastCoordinator], SensorEntity):
    """Solar energy remaining today (Wh), derived from the hourly JSON forecast."""

    _attr_unique_id = "ems_solar_remaining"
    _attr_name = "EMS Solar Remaining"
    _attr_native_unit_of_measurement = "Wh"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:white-balance-sunny"

    def __init__(self, coordinator: EmsForecastCoordinator) -> None:
        super().__init__(coordinator)

    @property
    def device_info(self):
        return {"identifiers": {(DOMAIN, "ems")}, "name": "EMS", "manufacturer": "EMS"}

    @property
    def native_value(self) -> int:
        if self.coordinator.data is None:
            return 0
        value = solar_remaining_wh(
            self.coordinator.data.solar_forecast_today,
            self.coordinator.data.now,
        )
        return 0 if value is None else round(value)

    @property
    def available(self) -> bool:
        return self.coordinator.data is not None


class EmsSolarTimeRemainingSensor(CoordinatorEntity[EmsForecastCoordinator], SensorEntity):
    """Seconds until the last non-zero forecast hour ends."""

    _attr_unique_id = "ems_solar_time_remaining"
    _attr_name = "EMS Solar Time Remaining"
    _attr_native_unit_of_measurement = "s"
    _attr_device_class = SensorDeviceClass.DURATION
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:timer-sand"

    def __init__(self, coordinator: EmsForecastCoordinator) -> None:
        super().__init__(coordinator)

    @property
    def device_info(self):
        return {"identifiers": {(DOMAIN, "ems")}, "name": "EMS", "manufacturer": "EMS"}

    @property
    def native_value(self) -> int:
        if self.coordinator.data is None:
            return 0
        value = solar_seconds_until_zero(
            self.coordinator.data.solar_forecast_today,
            self.coordinator.data.now,
        )
        return 0 if value is None else value

    @property
    def available(self) -> bool:
        return self.coordinator.data is not None


class EmsSolarProductionStartSensor(CoordinatorEntity[EmsForecastCoordinator], SensorEntity):
    """Datetime when solar production is forecast to begin today."""

    _attr_unique_id = "ems_solar_production_start"
    _attr_name = "EMS Solar Production Start"
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_icon = "mdi:weather-sunset-up"

    def __init__(self, coordinator: EmsForecastCoordinator) -> None:
        super().__init__(coordinator)

    @property
    def device_info(self):
        return {"identifiers": {(DOMAIN, "ems")}, "name": "EMS", "manufacturer": "EMS"}

    @property
    def native_value(self):
        if self.coordinator.data is None:
            return None
        return solar_production_start_time(
            self.coordinator.data.solar_forecast_today,
            self.coordinator.data.now,
        )

    @property
    def available(self) -> bool:
        if self.coordinator.data is None:
            return False
        return solar_production_start_time(
            self.coordinator.data.solar_forecast_today,
            self.coordinator.data.now,
        ) is not None


class EmsSolarProductionEndSensor(CoordinatorEntity[EmsForecastCoordinator], SensorEntity):
    """Datetime when solar production is forecast to end today."""

    _attr_unique_id = "ems_solar_production_end"
    _attr_name = "EMS Solar Production End"
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_icon = "mdi:weather-sunset-down"

    def __init__(self, coordinator: EmsForecastCoordinator) -> None:
        super().__init__(coordinator)

    @property
    def device_info(self):
        return {"identifiers": {(DOMAIN, "ems")}, "name": "EMS", "manufacturer": "EMS"}

    @property
    def native_value(self):
        if self.coordinator.data is None:
            return None
        return solar_production_end_time(
            self.coordinator.data.solar_forecast_today,
            self.coordinator.data.now,
        )

    @property
    def available(self) -> bool:
        if self.coordinator.data is None:
            return False
        return solar_production_end_time(
            self.coordinator.data.solar_forecast_today,
            self.coordinator.data.now,
        ) is not None
