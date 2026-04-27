from __future__ import annotations

import datetime

from homeassistant.components.time import TimeEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .const import DOMAIN

_DEFAULT_READY_BY = datetime.time(7, 0)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    async_add_entities([EmsEvReadyBy()])


class EmsEvReadyBy(TimeEntity, RestoreEntity):
    _attr_unique_id = "ems_ev_ready_by"
    _attr_name = "EMS EV Ready By"
    _attr_icon = "mdi:clock-end"

    def __init__(self) -> None:
        self._attr_native_value = _DEFAULT_READY_BY

    @property
    def device_info(self):
        return {"identifiers": {(DOMAIN, "ems")}, "name": "EMS", "manufacturer": "EMS"}

    async def async_added_to_hass(self) -> None:
        if (last := await self.async_get_last_state()) is not None:
            try:
                parts = last.state.split(":")
                self._attr_native_value = datetime.time(int(parts[0]), int(parts[1]))
            except (ValueError, IndexError):
                pass

    async def async_set_value(self, value: datetime.time) -> None:
        self._attr_native_value = value
        self.async_write_ha_state()
