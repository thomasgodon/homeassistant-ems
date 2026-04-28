from __future__ import annotations

from dataclasses import dataclass

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_ON
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .const import DOMAIN


@dataclass(frozen=True)
class EmsSwitchDescription:
    unique_id: str
    name: str
    icon: str
    default_on: bool


_SWITCHES: tuple[EmsSwitchDescription, ...] = (
    EmsSwitchDescription(
        unique_id="ems_ev_charging",
        name="EMS EV Charging",
        icon="mdi:car-electric",
        default_on=True,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    async_add_entities([EmsFeatureSwitch(desc) for desc in _SWITCHES])


class EmsFeatureSwitch(SwitchEntity, RestoreEntity):

    def __init__(self, desc: EmsSwitchDescription) -> None:
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

    async def async_turn_on(self, **kwargs) -> None:
        self._attr_is_on = True
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs) -> None:
        self._attr_is_on = False
        self.async_write_ha_state()
