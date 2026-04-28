from __future__ import annotations

import dataclasses

from custom_components.ems import strategy as strategy_module
from custom_components.ems.models import (
    Features,
    InverterVerb,
    VerbMapping,
)
from .conftest import InputsBuilder, _DEFAULT_FEATURES, _DEFAULT_MAPPING, _DEFAULT_THRESHOLDS


def _features(**overrides) -> Features:
    return Features(**{**dataclasses.asdict(_DEFAULT_FEATURES), **overrides})


def _thresholds(**overrides):
    return dataclasses.replace(_DEFAULT_THRESHOLDS, **overrides)


# ── one test per ladder rule ──────────────────────────────────────────────────

def test_peak_shave_wins(builder: InputsBuilder) -> None:
    inp = builder.build(grid_import_15m_avg_w=4400, battery_soc=50)
    decision = strategy_module.decide(inp)
    assert decision.strategy == "peak_shave"
    assert decision.inverter_verb == InverterVerb.FORCE_DISCHARGE


def test_battery_floor_wins(builder: InputsBuilder) -> None:
    inp = builder.build(battery_soc=10, grid_import_15m_avg_w=500)
    decision = strategy_module.decide(inp)
    assert decision.strategy == "battery_floor"
    assert decision.inverter_verb == InverterVerb.IDLE


def test_ev_charging_coupling_wins(builder: InputsBuilder) -> None:
    inp = builder.build(ev_power_w=150, battery_soc=30)
    decision = strategy_module.decide(inp)
    assert decision.strategy == "ev_charging_coupling"
    assert decision.inverter_verb == InverterVerb.SELF_USE


def test_export_curtail_wins(builder: InputsBuilder) -> None:
    inp = builder.build(
        battery_soc=96,
        export_price=-0.01,
        ev_plugged=False,
        pv_w=3000,
        house_load_w=1500,
        ev_power_w=0,
    )
    decision = strategy_module.decide(inp)
    assert decision.strategy == "export_curtail"
    assert decision.inverter_verb == InverterVerb.LIMIT_EXPORT


def test_proactive_charge_wins(builder: InputsBuilder) -> None:
    inp = builder.build(
        import_price=0.03,
        battery_soc=40,
        pv_w=0,
        house_load_w=200,
    )
    decision = strategy_module.decide(inp)
    assert decision.strategy == "proactive_charge"
    assert decision.inverter_verb == InverterVerb.FORCE_CHARGE


def test_solar_to_battery_wins(builder: InputsBuilder) -> None:
    inp = builder.build(pv_w=2500, battery_soc=70)
    decision = strategy_module.decide(inp)
    assert decision.strategy == "solar_to_battery"
    assert decision.inverter_verb == InverterVerb.SELF_USE


def test_expensive_self_use_wins(builder: InputsBuilder) -> None:
    inp = builder.build(
        import_price=0.30, avg_24h_price=0.20, is_daytime=True, pv_w=0, house_load_w=1500
    )
    decision = strategy_module.decide(inp)
    assert decision.strategy == "expensive_self_use"
    assert decision.inverter_verb == InverterVerb.SELF_USE


def test_fallback_wins(builder: InputsBuilder) -> None:
    inp = builder.build()
    decision = strategy_module.decide(inp)
    assert decision.strategy == "fallback"
    assert decision.inverter_verb == InverterVerb.SELF_USE


# ── edge cases ────────────────────────────────────────────────────────────────

def test_hysteresis_no_flap(builder: InputsBuilder) -> None:
    """Oscillating 15m avg must not flip peak_risk back and forth more than once."""
    avgs = [4250, 4350, 4250, 4350]
    latched = False
    risk_states = []
    for avg in avgs:
        inp = builder.build(
            grid_import_15m_avg_w=avg,
            battery_soc=50,
            peak_risk_latched=latched,
        )
        decision = strategy_module.decide(inp)
        latched = decision.new_peak_risk
        risk_states.append(latched)

    flips = sum(a != b for a, b in zip(risk_states, risk_states[1:]))
    assert flips <= 1


def test_peak_cap_boundary_exact(builder: InputsBuilder) -> None:
    """grid_import_15m_avg_w == cap − headroom (4200) should trigger peak_risk."""
    inp = builder.build(grid_import_15m_avg_w=4200, battery_soc=50, peak_risk_latched=False)
    decision = strategy_module.decide(inp)
    assert decision.new_peak_risk is True


def test_soc_at_reserve_boundary(builder: InputsBuilder) -> None:
    """battery_soc == reserve_soc (10) must trigger battery_floor."""
    inp = builder.build(battery_soc=10)
    decision = strategy_module.decide(inp)
    assert decision.strategy == "battery_floor"


def test_ev_charging_coupling_beats_export_curtail(builder: InputsBuilder) -> None:
    """Both EV coupling and export curtail conditions true — coupling wins (higher in ladder)."""
    inp = builder.build(
        ev_power_w=200,
        battery_soc=96,
        export_price=-0.05,
        pv_w=3000,
        house_load_w=1500,
    )
    decision = strategy_module.decide(inp)
    assert decision.strategy == "ev_charging_coupling"


def test_mode_unsupported_force_discharge_fallthrough(builder: InputsBuilder) -> None:
    """With FORCE_DISCHARGE unsupported, peak shave must fall through."""
    options = {**_DEFAULT_MAPPING.inverter_options, InverterVerb.FORCE_DISCHARGE: None}
    mapping = VerbMapping(
        inverter_select_entity=_DEFAULT_MAPPING.inverter_select_entity,
        inverter_options=options,
        inverter_power_target_entity=_DEFAULT_MAPPING.inverter_power_target_entity,
    )
    inp = builder.build(
        grid_import_15m_avg_w=4400,
        battery_soc=50,
        mapping=mapping,
    )
    decision = strategy_module.decide(inp)
    assert decision.strategy != "peak_shave"
    assert "mode_unsupported_FORCE_DISCHARGE" in decision.unsupported_verbs


def test_export_curtail_battery_not_full(builder: InputsBuilder) -> None:
    """battery_soc=80 (below full_soc=95) must not fire export_curtail."""
    inp = builder.build(
        battery_soc=80,
        export_price=-0.01,
        pv_w=3000,
        house_load_w=1500,
    )
    decision = strategy_module.decide(inp)
    assert decision.strategy != "export_curtail"


def test_disabled_ev_charging(builder: InputsBuilder) -> None:
    """switch.ems_ev_charging=off must skip ev_coupling rule and report 0 W EV target."""
    inp = builder.build(
        ev_power_w=150,
        battery_soc=30,
        features=_features(ev_charging=False),
    )
    decision = strategy_module.decide(inp)
    assert decision.strategy != "ev_charging_coupling"
    assert decision.ev_charge_power_w == 0.0


def test_disabled_proactive_charge(builder: InputsBuilder) -> None:
    inp = builder.build(
        import_price=0.03,
        battery_soc=40,
        pv_w=0,
        house_load_w=200,
        features=_features(proactive_grid_charge=False),
    )
    decision = strategy_module.decide(inp)
    assert decision.strategy != "proactive_charge"


def test_expensive_self_use_night_skipped(builder: InputsBuilder) -> None:
    """Expensive price at night must fall through to fallback."""
    inp = builder.build(
        import_price=0.30,
        avg_24h_price=0.20,
        is_daytime=False,
        pv_w=0,
        house_load_w=1500,
    )
    decision = strategy_module.decide(inp)
    assert decision.strategy != "expensive_self_use"
    assert decision.strategy == "fallback"


# ── EV charge power tests ─────────────────────────────────────────────────────

def test_ev_not_plugged_reports_zero(builder: InputsBuilder) -> None:
    inp = builder.build(ev_plugged=False, ev_strategy="Solar-only")
    decision = strategy_module.decide(inp)
    assert decision.ev_charge_power_w == 0.0


def test_ev_strategy_off_reports_zero(builder: InputsBuilder) -> None:
    inp = builder.build(ev_plugged=True, ev_soc=50, ev_strategy="Off")
    decision = strategy_module.decide(inp)
    assert decision.ev_charge_power_w == 0.0


def test_ev_at_target_soc_reports_zero(builder: InputsBuilder) -> None:
    inp = builder.build(ev_plugged=True, ev_soc=80.0, ev_strategy="Solar-only")
    decision = strategy_module.decide(inp)
    assert decision.ev_charge_power_w == 0.0


def test_ev_solar_surplus_below_minimum_reports_zero(builder: InputsBuilder) -> None:
    """Solar surplus < ev_min_charge_w (1400) → 0 W."""
    inp = builder.build(
        ev_plugged=True,
        ev_soc=50,
        ev_strategy="Solar-only",
        pv_w=2500,
        house_load_w=1500,
        house_load_non_ev_w=1500,
        battery_power_w=800.0,  # battery absorbing 800 W; surplus = 2500-1500-800 = 200 < 1400
    )
    decision = strategy_module.decide(inp)
    assert decision.ev_charge_power_w == 0.0


def test_ev_solar_surplus_above_minimum(builder: InputsBuilder) -> None:
    """Solar surplus ≥ ev_min_charge_w → EV gets the surplus (capped at ev_max_kw)."""
    inp = builder.build(
        ev_plugged=True,
        ev_soc=50,
        ev_strategy="Solar-only",
        pv_w=5000,
        house_load_w=1500,
        house_load_non_ev_w=1500,
        battery_power_w=0.0,
        ev_max_charge_kw=7.4,
    )
    # surplus = 5000 - 1500 - 0 = 3500
    decision = strategy_module.decide(inp)
    assert decision.ev_charge_power_w == 3500.0


def test_ev_solar_surplus_capped_at_max_charge(builder: InputsBuilder) -> None:
    """Surplus > ev_max_charge_kw * 1000 → capped at ev_max_kw."""
    inp = builder.build(
        ev_plugged=True,
        ev_soc=50,
        ev_strategy="Solar-only",
        pv_w=15000,
        house_load_w=1500,
        house_load_non_ev_w=1500,
        battery_power_w=0.0,
        ev_max_charge_kw=7.4,
    )
    decision = strategy_module.decide(inp)
    assert decision.ev_charge_power_w == 7400.0


def test_ev_in_cheap_window_full_speed(builder: InputsBuilder) -> None:
    """in_cheap_window → ev_max_charge_kw."""
    inp = builder.build(
        ev_plugged=True,
        ev_soc=50,
        ev_strategy="Price-optimised",
        in_cheap_window=True,
        ev_max_charge_kw=7.4,
        peak_risk_latched=False,
    )
    decision = strategy_module.decide(inp)
    assert decision.ev_charge_power_w == 7400.0


def test_ev_export_curtail_always_absorbs(builder: InputsBuilder) -> None:
    """When export_curtail fires, EV absorbs regardless of ev_strategy."""
    inp = builder.build(
        battery_soc=96,
        export_price=-0.01,
        ev_plugged=True,
        ev_soc=40,
        ev_strategy="Off",  # Off — but curtail overrides
        pv_w=3000,
        house_load_w=1500,
        ev_power_w=0,
        ev_max_charge_kw=7.4,
    )
    decision = strategy_module.decide(inp)
    assert decision.strategy == "export_curtail"
    assert decision.ev_charge_power_w == 7400.0


def test_ev_export_curtail_at_target_soc_reports_zero(builder: InputsBuilder) -> None:
    """Even with export_curtail active, EV at target SoC → 0 W."""
    inp = builder.build(
        battery_soc=96,
        export_price=-0.01,
        ev_plugged=True,
        ev_soc=80.0,  # at target
        ev_strategy="Off",
        pv_w=3000,
        house_load_w=1500,
        ev_power_w=0,
        ev_max_charge_kw=7.4,
    )
    decision = strategy_module.decide(inp)
    assert decision.strategy == "export_curtail"
    assert decision.ev_charge_power_w == 0.0


def test_ev_peak_shave_caps_cheap_window_charge(builder: InputsBuilder) -> None:
    """During peak shave, cheap-window charge is capped by headroom formula."""
    inp = builder.build(
        grid_import_15m_avg_w=4400,
        battery_soc=50,
        ev_plugged=True,
        ev_soc=40,
        ev_strategy="Price-optimised",
        in_cheap_window=True,
        ev_max_charge_kw=7.4,
        house_load_non_ev_w=1500.0,
        peak_risk_latched=True,
    )
    # ev_cap = max(0, 4500 + 3000 - 1500) = 6000
    decision = strategy_module.decide(inp)
    assert decision.ev_charge_power_w == 6000.0


def test_ev_battery_charge_subtracted_from_solar(builder: InputsBuilder) -> None:
    """Battery absorbing solar reduces EV's available surplus."""
    inp = builder.build(
        ev_plugged=True,
        ev_soc=50,
        ev_strategy="Solar-only",
        pv_w=5000,
        house_load_w=1500,
        house_load_non_ev_w=1500,
        battery_power_w=2000.0,  # battery taking 2 kW; surplus = 5000-1500-2000 = 1500 ≥ 1400
        ev_max_charge_kw=7.4,
    )
    decision = strategy_module.decide(inp)
    assert decision.ev_charge_power_w == 1500.0
