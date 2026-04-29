# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

`homeassistant-ems` is a Home Assistant custom integration called **EMS** (Energy Management System). It provides brand-agnostic **EV charging management** for solar + optional battery + EV setups. It decides how many watts to charge at based on solar surplus or cheap grid windows, while enforcing a maximum grid import cap.

## Documentation

Any change to integration code or behavior MUST update `README.md` in the same task. Stale docs are a bug.

`README.md` must always cover:
- **Core working**: the three-loop architecture and decision algorithm.
- **Inputs**: every config-flow field (all 4 steps, required vs optional), every runtime entity read by `coordinator.py::_build_inputs`, every `number.ems_*` threshold, every `switch.ems_*` feature toggle.
- **Outputs**: every HA service `controller.py` calls (when EV charging vs idle), every entity the integration registers (sensor, binary_sensor, number, select, switch, time).

When updating: edit the matching README section in the same commit. If a new input or output is added, add a row to the relevant table.

## Commands

```powershell
pytest tests/                  # Run all tests (80% coverage gate)
pytest tests/test_strategy.py tests/test_ev_scheduler.py `
  --cov=custom_components.ems.strategy `
  --cov=custom_components.ems.ev_scheduler `
  --cov-branch --cov-fail-under=100  # 100% coverage for pure modules
python -m ruff check custom_components/ems tests  # Lint
```

CI runs `hassfest` via GitHub Actions (`.github/workflows/validate.yml`).

## Architecture

The integration is structured around three execution loops managed by `coordinator.py`:

**10-second strategy loop** (`EmsStrategyCoordinator`):
1. `_build_inputs()` freezes all mapped HA entity states into a frozen `Inputs` dataclass
2. `strategy.decide(inputs)` — pure function, returns a `Decision` (EV target watts, inverter verb)
3. Skips writes during startup grace (first 60 s) to avoid flapping on `unavailable` sensors
4. `controller.write_full()` — idempotent: writes inverter mode only when `ev_charge_power_w > 0`; on idle resets write-cache

**1-second fast loop** (`EmsFastLoopCoordinator`):
- Always runs once the integration is active (no latch gate)
- Re-reads `pv_w`, `house_load_w`, `ev_power_w` and calls `strategy.recompute()`
- Calls `controller.write_full()` — keeps EV target enforced within 1 s of load changes

**5-minute EV scheduler** (`EvSchedulerOrchestrator`):
- Calls pure `ev_scheduler.compute_window()` to pick cheapest charging hours
- Updates `state.cached_window`; both loops read `state.cached_window.in_window_now`

### Module boundaries

| Module | Rule |
|--------|------|
| `models.py` | Zero HA imports — frozen dataclasses only (`Inputs`, `Decision`, `EvWindow`, `VerbMapping`) |
| `strategy.py` | Pure functions `decide(inputs) → Decision` and `recompute(...) → Decision`; no I/O, no HA |
| `ev_scheduler.py` | Pure function `compute_window(...) → EvWindow`; no I/O, no HA |
| `controller.py` | The **only** module that calls HA services |
| `coordinator.py` | Wires the loops; reads HA state; calls strategy, ev_scheduler, controller |

### Key abstractions

- **Single inverter verb** (`SELF_USE`): maps to the inverter's normal operating option string configured in Step 2. EMS writes it when the EV is charging.
- **EV charge target**: strategy emits `ev_charge_power_w` (float, W). No writes to charger. EMS exposes `sensor.ems_ev_charge_target_power`; user creates their own automation.
- **house_non_ev**: derived inline as `max(0.0, house_load_w − ev_power_w)` — never a stored field.

### Config flow (4 steps, no YAML editing)
1. Required sensors (grid power, PV, house load, EV power); optional: `grid_import_15m_avg`
2. Inverter (optional) — select entity + single SELF_USE option string
3. EV charger (optional) — plug/SoC/capacity/target-SoC entity selectors; EMS reads entity states at runtime
4. Price sources (import price, forecast attribute path)

### Feature toggles
Single feature toggle: `switch.ems_ev_charging` — when off, `ev_charge_power_w` is forced to 0 and EMS makes no inverter writes.

### Persistence
`homeassistant.helpers.storage.Store` persists `cached_window` to `ems_state.json` so state survives restarts.

### Target environment
- Home Assistant 2024.11+
- Python 3.12+
- Deployed via scp (see `deploy-ems` skill)
