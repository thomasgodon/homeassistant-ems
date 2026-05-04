"""Tests for strategy.decide() and strategy.recompute().

Grid headroom algorithm (power balance):
  house_non_ev  = max(0, house_load_w - ev_power_w)
  active_power  = pv_w - house_non_ev   (negative = discharge, positive = PV surplus into inverter)
  ev_target_w   = max(0, max_grid_import_w - grid_import_w + ev_power_w)
  ev_target_w   = min(ev_target_w, max_ev_charge_w)  if max_ev_charge_w is set
"""
from __future__ import annotations

import pytest

from custom_components.ems import strategy as strategy_module
from custom_components.ems.models import Decision, Thresholds

# ── fixtures / helpers ────────────────────────────────────────────────────────

_THRESHOLDS = Thresholds(
    max_grid_import_w=4500.0,
    ev_target_soc=80.0,
    ev_min_charge_w=1400.0,
)


def decide(builder, **overrides) -> Decision:
    return strategy_module.decide(builder.build(**overrides))


def recompute(builder, **overrides) -> Decision:
    inp = builder.build(**overrides)
    return strategy_module.recompute(
        pv_w=inp.pv_w,
        house_load_w=inp.house_load_w,
        ev_power_w=inp.ev_power_w,
        grid_import_w=inp.grid_import_w,
        ev_plugged=inp.ev_plugged,
        ev_soc=inp.ev_soc,
        ev_strategy=inp.ev_strategy,
        thresholds=inp.thresholds,
    )


# ── idle gates ────────────────────────────────────────────────────────────────

def test_idle_when_ev_not_plugged(builder) -> None:
    d = decide(builder, ev_plugged=False, ev_strategy="Energy-optimised")
    assert d.strategy == "ev_idle"
    assert d.ev_charge_power_w == 0.0
    assert d.charging_state == "ev_not_connected"


def test_idle_when_strategy_off(builder) -> None:
    d = decide(builder, ev_plugged=True, ev_strategy="Off")
    assert d.strategy == "ev_idle"
    assert d.ev_charge_power_w == 0.0
    assert d.charging_state == "charging_disabled"


def test_idle_when_soc_at_target(builder) -> None:
    d = decide(
        builder,
        ev_plugged=True,
        ev_strategy="Energy-optimised",
        ev_soc=80.0,
        thresholds=_THRESHOLDS,
    )
    assert d.strategy == "ev_idle"
    assert d.charging_state == "target_soc_reached"


def test_idle_when_soc_above_target(builder) -> None:
    d = decide(
        builder,
        ev_plugged=True,
        ev_strategy="Energy-optimised",
        ev_soc=95.0,
        thresholds=_THRESHOLDS,
    )
    assert d.strategy == "ev_idle"
    assert d.charging_state == "target_soc_reached"


def test_charges_when_soc_below_target(builder) -> None:
    d = decide(
        builder,
        ev_plugged=True,
        ev_strategy="Energy-optimised",
        ev_soc=50.0,
        grid_import_w=-1500.0,
        pv_w=2000.0,
        house_load_w=500.0,
        ev_power_w=0.0,
        thresholds=_THRESHOLDS,
    )
    assert d.strategy == "ev_charging"
    assert d.charging_state == "charging"


def test_charges_when_soc_is_none(builder) -> None:
    """No SoC configured → no SoC gate → can charge."""
    d = decide(
        builder,
        ev_plugged=True,
        ev_strategy="Energy-optimised",
        ev_soc=None,
        grid_import_w=-1500.0,
        pv_w=2000.0,
        house_load_w=500.0,
        ev_power_w=0.0,
        thresholds=_THRESHOLDS,
    )
    assert d.strategy == "ev_charging"
    assert d.charging_state == "charging"


# ── gate priority ─────────────────────────────────────────────────────────────

def test_charging_disabled_takes_priority_over_not_plugged(builder) -> None:
    """Mode=Off is checked before plug status."""
    d = decide(builder, ev_plugged=False, ev_strategy="Off")
    assert d.charging_state == "charging_disabled"


# ── user spec examples ────────────────────────────────────────────────────────

def test_spec_example_1_pv_surplus_positive_active_power(builder) -> None:
    """pv=500, house_non_ev=200 → active_power=+300 (surplus, battery charges into inverter)."""
    thresholds = Thresholds(max_grid_import_w=4500.0, ev_target_soc=80.0, ev_min_charge_w=100.0)
    d = decide(
        builder,
        ev_plugged=True,
        ev_strategy="Energy-optimised",
        ev_soc=50.0,
        grid_import_w=-300.0,
        pv_w=500.0,
        house_load_w=200.0,
        ev_power_w=0.0,
        thresholds=thresholds,
    )
    assert d.strategy == "ev_charging"
    assert d.active_power_w == pytest.approx(300.0)
    # ev_target = max(0, 4500 - (-300) + 0) = 4800
    assert d.ev_charge_power_w == pytest.approx(4800.0)


def test_spec_example_2_no_pv_house_from_battery(builder) -> None:
    """pv=0, house_non_ev=200 → battery discharges 200W for house (drain), EV from grid only."""
    thresholds = Thresholds(max_grid_import_w=4500.0, ev_target_soc=80.0, ev_min_charge_w=100.0)
    d = decide(
        builder,
        ev_plugged=True,
        ev_strategy="Energy-optimised",
        ev_soc=50.0,
        grid_import_w=200.0,
        pv_w=0.0,
        house_load_w=200.0,
        ev_power_w=0.0,
        thresholds=thresholds,
    )
    assert d.strategy == "ev_charging"
    assert d.active_power_w == pytest.approx(-200.0)
    # ev_target = max(0, 4500 - 200 + 0) = 4300
    assert d.ev_charge_power_w == pytest.approx(4300.0)


def test_spec_example_3_partial_pv(builder) -> None:
    """pv=150, house_non_ev=200 → battery discharges 50W for shortfall (drain), EV from grid."""
    thresholds = Thresholds(max_grid_import_w=4500.0, ev_target_soc=80.0, ev_min_charge_w=100.0)
    d = decide(
        builder,
        ev_plugged=True,
        ev_strategy="Energy-optimised",
        ev_soc=50.0,
        grid_import_w=50.0,
        pv_w=150.0,
        house_load_w=200.0,
        ev_power_w=0.0,
        thresholds=thresholds,
    )
    assert d.strategy == "ev_charging"
    assert d.active_power_w == pytest.approx(-50.0)
    # ev_target = max(0, 4500 - 50 + 0) = 4450
    assert d.ev_charge_power_w == pytest.approx(4450.0)


# ── power balance formula ─────────────────────────────────────────────────────

def test_high_grid_import_reduces_ev_target(builder) -> None:
    """High non-EV grid import consumes headroom, leaving less for EV."""
    thresholds = Thresholds(max_grid_import_w=4500.0, ev_target_soc=80.0, ev_min_charge_w=100.0)
    d = decide(
        builder,
        ev_plugged=True,
        ev_strategy="Energy-optimised",
        ev_soc=50.0,
        grid_import_w=3500.0,
        ev_power_w=0.0,
        thresholds=thresholds,
    )
    # ev_target = max(0, 4500 - 3500 + 0) = 1000
    assert d.ev_charge_power_w == pytest.approx(1000.0)


def test_grid_import_exceeds_cap_goes_idle(builder) -> None:
    """When non-EV grid import exceeds the cap → EV target is 0 → idle."""
    thresholds = Thresholds(max_grid_import_w=4500.0, ev_target_soc=80.0, ev_min_charge_w=100.0)
    d = decide(
        builder,
        ev_plugged=True,
        ev_strategy="Energy-optimised",
        ev_soc=50.0,
        grid_import_w=6000.0,
        ev_power_w=0.0,
        thresholds=thresholds,
    )
    assert d.strategy == "ev_idle"
    assert d.charging_state == "not_enough_power"


def test_pv_export_increases_ev_target(builder) -> None:
    """Net grid export (negative grid_import_w) increases headroom above max_grid_import_w."""
    thresholds = Thresholds(max_grid_import_w=4500.0, ev_target_soc=80.0, ev_min_charge_w=100.0)
    d = decide(
        builder,
        ev_plugged=True,
        ev_strategy="Energy-optimised",
        ev_soc=50.0,
        grid_import_w=-2000.0,
        ev_power_w=0.0,
        thresholds=thresholds,
    )
    # ev_target = max(0, 4500 - (-2000) + 0) = 6500
    assert d.ev_charge_power_w == pytest.approx(6500.0)


def test_over_import_reduces_ev_target(builder) -> None:
    """When actual grid import exceeds max, EV target is reduced to pull it back."""
    thresholds = Thresholds(max_grid_import_w=4500.0, ev_target_soc=80.0, ev_min_charge_w=100.0)
    d = decide(
        builder,
        ev_plugged=True,
        ev_strategy="Energy-optimised",
        ev_soc=50.0,
        grid_import_w=5000.0,
        ev_power_w=2000.0,
        thresholds=thresholds,
    )
    # ev_target = max(0, 4500 - 5000 + 2000) = 1500
    assert d.ev_charge_power_w == pytest.approx(1500.0)


# ── minimum charge gate ───────────────────────────────────────────────────────

def test_idle_when_below_minimum(builder) -> None:
    """If ev_target_w < ev_min_charge_w: strategy=ev_idle, charging_state=not_enough_power."""
    d = decide(
        builder,
        ev_plugged=True,
        ev_strategy="Energy-optimised",
        ev_soc=50.0,
        grid_import_w=3200.0,
        ev_power_w=0.0,
        thresholds=Thresholds(max_grid_import_w=4500.0, ev_target_soc=80.0, ev_min_charge_w=1400.0),
    )
    # ev_target = max(0, 4500 - 3200 + 0) = 1300 < 1400 → idle, not enough power
    assert d.strategy == "ev_idle"
    assert d.charging_state == "not_enough_power"


def test_charges_above_minimum(builder) -> None:
    d = decide(
        builder,
        ev_plugged=True,
        ev_strategy="Energy-optimised",
        ev_soc=50.0,
        grid_import_w=500.0,
        ev_power_w=0.0,
        thresholds=Thresholds(max_grid_import_w=4500.0, ev_target_soc=80.0, ev_min_charge_w=1400.0),
    )
    # ev_target = max(0, 4500 - 500 + 0) = 4000 >= 1400
    assert d.strategy == "ev_charging"
    assert d.ev_charge_power_w == pytest.approx(4000.0)


# ── maximum charger cap ───────────────────────────────────────────────────────

def test_ev_target_capped_by_max_charger(builder) -> None:
    """ev_target is capped by max_ev_charge_w when configured."""
    thresholds = Thresholds(
        max_grid_import_w=4500.0,
        ev_target_soc=80.0,
        ev_min_charge_w=100.0,
        max_ev_charge_w=3000.0,
    )
    d = decide(
        builder,
        ev_plugged=True,
        ev_strategy="Energy-optimised",
        ev_soc=50.0,
        grid_import_w=500.0,
        ev_power_w=0.0,
        thresholds=thresholds,
    )
    # Without cap: 4500 - 500 + 0 = 4000W; capped at 3000W
    assert d.strategy == "ev_charging"
    assert d.ev_charge_power_w == pytest.approx(3000.0)


def test_ev_target_no_cap_when_none(builder) -> None:
    """ev_target is uncapped when max_ev_charge_w is None."""
    thresholds = Thresholds(
        max_grid_import_w=4500.0,
        ev_target_soc=80.0,
        ev_min_charge_w=100.0,
        max_ev_charge_w=None,
    )
    d = decide(
        builder,
        ev_plugged=True,
        ev_strategy="Energy-optimised",
        ev_soc=50.0,
        grid_import_w=500.0,
        ev_power_w=0.0,
        thresholds=thresholds,
    )
    assert d.ev_charge_power_w == pytest.approx(4000.0)


def test_ev_target_cap_does_not_trigger_idle(builder) -> None:
    """Max charger cap applied after min-charge gate so a capped value can still charge."""
    thresholds = Thresholds(
        max_grid_import_w=4500.0,
        ev_target_soc=80.0,
        ev_min_charge_w=1400.0,
        max_ev_charge_w=2000.0,
    )
    d = decide(
        builder,
        ev_plugged=True,
        ev_strategy="Energy-optimised",
        ev_soc=50.0,
        grid_import_w=500.0,
        ev_power_w=0.0,
        thresholds=thresholds,
    )
    # Uncapped: 4000W → capped to 2000W; 2000 >= 1400 min → charges
    assert d.strategy == "ev_charging"
    assert d.ev_charge_power_w == pytest.approx(2000.0)


# ── recompute matches decide ──────────────────────────────────────────────────

def test_recompute_matches_decide(builder) -> None:
    inp = builder.build(
        ev_plugged=True,
        ev_strategy="Energy-optimised",
        ev_soc=50.0,
        grid_import_w=1000.0,
        pv_w=500.0,
        house_load_w=300.0,
        ev_power_w=1500.0,
        thresholds=_THRESHOLDS,
    )
    d1 = strategy_module.decide(inp)
    d2 = strategy_module.recompute(
        pv_w=inp.pv_w,
        house_load_w=inp.house_load_w,
        ev_power_w=inp.ev_power_w,
        grid_import_w=inp.grid_import_w,
        ev_plugged=inp.ev_plugged,
        ev_soc=inp.ev_soc,
        ev_strategy=inp.ev_strategy,
        thresholds=inp.thresholds,
    )
    assert d1 == d2
