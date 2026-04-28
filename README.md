# EMS — Energy Management System

A brand-agnostic Home Assistant custom integration for **EV charging management**. It decides how many watts your EV should charge at every second, using solar surplus or cheap grid windows, while enforcing a maximum grid import cap and preventing the home battery from being drained into the car.

Works with any inverter that exposes a `select` entity for operating modes and a `number` entity for power targets — no inverter-specific code.

---

## How it works

Three execution loops share a single `EmsState` object:

### 10-second strategy loop (`EmsStrategyCoordinator`)
1. `_build_inputs()` — reads all configured HA entities into a frozen `Inputs` dataclass.
2. `strategy.decide(inputs)` — pure function; returns a `Decision` (EV target watts, inverter verb, inverter discharge cap).
3. Writes are skipped for `startup_grace_seconds` after startup to avoid flapping on `unavailable` sensors.
4. `controller.write_full()` — idempotent: writes inverter mode and discharge cap only when EV is actively charging.

### 1-second fast loop (`EmsFastLoopCoordinator`)
Always runs once the integration is active. Re-reads grid, house load, and EV power, runs the same `strategy.recompute()` formula, and calls `controller.write_full()`. This keeps the grid import cap enforced within one second when loads change suddenly (e.g. kettle comes on).

### 5-minute EV scheduler (`EvSchedulerOrchestrator`)
Calls `ev_scheduler.compute_window()` to pick the cheapest charging hours from the price forecast. Result stored in `EmsState.cached_window`; both loops read `cached_window.in_window_now`.

### Decision algorithm

```
house_non_ev = max(0.0, house_load_w − ev_power_w)
ev_max_w     = ev_max_charge_kw × 1000
surplus      = max(0.0, pv_w − house_non_ev)

# Hard gates → idle
if not ev_plugged
   or (ev_soc is not None and ev_soc ≥ ev_target_soc)
   or ev_strategy == "Off"
   or not switch.ems_ev_charging:
    → ev_idle (ev_charge_power_w = 0, no inverter writes)

# Cheap window (Deadline / Price-optimised)
elif ev_strategy != "Solar-only" and in_cheap_window:
    ev_target = clamp(max_grid_import_w + pv_w − house_non_ev, 0, ev_max_w)
    → ev_charging_grid

# Solar surplus path
elif surplus ≥ ev_min_charge_w:
    ev_target = min(surplus, ev_max_w)
    → ev_charging_solar

else:
    → ev_idle

# Inverter (only when ev_target > 0):
    inverter_verb           = SELF_USE
    inverter_power_target_w = max(0.0, house_non_ev − pv_w)  # discharge cap
```

When `ev_target = 0`, EMS writes nothing to the inverter and resets its write-cache.

---

## Installation

**Manual:** Copy `custom_components/ems/` to `<config>/custom_components/ems/` and restart Home Assistant.

**Requirements:** Home Assistant 2024.11+, Python 3.12+.

After restart, add the integration via **Settings → Devices & Services → Add Integration → EMS**.

---

## Configuration (config flow)

The integration is configured in 4 steps — no YAML editing required.

### Step 1 — Required sensors

| Config key | Entity domain | Required | Description |
|------------|--------------|----------|-------------|
| `grid_import_power` | `sensor` | **Yes** | Current grid import power (W) |
| `grid_import_15m_avg` | `sensor` | No | 15-minute rolling average of grid import (W) |
| `pv_power` | `sensor` | **Yes** | Current PV generation (W) |
| `house_load` | `sensor` | **Yes** | Total house load including EV (W) |
| `ev_power` | `sensor` | **Yes** | Current EV charger power (W) |

### Step 2 — Inverter (optional)

Skip this step if you have no home battery or do not want EMS to control the inverter.

When configured, EMS writes `SELF_USE` mode and a discharge cap (`max(0, house_non_ev − pv_w)`) whenever the EV is charging. This prevents the battery from being drained into the car while still allowing it to cover house load.

| Config key | Type | Description |
|------------|------|-------------|
| `inverter_select` | select entity | The HA select entity that controls inverter operating mode |
| `inverter_verb_self_use` | text | Option string for your inverter's normal / self-use mode |
| `inverter_power_target` | number entity | HA number entity (W) the integration writes the discharge cap to |

### Step 3 — EV charger

All fields optional. Skip entirely if you have no EV.

EMS does **not** write to your EV charger directly. It publishes `sensor.ems_ev_charge_target_power` (W) and you create your own HA automation to act on it.

| Config key | Type | Description |
|------------|------|-------------|
| `ev_plug` | binary_sensor / input_boolean | Whether the EV is plugged in |
| `ev_soc` | sensor entity | EV battery state of charge (%) |
| `ev_capacity_kwh` | number (kWh) | EV battery capacity; default `60.0` |
| `ev_max_charge_kw` | number (kW) | EV maximum charge rate; default `7.4` |

### Step 4 — Price sources

Required for Deadline and Price-optimised charging modes. Skip if you only use Solar-only or Off.

| Config key | Type | Description |
|------------|------|-------------|
| `import_price` | sensor entity | Current electricity import price (EUR/kWh) |
| `forecast_path_import` | text (attribute path) | Dot-separated attribute path on `import_price` sensor containing the forecast list (e.g. `attributes.forecast`) |

---

## Runtime inputs

Every 10 seconds `coordinator.py::_build_inputs()` populates the following `Inputs` dataclass fields:

| Field | Source |
|-------|--------|
| `now` | System clock (`dt_util.utcnow()`) |
| `grid_import_w` | `grid_import_power` entity |
| `grid_import_15m_avg_w` | `grid_import_15m_avg` entity or `None` |
| `pv_w` | `pv_power` entity |
| `house_load_w` | `house_load` entity |
| `ev_plugged` | `ev_plug` entity |
| `ev_power_w` | `ev_power` entity |
| `ev_soc` | `ev_soc` entity (%) or `None` |
| `ev_capacity_kwh` | `ev_capacity_kwh` config scalar |
| `ev_max_charge_kw` | `ev_max_charge_kw` config scalar |
| `ev_ready_by` | `time.ems_ev_ready_by` |
| `ev_strategy` | `select.ems_ev_scheduling_strategy` |
| `import_price` | `import_price` entity (EUR/kWh) or `None` |
| `in_cheap_window` | `EmsState.cached_window.in_window_now` (set by EV scheduler) |
| `features` | Feature boolean read from `switch.ems_ev_charging` |
| `thresholds` | 3 `number.ems_*` entities (see Tunable thresholds) |
| `mapping` | Inverter select entity + SELF_USE option string from config (Step 2) |

The 1-second fast loop re-reads only: `pv_w`, `house_load_w`, `ev_power_w`; remaining fields are reused from the last 10 s result.

---

## Tunable thresholds

All thresholds are HA `number` entities — adjust them live from the UI without restarting.

| Entity | Default | Unit | Description |
|--------|---------|------|-------------|
| `number.ems_max_grid_import_w` | 4500 | W | Hard ceiling on grid import; EV target is capped so grid draw stays at or below this |
| `number.ems_ev_target_soc` | 80 | % | EV SoC at which charging stops |
| `number.ems_ev_min_charge_w` | 1400 | W | Minimum solar surplus required to start EV charging; below this the EV gets 0 W |
| `number.ems_strategy_interval_s` | 10 | s | Strategy loop interval |
| `number.ems_fast_loop_interval_s` | 1 | s | Fast loop interval |
| `number.ems_startup_grace_seconds` | 45 | s | No writes to hardware during this window after startup |

---

## Runtime switches

| Switch | Default | Effect when off |
|--------|---------|-----------------|
| `switch.ems_ev_charging` | on | Forces `ev_charge_power_w = 0`; EMS writes nothing to inverter |

---

## Modes and schedules

| Entity | Options | Description |
|--------|---------|-------------|
| `select.ems_ev_scheduling_strategy` | `Deadline`, `Price-optimised`, `Solar-only`, `Off` | How the EV scheduler picks the charging window |
| `time.ems_ev_ready_by` | time (default `07:00`) | Target time by which the EV should reach `ev_target_soc` |

---

## EV charge target (`sensor.ems_ev_charge_target_power`)

EMS publishes this sensor and you wire your own HA automation to act on it (e.g. set your charger's current limit).

| Situation | Sensor value |
|-----------|-------------|
| EV not plugged | 0 W |
| `ev_strategy = Off` | 0 W |
| `ev_soc ≥ ev_target_soc` | 0 W |
| `switch.ems_ev_charging = off` | 0 W |
| In cheap window (Deadline / Price-optimised) | `clamp(max_grid_import_w + pv_w − house_non_ev_w, 0, ev_max_charge_kw × 1000)` |
| Solar surplus ≥ `ev_min_charge_w` | `min(pv_w − house_non_ev_w, ev_max_charge_kw × 1000)` |
| Solar surplus < `ev_min_charge_w`, outside cheap window | 0 W |

---

## Outputs (writes to your hardware)

`controller.py` is the only module that calls HA services. All writes are idempotent — the last written value is cached and a call is skipped if the value has not changed.

### When EV is charging (`ev_charge_power_w > 0`)

| Service | Entity | Value |
|---------|--------|-------|
| `select.select_option` | Inverter select entity (Step 2) | SELF_USE option string |
| `number.set_value` | Inverter power target entity (Step 2) | `max(0, house_non_ev_w − pv_w)` — discharge cap (W) |

Both writes are skipped during the startup grace period.

### When EV is idle (`ev_charge_power_w = 0`)

No writes are made. The controller's write-cache is cleared so the next active tick re-fires all writes.

---

## Exposed entities

### Sensors (`sensor.ems_*`)

| Entity | Unit | Description |
|--------|------|-------------|
| `sensor.ems_current_strategy` | — | Active label: `ev_charging_grid`, `ev_charging_solar`, `ev_idle`, `starting_up` |
| `sensor.ems_solar_surplus_w` | W | `max(0, pv_w − house_non_ev_w)` |
| `sensor.ems_ev_kwh_needed` | kWh | Energy needed to reach `ev_target_soc` |
| `sensor.ems_cheapest_window_start` | timestamp | Start of the next cheap EV charging window |
| `sensor.ems_cheapest_window_end` | timestamp | End of the next cheap EV charging window |
| `sensor.ems_inverter_target_verb` | — | Verb last sent to inverter (`SELF_USE`) |
| `sensor.ems_inverter_power_target_w` | W | Discharge cap last written to inverter |
| `sensor.ems_ev_charge_target_power` | W | Recommended EV charge rate (0 = idle); wire your own automation |

### Binary sensors (`binary_sensor.ems_*`)

| Entity | Description |
|--------|-------------|
| `binary_sensor.ems_in_ev_charging_window` | `true` when in cheap window, EV plugged, SoC < target |

---

## Persistence

`homeassistant.helpers.storage.Store` writes `ems_state.json` (key `ems_state`, version 1) after every strategy tick:

| Field | Type | Description |
|-------|------|-------------|
| `cached_window` | object or null | `{start, end (ISO), in_window_now, reason}` — last computed EV charging window |

State is restored on startup so a HA restart does not discard the charging window.

---

## Development

```powershell
pytest tests/                  # Run all tests (80% coverage gate)
pytest tests/test_strategy.py tests/test_ev_scheduler.py `
  --cov=custom_components.ems.strategy `
  --cov=custom_components.ems.ev_scheduler `
  --cov-branch --cov-fail-under=100  # 100% coverage for pure modules
python -m ruff check custom_components/ems tests  # Lint
```

CI runs `hassfest` via GitHub Actions (`.github/workflows/validate.yml`).
