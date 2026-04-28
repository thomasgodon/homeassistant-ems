from __future__ import annotations

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.components.binary_sensor import DOMAIN as BINARY_SENSOR_DOMAIN
from homeassistant.components.input_boolean import DOMAIN as INPUT_BOOLEAN_DOMAIN
from homeassistant.components.input_select import DOMAIN as INPUT_SELECT_DOMAIN
from homeassistant.components.number import DOMAIN as NUMBER_DOMAIN
from homeassistant.components.select import DOMAIN as SELECT_DOMAIN
from homeassistant.components.sensor import DOMAIN as SENSOR_DOMAIN
from homeassistant.helpers.selector import (
    EntitySelector,
    EntitySelectorConfig,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    TextSelector,
)

from .const import (
    CONF_BATTERY_POWER,
    CONF_BATTERY_SOC,
    CONF_EV_CAPACITY_KWH,
    CONF_EV_MAX_CHARGE_KW,
    CONF_EV_PLUG,
    CONF_EV_POWER,
    CONF_EV_SOC,
    CONF_EXPORT_PRICE,
    CONF_FORECAST_PATH_EXPORT,
    CONF_FORECAST_PATH_IMPORT,
    CONF_GRID_IMPORT_15M_AVG,
    CONF_GRID_IMPORT_POWER,
    CONF_HOUSE_LOAD,
    CONF_HOUSE_LOAD_NON_EV,
    CONF_IMPORT_PRICE,
    CONF_INVERTER_OPTIONS,
    CONF_INVERTER_POWER_TARGET,
    CONF_INVERTER_SELECT,
    CONF_LOAD_FORECAST_KWH,
    CONF_PV_FORECAST_KWH,
    CONF_PV_POWER,
    DOMAIN,
)
from .models import InverterVerb

_POWER_SENSOR = EntitySelectorConfig(domain=[SENSOR_DOMAIN])
_SOC_SENSOR = EntitySelectorConfig(domain=[SENSOR_DOMAIN])
_BOOL_ENTITY = EntitySelectorConfig(domain=[BINARY_SENSOR_DOMAIN, INPUT_BOOLEAN_DOMAIN])
_SELECT_ENTITY = EntitySelectorConfig(domain=[SELECT_DOMAIN, INPUT_SELECT_DOMAIN])
_NUMBER_ENTITY = EntitySelectorConfig(domain=[NUMBER_DOMAIN])


class EmsConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        self._data: dict = {}

    async def async_step_user(self, user_input=None):
        return await self.async_step_required_inputs(user_input)

    async def async_step_required_inputs(self, user_input=None):
        errors: dict = {}
        if user_input is not None:
            self._data.update(user_input)
            return await self.async_step_inverter()

        schema = vol.Schema({
            vol.Required(CONF_GRID_IMPORT_POWER): EntitySelector(_POWER_SENSOR),
            vol.Optional(CONF_GRID_IMPORT_15M_AVG): EntitySelector(_POWER_SENSOR),
            vol.Required(CONF_PV_POWER): EntitySelector(_POWER_SENSOR),
            vol.Required(CONF_HOUSE_LOAD): EntitySelector(_POWER_SENSOR),
            vol.Optional(CONF_HOUSE_LOAD_NON_EV): EntitySelector(_POWER_SENSOR),
            vol.Required(CONF_BATTERY_SOC): EntitySelector(_SOC_SENSOR),
            vol.Required(CONF_BATTERY_POWER): EntitySelector(_POWER_SENSOR),
        })
        return self.async_show_form(
            step_id="required_inputs",
            data_schema=schema,
            errors=errors,
            description_placeholders={},
        )

    async def async_step_inverter(self, user_input=None):
        if user_input is not None:
            inverter_select = user_input.get(CONF_INVERTER_SELECT)
            if inverter_select:
                # Build verb → option mapping
                options = {}
                for verb in InverterVerb:
                    key = f"inverter_verb_{verb.value.lower()}"
                    options[verb.value] = user_input.get(key) or None
                self._data[CONF_INVERTER_SELECT] = inverter_select
                self._data[CONF_INVERTER_OPTIONS] = options
                self._data[CONF_INVERTER_POWER_TARGET] = user_input.get(CONF_INVERTER_POWER_TARGET)
            return await self.async_step_ev_charger()

        # We can't dynamically list select options without knowing the entity,
        # so we use text fields for verb mappings here.
        schema = vol.Schema({
            vol.Optional(CONF_INVERTER_SELECT): EntitySelector(_SELECT_ENTITY),
            vol.Optional("inverter_verb_self_use"): TextSelector(),
            vol.Optional("inverter_verb_force_charge"): TextSelector(),
            vol.Optional("inverter_verb_force_discharge"): TextSelector(),
            vol.Optional("inverter_verb_limit_export"): TextSelector(),
            vol.Optional("inverter_verb_idle"): TextSelector(),
            vol.Optional(CONF_INVERTER_POWER_TARGET): EntitySelector(_NUMBER_ENTITY),
        })
        return self.async_show_form(step_id="inverter", data_schema=schema)

    async def async_step_ev_charger(self, user_input=None):
        if user_input is not None:
            self._data[CONF_EV_POWER] = user_input.get(CONF_EV_POWER)
            self._data[CONF_EV_PLUG] = user_input.get(CONF_EV_PLUG)
            self._data[CONF_EV_SOC] = user_input.get(CONF_EV_SOC)
            self._data[CONF_EV_CAPACITY_KWH] = user_input.get(CONF_EV_CAPACITY_KWH, 60.0)
            self._data[CONF_EV_MAX_CHARGE_KW] = user_input.get(CONF_EV_MAX_CHARGE_KW, 7.4)
            return await self.async_step_price_sources()

        schema = vol.Schema({
            vol.Optional(CONF_EV_POWER): EntitySelector(_POWER_SENSOR),
            vol.Optional(CONF_EV_PLUG): EntitySelector(_BOOL_ENTITY),
            vol.Optional(CONF_EV_SOC): EntitySelector(_SOC_SENSOR),
            vol.Optional(CONF_EV_CAPACITY_KWH): NumberSelector(
                NumberSelectorConfig(min=1, max=200, step=0.5, mode=NumberSelectorMode.BOX)
            ),
            vol.Optional(CONF_EV_MAX_CHARGE_KW): NumberSelector(
                NumberSelectorConfig(min=1, max=50, step=0.1, mode=NumberSelectorMode.BOX)
            ),
        })
        return self.async_show_form(step_id="ev_charger", data_schema=schema)

    async def async_step_price_sources(self, user_input=None):
        if user_input is not None:
            self._data.update(user_input)
            return await self.async_step_forecast_sources()

        schema = vol.Schema({
            vol.Optional(CONF_IMPORT_PRICE): EntitySelector(_POWER_SENSOR),
            vol.Optional(CONF_EXPORT_PRICE): EntitySelector(_POWER_SENSOR),
            vol.Optional(CONF_FORECAST_PATH_IMPORT): TextSelector(),
            vol.Optional(CONF_FORECAST_PATH_EXPORT): TextSelector(),
        })
        return self.async_show_form(step_id="price_sources", data_schema=schema)

    async def async_step_forecast_sources(self, user_input=None):
        if user_input is not None:
            self._data.update(user_input)
            return self.async_create_entry(title="EMS", data=self._data)

        schema = vol.Schema({
            vol.Optional(CONF_PV_FORECAST_KWH): EntitySelector(_POWER_SENSOR),
            vol.Optional(CONF_LOAD_FORECAST_KWH): EntitySelector(_POWER_SENSOR),
        })
        return self.async_show_form(step_id="forecast_sources", data_schema=schema)

    @staticmethod
    def async_get_options_flow(config_entry):
        return EmsOptionsFlow(config_entry)


class EmsOptionsFlow(config_entries.OptionsFlow):
    """Options flow — re-runs the entity mapping wizard."""

    def __init__(self, config_entry) -> None:
        self._config_entry = config_entry
        self._data: dict = dict(config_entry.data)

    async def async_step_init(self, user_input=None):
        return await self.async_step_inverter(user_input)

    async def async_step_inverter(self, user_input=None):
        if user_input is not None:
            inverter_select = user_input.get(CONF_INVERTER_SELECT)
            if inverter_select:
                options = {}
                for verb in InverterVerb:
                    key = f"inverter_verb_{verb.value.lower()}"
                    options[verb.value] = user_input.get(key) or None
                self._data[CONF_INVERTER_SELECT] = inverter_select
                self._data[CONF_INVERTER_OPTIONS] = options
                self._data[CONF_INVERTER_POWER_TARGET] = user_input.get(CONF_INVERTER_POWER_TARGET)
            return await self.async_step_ev_charger()

        schema = vol.Schema({
            vol.Optional(
                CONF_INVERTER_SELECT, default=self._data.get(CONF_INVERTER_SELECT, "")
            ): EntitySelector(_SELECT_ENTITY),
            vol.Optional("inverter_verb_self_use"): TextSelector(),
            vol.Optional("inverter_verb_force_charge"): TextSelector(),
            vol.Optional("inverter_verb_force_discharge"): TextSelector(),
            vol.Optional("inverter_verb_limit_export"): TextSelector(),
            vol.Optional("inverter_verb_idle"): TextSelector(),
            vol.Optional(CONF_INVERTER_POWER_TARGET): EntitySelector(_NUMBER_ENTITY),
        })
        return self.async_show_form(step_id="inverter", data_schema=schema)

    async def async_step_ev_charger(self, user_input=None):
        if user_input is not None:
            return self.async_create_entry(title="", data=self._data)

        schema = vol.Schema({})
        return self.async_show_form(step_id="ev_charger", data_schema=schema)
