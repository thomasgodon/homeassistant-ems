from __future__ import annotations

import math

from .models import EvChargingState, EvDecision, Inputs, Thresholds


def decide(inputs: Inputs) -> EvDecision:
    """Compute EV charge target. Never raises."""
    house_non_ev = max(0.0, inputs.house_load_w - inputs.ev_power_w)
    return _make_decision(
        pv_w=inputs.pv_w,
        house_non_ev=house_non_ev,
        grid_import_w=inputs.grid_import_w,
        ev_power_w=inputs.ev_power_w,
        last_ev_target_w=inputs.last_ev_target_w,
        ev_plugged=inputs.ev_plugged,
        ev_soc=inputs.ev_soc,
        ev_strategy=inputs.ev_strategy,
        is_cheap_hour=inputs.is_cheap_hour,
        thresholds=inputs.thresholds,
        shortfall_kwh=solar_shortfall_kwh(inputs),
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
    last_ev_target_w: float | None = None,
    shortfall_kwh: float | None = None,
) -> EvDecision:
    """Fast-path re-computation for the 1 s loop. Same logic as decide()."""
    house_non_ev = max(0.0, house_load_w - ev_power_w)
    return _make_decision(
        pv_w=pv_w,
        house_non_ev=house_non_ev,
        grid_import_w=grid_import_w,
        ev_power_w=ev_power_w,
        last_ev_target_w=last_ev_target_w,
        ev_plugged=ev_plugged,
        ev_soc=ev_soc,
        ev_strategy=ev_strategy,
        is_cheap_hour=is_cheap_hour,
        thresholds=thresholds,
        shortfall_kwh=shortfall_kwh,
    )


# ── core logic ────────────────────────────────────────────────────────────────

def _make_decision(
    *,
    pv_w: float,
    house_non_ev: float,
    grid_import_w: float,
    ev_power_w: float,
    last_ev_target_w: float | None,
    ev_plugged: bool,
    ev_soc: float | None,
    ev_strategy: str,
    is_cheap_hour: bool,
    thresholds: Thresholds,
    shortfall_kwh: float | None,
) -> EvDecision:
    # Hard gates — evaluated in priority order
    if ev_strategy == "Off":
        return _idle("charging_disabled")
    if not ev_plugged:
        return _idle("ev_not_connected")
    if ev_strategy == "Price-optimised" and not is_cheap_hour:
        return _idle("outside_cheap_hours")
    if ev_soc is not None and ev_soc >= thresholds.ev_target_soc:
        return _idle("target_soc_reached")

    if ev_strategy == "Energy-optimised":
        # Binary budget: EV charges only when today's solar forecast has leftover
        # after covering house load and battery target. No grid, no battery drain.
        if shortfall_kwh is not None and shortfall_kwh > 0.0:
            return _idle("battery_short")
        # Forecast OK (or missing — assume fine). EV gets the full live PV surplus.
        # surplus = PV minus non-EV house load; EV's own draw is excluded so the EV
        # can absorb the headroom it creates as it ramps up.
        surplus_w = max(0.0, pv_w - house_non_ev)
        ev_target_w = surplus_w
        # Apply charger cap before hysteresis check so threshold comparison is accurate.
        if thresholds.max_ev_charge_w is not None:
            ev_target_w = min(ev_target_w, thresholds.max_ev_charge_w)
        # Hysteresis: raise start threshold to 110% of minimum to prevent chattering
        # when PV surplus hovers near the minimum charge power.
        was_charging = last_ev_target_w is not None and last_ev_target_w > 0.0
        if not was_charging and ev_target_w < thresholds.ev_min_charge_w * 1.1:
            return _idle("not_enough_power")
        # was_charging case: fall through to common min check (stop = ev_min_charge_w)
    else:
        # Integrating closed-loop: drives total grid import to max_grid_import_w.
        # Uses last commanded target as feedback to eliminate static error from
        # wallbox quantisation or ramp lag. Falls back to ev_power_w on cold start.
        feedback_w = last_ev_target_w if last_ev_target_w is not None else ev_power_w
        ev_target_w = max(0.0, thresholds.max_grid_import_w - grid_import_w + feedback_w)

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


# ── EOD battery fullness helpers ──────────────────────────────────────────────

def solar_shortfall_kwh(inputs: Inputs) -> float | None:
    """Return battery_gap_kwh − effective_solar_kwh.

    Positive = solar forecast falls short of filling battery to target.
    Negative or zero = solar covers the gap (or battery already at/above target).
    None if any required input is missing.
    """
    if (
        inputs.battery_soc is None
        or inputs.battery_capacity_kwh is None
        or inputs.solar_forecast_remaining_kwh is None
    ):
        return None

    battery_gap_kwh = inputs.battery_capacity_kwh * max(
        0.0, (inputs.battery_target_soc - inputs.battery_soc) / 100.0
    )
    house_forecast = inputs.house_load_forecast_remaining_kwh or 0.0
    effective_solar = max(0.0, inputs.solar_forecast_remaining_kwh - house_forecast)
    return battery_gap_kwh - effective_solar


def _cheap_hold_hours(inputs: Inputs) -> list[int] | None:
    """Return the cheapest remaining hours needed to cover the solar deficit, sorted by price.

    Each hour is assumed to save 1 kWh from the battery (conservative approximation).
    Returns None when hold logic doesn't apply (missing data or no deficit).
    """
    if inputs.prices_today is None or len(inputs.prices_today) < 24:
        return None
    shortfall = solar_shortfall_kwh(inputs)
    if shortfall is None or shortfall <= 0.0:
        return None
    current_hour = inputs.now.hour
    remaining = list(range(current_hour, 24))
    sorted_by_price = sorted(remaining, key=lambda h: inputs.prices_today[h])
    return sorted_by_price[: math.ceil(shortfall)]


def is_eod_suppress_hour(inputs: Inputs) -> bool:
    """Return True if this hour should suppress battery discharge for EOD battery target."""
    hours = _cheap_hold_hours(inputs)
    return hours is not None and inputs.now.hour in set(hours)


def cheap_hold_rank(inputs: Inputs) -> tuple[int, int] | None:
    """Return (1-based rank, total) if the current hour is in the cheap-hold set, else None.

    Rank 1 = cheapest hour in the set.
    """
    hours = _cheap_hold_hours(inputs)
    if hours is None or inputs.now.hour not in set(hours):
        return None
    return (hours.index(inputs.now.hour) + 1, len(hours))


def should_advise_ev_charge(inputs: Inputs) -> bool:
    """Return True when EV is not plugged in but it's a good time to charge by price.

    Mode-agnostic: fires regardless of ev_strategy setting.
    """
    if inputs.ev_plugged:
        return False
    if inputs.is_cheap_hour:
        return True
    if inputs.prices_today is None or len(inputs.prices_today) < 24:
        return False
    n = max(1, inputs.thresholds.ev_charge_advised_cheap_hours)
    current_hour = inputs.now.hour
    remaining = list(range(current_hour, 24))
    cheapest = set(sorted(remaining, key=lambda h: inputs.prices_today[h])[:n])
    return current_hour in cheapest


