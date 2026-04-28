from __future__ import annotations

from .models import Decision, Inputs, InverterVerb, Thresholds


def decide(inputs: Inputs) -> Decision:
    """Compute EV charge target and inverter settings. Never raises."""
    house_non_ev = max(0.0, inputs.house_load_w - inputs.ev_power_w)
    return _make_decision(
        pv_w=inputs.pv_w,
        house_non_ev=house_non_ev,
        ev_plugged=inputs.ev_plugged,
        ev_soc=inputs.ev_soc,
        ev_strategy=inputs.ev_strategy,
        ev_charging_enabled=inputs.features.ev_charging,
        in_cheap_window=inputs.in_cheap_window,
        ev_max_charge_kw=inputs.ev_max_charge_kw,
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
    ev_charging_enabled: bool,
    in_cheap_window: bool,
    ev_max_charge_kw: float,
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
        ev_charging_enabled=ev_charging_enabled,
        in_cheap_window=in_cheap_window,
        ev_max_charge_kw=ev_max_charge_kw,
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
    ev_charging_enabled: bool,
    in_cheap_window: bool,
    ev_max_charge_kw: float,
    thresholds: Thresholds,
) -> Decision:
    ev_max_w = ev_max_charge_kw * 1000.0
    # Discharge cap: battery may cover house deficit, but no more.
    inverter_cap_w = max(0.0, house_non_ev - pv_w)
    surplus = max(0.0, pv_w - house_non_ev)

    # Hard gates — always idle
    if (
        not ev_plugged
        or (ev_soc is not None and ev_soc >= thresholds.ev_target_soc)
        or ev_strategy == "Off"
        or not ev_charging_enabled
    ):
        return _idle()

    # Cheap window (Deadline / Price-optimised) — pull from grid + solar
    if ev_strategy != "Solar-only" and in_cheap_window:
        ev_target_w = min(
            max(0.0, thresholds.max_grid_import_w + pv_w - house_non_ev),
            ev_max_w,
        )
        if ev_target_w > 0:
            return Decision(
                strategy="ev_charging_grid",
                inverter_verb=InverterVerb.SELF_USE,
                inverter_power_target_w=inverter_cap_w,
                ev_charge_power_w=ev_target_w,
            )
        return _idle()

    # Solar surplus path (Solar-only mode or outside cheap window)
    if surplus >= thresholds.ev_min_charge_w:
        return Decision(
            strategy="ev_charging_solar",
            inverter_verb=InverterVerb.SELF_USE,
            inverter_power_target_w=inverter_cap_w,
            ev_charge_power_w=min(surplus, ev_max_w),
        )

    return _idle()


def _idle() -> Decision:
    return Decision(
        strategy="ev_idle",
        inverter_verb=None,
        inverter_power_target_w=None,
        ev_charge_power_w=0.0,
    )
