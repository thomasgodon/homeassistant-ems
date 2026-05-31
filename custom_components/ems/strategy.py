from __future__ import annotations

import math
import statistics
from datetime import datetime

from .models import (
    BatteryFocus,
    EvChargingState,
    EvDecision,
    GridFocus,
    Inputs,
    InverterDecision,
    PvDestinations,
    PvFocus,
    Thresholds,
)


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
        # Anti-windup: cap feedback to ev_power_w + headroom so the integrator
        # cannot race ahead of the wallbox ramp, preventing grid overshoot that
        # would otherwise drive the target below ev_min_charge_w and stop the session.
        if last_ev_target_w is not None:
            feedback_w = min(last_ev_target_w, ev_power_w + _ANTI_WINDUP_HEADROOM_W)
        else:
            feedback_w = ev_power_w
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

def battery_gap_kwh(inputs: Inputs) -> float | None:
    """kWh to lift battery from current SoC to target_soc. None if SoC or capacity unknown."""
    if inputs.battery_soc is None or inputs.battery_capacity_kwh is None:
        return None
    gap_fraction = max(0.0, (inputs.battery_target_soc - inputs.battery_soc) / 100.0)
    return inputs.battery_capacity_kwh * gap_fraction


def effective_solar_remaining_kwh(inputs: Inputs) -> float | None:
    """Solar forecast minus house load forecast, clamped >=0. None if solar forecast unknown."""
    if inputs.solar_forecast_remaining_kwh is None:
        return None
    house = inputs.house_load_forecast_remaining_kwh or 0.0
    return max(0.0, inputs.solar_forecast_remaining_kwh - house)


def solar_shortfall_kwh(inputs: Inputs) -> float | None:
    """Return battery_gap_kwh − effective_solar_kwh.

    Positive = solar forecast falls short of filling battery to target.
    Negative or zero = solar covers the gap (or battery already at/above target).
    None if any required input is missing.
    """
    gap = battery_gap_kwh(inputs)
    effective = effective_solar_remaining_kwh(inputs)
    if gap is None or effective is None:
        return None
    return gap - effective


def _cheap_hold_hours(inputs: Inputs) -> list[int] | None:
    """Return the cheapest remaining hours needed to cover the solar deficit, sorted by price.

    Only hours strictly below today's median price are eligible — if we are already
    past the cheap window, the shortfall is accepted and the battery is allowed to
    discharge rather than pulling expensive grid power.

    Each hour is assumed to save 1 kWh from the battery (conservative approximation).
    Returns None when hold logic doesn't apply (missing data, no deficit, or no
    remaining hour is below the day's median price).
    """
    if inputs.prices_today is None or len(inputs.prices_today) < 24:
        return None
    shortfall = solar_shortfall_kwh(inputs)
    if shortfall is None or shortfall <= 0.0:
        return None
    median_price = statistics.median(inputs.prices_today)
    current_hour = inputs.now.hour
    candidates = [
        h for h in range(current_hour, 24)
        if inputs.prices_today[h] < median_price
    ]
    if not candidates:
        return None
    sorted_by_price = sorted(candidates, key=lambda h: inputs.prices_today[h])
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


# ── status-surface helpers ─────────────────────────────────────────────────────

_ANTI_WINDUP_HEADROOM_W = 500.0

_BATTERY_POWER_DEADBAND_W = 200.0
_PV_DEADBAND_W = 100.0
_GRID_DEADBAND_W = 100.0
_PV_DOMINANT_FRACTION = 0.70
_PEAK_CAP_PROXIMITY_W = 200.0


def expected_eod_soc(inputs: Inputs) -> float | None:
    """Return the projected battery SoC (%) at end of day given the solar forecast.

    Uses the same formula and None semantics as solar_shortfall_kwh().
    Returns None if any required input is missing.
    """
    if inputs.battery_soc is None or inputs.battery_capacity_kwh is None:
        return None
    effective = effective_solar_remaining_kwh(inputs)
    if effective is None:
        return None
    delta_soc = (effective / inputs.battery_capacity_kwh) * 100.0
    return min(100.0, inputs.battery_soc + delta_soc)


def battery_focus(inputs: Inputs, inverter_decision: InverterDecision) -> BatteryFocus:
    """Return what the battery is focused on right now.

    Priority order: inverter intent overrides raw power balance.
    """
    battery_w = inputs.pv_w + inputs.grid_import_w - inputs.house_load_w

    if inverter_decision.status == "starting_up":
        return "starting_up"
    if inputs.battery_soc is None:
        return "unavailable"
    if inverter_decision.status == "peak_shaving":
        return "peak_shaving"
    if inverter_decision.status == "holding_battery":
        return "holding_for_evening"
    if inverter_decision.status == "battery_priority":
        return "prioritising_battery"
    if (
        inputs.battery_soc >= inputs.battery_target_soc
        and abs(battery_w) <= _BATTERY_POWER_DEADBAND_W
    ):
        return "full"
    if battery_w > _BATTERY_POWER_DEADBAND_W:
        return "charging_from_solar"
    if battery_w < -_BATTERY_POWER_DEADBAND_W:
        return "discharging_to_house"
    return "idle"


def pv_destinations(inputs: Inputs, inverter_decision: InverterDecision) -> PvDestinations:
    """Allocate PV generation across house, EV, battery, and grid export.

    Conservation invariant: to_house + to_ev + to_battery + to_grid == max(0, pv_w).
    Battery flow is inferred (no direct battery sensor):
    battery_w = pv_w + grid_import_w - house_load_w.
    Priority order depends on inverter intent:
    - battery_priority: house_non_ev first, then battery, then EV (EV is cut off from PV).
    - all other modes: whole-house first, then battery, then remainder.
    """
    available = max(0.0, inputs.pv_w)
    if available == 0.0:
        return PvDestinations(
            pv_w=inputs.pv_w, to_house_w=0.0, to_ev_w=0.0, to_battery_w=0.0, to_grid_w=0.0
        )

    battery_charging_w = max(0.0, inputs.pv_w + inputs.grid_import_w - inputs.house_load_w)
    house_non_ev = max(0.0, inputs.house_load_w - inputs.ev_power_w)
    remaining = available
    to_house = to_ev = to_battery = 0.0

    if inverter_decision.status == "battery_priority":
        # PV reserved for house (non-EV) and battery; EV is fed from grid.
        take = min(remaining, house_non_ev)
        to_house = take
        remaining -= take
        take = min(remaining, battery_charging_w)
        to_battery = take
        remaining -= take
        # to_ev stays 0 — PV is explicitly withheld from the EV.
    else:
        # Default: whole-house load (including EV) is covered first.
        take = min(remaining, inputs.house_load_w)
        to_house = take
        remaining -= take
        take = min(remaining, battery_charging_w)
        to_battery = take
        remaining -= take
        # Split the house bucket proportionally to expose the EV share.
        if to_house > 0.0 and inputs.house_load_w > 0.0:
            ev_share = to_house * (inputs.ev_power_w / inputs.house_load_w)
            to_ev = ev_share
            to_house -= ev_share

    to_grid = remaining  # exported PV
    return PvDestinations(
        pv_w=inputs.pv_w,
        to_house_w=to_house,
        to_ev_w=to_ev,
        to_battery_w=to_battery,
        to_grid_w=to_grid,
    )


def pv_focus(destinations: PvDestinations, inverter_decision: InverterDecision) -> PvFocus:
    """Return the dominant PV destination, or 'mixed' when no single destination dominates."""
    if inverter_decision.status == "starting_up":
        return "starting_up"
    if destinations.pv_w < _PV_DEADBAND_W:
        return "no_production"
    buckets: dict[PvFocus, float] = {
        "to_battery": destinations.to_battery_w,
        "to_ev": destinations.to_ev_w,
        "to_house": destinations.to_house_w,
        "exporting": destinations.to_grid_w,
    }
    top_name = max(buckets, key=lambda k: buckets[k])
    if buckets[top_name] / destinations.pv_w >= _PV_DOMINANT_FRACTION:
        return top_name
    return "mixed"


def grid_focus(inputs: Inputs, inverter_decision: InverterDecision) -> GridFocus:
    """Return what the grid connection is doing right now."""
    if inverter_decision.status == "starting_up":
        return "starting_up"
    if inverter_decision.status == "peak_shaving":
        return "peak_shaving_active"
    g = inputs.grid_import_w
    cap = inputs.thresholds.max_grid_import_w
    if g < -_GRID_DEADBAND_W:
        return "exporting"
    if abs(g) <= _GRID_DEADBAND_W:
        return "balanced"
    if abs(g - cap) <= _PEAK_CAP_PROXIMITY_W:
        return "at_peak_cap"
    return "importing"


# ── solar forecast helpers ─────────────────────────────────────────────────────

def solar_remaining_wh(forecast: tuple[float, ...] | None, now: datetime) -> float | None:
    """Wh of solar forecast remaining today.

    Pro-rates the current hour by minutes left; sums future hours verbatim.
    Returns None when forecast is None or not exactly 24 elements.
    """
    if forecast is None or len(forecast) != 24:
        return None
    h = now.hour
    minutes_left = 60 - now.minute
    total = forecast[h] * (minutes_left / 60.0)
    total += sum(forecast[h + 1:])
    return total


def solar_seconds_until_zero(forecast: tuple[float, ...] | None, now: datetime) -> int | None:
    """Seconds from *now* until the end of the last hour with forecast > 0.

    Returns 0 when already past the last productive hour.
    Returns None when forecast is None or not exactly 24 elements.
    """
    if forecast is None or len(forecast) != 24:
        return None
    last_hour = -1
    for h in range(23, -1, -1):
        if forecast[h] > 0:
            last_hour = h
            break
    if last_hour < 0:
        return 0
    end_second = (last_hour + 1) * 3600
    now_second = now.hour * 3600 + now.minute * 60 + now.second
    return max(0, end_second - now_second)


