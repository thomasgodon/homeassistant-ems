from __future__ import annotations

import dataclasses

from custom_components.ems import strategy as strategy_module
from custom_components.ems.models import (
    EvVerb,
    Features,
    InverterVerb,
    VerbMapping,
)

from .conftest import InputsBuilder, _DEFAULT_FEATURES, _DEFAULT_MAPPING


def _features(**overrides) -> Features:
    return Features(**{**dataclasses.asdict(_DEFAULT_FEATURES), **overrides})


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


def test_solar_to_ev_wins(builder: InputsBuilder) -> None:
    inp = builder.build(pv_w=4000, battery_soc=96, ev_plugged=True, ev_soc=50)
    decision = strategy_module.decide(inp)
    assert decision.strategy == "solar_to_ev"
    assert decision.ev_verb == EvVerb.SOLAR_ONLY


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


def test_off_when_mode_off(builder: InputsBuilder) -> None:
    inp = builder.build(ems_mode="Off", grid_import_15m_avg_w=4400, battery_soc=50)
    decision = strategy_module.decide(inp)
    assert decision.strategy == "off"
    assert decision.inverter_verb is None
    assert decision.ev_verb is None


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

    # Should not flip more than once
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
        ev_select_entity=_DEFAULT_MAPPING.ev_select_entity,
        ev_options=_DEFAULT_MAPPING.ev_options,
        ev_max_power_entity=_DEFAULT_MAPPING.ev_max_power_entity,
    )
    inp = builder.build(
        grid_import_15m_avg_w=4400,
        battery_soc=50,
        mapping=mapping,
    )
    decision = strategy_module.decide(inp)
    assert decision.strategy != "peak_shave"
    assert "mode_unsupported_FORCE_DISCHARGE" in decision.unsupported_verbs


def test_export_curtail_ev_absorb_on(builder: InputsBuilder) -> None:
    inp = builder.build(
        battery_soc=96,
        export_price=-0.01,
        ev_plugged=True,
        ev_soc=40,
        pv_w=3000,
        house_load_w=1500,
        ev_power_w=0,
        features=_features(ev_absorbs_curtail=True),
    )
    decision = strategy_module.decide(inp)
    assert decision.strategy == "export_curtail"
    assert decision.inverter_verb == InverterVerb.LIMIT_EXPORT
    assert decision.ev_verb == EvVerb.CHARGE_NOW


def test_export_curtail_ev_absorb_off(builder: InputsBuilder) -> None:
    inp = builder.build(
        battery_soc=96,
        export_price=-0.01,
        ev_plugged=True,
        ev_soc=40,
        pv_w=3000,
        house_load_w=1500,
        ev_power_w=0,
        features=_features(ev_absorbs_curtail=False),
    )
    decision = strategy_module.decide(inp)
    assert decision.strategy == "export_curtail"
    assert decision.inverter_verb == InverterVerb.LIMIT_EXPORT
    # EV not forced by curtail — ev_overlay decides
    assert decision.ev_verb != EvVerb.CHARGE_NOW or decision.ev_verb == EvVerb.IDLE


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


def test_disabled_peak_shave(builder: InputsBuilder) -> None:
    inp = builder.build(
        grid_import_15m_avg_w=4400,
        battery_soc=50,
        features=_features(peak_shaving=False),
    )
    decision = strategy_module.decide(inp)
    assert decision.strategy != "peak_shave"


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


def test_disabled_export_curtail(builder: InputsBuilder) -> None:
    inp = builder.build(
        battery_soc=96,
        export_price=-0.01,
        pv_w=3000,
        house_load_w=1500,
        features=_features(export_curtail=False),
    )
    decision = strategy_module.decide(inp)
    assert decision.strategy != "export_curtail"


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
