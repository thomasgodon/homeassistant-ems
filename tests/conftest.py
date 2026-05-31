from __future__ import annotations

import datetime
from typing import Any

import pytest

from custom_components.ems.models import (
    Inputs,
    Thresholds,
    VerbMapping,
)

_DEFAULT_MAPPING = VerbMapping(
    inverter_select_entity="select.inverter_mode",
    disabled_option="disabled",
    power_control_option="power_control",
    active_power_entity="number.battery_cap",
    ev_charge_target_entity=None,
)

_DEFAULT_THRESHOLDS = Thresholds(
    max_grid_import_w=4500.0,
    ev_target_soc=80.0,
    ev_min_charge_w=1400.0,
)

_BASE_NOW = datetime.datetime(2026, 4, 27, 12, 0, 0, tzinfo=datetime.UTC)


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
            pv_w=get("pv_w", 500.0),
            house_load_w=get("house_load_w", 1500.0),
            ev_plugged=get("ev_plugged", False),
            ev_power_w=get("ev_power_w", 0.0),
            ev_soc=get("ev_soc", None),
            ev_strategy=get("ev_strategy", "Off"),
            is_cheap_hour=get("is_cheap_hour", True),
            inverter_strategy=get("inverter_strategy", "Off"),
            thresholds=get("thresholds", _DEFAULT_THRESHOLDS),
            mapping=get("mapping", _DEFAULT_MAPPING),
            last_ev_target_w=get("last_ev_target_w", None),
            battery_soc=get("battery_soc", None),
            battery_capacity_kwh=get("battery_capacity_kwh", None),
            battery_target_soc=get("battery_target_soc", 80.0),
            solar_forecast_remaining_kwh=get("solar_forecast_remaining_kwh", None),
            house_load_forecast_remaining_kwh=get("house_load_forecast_remaining_kwh", None),
            prices_today=get("prices_today", None),
            solar_forecast_today=get("solar_forecast_today", None),
        )


@pytest.fixture
def builder() -> InputsBuilder:
    return InputsBuilder()
