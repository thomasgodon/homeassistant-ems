from __future__ import annotations

DOMAIN = "ems"

# Default tick intervals (seconds)
DEFAULT_STRATEGY_INTERVAL_S = 10
DEFAULT_PEAK_SHAVE_INTERVAL_S = 1
DEFAULT_STARTUP_GRACE_S = 45

# Config entry data keys
CONF_GRID_IMPORT_POWER = "grid_import_power"
CONF_GRID_IMPORT_15M_AVG = "grid_import_15m_avg"
CONF_PV_POWER = "pv_power"
CONF_HOUSE_LOAD = "house_load"
CONF_HOUSE_LOAD_NON_EV = "house_load_non_ev"
CONF_BATTERY_SOC = "battery_soc"
CONF_BATTERY_POWER = "battery_power"

CONF_INVERTER_SELECT = "inverter_select"
CONF_INVERTER_OPTIONS = "inverter_options"
CONF_INVERTER_POWER_TARGET = "inverter_power_target"

CONF_EV_POWER = "ev_power"
CONF_EV_PLUG = "ev_plug"
CONF_EV_SOC = "ev_soc"
CONF_EV_CAPACITY_KWH = "ev_capacity_kwh"
CONF_EV_MAX_CHARGE_KW = "ev_max_charge_kw"

CONF_IMPORT_PRICE = "import_price"
CONF_EXPORT_PRICE = "export_price"
CONF_FORECAST_PATH_IMPORT = "forecast_path_import"
CONF_FORECAST_PATH_EXPORT = "forecast_path_export"

CONF_PV_FORECAST_KWH = "pv_forecast_kwh"
CONF_LOAD_FORECAST_KWH = "load_forecast_kwh"

CONF_ENABLE_PROACTIVE_CHARGE = "enable_proactive_charge"
CONF_ENABLE_EV_SCHEDULING = "enable_ev_scheduling"
CONF_ENABLE_FORECAST_PLANNING = "enable_forecast_planning"

# Storage
STORAGE_KEY = "ems_state"
STORAGE_VERSION = 1
