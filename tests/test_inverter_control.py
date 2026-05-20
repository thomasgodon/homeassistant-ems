"""Tests for inverter_control.decide() and inverter_control.recompute().

Priority ladder (inverter_strategy == "Control Inverter"):
  1. Peak shaving       — grid_import > max_grid_import AND commanded_ev_charge_w == 0
                          AND ev_power_w == 0 (suppressed during EV sessions to prevent
                          double-correction with the EV integrator)
                          → verb=power_control, active_power=-(pv + excess), status=peak_shaving
  2. EOD suppress       — eod_suppress=True AND pv>0
                          → verb=power_control, active_power=0, status=holding_battery
  3. Cheap-hour hold    — no EV AND cheap_for_inverter=True
                          AND (battery_soc is None OR battery_soc < battery_target_soc)
                          → verb=power_control, active_power=0, status=holding_battery_cheap
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
    cheap_for_inverter: bool = False,
    battery_soc: float | None = None,
    battery_target_soc: float = 80.0,
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
        cheap_for_inverter=cheap_for_inverter,
        battery_soc=battery_soc,
        battery_target_soc=battery_target_soc,
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


# ── EOD suppress (holding_battery) ────────────────────────────────────────────

def test_eod_suppress_with_pv_holds_battery() -> None:
    """eod_suppress=True + pv>0 → active_power=0, status=holding_battery."""
    d = _decide(pv_w=3000.0, eod_suppress=True)
    assert d.status == "holding_battery"
    assert d.inverter_verb == "power_control"
    assert d.active_power_w == pytest.approx(0.0)


def test_eod_suppress_ignored_without_pv() -> None:
    """eod_suppress but no PV (night) → idle, no write."""
    d = _decide(pv_w=0.0, ev_power_w=0.0, eod_suppress=True)
    assert d.status == "idle"


def test_eod_suppress_with_ev_drawing_holds_battery() -> None:
    """Battery hold takes priority over controlling when EOD suppress is active with PV."""
    d = _decide(pv_w=3000.0, house_load_w=1500.0, ev_power_w=2000.0, eod_suppress=True)
    assert d.status == "holding_battery"
    assert d.active_power_w == pytest.approx(0.0)


# ── Priority precedence ────────────────────────────────────────────────────────

def test_priority_peak_shave_over_eod_suppress() -> None:
    """Peak shaving beats EOD suppress when both conditions are met."""
    d = _decide(
        pv_w=1000.0,
        grid_import_w=5500.0,
        eod_suppress=True,
        max_grid_import_w=4500.0,
    )
    assert d.status == "peak_shaving"


def test_priority_eod_suppress_over_idle() -> None:
    """EOD suppress (pv>0) beats idle."""
    d = _decide(pv_w=500.0, ev_power_w=0.0, eod_suppress=True)
    assert d.status == "holding_battery"


def test_priority_eod_suppress_over_controlling() -> None:
    """EOD suppress beats the default controlling branch."""
    d = _decide(pv_w=2000.0, ev_power_w=1000.0, eod_suppress=True)
    assert d.status == "holding_battery"


# ── Cheap-hour hold ───────────────────────────────────────────────────────────

def test_cheap_hour_idle_holds_battery() -> None:
    """Cheap hour + no EV → power_control, active_power=0, status=holding_battery_cheap."""
    d = _decide(pv_w=3000.0, house_load_w=1500.0, cheap_for_inverter=True)
    assert d.status == "holding_battery_cheap"
    assert d.inverter_verb == "power_control"
    assert d.active_power_w == pytest.approx(0.0)


def test_expensive_hour_idle_releases_to_self_use() -> None:
    """Expensive hour (cheap_for_inverter=False) + no EV → idle / Self-Use Mode."""
    d = _decide(pv_w=3000.0, house_load_w=1500.0, cheap_for_inverter=False)
    assert d.status == "idle"
    assert d.inverter_verb == "disabled"
    assert d.active_power_w is None


def test_cheap_hour_ev_charging_unchanged() -> None:
    """Cheap hour + EV drawing → cheap-hour hold does not preempt EV charging path."""
    d = _decide(pv_w=3000.0, house_load_w=1500.0, ev_power_w=2000.0, cheap_for_inverter=True)
    assert d.status == "controlling"
    assert d.active_power_w == pytest.approx(-min(3000.0, 1500.0))


def test_cheap_hour_commanded_ev_charge_unchanged() -> None:
    """Cheap hour + commanded EV charge > 0 → controlling, not cheap hold."""
    d = _decide(
        pv_w=3000.0, house_load_w=1500.0, commanded_ev_charge_w=1000.0, cheap_for_inverter=True
    )
    assert d.status == "controlling"


def test_peak_shaving_beats_cheap_hour_hold() -> None:
    """Grid spike beats cheap-hour hold — peak shaving is Priority 1."""
    d = _decide(
        pv_w=1000.0,
        grid_import_w=5500.0,
        max_grid_import_w=4500.0,
        cheap_for_inverter=True,
    )
    assert d.status == "peak_shaving"
    assert d.active_power_w == pytest.approx(-2000.0)


def test_eod_suppress_beats_cheap_hour_hold() -> None:
    """EOD suppress (Priority 2) beats cheap-hour hold (Priority 3)."""
    d = _decide(pv_w=3000.0, eod_suppress=True, cheap_for_inverter=True)
    assert d.status == "holding_battery"


def test_cheap_hour_hold_without_prices_falls_through() -> None:
    """cheap_for_inverter=False (no prices) → idle, not cheap hold."""
    d = _decide(pv_w=3000.0, cheap_for_inverter=False)
    assert d.status == "idle"


def test_cheap_hour_hold_fires_when_battery_soc_unknown() -> None:
    """battery_soc=None + cheap hour → hold conservatively (no sensor to say it's full)."""
    d = _decide(pv_w=3000.0, house_load_w=1500.0, cheap_for_inverter=True, battery_soc=None)
    assert d.status == "holding_battery_cheap"


def test_cheap_hour_hold_fires_when_battery_below_target() -> None:
    """battery_soc < battery_target_soc + cheap hour → hold."""
    d = _decide(
        pv_w=3000.0, house_load_w=1500.0, cheap_for_inverter=True,
        battery_soc=50.0, battery_target_soc=80.0,
    )
    assert d.status == "holding_battery_cheap"


def test_cheap_hour_hold_skipped_when_battery_at_target() -> None:
    """battery_soc == battery_target_soc + cheap hour → idle (battery already full)."""
    d = _decide(
        pv_w=3000.0, house_load_w=1500.0, cheap_for_inverter=True,
        battery_soc=80.0, battery_target_soc=80.0,
    )
    assert d.status == "idle"
    assert d.inverter_verb == "disabled"
    assert d.active_power_w is None


def test_cheap_hour_hold_skipped_when_battery_above_target() -> None:
    """battery_soc > battery_target_soc + cheap hour → idle (no need to grid-fill)."""
    d = _decide(
        pv_w=3000.0, house_load_w=1500.0, cheap_for_inverter=True,
        battery_soc=95.0, battery_target_soc=80.0,
    )
    assert d.status == "idle"
    assert d.inverter_verb == "disabled"
    assert d.active_power_w is None


# ── _is_cheap_hour_for_inverter helper ───────────────────────────────────────

def test_helper_none_prices_returns_false() -> None:
    assert inverter_control_module._is_cheap_hour_for_inverter(None, 10) is False


def test_helper_short_tuple_returns_false() -> None:
    assert inverter_control_module._is_cheap_hour_for_inverter((1.0,) * 23, 10) is False


def test_helper_threshold_is_midpoint() -> None:
    """With prices 1.0×23 + 5.0, threshold=(1+5)/2=3.0; hour 23 is expensive, rest are cheap."""
    prices = (1.0,) * 23 + (5.0,)
    assert inverter_control_module._is_cheap_hour_for_inverter(prices, 0) is True
    assert inverter_control_module._is_cheap_hour_for_inverter(prices, 22) is True
    assert inverter_control_module._is_cheap_hour_for_inverter(prices, 23) is False


def test_helper_boundary_at_threshold_is_cheap() -> None:
    """Price exactly at threshold → cheap (<=)."""
    prices = tuple([1.0] * 12 + [5.0] * 12)
    threshold = (1.0 + 5.0) / 2  # 3.0; price[0]=1.0≤3.0 cheap, price[12]=5.0>3.0 expensive
    assert inverter_control_module._is_cheap_hour_for_inverter(prices, 0) is True
    assert inverter_control_module._is_cheap_hour_for_inverter(prices, 12) is False
    _ = threshold  # referenced for documentation only


def test_helper_all_equal_prices_are_cheap() -> None:
    """All prices equal → threshold equals price → all hours are cheap (<=)."""
    prices = (3.0,) * 24
    assert inverter_control_module._is_cheap_hour_for_inverter(prices, 0) is True
    assert inverter_control_module._is_cheap_hour_for_inverter(prices, 23) is True
