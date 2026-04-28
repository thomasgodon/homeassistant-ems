from __future__ import annotations

import dataclasses

from custom_components.ems import strategy as strategy_module
from custom_components.ems.models import Features, InverterVerb

from .conftest import _DEFAULT_FEATURES, _DEFAULT_THRESHOLDS, InputsBuilder


def _features(**overrides) -> Features:
    return Features(**{**dataclasses.asdict(_DEFAULT_FEATURES), **overrides})


def _thresholds(**overrides):
    return dataclasses.replace(_DEFAULT_THRESHOLDS, **overrides)


# ── idle gates ────────────────────────────────────────────────────────────────

def test_ev_not_plugged_is_idle(builder: InputsBuilder) -> None:
    inp = builder.build(ev_plugged=False, ev_strategy="Solar-only")
    decision = strategy_module.decide(inp)
    assert decision.strategy == "ev_idle"
    assert decision.ev_charge_power_w == 0.0
    assert decision.inverter_verb is None


def test_ev_strategy_off_is_idle(builder: InputsBuilder) -> None:
    inp = builder.build(ev_plugged=True, ev_soc=50, ev_strategy="Off")
    decision = strategy_module.decide(inp)
    assert decision.strategy == "ev_idle"
    assert decision.ev_charge_power_w == 0.0


def test_ev_at_target_soc_is_idle(builder: InputsBuilder) -> None:
    inp = builder.build(ev_plugged=True, ev_soc=80.0, ev_strategy="Solar-only")
    decision = strategy_module.decide(inp)
    assert decision.strategy == "ev_idle"
    assert decision.ev_charge_power_w == 0.0


def test_ev_switch_off_is_idle(builder: InputsBuilder) -> None:
    inp = builder.build(
        ev_plugged=True,
        ev_soc=50,
        ev_strategy="Solar-only",
        pv_w=5000,
        house_load_w=1000,
        ev_power_w=0,
        features=_features(ev_charging=False),
    )
    decision = strategy_module.decide(inp)
    assert decision.strategy == "ev_idle"
    assert decision.ev_charge_power_w == 0.0


# ── solar surplus path ────────────────────────────────────────────────────────

def test_solar_surplus_below_minimum_is_idle(builder: InputsBuilder) -> None:
    """Surplus < ev_min_charge_w (1400) → idle."""
    inp = builder.build(
        ev_plugged=True,
        ev_soc=50,
        ev_strategy="Solar-only",
        pv_w=2500,
        house_load_w=1500,
        ev_power_w=0,
    )
    # surplus = 2500 - max(0, 1500-0) = 1000 < 1400
    decision = strategy_module.decide(inp)
    assert decision.strategy == "ev_idle"
    assert decision.ev_charge_power_w == 0.0


def test_solar_surplus_above_minimum(builder: InputsBuilder) -> None:
    """Surplus ≥ ev_min_charge_w → EV gets the surplus."""
    inp = builder.build(
        ev_plugged=True,
        ev_soc=50,
        ev_strategy="Solar-only",
        pv_w=5000,
        house_load_w=1500,
        ev_power_w=0,
        ev_max_charge_kw=7.4,
    )
    # house_non_ev = 1500; surplus = 5000-1500 = 3500
    decision = strategy_module.decide(inp)
    assert decision.strategy == "ev_charging_solar"
    assert decision.ev_charge_power_w == 3500.0
    assert decision.inverter_verb == InverterVerb.SELF_USE


def test_solar_surplus_capped_at_ev_max(builder: InputsBuilder) -> None:
    """Surplus > ev_max_kw * 1000 → capped at ev_max."""
    inp = builder.build(
        ev_plugged=True,
        ev_soc=50,
        ev_strategy="Solar-only",
        pv_w=15000,
        house_load_w=1500,
        ev_power_w=0,
        ev_max_charge_kw=7.4,
    )
    decision = strategy_module.decide(inp)
    assert decision.strategy == "ev_charging_solar"
    assert decision.ev_charge_power_w == 7400.0


def test_solar_house_non_ev_derived_from_load_minus_ev(builder: InputsBuilder) -> None:
    """house_non_ev = house_load - ev_power; surplus uses non-EV load."""
    inp = builder.build(
        ev_plugged=True,
        ev_soc=50,
        ev_strategy="Solar-only",
        pv_w=4000,
        house_load_w=3000,   # 2500 of which is EV; non-EV = 500
        ev_power_w=2500,
        ev_max_charge_kw=7.4,
    )
    # house_non_ev = max(0, 3000-2500) = 500; surplus = 4000-500 = 3500 ≥ 1400
    decision = strategy_module.decide(inp)
    assert decision.strategy == "ev_charging_solar"
    assert decision.ev_charge_power_w == 3500.0


# ── cheap window path ─────────────────────────────────────────────────────────

def test_cheap_window_full_speed(builder: InputsBuilder) -> None:
    """in_cheap_window → ev_target = clamp(max_grid + pv - house_non_ev, 0, ev_max)."""
    inp = builder.build(
        ev_plugged=True,
        ev_soc=50,
        ev_strategy="Price-optimised",
        in_cheap_window=True,
        pv_w=0,
        house_load_w=1500,
        ev_power_w=0,
        ev_max_charge_kw=7.4,
    )
    # ev_target = clamp(4500 + 0 - 1500, 0, 7400) = clamp(3000, 0, 7400) = 3000
    decision = strategy_module.decide(inp)
    assert decision.strategy == "ev_charging_grid"
    assert decision.ev_charge_power_w == 3000.0
    assert decision.inverter_verb == InverterVerb.SELF_USE


def test_cheap_window_capped_at_ev_max(builder: InputsBuilder) -> None:
    """ev_target capped at ev_max_charge_kw."""
    inp = builder.build(
        ev_plugged=True,
        ev_soc=50,
        ev_strategy="Price-optimised",
        in_cheap_window=True,
        pv_w=3000,
        house_load_w=500,
        ev_power_w=0,
        ev_max_charge_kw=7.4,
    )
    # ev_target = clamp(4500+3000-500, 0, 7400) = clamp(7000, 0, 7400) = 7000
    decision = strategy_module.decide(inp)
    assert decision.strategy == "ev_charging_grid"
    assert decision.ev_charge_power_w == 7000.0


def test_cheap_window_deadline_strategy(builder: InputsBuilder) -> None:
    """Deadline strategy also uses cheap window when in_cheap_window=True."""
    inp = builder.build(
        ev_plugged=True,
        ev_soc=50,
        ev_strategy="Deadline",
        in_cheap_window=True,
        pv_w=0,
        house_load_w=1000,
        ev_power_w=0,
        ev_max_charge_kw=7.4,
    )
    decision = strategy_module.decide(inp)
    assert decision.strategy == "ev_charging_grid"


def test_solar_only_strategy_ignores_cheap_window(builder: InputsBuilder) -> None:
    """Solar-only mode never uses grid, even with in_cheap_window=True."""
    inp = builder.build(
        ev_plugged=True,
        ev_soc=50,
        ev_strategy="Solar-only",
        in_cheap_window=True,
        pv_w=500,
        house_load_w=1500,
        ev_power_w=0,
        ev_max_charge_kw=7.4,
    )
    # pv < house → no surplus → idle (Solar-only ignores cheap window)
    decision = strategy_module.decide(inp)
    assert decision.strategy == "ev_idle"
    assert decision.ev_charge_power_w == 0.0


def test_outside_cheap_window_falls_back_to_solar(builder: InputsBuilder) -> None:
    """Price-optimised outside window falls through to solar surplus path."""
    inp = builder.build(
        ev_plugged=True,
        ev_soc=50,
        ev_strategy="Price-optimised",
        in_cheap_window=False,
        pv_w=5000,
        house_load_w=1500,
        ev_power_w=0,
        ev_max_charge_kw=7.4,
    )
    # no cheap window → solar path; surplus = 3500 ≥ 1400
    decision = strategy_module.decide(inp)
    assert decision.strategy == "ev_charging_solar"
    assert decision.ev_charge_power_w == 3500.0


# ── inverter discharge cap ────────────────────────────────────────────────────

def test_inverter_cap_when_pv_exceeds_house(builder: InputsBuilder) -> None:
    """When pv > house_non_ev, cap = 0 (battery doesn't need to discharge)."""
    inp = builder.build(
        ev_plugged=True,
        ev_soc=50,
        ev_strategy="Solar-only",
        pv_w=5000,
        house_load_w=1500,
        ev_power_w=0,
        ev_max_charge_kw=7.4,
    )
    decision = strategy_module.decide(inp)
    assert decision.inverter_power_target_w == 0.0


def test_inverter_cap_when_pv_below_house(builder: InputsBuilder) -> None:
    """When in cheap window and pv < house_non_ev, cap = house_non_ev - pv."""
    inp = builder.build(
        ev_plugged=True,
        ev_soc=50,
        ev_strategy="Price-optimised",
        in_cheap_window=True,
        pv_w=500,
        house_load_w=2000,
        ev_power_w=0,
        ev_max_charge_kw=7.4,
    )
    # house_non_ev = 2000; cap = max(0, 2000-500) = 1500
    decision = strategy_module.decide(inp)
    assert decision.inverter_power_target_w == 1500.0


def test_inverter_not_written_when_idle(builder: InputsBuilder) -> None:
    """When EV is idle, inverter_verb is None (controller skips write)."""
    inp = builder.build(ev_plugged=False)
    decision = strategy_module.decide(inp)
    assert decision.inverter_verb is None
    assert decision.inverter_power_target_w is None


# ── recompute fast path ───────────────────────────────────────────────────────

def test_recompute_matches_decide(builder: InputsBuilder) -> None:
    """recompute() with same values as decide() must return same ev_charge_power_w."""
    inp = builder.build(
        ev_plugged=True,
        ev_soc=50,
        ev_strategy="Solar-only",
        pv_w=5000,
        house_load_w=1500,
        ev_power_w=0,
        ev_max_charge_kw=7.4,
    )
    full = strategy_module.decide(inp)
    fast = strategy_module.recompute(
        pv_w=inp.pv_w,
        house_load_w=inp.house_load_w,
        ev_power_w=inp.ev_power_w,
        ev_plugged=inp.ev_plugged,
        ev_soc=inp.ev_soc,
        ev_strategy=inp.ev_strategy,
        ev_charging_enabled=inp.features.ev_charging,
        in_cheap_window=inp.in_cheap_window,
        ev_max_charge_kw=inp.ev_max_charge_kw,
        thresholds=inp.thresholds,
    )
    assert fast.ev_charge_power_w == full.ev_charge_power_w
    assert fast.strategy == full.strategy
