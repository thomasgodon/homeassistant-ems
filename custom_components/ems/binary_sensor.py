from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import EmsStrategyCoordinator
from .models import LadderResult


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: EmsStrategyCoordinator = hass.data[DOMAIN][entry.entry_id]["strategy_coordinator"]
    async_add_entities([
        EmsEvChargingActiveSensor(coordinator),
    ])


class _EmsBaseBinarySensor(CoordinatorEntity[EmsStrategyCoordinator], BinarySensorEntity):
    def __init__(self, coordinator: EmsStrategyCoordinator) -> None:
        super().__init__(coordinator)

    @property
    def device_info(self):
        return {"identifiers": {(DOMAIN, "ems")}, "name": "EMS", "manufacturer": "EMS"}

    @property
    def _result(self) -> LadderResult | None:
        return self.coordinator.data


class EmsEvChargingActiveSensor(_EmsBaseBinarySensor):
    _attr_unique_id = "ems_ev_charging_active"
    _attr_name = "EMS EV Charging Active"
    _attr_icon = "mdi:car-electric"

    @property
    def is_on(self) -> bool:
        return (
            self._result is not None
            and self._result.decision.strategy == "ev_charging"
        )
