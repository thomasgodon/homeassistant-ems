from __future__ import annotations

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import EmsFastLoopCoordinator
from .strategy import solar_shortfall_kwh

_INVERTER_STATUS_LABELS: dict[str, str] = {
    "controlling": "Controlling",
    "idle": "Idle",
    "disabled": "Disabled",
    "starting_up": "Starting up",
    "peak_shaving": "Peak shaving",
    "holding_battery": "Holding battery",
    "holding_battery_cheap": "Holding battery (cheap hour)",
}

_BATTERY_POWER_DEADBAND_W = 200.0

_INVERTER_STATUS_REASONS: dict[str, str] = {
    "idle": (
        "No EV draw. Inverter set to its disabled option;"
        " EMS leaves inverter to manage itself."
    ),
    "disabled": "Inverter Control Mode is set to Off. EMS does not write to the inverter.",
    "starting_up": (
        "Startup grace (60 s). Strategy still computing,"
        " but writes are suppressed while sensors stabilize."
    ),
    "peak_shaving": (
        "Grid import exceeded the cap. Battery is discharging to cover the excess."
    ),
    "holding_battery": (
        "Cheap hour with PV producing. Battery discharge suppressed so PV charges the battery."
    ),
    "holding_battery_cheap": (
        "Cheap-priced hour with no EV activity."
        " Battery hold so house load comes from grid, not battery."
    ),
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
        EmsEvStatusSensor(coordinator),
        EmsInverterActivePowerSensor(coordinator),
        EmsInverterStatusSensor(coordinator),
        EmsBatteryStatusSensor(coordinator),
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
        if state == "awaiting_solar_surplus":
            return "Waiting for solar surplus"
        if state == "not_enough_power":
            return "Not enough surplus to charge"
        # state == "charging"
        kw = round(ev.ev_charge_power_w / 1000, 1)
        if inp.grid_import_w > 0:
            return f"Charging EV at {kw} kW with grid headroom"
        return f"Charging EV at {kw} kW from solar surplus"

    @property
    def available(self) -> bool:
        return self.coordinator.data is not None


class EmsInverterStatusSensor(CoordinatorEntity[EmsFastLoopCoordinator], SensorEntity):
    _attr_unique_id = "ems_inverter_status"
    _attr_name = "EMS Inverter Status"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = list(_INVERTER_STATUS_LABELS.values())

    def __init__(self, coordinator: EmsFastLoopCoordinator) -> None:
        super().__init__(coordinator)

    @property
    def device_info(self):
        return {"identifiers": {(DOMAIN, "ems")}, "name": "EMS", "manufacturer": "EMS"}

    @property
    def native_value(self) -> str | None:
        if self.coordinator.data is None:
            return None
        return _INVERTER_STATUS_LABELS.get(self.coordinator.data.inverter_decision.status)

    @property
    def extra_state_attributes(self) -> dict | None:
        if self.coordinator.data is None:
            return None
        data = self.coordinator.data
        inv = data.inverter_decision
        inp = data.inputs
        pv = inp.pv_w
        house = inp.house_load_w
        ev = inp.ev_power_w
        house_non_ev = max(0.0, house - ev)

        if inv.status == "controlling":
            ap = inv.active_power_w
            reason = (
                f"EV drawing {ev:.0f} W. Inverter set to power-control mode;"
                " directing PV to AC (battery neither charged nor drained)."
            )
            formula = (
                f"active_power = -min(pv_w, house_load_w)"
                f" = -min({pv:.0f}, {house:.0f}) = {ap:.0f} W"
            )
        elif inv.status == "peak_shaving":
            ap = inv.active_power_w
            grid = inp.grid_import_w
            max_imp = inp.thresholds.max_grid_import_w
            reason = _INVERTER_STATUS_REASONS["peak_shaving"]
            formula = (
                f"active_power = -(pv_w + (grid_import_w - max_grid_import_w))"
                f" = -({pv:.0f} + ({grid:.0f} - {max_imp:.0f})) = {ap:.0f} W"
            )
        elif inv.status == "holding_battery":
            ap = 0.0
            reason = _INVERTER_STATUS_REASONS["holding_battery"]
            formula = "active_power = 0 W (battery discharge suppressed)"
        elif inv.status == "starting_up":
            ap = -min(pv, house)
            reason = _INVERTER_STATUS_REASONS["starting_up"]
            formula = (
                f"Would write: -min({pv:.0f}, {house:.0f})"
                f" = {ap:.0f} W (suppressed during startup)"
            )
        else:
            ap = None
            reason = _INVERTER_STATUS_REASONS.get(inv.status, "")
            formula = "Not writing active_power."

        return {
            "reason": reason,
            "formula": formula,
            "pv_w": pv,
            "house_load_w": house,
            "ev_power_w": ev,
            "house_non_ev_w": house_non_ev,
            "active_power_w": ap,
        }

    @property
    def available(self) -> bool:
        return self.coordinator.data is not None


class EmsBatteryStatusSensor(CoordinatorEntity[EmsFastLoopCoordinator], SensorEntity):
    """Prose status for the battery — Reason > Action > SoC priority ordering."""

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

        soc_str = f" · {round(inp.battery_soc)}%" if inp.battery_soc is not None else ""

        # Reason rows — override power-balance when EMS has an explicit intent
        if inv.status == "starting_up":
            return "Starting up…"
        if inv.status == "peak_shaving":
            return f"Discharging to limit grid import{soc_str}"
        if inv.status == "holding_battery":
            return f"Saving solar charge for tonight{soc_str}"
        if inv.status == "holding_battery_cheap":
            return f"Drawing from grid — electricity is cheap now{soc_str}"

        # Action rows — derived from power balance
        battery_w = inp.pv_w + inp.grid_import_w - inp.house_load_w

        if (
            inp.battery_soc is not None
            and inp.battery_soc >= inp.battery_target_soc
            and abs(battery_w) <= _BATTERY_POWER_DEADBAND_W
        ):
            return f"Battery full — {round(inp.battery_soc)}% reached"

        shortfall = solar_shortfall_kwh(inp)
        shortfall_str = (
            f" · solar short by {shortfall:.1f} kWh"
            if shortfall is not None and shortfall > 0.0
            else ""
        )

        if battery_w > _BATTERY_POWER_DEADBAND_W:
            kw = round(abs(battery_w) / 1000, 1)
            return f"Charging from solar at {kw} kW{soc_str}{shortfall_str}"
        if battery_w < -_BATTERY_POWER_DEADBAND_W:
            kw = round(abs(battery_w) / 1000, 1)
            return f"Powering house at {kw} kW{soc_str}{shortfall_str}"
        return f"Idle{soc_str}{shortfall_str}"

    @property
    def available(self) -> bool:
        return self.coordinator.data is not None
