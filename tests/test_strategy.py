"""Tests for strategy.decide() and strategy.recompute().

Grid headroom algorithm (integrating closed-loop):
  feedback_w    = last_ev_target_w if not None else ev_power_w  (cold-start fallback)
  ev_target_w   = max(0, max_grid_import_w - grid_import_w + feedback_w)
  ev_target_w   = min(ev_target_w, max_ev_charge_w)  if max_ev_charge_w is set

Using the last commanded target as feedback eliminates the static error that arises
when the wallbox quantises to whole-amp steps and the measured ev_power_w never
reaches the commanded value.
"""
from __future__ import annotations

import datetime

import pytest

from custom_components.ems import strategy as strategy_module
from custom_components.ems.models import EvDecision, Inputs, Thresholds

# ── fixtures / helpers ────────────────────────────────────────────────────────

_THRESHOLDS = Thresholds(
    max_grid_import_w=4500.0,
    ev_target_soc=80.0,
    ev_min_charge_w=1400.0,
)


def decide(builder, **overrides) -> EvDecision:
    return strategy_module.decide(builder.build(**overrides))


def recompute(builder, **overrides) -> EvDecision:
    inp = builder.build(**overrides)
    return strategy_module.recompute(
        pv_w=inp.pv_w,
        house_load_w=inp.house_load_w,
        ev_power_w=inp.ev_power_w,
        grid_import_w=inp.grid_import_w,
        ev_plugged=inp.ev_plugged,
        ev_soc=inp.ev_soc,
        ev_strategy=inp.ev_strategy,
        is_cheap_hour=inp.is_cheap_hour,
        thresholds=inp.thresholds,
        last_ev_target_w=inp.last_ev_target_w,
        energy_optimised_allowed=strategy_module._energy_optimised_allows_ev(inp),
    )


# ── idle gates ────────────────────────────────────────────────────────────────

def test_idle_when_ev_not_plugged(builder) -> None:
    d = decide(builder, ev_plugged=False, ev_strategy="Grid-optimised")
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
        ev_strategy="Grid-optimised",
        ev_soc=80.0,
        thresholds=_THRESHOLDS,
    )
    assert d.strategy == "ev_idle"
    assert d.charging_state == "target_soc_reached"


def test_idle_when_soc_above_target(builder) -> None:
    d = decide(
        builder,
        ev_plugged=True,
        ev_strategy="Grid-optimised",
        ev_soc=95.0,
        thresholds=_THRESHOLDS,
    )
    assert d.strategy == "ev_idle"
    assert d.charging_state == "target_soc_reached"


def test_charges_when_soc_below_target(builder) -> None:
    d = decide(
        builder,
        ev_plugged=True,
        ev_strategy="Grid-optimised",
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
        ev_strategy="Grid-optimised",
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


# ── power balance formula ─────────────────────────────────────────────────────

def test_high_grid_import_reduces_ev_target(builder) -> None:
    """High non-EV grid import consumes headroom, leaving less for EV."""
    thresholds = Thresholds(max_grid_import_w=4500.0, ev_target_soc=80.0, ev_min_charge_w=100.0)
    d = decide(
        builder,
        ev_plugged=True,
        ev_strategy="Grid-optimised",
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
        ev_strategy="Grid-optimised",
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
        ev_strategy="Grid-optimised",
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
        ev_strategy="Grid-optimised",
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
        ev_strategy="Grid-optimised",
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
        ev_strategy="Grid-optimised",
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
        ev_strategy="Grid-optimised",
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
        ev_strategy="Grid-optimised",
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
        ev_strategy="Grid-optimised",
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
        ev_strategy="Grid-optimised",
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
        is_cheap_hour=inp.is_cheap_hour,
        thresholds=inp.thresholds,
        last_ev_target_w=inp.last_ev_target_w,
    )
    assert d1 == d2


# ── price-optimised mode ──────────────────────────────────────────────────────

def test_price_optimised_charges_during_cheap_hour(builder) -> None:
    d = decide(
        builder,
        ev_plugged=True,
        ev_strategy="Price-optimised",
        is_cheap_hour=True,
        ev_soc=50.0,
        grid_import_w=-1500.0,
        pv_w=2000.0,
        house_load_w=500.0,
        ev_power_w=0.0,
        thresholds=_THRESHOLDS,
    )
    assert d.strategy == "ev_charging"
    assert d.charging_state == "charging"


def test_price_optimised_idle_outside_cheap_hour(builder) -> None:
    d = decide(
        builder,
        ev_plugged=True,
        ev_strategy="Price-optimised",
        is_cheap_hour=False,
        ev_soc=50.0,
        grid_import_w=-1500.0,
        pv_w=2000.0,
        house_load_w=500.0,
        ev_power_w=0.0,
        thresholds=_THRESHOLDS,
    )
    assert d.strategy == "ev_idle"
    assert d.charging_state == "outside_cheap_hours"
    assert d.ev_charge_power_w == 0.0


def test_price_optimised_outside_cheap_hour_takes_priority_over_power(builder) -> None:
    """Even with huge PV surplus, outside cheap hours → idle."""
    d = decide(
        builder,
        ev_plugged=True,
        ev_strategy="Price-optimised",
        is_cheap_hour=False,
        ev_soc=50.0,
        grid_import_w=-10000.0,
        pv_w=15000.0,
        house_load_w=500.0,
        ev_power_w=0.0,
        thresholds=_THRESHOLDS,
    )
    assert d.strategy == "ev_idle"
    assert d.charging_state == "outside_cheap_hours"


def test_price_optimised_matches_grid_optimised_when_cheap(builder) -> None:
    """Price-optimised with is_cheap_hour=True produces identical result to Grid-optimised."""
    thresholds = Thresholds(max_grid_import_w=4500.0, ev_target_soc=80.0, ev_min_charge_w=100.0)
    kwargs = dict(
        ev_plugged=True,
        ev_soc=50.0,
        grid_import_w=1000.0,
        pv_w=500.0,
        house_load_w=300.0,
        ev_power_w=0.0,
        thresholds=thresholds,
        is_cheap_hour=True,
    )
    d_grid = decide(builder, ev_strategy="Grid-optimised", **kwargs)
    d_price = decide(builder, ev_strategy="Price-optimised", **kwargs)
    assert d_grid == d_price


# ── integrating feedback (last_ev_target_w) ───────────────────────────────────

def test_last_ev_target_none_falls_back_to_ev_power_w(builder) -> None:
    """Cold-start (last_ev_target_w=None) uses ev_power_w as feedback — same as the old formula."""
    thresholds = Thresholds(max_grid_import_w=4500.0, ev_target_soc=80.0, ev_min_charge_w=100.0)
    d = decide(
        builder,
        ev_plugged=True,
        ev_strategy="Grid-optimised",
        ev_soc=50.0,
        grid_import_w=3500.0,
        ev_power_w=2000.0,
        last_ev_target_w=None,
        thresholds=thresholds,
    )
    # feedback = ev_power_w = 2000; target = max(0, 4500 - 3500 + 2000) = 3000
    assert d.ev_charge_power_w == pytest.approx(3000.0)


def test_last_ev_target_used_when_provided(builder) -> None:
    """last_ev_target_w replaces ev_power_w in the formula, breaking wallbox-lag deadlock."""
    thresholds = Thresholds(max_grid_import_w=4500.0, ev_target_soc=80.0, ev_min_charge_w=100.0)
    d = decide(
        builder,
        ev_plugged=True,
        ev_strategy="Grid-optimised",
        ev_soc=50.0,
        grid_import_w=4244.0,
        ev_power_w=2496.0,        # wallbox quantised to 11 A, lags target
        last_ev_target_w=2729.0,  # last commanded value
        thresholds=thresholds,
    )
    # feedback = last_ev_target_w = 2729; target = max(0, 4500 - 4244 + 2729) = 2985
    # (was 2752 with ev_power_w feedback — old formula stayed stuck at 11 A)
    assert d.ev_charge_power_w == pytest.approx(2985.0)


def test_integrator_drives_grid_to_cap(builder) -> None:
    """Verify integrator converges: when last_target == grid_target, formula stabilises at cap."""
    thresholds = Thresholds(max_grid_import_w=4500.0, ev_target_soc=80.0, ev_min_charge_w=100.0)
    # Simulate steady state: wallbox reached last commanded target exactly.
    # non_ev_grid = 1748 W constant.
    last_target = 2752.0
    ev_power = last_target        # wallbox caught up
    grid_import = ev_power + 1748.0  # = 4500 W
    d = decide(
        builder,
        ev_plugged=True,
        ev_strategy="Grid-optimised",
        ev_soc=50.0,
        grid_import_w=grid_import,
        ev_power_w=ev_power,
        last_ev_target_w=last_target,
        thresholds=thresholds,
    )
    # feedback = last_target = 2752; target = max(0, 4500 - 4500 + 2752) = 2752 — stable
    assert d.ev_charge_power_w == pytest.approx(2752.0)


def test_integrator_keeps_pushing_when_grid_below_cap(builder) -> None:
    """When grid < cap with last_target higher than ev_power, integrator pushes target up."""
    thresholds = Thresholds(max_grid_import_w=4500.0, ev_target_soc=80.0, ev_min_charge_w=100.0)
    # Wallbox stuck at 11 A (2530 W), last_target = 2752 W, non_ev_grid = 1748 W.
    d = decide(
        builder,
        ev_plugged=True,
        ev_strategy="Grid-optimised",
        ev_soc=50.0,
        grid_import_w=2530.0 + 1748.0,  # 4278
        ev_power_w=2530.0,
        last_ev_target_w=2752.0,
        thresholds=thresholds,
    )
    # feedback = 2752; target = max(0, 4500 - 4278 + 2752) = 2974 > 2752 — keeps pushing up
    assert d.ev_charge_power_w == pytest.approx(2974.0)
    assert d.strategy == "ev_charging"


# ── is_eod_suppress_hour truth table ─────────────────────────────────────────

_PRICES_24 = tuple(float(i) for i in range(24))  # hour 0 = cheapest, hour 23 = most expensive
_BASE_NOW = datetime.datetime(2026, 4, 27, 12, 0, 0, tzinfo=datetime.UTC)


def _eod_inputs(**overrides) -> Inputs:
    """Build a minimal Inputs for is_eod_suppress_hour tests."""
    from tests.conftest import InputsBuilder
    b = InputsBuilder()
    return b.build(**overrides)


def test_eod_suppress_disabled_when_battery_soc_missing(builder) -> None:
    inp = _eod_inputs(
        battery_capacity_kwh=10.0,
        solar_forecast_remaining_kwh=5.0,
        prices_today=_PRICES_24,
    )
    assert strategy_module.is_eod_suppress_hour(inp) is False


def test_eod_suppress_disabled_when_solar_covers_gap(builder) -> None:
    inp = _eod_inputs(
        battery_soc=50.0,
        battery_capacity_kwh=10.0,
        battery_target_soc=80.0,
        solar_forecast_remaining_kwh=10.0,  # gap=3 kWh, solar=10 → covers it
        prices_today=_PRICES_24,
    )
    assert strategy_module.is_eod_suppress_hour(inp) is False


def test_eod_suppress_disabled_when_battery_already_at_target(builder) -> None:
    inp = _eod_inputs(
        battery_soc=80.0,
        battery_capacity_kwh=10.0,
        battery_target_soc=80.0,
        solar_forecast_remaining_kwh=0.0,
        prices_today=_PRICES_24,
    )
    assert strategy_module.is_eod_suppress_hour(inp) is False


def test_eod_suppress_true_when_current_hour_is_cheapest(builder) -> None:
    # battery gap = 10 * (80-50)/100 = 3 kWh, solar = 0 → deficit = 3 → need ceil(3)=3 cheap hours
    # prices_today[12] = 12 → hour 12 is in cheapest 3 of hours 12..23 (hours 12,13,14)
    inp = _eod_inputs(
        now=_BASE_NOW,  # hour=12
        battery_soc=50.0,
        battery_capacity_kwh=10.0,
        battery_target_soc=80.0,
        solar_forecast_remaining_kwh=0.0,
        prices_today=_PRICES_24,
    )
    assert strategy_module.is_eod_suppress_hour(inp) is True


def test_eod_suppress_false_when_current_hour_is_expensive(builder) -> None:
    # hour=22, small deficit (0.1 kWh → ceil=1 hour needed).
    # prices_cheap_early: hour 0 = 23 (most expensive), hour 23 = 0 (cheapest).
    # Cheapest 1 hour of [22,23] = hour 23. Hour 22 not in set → False.
    prices_cheap_early = tuple(23.0 - float(i) for i in range(24))
    inp = _eod_inputs(
        now=datetime.datetime(2026, 4, 27, 22, 0, 0, tzinfo=datetime.UTC),  # hour=22
        battery_soc=79.0,
        battery_capacity_kwh=10.0,
        battery_target_soc=80.0,  # gap = 0.1 kWh → deficit=0.1 → ceil(0.1)=1 hour
        solar_forecast_remaining_kwh=0.0,
        prices_today=prices_cheap_early,
    )
    # cheapest 1 hour of [22,23] at prices [1,0] → hour 23 (price=0) is cheapest
    # current hour 22 is NOT in {23} → False
    assert strategy_module.is_eod_suppress_hour(inp) is False


def test_eod_suppress_house_load_forecast_reduces_surplus(builder) -> None:
    # solar=5, house_forecast=3 → effective=2; gap=3 → deficit=1 → need 1 hour
    inp = _eod_inputs(
        now=_BASE_NOW,  # hour=12
        battery_soc=50.0,
        battery_capacity_kwh=10.0,
        battery_target_soc=80.0,
        solar_forecast_remaining_kwh=5.0,
        house_load_forecast_remaining_kwh=3.0,
        prices_today=_PRICES_24,
    )
    # effective_solar=2, gap=3, deficit=1, cheapest 1 hour of [12..23] = hour 12 → True
    assert strategy_module.is_eod_suppress_hour(inp) is True


# ── Energy-optimised EV mode ──────────────────────────────────────────────────

def test_energy_optimised_blocked_when_no_forecast(builder) -> None:
    d = decide(builder, ev_plugged=True, ev_strategy="Energy-optimised", grid_import_w=-2000.0)
    assert d.strategy == "ev_idle"
    assert d.charging_state == "awaiting_solar_surplus"


def test_energy_optimised_blocked_when_solar_insufficient(builder) -> None:
    # gap=3 kWh, solar=2 kWh → not enough
    d = decide(
        builder,
        ev_plugged=True,
        ev_strategy="Energy-optimised",
        grid_import_w=-2000.0,
        battery_soc=50.0,
        battery_capacity_kwh=10.0,
        battery_target_soc=80.0,
        solar_forecast_remaining_kwh=2.0,
    )
    assert d.strategy == "ev_idle"
    assert d.charging_state == "awaiting_solar_surplus"


def test_energy_optimised_charges_when_surplus_exists(builder) -> None:
    # gap=3, solar=10 → surplus=7 > 3 → allowed; grid_import=-2000 → EV target=2000
    d = decide(
        builder,
        ev_plugged=True,
        ev_strategy="Energy-optimised",
        grid_import_w=-2000.0,
        ev_power_w=0.0,
        battery_soc=50.0,
        battery_capacity_kwh=10.0,
        battery_target_soc=80.0,
        solar_forecast_remaining_kwh=10.0,
        thresholds=_THRESHOLDS,
    )
    assert d.strategy == "ev_charging"
    assert d.charging_state == "charging"
    # formula: max(0, 0 - (-2000) + 0) = 2000
    assert d.ev_charge_power_w == pytest.approx(2000.0)


def test_energy_optimised_drives_to_zero_grid_import(builder) -> None:
    # When grid is importing (not exporting), target drops
    # grid_import=500, last_target=0 → ev_target = max(0, 0-500+0) = 0 → not_enough_power
    d = decide(
        builder,
        ev_plugged=True,
        ev_strategy="Energy-optimised",
        grid_import_w=500.0,
        ev_power_w=0.0,
        last_ev_target_w=None,
        battery_soc=50.0,
        battery_capacity_kwh=10.0,
        battery_target_soc=80.0,
        solar_forecast_remaining_kwh=10.0,
        thresholds=_THRESHOLDS,
    )
    assert d.strategy == "ev_idle"
    assert d.charging_state == "not_enough_power"


def test_energy_optimised_respects_min_charge_floor(builder) -> None:
    # grid_import=-1000 → ev_target=1000, below 1400 min → not_enough_power
    d = decide(
        builder,
        ev_plugged=True,
        ev_strategy="Energy-optimised",
        grid_import_w=-1000.0,
        ev_power_w=0.0,
        last_ev_target_w=None,
        battery_soc=50.0,
        battery_capacity_kwh=10.0,
        battery_target_soc=80.0,
        solar_forecast_remaining_kwh=10.0,
        thresholds=_THRESHOLDS,
    )
    assert d.strategy == "ev_idle"
    assert d.charging_state == "not_enough_power"
