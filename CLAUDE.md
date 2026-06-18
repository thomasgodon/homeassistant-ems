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
2. `strategy.decide(inputs)` — pure function, returns an `EvDecision` (EV target watts)
3. Skips writes during startup grace (first 60 s) to avoid flapping on `unavailable` sensors
4. `controller.write_slow(ev_decision, inverter_decision)` — writes inverter verb on every cycle (so manual overrides are corrected); writes `active_power_w` when non-None; always writes EV target

**1-second fast loop** (`EmsFastLoopCoordinator`):
- Always runs once the integration is active
- Re-reads `pv_w`, `house_load_w`, `ev_power_w`, `grid_import_w` and calls `strategy.recompute()`
- Calls `controller.write_full()` — keeps EV target and battery discharge cap enforced within 1 s of load changes

### Module boundaries

| Module | Rule |
|--------|------|
| `models.py` | Zero HA imports — frozen dataclasses only (`Inputs`, `EvDecision`, `InverterDecision`, `VerbMapping`) |
| `strategy.py` | Pure functions `decide(inputs) → EvDecision` and `recompute(...) → EvDecision`; no I/O, no HA |
| `controller.py` | The **only** module that calls HA services |
| `coordinator.py` | Wires the loops; reads HA state; calls strategy and controller |

### Key abstractions

- **Power balance algorithm**: `ev_target = max(0, max_grid_import_w − grid_import_avg_w + last_ev_target_w)`. Integrating closed-loop: `last_ev_target_w` is the EMA-smoothed value written to the wallbox on the previous cycle. Using the commanded target (not the measured EV draw) makes the loop a pure integrator on grid error, driving `grid_import → max_grid_import_w` regardless of wallbox quantisation or ramp lag. On cold start (`last_ev_target_w = None`), falls back to `ev_power_w`.
- **Grid demand-averaging filter**: `grid_import_avg_w` is a 60 s EMA of grid import maintained by the fast loop (`EmsState.grid_ema_w`, `_EV_GRID_FILTER_TAU_S = 60.0`, `alpha = 1 − exp(−1/τ)` per 1 s tick). **Only** the EV integrator consumes it — peak shaving, active power, and status sensors read raw `grid_import_w`. It stops short transients (e.g. a 20 s pump) from cratering the target below `ev_min_charge_w` and stopping the session. The fast loop owns/updates it and stamps it onto the result `Inputs`; the slow loop reads `self._state.grid_ema_w` into the `Inputs` it builds. `strategy._make_decision` falls back to raw `grid_import_w` when `grid_import_avg_w is None` (cold start / tests). Exposed as `sensor.ems_grid_import_average`.
- **Active power**: `inverter_control.py` emits `active_power_w = -min(pv_w, house_load_w)` as the default "controlling" output whenever EMS is commanding an EV charge target or the EV is still drawing. Sign convention: **negative = inverter drains PV → AC**; **positive = battery charges from AC** (planned feature, not yet written). Always ≤ 0: magnitude capped by `pv_w` (battery never drained for EV), sign never positive (battery never charged from AC). Controller writes it every cycle the ladder produces a non-None value.
- **One inverter verb**: `power_control_option` is asserted whenever `inverter_strategy == "Control Inverter"` AND (a) `commanded_ev_charge_w > 0` (EMS is actively charging), or (b) `ev_power_w > 0` (EV winding down), or (c) peak shaving or EOD suppress is active. EOD suppress fires even during a Price-optimised EV session in a cheap hour — when PV > house_non_ev, `active_power_w = -house_non_ev` (PV covers house, surplus to battery); when PV ≤ house_non_ev, `active_power_w = 0` (all PV to battery, house from cheap grid). Battery never discharges. For Grid-optimised sessions, EOD suppress is bypassed (EV integrator controls grid import). Otherwise EMS writes `disabled_option`, releasing the inverter to its native Self-Use Mode. Power Control is asserted on cycle 1 of a session, before the first target write. During daytime with PV producing but no active charging session, the inverter runs in Self-Use Mode autonomously — EMS stays out of the way.
- **EV charge target**: strategy emits `ev_charge_power_w` (float, W). The fast loop's EMA smooths this during active charging; when strategy returns any idle decision (EV not connected, outside cheap hours, etc.) the EMA snaps to exactly `0.0` — this is critical so that `commanded_ev_charge_w == 0.0` holds in `inverter_control._compute` and the idle branch fires (releasing active-power writes). Controller writes via `number.set_value` to `ev_charge_target_entity` (Step 3).
- **house_non_ev**: derived inline as `max(0.0, house_load_w − ev_power_w)` — never a stored field.

### Config flow (3 steps, no YAML editing)
1. Required sensors (grid power, PV, house load, EV power); optional: `max_grid_import_w` entity
2. Inverter (optional) — select entity + `power_control_option` string + `disabled_option` string + `active_power_entity`
3. EV charger (optional) — plug/SoC/target-SoC/charge-target/max-charge entity selectors; EMS reads entity states at runtime; writes computed watts to `ev_charge_target_entity`

### EV charging mode
`select.ems_ev_charging_mode` — options: `Grid-optimised`, `Price-optimised`, `Off`. `Off` forces `ev_charge_power_w = 0` and EMS writes 0 to the EV target entity.

### Target environment
- Home Assistant 2024.11+
- Python 3.12+
- Deployed via scp (see `deploy-ems` skill)
