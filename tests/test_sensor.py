from __future__ import annotations

import datetime
from unittest.mock import MagicMock

import pytest

from custom_components.ems.models import EvDecision, InverterDecision, LadderResult
from custom_components.ems.sensor import (
    EmsBatteryActionSensor,
    EmsEvChargingStateSensor,
    EmsEvCurrentSensor,
    EmsInverterActivePowerSensor,
    EmsInverterModeSensor,
    EmsInverterStatusSensor,
)


def _make_coordinator(
    ev_charge_power_w: float | None,
    charging_state: str = "not_enough_power",
    ev_power_w: float = 0.0,
    active_power_w: float | None = None,
    inverter_status: str | None = None,
    pv_w: float = 6000.0,
    house_load_w: float = 2000.0,
    grid_import_w: float = 0.0,
) -> MagicMock:
    coordinator = MagicMock()
    if ev_charge_power_w is None:
        coordinator.data = None
    else:
        ev_decision = EvDecision(
            strategy="ev_charging" if ev_charge_power_w > 0.0 else "ev_idle",
            ev_charge_power_w=ev_charge_power_w,
            charging_state=charging_state if ev_charge_power_w == 0.0 else "charging",
        )
        default_status = "controlling" if ev_power_w > 0.0 else "idle"
        inverter_decision = InverterDecision(
            inverter_verb="power_control" if ev_power_w > 0.0 else "disabled",
            active_power_w=active_power_w,
            status=inverter_status or default_status,
        )
        coordinator.data = LadderResult(
            ev_decision=ev_decision,
            inverter_decision=inverter_decision,
            inputs=MagicMock(
                ev_power_w=ev_power_w,
                pv_w=pv_w,
                house_load_w=house_load_w,
                grid_import_w=grid_import_w,
            ),
            computed_at=datetime.datetime(2026, 5, 4, 12, 0, 0, tzinfo=datetime.UTC),
        )
    return coordinator


# ── EmsInverterModeSensor ─────────────────────────────────────────────────────

def test_shows_disabled_when_idle():
    sensor = EmsInverterModeSensor(_make_coordinator(0.0))
    assert sensor.native_value == "disabled"


def test_shows_power_control_when_charging():
    sensor = EmsInverterModeSensor(_make_coordinator(3500.0, ev_power_w=3500.0))
    assert sensor.native_value == "power_control"


def test_shows_disabled_when_ev_not_yet_drawing():
    # Strategy wants to charge but EV hasn't started drawing — inverter still disabled
    sensor = EmsInverterModeSensor(_make_coordinator(3500.0, ev_power_w=0.0))
    assert sensor.native_value == "disabled"


def test_inverter_mode_shows_none_verb_as_disabled():
    """When inverter_verb is None (Off mode), sensor falls back to 'disabled'."""
    coordinator = MagicMock()
    ev_decision = EvDecision(
        strategy="ev_idle", ev_charge_power_w=0.0, charging_state="charging_disabled"
    )
    inverter_decision = InverterDecision(inverter_verb=None, active_power_w=None)
    coordinator.data = LadderResult(
        ev_decision=ev_decision,
        inverter_decision=inverter_decision,
        inputs=MagicMock(),
        computed_at=datetime.datetime(2026, 5, 4, 12, 0, 0, tzinfo=datetime.UTC),
    )
    sensor = EmsInverterModeSensor(coordinator)
    assert sensor.native_value == "disabled"


def test_unavailable_when_no_data():
    sensor = EmsInverterModeSensor(_make_coordinator(None))
    assert sensor.native_value is None
    assert sensor.available is False


def test_available_when_data_present():
    sensor = EmsInverterModeSensor(_make_coordinator(0.0))
    assert sensor.available is True


def test_unique_id():
    sensor = EmsInverterModeSensor(_make_coordinator(0.0))
    assert sensor.unique_id == "ems_inverter_mode"


# ── EmsEvCurrentSensor ────────────────────────────────────────────────────────

def test_ev_current_shows_zero_when_idle():
    sensor = EmsEvCurrentSensor(_make_coordinator(0.0))
    assert sensor.native_value == 0.0


def test_ev_current_shows_watts_when_charging():
    sensor = EmsEvCurrentSensor(_make_coordinator(3500.0))
    assert sensor.native_value == 3500.0


def test_ev_current_shows_zero_when_no_data():
    sensor = EmsEvCurrentSensor(_make_coordinator(None))
    assert sensor.native_value == 0.0
    assert sensor.available is True


def test_ev_current_unique_id():
    sensor = EmsEvCurrentSensor(_make_coordinator(0.0))
    assert sensor.unique_id == "ems_ev_current_w"


# ── EmsEvChargingStateSensor ──────────────────────────────────────────────────

def test_charging_state_shows_charging():
    sensor = EmsEvChargingStateSensor(_make_coordinator(3500.0))
    assert sensor.native_value == "Charging"


def test_charging_state_shows_not_enough_power():
    sensor = EmsEvChargingStateSensor(_make_coordinator(0.0, charging_state="not_enough_power"))
    assert sensor.native_value == "Not enough power"


def test_charging_state_shows_ev_not_connected():
    sensor = EmsEvChargingStateSensor(_make_coordinator(0.0, charging_state="ev_not_connected"))
    assert sensor.native_value == "EV not connected"


def test_charging_state_shows_charging_disabled():
    sensor = EmsEvChargingStateSensor(_make_coordinator(0.0, charging_state="charging_disabled"))
    assert sensor.native_value == "Charging disabled"


def test_charging_state_shows_target_soc_reached():
    sensor = EmsEvChargingStateSensor(_make_coordinator(0.0, charging_state="target_soc_reached"))
    assert sensor.native_value == "Target SoC reached"


def test_charging_state_shows_starting_up():
    sensor = EmsEvChargingStateSensor(_make_coordinator(0.0, charging_state="starting_up"))
    assert sensor.native_value == "Starting up"


def test_charging_state_none_when_no_data():
    sensor = EmsEvChargingStateSensor(_make_coordinator(None))
    assert sensor.native_value is None
    assert sensor.available is False


def test_charging_state_unique_id():
    sensor = EmsEvChargingStateSensor(_make_coordinator(0.0))
    assert sensor.unique_id == "ems_ev_charging_state"


def test_charging_state_options_cover_all_states():
    sensor = EmsEvChargingStateSensor(_make_coordinator(0.0))
    assert set(sensor.options) == {
        "Charging",
        "EV not connected",
        "Charging disabled",
        "Target SoC reached",
        "Not enough power",
        "Starting up",
        "Outside cheap hours",
        "Awaiting solar surplus",
    }


# ── EmsInverterActivePowerSensor ──────────────────────────────────────────────

def test_active_power_shows_zero_when_no_data():
    sensor = EmsInverterActivePowerSensor(_make_coordinator(None))
    assert sensor.native_value == 0.0


def test_active_power_shows_zero_when_none():
    """active_power_w=None (not writing) shows 0 in the sensor."""
    sensor = EmsInverterActivePowerSensor(_make_coordinator(0.0, active_power_w=None))
    assert sensor.native_value == 0.0


def test_active_power_shows_value_when_set():
    sensor = EmsInverterActivePowerSensor(
        _make_coordinator(3500.0, ev_power_w=3500.0, active_power_w=1500.0)
    )
    assert sensor.native_value == 1500.0


# ── EmsInverterStatusSensor ───────────────────────────────────────────────────

def test_inverter_status_controlling():
    sensor = EmsInverterStatusSensor(
        _make_coordinator(3500.0, ev_power_w=3500.0, active_power_w=4500.0,
                          inverter_status="controlling", pv_w=6000.0, house_load_w=5500.0)
    )
    assert sensor.native_value == "Controlling"


def test_inverter_status_idle():
    sensor = EmsInverterStatusSensor(_make_coordinator(0.0, inverter_status="idle"))
    assert sensor.native_value == "Idle"


def test_inverter_status_disabled():
    sensor = EmsInverterStatusSensor(_make_coordinator(0.0, inverter_status="disabled"))
    assert sensor.native_value == "Disabled"


def test_inverter_status_starting_up():
    sensor = EmsInverterStatusSensor(_make_coordinator(0.0, inverter_status="starting_up"))
    assert sensor.native_value == "Starting up"


def test_inverter_status_none_when_no_data():
    sensor = EmsInverterStatusSensor(_make_coordinator(None))
    assert sensor.native_value is None
    assert sensor.available is False


def test_inverter_status_available_when_data_present():
    sensor = EmsInverterStatusSensor(_make_coordinator(0.0))
    assert sensor.available is True


def test_inverter_status_unique_id():
    sensor = EmsInverterStatusSensor(_make_coordinator(0.0))
    assert sensor.unique_id == "ems_inverter_status"


def test_inverter_status_options_cover_all_states():
    sensor = EmsInverterStatusSensor(_make_coordinator(0.0))
    assert set(sensor.options) == {
        "Controlling", "Idle", "Disabled", "Starting up", "Peak shaving",
        "Holding battery", "Holding battery (cheap hour)",
    }


def test_inverter_status_attributes_none_when_no_data():
    sensor = EmsInverterStatusSensor(_make_coordinator(None))
    assert sensor.extra_state_attributes is None


def test_inverter_status_attributes_controlling():
    sensor = EmsInverterStatusSensor(
        _make_coordinator(3500.0, ev_power_w=3500.0, active_power_w=4500.0,
                          inverter_status="controlling", pv_w=6000.0, house_load_w=5500.0)
    )
    attrs = sensor.extra_state_attributes
    assert attrs["pv_w"] == 6000.0
    assert attrs["house_load_w"] == 5500.0
    assert attrs["ev_power_w"] == 3500.0
    assert attrs["house_non_ev_w"] == max(0.0, 5500.0 - 3500.0)
    assert attrs["active_power_w"] == 4500.0
    assert "EV drawing 3500 W" in attrs["reason"]
    assert "-min(" in attrs["formula"]
    assert "4500" in attrs["formula"]


def test_inverter_status_attributes_idle():
    sensor = EmsInverterStatusSensor(
        _make_coordinator(0.0, inverter_status="idle", pv_w=3000.0, house_load_w=1000.0)
    )
    attrs = sensor.extra_state_attributes
    assert attrs["active_power_w"] is None
    assert attrs["formula"] == "Not writing active_power."
    assert "No EV draw" in attrs["reason"]


def test_inverter_status_attributes_disabled():
    sensor = EmsInverterStatusSensor(
        _make_coordinator(0.0, inverter_status="disabled")
    )
    attrs = sensor.extra_state_attributes
    assert attrs["active_power_w"] is None
    assert "Off" in attrs["reason"]


def test_inverter_status_attributes_starting_up():
    sensor = EmsInverterStatusSensor(
        _make_coordinator(0.0, inverter_status="starting_up", pv_w=5000.0, house_load_w=1000.0)
    )
    attrs = sensor.extra_state_attributes
    assert "startup" in attrs["formula"].lower() or "suppressed" in attrs["formula"]
    assert "Startup grace" in attrs["reason"]
    assert attrs["active_power_w"] == pytest.approx(-min(5000.0, 1000.0))


# ── EmsBatteryActionSensor ────────────────────────────────────────────────────

def test_battery_action_charging_from_solar():
    # pv=5000, grid=-1000 (exporting), house=1000 → balance=3000 → charging
    sensor = EmsBatteryActionSensor(
        _make_coordinator(0.0, pv_w=5000.0, grid_import_w=-1000.0, house_load_w=1000.0)
    )
    assert sensor.native_value == "Charging from solar"


def test_battery_action_idle_balanced():
    # pv=0, grid=2000, house=2000 → balance=0 → idle
    sensor = EmsBatteryActionSensor(
        _make_coordinator(0.0, pv_w=0.0, grid_import_w=2000.0, house_load_w=2000.0)
    )
    assert sensor.native_value == "Idle"


def test_battery_action_discharging():
    # pv=500, grid=500, house=2000 → balance=-1000 → discharging
    sensor = EmsBatteryActionSensor(
        _make_coordinator(0.0, pv_w=500.0, grid_import_w=500.0, house_load_w=2000.0)
    )
    assert sensor.native_value == "Discharging"


def test_battery_action_idle_within_positive_deadband():
    # pv=1000, grid=0, house=900 → balance=+100 < 200 → idle
    sensor = EmsBatteryActionSensor(
        _make_coordinator(0.0, pv_w=1000.0, grid_import_w=0.0, house_load_w=900.0)
    )
    assert sensor.native_value == "Idle"


def test_battery_action_idle_within_negative_deadband():
    # pv=1000, grid=0, house=1100 → balance=-100 > -200 → idle
    sensor = EmsBatteryActionSensor(
        _make_coordinator(0.0, pv_w=1000.0, grid_import_w=0.0, house_load_w=1100.0)
    )
    assert sensor.native_value == "Idle"


def test_battery_action_charging_just_over_deadband():
    # pv=1000, grid=0, house=750 → balance=+250 > 200 → charging
    sensor = EmsBatteryActionSensor(
        _make_coordinator(0.0, pv_w=1000.0, grid_import_w=0.0, house_load_w=750.0)
    )
    assert sensor.native_value == "Charging from solar"


def test_battery_action_none_when_no_data():
    sensor = EmsBatteryActionSensor(_make_coordinator(None))
    assert sensor.native_value is None
    assert sensor.available is False


def test_battery_action_available_when_data_present():
    sensor = EmsBatteryActionSensor(_make_coordinator(0.0))
    assert sensor.available is True


def test_battery_action_unique_id():
    sensor = EmsBatteryActionSensor(_make_coordinator(0.0))
    assert sensor.unique_id == "ems_battery_action"


def test_battery_action_options_cover_all_states():
    sensor = EmsBatteryActionSensor(_make_coordinator(0.0))
    assert set(sensor.options) == {"Charging from solar", "Discharging", "Idle"}


def test_battery_action_attributes_contain_signed_value():
    # pv=5000, grid=-1000, house=1000 → balance=3000
    sensor = EmsBatteryActionSensor(
        _make_coordinator(0.0, pv_w=5000.0, grid_import_w=-1000.0, house_load_w=1000.0)
    )
    attrs = sensor.extra_state_attributes
    assert attrs["battery_action_w"] == pytest.approx(3000.0)
    assert attrs["pv_w"] == 5000.0
    assert attrs["grid_import_w"] == -1000.0
    assert attrs["house_load_w"] == 1000.0
    assert attrs["deadband_w"] == 200.0
    assert "formula" in attrs


def test_battery_action_attributes_none_when_no_data():
    sensor = EmsBatteryActionSensor(_make_coordinator(None))
    assert sensor.extra_state_attributes is None
