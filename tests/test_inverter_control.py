"""Tests for inverter_control.decide() and inverter_control.recompute().

Inverter control algorithm:
  Off → inverter_verb=None, active_power_w=None (hands-off)
  Control Inverter + ev_power_w == 0 → verb=disabled_option, active_power_w=None
  Control Inverter + ev_power_w > 0  → verb=power_control_option,
                                        active_power_w=max(0, pv_w - house_non_ev)
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
    inverter_strategy: str = "Control Inverter",
    mapping: VerbMapping = _MAPPING,
) -> InverterDecision:
    return inverter_control_module.recompute(
        pv_w=pv_w,
        house_load_w=house_load_w,
        ev_power_w=ev_power_w,
        inverter_strategy=inverter_strategy,
        mapping=mapping,
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
    d = _decide(ev_power_w=3000.0)
    assert d.inverter_verb == "power_control"


def test_ev_charging_uses_power_control_option_from_mapping() -> None:
    mapping = VerbMapping(
        inverter_select_entity="select.inv",
        disabled_option="Self Use",
        power_control_option="Power Control",
        active_power_entity=None,
        ev_charge_target_entity=None,
    )
    d = _decide(ev_power_w=3000.0, mapping=mapping)
    assert d.inverter_verb == "Power Control"


def test_pv_surplus_produces_positive_active_power() -> None:
    """pv=5000, house_non_ev=1000 → active_power=4000 (PV surplus, battery charges)."""
    d = _decide(pv_w=5000.0, house_load_w=1000.0, ev_power_w=3000.0)
    # house_non_ev = max(0, 1000 - 3000) = 0; active_power = 5000 - 0 = 5000
    assert d.active_power_w == pytest.approx(5000.0)


def test_house_non_ev_subtracted_from_pv() -> None:
    """pv=3000, house=2000, ev=500 → house_non_ev=1500, active_power=1500."""
    d = _decide(pv_w=3000.0, house_load_w=2000.0, ev_power_w=500.0)
    # house_non_ev = max(0, 2000 - 500) = 1500; active_power = 3000 - 1500 = 1500
    assert d.active_power_w == pytest.approx(1500.0)


def test_battery_not_discharged_when_pv_below_house(builder) -> None:
    """When pv < house_non_ev, active_power is clamped to 0 (battery never discharges for EV)."""
    d = _decide(pv_w=500.0, house_load_w=2000.0, ev_power_w=3000.0)
    # house_non_ev = max(0, 2000 - 3000) = 0; active_power = 500 - 0 = 500 → still positive
    assert d.active_power_w == pytest.approx(500.0)


def test_battery_not_discharged_when_no_pv_and_house_load() -> None:
    """pv=0, house=1500, ev=500 → house_non_ev=1000, active_power=max(0, 0-1000)=0."""
    d = _decide(pv_w=0.0, house_load_w=1500.0, ev_power_w=500.0)
    # house_non_ev = max(0, 1500 - 500) = 1000; pv - house_non_ev = -1000 → clamped to 0
    assert d.active_power_w == pytest.approx(0.0)


def test_active_power_zero_when_pv_equals_house_non_ev() -> None:
    """pv=1000, house=1500, ev=500 → house_non_ev=1000, active_power=0."""
    d = _decide(pv_w=1000.0, house_load_w=1500.0, ev_power_w=500.0)
    assert d.active_power_w == pytest.approx(0.0)


def test_house_non_ev_cannot_be_negative() -> None:
    """EV draws more than total house load → house_non_ev=0."""
    d = _decide(pv_w=2000.0, house_load_w=500.0, ev_power_w=3000.0)
    # house_non_ev = max(0, 500 - 3000) = 0; active_power = 2000 - 0 = 2000
    assert d.active_power_w == pytest.approx(2000.0)


# ── None verb/options fallthrough ─────────────────────────────────────────────

def test_none_power_control_option_passed_as_verb() -> None:
    """When mapping.power_control_option is None, verb is None (maps to the configured option)."""
    d = _decide(ev_power_w=3000.0, mapping=_MAPPING_NO_OPTIONS)
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
    d1 = inverter_control_module.decide(inp)
    d2 = inverter_control_module.recompute(
        pv_w=inp.pv_w,
        house_load_w=inp.house_load_w,
        ev_power_w=inp.ev_power_w,
        inverter_strategy=inp.inverter_strategy,
        mapping=inp.mapping,
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
    d = _decide(ev_power_w=3000.0)
    assert d.status == "controlling"
