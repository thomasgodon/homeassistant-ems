from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal


@dataclass(frozen=True, slots=True)
class VerbMapping:
    inverter_select_entity: str | None
    self_use_option: str | None
    battery_control_option: str | None
    battery_discharge_cap_entity: str | None
    ev_charge_target_entity: str | None


@dataclass(frozen=True, slots=True)
class Thresholds:
    max_grid_import_w: float
    ev_target_soc: float
    ev_min_charge_w: float


@dataclass(frozen=True, slots=True)
class Inputs:
    now: datetime
    grid_import_w: float
    pv_w: float
    house_load_w: float
    ev_plugged: bool
    ev_power_w: float
    ev_soc: float | None
    ev_strategy: Literal["Energy-optimised", "Off"]
    thresholds: Thresholds
    mapping: VerbMapping


StrategyLabel = Literal["ev_charging", "ev_idle", "starting_up"]


@dataclass(frozen=True, slots=True)
class Decision:
    strategy: StrategyLabel
    battery_discharge_cap_w: float  # 0 = no battery discharge for house; positive = cap in W
    ev_charge_power_w: float  # watts; 0 = idle


@dataclass(frozen=True, slots=True)
class LadderResult:
    decision: Decision
    inputs: Inputs
    computed_at: datetime
