# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

`homeassistant-ems` is a spec-first repository for a Home Assistant custom integration called **EMS** (Energy Management System). The full implementation specification lives in `implemenation.md`. The integration provides brand-agnostic energy management for solar + battery + EV setups via a **priority ladder algorithm**.

## Commands

Once implemented, the development commands will be (per spec):

```powershell
pytest tests/                  # Run all tests (80% coverage gate)
pytest tests/test_strategy.py tests/test_ev_scheduler.py `
  --cov=custom_components.ems.strategy `
  --cov=custom_components.ems.ev_scheduler `
  --cov-branch --cov-fail-under=100  # 100% coverage for pure modules
ruff check                     # Lint
```

CI runs `hassfest` and `hacs validation` via GitHub Actions (`.github/workflows/validate.yml`).

## Architecture

The integration is structured around three execution loops managed by `coordinator.py`:

**10-second strategy loop** (`EmsStrategyCoordinator`):
1. `_build_inputs()` freezes all mapped HA entity states into a frozen `Inputs` dataclass
2. `strategy.decide(inputs)` — pure function, returns a `Decision` (which ladder rule fired, verb assignments, power targets)
3. Updates hysteresis latch: `state.peak_risk_latched`
4. Skips writes during startup grace (first 45 s) to avoid flapping on `unavailable` sensors
5. `controller.write_full()` — idempotent calls to `select.select_option()` + `number.set_value()`

**1-second peak-shave loop** (`EmsPeakShaveCoordinator`):
- Only active when `peak_risk_latched` is true
- Re-reads fast inputs (grid, house load, EV power), calls `strategy.recompute_peak_shave_power()`
- Calls `controller.write_power_only()` — never flips modes, only adjusts wattage

**5-minute EV scheduler** (`EvSchedulerOrchestrator`):
- Calls pure `ev_scheduler.compute_window()` to pick cheapest charging hours
- Updates `state.cached_window`; the 10 s loop reads `state.cached_window.in_window_now`

### Module boundaries

| Module | Rule |
|--------|------|
| `models.py` | Zero HA imports — frozen dataclasses only (`Inputs`, `Decision`, `EvWindow`, `VerbMapping`) |
| `strategy.py` | Pure function `decide(inputs) → Decision`; no I/O, no HA |
| `ev_scheduler.py` | Pure function `compute_window(...) → EvWindow`; no I/O, no HA |
| `controller.py` | The **only** module that calls HA services |
| `coordinator.py` | Wires the loops; reads HA state; calls strategy, ev_scheduler, controller |

### Key abstractions

- **Abstract verbs** (`FORCE_CHARGE`, `FORCE_DISCHARGE`, `IDLE`, `NORMAL`): inverter-agnostic. Users map each verb to their hardware's actual option string in the config flow. Never hardcode inverter mode strings.
- **Priority ladder order**: peak shave → battery floor → EV coupling → export curtail → proactive charge → solar-use → expensive-grid → fallback

### Config flow (6 steps, no YAML editing)
1. Required sensors (grid power, PV, house load, battery SoC/power)
2. Inverter select entity + verb→option mapping
3. EV charger (optional) — mode select, power/SoC/capacity sensors
4. Price sources (import/export, forecast paths)
5. PV + load forecast sources
6. Feature toggle flags (peak shaving, proactive charge, EV scheduling, export curtailment)

### Persistence
`homeassistant.helpers.storage.Store` persists `peak_risk_latched` and `cached_window` to `ems_state.json` so state survives restarts.

### Target environment
- Home Assistant 2024.11+
- Python 3.11+
- Distributed via HACS
