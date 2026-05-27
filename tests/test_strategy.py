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
        shortfall_kwh=strategy_module.solar_shortfall_kwh(inp),
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

# decide() integration tests for Energy-optimised

def test_energy_optimised_charges_full_surplus_when_battery_will_fill(builder) -> None:
    # scale=1 (solar=10 → predicted>>target); pv=4000, house_non_ev=2000 → surplus=2000
    d = decide(
        builder,
        ev_plugged=True,
        ev_strategy="Energy-optimised",
        pv_w=4000.0,
        house_load_w=2000.0,
        ev_power_w=0.0,
        battery_soc=50.0,
        battery_capacity_kwh=10.0,
        battery_target_soc=80.0,
        solar_forecast_remaining_kwh=10.0,
        thresholds=_THRESHOLDS,
    )
    assert d.strategy == "ev_charging"
    assert d.charging_state == "charging"
    assert d.ev_charge_power_w == pytest.approx(2000.0)


def test_energy_optimised_charges_full_surplus_when_no_forecast_data(builder) -> None:
    # scale=1 (missing data); pv=4000, house=2000 → surplus=2000
    d = decide(
        builder,
        ev_plugged=True,
        ev_strategy="Energy-optimised",
        pv_w=4000.0,
        house_load_w=2000.0,
        ev_power_w=0.0,
        thresholds=_THRESHOLDS,
    )
    assert d.strategy == "ev_charging"
    assert d.charging_state == "charging"
    assert d.ev_charge_power_w == pytest.approx(2000.0)


def test_energy_optimised_blocks_ev_when_battery_wont_advance(builder) -> None:
    # solar shortfall > 0 → battery_short (binary gate, no partial charging)
    d = decide(
        builder,
        ev_plugged=True,
        ev_strategy="Energy-optimised",
        pv_w=5000.0,
        house_load_w=2000.0,
        ev_power_w=0.0,
        battery_soc=50.0,
        battery_capacity_kwh=10.0,
        battery_target_soc=80.0,
        solar_forecast_remaining_kwh=0.5,
        house_load_forecast_remaining_kwh=1.0,
        thresholds=_THRESHOLDS,
    )
    assert d.strategy == "ev_idle"
    assert d.charging_state == "battery_short"


def test_energy_optimised_battery_short_idles_regardless_of_surplus(builder) -> None:
    # Even with large PV surplus, binary gate blocks EV when shortfall > 0
    d = decide(
        builder,
        ev_plugged=True,
        ev_strategy="Energy-optimised",
        pv_w=5000.0,
        house_load_w=1000.0,
        ev_power_w=0.0,
        battery_soc=50.0,
        battery_capacity_kwh=10.0,
        battery_target_soc=80.0,
        solar_forecast_remaining_kwh=1.5,  # gap=3 kWh, effective_solar=1.5 → shortfall=1.5
        thresholds=_THRESHOLDS,
    )
    assert d.strategy == "ev_idle"
    assert d.charging_state == "battery_short"


def test_energy_optimised_ev_draw_excluded_from_surplus(builder) -> None:
    # house_load=5000, ev_power=3000 → house_non_ev=2000; pv=5000 → surplus=3000; scale=1
    d = decide(
        builder,
        ev_plugged=True,
        ev_strategy="Energy-optimised",
        pv_w=5000.0,
        house_load_w=5000.0,
        ev_power_w=3000.0,
        battery_soc=80.0,  # at target → scale=1
        battery_capacity_kwh=10.0,
        battery_target_soc=80.0,
        solar_forecast_remaining_kwh=5.0,
        thresholds=_THRESHOLDS,
    )
    assert d.strategy == "ev_charging"
    assert d.ev_charge_power_w == pytest.approx(3000.0)


def test_energy_optimised_respects_min_charge_floor(builder) -> None:
    # surplus=1000 (below ev_min_charge_w=1400) → not_enough_power
    d = decide(
        builder,
        ev_plugged=True,
        ev_strategy="Energy-optimised",
        pv_w=2500.0,
        house_load_w=1500.0,
        ev_power_w=0.0,
        thresholds=_THRESHOLDS,
    )
    assert d.strategy == "ev_idle"
    assert d.charging_state == "not_enough_power"


def test_energy_optimised_respects_max_charge_cap(builder) -> None:
    # surplus=10000, max_ev_charge_w=7400 → capped at 7400
    capped = Thresholds(max_grid_import_w=4500.0, ev_target_soc=80.0,
                        ev_min_charge_w=1400.0, max_ev_charge_w=7400.0)
    d = decide(
        builder,
        ev_plugged=True,
        ev_strategy="Energy-optimised",
        pv_w=12000.0,
        house_load_w=2000.0,
        ev_power_w=0.0,
        thresholds=capped,
    )
    assert d.strategy == "ev_charging"
    assert d.ev_charge_power_w == pytest.approx(7400.0)


def test_energy_optimised_no_surplus_when_pv_below_house_load(builder) -> None:
    # pv=500 < house_non_ev=1500 → surplus=0 → not_enough_power
    d = decide(
        builder,
        ev_plugged=True,
        ev_strategy="Energy-optimised",
        pv_w=500.0,
        house_load_w=1500.0,
        ev_power_w=0.0,
        thresholds=_THRESHOLDS,
    )
    assert d.strategy == "ev_idle"
    assert d.charging_state == "not_enough_power"


# ── Energy-optimised hysteresis ───────────────────────────────────────────────

def test_energy_optimised_hysteresis_blocks_start_below_start_band(builder) -> None:
    # surplus=1450 is above ev_min(1400) but below start band (1400*1.1=1540); was idle → blocked
    d = decide(
        builder,
        ev_plugged=True,
        ev_strategy="Energy-optimised",
        pv_w=2950.0,
        house_load_w=1500.0,
        ev_power_w=0.0,
        last_ev_target_w=0.0,
        thresholds=_THRESHOLDS,
    )
    assert d.charging_state == "not_enough_power"


def test_energy_optimised_hysteresis_allows_start_above_start_band(builder) -> None:
    # surplus=1600 is above start band (1400*1.1=1540); was idle → starts
    d = decide(
        builder,
        ev_plugged=True,
        ev_strategy="Energy-optimised",
        pv_w=3100.0,
        house_load_w=1500.0,
        ev_power_w=0.0,
        last_ev_target_w=0.0,
        thresholds=_THRESHOLDS,
    )
    assert d.strategy == "ev_charging"
    assert d.ev_charge_power_w == pytest.approx(1600.0)


def test_energy_optimised_hysteresis_continues_when_surplus_in_band(builder) -> None:
    # surplus=1450 is between stop(1400) and start(1540); was charging → keeps going
    d = decide(
        builder,
        ev_plugged=True,
        ev_strategy="Energy-optimised",
        pv_w=2950.0,
        house_load_w=1500.0,
        ev_power_w=0.0,
        last_ev_target_w=1500.0,  # was charging
        thresholds=_THRESHOLDS,
    )
    assert d.strategy == "ev_charging"
    assert d.ev_charge_power_w == pytest.approx(1450.0)


def test_energy_optimised_hysteresis_stops_below_min(builder) -> None:
    # surplus=1300 is below ev_min(1400); was charging → stops regardless of was_charging
    d = decide(
        builder,
        ev_plugged=True,
        ev_strategy="Energy-optimised",
        pv_w=2800.0,
        house_load_w=1500.0,
        ev_power_w=0.0,
        last_ev_target_w=1400.0,  # was charging
        thresholds=_THRESHOLDS,
    )
    assert d.strategy == "ev_idle"
    assert d.charging_state == "not_enough_power"


# ── solar_shortfall_kwh ───────────────────────────────────────────────────────

def test_solar_shortfall_kwh_returns_none_when_battery_soc_missing() -> None:
    inp = _eod_inputs(battery_capacity_kwh=10.0, solar_forecast_remaining_kwh=5.0)
    assert strategy_module.solar_shortfall_kwh(inp) is None


def test_solar_shortfall_kwh_returns_none_when_capacity_missing() -> None:
    inp = _eod_inputs(battery_soc=50.0, solar_forecast_remaining_kwh=5.0)
    assert strategy_module.solar_shortfall_kwh(inp) is None


def test_solar_shortfall_kwh_returns_none_when_solar_missing() -> None:
    inp = _eod_inputs(battery_soc=50.0, battery_capacity_kwh=10.0)
    assert strategy_module.solar_shortfall_kwh(inp) is None


def test_solar_shortfall_kwh_positive_when_solar_short() -> None:
    # gap = 10 * (80-50)/100 = 3 kWh, solar = 2 kWh, house = 0 → shortfall = 3 - 2 = 1.0
    inp = _eod_inputs(
        battery_soc=50.0,
        battery_capacity_kwh=10.0,
        battery_target_soc=80.0,
        solar_forecast_remaining_kwh=2.0,
    )
    assert strategy_module.solar_shortfall_kwh(inp) == pytest.approx(1.0)


def test_solar_shortfall_kwh_negative_when_solar_covers() -> None:
    # gap = 3 kWh, solar = 10 kWh → shortfall = 3 - 10 = -7.0
    inp = _eod_inputs(
        battery_soc=50.0,
        battery_capacity_kwh=10.0,
        battery_target_soc=80.0,
        solar_forecast_remaining_kwh=10.0,
    )
    assert strategy_module.solar_shortfall_kwh(inp) == pytest.approx(-7.0)


def test_solar_shortfall_kwh_subtracts_house_forecast() -> None:
    # gap = 5 kWh (50%→100% on 10 kWh), solar = 10, house = 4 → effective = 6 → shortfall = -1.0
    inp = _eod_inputs(
        battery_soc=50.0,
        battery_capacity_kwh=10.0,
        battery_target_soc=100.0,
        solar_forecast_remaining_kwh=10.0,
        house_load_forecast_remaining_kwh=4.0,
    )
    assert strategy_module.solar_shortfall_kwh(inp) == pytest.approx(-1.0)


def test_solar_shortfall_kwh_negative_when_battery_at_target() -> None:
    # battery_gap = 0 (soc >= target), effective_solar = 5 → shortfall = -5.0
    inp = _eod_inputs(
        battery_soc=80.0,
        battery_capacity_kwh=10.0,
        battery_target_soc=80.0,
        solar_forecast_remaining_kwh=5.0,
    )
    assert strategy_module.solar_shortfall_kwh(inp) == pytest.approx(-5.0)


def test_solar_shortfall_clamps_effective_solar_at_zero() -> None:
    # solar=1 kWh, house_forecast=4 kWh → effective_solar unclamped would be -3.
    # battery_gap=0 (soc=98 >= target=80). Without clamp: shortfall = 0 - (-3) = 3 (WRONG).
    # With clamp: effective_solar=max(0,-3)=0, shortfall = 0 - 0 = 0.
    inp = _eod_inputs(
        battery_soc=98.0,
        battery_capacity_kwh=10.0,
        battery_target_soc=80.0,
        solar_forecast_remaining_kwh=1.0,
        house_load_forecast_remaining_kwh=4.0,
    )
    shortfall = strategy_module.solar_shortfall_kwh(inp)
    assert shortfall is not None
    assert shortfall <= 0.0


def test_eod_suppress_disabled_when_battery_above_target_with_solar_short() -> None:
    # Regression: battery at 98%, target 80%, solar < house forecast.
    # Before the clamp fix, negative effective_solar made shortfall positive → hold fired.
    inp = _eod_inputs(
        now=_BASE_NOW,  # hour=12
        battery_soc=98.0,
        battery_capacity_kwh=10.0,
        battery_target_soc=80.0,
        solar_forecast_remaining_kwh=1.0,
        house_load_forecast_remaining_kwh=4.0,
        prices_today=_PRICES_24,
    )
    assert strategy_module.is_eod_suppress_hour(inp) is False


# ── cheap_hold_rank ───────────────────────────────────────────────────────────

def test_cheap_hold_rank_returns_none_when_no_prices() -> None:
    inp = _eod_inputs(
        battery_soc=50.0, battery_capacity_kwh=10.0, battery_target_soc=80.0,
        solar_forecast_remaining_kwh=0.0,
    )
    assert strategy_module.cheap_hold_rank(inp) is None


def test_cheap_hold_rank_returns_none_when_no_deficit() -> None:
    inp = _eod_inputs(
        battery_soc=50.0, battery_capacity_kwh=10.0, battery_target_soc=80.0,
        solar_forecast_remaining_kwh=10.0,  # solar covers gap → shortfall ≤ 0
        prices_today=_PRICES_24,
    )
    assert strategy_module.cheap_hold_rank(inp) is None


def test_cheap_hold_rank_returns_position_when_in_set() -> None:
    # hour=12, gap=3 kWh, solar=0 → deficit=3 → cheapest 3 hours of [12..23]
    # _PRICES_24 = (0,1,...,23): hours 12,13,14 are cheapest remaining → hour 12 = rank 1 of 3
    inp = _eod_inputs(
        now=_BASE_NOW,  # hour=12
        battery_soc=50.0, battery_capacity_kwh=10.0, battery_target_soc=80.0,
        solar_forecast_remaining_kwh=0.0,
        prices_today=_PRICES_24,
    )
    result = strategy_module.cheap_hold_rank(inp)
    assert result == (1, 3)


def test_cheap_hold_rank_returns_none_when_outside_set() -> None:
    # hour=22, gap=0.1 kWh → ceil(0.1)=1 cheap hour needed
    # prices_cheap_early: hour 0 is most expensive, hour 23 is cheapest
    # cheapest 1 hour of [22,23] is hour 23 → hour 22 is NOT in set
    prices_cheap_early = tuple(23.0 - float(i) for i in range(24))
    inp = _eod_inputs(
        now=datetime.datetime(2026, 4, 27, 22, 0, 0, tzinfo=datetime.UTC),
        battery_soc=79.0, battery_capacity_kwh=10.0, battery_target_soc=80.0,
        solar_forecast_remaining_kwh=0.0,
        prices_today=prices_cheap_early,
    )
    assert strategy_module.cheap_hold_rank(inp) is None


# ── should_plug_in_now ────────────────────────────────────────────────────────

# Prices ascending hour 0..23: hour 0 cheapest, hour 23 most expensive.
_PRICES_ASCENDING = tuple(float(i) for i in range(24))
# Prices descending: hour 0 most expensive, hour 23 cheapest.
_PRICES_DESCENDING = tuple(23.0 - float(i) for i in range(24))


def _hint_inputs(builder, **overrides) -> Inputs:
    return builder.build(
        is_cheap_hour=overrides.pop("is_cheap_hour", False),
        ev_plugged=overrides.pop("ev_plugged", False),
        **overrides,
    )


class TestShouldAdviseEvCharge:
    def test_returns_false_when_ev_plugged(self, builder) -> None:
        inp = _hint_inputs(builder, ev_plugged=True, is_cheap_hour=True)
        assert strategy_module.should_advise_ev_charge(inp) is False

    def test_returns_true_when_is_cheap_hour(self, builder) -> None:
        inp = _hint_inputs(builder, is_cheap_hour=True)
        assert strategy_module.should_advise_ev_charge(inp) is True

    def test_returns_false_when_no_prices_and_not_cheap_hour(self, builder) -> None:
        inp = _hint_inputs(builder, is_cheap_hour=False, prices_today=None)
        assert strategy_module.should_advise_ev_charge(inp) is False

    def test_returns_true_when_current_hour_in_cheapest_n(self, builder) -> None:
        # hour 12, ascending prices → hours 12..23 remain; cheapest 4 are 12,13,14,15
        thresholds = Thresholds(
            max_grid_import_w=4500.0, ev_target_soc=80.0, ev_min_charge_w=1400.0,
            ev_charge_advised_cheap_hours=4,
        )
        inp = _hint_inputs(
            builder,
            now=datetime.datetime(2026, 4, 27, 12, 0, 0, tzinfo=datetime.UTC),
            prices_today=_PRICES_ASCENDING,
            thresholds=thresholds,
        )
        assert strategy_module.should_advise_ev_charge(inp) is True

    def test_returns_false_when_current_hour_outside_cheapest_n(self, builder) -> None:
        # hour 12, descending prices (hour 0=23.0, hour 23=0.0) → hours 12..23 remain
        # cheapest 4 of remaining are hours 20,21,22,23 (prices 3,2,1,0)
        # hour 12 has price 11.0, NOT in the cheapest 4
        thresholds = Thresholds(
            max_grid_import_w=4500.0, ev_target_soc=80.0, ev_min_charge_w=1400.0,
            ev_charge_advised_cheap_hours=4,
        )
        inp = _hint_inputs(
            builder,
            now=datetime.datetime(2026, 4, 27, 12, 0, 0, tzinfo=datetime.UTC),
            prices_today=_PRICES_DESCENDING,
            thresholds=thresholds,
        )
        assert strategy_module.should_advise_ev_charge(inp) is False

    def test_returns_true_with_n1_cheapest_remaining(self, builder) -> None:
        # hour 22, descending prices → hours 22,23 remain; cheapest 1 = hour 23
        # hour 22 is NOT the cheapest 1 when prices descend
        # use ascending: cheapest 1 of [22,23] is hour 22
        thresholds = Thresholds(
            max_grid_import_w=4500.0, ev_target_soc=80.0, ev_min_charge_w=1400.0,
            ev_charge_advised_cheap_hours=1,
        )
        inp = _hint_inputs(
            builder,
            now=datetime.datetime(2026, 4, 27, 22, 0, 0, tzinfo=datetime.UTC),
            prices_today=_PRICES_ASCENDING,
            thresholds=thresholds,
        )
        assert strategy_module.should_advise_ev_charge(inp) is True

    def test_is_cheap_hour_takes_priority_over_rank(self, builder) -> None:
        # Even without prices, is_cheap_hour=True fires the hint
        thresholds = Thresholds(
            max_grid_import_w=4500.0, ev_target_soc=80.0, ev_min_charge_w=1400.0,
            ev_charge_advised_cheap_hours=1,
        )
        inp = _hint_inputs(
            builder,
            is_cheap_hour=True,
            prices_today=None,
            thresholds=thresholds,
        )
        assert strategy_module.should_advise_ev_charge(inp) is True

    def test_ev_plugged_overrides_cheap_hour(self, builder) -> None:
        inp = _hint_inputs(
            builder,
            ev_plugged=True,
            is_cheap_hour=True,
            prices_today=_PRICES_ASCENDING,
        )
        assert strategy_module.should_advise_ev_charge(inp) is False
