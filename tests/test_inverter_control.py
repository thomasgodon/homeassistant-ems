"""Tests for inverter_control.decide() and inverter_control.recompute().

Inverter control algorithm:
  Off → inverter_verb=None, active_power_w=None (hands-off)
  Control Inverter + ev_charging_commanded=False → verb=disabled_option, active_power_w=None
  Control Inverter + ev_charging_commanded=True  → verb=power_control_option,
                                                    active_power_w=-min(pv_w, house_load_w)

active_power is clamped to ≤ 0: magnitude never exceeds pv_w so battery is never drained;
sign is always ≤ 0 so battery is never charged from AC. PV is directed to AC up to AC demand.

The gate is ev_charging_commanded (strategy output), NOT measured ev_power_w, to avoid a
start-of-session deadlock where the inverter would never switch before the EV begins drawing.
"""
from __future__ import annotations

import pytest

from custom_components.ems import inverter_control as inverter_control_module
from custom_components.ems.models import EvDecision, InverterDecision, VerbMapping

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
    inverter_strategy: str = "Control Inverter",
    mapping: VerbMapping = _MAPPING,
    ev_charging_commanded: bool = False,
) -> InverterDecision:
    return inverter_control_module.recompute(
        pv_w=pv_w,
        house_load_w=house_load_w,
        ev_power_w=ev_power_w,
        inverter_strategy=inverter_strategy,
        mapping=mapping,
        ev_charging_commanded=ev_charging_commanded,
    )


# ── Off mode: never write anything ───────────────────────────────────────────

def test_off_mode_verb_is_none() -> None:
    d = _decide(inverter_strategy="Off")
    assert d.inverter_verb is None


def test_off_mode_active_power_is_none() -> None:
    d = _decide(inverter_strategy="Off", ev_power_w=3000.0)
    assert d.active_power_w is None


def test_off_mode_ev_charging_still_none() -> None:
    """Even when EV is drawing, Off mode produces None for both fields."""
    d = _decide(inverter_strategy="Off", pv_w=5000.0, house_load_w=500.0, ev_power_w=3000.0)
    assert d.inverter_verb is None
    assert d.active_power_w is None


# ── Control Inverter, EV idle: assert disabled verb ───────────────────────────

def test_ev_idle_verb_is_disabled() -> None:
    d = _decide(ev_power_w=0.0)
    assert d.inverter_verb == "disabled"


def test_ev_idle_active_power_is_none() -> None:
    """When EV is not drawing, active_power is not written."""
    d = _decide(pv_w=5000.0, house_load_w=1000.0, ev_power_w=0.0)
    assert d.active_power_w is None


def test_ev_idle_uses_disabled_option_from_mapping() -> None:
    mapping = VerbMapping(
        inverter_select_entity="select.inv",
        disabled_option="Self Use",
        power_control_option="Power Control",
        active_power_entity=None,
        ev_charge_target_entity=None,
    )
    d = _decide(ev_power_w=0.0, mapping=mapping)
    assert d.inverter_verb == "Self Use"


# ── Control Inverter, EV charging: power_control + clamped active_power ───────

def test_ev_charging_verb_is_power_control() -> None:
    d = _decide(ev_charging_commanded=True)
    assert d.inverter_verb == "power_control"


def test_ev_charging_uses_power_control_option_from_mapping() -> None:
    mapping = VerbMapping(
        inverter_select_entity="select.inv",
        disabled_option="Self Use",
        power_control_option="Power Control",
        active_power_entity=None,
        ev_charge_target_entity=None,
    )
    d = _decide(ev_charging_commanded=True, mapping=mapping)
    assert d.inverter_verb == "Power Control"


def test_pv_below_house_load_drains_full_pv() -> None:
    """pv=500, house=4000 → active_power=-min(500,4000)=-500: all PV to AC, battery untouched."""
    d = _decide(pv_w=500.0, house_load_w=4000.0, ev_charging_commanded=True)
    assert d.active_power_w == pytest.approx(-500.0)


def test_pv_above_house_load_capped_by_demand() -> None:
    """pv=6000, house=1000 → active_power=-1000: cap at AC demand, surplus goes to battery."""
    d = _decide(pv_w=6000.0, house_load_w=1000.0, ev_charging_commanded=True)
    assert d.active_power_w == pytest.approx(-1000.0)


def test_pv_equals_house_load() -> None:
    """pv=3000, house=3000 → active_power=-3000: balanced, battery untouched."""
    d = _decide(pv_w=3000.0, house_load_w=3000.0, ev_charging_commanded=True)
    assert d.active_power_w == pytest.approx(-3000.0)


def test_no_pv_active_power_is_zero() -> None:
    """pv=0 → active_power=0: nothing to drain, inverter effectively idle."""
    d = _decide(pv_w=0.0, house_load_w=4000.0, ev_charging_commanded=True)
    assert d.active_power_w == pytest.approx(0.0)


def test_battery_never_charged_from_ac() -> None:
    """active_power is always ≤ 0 — battery is never commanded to charge from AC."""
    for pv, house in [(0, 0), (1000, 500), (500, 1000), (3000, 3000)]:
        d = _decide(pv_w=float(pv), house_load_w=float(house), ev_charging_commanded=True)
        assert (d.active_power_w or 0.0) <= 0.0


def test_ev_power_not_needed_for_formula() -> None:
    """Formula uses house_load_w (full AC demand); ev_power_w doesn't change active_power."""
    d1 = _decide(pv_w=1000.0, house_load_w=4000.0, ev_power_w=0.0, ev_charging_commanded=True)
    d2 = _decide(pv_w=1000.0, house_load_w=4000.0, ev_power_w=3500.0, ev_charging_commanded=True)
    assert d1.active_power_w == d2.active_power_w == pytest.approx(-1000.0)


# ── None verb/options fallthrough ─────────────────────────────────────────────

def test_none_power_control_option_passed_as_verb() -> None:
    """When mapping.power_control_option is None, verb is None (maps to the configured option)."""
    d = _decide(ev_charging_commanded=True, mapping=_MAPPING_NO_OPTIONS)
    assert d.inverter_verb is None


def test_none_disabled_option_passed_as_verb() -> None:
    """When mapping.disabled_option is None, verb is None."""
    d = _decide(ev_power_w=0.0, mapping=_MAPPING_NO_OPTIONS)
    assert d.inverter_verb is None


# ── recompute matches decide ──────────────────────────────────────────────────

def test_recompute_matches_decide(builder) -> None:
    inp = builder.build(
        pv_w=3000.0,
        house_load_w=1500.0,
        ev_power_w=2000.0,
        inverter_strategy="Control Inverter",
    )
    ev_decision = EvDecision(
        strategy="ev_charging", ev_charge_power_w=2000.0, charging_state="charging"
    )
    d1 = inverter_control_module.decide(inp, ev_decision)
    d2 = inverter_control_module.recompute(
        pv_w=inp.pv_w,
        house_load_w=inp.house_load_w,
        ev_power_w=inp.ev_power_w,
        inverter_strategy=inp.inverter_strategy,
        mapping=inp.mapping,
        ev_charging_commanded=True,
    )
    assert d1 == d2


# ── status field ──────────────────────────────────────────────────────────────

def test_off_mode_status_is_disabled() -> None:
    d = _decide(inverter_strategy="Off")
    assert d.status == "disabled"


def test_ev_idle_status_is_idle() -> None:
    d = _decide(ev_power_w=0.0)
    assert d.status == "idle"


def test_ev_charging_status_is_controlling() -> None:
    d = _decide(ev_charging_commanded=True)
    assert d.status == "controlling"


# ── Regression: commanded vs measured gating ──────────────────────────────────

def test_commanded_charging_with_zero_ev_power_uses_power_control() -> None:
    """Strategy commands charging but EV not yet drawing → inverter must switch immediately.

    This was the bug: ev_power_w=0 caused disabled_option even when EV charging was commanded,
    creating a deadlock where the inverter never switched before the wallbox started drawing.
    """
    d = _decide(ev_charging_commanded=True, ev_power_w=0.0)
    assert d.inverter_verb == "power_control"
    assert d.status == "controlling"


def test_uncommanded_with_residual_ev_power_uses_disabled() -> None:
    """Phantom/residual EV draw must not pin the inverter to power_control.

    When the strategy is not commanding charging (e.g. mode Off, SoC reached),
    the inverter must revert to disabled_option regardless of measured ev_power_w.
    """
    d = _decide(ev_charging_commanded=False, ev_power_w=500.0)
    assert d.inverter_verb == "disabled"
    assert d.status == "idle"
