from __future__ import annotations

import datetime
from unittest.mock import MagicMock

import pytest

from custom_components.ems.models import EvDecision, InverterDecision, LadderResult
from custom_components.ems.sensor import (
    EmsBatteryStatusSensor,
    EmsEvCurrentSensor,
    EmsEvStatusSensor,
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
    battery_soc: float | None = None,
    battery_target_soc: float = 80.0,
    ev_soc: float | None = None,
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
                battery_soc=battery_soc,
                battery_target_soc=battery_target_soc,
                ev_soc=ev_soc,
                # Forecast fields default to None so solar_shortfall_kwh returns None
                # (no shortfall suffix) unless the test explicitly monkeypatches the helper.
                battery_capacity_kwh=None,
                solar_forecast_remaining_kwh=None,
                house_load_forecast_remaining_kwh=None,
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


# ── EmsEvStatusSensor ─────────────────────────────────────────────────────────

def test_ev_status_starting_up():
    sensor = EmsEvStatusSensor(_make_coordinator(0.0, charging_state="starting_up"))
    assert sensor.native_value == "Starting up…"


def test_ev_status_charging_disabled():
    sensor = EmsEvStatusSensor(_make_coordinator(0.0, charging_state="charging_disabled"))
    assert sensor.native_value == "EV charging disabled"


def test_ev_status_ev_not_connected():
    sensor = EmsEvStatusSensor(_make_coordinator(0.0, charging_state="ev_not_connected"))
    assert sensor.native_value == "EV not connected"


def test_ev_status_target_soc_reached_with_soc():
    sensor = EmsEvStatusSensor(
        _make_coordinator(0.0, charging_state="target_soc_reached", ev_soc=80.0)
    )
    assert sensor.native_value == "EV battery full (80% reached)"


def test_ev_status_target_soc_reached_without_soc():
    sensor = EmsEvStatusSensor(
        _make_coordinator(0.0, charging_state="target_soc_reached", ev_soc=None)
    )
    assert sensor.native_value == "EV battery full"


def test_ev_status_outside_cheap_hours():
    sensor = EmsEvStatusSensor(_make_coordinator(0.0, charging_state="outside_cheap_hours"))
    assert sensor.native_value == "Waiting for cheap hours"


def test_ev_status_awaiting_solar_surplus():
    sensor = EmsEvStatusSensor(_make_coordinator(0.0, charging_state="awaiting_solar_surplus"))
    assert sensor.native_value == "Waiting for solar surplus"


def test_ev_status_not_enough_power():
    sensor = EmsEvStatusSensor(_make_coordinator(0.0, charging_state="not_enough_power"))
    assert sensor.native_value == "Not enough surplus to charge"


def test_ev_status_charging_from_solar_surplus():
    # grid_import_w <= 0 → solar surplus
    sensor = EmsEvStatusSensor(
        _make_coordinator(3500.0, grid_import_w=0.0)
    )
    assert sensor.native_value == "Charging EV at 3.5 kW from solar surplus"


def test_ev_status_charging_with_grid_headroom():
    # grid_import_w > 0 → grid headroom
    sensor = EmsEvStatusSensor(
        _make_coordinator(3500.0, grid_import_w=500.0)
    )
    assert sensor.native_value == "Charging EV at 3.5 kW with grid headroom"


def test_ev_status_charging_negative_grid_is_solar():
    # Exporting to grid — definitely on solar surplus
    sensor = EmsEvStatusSensor(
        _make_coordinator(3500.0, grid_import_w=-200.0)
    )
    assert sensor.native_value == "Charging EV at 3.5 kW from solar surplus"


def test_ev_status_none_when_no_data():
    sensor = EmsEvStatusSensor(_make_coordinator(None))
    assert sensor.native_value is None
    assert sensor.available is False


def test_ev_status_unique_id():
    sensor = EmsEvStatusSensor(_make_coordinator(0.0))
    assert sensor.unique_id == "ems_ev_status"


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
        "Holding battery",
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


# ── EmsBatteryStatusSensor ────────────────────────────────────────────────────

def test_battery_status_starting_up():
    sensor = EmsBatteryStatusSensor(
        _make_coordinator(0.0, inverter_status="starting_up", battery_soc=60.0)
    )
    assert sensor.native_value == "Starting up…"


def test_battery_status_peak_shaving_with_soc():
    sensor = EmsBatteryStatusSensor(
        _make_coordinator(0.0, inverter_status="peak_shaving", battery_soc=75.0)
    )
    assert sensor.native_value == "Discharging to limit grid import · 75%"


def test_battery_status_peak_shaving_without_soc():
    sensor = EmsBatteryStatusSensor(
        _make_coordinator(0.0, inverter_status="peak_shaving", battery_soc=None)
    )
    assert sensor.native_value == "Discharging to limit grid import"


def test_battery_status_holding_battery_with_rank(monkeypatch):
    monkeypatch.setattr("custom_components.ems.sensor.cheap_hold_rank", lambda _: (1, 3))
    sensor = EmsBatteryStatusSensor(
        _make_coordinator(0.0, inverter_status="holding_battery", battery_soc=60.0)
    )
    assert sensor.native_value == "Drawing cheap grid · 60% (1 of 3 cheap hours today)"


def test_battery_status_holding_battery_no_rank(monkeypatch):
    monkeypatch.setattr("custom_components.ems.sensor.cheap_hold_rank", lambda _: None)
    sensor = EmsBatteryStatusSensor(
        _make_coordinator(0.0, inverter_status="holding_battery", battery_soc=60.0)
    )
    assert sensor.native_value == "Drawing cheap grid · 60%"


def test_battery_status_holding_battery_no_soc(monkeypatch):
    monkeypatch.setattr("custom_components.ems.sensor.cheap_hold_rank", lambda _: (2, 4))
    sensor = EmsBatteryStatusSensor(
        _make_coordinator(0.0, inverter_status="holding_battery", battery_soc=None)
    )
    assert sensor.native_value == "Drawing cheap grid (2 of 4 cheap hours today)"


def test_battery_status_full():
    # pv=2000, grid=0, house=2100 → balance=-100 W (within ±200 deadband)
    sensor = EmsBatteryStatusSensor(
        _make_coordinator(
            0.0, inverter_status="idle",
            pv_w=2000.0, grid_import_w=0.0, house_load_w=2100.0,
            battery_soc=80.0, battery_target_soc=80.0,
        )
    )
    assert sensor.native_value == "Battery full — 80% reached"


def test_battery_status_charging_from_solar():
    # pv=5000, grid=-1000, house=1000 → balance=+3000 W > 200
    sensor = EmsBatteryStatusSensor(
        _make_coordinator(
            0.0, inverter_status="idle",
            pv_w=5000.0, grid_import_w=-1000.0, house_load_w=1000.0,
            battery_soc=60.0,
        )
    )
    assert sensor.native_value == "Charging from solar at 3.0 kW · 60%"


def test_battery_status_powering_house():
    # pv=500, grid=500, house=2000 → balance=-1000 W < -200
    sensor = EmsBatteryStatusSensor(
        _make_coordinator(
            0.0, inverter_status="idle",
            pv_w=500.0, grid_import_w=500.0, house_load_w=2000.0,
            battery_soc=70.0,
        )
    )
    assert sensor.native_value == "Powering house at 1.0 kW · 70%"


def test_battery_status_idle_with_soc():
    # pv=1000, grid=0, house=900 → balance=+100 W (within deadband, below target)
    sensor = EmsBatteryStatusSensor(
        _make_coordinator(
            0.0, inverter_status="idle",
            pv_w=1000.0, grid_import_w=0.0, house_load_w=900.0,
            battery_soc=50.0, battery_target_soc=80.0,
        )
    )
    assert sensor.native_value == "Idle · 50%"


def test_battery_status_idle_without_soc():
    # Same power balance, battery_soc=None
    sensor = EmsBatteryStatusSensor(
        _make_coordinator(
            0.0, inverter_status="idle",
            pv_w=1000.0, grid_import_w=0.0, house_load_w=900.0,
            battery_soc=None,
        )
    )
    assert sensor.native_value == "Idle"


def test_battery_status_reason_overrides_power_balance():
    # peak_shaving should win even if power balance says charging
    sensor = EmsBatteryStatusSensor(
        _make_coordinator(
            0.0, inverter_status="peak_shaving",
            pv_w=5000.0, grid_import_w=-1000.0, house_load_w=1000.0,
            battery_soc=75.0,
        )
    )
    assert "Discharging to limit grid import" in sensor.native_value


def test_battery_status_none_when_no_data():
    sensor = EmsBatteryStatusSensor(_make_coordinator(None))
    assert sensor.native_value is None
    assert sensor.available is False


def test_battery_status_available_when_data_present():
    sensor = EmsBatteryStatusSensor(_make_coordinator(0.0))
    assert sensor.available is True


def test_battery_status_unique_id():
    sensor = EmsBatteryStatusSensor(_make_coordinator(0.0))
    assert sensor.unique_id == "ems_battery_status"


def test_battery_status_shortfall_suffix_charging(monkeypatch):
    # pv=5000, grid=-1000, house=1000 → battery_w=+3000 > 200 → "Charging from solar..."
    monkeypatch.setattr("custom_components.ems.sensor.solar_shortfall_kwh", lambda _: 1.2)
    sensor = EmsBatteryStatusSensor(
        _make_coordinator(
            0.0, inverter_status="idle",
            pv_w=5000.0, grid_import_w=-1000.0, house_load_w=1000.0,
            battery_soc=60.0, battery_target_soc=80.0,
        )
    )
    assert sensor.native_value == "Charging from solar at 3.0 kW · 60% · solar short by 1.2 kWh"


def test_battery_status_shortfall_suffix_idle(monkeypatch):
    # pv=1000, grid=0, house=900 → battery_w=+100 (within deadband)
    monkeypatch.setattr("custom_components.ems.sensor.solar_shortfall_kwh", lambda _: 2.5)
    sensor = EmsBatteryStatusSensor(
        _make_coordinator(
            0.0, inverter_status="idle",
            pv_w=1000.0, grid_import_w=0.0, house_load_w=900.0,
            battery_soc=50.0, battery_target_soc=80.0,
        )
    )
    assert sensor.native_value == "Idle · 50% · solar short by 2.5 kWh"


def test_battery_status_no_shortfall_suffix_when_battery_full(monkeypatch):
    # battery_soc >= target and within deadband → "Battery full" row, no suffix
    monkeypatch.setattr("custom_components.ems.sensor.solar_shortfall_kwh", lambda _: 9.9)
    sensor = EmsBatteryStatusSensor(
        _make_coordinator(
            0.0, inverter_status="idle",
            pv_w=2000.0, grid_import_w=0.0, house_load_w=2100.0,
            battery_soc=80.0, battery_target_soc=80.0,
        )
    )
    assert sensor.native_value == "Battery full — 80% reached"


def test_battery_status_no_shortfall_suffix_when_reason_row(monkeypatch):
    # holding_battery is a reason row — suffix must not appear
    monkeypatch.setattr("custom_components.ems.sensor.solar_shortfall_kwh", lambda _: 3.0)
    sensor = EmsBatteryStatusSensor(
        _make_coordinator(0.0, inverter_status="holding_battery", battery_soc=60.0)
    )
    assert "solar short" not in sensor.native_value


def test_battery_status_no_shortfall_suffix_when_helper_returns_none(monkeypatch):
    # missing forecast inputs → helper returns None → no suffix
    monkeypatch.setattr("custom_components.ems.sensor.solar_shortfall_kwh", lambda _: None)
    sensor = EmsBatteryStatusSensor(
        _make_coordinator(
            0.0, inverter_status="idle",
            pv_w=1000.0, grid_import_w=0.0, house_load_w=900.0,
            battery_soc=50.0, battery_target_soc=80.0,
        )
    )
    assert "solar short" not in sensor.native_value


def test_battery_status_no_shortfall_suffix_when_solar_covers(monkeypatch):
    # helper returns negative → no shortfall, no suffix
    monkeypatch.setattr("custom_components.ems.sensor.solar_shortfall_kwh", lambda _: -0.5)
    sensor = EmsBatteryStatusSensor(
        _make_coordinator(
            0.0, inverter_status="idle",
            pv_w=1000.0, grid_import_w=0.0, house_load_w=900.0,
            battery_soc=50.0, battery_target_soc=80.0,
        )
    )
    assert "solar short" not in sensor.native_value
