# EMS — Energy Management System

A brand-agnostic Home Assistant custom integration for **EV charging management**. It decides how many watts your EV should charge at every second using solar surplus and available grid headroom, while enforcing a maximum grid import cap and never using battery power for EV charging.

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
5. `controller.write_slow()` — writes the inverter verb **every cycle** (overriding any manual user change within 10 s), writes active power if the EV is charging, and always writes the EV charge target.

### 1-second fast loop (`EmsFastLoopCoordinator`)
Always runs once the integration is active. Re-reads `pv_w`, `house_load_w`, `ev_power_w`, and `grid_import_w`, then runs `strategy.recompute()` and `inverter_control.recompute()`. Calls `controller.write_fast()` — which writes active power and EV target but **never writes the inverter verb** (verb enforcement is the slow loop's job).

### EV algorithm — power balance

```
house_non_ev   = max(0, house_load_w − ev_power_w)
feedback_w     = last_ev_target_w  ← last value commanded to wallbox (ev_power_w on cold start)
ev_target_w    = max(0, max_grid_import_w − grid_import_w + feedback_w)
ev_target_w    = min(ev_target_w, max_ev_charge_w)  ← if max_ev_charge_w is configured

# Hard gates → idle (evaluated in priority order)
if ev_strategy == "Off":                                           → charging_disabled
if not ev_plugged:                                                 → ev_not_connected
if ev_strategy == "Price-optimised" and not is_cheap_hour:         → outside_cheap_hours
if ev_soc is not None and ev_soc ≥ ev_target_soc:                 → target_soc_reached

# Below minimum → idle
elif ev_target_w < ev_min_charge_w:                                → not_enough_power

# Charge
else:
    → ev_charging (ev_target_w)
```

Integrating closed-loop: using the last commanded target (`last_ev_target_w`) as feedback makes the formula a pure integrator on grid error — it keeps nudging the EV target up until `grid_import_w = max_grid_import_w`, regardless of wallbox current quantisation or ramp lag. With measured `ev_power_w` as feedback (old formula), the integrator stalled when the wallbox quantised to a whole-amp step just below the threshold needed to raise the import to the cap.

### Inverter control algorithm

Controlled by `select.ems_inverter_control_mode`. When set to **`Off`**, EMS never touches the inverter select entity or active power entity — the user has full manual control. When set to **`Control Inverter`**:

```
if ev_decision.strategy == "ev_charging":   # EV strategy commands charging
    verb          = power_control_option
    active_power  = -min(pv_w, house_load_w)   ← directs PV to AC; battery neither charged nor drained
else:
    verb          = disabled_option
    active_power  = (not written)
```

The gate is the **commanded** EV state (`ev_decision.strategy`), not the **measured** `ev_power_w`. Gating on measured draw would create a start-of-session deadlock: the inverter would stay in `disabled_option` until the EV began drawing, but the EV cannot safely draw until the inverter is already in `power_control`. By keying on the command, the inverter switches mode as soon as the strategy decides to charge — before the wallbox ramps up.

**Battery protection:** `active_power = -min(pv_w, house_load_w)` ensures the battery is never charged from AC (sign always ≤ 0) and never drained for the EV (magnitude capped by available PV). PV is directed to AC up to total AC demand; any PV surplus beyond AC demand naturally charges the battery. Negative = inverter drains PV → AC; 0 = no PV (night). Positive values (intentional battery charging from AC) are reserved for a later feature.

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
| `inverter_disabled_option` | text | No | Option string written to the inverter select when EV is idle (e.g. `"Self Use"`) |
| `inverter_power_control_option` | text | No | Option string written to the inverter select when EV is charging (e.g. `"Power Control"`) |
| `active_power_entity` | `number` / `input_number` | No | EMS writes computed active power (W) to this entity when EV is charging: `-min(pv_w, house_load_w)`. Negative = inverter drains PV → AC; 0 = no PV. Battery is neither charged nor drained. |

### Step 2 — EV Charger

| Config key | Type | Required | Description |
|------------|------|----------|-------------|
| `ev_power` | `sensor` | **Yes** | Current EV charger power (W) |
| `ev_plug` | binary_sensor / input_boolean | No | Whether the EV is plugged in. When not configured EMS assumes always plugged. |
| `ev_soc` | `sensor` | No | EV battery state of charge (%) |
| `ev_target_soc` | `number` / `input_number` / `sensor` | No | SoC at which EV charging stops; reads entity at runtime; falls back to `80` % |
| `ev_charge_target_entity` | `number` / `input_number` | No | EMS writes the computed charge power (W) to this entity each cycle |
| `ev_max_charge_w` | `number` / `input_number` | No | Physical maximum output of the EV charger (W); EMS caps the computed EV target to this value at runtime |
| `price_signal_entity` | binary_sensor / input_boolean | No | Cheap-hour signal for `Price-optimised` mode. EMS charges only when `on`; defaults to `on` (always cheap) when not configured or unavailable. |

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
| `inverter_strategy` | `select.ems_inverter_control_mode` |
| `is_cheap_hour` | `price_signal_entity` (Step 2); `True` when not configured or unavailable |
| `thresholds` | `max_grid_import_w` from entity (Step 3); `ev_target_soc` from entity (Step 2); `ev_min_charge_w` from `number.ems_ev_min_charge_w`; `max_ev_charge_w` from entity (Step 2) |
| `mapping` | Inverter select entity + active power entity + EV charge target entity from config |

The 1-second fast loop re-reads only: `pv_w`, `house_load_w`, `ev_power_w`, `grid_import_w`, `ev_strategy`, `inverter_strategy`; remaining fields are reused from the last 10 s result.

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
| `select.ems_ev_charging_mode` | `Grid-optimised`, `Price-optimised`, `Off` | `Grid-optimised`: charge using solar surplus + grid headroom. `Price-optimised`: same as Grid-optimised but only charges when the `price_signal_entity` is `on` (i.e. a cheap hour). `Off`: disables EV charging entirely. |
| `select.ems_ev_smoothing` | `Fast`, `Normal`, `Slow` | Controls the EMA alpha applied to the EV charge target in the 1-second fast loop. `Fast` (α=0.3, ~3 s settling), `Normal` (α=0.2, ~5 s), `Slow` (α=0.1, ~10 s). Default: `Fast`. |

---

## Inverter control mode

| Entity | Options | Default | Description |
|--------|---------|---------|-------------|
| `select.ems_inverter_control_mode` | `Control Inverter`, `Off` | `Off` | `Control Inverter`: EMS asserts the correct inverter verb every 10-second slow loop cycle (overrides any manual selection within 10 s). When the EV strategy commands charging the verb is `inverter_power_control_option` and active power is written every 1 s; otherwise the verb is `inverter_disabled_option` and active power is not written. The switch happens as soon as charging is commanded — before the EV begins drawing — to avoid inverter mode lag at session start. `Off`: EMS never writes to the inverter select entity or active power entity — the user has full manual control. EV charge target writes are unaffected by this setting. |

---

## Outputs (writes to your hardware)

`controller.py` is the only module that calls HA services.

### Slow loop (10 s) — `controller.write_slow()`

| Service | Entity | Condition | Value |
|---------|--------|-----------|-------|
| `select.select_option` | Inverter select entity (Step 1) | `inverter_control_mode = Control Inverter` AND `inverter_verb is not None` | `power_control_option` (EV commanded charging) or `disabled_option` (EV idle) |
| `number.set_value` | Active power entity (Step 1) | `inverter_control_mode = Control Inverter` AND EV commanded charging | `min(0, pv_w − house_non_ev)` W — always ≤ 0 |
| `number.set_value` | EV charge target entity (Step 2) | Always | Computed EV target watts (0 when idle) |

The inverter verb is written on **every slow loop cycle** — no idempotency cache. This ensures user manual overrides are corrected within 10 seconds.

### Fast loop (1 s) — `controller.write_fast()`

| Service | Entity | Condition | Value |
|---------|--------|-----------|-------|
| `number.set_value` | Active power entity (Step 1) | `inverter_control_mode = Control Inverter` AND EV commanded charging | Smoothed `min(0, pv_w − house_non_ev)` W |
| `number.set_value` | EV charge target entity (Step 2) | Always | Smoothed EV target watts (0 when idle) |

Active power and EV target are cached — a service call is skipped if the value has not changed from the last written value. Writes are skipped during the 60-second startup grace period.

---

## Exposed entities

### Select entities

| Entity | Options | Default | Description |
|--------|---------|---------|-------------|
| `select.ems_ev_charging_mode` | `Grid-optimised`, `Price-optimised`, `Off` | `Off` | EV charging strategy |
| `select.ems_ev_smoothing` | `Fast`, `Normal`, `Slow` | `Fast` | EMA smoothing speed for EV charge target in the 1 s loop |
| `select.ems_inverter_control_mode` | `Control Inverter`, `Off` | `Off` | Whether EMS controls the inverter select entity |

### Sensor entities (`sensor.ems_*`)

| Entity | Values | Description |
|--------|--------|-------------|
| `sensor.ems_ev_charging_state` | see below | Human-readable EV charging state. Updated every second. Unavailable until the first strategy cycle completes. |
| `sensor.ems_inverter_mode` | `power_control`, `disabled`, or `None` | Current inverter verb from the last `InverterDecision`. Reflects what EMS last intended to write. Unavailable until the first strategy cycle completes. |
| `sensor.ems_ev_current_w` | float (W) | Computed EV charge target EMS is writing to `ev_charge_target_entity`. `0` when idle, off, or before the first strategy cycle. Always available. |
| `sensor.ems_inverter_active_power` | float (W) | Active power EMS is writing to `active_power_entity` (`-min(pv_w, house_load_w)`). Negative = inverter drains PV → AC; `0` when no PV, EV not charging, inverter control is Off, or before the first strategy cycle. Always available. |
| `sensor.ems_inverter_status` | `Controlling`, `Idle`, `Disabled`, `Starting up` | Explains why and how EMS is (or isn't) controlling the inverter. Attributes: `reason` (human sentence), `formula` (shows the `active_power = -min(pv_w, house_load_w)` calculation), plus `pv_w`, `house_load_w`, `ev_power_w`, `house_non_ev_w`, `active_power_w`. Unavailable until the first strategy cycle completes. |

#### `sensor.ems_ev_charging_state` values

| Value | Meaning |
|-------|---------|
| `Charging` | EV is actively charging under EMS control |
| `EV not connected` | EV plug sensor reports not connected |
| `Charging disabled` | EV charging mode is set to `Off` |
| `Target SoC reached` | EV SoC is at or above the configured target |
| `Not enough power` | Available grid headroom + PV is below the minimum charge threshold |
| `Starting up` | EMS is in the 60-second startup grace period; no writes yet |
| `Outside cheap hours` | Mode is `Price-optimised` and the price signal entity is `off` |

#### `sensor.ems_inverter_status` values

| Value | Meaning |
|-------|---------|
| `Controlling` | EV charging commanded; inverter set to power-control mode, active power written to prevent battery from feeding the EV |
| `Idle` | No EV draw; inverter set to its `disabled_option` so it manages its own mode |
| `Disabled` | Inverter Control Mode is `Off`; EMS does not write to the inverter at all |
| `Starting up` | EMS is in the 60-second startup grace period; strategy computes but writes are suppressed |

### Number entities (`number.ems_*`)

| Entity | Default | Unit | Description |
|--------|---------|------|-------------|
| `number.ems_ev_min_charge_w` | 1400 | W | Minimum EV charge power; below this the EV stays idle |

---

## Development

```powershell
pytest tests/                  # Run all tests (80% coverage gate)
pytest tests/test_inverter_control.py `
  --cov=custom_components.ems.inverter_control `
  --cov-branch --cov-fail-under=100  # 100% coverage for pure module
pytest tests/test_strategy.py `
  --cov=custom_components.ems.strategy `
  --cov-branch --cov-fail-under=100  # 100% coverage for pure module
python -m ruff check custom_components/ems tests  # Lint
```

CI runs `hassfest` via GitHub Actions (`.github/workflows/validate.yml`).
