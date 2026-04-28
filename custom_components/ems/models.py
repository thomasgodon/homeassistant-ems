from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time
from enum import StrEnum
from typing import Literal


class InverterVerb(StrEnum):
    SELF_USE = "SELF_USE"


@dataclass(frozen=True, slots=True)
class VerbMapping:
    inverter_select_entity: str | None
    self_use_option: str | None  # option string for the SELF_USE mode
    inverter_power_target_entity: str | None  # number entity, watts, non-negative


@dataclass(frozen=True, slots=True)
class Features:
    ev_charging: bool


@dataclass(frozen=True, slots=True)
class Thresholds:
    max_grid_import_w: float
    ev_target_soc: float
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
    ev_plugged: bool
    ev_power_w: float
    ev_soc: float | None
    ev_capacity_kwh: float
    ev_max_charge_kw: float
    ev_ready_by: time
    ev_strategy: Literal["Deadline", "Price-optimised", "Solar-only", "Off"]
    import_price: float | None
    in_cheap_window: bool  # set by ev_scheduler; coordinator threads it in
    features: Features
    thresholds: Thresholds
    mapping: VerbMapping


StrategyLabel = Literal[
    "ev_charging_grid",
    "ev_charging_solar",
    "ev_idle",
    "starting_up",
]


@dataclass(frozen=True, slots=True)
class Decision:
    strategy: StrategyLabel
    inverter_verb: InverterVerb | None
    inverter_power_target_w: float | None  # non-negative; None = don't write
    ev_charge_power_w: float  # watts; 0 = idle


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
