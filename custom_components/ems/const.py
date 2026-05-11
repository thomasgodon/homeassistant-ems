from __future__ import annotations

DOMAIN = "ems"

# Config entry data keys — sensors
CONF_GRID_IMPORT_POWER = "grid_import_power"
CONF_PV_POWER = "pv_power"
CONF_HOUSE_LOAD = "house_load"

# Config entry data keys — inverter (optional)
CONF_INVERTER_SELECT = "inverter_select"
CONF_INVERTER_DISABLED_OPTION = "inverter_disabled_option"
CONF_INVERTER_POWER_CONTROL_OPTION = "inverter_power_control_option"
CONF_ACTIVE_POWER_ENTITY = "active_power_entity"

# Config entry data keys — EV charger (optional)
CONF_EV_POWER = "ev_power"
CONF_EV_PLUG = "ev_plug"
CONF_EV_SOC = "ev_soc"
CONF_EV_CHARGE_TARGET_ENTITY = "ev_charge_target_entity"
CONF_EV_MAX_CHARGE_W = "ev_max_charge_w"

# Config entry data keys — thresholds (config scalars, set in config flow)
CONF_MAX_GRID_IMPORT_W = "max_grid_import_w"
CONF_EV_TARGET_SOC = "ev_target_soc"
CONF_PRICE_SIGNAL_ENTITY = "price_signal_entity"

# Config entry data keys — battery management (optional)
CONF_BATTERY_SOC = "battery_soc"
CONF_BATTERY_CAPACITY = "battery_capacity"
CONF_SOLAR_FORECAST_REMAINING = "solar_forecast_remaining"
CONF_HOUSE_LOAD_FORECAST_REMAINING = "house_load_forecast_remaining"
CONF_PRICES_JSON_ENTITY = "prices_json_entity"

# Threshold defaults
DEFAULT_MAX_GRID_IMPORT_W = 4500.0
DEFAULT_EV_TARGET_SOC = 80.0
DEFAULT_BATTERY_TARGET_SOC = 80.0
