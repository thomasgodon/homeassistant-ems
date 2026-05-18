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
        cheap_for_inverter=_is_cheap_hour_for_inverter(inputs.prices_today, inputs.now.hour),
        battery_soc=inputs.battery_soc,
        battery_target_soc=inputs.battery_target_soc,
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
    cheap_for_inverter: bool = False,
    battery_soc: float | None = None,
    battery_target_soc: float = 80.0,
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
        cheap_for_inverter=cheap_for_inverter,
        battery_soc=battery_soc,
        battery_target_soc=battery_target_soc,
    )


# ── core logic ────────────────────────────────────────────────────────────────

def _is_cheap_hour_for_inverter(prices_today: tuple[float, ...] | None, current_hour: int) -> bool:
    if prices_today is None or len(prices_today) < 24:
        return False
    threshold = (min(prices_today) + max(prices_today)) / 2
    return prices_today[current_hour] <= threshold


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
    cheap_for_inverter: bool = False,
    battery_soc: float | None = None,
    battery_target_soc: float = 80.0,
) -> InverterDecision:
    if inverter_strategy == "Off":
        return InverterDecision(inverter_verb=None, active_power_w=None, status="disabled")

    # Priority 1 — peak shaving: grid spike above cap.
    # Battery discharges to cover the excess.
    if grid_import_w > max_grid_import_w:
        excess_w = grid_import_w - max_grid_import_w
        return InverterDecision(
            inverter_verb=mapping.power_control_option,
            active_power_w=-(pv_w + excess_w),
            status="peak_shaving",
        )

    # Priority 2 — EOD suppress: hold battery during cheap daylight hours so
    # PV charges the battery instead of battery discharging for house load.
    if eod_suppress and pv_w > 0.0:
        return InverterDecision(
            inverter_verb=mapping.power_control_option,
            active_power_w=0.0,
            status="holding_battery",
        )

    # Priority 3 — cheap-hour hold: no EV activity, current hour is cheap, and battery
    # is below target. Force active_power_w=0 to prevent battery drain; grid covers house.
    # Skipped when battery is at/above target — no reason to grid-fill an already-full battery.
    # When battery_soc is unknown (None), hold conservatively.
    if (
        commanded_ev_charge_w == 0.0
        and ev_power_w == 0.0
        and cheap_for_inverter
        and (battery_soc is None or battery_soc < battery_target_soc)
    ):
        return InverterDecision(
            inverter_verb=mapping.power_control_option,
            active_power_w=0.0,
            status="holding_battery_cheap",
        )

    # Priority 4 — idle: EMS is not commanding any charge and the EV is not
    # drawing. Release inverter to its native Self-Use Mode; PV routing and
    # battery management happen autonomously without EMS involvement.
    if commanded_ev_charge_w == 0.0 and ev_power_w == 0.0:
        return InverterDecision(
            inverter_verb=mapping.disabled_option,
            active_power_w=None,
            status="idle",
        )

    # Default — EMS is actively charging the EV (or EV is winding down).
    # Direct available PV to AC; battery never drained for EV (magnitude
    # capped by pv_w), never charged from AC (sign always ≤ 0).
    return InverterDecision(
        inverter_verb=mapping.power_control_option,
        active_power_w=-min(pv_w, house_load_w),
        status="controlling",
    )
