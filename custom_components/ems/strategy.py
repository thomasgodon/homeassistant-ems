from __future__ import annotations

from .models import EvChargingState, EvDecision, Inputs, Thresholds


def decide(inputs: Inputs) -> EvDecision:
    """Compute EV charge target. Never raises."""
    house_non_ev = max(0.0, inputs.house_load_w - inputs.ev_power_w)
    return _make_decision(
        house_non_ev=house_non_ev,
        grid_import_w=inputs.grid_import_w,
        ev_power_w=inputs.ev_power_w,
        ev_plugged=inputs.ev_plugged,
        ev_soc=inputs.ev_soc,
        ev_strategy=inputs.ev_strategy,
        is_cheap_hour=inputs.is_cheap_hour,
        thresholds=inputs.thresholds,
    )


def recompute(
    *,
    pv_w: float,
    house_load_w: float,
    ev_power_w: float,
    grid_import_w: float,
    ev_plugged: bool,
    ev_soc: float | None,
    ev_strategy: str,
    is_cheap_hour: bool,
    thresholds: Thresholds,
) -> EvDecision:
    """Fast-path re-computation for the 1 s loop. Same logic as decide()."""
    house_non_ev = max(0.0, house_load_w - ev_power_w)
    return _make_decision(
        house_non_ev=house_non_ev,
        grid_import_w=grid_import_w,
        ev_power_w=ev_power_w,
        ev_plugged=ev_plugged,
        ev_soc=ev_soc,
        ev_strategy=ev_strategy,
        is_cheap_hour=is_cheap_hour,
        thresholds=thresholds,
    )


# ── core logic ────────────────────────────────────────────────────────────────

def _make_decision(
    *,
    house_non_ev: float,
    grid_import_w: float,
    ev_power_w: float,
    ev_plugged: bool,
    ev_soc: float | None,
    ev_strategy: str,
    is_cheap_hour: bool,
    thresholds: Thresholds,
) -> EvDecision:
    # Hard gates — evaluated in priority order
    if ev_strategy == "Off":
        return _idle("charging_disabled")
    if ev_strategy == "Price-optimised" and not is_cheap_hour:
        return _idle("outside_cheap_hours")
    if not ev_plugged:
        return _idle("ev_not_connected")
    if ev_soc is not None and ev_soc >= thresholds.ev_target_soc:
        return _idle("target_soc_reached")

    # Closed-loop: non-EV grid draw = grid_import_w − ev_power_w; EV gets the remaining headroom.
    ev_target_w = max(0.0, thresholds.max_grid_import_w - grid_import_w + ev_power_w)

    if thresholds.max_ev_charge_w is not None:
        ev_target_w = min(ev_target_w, thresholds.max_ev_charge_w)

    if ev_target_w < thresholds.ev_min_charge_w:
        return _idle("not_enough_power")

    return EvDecision(
        strategy="ev_charging",
        ev_charge_power_w=ev_target_w,
        charging_state="charging",
    )


def _idle(charging_state: EvChargingState) -> EvDecision:
    return EvDecision(
        strategy="ev_idle",
        ev_charge_power_w=0.0,
        charging_state=charging_state,
    )
