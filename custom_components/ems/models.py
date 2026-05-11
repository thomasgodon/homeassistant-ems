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
    ev_strategy: Literal["Grid-optimised", "Price-optimised", "Energy-optimised", "Off"]
    is_cheap_hour: bool
    inverter_strategy: Literal["Control Inverter", "Off"]
    thresholds: Thresholds
    mapping: VerbMapping
    last_ev_target_w: float | None = None
    battery_soc: float | None = None
    battery_capacity_kwh: float | None = None
    battery_target_soc: float = 80.0
    solar_forecast_remaining_kwh: float | None = None
    house_load_forecast_remaining_kwh: float | None = None
    prices_today: tuple[float, ...] | None = None


StrategyLabel = Literal["ev_charging", "ev_idle"]

InverterStatus = Literal[
    "controlling", "idle", "disabled", "starting_up", "peak_shaving", "holding_battery"
]

EvChargingState = Literal[
    "charging",
    "ev_not_connected",
    "charging_disabled",
    "target_soc_reached",
    "not_enough_power",
    "starting_up",
    "outside_cheap_hours",
    "awaiting_solar_surplus",
]


@dataclass(frozen=True, slots=True)
class EvDecision:
    strategy: StrategyLabel
    ev_charge_power_w: float  # watts; 0 = idle
    charging_state: EvChargingState


@dataclass(frozen=True, slots=True)
class InverterDecision:
    inverter_verb: str | None  # None = do not write inverter select
    active_power_w: float | None  # None = do not write active power
    status: InverterStatus = "idle"


@dataclass(frozen=True, slots=True)
class LadderResult:
    ev_decision: EvDecision
    inverter_decision: InverterDecision
    inputs: Inputs
    computed_at: datetime
