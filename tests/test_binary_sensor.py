from __future__ import annotations

import datetime
from unittest.mock import MagicMock

from custom_components.ems.binary_sensor import EmsEvChargingBinarySensor
from custom_components.ems.models import Decision, LadderResult


def _make_coordinator(strategy: str | None) -> MagicMock:
    coordinator = MagicMock()
    if strategy is None:
        coordinator.data = None
    else:
        decision = Decision(
            strategy=strategy,
            active_power_w=0.0,
            ev_charge_power_w=3500.0 if strategy == "ev_charging" else 0.0,
        )
        coordinator.data = LadderResult(
            decision=decision,
            inputs=MagicMock(),
            computed_at=datetime.datetime(2026, 5, 4, 12, 0, 0, tzinfo=datetime.timezone.utc),
        )
    return coordinator


def test_is_on_when_ev_charging():
    sensor = EmsEvChargingBinarySensor(_make_coordinator("ev_charging"))
    assert sensor.is_on is True


def test_is_off_when_ev_idle():
    sensor = EmsEvChargingBinarySensor(_make_coordinator("ev_idle"))
    assert sensor.is_on is False


def test_is_off_when_no_data():
    sensor = EmsEvChargingBinarySensor(_make_coordinator(None))
    assert sensor.is_on is False


def test_always_available():
    sensor = EmsEvChargingBinarySensor(_make_coordinator(None))
    assert sensor.available is True


def test_unique_id():
    sensor = EmsEvChargingBinarySensor(_make_coordinator("ev_idle"))
    assert sensor.unique_id == "ems_ev_charging"
