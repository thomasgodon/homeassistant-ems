from __future__ import annotations

import datetime
from typing import Any

import pytest

from custom_components.ems.models import (
    Features,
    Inputs,
    PriceSlot,
    Thresholds,
    VerbMapping,
)

_DEFAULT_MAPPING = VerbMapping(
    inverter_select_entity="select.inverter_mode",
    self_use_option="Self Use",
    inverter_power_target_entity="number.inverter_power",
)

_DEFAULT_FEATURES = Features(
    ev_charging=True,
)

_DEFAULT_THRESHOLDS = Thresholds(
    max_grid_import_w=4500.0,
    ev_target_soc=80.0,
    ev_min_charge_w=1400.0,
)

_BASE_NOW = datetime.datetime(2026, 4, 27, 12, 0, 0, tzinfo=datetime.UTC)
_BASE_EV_READY_BY = datetime.time(7, 0)


class InputsBuilder:
    """Builder with calm mid-day defaults that produce 'ev_idle'."""

    def __init__(self) -> None:
        self._overrides: dict[str, Any] = {}

    def build(self, **overrides) -> Inputs:
        merged = {**self._overrides, **overrides}

        def get(key, default):
            return merged.get(key, default)

        return Inputs(
            now=get("now", _BASE_NOW),
            grid_import_w=get("grid_import_w", 500.0),
            grid_import_15m_avg_w=get("grid_import_15m_avg_w", 1000.0),
            pv_w=get("pv_w", 500.0),
            house_load_w=get("house_load_w", 1500.0),
            ev_plugged=get("ev_plugged", False),
            ev_power_w=get("ev_power_w", 0.0),
            ev_soc=get("ev_soc", None),
            ev_capacity_kwh=get("ev_capacity_kwh", 60.0),
            ev_max_charge_kw=get("ev_max_charge_kw", 7.4),
            ev_ready_by=get("ev_ready_by", _BASE_EV_READY_BY),
            ev_strategy=get("ev_strategy", "Off"),
            import_price=get("import_price", 0.20),
            in_cheap_window=get("in_cheap_window", False),
            features=get("features", _DEFAULT_FEATURES),
            thresholds=get("thresholds", _DEFAULT_THRESHOLDS),
            mapping=get("mapping", _DEFAULT_MAPPING),
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
