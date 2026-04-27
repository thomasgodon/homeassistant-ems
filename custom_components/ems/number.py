from __future__ import annotations

from dataclasses import dataclass

from homeassistant.components.number import NumberEntity, NumberEntityDescription, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfPower, PERCENTAGE, UnitOfEnergy, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .const import DOMAIN


@dataclass(frozen=True)
class EmsNumberDescription(NumberEntityDescription):
    default_value: float = 0.0
    native_min_value: float = 0.0
    native_max_value: float = 100.0
    native_step: float = 1.0


_NUMBERS: tuple[EmsNumberDescription, ...] = (
    EmsNumberDescription(
        key="ems_max_grid_import_w",
        name="EMS Max Grid Import",
        native_unit_of_measurement=UnitOfPower.WATT,
        native_min_value=0,
        native_max_value=25000,
        native_step=100,
        default_value=4500,
        icon="mdi:transmission-tower-import",
    ),
    EmsNumberDescription(
        key="ems_battery_reserve_soc",
        name="EMS Battery Reserve SoC",
        native_unit_of_measurement=PERCENTAGE,
        native_min_value=0,
        native_max_value=100,
        native_step=1,
        default_value=10,
        icon="mdi:battery-low",
    ),
    EmsNumberDescription(
        key="ems_battery_target_soc_sunset",
        name="EMS Battery Target SoC at Sunset",
        native_unit_of_measurement=PERCENTAGE,
        native_min_value=0,
        native_max_value=100,
        native_step=1,
        default_value=80,
        icon="mdi:weather-sunset",
    ),
    EmsNumberDescription(
        key="ems_battery_capacity_kwh",
        name="EMS Battery Capacity",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        native_min_value=0.5,
        native_max_value=200,
        native_step=0.5,
        default_value=10,
        icon="mdi:battery",
    ),
    EmsNumberDescription(
        key="ems_battery_max_discharge_w",
        name="EMS Battery Max Discharge Power",
        native_unit_of_measurement=UnitOfPower.WATT,
        native_min_value=0,
        native_max_value=25000,
        native_step=100,
        default_value=3000,
        icon="mdi:battery-arrow-down",
    ),
    EmsNumberDescription(
        key="ems_battery_full_soc",
        name="EMS Battery Full SoC",
        native_unit_of_measurement=PERCENTAGE,
        native_min_value=50,
        native_max_value=100,
        native_step=1,
        default_value=95,
        icon="mdi:battery-high",
    ),
    EmsNumberDescription(
        key="ems_proactive_grid_charge_price",
        name="EMS Proactive Grid Charge Price Threshold",
        native_unit_of_measurement="EUR/kWh",
        native_min_value=0,
        native_max_value=1,
        native_step=0.001,
        default_value=0.05,
        icon="mdi:currency-eur",
    ),
    EmsNumberDescription(
        key="ems_expensive_price_multiplier",
        name="EMS Expensive Price Multiplier",
        native_unit_of_measurement="×",
        native_min_value=1,
        native_max_value=5,
        native_step=0.05,
        default_value=1.3,
        icon="mdi:trending-up",
    ),
    EmsNumberDescription(
        key="ems_export_curtail_price",
        name="EMS Export Curtail Price Threshold",
        native_unit_of_measurement="EUR/kWh",
        native_min_value=-1,
        native_max_value=1,
        native_step=0.001,
        default_value=0.0,
        icon="mdi:solar-power",
    ),
    EmsNumberDescription(
        key="ems_ev_target_soc",
        name="EMS EV Target SoC",
        native_unit_of_measurement=PERCENTAGE,
        native_min_value=0,
        native_max_value=100,
        native_step=1,
        default_value=80,
        icon="mdi:car-electric",
    ),
    EmsNumberDescription(
        key="ems_peak_shave_headroom_w",
        name="EMS Peak Shave Headroom",
        native_unit_of_measurement=UnitOfPower.WATT,
        native_min_value=0,
        native_max_value=2000,
        native_step=50,
        default_value=300,
        icon="mdi:gauge",
    ),
    EmsNumberDescription(
        key="ems_solar_surplus_threshold_w",
        name="EMS Solar Surplus Threshold",
        native_unit_of_measurement=UnitOfPower.WATT,
        native_min_value=0,
        native_max_value=5000,
        native_step=50,
        default_value=100,
        icon="mdi:solar-power",
    ),
    EmsNumberDescription(
        key="ems_ev_max_power_w",
        name="EMS EV Max Power",
        native_unit_of_measurement=UnitOfPower.WATT,
        native_min_value=0,
        native_max_value=22000,
        native_step=100,
        default_value=7400,
        icon="mdi:ev-station",
    ),
    EmsNumberDescription(
        key="ems_strategy_interval_s",
        name="EMS Strategy Interval",
        native_unit_of_measurement=UnitOfTime.SECONDS,
        native_min_value=1,
        native_max_value=60,
        native_step=1,
        default_value=10,
        icon="mdi:timer",
    ),
    EmsNumberDescription(
        key="ems_peak_shave_interval_s",
        name="EMS Peak Shave Interval",
        native_unit_of_measurement=UnitOfTime.SECONDS,
        native_min_value=0.5,
        native_max_value=5,
        native_step=0.5,
        default_value=1,
        icon="mdi:timer",
    ),
    EmsNumberDescription(
        key="ems_startup_grace_seconds",
        name="EMS Startup Grace Period",
        native_unit_of_measurement=UnitOfTime.SECONDS,
        native_min_value=10,
        native_max_value=300,
        native_step=5,
        default_value=45,
        icon="mdi:timer-sand",
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    async_add_entities([EmsNumber(desc) for desc in _NUMBERS])


class EmsNumber(NumberEntity, RestoreEntity):
    _attr_mode = NumberMode.BOX

    def __init__(self, description: EmsNumberDescription) -> None:
        self.entity_description = description
        self._attr_unique_id = description.key
        self._attr_native_value = description.default_value

    @property
    def device_info(self):
        return {"identifiers": {(DOMAIN, "ems")}, "name": "EMS", "manufacturer": "EMS"}

    async def async_added_to_hass(self) -> None:
        if (last := await self.async_get_last_state()) is not None:
            try:
                self._attr_native_value = float(last.state)
            except (ValueError, TypeError):
                pass

    async def async_set_native_value(self, value: float) -> None:
        self._attr_native_value = value
        self.async_write_ha_state()
