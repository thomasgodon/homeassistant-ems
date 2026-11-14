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

**Decision charts**: the README `### Decision flow` section contains two Mermaid flowcharts mirroring `strategy.py::_make_decision`/`apply_stop_debounce`/`apply_energy_stop_guards` (Chart A) and `inverter_control.py::_compute` (Chart B). Whenever a branch, gate, ordering, or reason string in those decision paths changes, **redraw the matching chart in the same task** — a chart that diverges from the code is a bug.

## Commands

```powershell
pytest tests/                  # Run all tests (80% coverage gate)
pytest tests/test_strategy.py -o addopts="" `
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
- For `Energy-optimised`, passes `energy_surplus_override_w = EmsState.ev_energy_surplus_w` (the slow loop's PV surplus) so the fast loop does **not** recompute the `house_load − ev_power` subtraction — see the Energy-optimised surplus sampling abstraction
- Calls `controller.write_full()` — keeps EV target and battery discharge cap enforced within 1 s of load changes

### Module boundaries

| Module | Rule |
|--------|------|
| `models.py` | Zero HA imports — frozen dataclasses only (`Inputs`, `EvDecision`, `InverterDecision`, `VerbMapping`) |
| `strategy.py` | Pure functions `decide(inputs) → EvDecision` and `recompute(...) → EvDecision`; no I/O, no HA |
| `controller.py` | The **only** module that calls HA services |
| `coordinator.py` | Wires the loops; reads HA state; calls strategy and controller |

### Key abstractions

- **Power balance algorithm**: `ev_target = max(0, max_grid_import_w − grid_import_w + last_ev_target_w)`. Integrating closed-loop on the **instantaneous** grid reading: `last_ev_target_w` is the EMA-smoothed value written to the wallbox on the previous cycle. Using the commanded target (not the measured EV draw) makes the loop a pure integrator on grid error, driving `grid_import → max_grid_import_w` regardless of wallbox quantisation or ramp lag. On cold start (`last_ev_target_w = None`), falls back to `ev_power_w`. The grid feedback must be near-instantaneous — a low-pass filter here (the removed 60 s demand-averaging EMA) adds phase lag that destabilises the integrator into a slow stop/start limit cycle.
- **Stop-debounce**: `strategy.apply_stop_debounce` keeps a *previously charging* session alive through a brief sub-minimum dip instead of idling and flapping the wallbox. When `decide`/`recompute` returns `not_enough_power` and the session was charging, the command is held at `ev_min_charge_w` (state `holding_through_dip`) for up to `debounce_s` (default `_EV_STOP_DEBOUNCE_S = 60.0`); only a *sustained* shortfall actually stops it. Hard gates (unplugged, Off, target SoC, outside cheap hours, battery_short) are never debounced. The function is pure: the dwell start timestamp lives in `EmsState.ev_below_min_since`, shared by both loops (the 1 s fast loop is authoritative; the slow loop applies the same helper so `write_slow` never momentarily writes 0). Both loops apply it **before** the inverter decision so `commanded_ev_charge_w` reflects the held minimum and the inverter doesn't flap.
- **Energy-optimised stop guards**: for `ev_strategy == "Energy-optimised"` **only**, both loops call `strategy.apply_energy_stop_guards` instead of the plain debounce (other modes are unchanged). It composes two independent, pure timers: (1) a **battery-drain hold** — `apply_stop_debounce` at the longer `_EV_BATTERY_DRAIN_S = 120.0`, so a sub-minimum dip is held at `ev_min_charge_w` (battery covers the shortfall — the inverter is on Self-Use, so it discharges naturally) for up to 2 min before stopping; and (2) a **grid-overload cutoff** — `_apply_grid_overload_stop`, which stops any charging command (normal `charging` or a held `holding_through_dip`) with reason `grid_overloaded` once `grid_import_w > _EV_GRID_OVERLOAD_W = 100.0` for `_EV_GRID_OVERLOAD_S = 30.0`, regardless of the battery-drain timer. Applied after the hold so the grid cutoff overrides it. The grid dwell timestamp lives in `EmsState.ev_grid_overload_since` (cleared to `None` for non-Energy modes); `grid_overloaded` is an `EvChargingState`.
- **Energy-optimised surplus sampling**: for `ev_strategy == "Energy-optimised"`, the PV surplus target (`strategy.energy_surplus_w = max(0, pv_w − max(0, house_load_w − ev_power_w))`) is computed **only on the 10 s slow loop**, stored in `EmsState.ev_energy_surplus_w`, and passed to the 1 s fast loop as `recompute(energy_surplus_override_w=…)`. `house_load` and `ev_power` are independent, asynchronous sensors (on the reference deployment, two KNX telegrams); the EV's multi-kW draw appears in both, so recomputing the subtraction every second let telegram skew inject up to the full EV power into the setpoint and the fast loop chased it into a large ~4 s limit cycle (wallbox flapping). Sampling the subtraction once per slow cycle severs that fast feedback; the fast loop still reacts at 1 s via the grid-overload guard and the EMA. `ev_energy_surplus_w` is `None` for non-Energy modes (and until the first slow cycle), in which case the fast loop falls back to recomputing surplus itself (harmless — only Energy-optimised reads it). This is *not* the same class of fix as the Power-balance integrator warning above: Energy-optimised is a proportional map, not an integrator, so coarser sampling merely slows response rather than destabilising it.
- **Solar-only**: `ev_strategy == "Solar-only"` targets the **whole** `pv_w` (capped by `max_ev_charge_w`, same 110 % start hysteresis as Energy-optimised via `strategy._pv_target_with_hysteresis`); the house is fed by battery then grid. Ignores `battery_short` and cheap hours. Both loops call `strategy.apply_solar_stop_guards` = `apply_stop_debounce` at `_EV_BATTERY_DRAIN_S` (120 s) with **no** grid-overload cutoff (`ev_grid_overload_since` cleared). `pv_w` is a single sensor, so no slow-loop surplus sampling (`ev_energy_surplus_w` stays `None`). `inverter_control._compute` Priority 3 releases the inverter to Self-Use for Solar-only as for Energy-optimised (never `battery_priority`/`controlling`).
- **Active power**: `inverter_control.py` emits `active_power_w = -min(pv_w, house_load_w)` as the default "controlling" output whenever EMS is commanding an EV charge target or the EV is still drawing. Sign convention: **negative = inverter drains PV → AC**; **positive = battery charges from AC** (planned feature, not yet written). Always ≤ 0: magnitude capped by `pv_w` (battery never drained for EV), sign never positive (battery never charged from AC). Controller writes it every cycle the ladder produces a non-None value.
- **One inverter verb**: `power_control_option` is asserted whenever `inverter_strategy == "Control Inverter"` AND (a) `commanded_ev_charge_w > 0` (EMS is actively charging), or (b) `ev_power_w > 0` (EV winding down), or (c) peak shaving or EOD suppress is active. EOD suppress fires even during a Price-optimised EV session in a cheap hour — when PV > house_non_ev, `active_power_w = -house_non_ev` (PV covers house, surplus to battery); when PV ≤ house_non_ev, `active_power_w = 0` (all PV to battery, house from cheap grid). Battery never discharges. For Grid-optimised sessions, EOD suppress is bypassed (EV integrator controls grid import). Otherwise EMS writes `disabled_option`, releasing the inverter to its native Self-Use Mode. Power Control is asserted on cycle 1 of a session, before the first target write. During daytime with PV producing but no active charging session, the inverter runs in Self-Use Mode autonomously — EMS stays out of the way.
- **EV charge target**: strategy emits `ev_charge_power_w` (float, W). The fast loop's EMA smooths this during active charging; when strategy returns any idle decision (EV not connected, outside cheap hours, etc.) the EMA snaps to exactly `0.0` — this is critical so that `commanded_ev_charge_w == 0.0` holds in `inverter_control._compute` and the idle branch fires (releasing active-power writes). Controller writes via `number.set_value` to `ev_charge_target_entity` (Step 3).
- **house_non_ev**: derived inline as `max(0.0, house_load_w − ev_power_w)` — never a stored field.

### Config flow (3 steps, no YAML editing)
1. Required sensors (grid power, PV, house load, EV power); optional: `max_grid_import_w` entity
2. Inverter (optional) — select entity + `power_control_option` string + `disabled_option` string + `active_power_entity`
3. EV charger (optional) — plug/SoC/target-SoC/charge-target/max-charge entity selectors; EMS reads entity states at runtime; writes computed watts to `ev_charge_target_entity`

### EV charging mode
`select.ems_ev_charging_mode` — options: `Grid-optimised`, `Price-optimised`, `Energy-optimised`, `Solar-only`, `Off`. `Off` forces `ev_charge_power_w = 0` and EMS writes 0 to the EV target entity.

### Target environment
- Home Assistant 2024.11+
- Python 3.12+
- Deployed via scp (see `deploy-ems` skill)
