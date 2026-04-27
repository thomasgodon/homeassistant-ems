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
        EmsMode(),
        EmsEvSchedulingStrategy(),
    ])


class EmsMode(SelectEntity, RestoreEntity):
    _attr_unique_id = "ems_mode"
    _attr_name = "EMS Mode"
    _attr_options = ["Auto", "Off"]
    _attr_current_option = "Off"
    _attr_icon = "mdi:home-lightning-bolt"

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


class EmsEvSchedulingStrategy(SelectEntity, RestoreEntity):
    _attr_unique_id = "ems_ev_scheduling_strategy"
    _attr_name = "EMS EV Scheduling Strategy"
    _attr_options = ["Deadline", "Price-optimised", "Solar-only", "Off"]
    _attr_current_option = "Off"
    _attr_icon = "mdi:car-electric"

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
