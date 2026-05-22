from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import EmsFastLoopCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: EmsFastLoopCoordinator = hass.data[DOMAIN][entry.entry_id]["fast_loop_coordinator"]
    async_add_entities([EmsEvChargeAdvisedBinarySensor(coordinator)])


class EmsEvChargeAdvisedBinarySensor(CoordinatorEntity[EmsFastLoopCoordinator], BinarySensorEntity):
    _attr_unique_id = "ems_ev_charge_advised"
    _attr_name = "EMS EV Charge Advised"

    def __init__(self, coordinator: EmsFastLoopCoordinator) -> None:
        super().__init__(coordinator)

    @property
    def device_info(self):
        return {"identifiers": {(DOMAIN, "ems")}, "name": "EMS", "manufacturer": "EMS"}

    @property
    def is_on(self) -> bool:
        if self.coordinator.data is None:
            return False
        return self.coordinator.data.should_advise_ev_charge

    @property
    def available(self) -> bool:
        return True
