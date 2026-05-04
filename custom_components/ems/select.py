from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .const import DOMAIN


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    async_add_entities([
        EmsEvChargingMode(),
        EmsEvSmoothing(),
    ])


class EmsEvChargingMode(SelectEntity, RestoreEntity):
    _attr_unique_id = "ems_ev_charging_mode"
    _attr_name = "EMS EV Charging Mode"
    _attr_options = ["Grid-optimised", "Price-optimised", "Off"]
    _attr_current_option = "Off"
    _attr_icon = "mdi:car-electric"

    @property
    def device_info(self):
        return {"identifiers": {(DOMAIN, "ems")}, "name": "EMS", "manufacturer": "EMS"}

    async def async_added_to_hass(self) -> None:
        if (last := await self.async_get_last_state()) is not None:
            state = "Grid-optimised" if last.state == "Energy-optimised" else last.state
            if state in self._attr_options:
                self._attr_current_option = state

    async def async_select_option(self, option: str) -> None:
        self._attr_current_option = option
        self.async_write_ha_state()


class EmsEvSmoothing(SelectEntity, RestoreEntity):
    _attr_unique_id = "ems_ev_smoothing"
    _attr_name = "EMS EV Smoothing"
    _attr_options = ["Fast", "Normal", "Slow"]
    _attr_current_option = "Fast"
    _attr_icon = "mdi:chart-bell-curve"

    @property
    def device_info(self):
        return {"identifiers": {(DOMAIN, "ems")}, "name": "EMS", "manufacturer": "EMS"}

    async def async_added_to_hass(self) -> None:
        if (last := await self.async_get_last_state()) is not None:
            if last.state in self._attr_options:
                self._attr_current_option = last.state

    async def async_select_option(self, option: str) -> None:
        self._attr_current_option = option
        self.async_write_ha_state()
