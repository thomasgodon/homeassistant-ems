"""Tests for strategy.decide() and strategy.recompute().

Grid headroom algorithm (integrating closed-loop):
  feedback_w    = min(last_ev_target_w, ev_power_w + 500)  if last_ev_target_w is not None
               else ev_power_w  (cold-start fallback)
  ev_target_w   = max(0, max_grid_import_w - grid_import_w + feedback_w)
  ev_target_w   = min(ev_target_w, max_ev_charge_w)  if max_ev_charge_w is set

Using the last commanded target as feedback eliminates the static error that arises
when the wallbox quantises to whole-amp steps and the measured ev_power_w never
reaches the commanded value.

The 500 W anti-windup headroom prevents the integrator from racing ahead of the
wallbox ramp: during the 5-15 s ramp-up, feedback can only project 500 W above
the actual draw, preventing the target from climbing to the max_ev_charge_w cap
before the wallbox has caught up.
"""
from __future__ import annotations

import datetime

import pytest

from custom_components.ems import strategy as strategy_module
from custom_components.ems.models import (
    EvDecision,
    Inputs,
    InverterDecision,
    PvDestinations,
    Thresholds,
)

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


def test_integrator_uses_instantaneous_grid(builder) -> None:
    """The integrator checks against the raw instantaneous grid reading (no smoothing)."""
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


def test_grid_transient_craters_target_in_loop(builder) -> None:
    """The control loop uses raw grid: a spike drops the target below the minimum → idle.

    Riding through a short transient is the job of apply_stop_debounce (tested below),
    not the control loop — the loop must stay fast so the integrator stays stable.
    """
    thresholds = Thresholds(max_grid_import_w=4500.0, ev_target_soc=80.0, ev_min_charge_w=1400.0)
    # feedback = min(2700, 2500+500) = 2700; target = 4500 - grid + 2700 = 7200 - grid.
    # Raw grid spikes to 6200 → 7200 - 6200 = 1000 < 1400 → idle.
    d = decide(
        builder,
        ev_plugged=True,
        ev_strategy="Grid-optimised",
        ev_soc=50.0,
        ev_power_w=2500.0,
        last_ev_target_w=2700.0,
        grid_import_w=6200.0,
        thresholds=thresholds,
    )
    assert d.strategy == "ev_idle"
    assert d.charging_state == "not_enough_power"


# ── stop-debounce ─────────────────────────────────────────────────────────────

_T0 = datetime.datetime(2026, 4, 27, 12, 0, 0, tzinfo=datetime.UTC)
_NOT_ENOUGH = EvDecision(
    strategy="ev_idle", ev_charge_power_w=0.0, charging_state="not_enough_power"
)
_CHARGING = EvDecision(
    strategy="ev_charging", ev_charge_power_w=3000.0, charging_state="charging"
)


def test_debounce_holds_at_min_when_charging_dips_below_min() -> None:
    d, since = strategy_module.apply_stop_debounce(
        _NOT_ENOUGH, was_charging=True, now=_T0, below_min_since=None, min_charge_w=1400.0,
    )
    assert d.strategy == "ev_charging"
    assert d.charging_state == "holding_through_dip"
    assert d.ev_charge_power_w == 1400.0
    assert since == _T0  # dwell timer starts now


def test_debounce_keeps_holding_within_window() -> None:
    now = _T0 + datetime.timedelta(seconds=59)
    d, since = strategy_module.apply_stop_debounce(
        _NOT_ENOUGH, was_charging=True, now=now, below_min_since=_T0, min_charge_w=1400.0,
    )
    assert d.charging_state == "holding_through_dip"
    assert since == _T0  # carried, not reset


def test_debounce_expires_after_window_and_idles() -> None:
    now = _T0 + datetime.timedelta(seconds=60)
    d, since = strategy_module.apply_stop_debounce(
        _NOT_ENOUGH, was_charging=True, now=now, below_min_since=_T0, min_charge_w=1400.0,
    )
    assert d.strategy == "ev_idle"
    assert d.charging_state == "not_enough_power"
    assert since == _T0


def test_debounce_resets_when_charging_normally() -> None:
    d, since = strategy_module.apply_stop_debounce(
        _CHARGING, was_charging=True, now=_T0, below_min_since=_T0, min_charge_w=1400.0,
    )
    assert d is _CHARGING
    assert since is None


def test_debounce_not_engaged_when_not_previously_charging() -> None:
    d, since = strategy_module.apply_stop_debounce(
        _NOT_ENOUGH, was_charging=False, now=_T0, below_min_since=None, min_charge_w=1400.0,
    )
    assert d is _NOT_ENOUGH
    assert since is None


@pytest.mark.parametrize(
    "state",
    ["ev_not_connected", "charging_disabled", "target_soc_reached",
     "outside_cheap_hours", "battery_short"],
)
def test_debounce_never_holds_hard_gates(state) -> None:
    idle = EvDecision(strategy="ev_idle", ev_charge_power_w=0.0, charging_state=state)
    d, since = strategy_module.apply_stop_debounce(
        idle, was_charging=True, now=_T0, below_min_since=_T0, min_charge_w=1400.0,
    )
    assert d is idle
    assert since is None


def test_debounce_custom_window_holds_longer() -> None:
    """The debounce_s param overrides the default window (Energy-optimised = 120 s)."""
    # At 61 s the default 60 s window would have expired, but 120 s still holds.
    now = _T0 + datetime.timedelta(seconds=61)
    d, since = strategy_module.apply_stop_debounce(
        _NOT_ENOUGH, was_charging=True, now=now, below_min_since=_T0,
        min_charge_w=1400.0, debounce_s=120.0,
    )
    assert d.charging_state == "holding_through_dip"
    assert since == _T0


# ── Energy-optimised stop guards ──────────────────────────────────────────────

def _guards(decision, *, was_charging, now, grid_import_w,
            below_min_since=None, grid_overload_since=None, min_charge_w=1400.0):
    return strategy_module.apply_energy_stop_guards(
        decision,
        was_charging=was_charging,
        now=now,
        grid_import_w=grid_import_w,
        below_min_since=below_min_since,
        grid_overload_since=grid_overload_since,
        min_charge_w=min_charge_w,
    )


def test_energy_guards_battery_drain_holds_at_min() -> None:
    """Sub-minimum dip with low grid → held at min while the 120 s timer runs."""
    d, below, grid_since = _guards(
        _NOT_ENOUGH, was_charging=True, now=_T0, grid_import_w=0.0,
    )
    assert d.strategy == "ev_charging"
    assert d.charging_state == "holding_through_dip"
    assert d.ev_charge_power_w == 1400.0
    assert below == _T0
    assert grid_since is None


def test_energy_guards_battery_drain_holds_until_120s() -> None:
    """Still holding at 119 s (longer than the generic 60 s debounce)."""
    now = _T0 + datetime.timedelta(seconds=119)
    d, below, _ = _guards(
        _NOT_ENOUGH, was_charging=True, now=now, grid_import_w=0.0, below_min_since=_T0,
    )
    assert d.charging_state == "holding_through_dip"
    assert below == _T0


def test_energy_guards_battery_drain_stops_after_120s() -> None:
    """A sustained 120 s shortfall (grid still low) finally stops the session."""
    now = _T0 + datetime.timedelta(seconds=120)
    d, below, grid_since = _guards(
        _NOT_ENOUGH, was_charging=True, now=now, grid_import_w=0.0, below_min_since=_T0,
    )
    assert d.strategy == "ev_idle"
    assert d.charging_state == "not_enough_power"
    assert below == _T0
    assert grid_since is None


def test_energy_guards_grid_overload_starts_timer() -> None:
    """Grid above the overload threshold starts the 30 s cutoff timer but keeps charging."""
    d, _, grid_since = _guards(
        _CHARGING, was_charging=True, now=_T0, grid_import_w=500.0,
    )
    assert d is _CHARGING
    assert grid_since == _T0


def test_energy_guards_grid_overload_within_window_keeps_charging() -> None:
    now = _T0 + datetime.timedelta(seconds=29)
    d, _, grid_since = _guards(
        _CHARGING, was_charging=True, now=now, grid_import_w=500.0, grid_overload_since=_T0,
    )
    assert d is _CHARGING
    assert grid_since == _T0


def test_energy_guards_grid_overload_stops_after_30s() -> None:
    """Sustained grid overload stops a normally-charging session after 30 s."""
    now = _T0 + datetime.timedelta(seconds=30)
    d, _, grid_since = _guards(
        _CHARGING, was_charging=True, now=now, grid_import_w=500.0, grid_overload_since=_T0,
    )
    assert d.strategy == "ev_idle"
    assert d.charging_state == "grid_overloaded"
    assert grid_since == _T0


def test_energy_guards_grid_overload_overrides_battery_drain_hold() -> None:
    """Grid cutoff (30 s) fires even while the 120 s battery-drain hold is still active."""
    now = _T0 + datetime.timedelta(seconds=30)
    d, below, grid_since = _guards(
        _NOT_ENOUGH, was_charging=True, now=now, grid_import_w=500.0,
        below_min_since=_T0, grid_overload_since=_T0,
    )
    assert d.charging_state == "grid_overloaded"
    assert below == _T0          # battery-drain timer still tracked
    assert grid_since == _T0


def test_energy_guards_grid_import_below_threshold_resets_timer() -> None:
    """Grid at or below 100 W clears the overload timer."""
    d, _, grid_since = _guards(
        _CHARGING, was_charging=True, now=_T0, grid_import_w=100.0, grid_overload_since=_T0,
    )
    assert d is _CHARGING
    assert grid_since is None


def test_energy_guards_grid_overload_bypassed_when_not_charging() -> None:
    """Not previously charging → no hold, no grid cutoff, both timers cleared."""
    d, below, grid_since = _guards(
        _NOT_ENOUGH, was_charging=False, now=_T0, grid_import_w=500.0,
        below_min_since=_T0, grid_overload_since=_T0,
    )
    assert d is _NOT_ENOUGH
    assert below is None
    assert grid_since is None


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


# ── anti-windup: integrator feedback cap ─────────────────────────────────────

def test_antiwindup_caps_feedback_when_wallbox_lags(builder) -> None:
    """During wallbox ramp-up, last_ev_target_w >> ev_power_w.

    Without the cap the integrator would keep adding (max_grid - grid_import)
    on top of the already-inflated last_target, driving the target to the wallbox
    maximum and causing a grid overshoot that stops the session.

    With the cap: feedback = min(last_target, ev_power + 500), which limits
    the overshoot and lets the grid settle near max_grid_import_w.
    """
    thresholds = Thresholds(max_grid_import_w=5000.0, ev_target_soc=80.0, ev_min_charge_w=100.0)
    d = decide(
        builder,
        ev_plugged=True,
        ev_strategy="Grid-optimised",
        ev_soc=50.0,
        grid_import_w=4500.0,        # background (2500) + EV (2000) still ramping
        ev_power_w=2000.0,            # wallbox only drew 2000 W so far
        last_ev_target_w=5000.0,      # last commanded was 5000 W (over-shoot)
        thresholds=thresholds,
    )
    # Without anti-windup: feedback=5000, target = max(0, 5000 - 4500 + 5000) = 5500 — GROWS
    # With anti-windup:    feedback=min(5000, 2000+500)=2500, target=max(0, 5000-4500+2500)=3000
    assert d.ev_charge_power_w == pytest.approx(3000.0)
    assert d.strategy == "ev_charging"


def test_antiwindup_cold_start_uses_ev_power_w(builder) -> None:
    """When last_ev_target_w is None (cold start), feedback is ev_power_w — unchanged."""
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


def test_antiwindup_steady_state_cap_not_binding(builder) -> None:
    """At steady state (last_target ≈ ev_power), anti-windup headroom is not consumed."""
    thresholds = Thresholds(max_grid_import_w=4500.0, ev_target_soc=80.0, ev_min_charge_w=100.0)
    # Wallbox reached the commanded value; lag = 0.
    d = decide(
        builder,
        ev_plugged=True,
        ev_strategy="Grid-optimised",
        ev_soc=50.0,
        grid_import_w=4500.0,
        ev_power_w=2752.0,
        last_ev_target_w=2752.0,
        thresholds=thresholds,
    )
    # feedback = min(2752, 2752+500) = 2752 — cap not binding; target = 4500-4500+2752 = 2752
    assert d.ev_charge_power_w == pytest.approx(2752.0)


def test_antiwindup_quantisation_lag_within_headroom(builder) -> None:
    """Wallbox quantises to whole amps; lag within headroom — anti-windup cap not binding."""
    thresholds = Thresholds(max_grid_import_w=4500.0, ev_target_soc=80.0, ev_min_charge_w=100.0)
    d = decide(
        builder,
        ev_plugged=True,
        ev_strategy="Grid-optimised",
        ev_soc=50.0,
        grid_import_w=4244.0,
        ev_power_w=2496.0,        # quantised to 11 A, 233 W below target
        last_ev_target_w=2729.0,  # last commanded
        thresholds=thresholds,
    )
    # feedback = min(2729, 2496+500) = min(2729, 2996) = 2729 — cap not binding
    # target = max(0, 4500 - 4244 + 2729) = 2985 (same as before anti-windup)
    assert d.ev_charge_power_w == pytest.approx(2985.0)


# ── is_eod_suppress_hour truth table ─────────────────────────────────────────

_PRICES_24 = tuple(float(i) for i in range(24))  # hour 0 = cheapest, hour 23 = most expensive
_BASE_NOW = datetime.datetime(2026, 4, 27, 12, 0, 0, tzinfo=datetime.UTC)

# ── solar/house forecast fixtures for EOD battery tests ───────────────────────
# Solar: hours 8-17 productive (1 kWh/h each = 10 kWh total).
# At _BASE_NOW (h=12, min=0) the solar window is (8, 17); active slice is h=12-17 (6 full hours).
_SOLAR_TODAY_10KWH = tuple(1000.0 if 8 <= h <= 17 else 0.0 for h in range(24))
# House-load forecasts that produce exact kWh within the active window at noon:
#   active window = h=12-17 (6 hrs); minute=0 → pro-rate factor=1.0 for h=12
_HOUSE_TODAY_1KWH_NOON = tuple(1000.0 if h == 12 else 0.0 for h in range(24))          # 1 kWh
_HOUSE_TODAY_3KWH_NOON = tuple(1000.0 if 12 <= h <= 14 else 0.0 for h in range(24))    # 3 kWh
_HOUSE_TODAY_4KWH_NOON = tuple(1000.0 if 12 <= h <= 15 else 0.0 for h in range(24))    # 4 kWh
_HOUSE_TODAY_5KWH_NOON = tuple(1000.0 if 12 <= h <= 16 else 0.0 for h in range(24))    # 5 kWh

# Realistic daily profile: cheap midday solar window, expensive evening peak.
# statistics.median == 0.20; only hours 10-15 (0.05) are strictly below median.
# At _BASE_NOW (hour 12): below-median remaining hours are [12, 13, 14, 15].
_PRICES_24_MIDDAY_CHEAP = (
    0.20, 0.20, 0.20, 0.20, 0.20, 0.20, 0.20, 0.20, 0.20, 0.20,  # 0-9
    0.05, 0.05, 0.05, 0.05, 0.05, 0.05,                            # 10-15 (cheap)
    0.20, 0.20,                                                      # 16-17
    0.45, 0.45, 0.45, 0.45, 0.45, 0.45,                            # 18-23 (expensive)
)


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
    # _PRICES_24_MIDDAY_CHEAP: median=0.20; below-median remaining at hour 12 = [12,13,14,15]
    # cheapest 3 of those = [12,13,14] → hour 12 in set → True
    inp = _eod_inputs(
        now=_BASE_NOW,  # hour=12
        battery_soc=50.0,
        battery_capacity_kwh=10.0,
        battery_target_soc=80.0,
        solar_forecast_remaining_kwh=0.0,
        prices_today=_PRICES_24_MIDDAY_CHEAP,
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


def test_eod_suppress_false_when_all_remaining_hours_above_median(builder) -> None:
    # Regression: 2026-05-28 19:00 bug. At 19:00 the system held the battery and drew
    # from the grid at 0.24 €/kWh because the old logic ranked hour 19 as the
    # "cheapest remaining" (vs 0.43 at 20:00). With the median filter, all hours
    # 19–23 are above today's median (≈ 0.155) → no hold → battery discharges.
    prices = (0.16, 0.16, 0.15, 0.15, 0.16, 0.16, 0.18, 0.17, 0.15, 0.11,
              0.04, 0.02, 0.02, 0.02, 0.02, 0.02, 0.06, 0.12, 0.17, 0.24,
              0.43, 0.42, 0.25, 0.19)
    inp = _eod_inputs(
        now=datetime.datetime(2026, 5, 28, 19, 0, 0, tzinfo=datetime.UTC),
        battery_soc=50.0,
        battery_capacity_kwh=10.0,
        battery_target_soc=80.0,  # gap=3 kWh, solar=0 → shortfall=3 kWh
        solar_forecast_remaining_kwh=0.0,
        prices_today=prices,
    )
    assert strategy_module.is_eod_suppress_hour(inp) is False


def test_eod_suppress_house_load_forecast_reduces_surplus(builder) -> None:
    # solar=5, house_forecast=3 → effective=2; gap=3 → deficit=1 → need 1 hour
    # _PRICES_24_MIDDAY_CHEAP: median=0.20; cheapest below-median hour from 12+ = hour 12 (0.05)
    inp = _eod_inputs(
        now=_BASE_NOW,  # hour=12
        battery_soc=50.0,
        battery_capacity_kwh=10.0,
        battery_target_soc=80.0,
        solar_forecast_remaining_kwh=5.0,
        solar_forecast_today=_SOLAR_TODAY_10KWH,
        house_load_forecast_today=_HOUSE_TODAY_3KWH_NOON,
        prices_today=_PRICES_24_MIDDAY_CHEAP,
    )
    # effective_solar=5-3=2, gap=3, deficit=1
    # cheapest 1 below-median hour of [12..23] = hour 12 → True
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
        solar_forecast_today=_SOLAR_TODAY_10KWH,
        house_load_forecast_today=_HOUSE_TODAY_1KWH_NOON,
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


# ── Energy-optimised surplus override (slow-loop-sampled; flap fix) ────────────

_ENERGY_TH = Thresholds(
    max_grid_import_w=4500.0, ev_target_soc=80.0,
    ev_min_charge_w=1400.0, max_ev_charge_w=7400.0,
)


def _energy_recompute(**overrides):
    base = dict(
        pv_w=4000.0, house_load_w=2000.0, ev_power_w=0.0, grid_import_w=0.0,
        ev_plugged=True, ev_soc=50.0, ev_strategy="Energy-optimised",
        is_cheap_hour=True, thresholds=_ENERGY_TH, last_ev_target_w=2000.0,
        shortfall_kwh=-1.0,  # battery will fill → EV allowed
    )
    base.update(overrides)
    return strategy_module.recompute(**base)


def test_energy_surplus_w_helper_computes_and_clamps() -> None:
    assert strategy_module.energy_surplus_w(
        pv_w=5000.0, house_load_w=5000.0, ev_power_w=3000.0
    ) == pytest.approx(3000.0)
    # pv below non-EV house load → clamped to zero, not negative
    assert strategy_module.energy_surplus_w(
        pv_w=500.0, house_load_w=1500.0, ev_power_w=0.0
    ) == 0.0


def test_energy_optimised_recompute_without_override_uses_fresh_pv() -> None:
    # No override → surplus = pv − house_non_ev = 4000 − 2000 = 2000 (fallback path).
    d = _energy_recompute(energy_surplus_override_w=None)
    assert d.strategy == "ev_charging"
    assert d.ev_charge_power_w == pytest.approx(2000.0)


def test_energy_optimised_override_ignores_fresh_pv_and_house() -> None:
    # Fast loop must use the slow-loop surplus, NOT recompute from the (skewed) fast
    # pv/house/ev telegrams — the root of the flap. Fresh values are absurd; override wins.
    d = _energy_recompute(
        energy_surplus_override_w=3200.0,
        pv_w=99999.0, house_load_w=99999.0, ev_power_w=0.0,
    )
    assert d.strategy == "ev_charging"
    assert d.ev_charge_power_w == pytest.approx(3200.0)


def test_energy_optimised_override_respects_max_charge_cap() -> None:
    d = _energy_recompute(energy_surplus_override_w=10000.0)
    assert d.ev_charge_power_w == pytest.approx(7400.0)


def test_energy_optimised_override_below_min_is_not_enough_power() -> None:
    d = _energy_recompute(energy_surplus_override_w=1000.0)
    assert d.strategy == "ev_idle"
    assert d.charging_state == "not_enough_power"


def test_energy_optimised_override_negative_clamped_to_zero() -> None:
    d = _energy_recompute(energy_surplus_override_w=-500.0)
    assert d.strategy == "ev_idle"
    assert d.charging_state == "not_enough_power"


def test_grid_optimised_ignores_energy_surplus_override() -> None:
    # Override is Energy-optimised only; Grid-optimised uses the grid integrator.
    # target = max_grid(4500) − grid(1000) + feedback(min(2000, 0+500)=500) = 4000.
    d = _energy_recompute(
        ev_strategy="Grid-optimised", grid_import_w=1000.0,
        energy_surplus_override_w=99999.0,
    )
    assert d.ev_charge_power_w == pytest.approx(4000.0)


# ── solar_remaining_wh ────────────────────────────────────────────────────────

_FORECAST_24 = tuple(float(h * 100) for h in range(24))  # 0, 100, 200, ..., 2300


_NOON = datetime.datetime(2026, 5, 29, 12, 0, 0)


def test_solar_remaining_wh_none_on_none_forecast() -> None:
    assert strategy_module.solar_remaining_wh(None, _NOON) is None


def test_solar_remaining_wh_none_on_wrong_length() -> None:
    assert strategy_module.solar_remaining_wh(tuple(range(23)), _NOON) is None


def test_solar_remaining_wh_midnight_full_day() -> None:
    # At 00:00:00 all 24 hours remain; hour 0 = 0 Wh so pro-rated contribution is 0
    result = strategy_module.solar_remaining_wh(
        _FORECAST_24, datetime.datetime(2026, 5, 29, 0, 0, 0)
    )
    expected = sum(_FORECAST_24)  # 0 pro-rated + 100+200+...+2300
    assert result == pytest.approx(expected)


def test_solar_remaining_wh_pro_rates_current_hour() -> None:
    # At 13:42 → 18 minutes left → hour 13 contribution = 1300 * (18/60)
    now = datetime.datetime(2026, 5, 29, 13, 42, 0)
    result = strategy_module.solar_remaining_wh(_FORECAST_24, now)
    current = 1300.0 * (18 / 60.0)
    future = sum(_FORECAST_24[14:])
    assert result == pytest.approx(current + future)


def test_solar_remaining_wh_last_minute_of_day() -> None:
    # At 23:59 → 1 minute left → only hour 23 pro-rated, no future hours
    now = datetime.datetime(2026, 5, 29, 23, 59, 0)
    result = strategy_module.solar_remaining_wh(_FORECAST_24, now)
    expected = 2300.0 * (1 / 60.0)
    assert result == pytest.approx(expected)


def test_solar_remaining_wh_all_zero_forecast() -> None:
    zeros: tuple[float, ...] = tuple(0.0 for _ in range(24))
    result = strategy_module.solar_remaining_wh(zeros, datetime.datetime(2026, 5, 29, 12, 0, 0))
    assert result == pytest.approx(0.0)


def test_solar_remaining_wh_partial_zero_tail() -> None:
    # forecast has non-zero only in first half; by hour 14 everything is 0
    partial = tuple(float(h * 100) if h < 14 else 0.0 for h in range(24))
    now = datetime.datetime(2026, 5, 29, 13, 0, 0)
    result = strategy_module.solar_remaining_wh(partial, now)
    # hour 13: 1300 * (60/60) = 1300; hour 14..23: all 0
    assert result == pytest.approx(1300.0)


# ── solar_seconds_until_zero ──────────────────────────────────────────────────

def test_solar_seconds_until_zero_none_on_none_forecast() -> None:
    assert strategy_module.solar_seconds_until_zero(None, _NOON) is None


def test_solar_seconds_until_zero_none_on_wrong_length() -> None:
    assert strategy_module.solar_seconds_until_zero(tuple(range(5)), _NOON) is None


def test_solar_seconds_until_zero_all_zero_returns_zero() -> None:
    zeros: tuple[float, ...] = tuple(0.0 for _ in range(24))
    assert strategy_module.solar_seconds_until_zero(zeros, _NOON) == 0


def test_solar_seconds_until_zero_typical_midday() -> None:
    # last non-zero hour = 17 → end = 18:00:00 = 64800 s; now = 13:00:00 = 46800 s → diff = 18000 s
    forecast = tuple(500.0 if 6 <= h <= 17 else 0.0 for h in range(24))
    now = datetime.datetime(2026, 5, 29, 13, 0, 0)
    result = strategy_module.solar_seconds_until_zero(forecast, now)
    assert result == 5 * 3600  # 18:00 - 13:00 = 5 hours


def test_solar_seconds_until_zero_past_sunset_returns_zero() -> None:
    # last non-zero hour = 17; now = 20:00:00 (well past sunset)
    forecast = tuple(500.0 if 6 <= h <= 17 else 0.0 for h in range(24))
    now = datetime.datetime(2026, 5, 29, 20, 0, 0)
    result = strategy_module.solar_seconds_until_zero(forecast, now)
    assert result == 0


def test_solar_seconds_until_zero_accounts_for_seconds() -> None:
    # last non-zero = 17 → end at 64800 s; now = 17:45:30 = 63930 s → 870 s left
    forecast = tuple(500.0 if 6 <= h <= 17 else 0.0 for h in range(24))
    now = datetime.datetime(2026, 5, 29, 17, 45, 30)
    result = strategy_module.solar_seconds_until_zero(forecast, now)
    assert result == 18 * 60 - 30  # 17 min 30 s


# ── solar_production_window ────────────────────────────────────────────────────

def test_solar_production_window_none_on_none_forecast() -> None:
    assert strategy_module.solar_production_window(None) is None


def test_solar_production_window_none_on_wrong_length() -> None:
    assert strategy_module.solar_production_window(tuple(range(23))) is None


def test_solar_production_window_none_on_all_zero() -> None:
    assert strategy_module.solar_production_window(tuple(0.0 for _ in range(24))) is None


def test_solar_production_window_typical() -> None:
    # hours 8-17 productive
    forecast = tuple(1000.0 if 8 <= h <= 17 else 0.0 for h in range(24))
    assert strategy_module.solar_production_window(forecast) == (8, 17)


def test_solar_production_window_single_hour() -> None:
    # only hour 12 has production
    forecast = tuple(500.0 if h == 12 else 0.0 for h in range(24))
    assert strategy_module.solar_production_window(forecast) == (12, 12)


# ── solar_production_start_time / solar_production_end_time ───────────────────

def test_solar_production_start_time_none_on_all_zero_forecast() -> None:
    zero = tuple(0.0 for _ in range(24))
    now = datetime.datetime(2026, 4, 27, 12, 0, 0, tzinfo=datetime.UTC)
    assert strategy_module.solar_production_start_time(zero, now) is None


def test_solar_production_start_time_typical() -> None:
    forecast = tuple(1000.0 if 8 <= h <= 17 else 0.0 for h in range(24))
    now = datetime.datetime(2026, 4, 27, 14, 0, 0, tzinfo=datetime.UTC)
    result = strategy_module.solar_production_start_time(forecast, now)
    assert result == datetime.datetime(2026, 4, 27, 8, 0, 0, tzinfo=datetime.UTC)


def test_solar_production_start_time_keeps_today_after_window_passes() -> None:
    # Even when called after sunrise (now=20:00), still shows today's start
    forecast = tuple(1000.0 if 8 <= h <= 17 else 0.0 for h in range(24))
    now = datetime.datetime(2026, 4, 27, 20, 0, 0, tzinfo=datetime.UTC)
    result = strategy_module.solar_production_start_time(forecast, now)
    assert result == datetime.datetime(2026, 4, 27, 8, 0, 0, tzinfo=datetime.UTC)


def test_solar_production_end_time_none_on_all_zero_forecast() -> None:
    zero = tuple(0.0 for _ in range(24))
    now = datetime.datetime(2026, 4, 27, 12, 0, 0, tzinfo=datetime.UTC)
    assert strategy_module.solar_production_end_time(zero, now) is None


def test_solar_production_end_time_typical() -> None:
    # last hour = 17 → end = hour 18 = 18:00
    forecast = tuple(1000.0 if 8 <= h <= 17 else 0.0 for h in range(24))
    now = datetime.datetime(2026, 4, 27, 12, 0, 0, tzinfo=datetime.UTC)
    result = strategy_module.solar_production_end_time(forecast, now)
    assert result == datetime.datetime(2026, 4, 27, 18, 0, 0, tzinfo=datetime.UTC)


def test_solar_production_end_time_last_hour_23() -> None:
    # last non-zero hour = 23 → end = next day 00:00
    forecast = tuple(500.0 if h == 23 else 0.0 for h in range(24))
    now = datetime.datetime(2026, 4, 27, 12, 0, 0, tzinfo=datetime.UTC)
    result = strategy_module.solar_production_end_time(forecast, now)
    assert result == datetime.datetime(2026, 4, 28, 0, 0, 0, tzinfo=datetime.UTC)


def test_solar_production_end_time_keeps_today_after_window_passes() -> None:
    # Called at 20:00, still shows today's end
    forecast = tuple(1000.0 if 8 <= h <= 17 else 0.0 for h in range(24))
    now = datetime.datetime(2026, 4, 27, 20, 0, 0, tzinfo=datetime.UTC)
    result = strategy_module.solar_production_end_time(forecast, now)
    assert result == datetime.datetime(2026, 4, 27, 18, 0, 0, tzinfo=datetime.UTC)


# ── house_load_in_solar_window_kwh ─────────────────────────────────────────────

def test_house_load_in_solar_window_none_on_none_solar_forecast() -> None:
    house = tuple(500.0 for _ in range(24))
    now = datetime.datetime(2026, 4, 27, 12, 0, 0, tzinfo=datetime.UTC)
    assert strategy_module.house_load_in_solar_window_kwh(None, house, now) is None


def test_house_load_in_solar_window_none_on_wrong_length_solar() -> None:
    house = tuple(500.0 for _ in range(24))
    now = datetime.datetime(2026, 4, 27, 12, 0, 0, tzinfo=datetime.UTC)
    assert strategy_module.house_load_in_solar_window_kwh(tuple(range(23)), house, now) is None


def test_house_load_in_solar_window_none_on_none_house_forecast() -> None:
    solar = tuple(1000.0 if 8 <= h <= 17 else 0.0 for h in range(24))
    now = datetime.datetime(2026, 4, 27, 12, 0, 0, tzinfo=datetime.UTC)
    assert strategy_module.house_load_in_solar_window_kwh(solar, None, now) is None


def test_house_load_in_solar_window_none_on_all_zero_solar() -> None:
    # All-zero solar → no window → None
    solar = tuple(0.0 for _ in range(24))
    house = tuple(500.0 for _ in range(24))
    now = datetime.datetime(2026, 4, 27, 12, 0, 0, tzinfo=datetime.UTC)
    assert strategy_module.house_load_in_solar_window_kwh(solar, house, now) is None


def test_house_load_in_solar_window_zero_when_past_solar_end() -> None:
    # now=20:00 is past last solar hour (17) → 0.0
    solar = tuple(1000.0 if 8 <= h <= 17 else 0.0 for h in range(24))
    house = tuple(1000.0 for _ in range(24))
    now = datetime.datetime(2026, 4, 27, 20, 0, 0, tzinfo=datetime.UTC)
    assert strategy_module.house_load_in_solar_window_kwh(solar, house, now) == 0.0


def test_house_load_in_solar_window_full_window_before_solar_start() -> None:
    # now=06:00, window starts at 8 → sums full window (8-17)
    # house: 1000 Wh each at hours 12-14 → 3 hours in window → 3.0 kWh
    solar = tuple(1000.0 if 8 <= h <= 17 else 0.0 for h in range(24))
    house = tuple(1000.0 if 12 <= h <= 14 else 0.0 for h in range(24))
    now = datetime.datetime(2026, 4, 27, 6, 0, 0, tzinfo=datetime.UTC)
    result = strategy_module.house_load_in_solar_window_kwh(solar, house, now)
    assert result == pytest.approx(3.0)


def test_house_load_in_solar_window_mid_window_at_noon() -> None:
    # now=12:00, minute=0 → active window 12-17 (6 full hours), house=1000 Wh at h12-14 → 3.0 kWh
    solar = tuple(1000.0 if 8 <= h <= 17 else 0.0 for h in range(24))
    house = tuple(1000.0 if 12 <= h <= 14 else 0.0 for h in range(24))
    now = datetime.datetime(2026, 4, 27, 12, 0, 0, tzinfo=datetime.UTC)
    result = strategy_module.house_load_in_solar_window_kwh(solar, house, now)
    assert result == pytest.approx(3.0)


def test_house_load_in_solar_window_current_hour_pro_rated() -> None:
    # now=14:30 → h14 pro-rated 30/60=0.5; house 1200 Wh/h
    # h14=600, h15=1200, h16=1200, h17=1200 → 4.2 kWh
    solar = tuple(1000.0 if 8 <= h <= 17 else 0.0 for h in range(24))
    house = tuple(1200.0 for _ in range(24))
    now = datetime.datetime(2026, 4, 27, 14, 30, 0, tzinfo=datetime.UTC)
    result = strategy_module.house_load_in_solar_window_kwh(solar, house, now)
    assert result == pytest.approx(4.2)


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


# ── Solar-only ────────────────────────────────────────────────────────────────

def test_solar_only_target_is_whole_pv_ignoring_house_load(builder) -> None:
    # pv=3000, house_non_ev=2500 → Energy-optimised would give 500; Solar-only gives 3000
    d = decide(
        builder,
        ev_plugged=True,
        ev_strategy="Solar-only",
        pv_w=3000.0,
        house_load_w=2500.0,
        ev_power_w=0.0,
        grid_import_w=2500.0,
        thresholds=_THRESHOLDS,
    )
    assert d.strategy == "ev_charging"
    assert d.charging_state == "charging"
    assert d.ev_charge_power_w == pytest.approx(3000.0)


def test_solar_only_recompute_matches_decide(builder) -> None:
    d = recompute(
        builder,
        ev_plugged=True,
        ev_strategy="Solar-only",
        pv_w=3000.0,
        house_load_w=2500.0,
        thresholds=_THRESHOLDS,
    )
    assert d.ev_charge_power_w == pytest.approx(3000.0)


def test_solar_only_capped_by_max_ev_charge(builder) -> None:
    thresholds = Thresholds(
        max_grid_import_w=4500.0, ev_target_soc=80.0, ev_min_charge_w=1400.0,
        max_ev_charge_w=2500.0,
    )
    d = decide(
        builder, ev_plugged=True, ev_strategy="Solar-only", pv_w=6000.0,
        thresholds=thresholds,
    )
    assert d.ev_charge_power_w == pytest.approx(2500.0)


def test_solar_only_hysteresis_blocks_start_below_start_band(builder) -> None:
    # pv=1450 ≥ min(1400) but < start band (1540); was idle → blocked
    d = decide(
        builder, ev_plugged=True, ev_strategy="Solar-only", pv_w=1450.0,
        last_ev_target_w=0.0, thresholds=_THRESHOLDS,
    )
    assert d.strategy == "ev_idle"
    assert d.charging_state == "not_enough_power"


def test_solar_only_hysteresis_continues_when_pv_in_band(builder) -> None:
    d = decide(
        builder, ev_plugged=True, ev_strategy="Solar-only", pv_w=1450.0,
        last_ev_target_w=1500.0, thresholds=_THRESHOLDS,
    )
    assert d.strategy == "ev_charging"
    assert d.ev_charge_power_w == pytest.approx(1450.0)


def test_solar_only_stops_below_min_when_charging(builder) -> None:
    d = decide(
        builder, ev_plugged=True, ev_strategy="Solar-only", pv_w=1300.0,
        last_ev_target_w=1500.0, thresholds=_THRESHOLDS,
    )
    assert d.strategy == "ev_idle"
    assert d.charging_state == "not_enough_power"


def test_solar_only_negative_pv_clamped(builder) -> None:
    d = decide(
        builder, ev_plugged=True, ev_strategy="Solar-only", pv_w=-20.0,
        last_ev_target_w=1500.0, thresholds=_THRESHOLDS,
    )
    assert d.charging_state == "not_enough_power"


def test_solar_only_ignores_battery_short(builder) -> None:
    # Same shortfall setup that makes Energy-optimised idle with battery_short
    d = decide(
        builder,
        ev_plugged=True,
        ev_strategy="Solar-only",
        pv_w=5000.0,
        house_load_w=1000.0,
        ev_power_w=0.0,
        battery_soc=50.0,
        battery_capacity_kwh=10.0,
        battery_target_soc=80.0,
        solar_forecast_remaining_kwh=1.5,
        thresholds=_THRESHOLDS,
    )
    assert d.strategy == "ev_charging"
    assert d.ev_charge_power_w == pytest.approx(5000.0)


def test_solar_only_ignores_cheap_hours(builder) -> None:
    d = decide(
        builder, ev_plugged=True, ev_strategy="Solar-only", pv_w=3000.0,
        is_cheap_hour=False, thresholds=_THRESHOLDS,
    )
    assert d.strategy == "ev_charging"


@pytest.mark.parametrize(
    ("overrides", "state"),
    [
        ({"ev_plugged": False}, "ev_not_connected"),
        ({"ev_soc": 85.0}, "target_soc_reached"),
    ],
)
def test_solar_only_hard_gates_still_win(builder, overrides, state) -> None:
    kwargs = {"ev_plugged": True, **overrides}
    d = decide(
        builder, ev_strategy="Solar-only", pv_w=5000.0, thresholds=_THRESHOLDS, **kwargs,
    )
    assert d.charging_state == state


def test_solar_stop_guards_hold_past_60s_then_stop() -> None:
    held, since = strategy_module.apply_solar_stop_guards(
        _NOT_ENOUGH, was_charging=True, now=_T0 + datetime.timedelta(seconds=61),
        below_min_since=_T0, min_charge_w=1400.0,
    )
    assert held.charging_state == "holding_through_dip"
    assert held.ev_charge_power_w == pytest.approx(1400.0)
    assert since == _T0

    stopped, since = strategy_module.apply_solar_stop_guards(
        _NOT_ENOUGH, was_charging=True, now=_T0 + datetime.timedelta(seconds=120),
        below_min_since=_T0, min_charge_w=1400.0,
    )
    assert stopped is _NOT_ENOUGH
    assert since == _T0


def test_solar_stop_guards_never_stop_charging_session() -> None:
    """No grid-overload cutoff: a charging decision passes through untouched."""
    charging = EvDecision(
        strategy="ev_charging", ev_charge_power_w=3000.0, charging_state="charging"
    )
    d, since = strategy_module.apply_solar_stop_guards(
        charging, was_charging=True, now=_T0, below_min_since=_T0, min_charge_w=1400.0,
    )
    assert d is charging
    assert since is None


# ── battery_gap_kwh ───────────────────────────────────────────────────────────

def test_battery_gap_kwh_none_when_soc_missing() -> None:
    inp = _eod_inputs(battery_capacity_kwh=10.0)
    assert strategy_module.battery_gap_kwh(inp) is None


def test_battery_gap_kwh_none_when_capacity_missing() -> None:
    inp = _eod_inputs(battery_soc=50.0)
    assert strategy_module.battery_gap_kwh(inp) is None


def test_battery_gap_kwh_positive_when_below_target() -> None:
    # 10 kWh * (80 - 50) / 100 = 3 kWh
    inp = _eod_inputs(battery_soc=50.0, battery_capacity_kwh=10.0, battery_target_soc=80.0)
    assert strategy_module.battery_gap_kwh(inp) == pytest.approx(3.0)


def test_battery_gap_kwh_zero_when_at_target() -> None:
    inp = _eod_inputs(battery_soc=80.0, battery_capacity_kwh=10.0, battery_target_soc=80.0)
    assert strategy_module.battery_gap_kwh(inp) == pytest.approx(0.0)


def test_battery_gap_kwh_zero_when_above_target() -> None:
    inp = _eod_inputs(battery_soc=95.0, battery_capacity_kwh=10.0, battery_target_soc=80.0)
    assert strategy_module.battery_gap_kwh(inp) == pytest.approx(0.0)


# ── effective_solar_remaining_kwh ─────────────────────────────────────────────

def test_effective_solar_remaining_none_when_solar_missing() -> None:
    inp = _eod_inputs(
        now=_BASE_NOW,
        solar_forecast_today=_SOLAR_TODAY_10KWH,
        house_load_forecast_today=_HOUSE_TODAY_3KWH_NOON,
    )
    assert strategy_module.effective_solar_remaining_kwh(inp) is None


def test_effective_solar_remaining_positive_when_solar_exceeds_house() -> None:
    # remaining=8 kWh, house in window=3 kWh → effective=5 kWh
    inp = _eod_inputs(
        now=_BASE_NOW,
        solar_forecast_remaining_kwh=8.0,
        solar_forecast_today=_SOLAR_TODAY_10KWH,
        house_load_forecast_today=_HOUSE_TODAY_3KWH_NOON,
    )
    assert strategy_module.effective_solar_remaining_kwh(inp) == pytest.approx(5.0)


def test_effective_solar_remaining_zero_when_house_exceeds_solar() -> None:
    # remaining=2 kWh, house in window=5 kWh → clamped to 0
    inp = _eod_inputs(
        now=_BASE_NOW,
        solar_forecast_remaining_kwh=2.0,
        solar_forecast_today=_SOLAR_TODAY_10KWH,
        house_load_forecast_today=_HOUSE_TODAY_5KWH_NOON,
    )
    assert strategy_module.effective_solar_remaining_kwh(inp) == pytest.approx(0.0)


def test_effective_solar_remaining_treats_missing_house_as_zero() -> None:
    # No house forecast → windowing returns None → house treated as 0
    inp = _eod_inputs(solar_forecast_remaining_kwh=4.0)
    assert strategy_module.effective_solar_remaining_kwh(inp) == pytest.approx(4.0)


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
    # gap=5 kWh (50→100% on 10 kWh), solar=10, house in window=4 → effective=6 → shortfall=-1.0
    inp = _eod_inputs(
        now=_BASE_NOW,
        battery_soc=50.0,
        battery_capacity_kwh=10.0,
        battery_target_soc=100.0,
        solar_forecast_remaining_kwh=10.0,
        solar_forecast_today=_SOLAR_TODAY_10KWH,
        house_load_forecast_today=_HOUSE_TODAY_4KWH_NOON,
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
    # solar=1 kWh, house in window=4 kWh → effective_solar unclamped would be -3.
    # battery_gap=0 (soc=98 >= target=80). Without clamp: shortfall = 0 - (-3) = 3 (WRONG).
    # With clamp: effective_solar=max(0,-3)=0, shortfall = 0 - 0 = 0.
    inp = _eod_inputs(
        now=_BASE_NOW,
        battery_soc=98.0,
        battery_capacity_kwh=10.0,
        battery_target_soc=80.0,
        solar_forecast_remaining_kwh=1.0,
        solar_forecast_today=_SOLAR_TODAY_10KWH,
        house_load_forecast_today=_HOUSE_TODAY_4KWH_NOON,
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
        solar_forecast_today=_SOLAR_TODAY_10KWH,
        house_load_forecast_today=_HOUSE_TODAY_4KWH_NOON,
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
    # hour=12, gap=3 kWh, solar=0 → deficit=3 → cheapest 3 below-median hours of [12..23]
    # _PRICES_24_MIDDAY_CHEAP: median=0.20; below-median remaining = [12,13,14,15] (all 0.05)
    # cheapest 3 = [12,13,14] (stable sort on equal prices) → hour 12 = rank 1 of 3
    inp = _eod_inputs(
        now=_BASE_NOW,  # hour=12
        battery_soc=50.0, battery_capacity_kwh=10.0, battery_target_soc=80.0,
        solar_forecast_remaining_kwh=0.0,
        prices_today=_PRICES_24_MIDDAY_CHEAP,
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


# ── expected_eod_soc ──────────────────────────────────────────────────────────

def test_expected_eod_soc_covers_gap(builder) -> None:
    # gap=3 kWh (50→80% on 10 kWh), effective_solar=5 kWh → delta=50% → EOD=100% (capped)
    inp = builder.build(
        battery_soc=50.0, battery_capacity_kwh=10.0, battery_target_soc=80.0,
        solar_forecast_remaining_kwh=5.0,
    )
    result = strategy_module.expected_eod_soc(inp)
    assert result == pytest.approx(100.0)


def test_expected_eod_soc_short(builder) -> None:
    # gap=3 kWh, effective_solar=1 kWh → delta=10% → EOD=60%
    inp = builder.build(
        battery_soc=50.0, battery_capacity_kwh=10.0, battery_target_soc=80.0,
        solar_forecast_remaining_kwh=1.0,
    )
    result = strategy_module.expected_eod_soc(inp)
    assert result == pytest.approx(60.0)


def test_expected_eod_soc_uses_house_forecast(builder) -> None:
    # solar=6 kWh, house in window=4 kWh → effective=2 kWh → delta=20% → EOD=70%
    inp = builder.build(
        now=_BASE_NOW,
        battery_soc=50.0, battery_capacity_kwh=10.0, battery_target_soc=80.0,
        solar_forecast_remaining_kwh=6.0,
        solar_forecast_today=_SOLAR_TODAY_10KWH,
        house_load_forecast_today=_HOUSE_TODAY_4KWH_NOON,
    )
    result = strategy_module.expected_eod_soc(inp)
    assert result == pytest.approx(70.0)


def test_expected_eod_soc_none_when_house_forecast_missing_defaults_to_zero(builder) -> None:
    # house_load_forecast_today=None treated as 0 → effective_solar=5 kWh
    inp = builder.build(
        battery_soc=50.0, battery_capacity_kwh=10.0, battery_target_soc=80.0,
        solar_forecast_remaining_kwh=5.0,
    )
    result = strategy_module.expected_eod_soc(inp)
    assert result is not None and result > 50.0


@pytest.mark.parametrize("missing_field,overrides", [
    ("battery_soc",          {"battery_capacity_kwh": 10.0, "solar_forecast_remaining_kwh": 5.0}),
    ("battery_capacity_kwh", {"battery_soc": 50.0,          "solar_forecast_remaining_kwh": 5.0}),
    ("solar_forecast",       {"battery_soc": 50.0,          "battery_capacity_kwh": 10.0}),
])
def test_expected_eod_soc_none_when_input_missing(builder, missing_field, overrides) -> None:
    inp = builder.build(**overrides)
    assert strategy_module.expected_eod_soc(inp) is None


# ── battery_focus ──────────────────────────────────────────────────────────────

def _inv(status: str) -> InverterDecision:
    return InverterDecision(inverter_verb=None, active_power_w=None, status=status)  # type: ignore[arg-type]


def test_battery_focus_starting_up(builder) -> None:
    inp = builder.build(battery_soc=60.0, pv_w=2000.0, house_load_w=1000.0, grid_import_w=0.0)
    assert strategy_module.battery_focus(inp, _inv("starting_up")) == "starting_up"


def test_battery_focus_starting_up_overrides_unavailable(builder) -> None:
    # starting_up takes priority over battery_soc=None (unavailable)
    inp = builder.build(battery_soc=None, pv_w=2000.0, house_load_w=1000.0, grid_import_w=0.0)
    assert strategy_module.battery_focus(inp, _inv("starting_up")) == "starting_up"


def test_battery_focus_unavailable_when_no_soc(builder) -> None:
    inp = builder.build(battery_soc=None, pv_w=1000.0, house_load_w=1000.0, grid_import_w=0.0)
    assert strategy_module.battery_focus(inp, _inv("idle")) == "unavailable"


def test_battery_focus_peak_shaving(builder) -> None:
    inp = builder.build(battery_soc=60.0, pv_w=1000.0, house_load_w=1000.0, grid_import_w=0.0)
    assert strategy_module.battery_focus(inp, _inv("peak_shaving")) == "peak_shaving"


def test_battery_focus_holding_for_evening(builder) -> None:
    inp = builder.build(battery_soc=60.0, pv_w=1000.0, house_load_w=1000.0, grid_import_w=0.0)
    assert strategy_module.battery_focus(inp, _inv("holding_battery")) == "holding_for_evening"


def test_battery_focus_prioritising_battery(builder) -> None:
    inp = builder.build(battery_soc=60.0, pv_w=1000.0, house_load_w=1000.0, grid_import_w=0.0)
    assert strategy_module.battery_focus(inp, _inv("battery_priority")) == "prioritising_battery"


def test_battery_focus_full(builder) -> None:
    # soc=80 >= target=80, battery_w = 1000 + 0 - 1000 = 0 (within 200 W deadband)
    inp = builder.build(
        battery_soc=80.0, battery_target_soc=80.0,
        pv_w=1000.0, house_load_w=1000.0, grid_import_w=0.0,
    )
    assert strategy_module.battery_focus(inp, _inv("idle")) == "full"


def test_battery_focus_charging_from_solar(builder) -> None:
    # battery_w = 3000 + 0 - 1000 = +2000 > 200
    inp = builder.build(
        battery_soc=60.0, battery_target_soc=80.0,
        pv_w=3000.0, house_load_w=1000.0, grid_import_w=0.0,
    )
    assert strategy_module.battery_focus(inp, _inv("idle")) == "charging_from_solar"


def test_battery_focus_discharging_to_house(builder) -> None:
    # battery_w = 500 + 0 - 2000 = -1500 < -200
    inp = builder.build(
        battery_soc=60.0, battery_target_soc=80.0,
        pv_w=500.0, house_load_w=2000.0, grid_import_w=0.0,
    )
    assert strategy_module.battery_focus(inp, _inv("idle")) == "discharging_to_house"


def test_battery_focus_idle(builder) -> None:
    # battery_w = 1000 + 100 - 1000 = +100, within ±200 deadband
    inp = builder.build(
        battery_soc=60.0, battery_target_soc=80.0,
        pv_w=1000.0, house_load_w=1000.0, grid_import_w=100.0,
    )
    assert strategy_module.battery_focus(inp, _inv("idle")) == "idle"


# ── pv_destinations ───────────────────────────────────────────────────────────

def _pv_inp(builder, **overrides) -> Inputs:
    return builder.build(
        pv_w=overrides.pop("pv_w", 3000.0),
        house_load_w=overrides.pop("house_load_w", 1000.0),
        ev_power_w=overrides.pop("ev_power_w", 0.0),
        grid_import_w=overrides.pop("grid_import_w", -2000.0),
        **overrides,
    )


def _check_conservation(d: PvDestinations) -> None:
    total = d.to_house_w + d.to_ev_w + d.to_battery_w + d.to_grid_w
    assert total == pytest.approx(max(0.0, d.pv_w), abs=1e-6)


def test_pv_destinations_surplus_goes_to_grid(builder) -> None:
    # pv=3000, house=1000, battery_charging=0 (grid_import=-2000: battery_w=3000-2000-1000=0)
    # → to_house=1000, to_battery=0, to_grid=2000
    inp = _pv_inp(builder, pv_w=3000.0, house_load_w=1000.0, ev_power_w=0.0, grid_import_w=-2000.0)
    d = strategy_module.pv_destinations(inp, _inv("idle"))
    assert d.to_house_w == pytest.approx(1000.0)
    assert d.to_ev_w == pytest.approx(0.0)
    assert d.to_battery_w == pytest.approx(0.0)
    assert d.to_grid_w == pytest.approx(2000.0)
    _check_conservation(d)


def test_pv_destinations_pv_less_than_house(builder) -> None:
    # pv=500 < house=1000 → to_house=500, everything else 0
    inp = _pv_inp(builder, pv_w=500.0, house_load_w=1000.0, ev_power_w=0.0, grid_import_w=500.0)
    d = strategy_module.pv_destinations(inp, _inv("idle"))
    assert d.to_house_w == pytest.approx(500.0)
    assert d.to_battery_w == pytest.approx(0.0)
    assert d.to_grid_w == pytest.approx(0.0)
    _check_conservation(d)


def test_pv_destinations_zero_pv(builder) -> None:
    inp = _pv_inp(builder, pv_w=0.0, house_load_w=1000.0, ev_power_w=0.0, grid_import_w=1000.0)
    d = strategy_module.pv_destinations(inp, _inv("idle"))
    assert d.to_house_w == pytest.approx(0.0)
    assert d.to_ev_w == pytest.approx(0.0)
    assert d.to_battery_w == pytest.approx(0.0)
    assert d.to_grid_w == pytest.approx(0.0)
    _check_conservation(d)


def test_pv_destinations_battery_priority_cuts_ev(builder) -> None:
    # pv=3000, house_non_ev=500 (house=1500, ev=1000), battery_w=3000+0-1500=1500
    inp = _pv_inp(builder, pv_w=3000.0, house_load_w=1500.0, ev_power_w=1000.0, grid_import_w=0.0)
    d = strategy_module.pv_destinations(inp, _inv("battery_priority"))
    assert d.to_ev_w == pytest.approx(0.0)
    assert d.to_house_w == pytest.approx(500.0)  # only house_non_ev
    assert d.to_battery_w == pytest.approx(1500.0)
    assert d.to_grid_w == pytest.approx(1000.0)
    _check_conservation(d)


def test_pv_destinations_battery_priority_battery_discharging(builder) -> None:
    # battery_w = 500 - 2000 + 0 = -1500 < 0 → battery_charging_w = 0 → to_battery=0
    inp = _pv_inp(builder, pv_w=500.0, house_load_w=2000.0, ev_power_w=1000.0, grid_import_w=0.0)
    d = strategy_module.pv_destinations(inp, _inv("battery_priority"))
    assert d.to_battery_w == pytest.approx(0.0)
    assert d.to_ev_w == pytest.approx(0.0)
    _check_conservation(d)


def test_pv_destinations_ev_share_proportional_in_default_mode(builder) -> None:
    # house=2000, ev=1000 (50% of house); pv=2000 covers all → split: to_ev=1000, to_house=1000
    # battery_w = 0 → to_battery = 0
    inp = _pv_inp(builder, pv_w=2000.0, house_load_w=2000.0, ev_power_w=1000.0, grid_import_w=0.0)
    d = strategy_module.pv_destinations(inp, _inv("idle"))
    assert d.to_ev_w == pytest.approx(1000.0)
    assert d.to_house_w == pytest.approx(1000.0)
    assert d.to_battery_w == pytest.approx(0.0)
    _check_conservation(d)


def test_pv_destinations_battery_charges_after_house(builder) -> None:
    # pv=4000, house=1000, battery_w=4000+0-1000=3000 → to_battery=min(3000,3000)=3000
    # to_grid=0
    inp = _pv_inp(builder, pv_w=4000.0, house_load_w=1000.0, ev_power_w=0.0, grid_import_w=0.0)
    d = strategy_module.pv_destinations(inp, _inv("controlling"))
    assert d.to_house_w == pytest.approx(1000.0)
    assert d.to_battery_w == pytest.approx(3000.0)
    assert d.to_grid_w == pytest.approx(0.0)
    _check_conservation(d)


@pytest.mark.parametrize("pv,house,ev,grid,status", [
    (3000.0, 1000.0, 0.0,    -2000.0, "idle"),
    (0.0,    1000.0, 0.0,    1000.0,  "idle"),
    (4000.0, 1500.0, 1000.0, 0.0,     "battery_priority"),
    (500.0,  2000.0, 500.0,  1500.0,  "controlling"),
    (2500.0, 1000.0, 500.0,  0.0,     "idle"),
])
def test_pv_destinations_conservation_invariant(builder, pv, house, ev, grid, status) -> None:
    inp = _pv_inp(builder, pv_w=pv, house_load_w=house, ev_power_w=ev, grid_import_w=grid)
    d = strategy_module.pv_destinations(inp, _inv(status))
    _check_conservation(d)


# ── pv_focus ──────────────────────────────────────────────────────────────────

def _dest(pv, house=0.0, ev=0.0, battery=0.0, grid=0.0) -> PvDestinations:
    return PvDestinations(
        pv_w=pv, to_house_w=house, to_ev_w=ev, to_battery_w=battery, to_grid_w=grid
    )


def test_pv_focus_starting_up(builder) -> None:
    d = _dest(3000.0, house=3000.0)
    assert strategy_module.pv_focus(d, _inv("starting_up")) == "starting_up"


def test_pv_focus_no_production(builder) -> None:
    d = _dest(50.0)
    assert strategy_module.pv_focus(d, _inv("idle")) == "no_production"


def test_pv_focus_to_battery(builder) -> None:
    d = _dest(3000.0, house=300.0, battery=2700.0)
    assert strategy_module.pv_focus(d, _inv("idle")) == "to_battery"


def test_pv_focus_to_house(builder) -> None:
    d = _dest(3000.0, house=2700.0, battery=300.0)
    assert strategy_module.pv_focus(d, _inv("idle")) == "to_house"


def test_pv_focus_to_ev(builder) -> None:
    # EV accounts for 100% of PV → clearly dominant
    d = _dest(3000.0, ev=3000.0)
    assert strategy_module.pv_focus(d, _inv("idle")) == "to_ev"


def test_pv_focus_exporting(builder) -> None:
    d = _dest(3000.0, house=300.0, grid=2700.0)
    assert strategy_module.pv_focus(d, _inv("idle")) == "exporting"


def test_pv_focus_mixed(builder) -> None:
    # 40/30/30 split — no single destination ≥ 70%
    d = _dest(3000.0, house=1200.0, battery=900.0, grid=900.0)
    assert strategy_module.pv_focus(d, _inv("idle")) == "mixed"


# ── grid_focus ────────────────────────────────────────────────────────────────

def test_grid_focus_starting_up(builder) -> None:
    inp = builder.build(grid_import_w=1000.0, thresholds=_THRESHOLDS)
    assert strategy_module.grid_focus(inp, _inv("starting_up")) == "starting_up"


def test_grid_focus_peak_shaving_active(builder) -> None:
    inp = builder.build(grid_import_w=5000.0, thresholds=_THRESHOLDS)
    assert strategy_module.grid_focus(inp, _inv("peak_shaving")) == "peak_shaving_active"


def test_grid_focus_exporting(builder) -> None:
    inp = builder.build(grid_import_w=-500.0, thresholds=_THRESHOLDS)
    assert strategy_module.grid_focus(inp, _inv("idle")) == "exporting"


def test_grid_focus_balanced(builder) -> None:
    inp = builder.build(grid_import_w=50.0, thresholds=_THRESHOLDS)
    assert strategy_module.grid_focus(inp, _inv("idle")) == "balanced"


def test_grid_focus_at_peak_cap(builder) -> None:
    # cap=4500, import=4400 → |4400-4500|=100 ≤ 200 (proximity) → at_peak_cap
    inp = builder.build(grid_import_w=4400.0, thresholds=_THRESHOLDS)
    assert strategy_module.grid_focus(inp, _inv("idle")) == "at_peak_cap"


def test_grid_focus_importing(builder) -> None:
    # import=1000, cap=4500 → |1000-4500|=3500 > 200 → importing
    inp = builder.build(grid_import_w=1000.0, thresholds=_THRESHOLDS)
    assert strategy_module.grid_focus(inp, _inv("idle")) == "importing"
