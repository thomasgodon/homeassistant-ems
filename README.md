# EMS — Energy Management System

A brand-agnostic Home Assistant custom integration for solar + battery + EV setups. It runs a **priority ladder** algorithm every 10 seconds to decide how to operate your inverter and EV charger, with a 1-second fast loop for peak shaving and a 5-minute scheduler for cheap EV charging windows.

Works with any hardware that exposes a `select` entity for operating modes and a `number` entity for power targets — no inverter-specific code.

---

## How it works

Three execution loops share a single `EmsState` object:

### 10-second strategy loop (`EmsStrategyCoordinator`)
1. `_build_inputs()` — reads all configured HA entities into a frozen `Inputs` dataclass.
2. `strategy.decide(inputs)` — pure function; evaluates the priority ladder and returns a `Decision`.
3. Updates the hysteresis latch `peak_risk_latched`.
4. Writes are skipped for `startup_grace_seconds` after startup to avoid flapping on `unavailable` sensors.
5. `controller.write_full()` — idempotent: calls `select.select_option()` on the inverter/EV charger and `number.set_value()` for power targets.

### 1-second peak-shave loop (`EmsPeakShaveCoordinator`)
Active only when `peak_risk_latched` is `true`. Re-reads grid, house load, and EV power, calls `strategy.recompute_peak_shave_power()`, and calls `controller.write_power_only()` — adjusts wattage only, never flips operating modes.

### 5-minute EV scheduler (`EvSchedulerOrchestrator`)
Calls `ev_scheduler.compute_window()` to pick the cheapest charging hours from the price forecast. Result stored in `EmsState.cached_window`; the 10-second loop reads `cached_window.in_window_now`.

### Priority ladder order

| Priority | Rule | Trigger |
|----------|------|---------|
| 1 | **Peak shave** | Grid import ≥ `max_grid_import_w` |
| 2 | **Battery floor** | Battery SoC ≤ `battery_reserve_soc` |
| 3 | **EV charging coupling** | EV plugged, in cheap window, SoC < target |
| 4 | **Export curtail** | Export price ≤ `export_curtail_price` |
| 5 | **Proactive charge** | Import price ≤ `proactive_grid_charge_price` |
| 6 | **Solar to battery/EV** | Solar surplus ≥ `solar_surplus_threshold_w` |
| 7 | **Expensive self-use** | Import price > `avg_24h_price × multiplier` |
| 8 | **Fallback** | None of the above |

---

## Installation

**Manual:** Copy `custom_components/ems/` to `<config>/custom_components/ems/` and restart Home Assistant.

**Requirements:** Home Assistant 2024.11+, Python 3.12+.

After restart, add the integration via **Settings → Devices & Services → Add Integration → EMS**.

---

## Configuration (config flow)

The integration is configured in 5 steps — no YAML editing required.

### Step 1 — Required sensors

| Config key | Entity domain | Required | Description |
|------------|--------------|----------|-------------|
| `grid_import_power` | `sensor` | **Yes** | Current grid import power (W) |
| `grid_import_15m_avg` | `sensor` | No | 15-minute rolling average of grid import (W); used for peak-shave reference |
| `pv_power` | `sensor` | **Yes** | Current PV generation (W) |
| `house_load` | `sensor` | **Yes** | Total house load (W), EV included |
| `house_load_non_ev` | `sensor` | No | House load excluding EV (W); falls back to `house_load − ev_power` if absent |
| `battery_soc` | `sensor` | **Yes** | Battery state of charge (%) |
| `battery_power` | `sensor` | **Yes** | Battery power (W); positive = charging, negative = discharging |

### Step 2 — Inverter

Map abstract verbs to the exact option strings your inverter's select entity uses. Leave blank for unsupported verbs.

| Config key | Type | Description |
|------------|------|-------------|
| `inverter_select` | select entity | The HA select entity that controls inverter operating mode |
| `inverter_verb_self_use` | text | Option string for `SELF_USE` (normal solar-to-load mode) |
| `inverter_verb_force_charge` | text | Option string for `FORCE_CHARGE` (charge battery from grid) |
| `inverter_verb_force_discharge` | text | Option string for `FORCE_DISCHARGE` (discharge battery to grid/load) |
| `inverter_verb_limit_export` | text | Option string for `LIMIT_EXPORT` (cap power exported) |
| `inverter_verb_idle` | text | Option string for `IDLE` (standby) |
| `inverter_power_target` | number entity | HA number entity (W) the integration writes power targets to |

### Step 3 — EV charger

All optional. Skip entirely if you have no managed EV charger.

| Config key | Type | Description |
|------------|------|-------------|
| `ev_select` | select entity | The HA select entity that controls EV charger mode |
| `ev_verb_charge_now` | text | Option string for `CHARGE_NOW` |
| `ev_verb_solar_only` | text | Option string for `SOLAR_ONLY` |
| `ev_verb_solar_plus_battery` | text | Option string for `SOLAR_PLUS_BATTERY` |
| `ev_verb_idle` | text | Option string for `IDLE` |
| `ev_power` | sensor entity | Current EV charging power (W) |
| `ev_plug` | binary_sensor / input_boolean | Whether the EV is plugged in |
| `ev_soc` | sensor entity | EV battery state of charge (%) |
| `ev_capacity_kwh` | number (kWh) | EV battery capacity; default `60.0` |
| `ev_max_charge_kw` | number (kW) | EV maximum charge rate; default `7.4` |
| `ev_max_power_entity` | number entity | HA number entity the integration writes to cap EV power during peak-shave |

### Step 4 — Price sources

| Config key | Type | Description |
|------------|------|-------------|
| `import_price` | sensor entity | Current electricity import price (EUR/kWh) |
| `export_price` | sensor entity | Current electricity export price (EUR/kWh) |
| `forecast_path_import` | text (attribute path) | Dot-separated attribute path on `import_price` sensor that contains the forecast list (e.g. `attributes.forecast`) |
| `forecast_path_export` | text (attribute path) | Dot-separated attribute path on `export_price` sensor for export forecast list |

### Step 5 — Forecasts

| Config key | Type | Description |
|------------|------|-------------|
| `pv_forecast_kwh` | sensor entity | Remaining PV energy forecast for today (kWh) |
| `load_forecast_kwh` | sensor entity | Remaining house load forecast for today (kWh) |

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
| `house_load_non_ev_w` | `house_load_non_ev` entity; fallback: `house_load_w − ev_power_w` |
| `battery_soc` | `battery_soc` entity (%) |
| `battery_power_w` | `battery_power` entity; positive = charging |
| `ev_plugged` | `ev_plug` entity |
| `ev_power_w` | `ev_power` entity |
| `ev_soc` | `ev_soc` entity (%) or `None` |
| `ev_capacity_kwh` | `ev_capacity_kwh` config scalar |
| `ev_max_charge_kw` | `ev_max_charge_kw` config scalar |
| `ev_ready_by` | `time.ems_ev_ready_by` |
| `ev_strategy` | `select.ems_ev_scheduling_strategy` |
| `import_price` | `import_price` entity (EUR/kWh) or `None` |
| `export_price` | `export_price` entity (EUR/kWh) or `None` |
| `avg_24h_price` | `sensor.ems_24h_avg_price` (computed by integration) or `None` |
| `in_cheap_window` | `EmsState.cached_window.in_window_now` (set by EV scheduler) |
| `pv_forecast_remaining_kwh` | `pv_forecast_kwh` entity or `None` |
| `house_load_forecast_kwh` | `load_forecast_kwh` entity or `None` |
| `is_daytime` | `sun.sun` state |
| `features` | Feature booleans read from `switch.ems_*` runtime switch entities |
| `thresholds` | 12 `number.ems_*` entities (see Tunable thresholds) |
| `mapping` | Inverter/EV select entities + verb→option maps from config (Steps 2–3) |
| `peak_risk_latched` | Persisted hysteresis latch from `ems_state.json` |

The 1-second peak-shave loop re-reads only: `grid_import_w`, `grid_import_15m_avg_w`, `house_load_w`, `house_load_non_ev_w`, `ev_power_w`.

---

## Tunable thresholds

All thresholds are HA `number` entities — adjust them live from the UI without restarting.

| Entity | Default | Unit | Description |
|--------|---------|------|-------------|
| `number.ems_max_grid_import_w` | 4500 | W | Hard ceiling on grid import; peak-shave activates when this is exceeded |
| `number.ems_battery_reserve_soc` | 10 | % | SoC floor; battery will not discharge below this |
| `number.ems_battery_target_soc_sunset` | 80 | % | Target SoC to reach by sunset (used with forecast planning) |
| `number.ems_battery_capacity_kwh` | 10 | kWh | Total usable battery capacity |
| `number.ems_battery_max_discharge_w` | 3000 | W | Maximum battery discharge rate |
| `number.ems_battery_full_soc` | 95 | % | SoC considered "full" |
| `number.ems_proactive_grid_charge_price` | 0.05 | EUR/kWh | Import price at or below which proactive grid charging triggers |
| `number.ems_expensive_price_multiplier` | 1.3 | × | Current import must exceed `avg_24h_price × multiplier` to be classed as expensive |
| `number.ems_export_curtail_price` | 0.0 | EUR/kWh | Export price at or below which export curtailment triggers |
| `number.ems_ev_target_soc` | 80 | % | EV SoC target for scheduling and kWh-needed calculations |
| `number.ems_peak_shave_headroom_w` | 300 | W | Safety margin kept below `max_grid_import_w` during peak-shave control |
| `number.ems_solar_surplus_threshold_w` | 100 | W | Minimum solar surplus (W) before solar-to-EV/battery rules activate |
| `number.ems_ev_max_power_w` | 7400 | W | EV charge power cap written during peak-shave |
| `number.ems_strategy_interval_s` | 10 | s | Strategy loop interval |
| `number.ems_peak_shave_interval_s` | 1 | s | Peak-shave loop interval |
| `number.ems_startup_grace_seconds` | 45 | s | No writes to hardware during this window after startup |

---

## Runtime switches

Toggle individual EMS features from the HA dashboard without touching the config flow. State is restored across restarts. Existing config-flow values (from a prior Step 6) are used as the initial default on first load.

| Switch | Default | Effect when off |
|--------|---------|-----------------|
| `switch.ems_peak_shaving` | on | Disables the peak-shave rule (`_rule_peak_shave` → skip) |
| `switch.ems_battery_grid_charging` | on | Disables proactive grid-to-battery charging (the only place `FORCE_CHARGE` is issued) |
| `switch.ems_ev_charging` | on | Disables all EV control: EV-coupling rule is skipped and EV verb is forced to `IDLE`. **Side effect:** if the EV charges via an external source while this switch is off, the battery may discharge to feed it since the coupling rule is not active. |
| `switch.ems_export_curtailment` | off | Disables export curtailment rule |
| `switch.ems_ev_absorbs_curtailment` | on | When export curtailment is active, stops directing surplus to the EV charger |
| `switch.ems_forecast_planning` | off | Reserved — flag is propagated to `Features.forecast_planning` but no rule currently reads it |

## Modes and schedules

| Entity | Options | Description |
|--------|---------|-------------|
| `select.ems_ev_scheduling_strategy` | `Deadline`, `Price-optimised`, `Solar-only`, `Off` | How the EV scheduler picks the charging window |
| `time.ems_ev_ready_by` | time (default `07:00`) | Target time by which the EV should reach `ev_target_soc` |

---

## Outputs (writes to your hardware)

`controller.py` is the only module that calls HA services. All writes are idempotent — the last written value is cached and a call is skipped if the value has not changed.

### Full write (`controller.write_full`) — called every 10 s
1. `select.select_option` on the configured **inverter select entity** with the resolved option string for `decision.inverter_verb`.
2. `number.set_value` on the configured **inverter power target entity** with `decision.inverter_power_target_w`.
3. `select.select_option` on the configured **EV select entity** with the resolved option string for `decision.ev_verb`.
4. `number.set_value` on the configured **EV max power entity** with `decision.ev_max_power_w`.

Full writes are skipped during the startup grace period.

### Power-only write (`controller.write_power_only`) — called every 1 s (peak-shave only)
Steps 2 and 4 only — adjusts wattage without changing operating modes.

---

## Exposed entities

### Sensors (`sensor.ems_*`)

| Entity | Unit | Description |
|--------|------|-------------|
| `sensor.ems_current_strategy` | — | Active ladder rule (`peak_shave`, `battery_floor`, `ev_charging_coupling`, `export_curtail`, `proactive_charge`, `solar_to_battery`, `solar_to_ev`, `expensive_self_use`, `fallback`, `starting_up`) |
| `sensor.ems_solar_surplus_w` | W | `max(0, pv_w − house_load_w)` |
| `sensor.ems_ev_kwh_needed` | kWh | Energy needed to reach `ev_target_soc` |
| `sensor.ems_cheapest_window_start` | timestamp | Start of the next cheap EV charging window |
| `sensor.ems_cheapest_window_end` | timestamp | End of the next cheap EV charging window |
| `sensor.ems_expected_battery_deficit_evening_kwh` | kWh | Projected deficit at end of day given forecasts |
| `sensor.ems_24h_avg_price` | EUR/kWh | Rolling 24 h average import price |
| `sensor.ems_inverter_target_verb` | — | Abstract verb last sent to inverter (`SELF_USE`, `FORCE_CHARGE`, etc.) |
| `sensor.ems_ev_target_verb` | — | Abstract verb last sent to EV charger |
| `sensor.ems_inverter_power_target_w` | W | Power target last written to inverter |

### Binary sensors (`binary_sensor.ems_*`)

| Entity | Description |
|--------|-------------|
| `binary_sensor.ems_peak_risk` | `true` when the peak-shave hysteresis latch is active |
| `binary_sensor.ems_in_ev_charging_window` | `true` when in cheap window, EV plugged, SoC < target |
| `binary_sensor.ems_cheap_price_now` | `true` when `import_price ≤ proactive_grid_charge_price` |
| `binary_sensor.ems_expensive_price_now` | `true` when `import_price > avg_24h_price × multiplier` |
| `binary_sensor.ems_battery_at_reserve` | `true` when `battery_soc ≤ battery_reserve_soc` |
| `binary_sensor.ems_export_at_loss` | `true` when `export_price ≤ export_curtail_price` |
| `binary_sensor.ems_curtailing_export` | `true` when the active strategy is `export_curtail` |

---

## Persistence

`homeassistant.helpers.storage.Store` writes `ems_state.json` (key `ems_state`, version 1) after every strategy tick:

| Field | Type | Description |
|-------|------|-------------|
| `peak_risk_latched` | bool | Hysteresis latch; prevents rapid on/off cycling of peak-shave |
| `cached_window` | object or null | `{start, end (ISO), in_window_now, reason}` — last computed EV charging window |

State is restored on startup so a HA restart does not reset the latch or discard the charging window.

---

## Development

```powershell
pytest tests/                  # Run all tests (80% coverage gate)
pytest tests/test_strategy.py tests/test_ev_scheduler.py `
  --cov=custom_components.ems.strategy `
  --cov=custom_components.ems.ev_scheduler `
  --cov-branch --cov-fail-under=100  # 100% coverage for pure modules
ruff check                     # Lint
```

CI runs `hassfest` via GitHub Actions (`.github/workflows/validate.yml`).
