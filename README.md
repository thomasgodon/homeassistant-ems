# EMS — Energy Management System

A brand-agnostic Home Assistant custom integration for **EV charging management**. It decides how many watts your EV should charge at every second using solar surplus and available grid headroom, while enforcing a maximum grid import cap and never using battery power for EV charging.

Works with any inverter that exposes a `select` entity for operating modes and a `number` entity for active power control — no inverter-specific code.

---

## How it works

Two execution loops share a single `EmsState` object:

### 10-second strategy loop (`EmsStrategyCoordinator`)
1. `_build_inputs()` — reads all configured HA entities into a frozen `Inputs` dataclass.
2. `strategy.decide(inputs)` — pure function; returns a `Decision` (EV target watts, inverter active power).
3. Writes are skipped for 60 seconds after startup to avoid flapping on `unavailable` sensors.
4. `controller.write_full()` — idempotent: switches inverter to `power_control` mode and writes active power cap only when `ev_power_w > 0` (EV confirmed drawing); always writes the computed EV charge target; **leaves the inverter untouched when `ev_power_w == 0`**.

### 1-second fast loop (`EmsFastLoopCoordinator`)
Always runs once the integration is active. Re-reads `pv_w`, `house_load_w`, `ev_power_w`, and `grid_import_w`, runs the same `strategy.recompute()` formula, and calls `controller.write_full()`. This keeps the grid import cap enforced within one second when loads change suddenly (e.g. a kettle comes on).

### Decision algorithm — power balance

```
house_non_ev   = max(0, house_load_w − ev_power_w)
active_power_w = pv_w − house_non_ev   ← negative: battery discharges for house; positive: PV surplus (charging into inverter)
ev_target_w    = max(0, max_grid_import_w − grid_import_w + ev_power_w)
ev_target_w    = min(ev_target_w, max_ev_charge_w)  ← if max_ev_charge_w is configured

# Hard gates → idle (evaluated in priority order)
if ev_strategy == "Off":                           → charging_disabled
if not ev_plugged:                                 → ev_not_connected
if ev_soc is not None and ev_soc ≥ ev_target_soc: → target_soc_reached

# Below minimum → idle
elif ev_target_w < ev_min_charge_w:               → not_enough_power

# Charge
else:
    → ev_charging (ev_target_w, active_power_w)
```

Closed-loop formula: `non_ev_grid_draw = grid_import_w − ev_power_w`; the EV target gets the remaining headroom up to `max_grid_import_w`. Adding `ev_power_w` back is essential — the EV's current draw is already inside the measured `grid_import_w`, so we're computing the new target, not an increment. This reacts to the actual measured grid import, so sensor offsets, untracked loads, and other discrepancies are automatically corrected.

`active_power_w` is written to the inverter's active power entity each cycle. Negative means the battery is discharging (draining from the inverter to cover house load shortfall); positive means PV has surplus and power is flowing into the inverter (e.g. battery charging, preventing grid export).

#### Worked examples

| Situation | active_power_w | ev_target_w |
|-----------|---------------|-------------|
| grid_import=−300W (exporting), pv=500W, house_non_ev=200W, ev=0, max=4500W | +300 W (PV surplus, charging into inverter) | 4800 W |
| grid_import=200W, pv=0W, house_non_ev=200W, ev=0, max=4500W | −200 W (battery discharges for house) | 4300 W |
| grid_import=50W, pv=150W, house_non_ev=200W, ev=0, max=4500W | −50 W (battery discharges for shortfall) | 4450 W |
| grid_import=3700W (under-using headroom), ev=2000W, max=4500W | (depends on pv/house) | 2800 W (increases to use 800W headroom) |

---

## Installation

**Manual:** Copy `custom_components/ems/` to `<config>/custom_components/ems/` and restart Home Assistant.

**Requirements:** Home Assistant 2024.11+, Python 3.12+.

After restart, add the integration via **Settings → Devices & Services → Add Integration → EMS**.

---

## Configuration (config flow)

The integration is configured in 3 steps — no YAML editing required.

The **Configure** button (Settings → Devices & Services → EMS → Configure) walks the same 3 steps with current values prefilled.

### Step 1 — Inverter

| Config key | Type | Required | Description |
|------------|------|----------|-------------|
| `pv_power` | `sensor` | **Yes** | Current PV generation (W) |
| `inverter_select` | select entity | No | HA select entity that controls inverter operating mode |
| `inverter_disabled_option` | text | No | Option string written to the inverter select when EV is idle (default: `"disabled"`) |
| `inverter_power_control_option` | text | No | Option string written to the inverter select when EV is charging (default: `"power_control"`) |
| `active_power_entity` | `number` / `input_number` | No | EMS writes computed active power (W) to this entity each cycle; negative = battery discharging (drain), positive = PV surplus (charging into inverter) |

### Step 2 — EV Charger

| Config key | Type | Required | Description |
|------------|------|----------|-------------|
| `ev_power` | `sensor` | **Yes** | Current EV charger power (W) |
| `ev_plug` | binary_sensor / input_boolean | No | Whether the EV is plugged in. When not configured EMS assumes always plugged. |
| `ev_soc` | `sensor` | No | EV battery state of charge (%) |
| `ev_target_soc` | `number` / `input_number` / `sensor` | No | SoC at which EV charging stops; reads entity at runtime; falls back to `80` % |
| `ev_charge_target_entity` | `number` / `input_number` | No | EMS writes the computed charge power (W) to this entity each cycle |
| `ev_max_charge_w` | `number` / `input_number` | No | Physical maximum output of the EV charger (W); EMS caps the computed EV target to this value at runtime |

### Step 3 — Grid

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
| `thresholds` | `max_grid_import_w` from entity (Step 3); `ev_target_soc` from entity (Step 2); `ev_min_charge_w` from `number.ems_ev_min_charge_w`; `max_ev_charge_w` from entity (Step 2) |
| `mapping` | Inverter select entity + active power entity + EV charge target entity from config |

The 1-second fast loop re-reads only: `pv_w`, `house_load_w`, `ev_power_w`, `grid_import_w`; remaining fields are reused from the last 10 s result.

---

## Tunable thresholds

`ev_target_soc` and `max_grid_import_w` are entity references set in the config flow; EMS reads the current value at runtime.

`ev_min_charge_w` is a live HA `number` entity — adjust it from the UI at any time.

| Where | Key / Entity | Default | Unit | Description |
|-------|-------------|---------|------|-------------|
| Config flow Step 3 (entity selector) | `max_grid_import_w` | 4500 | W | Hard ceiling on grid import |
| Config flow Step 2 (entity selector) | `ev_target_soc` | 80 | % | EV SoC at which charging stops |
| Config flow Step 2 (entity selector) | `ev_max_charge_w` | — | W | Physical maximum output of the EV charger; uncapped when not set |
| `number.ems_ev_min_charge_w` | — | 1400 | W | Minimum EV charge target; below this the EV stays idle |

---

## EV charging mode

| Entity | Options | Description |
|--------|---------|-------------|
| `select.ems_ev_charging_mode` | `Energy-optimised`, `Off` | `Energy-optimised`: charge using solar surplus + grid headroom. `Off`: disables EV charging entirely. |

---

## Outputs (writes to your hardware)

`controller.py` is the only module that calls HA services. All writes are idempotent — the last written value is cached and a call is skipped if the value has not changed.

### When EV is confirmed charging (`ev_power_w > 0`)

| Service | Entity | Value |
|---------|--------|-------|
| `select.select_option` | Inverter select entity (Step 1) | `"power_control"` |
| `number.set_value` | Active power entity (Step 1) | Computed active power (W); negative = discharge, positive = PV surplus (charging into inverter) |
| `number.set_value` | EV charge target entity (Step 2) | Computed EV target watts |

### When EV is not drawing power (`ev_power_w = 0`)

| Service | Entity | Value |
|---------|--------|-------|
| `number.set_value` | EV charge target entity (Step 2) | Computed EV target watts (or 0 when strategy is idle) |

The inverter is **not touched** when the EV is not drawing — it manages its own mode. EMS only writes to the inverter when `ev_power_w > 0`. The EV charge target is always written so the charger knows what power to draw when it starts.

All writes are idempotent — each path writes only when the value changes from the last written value. Writes are skipped during the 60-second startup grace period.

---

## Exposed entities

### Sensor entities (`sensor.ems_*`)

| Entity | Values | Description |
|--------|--------|-------------|
| `sensor.ems_ev_charging_state` | see below | Human-readable EV charging state. Updated every 10 seconds. Unavailable until the first strategy cycle completes. |
| `sensor.ems_inverter_mode` | `power_control`, or last written value | Last inverter option EMS wrote. `power_control` when EV is actively charging; value is unchanged when EV stops (EMS leaves the inverter alone). Unavailable until the first strategy cycle completes. |
| `sensor.ems_ev_current_w` | float (W) | Computed EV charge target EMS is writing to `ev_charge_target_entity`. `0` when idle, off, or before the first strategy cycle. Always available. |
| `sensor.ems_inverter_active_power` | float (W) | Active power EMS is writing to `active_power_entity`. Negative = battery discharging (drain); positive = PV surplus (charging into inverter); `0` when idle or before the first strategy cycle. Always available. |

#### `sensor.ems_ev_charging_state` values

| Value | Meaning |
|-------|---------|
| `Charging` | EV is actively charging under EMS control |
| `EV not connected` | EV plug sensor reports not connected |
| `Charging disabled` | EV charging mode is set to `Off` |
| `Target SoC reached` | EV SoC is at or above the configured target |
| `Not enough power` | Available grid headroom + PV is below the minimum charge threshold |
| `Starting up` | EMS is in the 60-second startup grace period; no writes yet |

### Number entities (`number.ems_*`)

| Entity | Default | Unit | Description |
|--------|---------|------|-------------|
| `number.ems_ev_min_charge_w` | 1400 | W | Minimum EV charge power; below this the EV stays idle |

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
