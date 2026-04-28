from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, time
from enum import StrEnum
from typing import Literal


class InverterVerb(StrEnum):
    SELF_USE = "SELF_USE"
    FORCE_CHARGE = "FORCE_CHARGE"
    FORCE_DISCHARGE = "FORCE_DISCHARGE"
    LIMIT_EXPORT = "LIMIT_EXPORT"
    IDLE = "IDLE"


@dataclass(frozen=True, slots=True)
class VerbMapping:
    inverter_select_entity: str | None
    inverter_options: Mapping[InverterVerb, str | None]  # None = unsupported
    inverter_power_target_entity: str | None  # number entity, watts, non-negative


@dataclass(frozen=True, slots=True)
class Features:
    proactive_grid_charge: bool
    ev_charging: bool
    forecast_planning: bool


@dataclass(frozen=True, slots=True)
class Thresholds:
    max_grid_import_w: float
    battery_reserve_soc: float
    battery_target_soc_sunset: float
    battery_capacity_kwh: float
    battery_max_discharge_w: float
    battery_full_soc: float
    proactive_grid_charge_price: float
    expensive_price_multiplier: float
    export_curtail_price: float
    ev_target_soc: float
    peak_shave_headroom_w: float
    solar_surplus_threshold_w: float
    ev_min_charge_w: float


@dataclass(frozen=True, slots=True)
class PriceSlot:
    start: datetime
    end: datetime
    price: float


@dataclass(frozen=True, slots=True)
class Inputs:
    now: datetime
    grid_import_w: float
    grid_import_15m_avg_w: float | None
    pv_w: float
    house_load_w: float
    house_load_non_ev_w: float | None  # caller computes fallback (house_load - ev_power)
    battery_soc: float
    battery_power_w: float  # signed: + charge / − discharge
    ev_plugged: bool
    ev_power_w: float
    ev_soc: float | None
    ev_capacity_kwh: float
    ev_max_charge_kw: float
    ev_ready_by: time
    ev_strategy: Literal["Deadline", "Price-optimised", "Solar-only", "Off"]
    import_price: float | None
    export_price: float | None
    avg_24h_price: float | None
    in_cheap_window: bool  # ev_scheduler already decided; coordinator threads it in
    pv_forecast_remaining_kwh: float | None
    house_load_forecast_kwh: float | None
    features: Features
    thresholds: Thresholds
    mapping: VerbMapping
    peak_risk_latched: bool  # hysteresis state in — coordinator threads it in
    is_daytime: bool  # caller resolves via sun.sun


StrategyLabel = Literal[
    "peak_shave",
    "battery_floor",
    "ev_charging_coupling",
    "export_curtail",
    "proactive_charge",
    "solar_to_battery",
    "expensive_self_use",
    "fallback",
    "starting_up",
]


@dataclass(frozen=True, slots=True)
class Decision:
    strategy: StrategyLabel
    inverter_verb: InverterVerb | None
    inverter_power_target_w: float | None  # non-negative; None = leave unchanged
    ev_charge_power_w: float  # watts; 0 = idle
    new_peak_risk: bool  # hysteresis latch output
    rule_chain: tuple[str, ...]  # diagnostic trace
    unsupported_verbs: tuple[str, ...]  # mode_unsupported_<verb> labels


@dataclass(frozen=True, slots=True)
class EvWindow:
    start: datetime | None
    end: datetime | None
    in_window_now: bool
    reason: Literal["price", "deadline", "solar", "no_need", "stale_forecast", "off"]


@dataclass(frozen=True, slots=True)
class LadderResult:
    decision: Decision
    inputs: Inputs
    computed_at: datetime
