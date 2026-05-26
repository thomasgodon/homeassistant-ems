<p align="center"><img src="brand/logo.png" alt="EMS" height="96"></p>

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/hacs/integration)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![CI](https://github.com/thomasgodon/homeassistant-ems/actions/workflows/test.yml/badge.svg)](https://github.com/thomasgodon/homeassistant-ems/actions/workflows/test.yml)

# EMS — Energy Management System

A brand-agnostic Home Assistant custom integration for **EV charging and battery management**. It decides how many watts your EV should charge at every second using solar surplus and available grid headroom, enforces a maximum grid import cap, prevents battery power from flowing to the EV, shaves grid import peaks with battery discharge, and steers the home battery toward a target SoC by end of day.

Works with any inverter that exposes a `select` entity for operating modes and a `number` entity for active power control — no inverter-specific code.

---

## Table of Contents

- [How it works](#how-it-works)
  - [10-second strategy loop](#10-second-strategy-loop-emsstrategycoordinator)
  - [1-second fast loop](#1-second-fast-loop-emsfastloopcoordinator)
  - [EV algorithm — power balance](#ev-algorithm--power-balance)
  - [Inverter control algorithm — priority ladder](#inverter-control-algorithm--priority-ladder)
  - [EOD battery fullness decision](#eod-battery-fullness-decision)
- [Installation](#installation)
- [Configuration](#configuration-config-flow)
  - [Step 1 — Inverter](#step-1--inverter)
  - [Step 2 — EV Charger](#step-2--ev-charger)
  - [Step 3 — Battery Management](#step-3--battery-management)
  - [Step 4 — Grid](#step-4--grid)
- [Runtime inputs](#runtime-inputs)
- [Tunable thresholds](#tunable-thresholds)
- [EV charging mode](#ev-charging-mode)
- [Inverter control mode](#inverter-control-mode)
- [Outputs](#outputs-writes-to-your-hardware)
  - [Slow loop (10 s)](#slow-loop-10-s--controllerwrite_slow)
  - [Fast loop (1 s)](#fast-loop-1-s--controllerwrite_fast)
- [Exposed entities](#exposed-entities)
  - [Select entities](#select-entities)
  - [Number entities](#number-entities-numberems_)
  - [Sensor entities](#sensor-entities-sensorems_)
  - [Binary sensor entities](#binary-sensor-entities-binary_sensorems_)
- [Development](#development)

---

## How it works

Two execution loops share a single `EmsState` object. Each loop runs two independent algorithms:

- **EV algorithm** (`strategy.py`) — computes the EV charge target.
- **Inverter control algorithm** (`inverter_control.py`) — computes the inverter verb and active power.

### 10-second strategy loop (`EmsStrategyCoordinator`)
1. `_build_inputs()` — reads all configured HA entities into a frozen `Inputs` dataclass.
2. `strategy.decide(inputs)` — pure function; returns an `EvDecision` (EV target watts and charging state).
3. `inverter_control.decide(inputs)` — pure function; returns an `InverterDecision` (inverter verb and active power).
4. Writes are skipped for 60 seconds after startup to avoid flapping on `unavailable` sensors.
5. `controller.write_slow()` — writes the inverter verb **every cycle** (overriding any manual user change within 10 s), writes active power when applicable, and always writes the EV charge target.

**Input reactivity:** the strategy loop registers a state-change listener on every configured input entity and every `select.ems_*` / `number.ems_*` entity. A state change on any of these triggers an immediate `async_request_refresh()` — the 10-second interval acts only as a fallback for entities that do not change. The fast loop's 1-second poll for `pv_w`, `house_load_w`, `ev_power_w`, and `grid_import_w` already provides sub-second latency for those values; changes to all other inputs (SoC, prices, thresholds, mode selects, number knobs) now propagate on the next event-loop tick rather than waiting up to 10 seconds.

### 1-second fast loop (`EmsFastLoopCoordinator`)
Always runs once the integration is active. Re-reads `pv_w`, `house_load_w`, `ev_power_w`, `grid_import_w`, `ev_plugged`, `ev_strategy`, `inverter_strategy`, and `is_cheap_hour`, then runs `strategy.recompute()` and `inverter_control.recompute()`. Calls `controller.write_fast()` — which writes active power and EV target but **never writes the inverter verb** (verb enforcement is the slow loop's job). Battery fields (`ev_soc`, `battery_soc`, thresholds, prices, forecasts) are carried from the most recent slow-loop result and refreshed whenever the strategy loop fires.

### EV algorithm — power balance

```
house_non_ev   = max(0, house_load_w − ev_power_w)

# Hard gates → idle (evaluated in priority order)
if ev_strategy == "Off":                                                    → charging_disabled
if not ev_plugged:                                                          → ev_not_connected
if ev_strategy == "Price-optimised" and not is_cheap_hour:                  → outside_cheap_hours
if ev_soc is not None and ev_soc ≥ ev_target_soc:                          → target_soc_reached

# EV target
if ev_strategy == "Energy-optimised":
    surplus_w    = max(0, pv_w − house_non_ev)         ← PV minus non-EV house load
    scale        = _energy_optimised_scale(inputs)     ← 0..1 battery priority factor (see below)
    ev_target_w  = surplus_w × scale
else:  # Grid-optimised / Price-optimised — integrating closed-loop
    feedback_w   = last_ev_target_w  ← last commanded target (ev_power_w on cold start)
    ev_target_w  = max(0, max_grid_import_w − grid_import_w + feedback_w)

ev_target_w    = min(ev_target_w, max_ev_charge_w)  ← if max_ev_charge_w is configured

# Below minimum → idle
if ev_target_w < ev_min_charge_w:                                           → not_enough_power

# Charge
else:
    → ev_charging (ev_target_w)
```

**Battery priority scale (Energy-optimised only):**

```
net_solar_kwh      = solar_forecast_remaining_kwh − (house_load_forecast_remaining_kwh or 0)
predicted_end_soc  = battery_soc + net_solar_kwh × 100 / battery_capacity_kwh
floor              = battery_soc   ← scale = 0 when forecast won't advance SoC at all
target             = battery_target_soc

scale = clamp((predicted_end_soc − floor) / (target − floor), 0, 1)
      = 1.0  when battery already at/above target, OR prediction meets/exceeds target
      = 0.0  when prediction will not advance SoC from its current level
      = 1.0  when any required input is missing (best-effort full surplus)
```

The `active_power_w` write (`−min(pv_w, house_load_w)`) naturally ensures whatever fraction of the surplus is not given to the EV stays available for the battery via the inverter's own self-use logic.

Integrating closed-loop: using the last commanded target (`last_ev_target_w`) as feedback makes the formula a pure integrator on grid error — it keeps nudging the EV target until `grid_import_w = grid_target`, regardless of wallbox current quantisation or ramp lag.

### Inverter control algorithm — priority ladder

Controlled by `select.ems_inverter_control_mode`. When set to **`Off`**, EMS never touches the inverter. When set to **`Control Inverter`**, the following cases are evaluated in strict priority order each second.

| Priority | Condition | `active_power_w` | Status |
|---|---|---|---|
| 1 | `grid_import_w > max_grid_import_w` AND `commanded_ev_charge_w == 0` AND `ev_power_w == 0` | `-(pv_w + (grid_import_w − max_grid_import_w))` | `peak_shaving` |
| 2 | EOD suppress active AND `pv_w < house_load_w` AND `commanded_ev_charge_w == 0` AND `ev_power_w == 0` | `0` | `holding_battery` |
| 3 | `commanded_ev_charge_w == 0` AND `ev_power_w == 0` | not written | `idle` |
| 4 | otherwise (`commanded_ev_charge_w > 0` OR `ev_power_w > 0`) | `-min(pv_w, house_load_w)` | `controlling` |

`commanded_ev_charge_w` is the EMA-smoothed EV charge target EMS will write to the wallbox this cycle. When strategy returns any idle decision (EV unplugged, outside cheap hours, target SoC reached, etc.), the EMA snaps to exactly `0.0` immediately — this ensures row 3 fires reliably and active-power writes stop as soon as the EV is no longer charging.

**Peak shaving (priority 1):** when grid import spikes above the cap **and no EV session is active**, the battery discharges to cover the excess — protecting against sudden house-load events (appliances, etc.). During an EV charging session, peak shaving is suppressed: the closed-loop EV integrator already drives `grid_import → max_grid_import_w` by adjusting the EV charge target, and firing peak shaving simultaneously would double-correct (crashing the EV target to zero, stopping the EV, collapsing grid import, then winding the integrator back up to maximum — a sawtooth oscillation). Responds within 1 second via the fast loop.

**Deficit hold (priority 2):** when solar forecast won't fill the battery to the target SoC by end of day, EMS selects the cheapest `ceil(deficit_kwh)` remaining hours and holds the battery during those hours (`active_power_w = 0`), so the house load is drawn from cheap grid instead of draining the battery. The battery is then available to cover the expensive evening. The hold is bypassed when PV alone covers the house load (`pv_w ≥ house_load_w`) — Self-Use Mode handles it without EMS involvement. The hold is also bypassed whenever an EV session is active (`commanded_ev_charge_w > 0` or `ev_power_w > 0`): during a session the EV integrator already manages grid import, and holding the battery simultaneously would divert PV into the battery while the EV draws from the grid. This means the hold can fire at night (when `pv_w = 0 < house_load_w` and no EV session is running). Peak shaving overrides it if a grid spike occurs.

**Idle (priority 3):** when EMS is not commanding an EV charge target and the EV is not drawing, EMS releases the inverter to its native Self-Use Mode. In Self-Use Mode the inverter autonomously discharges the battery to cover house load — this is the desired behaviour during expensive hours when the deficit hold is not active.

**Controlling (default):** whenever EMS is commanding a charge target or the EV is still drawing (wind-down), EMS asserts `power_control` with `active_power_w = -min(pv_w, house_load_w)`. Power Control is asserted on cycle 1 of a session, before the first target is written. Battery is never drained for the EV (magnitude capped by `pv_w`) and never charged from AC (sign always ≤ 0).

**Battery protection:** `active_power_w` is always ≤ 0. The inverter is held in `power_control` for the duration of a charging session; outside sessions, Self-Use Mode handles battery routing autonomously.

**Observing physical battery flow:** the inverter status describes the EMS *control mode*, not the instantaneous battery direction. The separate `sensor.ems_battery_status` sensor surfaces this as human-readable prose, prioritising the *reason* (peak shaving, deficit hold) before the raw power-balance action.

### EOD battery fullness decision

Each 10-second cycle, `strategy.is_eod_suppress_hour(inputs)` determines whether the current hour should suppress battery discharge:

1. Return `False` if any of `battery_soc`, `battery_capacity_kwh`, `solar_forecast_remaining_kwh`, or `prices_today` is `None` (feature disabled).
2. `battery_gap_kwh = battery_capacity_kwh × max(0, (battery_target_soc − battery_soc) / 100)`.
3. `effective_solar = max(0, solar_forecast_remaining_kwh − (house_load_forecast_remaining_kwh or 0))`. Clamped at zero so a day where solar can't cover the house load doesn't invent a phantom battery shortfall when the battery is already at target.
4. If `effective_solar ≥ battery_gap_kwh` → solar will cover it; return `False`.
5. `deficit_kwh = battery_gap_kwh − effective_solar`.
6. Rank remaining hours (current hour..23) by price ascending; select cheapest `ceil(deficit_kwh)` hours.
7. Return `True` if the current hour is in that set.

---

## Installation

**Manual:** Copy `custom_components/ems/` to `<config>/custom_components/ems/` and restart Home Assistant.

**Requirements:** Home Assistant 2024.11+, Python 3.12+.

After restart, add the integration via **Settings → Devices & Services → Add Integration → EMS**.

---

## Configuration (config flow)

The integration is configured in 4 steps — no YAML editing required.

The **Configure** button (Settings → Devices & Services → EMS → Configure) walks the same 4 steps with current values prefilled.

### Step 1 — Inverter

| Config key | Type | Required | Description |
|------------|------|----------|-------------|
| `pv_power` | `sensor` | **Yes** | Current PV generation (W) |
| `inverter_select` | select entity | No | HA select entity that controls inverter operating mode |
| `inverter_disabled_option` | text | No | Option string written to the inverter select when EV is idle (e.g. `"Self Use"`) |
| `inverter_power_control_option` | text | No | Option string written to the inverter select when EV is charging (e.g. `"Power Control"`) |
| `active_power_entity` | `number` / `input_number` | No | EMS writes computed active power (W) to this entity. Negative = inverter drains PV → AC; `0` = hold battery. |

### Step 2 — EV Charger

| Config key | Type | Required | Description |
|------------|------|----------|-------------|
| `ev_power` | `sensor` | **Yes** | Current EV charger power (W) |
| `ev_plug` | binary_sensor / input_boolean | No | Whether the EV is plugged in. When not configured EMS assumes always plugged. |
| `ev_soc` | `sensor` | No | EV battery state of charge (%) |
| `ev_target_soc` | `number` / `input_number` / `sensor` | No | SoC at which EV charging stops; reads entity at runtime; falls back to `80` % |
| `ev_charge_target_entity` | `number` / `input_number` | No | EMS writes the computed charge power (W) to this entity each cycle |
| `ev_max_charge_w` | `number` / `input_number` | No | Physical maximum output of the EV charger (W) |
| `price_signal_entity` | binary_sensor / input_boolean | No | Cheap-hour signal for `Price-optimised` mode. EMS charges only when `on`. |

### Step 3 — Battery Management

All fields optional. When not configured the corresponding battery features are disabled.

| Config key | Type | Description |
|------------|------|-------------|
| `battery_soc` | `sensor` | Current home battery state of charge (%) |
| `battery_capacity` | `sensor` / `number` / `input_number` | Total usable battery capacity. EMS reads `unit_of_measurement` and converts automatically — use **Wh** or **kWh**. |
| `solar_forecast_remaining` | `sensor` / `number` / `input_number` | Expected remaining solar generation today. EMS reads `unit_of_measurement` and converts automatically — use **Wh** or **kWh**. |
| `house_load_forecast_remaining` | `sensor` / `number` / `input_number` | Expected remaining house load today. EMS reads `unit_of_measurement` and converts automatically — use **Wh** or **kWh**. When absent, house load is ignored in the forecast comparison. |
| `prices_json_entity` | `sensor` | Sensor whose **state** is a JSON list of 24 floats (€/kWh or €ct/kWh) indexed by hour 0..23. Must be exactly 24 elements. Example: `[0.08, 0.07, 0.07, 0.08, 0.10, 0.14, 0.18, 0.21, 0.20, 0.17, 0.15, 0.14, 0.13, 0.14, 0.15, 0.17, 0.21, 0.25, 0.24, 0.22, 0.19, 0.16, 0.13, 0.10]` (hour 0 = midnight, hour 23 = 23:00). Only relative ordering matters so any consistent unit works. |

### Step 4 — Grid

| Config key | Entity domain | Required | Description |
|------------|--------------|----------|-------------|
| `grid_import_power` | `sensor` | **Yes** | Current grid import power (W); positive = importing |
| `house_load` | `sensor` | **Yes** | Total house load including EV (W) |
| `max_grid_import_w` | `number` / `input_number` | No | Hard ceiling on grid import (W); reads entity at runtime; falls back to `4500` W |

---

## Runtime inputs

Every 10 seconds `coordinator.py::_build_inputs()` populates the following `Inputs` dataclass fields:

| Field | Source |
|-------|--------|
| `now` | System clock (`dt_util.utcnow()`) |
| `grid_import_w` | `grid_import_power` entity |
| `pv_w` | `pv_power` entity |
| `house_load_w` | `house_load` entity |
| `ev_plugged` | `ev_plug` entity; defaults to `True` (plugged) when not configured |
| `ev_power_w` | `ev_power` entity |
| `ev_soc` | `ev_soc` entity (%) or `None` |
| `ev_strategy` | `select.ems_ev_charging_mode` |
| `inverter_strategy` | `select.ems_inverter_control_mode` |
| `is_cheap_hour` | `price_signal_entity` (Step 2); `True` when not configured or unavailable |
| `battery_soc` | `battery_soc` entity (%) or `None` |
| `battery_capacity_kwh` | `battery_capacity` entity, auto-converted to kWh (reads `unit_of_measurement`), or `None` |
| `battery_target_soc` | `number.ems_battery_target_soc`; defaults to `80` % |
| `solar_forecast_remaining_kwh` | `solar_forecast_remaining` entity (kWh) or `None` |
| `house_load_forecast_remaining_kwh` | `house_load_forecast_remaining` entity (kWh) or `None` |
| `prices_today` | `prices_json_entity` state parsed as 24-float tuple, or `None` |
| `thresholds` | `max_grid_import_w` entity; `ev_target_soc` entity; `ev_min_charge_w` from `number.ems_ev_min_charge_w`; `max_ev_charge_w` entity; `ev_charge_advised_cheap_hours` from `number.ems_ev_charge_advised_cheap_hours` |
| `mapping` | Inverter select + active power + EV charge target entities from config |

The 1-second fast loop re-reads: `pv_w`, `house_load_w`, `ev_power_w`, `grid_import_w`, `ev_plugged`, `ev_strategy`, `inverter_strategy`, `is_cheap_hour`; battery fields are carried from the last slow-loop result.

Every configured input entity and every `select.ems_*` / `number.ems_*` entity is also subscribed via `async_track_state_change_event`. A state change on any of them fires an immediate `async_request_refresh()` on the strategy loop, so threshold knobs, SoC sensors, price/forecast entities, and mode selects take effect on the next event-loop tick — not at the next 10-second boundary.

---

## Tunable thresholds

| Where | Key / Entity | Default | Unit | Description |
|-------|-------------|---------|------|-------------|
| Config flow Step 4 (entity selector) | `max_grid_import_w` | 4500 | W | Hard ceiling on grid import; used for EV headroom and peak shaving trigger |
| Config flow Step 2 (entity selector) | `ev_target_soc` | 80 | % | EV SoC at which charging stops |
| Config flow Step 2 (entity selector) | `ev_max_charge_w` | — | W | Physical maximum output of the EV charger; uncapped when not set |
| `number.ems_ev_min_charge_w` | — | 1400 | W | Minimum EV charge target; below this the EV stays idle |
| `number.ems_battery_target_soc` | — | 80 | % | Target home battery SoC to reach by end of day; used by EOD suppress logic and the `Energy-optimised` battery priority scale |
| `number.ems_ev_charge_advised_cheap_hours` | — | 4 | h | How many of the cheapest remaining hours of today count as "good time to charge" for the plug-in hint sensor |

---

## EV charging mode

| Entity | Options | Description |
|--------|---------|-------------|
| `select.ems_ev_charging_mode` | `Grid-optimised`, `Price-optimised`, `Energy-optimised`, `Off` | See below |
| `select.ems_ev_smoothing` | `Fast`, `Normal`, `Slow` | Controls the EMA alpha applied to the EV charge target in the 1-second fast loop. `Fast` (α=0.3, ~3 s settling), `Normal` (α=0.2, ~5 s), `Slow` (α=0.1, ~10 s). Default: `Fast`. |

### Mode details

| Mode | EV charges when | Battery |
|------|----------------|---------|
| `Grid-optimised` | Grid headroom available (`max_grid_import_w − grid_import_w + last_target > ev_min_charge_w`) | Best-effort (may not reach target SoC) |
| `Price-optimised` | Same as Grid-optimised AND `price_signal_entity` is `on` | Best-effort |
| `Energy-optimised` | Instantaneous PV surplus (`pv − non_ev_load`) × battery priority scale ≥ `ev_min_charge_w` | **Prioritised** — scale 0..1 based on predicted EOD battery SoC |
| `Off` | Never | Best-effort (EV writes 0 W to charger) |

`Energy-optimised` charges from real-time PV surplus scaled by how confident EMS is that the home battery will reach `battery_target_soc` by end of day. If the forecast predicts the battery will fill comfortably, the EV gets 100% of surplus. As the gap widens (less solar remaining, more battery needed), the scale reduces linearly down to 0. When any forecast/battery input is missing, scale defaults to 1.0 (full surplus).

---

## Inverter control mode

| Entity | Options | Default | Description |
|--------|---------|---------|-------------|
| `select.ems_inverter_control_mode` | `Control Inverter`, `Off` | `Off` | `Control Inverter`: EMS applies the priority ladder every second. `Off`: EMS never writes to the inverter. EV charge target writes are unaffected. |

---

## Outputs (writes to your hardware)

`controller.py` is the only module that calls HA services.

### Slow loop (10 s) — `controller.write_slow()`

| Service | Entity | Condition | Value |
|---------|--------|-----------|-------|
| `select.select_option` | Inverter select entity (Step 1) | `inverter_control_mode = Control Inverter` AND `inverter_verb is not None` | `power_control_option` or `disabled_option` |
| `number.set_value` | Active power entity (Step 1) | `inverter_control_mode = Control Inverter` AND `active_power_w is not None` | See priority ladder |
| `number.set_value` | EV charge target entity (Step 2) | Always | Computed EV target watts (0 when idle) |

### Fast loop (1 s) — `controller.write_fast()`

| Service | Entity | Condition | Value |
|---------|--------|-----------|-------|
| `number.set_value` | Active power entity (Step 1) | `inverter_control_mode = Control Inverter` AND `active_power_w is not None` | Smoothed active power |
| `number.set_value` | EV charge target entity (Step 2) | Always | Smoothed EV target watts (0 when idle) |

Active power and EV target are cached — a service call is skipped if the value has not changed. Writes are skipped during the 60-second startup grace period.

---

## Exposed entities

### Select entities

| Entity | Options | Default | Description |
|--------|---------|---------|-------------|
| `select.ems_ev_charging_mode` | `Grid-optimised`, `Price-optimised`, `Energy-optimised`, `Off` | `Off` | EV charging strategy |
| `select.ems_ev_smoothing` | `Fast`, `Normal`, `Slow` | `Fast` | EMA smoothing speed for EV charge target in the 1 s loop |
| `select.ems_inverter_control_mode` | `Control Inverter`, `Off` | `Off` | Whether EMS controls the inverter select entity |

### Number entities (`number.ems_*`)

| Entity | Default | Unit | Description |
|--------|---------|------|-------------|
| `number.ems_ev_min_charge_w` | 1400 | W | Minimum EV charge power; below this the EV stays idle |
| `number.ems_battery_target_soc` | 80 | % | Target home battery SoC to reach by end of day |

### Binary sensor entities (`binary_sensor.ems_*`)

| Entity | On when | Off when | Description |
|--------|---------|----------|-------------|
| `binary_sensor.ems_ev_charge_advised` | EV is **not** plugged in AND (`price_signal_entity` is `on` OR current hour is among the cheapest `ems_ev_charge_advised_cheap_hours` of remaining hours today) | EV is plugged in, or it's not a cheap hour | Mode-agnostic advisory — fires regardless of `select.ems_ev_charging_mode`. Requires `price_signal_entity` or `prices_json_entity` to evaluate cheapness; if both are absent the sensor stays off. |

### Sensor entities (`sensor.ems_*`)

| Entity | Values | Description |
|--------|--------|-------------|
| `sensor.ems_ev_status` | prose string | Priority-ordered EV status with kW values and reason. Updated every second. |
| `sensor.ems_inverter_mode` | `power_control`, `disabled`, or `None` | Current inverter verb from the last `InverterDecision`. |
| `sensor.ems_ev_current_w` | float (W) | Computed EV charge target EMS is writing to `ev_charge_target_entity`. `0` when idle. |
| `sensor.ems_inverter_active_power` | float (W) | Active power EMS is writing to `active_power_entity`. |
| `sensor.ems_inverter_status` | see below | Explains why and how EMS is (or isn't) controlling the inverter. Attributes include `reason`, `formula`, `pv_w`, `house_load_w`, `ev_power_w`, `house_non_ev_w`, `active_power_w`. |
| `sensor.ems_battery_status` | prose string | Priority-ordered battery status (Reason > Action > SoC). Surfaces peak shaving, EOD hold, and cheap-hour hold before raw power-balance action. |

#### `sensor.ems_ev_status` messages (priority order)

| Message | Condition |
|---------|-----------|
| `Starting up…` | 60-second startup grace; writes suppressed |
| `EV charging disabled` | EV charging mode is `Off` |
| `EV not connected` | EV plug sensor reports not connected |
| `EV battery full (N% reached)` | EV SoC is at or above the configured target |
| `Waiting for cheap hours` | Mode is `Price-optimised` and current hour is not cheap |
| `Waiting for solar surplus` | Mode is `Energy-optimised` and forecast surplus doesn't exceed battery gap |
| `Not enough surplus to charge` | Available headroom is below the minimum charge threshold |
| `Charging EV at N.N kW from solar surplus` | Actively charging; grid import ≤ 0 (exporting or balanced) |
| `Charging EV at N.N kW with grid headroom` | Actively charging; grid import > 0 (using allowed headroom) |

#### `sensor.ems_inverter_status` values

| Value | Meaning |
|-------|---------|
| `Controlling` | EV charging commanded; inverter in power-control mode, active power written |
| `Peak shaving` | Grid import exceeded cap; battery discharging to cover excess |
| `Holding battery` | Deficit hold active; battery discharge suppressed so cheap grid covers house load |
| `Idle` | No active condition; inverter set to its `disabled_option` |
| `Disabled` | Inverter Control Mode is `Off`; EMS does not write to the inverter |
| `Starting up` | Startup grace period; strategy computes but writes are suppressed |

#### `sensor.ems_battery_status` messages (priority order)

Reason rows (1–3) override the power-balance rows whenever EMS has an explicit control intent.

| Message | Priority | Condition |
|---------|----------|-----------|
| `Starting up…` | 1 | 60-second startup grace |
| `Discharging to limit grid import · N%` | 2 | Inverter status is `peak_shaving` |
| `Drawing cheap grid · N% (R of N cheap hours today)` | 3 | Inverter status is `holding_battery`; house load drawn from cheap grid to preserve battery |
| `Battery full — N% reached` | 4 | `battery_soc ≥ battery_target_soc` and power balance within 200 W |
| `Charging from solar at N.N kW · N%` | 5 | `pv_w + grid_import_w − house_load_w > +200` |
| `Powering house at N.N kW · N%` | 6 | `pv_w + grid_import_w − house_load_w < −200` |
| `Idle · N%` | 7 | Power balance within ±200 W |

SoC suffix (` · N%`) is omitted when `battery_soc` is unavailable. The `(R of N cheap hours today)` rank suffix is omitted when price data is unavailable.

A `· solar short by X.X kWh` suffix is appended to rows 5–7 when the remaining solar forecast (minus forecast house load) won't fully charge the battery to `battery_target_soc`. Hidden when forecast inputs are unavailable or solar covers the gap.

---

## Development

```powershell
pytest tests/                  # Run all tests (80% coverage gate)
pytest tests/test_strategy.py `
  --cov=custom_components.ems.strategy `
  --cov-branch --cov-fail-under=100  # 100% coverage for pure module
python -m ruff check custom_components/ems tests  # Lint
```

CI runs lint + tests (`.github/workflows/test.yml`) and `hassfest` (`.github/workflows/validate.yml`) via GitHub Actions.
