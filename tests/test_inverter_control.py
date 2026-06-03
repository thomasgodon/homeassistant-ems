"""Tests for inverter_control.decide() and inverter_control.recompute().

Priority ladder (inverter_strategy == "Control Inverter"):
  1. Peak shaving       — grid_import > max_grid_import AND commanded_ev_charge_w == 0
                          AND ev_power_w == 0 (suppressed during EV sessions to prevent
                          double-correction with the EV integrator)
                          → verb=power_control, active_power=-(pv + excess), status=peak_shaving
                          (fires regardless of ev_strategy, including Energy-optimised)
                          Hysteresis: once latched, holds until grid < max − 500 W to
                          prevent the controller's own injection from re-triggering it.
  2. Deficit hold       — eod_suppress=True AND pv < house_load
                          AND (no EV session OR ev_strategy == "Price-optimised")
                          (skipped during Grid-optimised EV sessions — hold would steal PV from EV;
                          fires in Price-optimised EV sessions — EV is deliberately grid-fed)
                          → verb=power_control, active_power=0, status=holding_battery
                          (fires regardless of ev_strategy, including Energy-optimised)
  3. Energy-optimised   — ev_strategy == "Energy-optimised" (after peak shaving and deficit hold)
                          → verb=disabled_option, active_power=None, status=idle
  4. Idle               — commanded_ev_charge_w == 0 AND ev_power_w == 0
                          → verb=disabled_option, active_power=None, status=idle
  5. Default            — commanded_ev_charge_w > 0 OR ev_power_w > 0
                          → verb=power_control, active_power=-min(pv,house), status=controlling

commanded_ev_charge_w is the smoothed EV target EMS will write this cycle.
Off mode: never write anything (disabled status, all None).
"""
from __future__ import annotations

import pytest

from custom_components.ems import inverter_control as inverter_control_module
from custom_components.ems.models import InverterDecision, VerbMapping

_MAPPING = VerbMapping(
    inverter_select_entity="select.inverter",
    disabled_option="disabled",
    power_control_option="power_control",
    active_power_entity="number.battery_cap",
    ev_charge_target_entity="number.ev_charger_power",
)

_MAPPING_NO_OPTIONS = VerbMapping(
    inverter_select_entity="select.inverter",
    disabled_option=None,
    power_control_option=None,
    active_power_entity=None,
    ev_charge_target_entity=None,
)


def _decide(
    *,
    pv_w: float = 0.0,
    house_load_w: float = 0.0,
    ev_power_w: float = 0.0,
    grid_import_w: float = 0.0,
    inverter_strategy: str = "Control Inverter",
    mapping: VerbMapping = _MAPPING,
    eod_suppress: bool = False,
    max_grid_import_w: float = 4500.0,
    commanded_ev_charge_w: float = 0.0,
    battery_soc: float | None = None,
    battery_target_soc: float = 80.0,
    ev_strategy: str = "Grid-optimised",
    solar_shortfall_kwh_result: float | None = None,
    max_active_power_w: float | None = None,
    currently_peak_shaving: bool = False,
) -> InverterDecision:
    return inverter_control_module.recompute(
        pv_w=pv_w,
        house_load_w=house_load_w,
        ev_power_w=ev_power_w,
        grid_import_w=grid_import_w,
        inverter_strategy=inverter_strategy,
        mapping=mapping,
        eod_suppress=eod_suppress,
        max_grid_import_w=max_grid_import_w,
        commanded_ev_charge_w=commanded_ev_charge_w,
        battery_soc=battery_soc,
        battery_target_soc=battery_target_soc,
        ev_strategy=ev_strategy,
        solar_shortfall_kwh_result=solar_shortfall_kwh_result,
        max_active_power_w=max_active_power_w,
        currently_peak_shaving=currently_peak_shaving,
    )


# ── Off mode: never write anything ───────────────────────────────────────────

def test_off_mode_verb_is_none() -> None:
    d = _decide(inverter_strategy="Off")
    assert d.inverter_verb is None


def test_off_mode_active_power_is_none() -> None:
    d = _decide(inverter_strategy="Off", ev_power_w=3000.0)
    assert d.active_power_w is None


def test_off_mode_ev_and_pv_still_none() -> None:
    """Even when EV is drawing and PV is producing, Off mode produces None for both fields."""
    d = _decide(inverter_strategy="Off", pv_w=5000.0, house_load_w=500.0, ev_power_w=3000.0)
    assert d.inverter_verb is None
    assert d.active_power_w is None


def test_off_mode_status_is_disabled() -> None:
    d = _decide(inverter_strategy="Off")
    assert d.status == "disabled"


# ── Idle: no PV and no EV draw ────────────────────────────────────────────────

def test_idle_when_no_pv_and_no_ev() -> None:
    """pv=0 and ev_power=0 → inverter hands back to native mode."""
    d = _decide(pv_w=0.0, ev_power_w=0.0)
    assert d.status == "idle"
    assert d.inverter_verb == "disabled"
    assert d.active_power_w is None


def test_idle_uses_disabled_option_from_mapping() -> None:
    mapping = VerbMapping(
        inverter_select_entity="select.inv",
        disabled_option="Self Use",
        power_control_option="Power Control",
        active_power_entity=None,
        ev_charge_target_entity=None,
    )
    d = _decide(pv_w=0.0, ev_power_w=0.0, mapping=mapping)
    assert d.inverter_verb == "Self Use"


def test_idle_none_disabled_option_passed_as_verb() -> None:
    """When mapping.disabled_option is None, verb is None."""
    d = _decide(pv_w=0.0, ev_power_w=0.0, mapping=_MAPPING_NO_OPTIONS)
    assert d.inverter_verb is None


def test_idle_status() -> None:
    d = _decide(pv_w=0.0, ev_power_w=0.0)
    assert d.status == "idle"


# ── Controlling: power_control + clamped active_power ─────────────────────────

def test_idle_when_pv_only_and_no_ev_charging() -> None:
    """pv>0 but EMS not charging EV → release inverter to Self-Use, no active_power write."""
    d = _decide(pv_w=3000.0, house_load_w=1500.0, ev_power_w=0.0, commanded_ev_charge_w=0.0)
    assert d.status == "idle"
    assert d.inverter_verb == "disabled"
    assert d.active_power_w is None


def test_controlling_when_ev_only() -> None:
    """pv=0, EV drawing at night → inverter stays in power_control to protect battery."""
    d = _decide(pv_w=0.0, house_load_w=3000.0, ev_power_w=2000.0)
    assert d.status == "controlling"
    assert d.inverter_verb == "power_control"
    assert d.active_power_w == pytest.approx(0.0)


def test_controlling_when_pv_and_ev() -> None:
    """Both pv and ev_power > 0 → controlling."""
    d = _decide(pv_w=3000.0, house_load_w=4000.0, ev_power_w=2000.0)
    assert d.status == "controlling"


def test_controlling_verb_from_mapping() -> None:
    mapping = VerbMapping(
        inverter_select_entity="select.inv",
        disabled_option="Self Use",
        power_control_option="Power Control",
        active_power_entity=None,
        ev_charge_target_entity=None,
    )
    d = _decide(pv_w=2000.0, mapping=mapping, commanded_ev_charge_w=2000.0)
    assert d.inverter_verb == "Power Control"


def test_none_power_control_option_passed_as_verb() -> None:
    """When mapping.power_control_option is None, verb is None."""
    d = _decide(pv_w=2000.0, mapping=_MAPPING_NO_OPTIONS, commanded_ev_charge_w=2000.0)
    assert d.inverter_verb is None


def test_pv_below_house_load_drains_full_pv() -> None:
    """pv=500, house=4000 → active_power=-min(500,4000)=-500: all PV to AC, battery untouched."""
    d = _decide(pv_w=500.0, house_load_w=4000.0, commanded_ev_charge_w=500.0)
    assert d.active_power_w == pytest.approx(-500.0)


def test_pv_above_house_load_capped_by_demand() -> None:
    """pv=6000, house=1000 → active_power=-1000: cap at AC demand, surplus goes to battery."""
    d = _decide(pv_w=6000.0, house_load_w=1000.0, commanded_ev_charge_w=1000.0)
    assert d.active_power_w == pytest.approx(-1000.0)


def test_pv_equals_house_load() -> None:
    """pv=3000, house=3000 → active_power=-3000: balanced, battery untouched."""
    d = _decide(pv_w=3000.0, house_load_w=3000.0, commanded_ev_charge_w=3000.0)
    assert d.active_power_w == pytest.approx(-3000.0)


def test_no_pv_ev_charging_active_power_is_zero() -> None:
    """pv=0 but EV is drawing → active_power=0: nothing to direct, battery protected."""
    d = _decide(pv_w=0.0, house_load_w=4000.0, ev_power_w=2000.0)
    assert d.active_power_w == pytest.approx(0.0)
    assert d.status == "controlling"


def test_battery_never_charged_from_ac() -> None:
    """active_power is always ≤ 0 — battery is never commanded to charge from AC."""
    for pv, house in [(0, 0), (1000, 500), (500, 1000), (3000, 3000)]:
        d = _decide(pv_w=float(pv), house_load_w=float(house), ev_power_w=1.0)
        assert (d.active_power_w or 0.0) <= 0.0


def test_active_power_formula_uses_house_load_not_ev_power() -> None:
    """Formula uses house_load_w (full AC demand); ev_power_w doesn't change active_power."""
    d1 = _decide(pv_w=1000.0, house_load_w=4000.0, ev_power_w=0.0, commanded_ev_charge_w=1000.0)
    d2 = _decide(pv_w=1000.0, house_load_w=4000.0, ev_power_w=3500.0, commanded_ev_charge_w=1000.0)
    assert d1.active_power_w == d2.active_power_w == pytest.approx(-1000.0)


def test_idle_when_pv_but_no_commanded_charge() -> None:
    """pv>0 but EMS not charging → idle, Self-Use takes over."""
    d = _decide(pv_w=1000.0, ev_power_w=0.0, commanded_ev_charge_w=0.0)
    assert d.status == "idle"


def test_controlling_status_with_ev() -> None:
    d = _decide(pv_w=0.0, ev_power_w=500.0)
    assert d.status == "controlling"


def test_controlling_when_commanded_but_ev_not_yet_drawing() -> None:
    """EMS commands a charge target but EV hasn't started drawing (first cycle of session)."""
    d = _decide(pv_w=3000.0, ev_power_w=0.0, commanded_ev_charge_w=2000.0)
    assert d.status == "controlling"
    assert d.inverter_verb == "power_control"


def test_controlling_during_ev_wind_down() -> None:
    """EMS commanded 0 but EV is still drawing from AC (winding down after session)."""
    d = _decide(pv_w=3000.0, house_load_w=1500.0, ev_power_w=1200.0, commanded_ev_charge_w=0.0)
    assert d.status == "controlling"
    assert d.inverter_verb == "power_control"


# ── recompute matches decide ──────────────────────────────────────────────────

def test_recompute_matches_decide(builder) -> None:
    inp = builder.build(
        pv_w=3000.0,
        house_load_w=1500.0,
        ev_power_w=2000.0,
        grid_import_w=500.0,
        inverter_strategy="Control Inverter",
    )
    commanded = 2000.0
    d1 = inverter_control_module.decide(inp, commanded_ev_charge_w=commanded)
    d2 = inverter_control_module.recompute(
        pv_w=inp.pv_w,
        house_load_w=inp.house_load_w,
        ev_power_w=inp.ev_power_w,
        grid_import_w=inp.grid_import_w,
        inverter_strategy=inp.inverter_strategy,
        mapping=inp.mapping,
        max_grid_import_w=inp.thresholds.max_grid_import_w,
        commanded_ev_charge_w=commanded,
        ev_strategy=inp.ev_strategy,
    )
    assert d1 == d2


# ── Peak shaving ──────────────────────────────────────────────────────────────

def test_peak_shaving_when_grid_exceeds_cap() -> None:
    """grid_import > max_grid_import → battery discharges for excess."""
    d = _decide(
        pv_w=1000.0,
        grid_import_w=5500.0,
        max_grid_import_w=4500.0,
    )
    # excess = 5500 - 4500 = 1000; active_power = -(1000 + 1000) = -2000
    assert d.status == "peak_shaving"
    assert d.inverter_verb == "power_control"
    assert d.active_power_w == pytest.approx(-2000.0)


def test_peak_shaving_suppressed_during_ev_charging() -> None:
    """Peak shaving is suppressed when EV is drawing — would double-correct with EV integrator."""
    d = _decide(
        pv_w=1000.0,
        house_load_w=500.0,
        ev_power_w=3000.0,
        grid_import_w=5500.0,
        max_grid_import_w=4500.0,
    )
    assert d.status == "controlling"
    assert d.active_power_w == pytest.approx(-min(1000.0, 500.0))


def test_peak_shaving_suppressed_when_commanded_ev_charge() -> None:
    """Peak shaving suppressed on cycle 1 of a session (commanded > 0, EV not yet drawing)."""
    d = _decide(
        pv_w=1000.0,
        house_load_w=500.0,
        ev_power_w=0.0,
        commanded_ev_charge_w=2000.0,
        grid_import_w=5500.0,
        max_grid_import_w=4500.0,
    )
    assert d.status == "controlling"


def test_no_peak_shaving_when_grid_at_cap() -> None:
    """grid_import == max_grid_import → no peak shaving (not strictly greater)."""
    d = _decide(
        grid_import_w=4500.0,
        max_grid_import_w=4500.0,
    )
    assert d.status == "idle"


def test_peak_shaving_active_power_always_negative() -> None:
    """Peak shaving active_power is always ≤ 0."""
    d = _decide(
        pv_w=0.0,
        grid_import_w=6000.0,
        max_grid_import_w=4500.0,
    )
    assert d.active_power_w is not None
    assert d.active_power_w <= 0.0


def test_peak_shaving_latches_in_hysteresis_band() -> None:
    """Once in peak_shaving, status holds while grid is still above cap − 500 W."""
    # grid_import=4200 < cap=4500, but inside the 500 W band → latch holds
    d = _decide(
        pv_w=1000.0,
        grid_import_w=4200.0,
        max_grid_import_w=4500.0,
        currently_peak_shaving=True,
    )
    assert d.status == "peak_shaving"
    assert d.inverter_verb == "power_control"


def test_peak_shaving_releases_below_hysteresis_band() -> None:
    """Peak shaving releases once grid import drops below cap − 500 W."""
    # grid_import=3900 < 4500-500=4000 → released
    d = _decide(
        pv_w=1000.0,
        grid_import_w=3900.0,
        max_grid_import_w=4500.0,
        currently_peak_shaving=True,
    )
    assert d.status == "idle"
    assert d.inverter_verb == "disabled"


def test_peak_shaving_no_false_entry_below_cap() -> None:
    """Without a prior latch, grid below cap does not enter peak shaving."""
    # grid_import=4400 < cap=4500, currently_peak_shaving=False → no entry
    d = _decide(
        grid_import_w=4400.0,
        max_grid_import_w=4500.0,
        currently_peak_shaving=False,
    )
    assert d.status == "idle"


def test_peak_shaving_active_power_tapers_in_hold_band() -> None:
    """In the hold band excess_w is negative so active_power magnitude < pv_w."""
    # grid=4200, cap=4500 → excess=-300 → active_power = -(1000 + (-300)) = -700
    d = _decide(
        pv_w=1000.0,
        grid_import_w=4200.0,
        max_grid_import_w=4500.0,
        currently_peak_shaving=True,
    )
    assert d.active_power_w == pytest.approx(-700.0)


# ── Deficit hold (holding_battery) ────────────────────────────────────────────

def test_deficit_hold_fires_when_pv_below_house() -> None:
    """eod_suppress=True + pv < house_load → active_power=0, status=holding_battery."""
    d = _decide(pv_w=1000.0, house_load_w=2000.0, eod_suppress=True)
    assert d.status == "holding_battery"
    assert d.inverter_verb == "power_control"
    assert d.active_power_w == pytest.approx(0.0)


def test_deficit_hold_fires_at_night_when_pv_zero() -> None:
    """eod_suppress + no PV but house draws at night → hold fires (pv=0 < house_load)."""
    d = _decide(pv_w=0.0, house_load_w=2000.0, eod_suppress=True)
    assert d.status == "holding_battery"
    assert d.active_power_w == pytest.approx(0.0)


def test_deficit_hold_bypassed_when_pv_covers_house() -> None:
    """eod_suppress=True but pv >= house_load → Self-Use takes over, no hold."""
    d = _decide(pv_w=3000.0, house_load_w=2000.0, eod_suppress=True)
    assert d.status == "idle"


def test_deficit_hold_skipped_when_ev_drawing_grid_optimised() -> None:
    """Deficit hold is skipped in Grid-optimised EV sessions — would steal PV from EV."""
    d = _decide(
        pv_w=1000.0, house_load_w=5000.0, ev_power_w=3000.0,
        eod_suppress=True, ev_strategy="Grid-optimised",
    )
    assert d.status == "controlling"
    assert d.active_power_w == pytest.approx(-min(1000.0, 5000.0))


def test_deficit_hold_fires_when_ev_drawing_price_optimised() -> None:
    """Deficit hold fires in Price-optimised EV sessions — EV is deliberately grid-fed."""
    d = _decide(
        pv_w=1000.0, house_load_w=5000.0, ev_power_w=3000.0,
        eod_suppress=True, ev_strategy="Price-optimised",
    )
    assert d.status == "holding_battery"
    assert d.active_power_w == pytest.approx(0.0)


def test_deficit_hold_skipped_when_commanded_ev_charge_grid_optimised() -> None:
    """Deficit hold skipped on cycle 1 in Grid-optimised (commanded > 0, EV not yet drawing)."""
    d = _decide(
        pv_w=1000.0, house_load_w=5000.0, ev_power_w=0.0,
        commanded_ev_charge_w=2000.0, eod_suppress=True, ev_strategy="Grid-optimised",
    )
    assert d.status == "controlling"


def test_deficit_hold_fires_on_cycle1_price_optimised() -> None:
    """Deficit hold fires on cycle 1 of a Price-optimised session (commanded > 0)."""
    d = _decide(
        pv_w=1000.0, house_load_w=5000.0, ev_power_w=0.0,
        commanded_ev_charge_w=2000.0, eod_suppress=True, ev_strategy="Price-optimised",
    )
    assert d.status == "holding_battery"
    assert d.active_power_w == pytest.approx(0.0)


def test_deficit_hold_serves_house_non_ev_when_pv_exceeds_it_price_optimised() -> None:
    """Price-optimised EV + pv > house_non_ev: active=-house_non_ev, surplus PV to battery."""
    # pv=4000, house=7070, ev=4512 → house_non_ev=2558; pv(4000) > house_non_ev(2558)
    d = _decide(
        pv_w=4000.0, house_load_w=7070.0, ev_power_w=4512.0,
        eod_suppress=True, ev_strategy="Price-optimised",
    )
    assert d.status == "holding_battery"
    assert d.active_power_w == pytest.approx(-2558.0)


def test_eod_suppress_ignored_when_no_deficit() -> None:
    """eod_suppress=False → no hold regardless of PV vs house."""
    d = _decide(pv_w=0.0, house_load_w=2000.0, eod_suppress=False)
    assert d.status == "idle"


# ── Priority precedence ────────────────────────────────────────────────────────

def test_priority_peak_shave_over_deficit_hold() -> None:
    """Peak shaving beats deficit hold when both conditions are met."""
    d = _decide(
        pv_w=1000.0,
        house_load_w=2000.0,
        grid_import_w=5500.0,
        eod_suppress=True,
        max_grid_import_w=4500.0,
    )
    assert d.status == "peak_shaving"


def test_priority_deficit_hold_over_idle() -> None:
    """Deficit hold (pv < house) beats idle."""
    d = _decide(pv_w=500.0, house_load_w=1000.0, ev_power_w=0.0, eod_suppress=True)
    assert d.status == "holding_battery"


def test_priority_deficit_hold_over_controlling() -> None:
    """Deficit hold beats the default controlling branch when EV is idle."""
    d = _decide(pv_w=500.0, house_load_w=2000.0, ev_power_w=0.0, eod_suppress=True)
    assert d.status == "holding_battery"


# ── Energy-optimised: hands-off branch ───────────────────────────────────────

def test_energy_optimised_returns_idle_when_ev_idle() -> None:
    """Energy-optimised never touches the inverter — returns disabled verb, status=idle."""
    d = _decide(ev_strategy="Energy-optimised", pv_w=3000.0, house_load_w=1000.0)
    assert d.status == "idle"
    assert d.inverter_verb == "disabled"
    assert d.active_power_w is None


def test_energy_optimised_returns_idle_when_ev_charging() -> None:
    """Energy-optimised leaves inverter in Self-Use even when EV is drawing."""
    d = _decide(
        ev_strategy="Energy-optimised",
        pv_w=4000.0, house_load_w=1500.0,
        ev_power_w=2000.0, commanded_ev_charge_w=2000.0,
    )
    assert d.status == "idle"
    assert d.inverter_verb == "disabled"
    assert d.active_power_w is None


def test_energy_optimised_overrides_shortfall_branch() -> None:
    """Energy-optimised fires before battery-priority — shortfall must not trigger controlling."""
    d = _decide(
        ev_strategy="Energy-optimised",
        pv_w=4000.0, house_load_w=1500.0,
        ev_power_w=2000.0, commanded_ev_charge_w=2000.0,
        solar_shortfall_kwh_result=2.0,
    )
    assert d.status == "idle"


def test_energy_optimised_holds_battery_when_eod_suppress() -> None:
    """Energy-optimised does not block deficit hold — battery is preserved on cheap hours."""
    d = _decide(
        ev_strategy="Energy-optimised",
        pv_w=1700.0, house_load_w=3500.0,
        eod_suppress=True,
    )
    assert d.status == "holding_battery"
    assert d.inverter_verb == "power_control"
    assert d.active_power_w == pytest.approx(0.0)


def test_energy_optimised_peak_shaves_when_grid_over_cap() -> None:
    """Energy-optimised does not block peak shaving — grid cap is enforced."""
    d = _decide(
        ev_strategy="Energy-optimised",
        pv_w=1000.0,
        grid_import_w=5500.0,
        max_grid_import_w=4500.0,
    )
    assert d.status == "peak_shaving"
    assert d.inverter_verb == "power_control"
    assert d.active_power_w == pytest.approx(-2000.0)


# ── Battery priority: new shortfall branch ────────────────────────────────────

def test_battery_priority_fires_when_shortfall_and_ev_drawing() -> None:
    """shortfall > 0 + EV drawing → power_control, active_power=-house_non_ev."""
    d = _decide(
        pv_w=4000.0,
        house_load_w=3000.0,
        ev_power_w=2000.0,
        commanded_ev_charge_w=2000.0,
        solar_shortfall_kwh_result=1.5,
    )
    assert d.status == "battery_priority"
    assert d.inverter_verb == "power_control"
    # house_non_ev = max(0, 3000 - 2000) = 1000
    assert d.active_power_w == pytest.approx(-1000.0)


def test_battery_priority_fires_when_commanded_but_no_draw_yet() -> None:
    """Battery priority also fires on cycle 1 (commanded > 0, EV not yet drawing)."""
    d = _decide(
        pv_w=4000.0,
        house_load_w=1500.0,
        ev_power_w=0.0,
        commanded_ev_charge_w=2000.0,
        solar_shortfall_kwh_result=1.5,
    )
    assert d.status == "battery_priority"
    # house_non_ev = max(0, 1500 - 0) = 1500
    assert d.active_power_w == pytest.approx(-1500.0)


def test_battery_priority_not_fired_when_ev_idle() -> None:
    """No EV draw and commanded=0 → shortfall does not trigger battery_priority."""
    d = _decide(
        pv_w=4000.0, house_load_w=1500.0, ev_power_w=0.0, commanded_ev_charge_w=0.0,
        solar_shortfall_kwh_result=2.0,
    )
    assert d.status == "idle"


def test_battery_priority_not_fired_when_shortfall_zero() -> None:
    """shortfall=0 → falls through to default controlling branch."""
    d = _decide(
        pv_w=4000.0, house_load_w=3000.0, ev_power_w=2000.0,
        commanded_ev_charge_w=2000.0, solar_shortfall_kwh_result=0.0,
    )
    assert d.status == "controlling"


def test_battery_priority_not_fired_when_shortfall_none() -> None:
    """shortfall=None (missing forecast) → falls through to default controlling branch."""
    d = _decide(
        pv_w=4000.0, house_load_w=3000.0, ev_power_w=2000.0,
        commanded_ev_charge_w=2000.0, solar_shortfall_kwh_result=None,
    )
    assert d.status == "controlling"


def test_p2_takes_precedence_over_p4_in_price_optimised() -> None:
    """P2 (deficit hold) beats P4 (battery_priority) in Price-optimised + cheap hour + EV session.

    Bug repro: PV=1701, house=7070, EV=4512, house_non_ev=2558, shortfall>0, eod_suppress=True.
    Expected: hold battery at 0W (P2), NOT discharge battery via battery_priority (P4).
    """
    d = _decide(
        pv_w=1701.0,
        house_load_w=7070.0,
        ev_power_w=4512.0,
        eod_suppress=True,
        ev_strategy="Price-optimised",
        solar_shortfall_kwh_result=1.33,
    )
    assert d.status == "holding_battery"
    assert d.active_power_w == pytest.approx(0.0)


def test_p4_still_fires_in_price_optimised_when_not_eod_suppress() -> None:
    """P4 (battery_priority) fires for Price-optimised outside a cheap hour (eod_suppress=False)."""
    d = _decide(
        pv_w=1701.0,
        house_load_w=7070.0,
        ev_power_w=4512.0,
        eod_suppress=False,
        ev_strategy="Price-optimised",
        solar_shortfall_kwh_result=1.33,
    )
    assert d.status == "battery_priority"
    # house_non_ev = max(0, 7070 - 4512) = 2558
    assert d.active_power_w == pytest.approx(-2558.0)


def test_p4_fires_in_price_optimised_when_pv_exceeds_house_load() -> None:
    """P2 falls through when pv >= house_load; P4 fires, no discharge (PV covers house_non_ev)."""
    d = _decide(
        pv_w=8000.0,
        house_load_w=7070.0,
        ev_power_w=4512.0,
        eod_suppress=True,
        ev_strategy="Price-optimised",
        solar_shortfall_kwh_result=1.33,
    )
    assert d.status == "battery_priority"
    # house_non_ev = max(0, 7070 - 4512) = 2558; PV (8000) > 2558, no battery discharge
    assert d.active_power_w == pytest.approx(-2558.0)


# ── max_active_power_w clamping ────────────────────────────────────────────────

def test_controlling_clamps_active_power_to_cap() -> None:
    """When PV and house_load both exceed the inverter range, active_power is clamped to ±cap."""
    d = _decide(
        pv_w=5282.0,
        house_load_w=7500.0,
        ev_power_w=5000.0,
        commanded_ev_charge_w=5000.0,
        max_active_power_w=5000.0,
    )
    assert d.status == "controlling"
    # -min(5282, 7500) = -5282 clamped to -5000
    assert d.active_power_w == pytest.approx(-5000.0)


def test_controlling_no_clamp_when_within_range() -> None:
    """When computed value is within cap, no clamping occurs."""
    d = _decide(
        pv_w=3000.0,
        house_load_w=4000.0,
        ev_power_w=2000.0,
        commanded_ev_charge_w=2000.0,
        max_active_power_w=5000.0,
    )
    assert d.status == "controlling"
    # -min(3000, 4000) = -3000, well within ±5000
    assert d.active_power_w == pytest.approx(-3000.0)


def test_controlling_no_clamp_when_cap_none() -> None:
    """Without a cap (max_active_power_w=None), raw computed value is written."""
    d = _decide(
        pv_w=5282.0,
        house_load_w=7500.0,
        ev_power_w=5000.0,
        commanded_ev_charge_w=5000.0,
        max_active_power_w=None,
    )
    assert d.status == "controlling"
    assert d.active_power_w == pytest.approx(-5282.0)


def test_peak_shaving_clamps_active_power() -> None:
    """Peak shaving clamps -(pv + excess) when it would exceed the cap."""
    d = _decide(
        pv_w=5000.0,
        grid_import_w=6000.0,
        max_grid_import_w=4500.0,
        commanded_ev_charge_w=0.0,
        ev_power_w=0.0,
        max_active_power_w=5000.0,
    )
    assert d.status == "peak_shaving"
    # excess = 6000 - 4500 = 1500; -(5000 + 1500) = -6500 clamped to -5000
    assert d.active_power_w == pytest.approx(-5000.0)


def test_holding_battery_clamps_active_power() -> None:
    """Deficit hold clamps -house_non_ev when house_non_ev exceeds cap."""
    d = _decide(
        pv_w=4000.0,
        house_load_w=8000.0,
        ev_power_w=2000.0,
        eod_suppress=True,
        max_active_power_w=5000.0,
    )
    assert d.status == "holding_battery"
    # pv (4000) > house_non_ev (6000)? No → active_power = -house_non_ev = -6000 → clamped -5000
    assert d.active_power_w == pytest.approx(-5000.0)


def test_battery_priority_clamps_active_power() -> None:
    """Battery priority clamps -house_non_ev when it would exceed the cap."""
    d = _decide(
        pv_w=4000.0,
        house_load_w=10000.0,
        ev_power_w=4000.0,
        commanded_ev_charge_w=4000.0,
        solar_shortfall_kwh_result=2.0,
        max_active_power_w=5000.0,
    )
    assert d.status == "battery_priority"
    # house_non_ev = 10000 - 4000 = 6000; -6000 clamped to -5000
    assert d.active_power_w == pytest.approx(-5000.0)
