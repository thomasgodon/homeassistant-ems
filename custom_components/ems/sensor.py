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

_INVERTER_STATUS_LABELS: dict[str, str] = {
    "controlling": "Controlling",
    "idle": "Idle",
    "disabled": "Disabled",
    "starting_up": "Starting up",
}

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
        EmsInverterStatusSensor(coordinator),
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
