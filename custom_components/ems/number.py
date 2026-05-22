from __future__ import annotations

from dataclasses import dataclass

from homeassistant.components.number import NumberEntity, NumberEntityDescription, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE, UnitOfPower
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .const import DEFAULT_EV_CHARGE_ADVISED_CHEAP_HOURS, DOMAIN


@dataclass(frozen=True)
class EmsNumberDescription(NumberEntityDescription):
    default_value: float = 0.0
    native_min_value: float = 0.0
    native_max_value: float = 100.0
    native_step: float = 1.0


_NUMBERS: tuple[EmsNumberDescription, ...] = (
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
        key="ems_battery_target_soc",
        name="EMS Battery Target SoC",
        native_unit_of_measurement=PERCENTAGE,
        native_min_value=0,
        native_max_value=100,
        native_step=1,
        default_value=80,
        icon="mdi:battery-charging-80",
    ),
    EmsNumberDescription(
        key="ems_ev_charge_advised_cheap_hours",
        name="EMS EV Charge Advised Cheap Hours",
        native_unit_of_measurement="h",
        native_min_value=1,
        native_max_value=12,
        native_step=1,
        default_value=DEFAULT_EV_CHARGE_ADVISED_CHEAP_HOURS,
        icon="mdi:clock-outline",
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
