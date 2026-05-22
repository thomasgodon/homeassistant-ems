# EMS — Energy Management System

A brand-agnostic Home Assistant custom integration for **EV charging and battery management**.

EMS decides how many watts your EV should charge at every second using solar surplus and available grid headroom. It enforces a maximum grid import cap, prevents battery power from flowing to the EV, shaves grid import peaks with battery discharge, and steers the home battery toward a target SoC by end of day.

Works with any inverter that exposes a `select` entity for operating modes and a `number` entity for active power control — no inverter-specific code.

## Features

- Closed-loop EV charge control: tracks `grid_import → max_grid_import_w` regardless of wallbox quantisation or ramp lag
- Grid-optimised and price-optimised EV charging modes
- Inverter peak shaving and end-of-day battery management
- Full config-flow setup — no YAML editing required
- Two-loop architecture: 10 s strategy loop + 1 s fast loop for sub-second response to load spikes

See the [README](https://github.com/thomasgodon/homeassistant-ems) for full documentation.
