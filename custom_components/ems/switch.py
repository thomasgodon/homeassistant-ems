from __future__ import annotations

from dataclasses import dataclass

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_ON
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .const import (
    CONF_EV_ABSORBS_CURTAIL,
    CONF_ENABLE_EV_SCHEDULING,
    CONF_ENABLE_EXPORT_CURTAIL,
    CONF_ENABLE_FORECAST_PLANNING,
    CONF_ENABLE_PEAK_SHAVING,
    CONF_ENABLE_PROACTIVE_CHARGE,
    DOMAIN,
)


@dataclass(frozen=True)
class EmsSwitchDescription:
    unique_id: str
    name: str
    icon: str
    legacy_conf_key: str
    default_on: bool


_SWITCHES: tuple[EmsSwitchDescription, ...] = (
    EmsSwitchDescription(
        unique_id="ems_peak_shaving",
        name="EMS Peak Shaving",
        icon="mdi:shield-bolt",
        legacy_conf_key=CONF_ENABLE_PEAK_SHAVING,
        default_on=True,
    ),
    EmsSwitchDescription(
        unique_id="ems_battery_grid_charging",
        name="EMS Battery Grid Charging",
        icon="mdi:battery-charging",
        legacy_conf_key=CONF_ENABLE_PROACTIVE_CHARGE,
        default_on=True,
    ),
    EmsSwitchDescription(
        unique_id="ems_ev_charging",
        name="EMS EV Charging",
        icon="mdi:car-electric",
        legacy_conf_key=CONF_ENABLE_EV_SCHEDULING,
        default_on=True,
    ),
    EmsSwitchDescription(
        unique_id="ems_export_curtailment",
        name="EMS Export Curtailment",
        icon="mdi:transmission-tower-export",
        legacy_conf_key=CONF_ENABLE_EXPORT_CURTAIL,
        default_on=False,
    ),
    EmsSwitchDescription(
        unique_id="ems_ev_absorbs_curtailment",
        name="EMS EV Absorbs Curtailment",
        icon="mdi:car-electric-outline",
        legacy_conf_key=CONF_EV_ABSORBS_CURTAIL,
        default_on=True,
    ),
    EmsSwitchDescription(
        unique_id="ems_forecast_planning",
        name="EMS Forecast Planning",
        icon="mdi:chart-timeline-variant",
        legacy_conf_key=CONF_ENABLE_FORECAST_PLANNING,
        default_on=False,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    async_add_entities([EmsFeatureSwitch(entry, desc) for desc in _SWITCHES])


class EmsFeatureSwitch(SwitchEntity, RestoreEntity):

    def __init__(self, entry: ConfigEntry, desc: EmsSwitchDescription) -> None:
        self._entry = entry
        self._desc = desc
        self._attr_unique_id = desc.unique_id
        self._attr_name = desc.name
        self._attr_icon = desc.icon
        self._attr_is_on = desc.default_on

    @property
    def device_info(self):
        return {"identifiers": {(DOMAIN, "ems")}, "name": "EMS", "manufacturer": "EMS"}

    async def async_added_to_hass(self) -> None:
        last = await self.async_get_last_state()
        if last is not None:
            self._attr_is_on = last.state == STATE_ON
        else:
            # Bootstrap from legacy config-flow value on first load after migration
            self._attr_is_on = bool(
                self._entry.data.get(self._desc.legacy_conf_key, self._desc.default_on)
            )

    async def async_turn_on(self, **kwargs) -> None:
        self._attr_is_on = True
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs) -> None:
        self._attr_is_on = False
        self.async_write_ha_state()
