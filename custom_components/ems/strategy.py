from __future__ import annotations

from .models import Decision, Inputs, Thresholds


def decide(inputs: Inputs) -> Decision:
    """Compute EV charge target and battery discharge cap. Never raises."""
    house_non_ev = max(0.0, inputs.house_load_w - inputs.ev_power_w)
    return _make_decision(
        grid_import_w=inputs.grid_import_w,
        pv_w=inputs.pv_w,
        house_non_ev=house_non_ev,
        ev_power_w=inputs.ev_power_w,
        ev_plugged=inputs.ev_plugged,
        ev_soc=inputs.ev_soc,
        ev_strategy=inputs.ev_strategy,
        thresholds=inputs.thresholds,
    )


def recompute(
    *,
    grid_import_w: float,
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
        grid_import_w=grid_import_w,
        pv_w=pv_w,
        house_non_ev=house_non_ev,
        ev_power_w=ev_power_w,
        ev_plugged=ev_plugged,
        ev_soc=ev_soc,
        ev_strategy=ev_strategy,
        thresholds=thresholds,
    )


# ── core logic ────────────────────────────────────────────────────────────────

def _make_decision(
    *,
    grid_import_w: float,
    pv_w: float,
    house_non_ev: float,
    ev_power_w: float,
    ev_plugged: bool,
    ev_soc: float | None,
    ev_strategy: str,
    thresholds: Thresholds,
) -> Decision:
    # Hard gates — always idle
    if (
        not ev_plugged
        or ev_strategy == "Off"
        or (ev_soc is not None and ev_soc >= thresholds.ev_target_soc)
    ):
        return _idle()

    # Grid headroom: available grid capacity for EV after accounting for non-EV load.
    # grid_import_w − ev_power_w = what grid currently supplies to non-EV loads.
    grid_non_ev_import = grid_import_w - ev_power_w
    grid_headroom = max(0.0, thresholds.max_grid_import_w - grid_non_ev_import)

    # Solar surplus left after covering non-EV house load.
    solar_for_ev = max(0.0, pv_w - house_non_ev)

    # Battery may only discharge enough to cover the house shortfall from PV.
    battery_discharge_cap_w = max(0.0, house_non_ev - pv_w)

    ev_target_w = solar_for_ev + grid_headroom

    if ev_target_w < thresholds.ev_min_charge_w:
        return _idle()

    return Decision(
        strategy="ev_charging",
        battery_discharge_cap_w=battery_discharge_cap_w,
        ev_charge_power_w=ev_target_w,
    )


def _idle() -> Decision:
    return Decision(
        strategy="ev_idle",
        battery_discharge_cap_w=0.0,
        ev_charge_power_w=0.0,
    )
