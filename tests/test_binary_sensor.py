from __future__ import annotations

import datetime
from unittest.mock import MagicMock

from custom_components.ems.binary_sensor import EmsEvChargingBinarySensor
from custom_components.ems.models import Decision, LadderResult


def _make_coordinator(strategy: str | None, ev_charge_intent: bool = False) -> MagicMock:
    coordinator = MagicMock()
    if strategy is None:
        coordinator.data = None
    else:
        decision = Decision(
            strategy=strategy,
            active_power_w=0.0,
            ev_charge_power_w=3500.0 if strategy == "ev_charging" else 0.0,
            ev_charge_intent=ev_charge_intent,
        )
        coordinator.data = LadderResult(
            decision=decision,
            inputs=MagicMock(),
            computed_at=datetime.datetime(2026, 5, 4, 12, 0, 0, tzinfo=datetime.UTC),
        )
    return coordinator


def test_is_on_when_ev_charging():
    sensor = EmsEvChargingBinarySensor(_make_coordinator("ev_charging", ev_charge_intent=True))
    assert sensor.is_on is True


def test_is_on_when_intent_but_power_insufficient():
    # mode=Energy-optimised + plugged + SOC<target but below min charge threshold
    sensor = EmsEvChargingBinarySensor(_make_coordinator("ev_idle", ev_charge_intent=True))
    assert sensor.is_on is True


def test_is_off_when_ev_idle_no_intent():
    # mode=Off or unplugged or SOC at target
    sensor = EmsEvChargingBinarySensor(_make_coordinator("ev_idle", ev_charge_intent=False))
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


def test_is_on_even_when_coordinator_write_would_fail():
    # Verifies that a charging intent is reported regardless of hardware write success.
    # The coordinator wraps write_full in try-except so coordinator.data is always set.
    sensor = EmsEvChargingBinarySensor(_make_coordinator("ev_charging", ev_charge_intent=True))
    assert sensor.is_on is True
