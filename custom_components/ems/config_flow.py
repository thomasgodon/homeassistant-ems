from __future__ import annotations

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.components.binary_sensor import DOMAIN as BINARY_SENSOR_DOMAIN
from homeassistant.components.input_boolean import DOMAIN as INPUT_BOOLEAN_DOMAIN
from homeassistant.components.input_number import DOMAIN as INPUT_NUMBER_DOMAIN
from homeassistant.components.input_select import DOMAIN as INPUT_SELECT_DOMAIN
from homeassistant.components.number import DOMAIN as NUMBER_DOMAIN
from homeassistant.components.select import DOMAIN as SELECT_DOMAIN
from homeassistant.components.sensor import DOMAIN as SENSOR_DOMAIN
from homeassistant.helpers.selector import (
    EntitySelector,
    EntitySelectorConfig,
    TextSelector,
)

from .const import (
    CONF_ACTIVE_POWER_ENTITY,
    CONF_BATTERY_CAPACITY,
    CONF_BATTERY_SOC,
    CONF_EV_CHARGE_TARGET_ENTITY,
    CONF_EV_MAX_CHARGE_W,
    CONF_EV_PLUG,
    CONF_EV_POWER,
    CONF_EV_SOC,
    CONF_EV_TARGET_SOC,
    CONF_GRID_IMPORT_POWER,
    CONF_HOUSE_LOAD,
    CONF_HOUSE_LOAD_FORECAST_JSON_ENTITY,
    CONF_INVERTER_DISABLED_OPTION,
    CONF_INVERTER_POWER_CONTROL_OPTION,
    CONF_INVERTER_SELECT,
    CONF_MAX_GRID_IMPORT_W,
    CONF_PRICE_SIGNAL_ENTITY,
    CONF_PRICES_JSON_ENTITY,
    CONF_PV_POWER,
    CONF_SOLAR_FORECAST_JSON_ENTITY,
    DOMAIN,
)

_POWER_SENSOR = EntitySelectorConfig(domain=[SENSOR_DOMAIN])
_SOC_SENSOR = EntitySelectorConfig(domain=[SENSOR_DOMAIN])
_BOOL_ENTITY = EntitySelectorConfig(domain=[BINARY_SENSOR_DOMAIN, INPUT_BOOLEAN_DOMAIN])
_SELECT_ENTITY = EntitySelectorConfig(domain=[SELECT_DOMAIN, INPUT_SELECT_DOMAIN])
_NUMBER_OR_INPUT_NUMBER = EntitySelectorConfig(domain=[NUMBER_DOMAIN, INPUT_NUMBER_DOMAIN])
_NUMBER_OR_SENSOR = EntitySelectorConfig(domain=[NUMBER_DOMAIN, INPUT_NUMBER_DOMAIN, SENSOR_DOMAIN])
_JSON_SENSOR = EntitySelectorConfig(domain=[SENSOR_DOMAIN])


class EmsConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 11

    def __init__(self) -> None:
        self._data: dict = {}

    async def async_step_user(self, user_input=None):
        return await self.async_step_inverter(user_input)

    async def async_step_inverter(self, user_input=None):
        if user_input is not None:
            self._data[CONF_PV_POWER] = user_input[CONF_PV_POWER]
            inverter_select = user_input.get(CONF_INVERTER_SELECT)
            if inverter_select:
                self._data[CONF_INVERTER_SELECT] = inverter_select
            self._data[CONF_INVERTER_DISABLED_OPTION] = (
                user_input.get(CONF_INVERTER_DISABLED_OPTION) or None
            )
            self._data[CONF_INVERTER_POWER_CONTROL_OPTION] = (
                user_input.get(CONF_INVERTER_POWER_CONTROL_OPTION) or None
            )
            self._data[CONF_ACTIVE_POWER_ENTITY] = user_input.get(CONF_ACTIVE_POWER_ENTITY)
            return await self.async_step_ev()

        schema = vol.Schema({
            vol.Required(CONF_PV_POWER): EntitySelector(_POWER_SENSOR),
            vol.Optional(CONF_INVERTER_SELECT): EntitySelector(_SELECT_ENTITY),
            vol.Optional(CONF_INVERTER_DISABLED_OPTION): TextSelector(),
            vol.Optional(CONF_INVERTER_POWER_CONTROL_OPTION): TextSelector(),
            vol.Optional(CONF_ACTIVE_POWER_ENTITY): EntitySelector(_NUMBER_OR_INPUT_NUMBER),
        })
        return self.async_show_form(step_id="inverter", data_schema=schema)

    async def async_step_ev(self, user_input=None):
        if user_input is not None:
            self._data[CONF_EV_POWER] = user_input[CONF_EV_POWER]
            self._data[CONF_EV_PLUG] = user_input.get(CONF_EV_PLUG)
            self._data[CONF_EV_SOC] = user_input.get(CONF_EV_SOC)
            self._data[CONF_EV_TARGET_SOC] = user_input.get(CONF_EV_TARGET_SOC)
            self._data[CONF_EV_CHARGE_TARGET_ENTITY] = user_input.get(CONF_EV_CHARGE_TARGET_ENTITY)
            self._data[CONF_EV_MAX_CHARGE_W] = user_input.get(CONF_EV_MAX_CHARGE_W)
            self._data[CONF_PRICE_SIGNAL_ENTITY] = user_input.get(CONF_PRICE_SIGNAL_ENTITY)
            return await self.async_step_battery()

        schema = vol.Schema({
            vol.Required(CONF_EV_POWER): EntitySelector(_POWER_SENSOR),
            vol.Optional(CONF_EV_PLUG): EntitySelector(_BOOL_ENTITY),
            vol.Optional(CONF_EV_SOC): EntitySelector(_SOC_SENSOR),
            vol.Optional(CONF_EV_TARGET_SOC): EntitySelector(_NUMBER_OR_SENSOR),
            vol.Optional(CONF_EV_CHARGE_TARGET_ENTITY): EntitySelector(_NUMBER_OR_INPUT_NUMBER),
            vol.Optional(CONF_EV_MAX_CHARGE_W): EntitySelector(_NUMBER_OR_INPUT_NUMBER),
            vol.Optional(CONF_PRICE_SIGNAL_ENTITY): EntitySelector(_BOOL_ENTITY),
        })
        return self.async_show_form(step_id="ev", data_schema=schema)

    async def async_step_battery(self, user_input=None):
        if user_input is not None:
            self._data[CONF_BATTERY_SOC] = user_input.get(CONF_BATTERY_SOC)
            self._data[CONF_BATTERY_CAPACITY] = user_input.get(CONF_BATTERY_CAPACITY)
            self._data[CONF_SOLAR_FORECAST_JSON_ENTITY] = user_input.get(
                CONF_SOLAR_FORECAST_JSON_ENTITY
            )
            self._data[CONF_HOUSE_LOAD_FORECAST_JSON_ENTITY] = user_input.get(
                CONF_HOUSE_LOAD_FORECAST_JSON_ENTITY
            )
            self._data[CONF_PRICES_JSON_ENTITY] = user_input.get(CONF_PRICES_JSON_ENTITY)
            return await self.async_step_grid()

        schema = vol.Schema({
            vol.Optional(CONF_BATTERY_SOC): EntitySelector(_SOC_SENSOR),
            vol.Optional(CONF_BATTERY_CAPACITY): EntitySelector(_NUMBER_OR_SENSOR),
            vol.Optional(CONF_SOLAR_FORECAST_JSON_ENTITY): EntitySelector(_JSON_SENSOR),
            vol.Optional(CONF_HOUSE_LOAD_FORECAST_JSON_ENTITY): EntitySelector(_JSON_SENSOR),
            vol.Optional(CONF_PRICES_JSON_ENTITY): EntitySelector(_JSON_SENSOR),
        })
        return self.async_show_form(step_id="battery", data_schema=schema)

    async def async_step_grid(self, user_input=None):
        if user_input is not None:
            self._data.update(user_input)
            return self.async_create_entry(title="EMS", data=self._data)

        schema = vol.Schema({
            vol.Required(CONF_GRID_IMPORT_POWER): EntitySelector(_POWER_SENSOR),
            vol.Required(CONF_HOUSE_LOAD): EntitySelector(_POWER_SENSOR),
            vol.Optional(CONF_MAX_GRID_IMPORT_W): EntitySelector(_NUMBER_OR_INPUT_NUMBER),
        })
        return self.async_show_form(step_id="grid", data_schema=schema)

    @staticmethod
    def async_get_options_flow(config_entry):
        return EmsOptionsFlow(config_entry)


class EmsOptionsFlow(config_entries.OptionsFlow):
    """Options flow — walks all 3 config steps so every entity can be re-attached."""

    def __init__(self, config_entry) -> None:
        self._config_entry = config_entry
        self._data: dict = dict(config_entry.data)

    async def async_step_init(self, user_input=None):
        return await self.async_step_inverter(user_input)

    async def async_step_inverter(self, user_input=None):
        if user_input is not None:
            self._data[CONF_PV_POWER] = user_input[CONF_PV_POWER]
            inverter_select = user_input.get(CONF_INVERTER_SELECT)
            if inverter_select:
                self._data[CONF_INVERTER_SELECT] = inverter_select
            self._data[CONF_INVERTER_DISABLED_OPTION] = (
                user_input.get(CONF_INVERTER_DISABLED_OPTION) or None
            )
            self._data[CONF_INVERTER_POWER_CONTROL_OPTION] = (
                user_input.get(CONF_INVERTER_POWER_CONTROL_OPTION) or None
            )
            self._data[CONF_ACTIVE_POWER_ENTITY] = user_input.get(CONF_ACTIVE_POWER_ENTITY)
            return await self.async_step_ev()

        schema = vol.Schema({
            vol.Required(
                CONF_PV_POWER,
                default=self._data.get(CONF_PV_POWER),
            ): EntitySelector(_POWER_SENSOR),
            vol.Optional(
                CONF_INVERTER_SELECT,
                default=self._data.get(CONF_INVERTER_SELECT),
            ): EntitySelector(_SELECT_ENTITY),
            vol.Optional(
                CONF_INVERTER_DISABLED_OPTION,
                default=self._data.get(CONF_INVERTER_DISABLED_OPTION),
            ): TextSelector(),
            vol.Optional(
                CONF_INVERTER_POWER_CONTROL_OPTION,
                default=self._data.get(CONF_INVERTER_POWER_CONTROL_OPTION),
            ): TextSelector(),
            vol.Optional(
                CONF_ACTIVE_POWER_ENTITY,
                default=self._data.get(CONF_ACTIVE_POWER_ENTITY),
            ): EntitySelector(_NUMBER_OR_INPUT_NUMBER),
        })
        return self.async_show_form(step_id="inverter", data_schema=schema)

    async def async_step_ev(self, user_input=None):
        if user_input is not None:
            self._data[CONF_EV_POWER] = user_input[CONF_EV_POWER]
            self._data[CONF_EV_PLUG] = user_input.get(CONF_EV_PLUG)
            self._data[CONF_EV_SOC] = user_input.get(CONF_EV_SOC)
            self._data[CONF_EV_TARGET_SOC] = user_input.get(CONF_EV_TARGET_SOC)
            self._data[CONF_EV_CHARGE_TARGET_ENTITY] = user_input.get(CONF_EV_CHARGE_TARGET_ENTITY)
            self._data[CONF_EV_MAX_CHARGE_W] = user_input.get(CONF_EV_MAX_CHARGE_W)
            self._data[CONF_PRICE_SIGNAL_ENTITY] = user_input.get(CONF_PRICE_SIGNAL_ENTITY)
            return await self.async_step_battery()

        schema = vol.Schema({
            vol.Required(
                CONF_EV_POWER,
                default=self._data.get(CONF_EV_POWER),
            ): EntitySelector(_POWER_SENSOR),
            vol.Optional(
                CONF_EV_PLUG,
                default=self._data.get(CONF_EV_PLUG),
            ): EntitySelector(_BOOL_ENTITY),
            vol.Optional(
                CONF_EV_SOC,
                default=self._data.get(CONF_EV_SOC),
            ): EntitySelector(_SOC_SENSOR),
            vol.Optional(
                CONF_EV_TARGET_SOC,
                default=self._data.get(CONF_EV_TARGET_SOC),
            ): EntitySelector(_NUMBER_OR_SENSOR),
            vol.Optional(
                CONF_EV_CHARGE_TARGET_ENTITY,
                default=self._data.get(CONF_EV_CHARGE_TARGET_ENTITY),
            ): EntitySelector(_NUMBER_OR_INPUT_NUMBER),
            vol.Optional(
                CONF_EV_MAX_CHARGE_W,
                default=self._data.get(CONF_EV_MAX_CHARGE_W),
            ): EntitySelector(_NUMBER_OR_INPUT_NUMBER),
            vol.Optional(
                CONF_PRICE_SIGNAL_ENTITY,
                default=self._data.get(CONF_PRICE_SIGNAL_ENTITY),
            ): EntitySelector(_BOOL_ENTITY),
        })
        return self.async_show_form(step_id="ev", data_schema=schema)

    async def async_step_battery(self, user_input=None):
        if user_input is not None:
            self._data[CONF_BATTERY_SOC] = user_input.get(CONF_BATTERY_SOC)
            self._data[CONF_BATTERY_CAPACITY] = user_input.get(CONF_BATTERY_CAPACITY)
            self._data[CONF_SOLAR_FORECAST_JSON_ENTITY] = user_input.get(
                CONF_SOLAR_FORECAST_JSON_ENTITY
            )
            self._data[CONF_HOUSE_LOAD_FORECAST_JSON_ENTITY] = user_input.get(
                CONF_HOUSE_LOAD_FORECAST_JSON_ENTITY
            )
            self._data[CONF_PRICES_JSON_ENTITY] = user_input.get(CONF_PRICES_JSON_ENTITY)
            return await self.async_step_grid()

        schema = vol.Schema({
            vol.Optional(
                CONF_BATTERY_SOC,
                default=self._data.get(CONF_BATTERY_SOC),
            ): EntitySelector(_SOC_SENSOR),
            vol.Optional(
                CONF_BATTERY_CAPACITY,
                default=self._data.get(CONF_BATTERY_CAPACITY),
            ): EntitySelector(_NUMBER_OR_SENSOR),
            vol.Optional(
                CONF_SOLAR_FORECAST_JSON_ENTITY,
                default=self._data.get(CONF_SOLAR_FORECAST_JSON_ENTITY),
            ): EntitySelector(_JSON_SENSOR),
            vol.Optional(
                CONF_HOUSE_LOAD_FORECAST_JSON_ENTITY,
                default=self._data.get(CONF_HOUSE_LOAD_FORECAST_JSON_ENTITY),
            ): EntitySelector(_JSON_SENSOR),
            vol.Optional(
                CONF_PRICES_JSON_ENTITY,
                default=self._data.get(CONF_PRICES_JSON_ENTITY),
            ): EntitySelector(_JSON_SENSOR),
        })
        return self.async_show_form(step_id="battery", data_schema=schema)

    async def async_step_grid(self, user_input=None):
        if user_input is not None:
            self._data.update(user_input)
            self.hass.config_entries.async_update_entry(self._config_entry, data=self._data)
            return self.async_create_entry(title="", data={})

        schema = vol.Schema({
            vol.Required(
                CONF_GRID_IMPORT_POWER,
                default=self._data.get(CONF_GRID_IMPORT_POWER),
            ): EntitySelector(_POWER_SENSOR),
            vol.Required(
                CONF_HOUSE_LOAD,
                default=self._data.get(CONF_HOUSE_LOAD),
            ): EntitySelector(_POWER_SENSOR),
            vol.Optional(
                CONF_MAX_GRID_IMPORT_W,
                default=self._data.get(CONF_MAX_GRID_IMPORT_W),
            ): EntitySelector(_NUMBER_OR_INPUT_NUMBER),
        })
        return self.async_show_form(step_id="grid", data_schema=schema)
