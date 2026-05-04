# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

`homeassistant-ems` is a Home Assistant custom integration called **EMS** (Energy Management System). It provides brand-agnostic **EV charging management** for solar + optional battery + EV setups. It decides how many watts to charge at every second based on solar surplus and available grid headroom, while enforcing a maximum grid import cap and preventing battery power from flowing to the EV.

## Documentation

Any change to integration code or behavior MUST update `README.md` in the same task. Stale docs are a bug.

`README.md` must always cover:
- **Core working**: the two-loop architecture and decision algorithm.
- **Inputs**: every config-flow field (all 3 steps, required vs optional), every runtime entity read by `coordinator.py::_build_inputs`, every `number.ems_*` threshold.
- **Outputs**: every HA service `controller.py` calls (when EV charging vs idle), every entity the integration registers (number, select).

When updating: edit the matching README section in the same commit. If a new input or output is added, add a row to the relevant table.

## Commands

```powershell
pytest tests/                  # Run all tests (80% coverage gate)
pytest tests/test_strategy.py `
  --cov=custom_components.ems.strategy `
  --cov-branch --cov-fail-under=100  # 100% coverage for pure module
python -m ruff check custom_components/ems tests  # Lint
```

CI runs `hassfest` via GitHub Actions (`.github/workflows/validate.yml`).

## Architecture

The integration is structured around two execution loops managed by `coordinator.py`:

**10-second strategy loop** (`EmsStrategyCoordinator`):
1. `_build_inputs()` freezes all mapped HA entity states into a frozen `Inputs` dataclass
2. `strategy.decide(inputs)` — pure function, returns a `Decision` (EV target watts, battery discharge cap)
3. Skips writes during startup grace (first 60 s) to avoid flapping on `unavailable` sensors
4. `controller.write_full(decision, ev_power_w)` — idempotent: switches inverter to `power_control` + writes active power cap only when `ev_power_w > 0` (EV confirmed drawing); always writes EV target; **leaves inverter untouched when `ev_power_w == 0`** (no revert to any mode)

**1-second fast loop** (`EmsFastLoopCoordinator`):
- Always runs once the integration is active
- Re-reads `pv_w`, `house_load_w`, `ev_power_w` and calls `strategy.recompute()`
- Calls `controller.write_full()` — keeps EV target and battery discharge cap enforced within 1 s of load changes

### Module boundaries

| Module | Rule |
|--------|------|
| `models.py` | Zero HA imports — frozen dataclasses only (`Inputs`, `Decision`, `VerbMapping`) |
| `strategy.py` | Pure functions `decide(inputs) → Decision` and `recompute(...) → Decision`; no I/O, no HA |
| `controller.py` | The **only** module that calls HA services |
| `coordinator.py` | Wires the loops; reads HA state; calls strategy and controller |

### Key abstractions

- **Power balance algorithm**: `ev_target = max(0, max_grid_import_w + pv_w - house_non_ev)`. Derivation: grid_import = house_non_ev + ev_target − pv_w; cap at max_grid_import_w. Works with both signed and positive-only grid sensors.
- **Battery discharge cap**: strategy emits `battery_discharge_cap_w` = `max(0, house_non_ev - pv_w)`. Controller writes it to `battery_discharge_cap_entity` via `number.set_value`. Ensures battery only covers the house load shortfall, never EV charging.
- **One inverter verb**: `power_control_option` (written only when EV is confirmed charging → controlled battery discharge). EMS never writes to the inverter when `ev_power_w == 0`; the inverter manages its own mode when idle.
- **EV charge target**: strategy emits `ev_charge_power_w` (float, W). Controller writes it via `number.set_value` to `ev_charge_target_entity` (Step 3). No manual automation needed.
- **house_non_ev**: derived inline as `max(0.0, house_load_w − ev_power_w)` — never a stored field.

### Config flow (3 steps, no YAML editing)
1. Required sensors (grid power, PV, house load, EV power); optional: `max_grid_import_w` entity
2. Inverter (optional) — select entity + `power_control_option` string + `disabled_option` string + `active_power_entity`
3. EV charger (optional) — plug/SoC/target-SoC/charge-target/max-charge entity selectors; EMS reads entity states at runtime; writes computed watts to `ev_charge_target_entity`

### EV charging mode
`select.ems_ev_charging_mode` — options: `Energy-optimised`, `Off`. `Off` forces `ev_charge_power_w = 0` and EMS writes 0 to the EV target entity.

### Target environment
- Home Assistant 2024.11+
- Python 3.12+
- Deployed via scp (see `deploy-ems` skill)
