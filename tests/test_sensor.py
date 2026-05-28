from __future__ import annotations

import datetime
from unittest.mock import MagicMock

import pytest

from custom_components.ems.models import EvDecision, InverterDecision, LadderResult, Thresholds
from custom_components.ems.sensor import (
    EmsBatteryEnergyToTargetSensor,
    EmsBatteryStatusSensor,
    EmsEvCurrentSensor,
    EmsEvStatusSensor,
    EmsGridStatusSensor,
    EmsInverterActivePowerSensor,
    EmsInverterModeSensor,
    EmsNetSolarRemainingSensor,
    EmsPvStatusSensor,
    EmsSolarBalanceSensor,
)

_DEFAULT_THRESHOLDS = Thresholds(
    max_grid_import_w=4500.0,
    ev_target_soc=80.0,
    ev_min_charge_w=1400.0,
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
    thresholds: Thresholds = _DEFAULT_THRESHOLDS,
    prices_today=None,
    battery_capacity_kwh: float | None = None,
    solar_forecast_remaining_kwh: float | None = None,
    house_load_forecast_remaining_kwh: float | None = None,
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
            status=inverter_status or default_status,  # type: ignore[arg-type]
        )
        mock_thresholds = MagicMock()
        mock_thresholds.max_grid_import_w = thresholds.max_grid_import_w
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
                thresholds=mock_thresholds,
                prices_today=prices_today,
                battery_capacity_kwh=battery_capacity_kwh,
                solar_forecast_remaining_kwh=solar_forecast_remaining_kwh,
                house_load_forecast_remaining_kwh=house_load_forecast_remaining_kwh,
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


def test_ev_status_not_enough_power():
    sensor = EmsEvStatusSensor(_make_coordinator(0.0, charging_state="not_enough_power"))
    assert sensor.native_value == "Not enough surplus to charge"


def test_ev_status_charging_from_solar_surplus():
    sensor = EmsEvStatusSensor(_make_coordinator(3500.0, grid_import_w=0.0))
    assert sensor.native_value == "Charging EV at 3.5 kW from solar surplus"


def test_ev_status_charging_with_grid_headroom():
    sensor = EmsEvStatusSensor(_make_coordinator(3500.0, grid_import_w=500.0))
    assert sensor.native_value == "Charging EV at 3.5 kW with grid headroom"


def test_ev_status_charging_negative_grid_is_solar():
    sensor = EmsEvStatusSensor(_make_coordinator(3500.0, grid_import_w=-200.0))
    assert sensor.native_value == "Charging EV at 3.5 kW from solar surplus"


def test_ev_status_none_when_no_data():
    sensor = EmsEvStatusSensor(_make_coordinator(None))
    assert sensor.native_value is None
    assert sensor.available is False


def test_ev_status_unique_id():
    sensor = EmsEvStatusSensor(_make_coordinator(0.0))
    assert sensor.unique_id == "ems_ev_status"


def test_ev_status_battery_short():
    sensor = EmsEvStatusSensor(_make_coordinator(0.0, charging_state="battery_short"))
    assert sensor.native_value == "Solar reserved for home battery"


# ── EmsInverterActivePowerSensor ──────────────────────────────────────────────

def test_active_power_shows_zero_when_no_data():
    sensor = EmsInverterActivePowerSensor(_make_coordinator(None))
    assert sensor.native_value == 0.0


def test_active_power_shows_zero_when_none():
    sensor = EmsInverterActivePowerSensor(_make_coordinator(0.0, active_power_w=None))
    assert sensor.native_value == 0.0


def test_active_power_shows_value_when_set():
    sensor = EmsInverterActivePowerSensor(
        _make_coordinator(3500.0, ev_power_w=3500.0, active_power_w=1500.0)
    )
    assert sensor.native_value == 1500.0


# ── EmsBatteryStatusSensor ────────────────────────────────────────────────────

def test_battery_status_starting_up():
    sensor = EmsBatteryStatusSensor(
        _make_coordinator(0.0, inverter_status="starting_up", battery_soc=60.0)
    )
    assert sensor.native_value == "Starting up…"


def test_battery_status_unavailable():
    # battery_soc=None and not starting_up → "Battery SoC unavailable"
    sensor = EmsBatteryStatusSensor(
        _make_coordinator(0.0, inverter_status="idle", battery_soc=None,
                          pv_w=1000.0, house_load_w=1000.0, grid_import_w=100.0)
    )
    assert sensor.native_value == "Battery SoC unavailable"


def test_battery_status_peak_shaving_with_soc():
    sensor = EmsBatteryStatusSensor(
        _make_coordinator(0.0, inverter_status="peak_shaving", battery_soc=75.0)
    )
    assert sensor.native_value == "Discharging to limit grid import · 75%"


def test_battery_status_peak_shaving_without_soc():
    # battery_soc=None → unavailable takes priority over peak_shaving
    # (starting_up → unavailable → peak_shaving in that order)
    sensor = EmsBatteryStatusSensor(
        _make_coordinator(0.0, inverter_status="peak_shaving", battery_soc=None)
    )
    assert sensor.native_value == "Battery SoC unavailable"


def test_battery_status_holding_for_evening_with_rank(monkeypatch):
    monkeypatch.setattr("custom_components.ems.sensor.cheap_hold_rank", lambda _: (1, 3))
    sensor = EmsBatteryStatusSensor(
        _make_coordinator(0.0, inverter_status="holding_battery", battery_soc=60.0)
    )
    assert sensor.native_value == "Holding for evening · 60% (1 of 3 cheap hours today)"


def test_battery_status_holding_for_evening_no_rank(monkeypatch):
    monkeypatch.setattr("custom_components.ems.sensor.cheap_hold_rank", lambda _: None)
    sensor = EmsBatteryStatusSensor(
        _make_coordinator(0.0, inverter_status="holding_battery", battery_soc=60.0)
    )
    assert sensor.native_value == "Holding for evening · 60%"


def test_battery_status_holding_for_evening_no_soc(monkeypatch):
    monkeypatch.setattr("custom_components.ems.sensor.cheap_hold_rank", lambda _: (2, 4))
    # battery_soc=None → "unavailable" beats "holding_for_evening"
    sensor = EmsBatteryStatusSensor(
        _make_coordinator(0.0, inverter_status="holding_battery", battery_soc=None)
    )
    assert sensor.native_value == "Battery SoC unavailable"


def test_battery_status_prioritising_battery():
    sensor = EmsBatteryStatusSensor(
        _make_coordinator(0.0, inverter_status="battery_priority", battery_soc=60.0)
    )
    assert sensor.native_value == "Prioritising battery — EV charging from grid · 60%"


def test_battery_status_full():
    # pv=2000, grid=0, house=2100 → balance=-100 W (within ±200 deadband), soc=80 >= target=80
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
    # battery_soc=None → "Battery SoC unavailable" before idle can show
    sensor = EmsBatteryStatusSensor(
        _make_coordinator(
            0.0, inverter_status="idle",
            pv_w=1000.0, grid_import_w=0.0, house_load_w=900.0,
            battery_soc=None,
        )
    )
    assert sensor.native_value == "Battery SoC unavailable"


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


def test_battery_status_eod_suffix_with_shortfall(monkeypatch):
    # Monkeypatch both helpers to simulate "forecast available, short 1.2 kWh, EOD 76%"
    monkeypatch.setattr("custom_components.ems.sensor.solar_shortfall_kwh", lambda _: 1.2)
    monkeypatch.setattr("custom_components.ems.sensor.expected_eod_soc", lambda _: 76.0)
    sensor = EmsBatteryStatusSensor(
        _make_coordinator(
            0.0, inverter_status="idle",
            pv_w=5000.0, grid_import_w=-1000.0, house_load_w=1000.0,
            battery_soc=60.0, battery_target_soc=80.0,
        )
    )
    expected = "Charging from solar at 3.0 kW · 60% · forecast EOD 76%, short 1.2 kWh"
    assert sensor.native_value == expected


def test_battery_status_eod_suffix_without_shortfall(monkeypatch):
    # Solar covers the gap → eod_str = " · forecast EOD N%" without "short"
    monkeypatch.setattr("custom_components.ems.sensor.solar_shortfall_kwh", lambda _: -0.5)
    monkeypatch.setattr("custom_components.ems.sensor.expected_eod_soc", lambda _: 85.0)
    sensor = EmsBatteryStatusSensor(
        _make_coordinator(
            0.0, inverter_status="idle",
            pv_w=5000.0, grid_import_w=-1000.0, house_load_w=1000.0,
            battery_soc=60.0, battery_target_soc=80.0,
        )
    )
    assert sensor.native_value == "Charging from solar at 3.0 kW · 60% · forecast EOD 85%"


def test_battery_status_no_eod_suffix_when_forecast_missing(monkeypatch):
    # expected_eod_soc returns None → no suffix
    monkeypatch.setattr("custom_components.ems.sensor.expected_eod_soc", lambda _: None)
    sensor = EmsBatteryStatusSensor(
        _make_coordinator(
            0.0, inverter_status="idle",
            pv_w=1000.0, grid_import_w=0.0, house_load_w=900.0,
            battery_soc=50.0, battery_target_soc=80.0,
        )
    )
    assert "forecast" not in sensor.native_value


def test_battery_status_no_eod_suffix_on_battery_full(monkeypatch):
    # battery_full row never shows eod_str
    monkeypatch.setattr("custom_components.ems.sensor.solar_shortfall_kwh", lambda _: 9.9)
    monkeypatch.setattr("custom_components.ems.sensor.expected_eod_soc", lambda _: 100.0)
    sensor = EmsBatteryStatusSensor(
        _make_coordinator(
            0.0, inverter_status="idle",
            pv_w=2000.0, grid_import_w=0.0, house_load_w=2100.0,
            battery_soc=80.0, battery_target_soc=80.0,
        )
    )
    assert sensor.native_value == "Battery full — 80% reached"


def test_battery_status_attributes_present():
    sensor = EmsBatteryStatusSensor(
        _make_coordinator(
            0.0, inverter_status="idle",
            pv_w=5000.0, grid_import_w=-1000.0, house_load_w=1000.0,
            battery_soc=60.0, battery_target_soc=80.0,
        )
    )
    attrs = sensor.extra_state_attributes
    assert attrs["focus"] == "charging_from_solar"
    assert attrs["power_w"] == pytest.approx(3000.0)
    assert attrs["soc"] == 60.0
    assert attrs["target_soc"] == 80.0


def test_battery_status_attributes_none_when_no_data():
    sensor = EmsBatteryStatusSensor(_make_coordinator(None))
    assert sensor.extra_state_attributes is None


# ── EmsPvStatusSensor ─────────────────────────────────────────────────────────

def test_pv_status_starting_up():
    sensor = EmsPvStatusSensor(
        _make_coordinator(0.0, inverter_status="starting_up", pv_w=3000.0, house_load_w=1000.0)
    )
    assert sensor.native_value == "Starting up…"


def test_pv_status_no_production():
    sensor = EmsPvStatusSensor(
        _make_coordinator(0.0, pv_w=50.0, house_load_w=1000.0, grid_import_w=950.0)
    )
    assert sensor.native_value == "No solar production"


def test_pv_status_to_house():
    # pv=3000, house=3000, battery_w=0 → pv split: to_house=3000 → 100% to_house
    sensor = EmsPvStatusSensor(
        _make_coordinator(0.0, pv_w=3000.0, house_load_w=3000.0, ev_power_w=0.0, grid_import_w=0.0)
    )
    assert sensor.native_value == "Powering the house at 3.0 kW"


def test_pv_status_to_battery():
    # pv=5000, house=1000, grid=-4000 → battery_w=0 but pv>house so to_grid=4000 (80%)
    # Hmm, 4000/5000=80%≥70% → exporting. Let me think.
    # pv=5000, house=1000, grid=0 → battery_w=5000+0-1000=4000 → to_battery=4000 (80%)
    sensor = EmsPvStatusSensor(
        _make_coordinator(0.0, pv_w=5000.0, house_load_w=1000.0, ev_power_w=0.0, grid_import_w=0.0)
    )
    assert sensor.native_value == "Charging the battery at 5.0 kW"


def test_pv_status_exporting():
    # pv=5000, house=500, grid=-4500 → battery_w=0; pv→house=500, rest→grid=4500 (90%)
    sensor = EmsPvStatusSensor(
        _make_coordinator(0.0, pv_w=5000.0, house_load_w=500.0, ev_power_w=0.0,
                          grid_import_w=-4500.0)
    )
    assert sensor.native_value == "Exporting 5.0 kW to grid"


def test_pv_status_mixed():
    # pv=3000, house=1000, battery_w=1000, grid=-1000 → each ~33% → mixed
    sensor = EmsPvStatusSensor(
        _make_coordinator(0.0, pv_w=3000.0, house_load_w=1000.0, ev_power_w=0.0,
                          grid_import_w=-1000.0)
    )
    # to_house=1000(33%), to_battery=min(2000,1000)=1000(33%), to_grid=1000(33%) → mixed
    assert "Producing 3.0 kW:" in sensor.native_value


def test_pv_status_none_when_no_data():
    sensor = EmsPvStatusSensor(_make_coordinator(None))
    assert sensor.native_value is None
    assert sensor.available is False


def test_pv_status_unique_id():
    sensor = EmsPvStatusSensor(_make_coordinator(0.0))
    assert sensor.unique_id == "ems_pv_status"


def test_pv_status_attributes_present():
    sensor = EmsPvStatusSensor(
        _make_coordinator(0.0, pv_w=5000.0, house_load_w=1000.0, ev_power_w=0.0, grid_import_w=0.0)
    )
    attrs = sensor.extra_state_attributes
    assert attrs["focus"] == "to_battery"
    assert attrs["pv_w"] == 5000.0
    assert attrs["to_house_w"] == pytest.approx(1000.0)
    assert attrs["to_battery_w"] == pytest.approx(4000.0)


def test_pv_status_attributes_none_when_no_data():
    sensor = EmsPvStatusSensor(_make_coordinator(None))
    assert sensor.extra_state_attributes is None


# ── EmsGridStatusSensor ───────────────────────────────────────────────────────

def test_grid_status_starting_up():
    sensor = EmsGridStatusSensor(
        _make_coordinator(0.0, inverter_status="starting_up", grid_import_w=1000.0)
    )
    assert sensor.native_value == "Starting up…"


def test_grid_status_exporting():
    sensor = EmsGridStatusSensor(
        _make_coordinator(0.0, inverter_status="idle", grid_import_w=-500.0)
    )
    assert sensor.native_value == "Exporting 0.5 kW to grid"


def test_grid_status_balanced():
    sensor = EmsGridStatusSensor(
        _make_coordinator(0.0, inverter_status="idle", grid_import_w=50.0)
    )
    assert sensor.native_value == "Balanced (neither importing nor exporting)"


def test_grid_status_peak_shaving_active():
    sensor = EmsGridStatusSensor(
        _make_coordinator(0.0, inverter_status="peak_shaving", grid_import_w=5000.0)
    )
    assert sensor.native_value == "At cap — battery discharging to limit import (5.0 kW)"


def test_grid_status_at_peak_cap():
    # cap=4500, import=4400 → within 200 W proximity
    sensor = EmsGridStatusSensor(
        _make_coordinator(0.0, inverter_status="idle", grid_import_w=4400.0)
    )
    assert sensor.native_value == "At import cap of 4.5 kW"


def test_grid_status_importing():
    sensor = EmsGridStatusSensor(
        _make_coordinator(0.0, inverter_status="idle", grid_import_w=1000.0)
    )
    assert sensor.native_value == "Importing 1.0 kW (cap 4.5 kW, headroom 3.5 kW)"


def test_grid_status_none_when_no_data():
    sensor = EmsGridStatusSensor(_make_coordinator(None))
    assert sensor.native_value is None
    assert sensor.available is False


def test_grid_status_unique_id():
    sensor = EmsGridStatusSensor(_make_coordinator(0.0))
    assert sensor.unique_id == "ems_grid_status"


def test_grid_status_attributes_present():
    sensor = EmsGridStatusSensor(
        _make_coordinator(0.0, inverter_status="idle", grid_import_w=1000.0)
    )
    attrs = sensor.extra_state_attributes
    assert attrs["focus"] == "importing"
    assert attrs["import_w"] == 1000.0
    assert attrs["max_import_w"] == 4500.0
    assert attrs["headroom_w"] == pytest.approx(3500.0)


def test_grid_status_attributes_none_when_no_data():
    sensor = EmsGridStatusSensor(_make_coordinator(None))
    assert sensor.extra_state_attributes is None


# ── EmsBatteryEnergyToTargetSensor ───────────────────────────────────────────

def test_battery_energy_to_target_unique_id():
    sensor = EmsBatteryEnergyToTargetSensor(_make_coordinator(0.0))
    assert sensor.unique_id == "ems_battery_energy_to_target"


def test_battery_energy_to_target_returns_value(monkeypatch):
    monkeypatch.setattr("custom_components.ems.sensor.battery_gap_kwh", lambda _: 3.2)
    sensor = EmsBatteryEnergyToTargetSensor(_make_coordinator(0.0))
    assert sensor.native_value == 3200
    assert sensor.available is True


def test_battery_energy_to_target_unavailable_when_no_data():
    sensor = EmsBatteryEnergyToTargetSensor(_make_coordinator(None))
    assert sensor.native_value is None
    assert sensor.available is False


def test_battery_energy_to_target_unavailable_when_helper_returns_none(monkeypatch):
    monkeypatch.setattr("custom_components.ems.sensor.battery_gap_kwh", lambda _: None)
    sensor = EmsBatteryEnergyToTargetSensor(_make_coordinator(0.0))
    assert sensor.native_value is None
    assert sensor.available is False


# ── EmsNetSolarRemainingSensor ────────────────────────────────────────────────

def test_net_solar_remaining_unique_id():
    sensor = EmsNetSolarRemainingSensor(_make_coordinator(0.0))
    assert sensor.unique_id == "ems_net_solar_remaining"


def test_net_solar_remaining_returns_value(monkeypatch):
    monkeypatch.setattr("custom_components.ems.sensor.effective_solar_remaining_kwh", lambda _: 4.5)
    sensor = EmsNetSolarRemainingSensor(_make_coordinator(0.0))
    assert sensor.native_value == 4500
    assert sensor.available is True


def test_net_solar_remaining_unavailable_when_no_data():
    sensor = EmsNetSolarRemainingSensor(_make_coordinator(None))
    assert sensor.native_value is None
    assert sensor.available is False


def test_net_solar_remaining_unavailable_when_helper_returns_none(monkeypatch):
    monkeypatch.setattr(
        "custom_components.ems.sensor.effective_solar_remaining_kwh", lambda _: None
    )
    sensor = EmsNetSolarRemainingSensor(_make_coordinator(0.0))
    assert sensor.native_value is None
    assert sensor.available is False


# ── EmsSolarBalanceSensor ─────────────────────────────────────────────────────

def test_solar_balance_unique_id():
    sensor = EmsSolarBalanceSensor(_make_coordinator(0.0))
    assert sensor.unique_id == "ems_solar_balance"


def test_solar_balance_negative_when_shortage(monkeypatch):
    monkeypatch.setattr("custom_components.ems.sensor.solar_shortfall_kwh", lambda _: 2.1)
    sensor = EmsSolarBalanceSensor(_make_coordinator(0.0))
    assert sensor.native_value == -2100
    assert sensor.available is True


def test_solar_balance_positive_when_surplus(monkeypatch):
    monkeypatch.setattr("custom_components.ems.sensor.solar_shortfall_kwh", lambda _: -4.2)
    sensor = EmsSolarBalanceSensor(_make_coordinator(0.0))
    assert sensor.native_value == 4200
    assert sensor.available is True


def test_solar_balance_unavailable_when_no_data():
    sensor = EmsSolarBalanceSensor(_make_coordinator(None))
    assert sensor.native_value is None
    assert sensor.available is False


def test_solar_balance_unavailable_when_helper_returns_none(monkeypatch):
    monkeypatch.setattr("custom_components.ems.sensor.solar_shortfall_kwh", lambda _: None)
    sensor = EmsSolarBalanceSensor(_make_coordinator(0.0))
    assert sensor.native_value is None
    assert sensor.available is False
