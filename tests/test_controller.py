from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.ems.controller import Controller
from custom_components.ems.models import EvDecision, InverterDecision, VerbMapping

_MAPPING = VerbMapping(
    inverter_select_entity="select.inverter",
    disabled_option="disabled",
    power_control_option="power_control",
    active_power_entity=None,
    ev_charge_target_entity=None,
)

_MAPPING_WITH_TARGET = VerbMapping(
    inverter_select_entity="select.inverter",
    disabled_option="disabled",
    power_control_option="power_control",
    active_power_entity="number.battery_cap",
    ev_charge_target_entity="number.ev_charger_power",
)

_MAPPING_INPUT_NUMBER = VerbMapping(
    inverter_select_entity="select.inverter",
    disabled_option="disabled",
    power_control_option="power_control",
    active_power_entity="input_number.active_power",
    ev_charge_target_entity="input_number.ev_charger_current",
)

_MAPPING_NO_INVERTER = VerbMapping(
    inverter_select_entity=None,
    disabled_option=None,
    power_control_option=None,
    active_power_entity=None,
    ev_charge_target_entity=None,
)


def _ev_charging(ev_charge_power_w: float = 3500.0) -> EvDecision:
    return EvDecision(
        strategy="ev_charging", ev_charge_power_w=ev_charge_power_w, charging_state="charging"
    )


def _ev_idle() -> EvDecision:
    return EvDecision(strategy="ev_idle", ev_charge_power_w=0.0, charging_state="not_enough_power")


def _inv_power_control(active_power_w: float = 200.0) -> InverterDecision:
    return InverterDecision(inverter_verb="power_control", active_power_w=active_power_w)


def _inv_disabled() -> InverterDecision:
    return InverterDecision(inverter_verb="disabled", active_power_w=None)


def _inv_off() -> InverterDecision:
    return InverterDecision(inverter_verb=None, active_power_w=None)


def _make_hass():
    hass = MagicMock()
    hass.services = MagicMock()
    hass.services.async_call = AsyncMock()
    return hass


@pytest.fixture
def controller():
    hass = _make_hass()
    return Controller(hass, _MAPPING), hass


@pytest.fixture
def controller_with_target():
    hass = _make_hass()
    return Controller(hass, _MAPPING_WITH_TARGET), hass


# ── write_slow: verb ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_write_slow_power_control_writes_verb(controller_with_target) -> None:
    ctrl, hass = controller_with_target
    await ctrl.write_slow(_ev_charging(), _inv_power_control())
    calls = hass.services.async_call.call_args_list
    select_calls = [c for c in calls if c[0][1] == "select_option"]
    assert len(select_calls) == 1
    assert select_calls[0][0][2]["option"] == "power_control"


@pytest.mark.asyncio
async def test_write_slow_disabled_writes_verb(controller_with_target) -> None:
    ctrl, hass = controller_with_target
    await ctrl.write_slow(_ev_idle(), _inv_disabled())
    calls = hass.services.async_call.call_args_list
    select_calls = [c for c in calls if c[0][1] == "select_option"]
    assert len(select_calls) == 1
    assert select_calls[0][0][2]["option"] == "disabled"


@pytest.mark.asyncio
async def test_write_slow_off_mode_no_verb_write(controller_with_target) -> None:
    """inverter_verb=None (Off mode) → no select_option call."""
    ctrl, hass = controller_with_target
    await ctrl.write_slow(_ev_idle(), _inv_off())
    calls = hass.services.async_call.call_args_list
    select_calls = [c for c in calls if c[0][1] == "select_option"]
    assert len(select_calls) == 0


@pytest.mark.asyncio
async def test_write_slow_writes_verb_every_call() -> None:
    """Verb is always written by write_slow — no idempotency cache for the select."""
    hass = _make_hass()
    ctrl = Controller(hass, _MAPPING_WITH_TARGET)
    await ctrl.write_slow(_ev_idle(), _inv_disabled())
    await ctrl.write_slow(_ev_idle(), _inv_disabled())
    calls = hass.services.async_call.call_args_list
    select_calls = [c for c in calls if c[0][1] == "select_option"]
    assert len(select_calls) == 2


@pytest.mark.asyncio
async def test_write_slow_no_inverter_entity_no_select_call() -> None:
    hass = _make_hass()
    ctrl = Controller(hass, _MAPPING_NO_INVERTER)
    await ctrl.write_slow(_ev_idle(), _inv_disabled())
    calls = hass.services.async_call.call_args_list
    select_calls = [c for c in calls if c[0][1] == "select_option"]
    assert len(select_calls) == 0


# ── write_slow: active_power ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_write_slow_writes_active_power_when_not_none(controller_with_target) -> None:
    ctrl, hass = controller_with_target
    await ctrl.write_slow(_ev_charging(), _inv_power_control(active_power_w=150.0))
    cap_calls = [
        c for c in hass.services.async_call.call_args_list
        if c[0][1] == "set_value" and c[0][2].get("entity_id") == "number.battery_cap"
    ]
    assert len(cap_calls) == 1
    assert cap_calls[0][0][2]["value"] == 150.0


@pytest.mark.asyncio
async def test_write_slow_skips_active_power_when_none(controller_with_target) -> None:
    ctrl, hass = controller_with_target
    await ctrl.write_slow(_ev_idle(), _inv_disabled())
    cap_calls = [
        c for c in hass.services.async_call.call_args_list
        if c[0][1] == "set_value" and c[0][2].get("entity_id") == "number.battery_cap"
    ]
    assert len(cap_calls) == 0


@pytest.mark.asyncio
async def test_write_slow_active_power_idempotent(controller_with_target) -> None:
    """Same active_power value twice → single service call."""
    ctrl, hass = controller_with_target
    await ctrl.write_slow(_ev_charging(), _inv_power_control(active_power_w=200.0))
    await ctrl.write_slow(_ev_charging(), _inv_power_control(active_power_w=200.0))
    cap_calls = [
        c for c in hass.services.async_call.call_args_list
        if c[0][1] == "set_value" and c[0][2].get("entity_id") == "number.battery_cap"
    ]
    assert len(cap_calls) == 1


# ── write_slow: EV target ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_write_slow_always_writes_ev_target(controller_with_target) -> None:
    ctrl, hass = controller_with_target
    await ctrl.write_slow(_ev_charging(ev_charge_power_w=3000.0), _inv_power_control())
    ev_calls = [
        c for c in hass.services.async_call.call_args_list
        if c[0][1] == "set_value" and c[0][2].get("entity_id") == "number.ev_charger_power"
    ]
    assert len(ev_calls) == 1
    assert ev_calls[0][0][2]["value"] == 3000.0


@pytest.mark.asyncio
async def test_write_slow_writes_ev_target_even_with_off_mode(controller_with_target) -> None:
    ctrl, hass = controller_with_target
    await ctrl.write_slow(_ev_idle(), _inv_off())
    ev_calls = [
        c for c in hass.services.async_call.call_args_list
        if c[0][1] == "set_value" and c[0][2].get("entity_id") == "number.ev_charger_power"
    ]
    assert len(ev_calls) == 1
    assert ev_calls[0][0][2]["value"] == 0.0


# ── write_slow: suppress_writes ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_suppress_writes_no_calls(controller_with_target) -> None:
    ctrl, hass = controller_with_target
    await ctrl.write_slow(_ev_charging(), _inv_power_control(), suppress_writes=True)
    hass.services.async_call.assert_not_called()


# ── write_fast: never writes verb ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_write_fast_never_writes_verb(controller_with_target) -> None:
    ctrl, hass = controller_with_target
    await ctrl.write_fast(_ev_charging(), _inv_power_control())
    calls = hass.services.async_call.call_args_list
    select_calls = [c for c in calls if c[0][1] == "select_option"]
    assert len(select_calls) == 0


@pytest.mark.asyncio
async def test_write_fast_writes_active_power_when_not_none(controller_with_target) -> None:
    ctrl, hass = controller_with_target
    await ctrl.write_fast(_ev_charging(), _inv_power_control(active_power_w=300.0))
    cap_calls = [
        c for c in hass.services.async_call.call_args_list
        if c[0][1] == "set_value" and c[0][2].get("entity_id") == "number.battery_cap"
    ]
    assert len(cap_calls) == 1
    assert cap_calls[0][0][2]["value"] == 300.0


@pytest.mark.asyncio
async def test_write_fast_skips_active_power_when_none(controller_with_target) -> None:
    ctrl, hass = controller_with_target
    await ctrl.write_fast(_ev_idle(), _inv_disabled())
    cap_calls = [
        c for c in hass.services.async_call.call_args_list
        if c[0][1] == "set_value" and c[0][2].get("entity_id") == "number.battery_cap"
    ]
    assert len(cap_calls) == 0


@pytest.mark.asyncio
async def test_write_fast_writes_ev_target(controller_with_target) -> None:
    ctrl, hass = controller_with_target
    await ctrl.write_fast(_ev_charging(ev_charge_power_w=2500.0), _inv_power_control())
    ev_calls = [
        c for c in hass.services.async_call.call_args_list
        if c[0][1] == "set_value" and c[0][2].get("entity_id") == "number.ev_charger_power"
    ]
    assert len(ev_calls) == 1
    assert ev_calls[0][0][2]["value"] == 2500.0


@pytest.mark.asyncio
async def test_write_fast_active_power_idempotent(controller_with_target) -> None:
    ctrl, hass = controller_with_target
    await ctrl.write_fast(_ev_charging(), _inv_power_control(active_power_w=100.0))
    await ctrl.write_fast(_ev_charging(), _inv_power_control(active_power_w=100.0))
    cap_calls = [
        c for c in hass.services.async_call.call_args_list
        if c[0][1] == "set_value" and c[0][2].get("entity_id") == "number.battery_cap"
    ]
    assert len(cap_calls) == 1


# ── domain splitting ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_input_number_domain_used_for_ev_target() -> None:
    hass = _make_hass()
    ctrl = Controller(hass, _MAPPING_INPUT_NUMBER)
    await ctrl.write_fast(_ev_charging(ev_charge_power_w=2000.0), _inv_power_control())
    ev_calls = [
        c for c in hass.services.async_call.call_args_list
        if c[0][2].get("entity_id") == "input_number.ev_charger_current"
    ]
    assert len(ev_calls) == 1
    assert ev_calls[0][0][0] == "input_number"
    assert ev_calls[0][0][1] == "set_value"


@pytest.mark.asyncio
async def test_no_inverter_configured_no_verb_write() -> None:
    hass = _make_hass()
    ctrl = Controller(hass, _MAPPING_NO_INVERTER)
    await ctrl.write_slow(_ev_charging(), _inv_power_control())
    calls = hass.services.async_call.call_args_list
    select_calls = [c for c in calls if c[0][1] == "select_option"]
    assert len(select_calls) == 0
