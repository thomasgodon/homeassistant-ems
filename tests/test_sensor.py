from __future__ import annotations

import datetime
from unittest.mock import MagicMock

from custom_components.ems.models import Decision, LadderResult
from custom_components.ems.sensor import (
    EmsEvChargingStateSensor,
    EmsEvCurrentSensor,
    EmsInverterModeSensor,
)


def _make_coordinator(
    ev_charge_power_w: float | None,
    charging_state: str = "not_enough_power",
    ev_power_w: float = 0.0,
) -> MagicMock:
    coordinator = MagicMock()
    if ev_charge_power_w is None:
        coordinator.data = None
    else:
        decision = Decision(
            strategy="ev_charging" if ev_charge_power_w > 0.0 else "ev_idle",
            active_power_w=0.0,
            ev_charge_power_w=ev_charge_power_w,
            charging_state=charging_state if ev_charge_power_w == 0.0 else "charging",
        )
        coordinator.data = LadderResult(
            decision=decision,
            inputs=MagicMock(ev_power_w=ev_power_w),
            computed_at=datetime.datetime(2026, 5, 4, 12, 0, 0, tzinfo=datetime.UTC),
        )
    return coordinator


def test_shows_disabled_when_idle():
    sensor = EmsInverterModeSensor(_make_coordinator(0.0))
    assert sensor.native_value == "disabled"


def test_shows_power_control_when_charging():
    sensor = EmsInverterModeSensor(_make_coordinator(3500.0, ev_power_w=3500.0))
    assert sensor.native_value == "power_control"


def test_shows_disabled_when_ev_not_yet_drawing():
    # Strategy wants to charge but EV hasn't started drawing — inverter not yet written
    sensor = EmsInverterModeSensor(_make_coordinator(3500.0, ev_power_w=0.0))
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
    }
