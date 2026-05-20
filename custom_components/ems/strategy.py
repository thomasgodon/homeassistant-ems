from __future__ import annotations

import math

from .models import EvChargingState, EvDecision, Inputs, Thresholds


def decide(inputs: Inputs) -> EvDecision:
    """Compute EV charge target. Never raises."""
    house_non_ev = max(0.0, inputs.house_load_w - inputs.ev_power_w)
    return _make_decision(
        house_non_ev=house_non_ev,
        grid_import_w=inputs.grid_import_w,
        ev_power_w=inputs.ev_power_w,
        last_ev_target_w=inputs.last_ev_target_w,
        ev_plugged=inputs.ev_plugged,
        ev_soc=inputs.ev_soc,
        ev_strategy=inputs.ev_strategy,
        is_cheap_hour=inputs.is_cheap_hour,
        thresholds=inputs.thresholds,
        energy_optimised_allowed=_energy_optimised_allows_ev(inputs),
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
    energy_optimised_allowed: bool = False,
) -> EvDecision:
    """Fast-path re-computation for the 1 s loop. Same logic as decide()."""
    house_non_ev = max(0.0, house_load_w - ev_power_w)
    return _make_decision(
        house_non_ev=house_non_ev,
        grid_import_w=grid_import_w,
        ev_power_w=ev_power_w,
        last_ev_target_w=last_ev_target_w,
        ev_plugged=ev_plugged,
        ev_soc=ev_soc,
        ev_strategy=ev_strategy,
        is_cheap_hour=is_cheap_hour,
        thresholds=thresholds,
        energy_optimised_allowed=energy_optimised_allowed,
    )


# ── core logic ────────────────────────────────────────────────────────────────

def _make_decision(
    *,
    house_non_ev: float,
    grid_import_w: float,
    ev_power_w: float,
    last_ev_target_w: float | None,
    ev_plugged: bool,
    ev_soc: float | None,
    ev_strategy: str,
    is_cheap_hour: bool,
    thresholds: Thresholds,
    energy_optimised_allowed: bool,
) -> EvDecision:
    # Hard gates — evaluated in priority order
    if ev_strategy == "Off":
        return _idle("charging_disabled")
    if not ev_plugged:
        return _idle("ev_not_connected")
    if ev_strategy == "Price-optimised" and not is_cheap_hour:
        return _idle("outside_cheap_hours")
    if ev_strategy == "Energy-optimised" and not energy_optimised_allowed:
        return _idle("awaiting_solar_surplus")
    if ev_soc is not None and ev_soc >= thresholds.ev_target_soc:
        return _idle("target_soc_reached")

    # Integrating closed-loop: use last commanded target as feedback so the
    # integrator drives total grid import to the target regardless of
    # wallbox quantisation or ramp lag. Falls back to ev_power_w on cold start.
    feedback_w = last_ev_target_w if last_ev_target_w is not None else ev_power_w

    # Energy-optimised drives toward zero grid import (absorbs solar export only).
    # Grid-optimised and Price-optimised drive toward max_grid_import_w.
    grid_target_w = 0.0 if ev_strategy == "Energy-optimised" else thresholds.max_grid_import_w
    ev_target_w = max(0.0, grid_target_w - grid_import_w + feedback_w)

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
    effective_solar = inputs.solar_forecast_remaining_kwh - house_forecast
    return battery_gap_kwh - effective_solar


def is_eod_suppress_hour(inputs: Inputs) -> bool:
    """Return True if this hour should suppress battery discharge for EOD battery target.

    Selects the cheapest remaining daylight hours needed to cover the forecast deficit,
    each hour counted as saving 1 kWh from the battery (conservative approximation).
    """
    if (
        inputs.prices_today is None
        or len(inputs.prices_today) < 24
    ):
        return False

    shortfall = solar_shortfall_kwh(inputs)
    if shortfall is None or shortfall <= 0.0:
        return False

    current_hour = inputs.now.hour

    # Rank remaining hours (current hour inclusive) by price, pick cheapest N
    # where N = number of hours needed to cover deficit (1 kWh assumed per hour).
    remaining = [h for h in range(current_hour, 24)]
    sorted_by_price = sorted(remaining, key=lambda h: inputs.prices_today[h])
    n_hours = math.ceil(shortfall)
    cheap_hours = set(sorted_by_price[:n_hours])

    return current_hour in cheap_hours


def _energy_optimised_allows_ev(inputs: Inputs) -> bool:
    """Allow EV charging in Energy-optimised mode only when forecast surplus exceeds battery gap."""
    shortfall = solar_shortfall_kwh(inputs)
    if shortfall is None:
        return False
    return shortfall < 0.0
