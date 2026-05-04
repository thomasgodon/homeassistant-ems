from __future__ import annotations

from .models import Decision, EvChargingState, Inputs, Thresholds


def decide(inputs: Inputs) -> Decision:
    """Compute EV charge target and inverter active power. Never raises."""
    house_non_ev = max(0.0, inputs.house_load_w - inputs.ev_power_w)
    return _make_decision(
        pv_w=inputs.pv_w,
        house_non_ev=house_non_ev,
        ev_plugged=inputs.ev_plugged,
        ev_soc=inputs.ev_soc,
        ev_strategy=inputs.ev_strategy,
        thresholds=inputs.thresholds,
    )


def recompute(
    *,
    pv_w: float,
    house_load_w: float,
    ev_power_w: float,
    ev_plugged: bool,
    ev_soc: float | None,
    ev_strategy: str,
    thresholds: Thresholds,
) -> Decision:
    """Fast-path re-computation for the 1 s loop. Same logic as decide()."""
    house_non_ev = max(0.0, house_load_w - ev_power_w)
    return _make_decision(
        pv_w=pv_w,
        house_non_ev=house_non_ev,
        ev_plugged=ev_plugged,
        ev_soc=ev_soc,
        ev_strategy=ev_strategy,
        thresholds=thresholds,
    )


# ── core logic ────────────────────────────────────────────────────────────────

def _make_decision(
    *,
    pv_w: float,
    house_non_ev: float,
    ev_plugged: bool,
    ev_soc: float | None,
    ev_strategy: str,
    thresholds: Thresholds,
) -> Decision:
    # Hard gates — evaluated in priority order
    if ev_strategy == "Off":
        return _idle("charging_disabled")
    if not ev_plugged:
        return _idle("ev_not_connected")
    if ev_soc is not None and ev_soc >= thresholds.ev_target_soc:
        return _idle("target_soc_reached")

    # Power balance: EV can use the full grid allowance plus whatever PV covers house load.
    # Derivation: grid_import = house_non_ev + ev_target − pv_w; cap at max_grid_import_w.
    ev_target_w = max(0.0, thresholds.max_grid_import_w + pv_w - house_non_ev)

    if thresholds.max_ev_charge_w is not None:
        ev_target_w = min(ev_target_w, thresholds.max_ev_charge_w)

    # Positive = battery discharges for house shortfall; negative = PV surplus (battery can absorb).
    active_power_w = house_non_ev - pv_w

    if ev_target_w < thresholds.ev_min_charge_w:
        return _idle("not_enough_power")

    return Decision(
        strategy="ev_charging",
        active_power_w=active_power_w,
        ev_charge_power_w=ev_target_w,
        charging_state="charging",
    )


def _idle(charging_state: EvChargingState) -> Decision:
    return Decision(
        strategy="ev_idle",
        active_power_w=0.0,
        ev_charge_power_w=0.0,
        charging_state=charging_state,
    )
