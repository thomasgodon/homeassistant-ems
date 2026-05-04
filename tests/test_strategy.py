"""Tests for strategy.decide() and strategy.recompute().

Grid headroom algorithm:
  house_non_ev  = max(0, house_load_w - ev_power_w)
  solar_for_ev  = max(0, pv_w - house_non_ev)
  active_power  = house_non_ev - pv_w   (positive = discharge, negative = surplus PV)
  grid_headroom = max(0, max_grid_import_w - (grid_import_w - ev_power_w))
  ev_target_w   = solar_for_ev + grid_headroom
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
        grid_import_w=inp.grid_import_w,
        pv_w=inp.pv_w,
        house_load_w=inp.house_load_w,
        ev_power_w=inp.ev_power_w,
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
    assert d.ev_charge_intent is False


def test_idle_when_strategy_off(builder) -> None:
    d = decide(builder, ev_plugged=True, ev_strategy="Off")
    assert d.strategy == "ev_idle"
    assert d.ev_charge_power_w == 0.0
    assert d.ev_charge_intent is False


def test_idle_when_soc_at_target(builder) -> None:
    d = decide(
        builder,
        ev_plugged=True,
        ev_strategy="Energy-optimised",
        ev_soc=80.0,
        thresholds=_THRESHOLDS,
    )
    assert d.strategy == "ev_idle"
    assert d.ev_charge_intent is False


def test_idle_when_soc_above_target(builder) -> None:
    d = decide(
        builder,
        ev_plugged=True,
        ev_strategy="Energy-optimised",
        ev_soc=95.0,
        thresholds=_THRESHOLDS,
    )
    assert d.strategy == "ev_idle"
    assert d.ev_charge_intent is False


def test_charges_when_soc_below_target(builder) -> None:
    d = decide(
        builder,
        ev_plugged=True,
        ev_strategy="Energy-optimised",
        ev_soc=50.0,
        grid_import_w=0.0,
        pv_w=2000.0,
        house_load_w=500.0,
        ev_power_w=0.0,
        thresholds=_THRESHOLDS,
    )
    assert d.strategy == "ev_charging"
    assert d.ev_charge_intent is True


def test_charges_when_soc_is_none(builder) -> None:
    """No SoC configured → no SoC gate → can charge."""
    d = decide(
        builder,
        ev_plugged=True,
        ev_strategy="Energy-optimised",
        ev_soc=None,
        grid_import_w=0.0,
        pv_w=2000.0,
        house_load_w=500.0,
        ev_power_w=0.0,
        thresholds=_THRESHOLDS,
    )
    assert d.strategy == "ev_charging"
    assert d.ev_charge_intent is True


# ── user spec examples ────────────────────────────────────────────────────────

def test_spec_example_1_pv_surplus_negative_active_power(builder) -> None:
    """pv=500, house_non_ev=200 → active_power=-300 (surplus, battery absorbs), solar_for_ev=300."""
    thresholds = Thresholds(max_grid_import_w=4500.0, ev_target_soc=80.0, ev_min_charge_w=100.0)
    d = decide(
        builder,
        ev_plugged=True,
        ev_strategy="Energy-optimised",
        ev_soc=50.0,
        pv_w=500.0,
        house_load_w=200.0,
        ev_power_w=0.0,
        grid_import_w=-300.0,  # currently exporting 300W
        thresholds=thresholds,
    )
    assert d.strategy == "ev_charging"
    assert d.active_power_w == pytest.approx(-300.0)
    # solar_for_ev=300, grid_headroom=4500-(-300-0)=4800 → total 5100
    assert d.ev_charge_power_w == pytest.approx(5100.0)


def test_spec_example_2_no_pv_house_from_battery(builder) -> None:
    """pv=0, house_non_ev=200 → battery covers 200W for house, EV from grid only."""
    thresholds = Thresholds(max_grid_import_w=4500.0, ev_target_soc=80.0, ev_min_charge_w=100.0)
    d = decide(
        builder,
        ev_plugged=True,
        ev_strategy="Energy-optimised",
        ev_soc=50.0,
        pv_w=0.0,
        house_load_w=200.0,
        ev_power_w=0.0,
        grid_import_w=200.0,
        thresholds=thresholds,
    )
    assert d.strategy == "ev_charging"
    assert d.active_power_w == pytest.approx(200.0)
    # grid_headroom = 4500 - (200 - 0) = 4300 (grid already importing 200 for house)
    assert d.ev_charge_power_w == pytest.approx(4300.0)


def test_spec_example_3_partial_pv(builder) -> None:
    """pv=150, house_non_ev=200 → battery covers 50W for house, EV from grid."""
    thresholds = Thresholds(max_grid_import_w=4500.0, ev_target_soc=80.0, ev_min_charge_w=100.0)
    d = decide(
        builder,
        ev_plugged=True,
        ev_strategy="Energy-optimised",
        ev_soc=50.0,
        pv_w=150.0,
        house_load_w=200.0,
        ev_power_w=0.0,
        grid_import_w=50.0,  # 50W from grid for house shortfall
        thresholds=thresholds,
    )
    assert d.strategy == "ev_charging"
    assert d.active_power_w == pytest.approx(50.0)
    # grid_headroom = 4500 - (50 - 0) = 4450
    assert d.ev_charge_power_w == pytest.approx(4450.0)


# ── grid headroom formula ─────────────────────────────────────────────────────

def test_grid_headroom_reduces_ev_target(builder) -> None:
    """When grid is already loaded, EV target is reduced accordingly."""
    thresholds = Thresholds(max_grid_import_w=4500.0, ev_target_soc=80.0, ev_min_charge_w=100.0)
    d = decide(
        builder,
        ev_plugged=True,
        ev_strategy="Energy-optimised",
        ev_soc=50.0,
        pv_w=0.0,
        house_load_w=1000.0,
        ev_power_w=2000.0,
        grid_import_w=5500.0,  # 5500 - 2000 = 3500 non-EV grid draw
        thresholds=thresholds,
    )
    # grid_headroom = 4500 - (5500 - 2000) = 1000; solar_for_ev = 0
    assert d.ev_charge_power_w == pytest.approx(1000.0)


def test_grid_headroom_clamps_to_zero_goes_idle(builder) -> None:
    """When non-EV grid draw exceeds limit and no solar surplus → idle but intent is True."""
    thresholds = Thresholds(max_grid_import_w=4500.0, ev_target_soc=80.0, ev_min_charge_w=100.0)
    d = decide(
        builder,
        ev_plugged=True,
        ev_strategy="Energy-optimised",
        ev_soc=50.0,
        pv_w=0.0,
        house_load_w=5000.0,
        ev_power_w=1000.0,
        grid_import_w=5800.0,  # non-EV grid = 4800 > 4500
        thresholds=thresholds,
    )
    assert d.strategy == "ev_idle"
    assert d.ev_charge_intent is True


# ── minimum charge gate ───────────────────────────────────────────────────────

def test_idle_when_below_minimum(builder) -> None:
    """If ev_target_w < ev_min_charge_w: strategy=ev_idle but ev_charge_intent=True."""
    d = decide(
        builder,
        ev_plugged=True,
        ev_strategy="Energy-optimised",
        ev_soc=50.0,
        pv_w=100.0,
        house_load_w=200.0,
        ev_power_w=0.0,
        grid_import_w=4400.0,  # non-EV grid=4400, headroom=100; solar_for_ev=0; total=100
        thresholds=Thresholds(max_grid_import_w=4500.0, ev_target_soc=80.0, ev_min_charge_w=1400.0),
    )
    assert d.strategy == "ev_idle"
    assert d.ev_charge_intent is True


def test_charges_above_minimum(builder) -> None:
    d = decide(
        builder,
        ev_plugged=True,
        ev_strategy="Energy-optimised",
        ev_soc=50.0,
        pv_w=0.0,
        house_load_w=500.0,
        ev_power_w=0.0,
        grid_import_w=500.0,
        thresholds=Thresholds(max_grid_import_w=4500.0, ev_target_soc=80.0, ev_min_charge_w=1400.0),
    )
    # grid_headroom = 4500 - 500 = 4000 >= 1400
    assert d.strategy == "ev_charging"
    assert d.ev_charge_power_w == pytest.approx(4000.0)


# ── recompute matches decide ──────────────────────────────────────────────────

def test_recompute_matches_decide(builder) -> None:
    inp = builder.build(
        ev_plugged=True,
        ev_strategy="Energy-optimised",
        ev_soc=50.0,
        pv_w=500.0,
        house_load_w=300.0,
        ev_power_w=1500.0,
        grid_import_w=1800.0,
        thresholds=_THRESHOLDS,
    )
    d1 = strategy_module.decide(inp)
    d2 = strategy_module.recompute(
        grid_import_w=inp.grid_import_w,
        pv_w=inp.pv_w,
        house_load_w=inp.house_load_w,
        ev_power_w=inp.ev_power_w,
        ev_plugged=inp.ev_plugged,
        ev_soc=inp.ev_soc,
        ev_strategy=inp.ev_strategy,
        thresholds=inp.thresholds,
    )
    assert d1 == d2
