from __future__ import annotations

from . import strategy as strategy_module
from .models import EvDecision, Inputs, InverterDecision, VerbMapping


def decide(inputs: Inputs, ev_decision: EvDecision) -> InverterDecision:
    """Compute inverter verb and active power target. Never raises."""
    return _compute(
        pv_w=inputs.pv_w,
        house_load_w=inputs.house_load_w,
        ev_power_w=inputs.ev_power_w,
        grid_import_w=inputs.grid_import_w,
        inverter_strategy=inputs.inverter_strategy,
        mapping=inputs.mapping,
        ev_charging_commanded=ev_decision.strategy == "ev_charging",
        eod_suppress=strategy_module.is_eod_suppress_hour(inputs),
        max_grid_import_w=inputs.thresholds.max_grid_import_w,
    )


def recompute(
    *,
    pv_w: float,
    house_load_w: float,
    ev_power_w: float,
    grid_import_w: float,
    inverter_strategy: str,
    mapping: VerbMapping,
    ev_charging_commanded: bool,
    eod_suppress: bool = False,
    max_grid_import_w: float = 0.0,
) -> InverterDecision:
    """Fast-path re-computation for the 1 s loop. Same logic as decide()."""
    return _compute(
        pv_w=pv_w,
        house_load_w=house_load_w,
        ev_power_w=ev_power_w,
        grid_import_w=grid_import_w,
        inverter_strategy=inverter_strategy,
        mapping=mapping,
        ev_charging_commanded=ev_charging_commanded,
        eod_suppress=eod_suppress,
        max_grid_import_w=max_grid_import_w,
    )


# ── core logic ────────────────────────────────────────────────────────────────

def _compute(
    *,
    pv_w: float,
    house_load_w: float,
    ev_power_w: float,
    grid_import_w: float,
    inverter_strategy: str,
    mapping: VerbMapping,
    ev_charging_commanded: bool,
    eod_suppress: bool,
    max_grid_import_w: float,
) -> InverterDecision:
    if inverter_strategy == "Off":
        return InverterDecision(inverter_verb=None, active_power_w=None, status="disabled")

    ev_idle = not ev_charging_commanded

    # Priority 1 — peak shaving: grid spike above cap, EV not active.
    # Battery discharges to cover the excess. EV must be idle to avoid
    # routing battery power through AC to the EV.
    if ev_idle and grid_import_w > max_grid_import_w:
        excess_w = grid_import_w - max_grid_import_w
        return InverterDecision(
            inverter_verb=mapping.power_control_option,
            active_power_w=-(pv_w + excess_w),
            status="peak_shaving",
        )

    # Priority 2 — EV charging commanded: direct all available PV to AC.
    # Battery is neither charged nor drained: magnitude never exceeds pv_w so
    # battery gap isn't touched; sign is always ≤ 0 so battery is never charged
    # from AC. Negative = inverter drains to AC; 0 = PV is 0 (night).
    if ev_charging_commanded:
        return InverterDecision(
            inverter_verb=mapping.power_control_option,
            active_power_w=-min(pv_w, house_load_w),
            status="controlling",
        )

    # Priority 3 — EOD suppress: hold battery during cheap daylight hours so
    # PV charges the battery instead of battery discharging for house load.
    if eod_suppress and pv_w > 0.0:
        return InverterDecision(
            inverter_verb=mapping.power_control_option,
            active_power_w=0.0,
            status="holding_battery",
        )

    # Priority 4 — idle: leave inverter to manage itself.
    return InverterDecision(
        inverter_verb=mapping.disabled_option,
        active_power_w=None,
        status="idle",
    )
