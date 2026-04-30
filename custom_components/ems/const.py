from __future__ import annotations

DOMAIN = "ems"

# Config entry data keys — sensors
CONF_GRID_IMPORT_POWER = "grid_import_power"
CONF_PV_POWER = "pv_power"
CONF_HOUSE_LOAD = "house_load"

# Config entry data keys — inverter (optional)
CONF_INVERTER_SELECT = "inverter_select"
CONF_INVERTER_OPTIONS = "inverter_options"
CONF_BATTERY_DISCHARGE_CAP_ENTITY = "battery_discharge_cap_entity"

# Config entry data keys — EV charger (optional)
CONF_EV_POWER = "ev_power"
CONF_EV_PLUG = "ev_plug"
CONF_EV_SOC = "ev_soc"
CONF_EV_CHARGE_TARGET_ENTITY = "ev_charge_target_entity"

# Config entry data keys — thresholds (config scalars, set in config flow)
CONF_MAX_GRID_IMPORT_W = "max_grid_import_w"
CONF_EV_TARGET_SOC = "ev_target_soc"

# Threshold defaults
DEFAULT_MAX_GRID_IMPORT_W = 4500.0
DEFAULT_EV_TARGET_SOC = 80.0
