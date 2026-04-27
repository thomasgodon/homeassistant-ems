from __future__ import annotations

import datetime
from typing import Any

import pytest

from custom_components.ems.models import (
    EvVerb,
    Features,
    Inputs,
    InverterVerb,
    PriceSlot,
    Thresholds,
    VerbMapping,
)

_ALL_INVERTER_OPTIONS: dict[InverterVerb, str] = {
    InverterVerb.SELF_USE: "Self Use",
    InverterVerb.FORCE_CHARGE: "Force Charge",
    InverterVerb.FORCE_DISCHARGE: "Force Discharge",
    InverterVerb.LIMIT_EXPORT: "Limit Export",
    InverterVerb.IDLE: "Idle",
}

_ALL_EV_OPTIONS: dict[EvVerb, str] = {
    EvVerb.CHARGE_NOW: "Charge",
    EvVerb.SOLAR_ONLY: "Solar Only",
    EvVerb.SOLAR_PLUS_BATTERY: "Solar + Battery",
    EvVerb.IDLE: "Idle",
}

_DEFAULT_MAPPING = VerbMapping(
    inverter_select_entity="select.inverter_mode",
    inverter_options=_ALL_INVERTER_OPTIONS,
    inverter_power_target_entity="number.inverter_power",
    ev_select_entity="select.ev_mode",
    ev_options=_ALL_EV_OPTIONS,
    ev_max_power_entity="number.ev_max_power",
)

_DEFAULT_FEATURES = Features(
    peak_shaving=True,
    proactive_grid_charge=True,
    ev_scheduling=True,
    forecast_planning=False,
    export_curtail=True,
    ev_absorbs_curtail=True,
)

_DEFAULT_THRESHOLDS = Thresholds(
    max_grid_import_w=4500.0,
    battery_reserve_soc=10.0,
    battery_target_soc_sunset=80.0,
    battery_capacity_kwh=10.0,
    battery_max_discharge_w=3000.0,
    battery_full_soc=95.0,
    proactive_grid_charge_price=0.05,
    expensive_price_multiplier=1.3,
    export_curtail_price=0.00,
    ev_target_soc=80.0,
    peak_shave_headroom_w=300.0,
    solar_surplus_threshold_w=100.0,
)

_BASE_NOW = datetime.datetime(2026, 4, 27, 12, 0, 0, tzinfo=datetime.UTC)
_BASE_EV_READY_BY = datetime.time(7, 0)


class InputsBuilder:
    """Builder with calm mid-day defaults that produce 'fallback'."""

    def __init__(self) -> None:
        self._overrides: dict[str, Any] = {}

    def build(self, **overrides) -> Inputs:
        merged = {**self._overrides, **overrides}

        def get(key, default):
            return merged.get(key, default)

        return Inputs(
            now=get("now", _BASE_NOW),
            ems_mode=get("ems_mode", "Auto"),
            grid_import_w=get("grid_import_w", 500.0),
            grid_import_15m_avg_w=get("grid_import_15m_avg_w", 1000.0),
            pv_w=get("pv_w", 2000.0),
            house_load_w=get("house_load_w", 1500.0),
            house_load_non_ev_w=get("house_load_non_ev_w", 1500.0),
            battery_soc=get("battery_soc", 60.0),
            battery_power_w=get("battery_power_w", 0.0),
            ev_plugged=get("ev_plugged", False),
            ev_power_w=get("ev_power_w", 0.0),
            ev_soc=get("ev_soc", None),
            ev_capacity_kwh=get("ev_capacity_kwh", 60.0),
            ev_max_charge_kw=get("ev_max_charge_kw", 7.4),
            ev_ready_by=get("ev_ready_by", _BASE_EV_READY_BY),
            ev_strategy=get("ev_strategy", "Off"),
            import_price=get("import_price", 0.20),
            export_price=get("export_price", 0.05),
            avg_24h_price=get("avg_24h_price", 0.20),
            in_cheap_window=get("in_cheap_window", False),
            pv_forecast_remaining_kwh=get("pv_forecast_remaining_kwh", None),
            house_load_forecast_kwh=get("house_load_forecast_kwh", None),
            features=get("features", _DEFAULT_FEATURES),
            thresholds=get("thresholds", _DEFAULT_THRESHOLDS),
            mapping=get("mapping", _DEFAULT_MAPPING),
            peak_risk_latched=get("peak_risk_latched", False),
            is_daytime=get("is_daytime", True),
        )


@pytest.fixture
def builder() -> InputsBuilder:
    return InputsBuilder()


def forecast_factory(
    pattern: str,
    start: datetime.datetime,
    hours: int = 24,
) -> list[PriceSlot]:
    """Generate a list of PriceSlots with the given pattern."""
    patterns = {
        "flat": lambda i: 0.20,
        "night_cheap": lambda i: 0.05 if i < 6 or i >= 22 else 0.25,
        "morning_spike": lambda i: 0.50 if 7 <= i <= 9 else 0.15,
        "holes": lambda i: None,  # handled below
        "ascending": lambda i: 0.05 + i * 0.01,
        "descending": lambda i: 0.30 - i * 0.01,
    }
    price_fn = patterns.get(pattern, patterns["flat"])
    slots = []
    for i in range(hours):
        slot_start = start + datetime.timedelta(hours=i)
        slot_end = slot_start + datetime.timedelta(hours=1)
        price = price_fn(i)
        if price is not None:
            slots.append(PriceSlot(start=slot_start, end=slot_end, price=price))
    return slots
