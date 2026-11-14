from __future__ import annotations

from . import strategy as strategy_module
from .models import Inputs, InverterDecision, VerbMapping


def decide(
    inputs: Inputs,
    *,
    commanded_ev_charge_w: float,
    currently_peak_shaving: bool = False,
) -> InverterDecision:
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
        max_active_power_w=inputs.thresholds.max_active_power_w,
        currently_peak_shaving=currently_peak_shaving,
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
    max_active_power_w: float | None = None,
    currently_peak_shaving: bool = False,
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
        max_active_power_w=max_active_power_w,
        currently_peak_shaving=currently_peak_shaving,
    )


# ── core logic ────────────────────────────────────────────────────────────────

# Peak shaving latches once active and only releases when grid import drops this
# far below max_grid_import_w.  Prevents the controller's own correction from
# immediately re-triggering peak shaving (bang-bang instability).
_PEAK_SHAVING_HYSTERESIS_W = 500.0


def _clamp_active_power(value: float, cap: float | None) -> float:
    """Clamp active_power_w to ±cap so inverter entity range is never exceeded."""
    if cap is None:
        return value
    return max(-cap, min(cap, value))


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
    max_active_power_w: float | None = None,
    currently_peak_shaving: bool = False,
) -> InverterDecision:
    if inverter_strategy == "Off":
        return InverterDecision(inverter_verb=None, active_power_w=None, status="disabled")

    # Priority 1 — peak shaving: grid spike above cap, only when EV is idle.
    # During an EV session the closed-loop EV integrator already drives
    # grid_import → max_grid_import_w; firing peak shaving simultaneously
    # causes double-correction that crashes ev_target to 0, stops the EV,
    # collapses grid_import, then unwinds to max — a sawtooth oscillation.
    #
    # Hysteresis: once latched the condition holds until grid import drops
    # _PEAK_SHAVING_HYSTERESIS_W below the cap, preventing the controller's
    # own injection from immediately un-triggering peak shaving (bang-bang).
    # In the hold band excess_w goes negative so active_power tapers smoothly.
    _peak_entry = grid_import_w > max_grid_import_w
    _peak_hold = (
        currently_peak_shaving
        and grid_import_w > max_grid_import_w - _PEAK_SHAVING_HYSTERESIS_W
    )
    if (_peak_entry or _peak_hold) and commanded_ev_charge_w == 0.0 and ev_power_w == 0.0:
        excess_w = grid_import_w - max_grid_import_w
        return InverterDecision(
            inverter_verb=mapping.power_control_option,
            active_power_w=_clamp_active_power(-(pv_w + excess_w), max_active_power_w),
            status="peak_shaving",
        )

    # Priority 2 — deficit hold: solar won't fill the battery today, so draw cheap
    # grid for house load during the cheapest remaining hours to preserve the battery
    # for the expensive evening. Applies regardless of EV strategy — battery holds
    # must fire even in Energy-optimised mode when grid is cheap and battery is short.
    # Bypass when PV alone covers the house — Self-Use handles it without EMS involvement.
    # Bypass during EV sessions in Grid-optimised mode — the EV integrator controls
    # grid import; holding the battery simultaneously steals PV from the EV and
    # causes unwanted grid draw. In Price-optimised mode the EV is deliberately
    # grid-fed during cheap hours, so the hold still fires: PV charges the battery,
    # house and EV both draw from cheap grid, battery is preserved for the evening peak.
    ev_session = commanded_ev_charge_w > 0.0 or ev_power_w > 0.0
    allow_during_session = ev_strategy == "Price-optimised"
    if (
        eod_suppress
        and pv_w < house_load_w
        and (not ev_session or allow_during_session)
    ):
        # When PV exceeds the non-EV house load, serve the house from PV and let
        # the surplus charge the battery via DC. When PV is insufficient, redirect
        # all PV straight into the battery (0 W AC output) and cover the house from
        # cheap grid — battery charges faster and avoids conversion losses.
        house_non_ev = max(0.0, house_load_w - ev_power_w)
        active_power = -house_non_ev if pv_w > house_non_ev else 0.0
        return InverterDecision(
            inverter_verb=mapping.power_control_option,
            active_power_w=_clamp_active_power(active_power, max_active_power_w),
            status="holding_battery",
        )

    # Priority 3 — Energy-optimised / Solar-only: peak shaving and deficit hold are
    # handled above. Energy-optimised caps charge to live PV surplus, so the battery is
    # inherently protected. Solar-only charges the EV with the whole PV production and
    # deliberately lets the battery (then grid) feed the house via native Self-Use.
    # Either way, release to Self-Use here (never battery_priority / controlling).
    if ev_strategy in ("Energy-optimised", "Solar-only"):
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
            active_power_w=_clamp_active_power(-house_non_ev, max_active_power_w),
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
        active_power_w=_clamp_active_power(-min(pv_w, house_load_w), max_active_power_w),
        status="controlling",
    )
