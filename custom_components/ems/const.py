from __future__ import annotations

DOMAIN = "ems"

# Config entry data keys — sensors
CONF_GRID_IMPORT_POWER = "grid_import_power"
CONF_GRID_IMPORT_15M_AVG = "grid_import_15m_avg"
CONF_PV_POWER = "pv_power"
CONF_HOUSE_LOAD = "house_load"

# Config entry data keys — inverter (optional)
CONF_INVERTER_SELECT = "inverter_select"
CONF_INVERTER_OPTIONS = "inverter_options"
CONF_INVERTER_POWER_TARGET = "inverter_power_target"

# Config entry data keys — EV charger (optional)
CONF_EV_POWER = "ev_power"
CONF_EV_PLUG = "ev_plug"
CONF_EV_SOC = "ev_soc"
CONF_EV_CAPACITY_KWH = "ev_capacity_kwh"
CONF_EV_MAX_CHARGE_KW = "ev_max_charge_kw"

# Config entry data keys — price
CONF_IMPORT_PRICE = "import_price"
CONF_FORECAST_PATH_IMPORT = "forecast_path_import"

# Storage
STORAGE_KEY = "ems_state"
STORAGE_VERSION = 1
