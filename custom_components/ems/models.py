from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal


@dataclass(frozen=True, slots=True)
class VerbMapping:
    inverter_select_entity: str | None
    disabled_option: str | None
    power_control_option: str | None
    active_power_entity: str | None
    ev_charge_target_entity: str | None


@dataclass(frozen=True, slots=True)
class Thresholds:
    max_grid_import_w: float
    ev_target_soc: float
    ev_min_charge_w: float
    max_ev_charge_w: float | None = None


@dataclass(frozen=True, slots=True)
class Inputs:
    now: datetime
    grid_import_w: float
    pv_w: float
    house_load_w: float
    ev_plugged: bool
    ev_power_w: float
    ev_soc: float | None
    ev_strategy: Literal["Grid-optimised", "Price-optimised", "Off"]
    is_cheap_hour: bool
    thresholds: Thresholds
    mapping: VerbMapping


StrategyLabel = Literal["ev_charging", "ev_idle"]

EvChargingState = Literal[
    "charging",
    "ev_not_connected",
    "charging_disabled",
    "target_soc_reached",
    "not_enough_power",
    "starting_up",
    "outside_cheap_hours",
]


@dataclass(frozen=True, slots=True)
class Decision:
    strategy: StrategyLabel
    active_power_w: float  # negative = drain from inverter; positive = PV surplus into inverter
    ev_charge_power_w: float  # watts; 0 = idle
    charging_state: EvChargingState


@dataclass(frozen=True, slots=True)
class LadderResult:
    decision: Decision
    inputs: Inputs
    computed_at: datetime
