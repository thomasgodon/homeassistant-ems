from __future__ import annotations

from .models import EvDecision, Inputs, InverterDecision, VerbMapping


def decide(inputs: Inputs, ev_decision: EvDecision) -> InverterDecision:
    """Compute inverter verb and active power target. Never raises."""
    return _compute(
        pv_w=inputs.pv_w,
        house_load_w=inputs.house_load_w,
        ev_power_w=inputs.ev_power_w,
        inverter_strategy=inputs.inverter_strategy,
        mapping=inputs.mapping,
        ev_charging_commanded=ev_decision.strategy == "ev_charging",
    )


def recompute(
    *,
    pv_w: float,
    house_load_w: float,
    ev_power_w: float,
    inverter_strategy: str,
    mapping: VerbMapping,
    ev_charging_commanded: bool,
) -> InverterDecision:
    """Fast-path re-computation for the 1 s loop. Same logic as decide()."""
    return _compute(
        pv_w=pv_w,
        house_load_w=house_load_w,
        ev_power_w=ev_power_w,
        inverter_strategy=inverter_strategy,
        mapping=mapping,
        ev_charging_commanded=ev_charging_commanded,
    )


# ── core logic ────────────────────────────────────────────────────────────────

def _compute(
    *,
    pv_w: float,
    house_load_w: float,
    ev_power_w: float,
    inverter_strategy: str,
    mapping: VerbMapping,
    ev_charging_commanded: bool,
) -> InverterDecision:
    if inverter_strategy == "Off":
        return InverterDecision(inverter_verb=None, active_power_w=None, status="disabled")

    house_non_ev = max(0.0, house_load_w - ev_power_w)

    if ev_charging_commanded:
        # Battery must not be charged while EV is charging: clamp active_power to <= 0.
        # Negative = inverter drains to AC (PV/battery feeds house+EV); 0 = idle.
        return InverterDecision(
            inverter_verb=mapping.power_control_option,
            active_power_w=min(0.0, pv_w - house_non_ev),
            status="controlling",
        )

    return InverterDecision(
        inverter_verb=mapping.disabled_option,
        active_power_w=None,
        status="idle",
    )
