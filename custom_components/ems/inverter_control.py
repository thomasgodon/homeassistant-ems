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

    if ev_charging_commanded:
        # Direct all available PV to AC (capped by total AC demand). Battery is neither
        # charged nor drained: magnitude never exceeds pv_w so battery gap isn't touched;
        # sign is always ≤ 0 so battery is never charged from AC.
        # Negative = inverter drains to AC; 0 = PV is 0 (night).
        return InverterDecision(
            inverter_verb=mapping.power_control_option,
            active_power_w=-min(pv_w, house_load_w),
            status="controlling",
        )

    return InverterDecision(
        inverter_verb=mapping.disabled_option,
        active_power_w=None,
        status="idle",
    )
