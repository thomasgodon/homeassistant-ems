# EMS — Energy Management System

A brand-agnostic Home Assistant custom integration for **EV charging management**. It decides how many watts your EV should charge at every second using solar surplus and available grid headroom, while enforcing a maximum grid import cap and never using battery power for EV charging.

Works with any inverter that exposes a `select` entity for operating modes and a `number` entity for battery discharge power — no inverter-specific code.

---

## How it works

Two execution loops share a single `EmsState` object:

### 10-second strategy loop (`EmsStrategyCoordinator`)
1. `_build_inputs()` — reads all configured HA entities into a frozen `Inputs` dataclass.
2. `strategy.decide(inputs)` — pure function; returns a `Decision` (EV target watts, battery discharge cap).
3. Writes are skipped for 60 seconds after startup to avoid flapping on `unavailable` sensors.
4. `controller.write_full()` — idempotent: writes inverter mode, battery discharge cap, and EV charge target when EV is actively charging; writes `self_use` inverter mode and 0 to the EV target when idle.

### 1-second fast loop (`EmsFastLoopCoordinator`)
Always runs once the integration is active. Re-reads `grid_import_w`, `pv_w`, `house_load_w`, and `ev_power_w`, runs the same `strategy.recompute()` formula, and calls `controller.write_full()`. This keeps the grid import cap enforced within one second when loads change suddenly (e.g. a kettle comes on).

### Decision algorithm — grid headroom tracking (DLM)

```
house_non_ev          = max(0, house_load_w − ev_power_w)
solar_for_ev          = max(0, pv_w − house_non_ev)
battery_discharge_cap = max(0, house_non_ev − pv_w)   ← battery covers house shortfall only
grid_non_ev_import    = grid_import_w − ev_power_w    ← current non-EV grid draw
grid_headroom         = max(0, max_grid_import_w − grid_non_ev_import)
ev_target_w           = solar_for_ev + grid_headroom

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
    → ev_charging (ev_target_w, battery_discharge_cap)
```

The battery discharge cap ensures battery power is only used to cover the house load shortfall — **never for EV charging**.

#### Worked examples

| Situation | Battery cap | EV target source |
|-----------|------------|-----------------|
| pv=500W, house_non_ev=200W | 0 W (battery rests) | 300 W solar + grid headroom |
| pv=0W, house_non_ev=200W | 200 W (covers house) | Grid only |
| pv=150W, house_non_ev=200W | 50 W (covers 50W shortfall) | Grid only |

---

## Installation

**Manual:** Copy `custom_components/ems/` to `<config>/custom_components/ems/` and restart Home Assistant.

**Requirements:** Home Assistant 2024.11+, Python 3.12+.

After restart, add the integration via **Settings → Devices & Services → Add Integration → EMS**.

---

## Configuration (config flow)

The integration is configured in 3 steps — no YAML editing required.

The **Configure** button (Settings → Devices & Services → EMS → Configure) walks the same 3 steps with current values prefilled.

### Step 1 — Required sensors

| Config key | Entity domain | Required | Description |
|------------|--------------|----------|-------------|
| `grid_import_power` | `sensor` | **Yes** | Current grid import power (W); positive = importing |
| `pv_power` | `sensor` | **Yes** | Current PV generation (W) |
| `house_load` | `sensor` | **Yes** | Total house load including EV (W) |
| `ev_power` | `sensor` | **Yes** | Current EV charger power (W) |
| `max_grid_import_w` | `number` / `input_number` | No | Hard ceiling on grid import (W); reads entity state at runtime; falls back to `4500` W |

### Step 2 — Inverter (optional)

Skip this step if you have no home battery or do not want EMS to control the inverter.

When EV is **charging**, EMS selects `battery_control_option` and writes the computed discharge cap to `battery_discharge_cap_entity`. When EV is **idle**, EMS selects `self_use_option` (normal operation).

| Config key | Type | Description |
|------------|------|-------------|
| `inverter_select` | select entity | HA select entity that controls inverter operating mode |
| `inverter_verb_self_use` | text | Option string for inverter's normal / self-use mode (written on idle) |
| `inverter_verb_battery_control` | text | Option string for battery discharge control mode (written when EV charges) |
| `battery_discharge_cap_entity` | `number` / `input_number` entity | EMS writes the battery max discharge power (W) to this entity each cycle |

### Step 3 — EV charger

All fields optional. Skip entirely if you have no EV.

EMS writes the computed charge power directly to `ev_charge_target_entity` every second (and writes 0 when idle). No manual automation required.

| Config key | Type | Description |
|------------|------|-------------|
| `ev_plug` | binary_sensor / input_boolean | Whether the EV is plugged in |
| `ev_soc` | sensor entity | EV battery state of charge (%) |
| `ev_target_soc` | `number` / `input_number` / `sensor` entity | SoC at which EV charging stops; reads entity at runtime; falls back to `80` % |
| `ev_charge_target_entity` | `number` / `input_number` entity | EMS writes the computed charge power (W) to this entity each cycle |

---

## Runtime inputs

Every 10 seconds `coordinator.py::_build_inputs()` populates the following `Inputs` dataclass fields:

| Field | Source |
|-------|--------|
| `now` | System clock (`dt_util.utcnow()`) |
| `grid_import_w` | `grid_import_power` entity |
| `pv_w` | `pv_power` entity |
| `house_load_w` | `house_load` entity |
| `ev_plugged` | `ev_plug` entity |
| `ev_power_w` | `ev_power` entity |
| `ev_soc` | `ev_soc` entity (%) or `None` |
| `ev_strategy` | `select.ems_ev_charging_mode` |
| `thresholds` | `max_grid_import_w` from entity (Step 1); `ev_target_soc` from entity (Step 3); `ev_min_charge_w` from `number.ems_ev_min_charge_w` |
| `mapping` | Inverter select entity + verb strings + battery cap entity + EV charge target entity from config |

The 1-second fast loop re-reads only: `grid_import_w`, `pv_w`, `house_load_w`, `ev_power_w`; remaining fields are reused from the last 10 s result.

---

## Tunable thresholds

`ev_target_soc` and `max_grid_import_w` are entity references set in the config flow; EMS reads the current value at runtime.

`ev_min_charge_w` is a live HA `number` entity — adjust it from the UI at any time.

| Where | Key / Entity | Default | Unit | Description |
|-------|-------------|---------|------|-------------|
| Config flow Step 1 (entity selector) | `max_grid_import_w` | 4500 | W | Hard ceiling on grid import |
| Config flow Step 3 (entity selector) | `ev_target_soc` | 80 | % | EV SoC at which charging stops |
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
| `select.select_option` | Inverter select entity (Step 2) | `battery_control_option` string |
| `number.set_value` | Battery discharge cap entity (Step 2) | Computed battery discharge cap (W) |
| `number.set_value` | EV charge target entity (Step 3) | Computed EV target watts |

### When EV is idle (`ev_charge_power_w = 0`)

| Service | Entity | Value |
|---------|--------|-------|
| `select.select_option` | Inverter select entity (Step 2) | `self_use_option` string |
| `number.set_value` | EV charge target entity (Step 3) | 0 W |

The controller's write-cache is cleared on idle so the next active tick re-fires all writes. Writes are skipped during the 60-second startup grace period.

---

## Exposed entities

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
