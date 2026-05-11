# EMS — Energy Management System

A brand-agnostic Home Assistant custom integration for **EV charging and battery management**. It decides how many watts your EV should charge at every second using solar surplus and available grid headroom, enforces a maximum grid import cap, prevents battery power from flowing to the EV, shaves grid import peaks with battery discharge, and steers the home battery toward a target SoC by end of day.

Works with any inverter that exposes a `select` entity for operating modes and a `number` entity for active power control — no inverter-specific code.

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

### 1-second fast loop (`EmsFastLoopCoordinator`)
Always runs once the integration is active. Re-reads `pv_w`, `house_load_w`, `ev_power_w`, `grid_import_w`, `ev_plugged`, `ev_strategy`, `inverter_strategy`, and `is_cheap_hour`, then runs `strategy.recompute()` and `inverter_control.recompute()`. Calls `controller.write_fast()` — which writes active power and EV target but **never writes the inverter verb** (verb enforcement is the slow loop's job).

### EV algorithm — power balance

```
house_non_ev   = max(0, house_load_w − ev_power_w)
feedback_w     = last_ev_target_w  ← last value commanded to wallbox (ev_power_w on cold start)
grid_target    = 0  (Energy-optimised) or max_grid_import_w (all other modes)
ev_target_w    = max(0, grid_target − grid_import_w + feedback_w)
ev_target_w    = min(ev_target_w, max_ev_charge_w)  ← if max_ev_charge_w is configured

# Hard gates → idle (evaluated in priority order)
if ev_strategy == "Off":                                                    → charging_disabled
if not ev_plugged:                                                          → ev_not_connected
if ev_strategy == "Price-optimised" and not is_cheap_hour:                  → outside_cheap_hours
if ev_strategy == "Energy-optimised" and not forecast_surplus > battery_gap:→ awaiting_solar_surplus
if ev_soc is not None and ev_soc ≥ ev_target_soc:                          → target_soc_reached

# Below minimum → idle
elif ev_target_w < ev_min_charge_w:                                         → not_enough_power

# Charge
else:
    → ev_charging (ev_target_w)
```

Integrating closed-loop: using the last commanded target (`last_ev_target_w`) as feedback makes the formula a pure integrator on grid error — it keeps nudging the EV target until `grid_import_w = grid_target`, regardless of wallbox current quantisation or ramp lag.

### Inverter control algorithm — priority ladder

Controlled by `select.ems_inverter_control_mode`. When set to **`Off`**, EMS never touches the inverter. When set to **`Control Inverter`**, the following four cases are evaluated in strict priority order each second:

| Priority | Condition | `active_power_w` | Status |
|---|---|---|---|
| 1 | `grid_import_w > max_grid_import_w` AND EV idle | `-(pv_w + (grid_import_w − max_grid_import_w))` | `peak_shaving` |
| 2 | EV charging commanded | `-min(pv_w, house_load_w)` | `controlling` |
| 3 | EOD suppress active AND `pv_w > 0` AND EV idle | `0` | `holding_battery` |
| 4 | otherwise | not written | `idle` |

**Peak shaving (priority 1):** when grid import spikes above the cap and the EV is not charging, the battery discharges to cover the excess. The EV must be idle to satisfy the no-battery-to-EV constraint. Responds within 1 second via the fast loop.

**EV charging (priority 2):** directs all available PV to AC (`-min(pv_w, house_load_w)`). Battery is neither charged from AC (sign always ≤ 0) nor drained for the EV (magnitude capped by `pv_w`).

**EOD suppress (priority 3):** during cheap hours (identified from the hourly prices JSON) when solar forecast alone won't fill the battery to the target SoC, EMS sets `active_power_w = 0` so PV charges the battery instead of the battery discharging to cover house load. Only active during PV production. Peak shaving overrides it if a grid spike occurs.

**Battery protection:** `active_power_w` is always ≤ 0 — battery is never commanded to charge from AC. The gate is the **commanded** EV state, not the measured `ev_power_w`, preventing start-of-session deadlock.

### EOD battery fullness decision

Each 10-second cycle, `strategy.is_eod_suppress_hour(inputs)` determines whether the current hour should suppress battery discharge:

1. Return `False` if any of `battery_soc`, `battery_capacity_kwh`, `solar_forecast_remaining_kwh`, or `prices_today` is `None` (feature disabled).
2. `battery_gap_kwh = battery_capacity_kwh × max(0, (battery_target_soc − battery_soc) / 100)`.
3. `effective_solar = solar_forecast_remaining_kwh − (house_load_forecast_remaining_kwh or 0)`.
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
| `battery_capacity` | `sensor` / `number` / `input_number` | Total usable battery capacity (kWh) |
| `solar_forecast_remaining` | `sensor` / `number` / `input_number` | Expected remaining solar generation today (kWh) — Solcast-style entity |
| `house_load_forecast_remaining` | `sensor` / `number` / `input_number` | Expected remaining house load today (kWh); when absent house load is ignored in the forecast comparison |
| `prices_json_entity` | `sensor` | Sensor whose **state** is a JSON list of 24 floats (€/kWh) indexed by hour 0..23 |

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
| `battery_capacity_kwh` | `battery_capacity` entity (kWh) or `None` |
| `battery_target_soc` | `number.ems_battery_target_soc`; defaults to `80` % |
| `solar_forecast_remaining_kwh` | `solar_forecast_remaining` entity (kWh) or `None` |
| `house_load_forecast_remaining_kwh` | `house_load_forecast_remaining` entity (kWh) or `None` |
| `prices_today` | `prices_json_entity` state parsed as 24-float tuple, or `None` |
| `thresholds` | `max_grid_import_w` entity; `ev_target_soc` entity; `ev_min_charge_w` from `number.ems_ev_min_charge_w`; `max_ev_charge_w` entity |
| `mapping` | Inverter select + active power + EV charge target entities from config |

The 1-second fast loop re-reads: `pv_w`, `house_load_w`, `ev_power_w`, `grid_import_w`, `ev_plugged`, `ev_strategy`, `inverter_strategy`, `is_cheap_hour`; battery fields are carried from the last 10 s result.

---

## Tunable thresholds

| Where | Key / Entity | Default | Unit | Description |
|-------|-------------|---------|------|-------------|
| Config flow Step 4 (entity selector) | `max_grid_import_w` | 4500 | W | Hard ceiling on grid import; used for EV headroom and peak shaving trigger |
| Config flow Step 2 (entity selector) | `ev_target_soc` | 80 | % | EV SoC at which charging stops |
| Config flow Step 2 (entity selector) | `ev_max_charge_w` | — | W | Physical maximum output of the EV charger; uncapped when not set |
| `number.ems_ev_min_charge_w` | — | 1400 | W | Minimum EV charge target; below this the EV stays idle |
| `number.ems_battery_target_soc` | — | 80 | % | Target home battery SoC to reach by end of day; used by EOD suppress and `Energy-optimised` EV gate |

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
| `Energy-optimised` | Solar forecast surplus exceeds battery gap AND real-time export (grid is exporting) ≥ 1400 W | **Prioritised** — EV only charges when surplus above battery need |
| `Off` | Never | Best-effort (EV writes 0 W to charger) |

`Energy-optimised` drives the closed loop with `grid_target = 0` W (absorbs solar export only). EV charges during peak solar hours and stops automatically as forecast surplus narrows to cover the battery gap.

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

### Sensor entities (`sensor.ems_*`)

| Entity | Values | Description |
|--------|--------|-------------|
| `sensor.ems_ev_charging_state` | see below | Human-readable EV charging state. Updated every second. |
| `sensor.ems_inverter_mode` | `power_control`, `disabled`, or `None` | Current inverter verb from the last `InverterDecision`. |
| `sensor.ems_ev_current_w` | float (W) | Computed EV charge target EMS is writing to `ev_charge_target_entity`. `0` when idle. |
| `sensor.ems_inverter_active_power` | float (W) | Active power EMS is writing to `active_power_entity`. |
| `sensor.ems_inverter_status` | see below | Explains why and how EMS is (or isn't) controlling the inverter. Attributes include `reason`, `formula`, `pv_w`, `house_load_w`, `ev_power_w`, `house_non_ev_w`, `active_power_w`. |

#### `sensor.ems_ev_charging_state` values

| Value | Meaning |
|-------|---------|
| `Charging` | EV is actively charging under EMS control |
| `EV not connected` | EV plug sensor reports not connected |
| `Charging disabled` | EV charging mode is set to `Off` |
| `Target SoC reached` | EV SoC is at or above the configured target |
| `Not enough power` | Available grid headroom is below the minimum charge threshold |
| `Starting up` | EMS is in the 60-second startup grace period; no writes yet |
| `Outside cheap hours` | Mode is `Price-optimised` and the price signal entity is `off` |
| `Awaiting solar surplus` | Mode is `Energy-optimised` and solar forecast surplus does not exceed battery gap |

#### `sensor.ems_inverter_status` values

| Value | Meaning |
|-------|---------|
| `Controlling` | EV charging commanded; inverter in power-control mode, active power written |
| `Peak shaving` | Grid import exceeded cap; battery discharging to cover excess |
| `Holding battery` | Cheap hour with PV producing; discharge suppressed so PV charges battery |
| `Idle` | No active condition; inverter set to its `disabled_option` |
| `Disabled` | Inverter Control Mode is `Off`; EMS does not write to the inverter |
| `Starting up` | Startup grace period; strategy computes but writes are suppressed |

---

## Development

```powershell
pytest tests/                  # Run all tests (80% coverage gate)
pytest tests/test_strategy.py `
  --cov=custom_components.ems.strategy `
  --cov-branch --cov-fail-under=100  # 100% coverage for pure module
python -m ruff check custom_components/ems tests  # Lint
```

CI runs `hassfest` via GitHub Actions (`.github/workflows/validate.yml`).
