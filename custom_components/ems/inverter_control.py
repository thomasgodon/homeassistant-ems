from __future__ import annotations

from . import strategy as strategy_module
from .models import Inputs, InverterDecision, VerbMapping


def decide(inputs: Inputs, *, commanded_ev_charge_w: float) -> InverterDecision:
    """Compute inverter verb and active power target. Never raises."""
    return _compute(
        pv_w=inputs.pv_w,
        house_load_w=inputs.house_load_w,
        ev_power_w=inputs.ev_power_w,
        grid_import_w=inputs.grid_import_w,
        inverter_strategy=inputs.inverter_strategy,
        mapping=inputs.mapping,
        eod_suppress=strategy_module.is_eod_suppress_hour(inputs),
        max_grid_import_w=inputs.thresholds.max_grid_import_w,
        commanded_ev_charge_w=commanded_ev_charge_w,
        battery_soc=inputs.battery_soc,
        battery_target_soc=inputs.battery_target_soc,
        ev_strategy=inputs.ev_strategy,
        solar_shortfall_kwh_result=strategy_module.solar_shortfall_kwh(inputs),
    )


def recompute(
    *,
    pv_w: float,
    house_load_w: float,
    ev_power_w: float,
    grid_import_w: float,
    inverter_strategy: str,
    mapping: VerbMapping,
    eod_suppress: bool = False,
    max_grid_import_w: float = 0.0,
    commanded_ev_charge_w: float = 0.0,
    battery_soc: float | None = None,
    battery_target_soc: float = 80.0,
    ev_strategy: str = "Off",
    solar_shortfall_kwh_result: float | None = None,
) -> InverterDecision:
    """Fast-path re-computation for the 1 s loop. Same logic as decide()."""
    return _compute(
        pv_w=pv_w,
        house_load_w=house_load_w,
        ev_power_w=ev_power_w,
        grid_import_w=grid_import_w,
        inverter_strategy=inverter_strategy,
        mapping=mapping,
        eod_suppress=eod_suppress,
        max_grid_import_w=max_grid_import_w,
        commanded_ev_charge_w=commanded_ev_charge_w,
        battery_soc=battery_soc,
        battery_target_soc=battery_target_soc,
        ev_strategy=ev_strategy,
        solar_shortfall_kwh_result=solar_shortfall_kwh_result,
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
    eod_suppress: bool,
    max_grid_import_w: float,
    commanded_ev_charge_w: float,
    battery_soc: float | None = None,
    battery_target_soc: float = 80.0,
    ev_strategy: str = "Off",
    solar_shortfall_kwh_result: float | None = None,
) -> InverterDecision:
    if inverter_strategy == "Off":
        return InverterDecision(inverter_verb=None, active_power_w=None, status="disabled")

    # Priority 1 — peak shaving: grid spike above cap, only when EV is idle.
    # During an EV session the closed-loop EV integrator already drives
    # grid_import → max_grid_import_w; firing peak shaving simultaneously
    # causes double-correction that crashes ev_target to 0, stops the EV,
    # collapses grid_import, then unwinds to max — a sawtooth oscillation.
    if (
        grid_import_w > max_grid_import_w
        and commanded_ev_charge_w == 0.0
        and ev_power_w == 0.0
    ):
        excess_w = grid_import_w - max_grid_import_w
        return InverterDecision(
            inverter_verb=mapping.power_control_option,
            active_power_w=-(pv_w + excess_w),
            status="peak_shaving",
        )

    # Priority 2 — deficit hold: solar won't fill the battery today, so draw cheap
    # grid for house load during the cheapest remaining hours to preserve the battery
    # for the expensive evening. Applies regardless of EV strategy — battery holds
    # must fire even in Energy-optimised mode when grid is cheap and battery is short.
    # Bypass when PV alone covers the house — Self-Use handles it without EMS involvement.
    # Bypass during EV sessions — the EV integrator already controls grid import;
    # holding the battery simultaneously steals PV from the EV and causes grid draw.
    if (
        eod_suppress
        and pv_w < house_load_w
        and commanded_ev_charge_w == 0.0
        and ev_power_w == 0.0
    ):
        return InverterDecision(
            inverter_verb=mapping.power_control_option,
            active_power_w=0.0,
            status="holding_battery",
        )

    # Priority 3 — Energy-optimised mode: peak shaving and deficit hold are handled
    # above. For EV-session management the strategy caps charge to live PV surplus,
    # so the battery is inherently protected — release to Self-Use here.
    if ev_strategy == "Energy-optimised":
        return InverterDecision(
            inverter_verb=mapping.disabled_option,
            active_power_w=None,
            status="idle",
        )

    # Priority 4 — battery priority: solar forecast won't fill the battery and an EV
    # session is active. PV is reserved for house (non-EV) load and battery charging;
    # the EV is fed from the grid only.
    if (
        solar_shortfall_kwh_result is not None
        and solar_shortfall_kwh_result > 0.0
        and (commanded_ev_charge_w > 0.0 or ev_power_w > 0.0)
    ):
        house_non_ev = max(0.0, house_load_w - ev_power_w)
        return InverterDecision(
            inverter_verb=mapping.power_control_option,
            active_power_w=-house_non_ev,
            status="battery_priority",
        )

    # Priority 5 — idle: EMS is not commanding any charge and the EV is not
    # drawing. Release inverter to its native Self-Use Mode; PV routing and
    # battery management happen autonomously without EMS involvement.
    if commanded_ev_charge_w == 0.0 and ev_power_w == 0.0:
        return InverterDecision(
            inverter_verb=mapping.disabled_option,
            active_power_w=None,
            status="idle",
        )

    # Default — EMS is actively charging the EV (or EV is winding down) and the
    # battery forecast is fine. Direct available PV to AC; battery never drained for
    # EV (magnitude capped by pv_w), never charged from AC (sign always ≤ 0).
    return InverterDecision(
        inverter_verb=mapping.power_control_option,
        active_power_w=-min(pv_w, house_load_w),
        status="controlling",
    )
