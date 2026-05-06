from __future__ import annotations

from .models import Inputs, InverterDecision, VerbMapping


def decide(inputs: Inputs) -> InverterDecision:
    """Compute inverter verb and active power target. Never raises."""
    return _compute(
        pv_w=inputs.pv_w,
        house_load_w=inputs.house_load_w,
        ev_power_w=inputs.ev_power_w,
        inverter_strategy=inputs.inverter_strategy,
        mapping=inputs.mapping,
    )


def recompute(
    *,
    pv_w: float,
    house_load_w: float,
    ev_power_w: float,
    inverter_strategy: str,
    mapping: VerbMapping,
) -> InverterDecision:
    """Fast-path re-computation for the 1 s loop. Same logic as decide()."""
    return _compute(
        pv_w=pv_w,
        house_load_w=house_load_w,
        ev_power_w=ev_power_w,
        inverter_strategy=inverter_strategy,
        mapping=mapping,
    )


# ── core logic ────────────────────────────────────────────────────────────────

def _compute(
    *,
    pv_w: float,
    house_load_w: float,
    ev_power_w: float,
    inverter_strategy: str,
    mapping: VerbMapping,
) -> InverterDecision:
    if inverter_strategy == "Off":
        return InverterDecision(inverter_verb=None, active_power_w=None)

    house_non_ev = max(0.0, house_load_w - ev_power_w)

    if ev_power_w > 0.0:
        # Battery must not discharge to feed the EV: clamp active_power to >= 0.
        return InverterDecision(
            inverter_verb=mapping.power_control_option,
            active_power_w=max(0.0, pv_w - house_non_ev),
        )

    return InverterDecision(
        inverter_verb=mapping.disabled_option,
        active_power_w=None,
    )
