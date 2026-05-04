from __future__ import annotations

import datetime
from unittest.mock import MagicMock

from custom_components.ems.models import Decision, LadderResult
from custom_components.ems.sensor import EmsInverterModeSensor


def _make_coordinator(ev_charge_power_w: float | None) -> MagicMock:
    coordinator = MagicMock()
    if ev_charge_power_w is None:
        coordinator.data = None
    else:
        decision = Decision(
            strategy="ev_charging" if ev_charge_power_w > 0.0 else "ev_idle",
            active_power_w=0.0,
            ev_charge_power_w=ev_charge_power_w,
            ev_charge_intent=ev_charge_power_w > 0.0,
        )
        coordinator.data = LadderResult(
            decision=decision,
            inputs=MagicMock(),
            computed_at=datetime.datetime(2026, 5, 4, 12, 0, 0, tzinfo=datetime.UTC),
        )
    return coordinator


def test_shows_disabled_when_idle():
    sensor = EmsInverterModeSensor(_make_coordinator(0.0))
    assert sensor.native_value == "disabled"


def test_shows_power_control_when_charging():
    sensor = EmsInverterModeSensor(_make_coordinator(3500.0))
    assert sensor.native_value == "power_control"


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
