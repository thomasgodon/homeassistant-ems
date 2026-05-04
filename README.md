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
4. `controller.write_full()` — idempotent: writes inverter mode (`power_control`), active power, and EV charge target when EV is actively charging; writes `disabled` inverter mode and 0 to the EV target when idle.

### 1-second fast loop (`EmsFastLoopCoordinator`)
Always runs once the integration is active. Re-reads `grid_import_w`, `pv_w`, `house_load_w`, and `ev_power_w`, runs the same `strategy.recompute()` formula, and calls `controller.write_full()`. This keeps the grid import cap enforced within one second when loads change suddenly (e.g. a kettle comes on).

### Decision algorithm — grid headroom tracking (DLM)

```
house_non_ev       = max(0, house_load_w − ev_power_w)
solar_for_ev       = max(0, pv_w − house_non_ev)
active_power_w     = house_non_ev − pv_w   ← positive: battery discharges for house; negative: PV surplus
grid_non_ev_import = grid_import_w − ev_power_w    ← current non-EV grid draw
grid_headroom      = max(0, max_grid_import_w − grid_non_ev_import)
ev_target_w        = solar_for_ev + grid_headroom

# Hard gates → idle
if not ev_plugged
   or ev_strategy == "Off"
   or (ev_soc is not None and ev_soc ≥ ev_target_soc):
    → ev_idle

# Below minimum → idle
elif ev_target_w < ev_min_charge_w:
    → ev_idle

# Charge
else:
    → ev_charging (ev_target_w, active_power_w)
```

`active_power_w` is written to the inverter's active power entity each cycle. Positive means the battery covers the house load shortfall; negative means PV has surplus and the inverter can absorb it (e.g. battery charging, preventing grid export).

#### Worked examples

| Situation | active_power_w | EV target source |
|-----------|---------------|-----------------|
| pv=500W, house_non_ev=200W | −300 W (PV surplus, battery absorbs) | 300 W solar + grid headroom |
| pv=0W, house_non_ev=200W | +200 W (battery covers house) | Grid only |
| pv=150W, house_non_ev=200W | +50 W (battery covers shortfall) | Grid only |

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
| `active_power_entity` | `number` / `input_number` | No | EMS writes computed active power (W) to this entity each cycle; positive = battery discharges for house, negative = PV surplus |

### Step 2 — EV Charger

| Config key | Type | Required | Description |
|------------|------|----------|-------------|
| `ev_power` | `sensor` | **Yes** | Current EV charger power (W) |
| `ev_plug` | binary_sensor / input_boolean | No | Whether the EV is plugged in. When not configured EMS assumes always plugged. |
| `ev_soc` | `sensor` | No | EV battery state of charge (%) |
| `ev_target_soc` | `number` / `input_number` / `sensor` | No | SoC at which EV charging stops; reads entity at runtime; falls back to `80` % |
| `ev_charge_target_entity` | `number` / `input_number` | No | EMS writes the computed charge power (W) to this entity each cycle |

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
| `thresholds` | `max_grid_import_w` from entity (Step 3); `ev_target_soc` from entity (Step 2); `ev_min_charge_w` from `number.ems_ev_min_charge_w` |
| `mapping` | Inverter select entity + active power entity + EV charge target entity from config |

The 1-second fast loop re-reads only: `grid_import_w`, `pv_w`, `house_load_w`, `ev_power_w`; remaining fields are reused from the last 10 s result.

---

## Tunable thresholds

`ev_target_soc` and `max_grid_import_w` are entity references set in the config flow; EMS reads the current value at runtime.

`ev_min_charge_w` is a live HA `number` entity — adjust it from the UI at any time.

| Where | Key / Entity | Default | Unit | Description |
|-------|-------------|---------|------|-------------|
| Config flow Step 3 (entity selector) | `max_grid_import_w` | 4500 | W | Hard ceiling on grid import |
| Config flow Step 2 (entity selector) | `ev_target_soc` | 80 | % | EV SoC at which charging stops |
| `number.ems_ev_min_charge_w` | — | 1400 | W | Minimum EV charge target; below this the EV stays idle |

---

## EV charging mode

| Entity | Options | Description |
|--------|---------|-------------|
| `select.ems_ev_charging_mode` | `Energy-optimised`, `Off` | `Energy-optimised`: charge using solar surplus + grid headroom. `Off`: disables EV charging entirely. |

---

## Outputs (writes to your hardware)

`controller.py` is the only module that calls HA services. All writes are idempotent — the last written value is cached and a call is skipped if the value has not changed.

### When EV is charging (`ev_charge_power_w > 0`)

| Service | Entity | Value |
|---------|--------|-------|
| `select.select_option` | Inverter select entity (Step 1) | `"power_control"` |
| `number.set_value` | Active power entity (Step 1) | Computed active power (W); positive = discharge, negative = surplus |
| `number.set_value` | EV charge target entity (Step 2) | Computed EV target watts |

### When EV is idle (`ev_charge_power_w = 0`)

| Service | Entity | Value |
|---------|--------|-------|
| `select.select_option` | Inverter select entity (Step 1) | `"disabled"` |
| `number.set_value` | EV charge target entity (Step 2) | 0 W |

The controller's write-cache is cleared on idle so the next active tick re-fires all writes. Writes are skipped during the 60-second startup grace period.

---

## Exposed entities

### Binary sensor entities (`binary_sensor.ems_*`)

| Entity | Description |
|--------|-------------|
| `binary_sensor.ems_ev_charging` | `on` when EMS intends to charge the EV (mode=Energy-optimised + EV plugged + SOC below target), regardless of whether available power meets the minimum charge threshold. `off` when mode=Off, EV unplugged, or SOC at target. Never unavailable. |

### Sensor entities (`sensor.ems_*`)

| Entity | Values | Description |
|--------|--------|-------------|
| `sensor.ems_inverter_mode` | `disabled`, `power_control` | Current inverter option EMS is targeting. `power_control` when EV is actively charging; `disabled` when idle. Unavailable until the first strategy cycle completes. |

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
