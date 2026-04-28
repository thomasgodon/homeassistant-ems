from __future__ import annotations

from dataclasses import dataclass

from homeassistant.components.number import NumberEntity, NumberEntityDescription, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE, UnitOfPower, UnitOfTime
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
        key="ems_ev_min_charge_w",
        name="EMS EV Minimum Charge Power",
        native_unit_of_measurement=UnitOfPower.WATT,
        native_min_value=0,
        native_max_value=7000,
        native_step=50,
        default_value=1400,
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
        key="ems_fast_loop_interval_s",
        name="EMS Fast Loop Interval",
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
