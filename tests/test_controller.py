from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.ems.controller import Controller
from custom_components.ems.models import Decision, VerbMapping

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


def _make_decision(**overrides) -> Decision:
    defaults = dict(
        strategy="ev_charging",
        active_power_w=200.0,
        ev_charge_power_w=3500.0,
        charging_state="charging",
    )
    defaults.update(overrides)
    return Decision(**defaults)


def _make_idle() -> Decision:
    return Decision(
        strategy="ev_idle",
        active_power_w=0.0,
        ev_charge_power_w=0.0,
        charging_state="not_enough_power",
    )


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


@pytest.mark.asyncio
async def test_idle_decision_no_inverter_write_and_zero_ev_target(controller_with_target) -> None:
    """Idle (ev_power=0) does not touch the inverter; writes EV target=0."""
    ctrl, hass = controller_with_target
    await ctrl.write_full(_make_idle(), ev_power_w=0.0)
    select_calls = [
        c for c in hass.services.async_call.call_args_list if c[0][1] == "select_option"
    ]
    number_calls = [c for c in hass.services.async_call.call_args_list if c[0][1] == "set_value"]
    assert len(select_calls) == 0
    assert len(number_calls) == 1
    assert number_calls[0][0][2]["value"] == 0.0


@pytest.mark.asyncio
async def test_idle_no_inverter_no_select_call(controller) -> None:
    """Idle with no target entity: no service calls at all."""
    ctrl, hass = controller
    await ctrl.write_full(_make_idle(), ev_power_w=0.0)
    hass.services.async_call.assert_not_called()


@pytest.mark.asyncio
async def test_active_writes_power_control_and_cap(controller_with_target) -> None:
    """Active decision with ev_power_w>0 selects 'power_control', writes cap and EV target."""
    ctrl, hass = controller_with_target
    await ctrl.write_full(
        _make_decision(active_power_w=150.0, ev_charge_power_w=3000.0), ev_power_w=3000.0
    )
    select_calls = [
        c for c in hass.services.async_call.call_args_list if c[0][1] == "select_option"
    ]
    number_calls = [c for c in hass.services.async_call.call_args_list if c[0][1] == "set_value"]
    assert len(select_calls) == 1
    assert select_calls[0][0][2]["option"] == "power_control"
    # number_calls: cap + EV target
    assert len(number_calls) == 2
    cap_call = next(c for c in number_calls if c[0][2]["entity_id"] == "number.battery_cap")
    ev_call = next(c for c in number_calls if c[0][2]["entity_id"] == "number.ev_charger_power")
    assert cap_call[0][2]["value"] == 150.0
    assert ev_call[0][2]["value"] == 3000.0


@pytest.mark.asyncio
async def test_no_inverter_configured_no_writes() -> None:
    """When mapping has no inverter entities, no service calls are made."""
    hass = _make_hass()
    ctrl = Controller(hass, _MAPPING_NO_INVERTER)
    await ctrl.write_full(_make_decision(), ev_power_w=3500.0)
    hass.services.async_call.assert_not_called()


@pytest.mark.asyncio
async def test_idempotent_select_write(controller_with_target) -> None:
    """Two consecutive identical active ticks write the select once."""
    ctrl, hass = controller_with_target
    decision = _make_decision()
    await ctrl.write_full(decision, ev_power_w=3500.0)
    await ctrl.write_full(decision, ev_power_w=3500.0)
    select_calls = [
        c for c in hass.services.async_call.call_args_list if c[0][1] == "select_option"
    ]
    assert len(select_calls) == 1


@pytest.mark.asyncio
async def test_idempotent_cap_write(controller_with_target) -> None:
    """Same battery discharge cap written twice calls set_value once for cap entity."""
    ctrl, hass = controller_with_target
    decision = _make_decision(active_power_w=200.0)
    await ctrl.write_full(decision, ev_power_w=3500.0)
    await ctrl.write_full(decision, ev_power_w=3500.0)
    cap_calls = [
        c for c in hass.services.async_call.call_args_list
        if c[0][1] == "set_value" and c[0][2].get("entity_id") == "number.battery_cap"
    ]
    assert len(cap_calls) == 1


@pytest.mark.asyncio
async def test_input_number_domain_used_for_ev_target() -> None:
    """When ev_charge_target_entity is input_number.*, service domain is input_number."""
    hass = _make_hass()
    ctrl = Controller(hass, _MAPPING_INPUT_NUMBER)
    await ctrl.write_full(
        _make_decision(active_power_w=100.0, ev_charge_power_w=2000.0), ev_power_w=2000.0
    )
    ev_calls = [
        c for c in hass.services.async_call.call_args_list
        if c[0][2].get("entity_id") == "input_number.ev_charger_current"
    ]
    assert len(ev_calls) == 1
    assert ev_calls[0][0][0] == "input_number"
    assert ev_calls[0][0][1] == "set_value"


@pytest.mark.asyncio
async def test_idle_idempotent_no_repeat_writes(controller_with_target) -> None:
    """Two consecutive idle ticks write EV target only once (idempotent on idle)."""
    ctrl, hass = controller_with_target
    await ctrl.write_full(_make_idle(), ev_power_w=0.0)
    ev_calls_first = [
        c for c in hass.services.async_call.call_args_list
        if c[0][1] == "set_value" and c[0][2].get("entity_id") == "number.ev_charger_power"
    ]
    assert len(ev_calls_first) == 1
    assert ev_calls_first[0][0][2]["value"] == 0.0

    hass.services.async_call.reset_mock()
    await ctrl.write_full(_make_idle(), ev_power_w=0.0)
    ev_calls_second = [
        c for c in hass.services.async_call.call_args_list
        if c[0][1] == "set_value" and c[0][2].get("entity_id") == "number.ev_charger_power"
    ]
    assert len(ev_calls_second) == 0


@pytest.mark.asyncio
async def test_idle_then_active_writes_ev_target(controller_with_target) -> None:
    """After an idle write (0 W cached), the next active tick writes the EV target."""
    ctrl, hass = controller_with_target
    await ctrl.write_full(_make_decision(ev_charge_power_w=3500.0), ev_power_w=3500.0)
    hass.services.async_call.reset_mock()

    await ctrl.write_full(_make_idle(), ev_power_w=0.0)
    hass.services.async_call.reset_mock()

    await ctrl.write_full(_make_decision(ev_charge_power_w=3500.0), ev_power_w=3500.0)
    ev_calls = [
        c for c in hass.services.async_call.call_args_list
        if c[0][1] == "set_value" and c[0][2].get("entity_id") == "number.ev_charger_power"
    ]
    assert len(ev_calls) == 1
    assert ev_calls[0][0][2]["value"] == 3500.0


@pytest.mark.asyncio
async def test_suppress_writes_active(controller_with_target) -> None:
    """Startup grace suppresses all writes even if EV would charge."""
    ctrl, hass = controller_with_target
    await ctrl.write_full(_make_decision(), ev_power_w=3500.0, suppress_writes=True)
    hass.services.async_call.assert_not_called()


@pytest.mark.asyncio
async def test_suppress_writes_idle(controller_with_target) -> None:
    """Startup grace suppresses writes on idle too."""
    ctrl, hass = controller_with_target
    await ctrl.write_full(_make_idle(), ev_power_w=0.0, suppress_writes=True)
    hass.services.async_call.assert_not_called()


@pytest.mark.asyncio
async def test_idle_after_active_leaves_inverter_untouched(controller_with_target) -> None:
    """After an active tick, going idle does NOT write to the inverter."""
    ctrl, hass = controller_with_target
    await ctrl.write_full(_make_decision(), ev_power_w=3500.0)
    hass.services.async_call.reset_mock()

    await ctrl.write_full(_make_idle(), ev_power_w=0.0)
    select_calls = [
        c for c in hass.services.async_call.call_args_list if c[0][1] == "select_option"
    ]
    assert len(select_calls) == 0


# ── inverter confirmation via ev_power_w ──────────────────────────────────────

@pytest.mark.asyncio
async def test_strategy_charge_with_zero_ev_power_no_inverter_write(controller_with_target) -> None:
    """Strategy says charge but ev_power_w=0 → inverter untouched, EV target written."""
    ctrl, hass = controller_with_target
    await ctrl.write_full(_make_decision(ev_charge_power_w=3000.0), ev_power_w=0.0)
    select_calls = [
        c for c in hass.services.async_call.call_args_list if c[0][1] == "select_option"
    ]
    assert len(select_calls) == 0
    ev_calls = [
        c for c in hass.services.async_call.call_args_list
        if c[0][1] == "set_value" and c[0][2].get("entity_id") == "number.ev_charger_power"
    ]
    assert len(ev_calls) == 1
    assert ev_calls[0][0][2]["value"] == 3000.0


@pytest.mark.asyncio
async def test_ev_power_above_zero_triggers_power_control(controller_with_target) -> None:
    """When ev_power_w > 0, inverter switches to power_control."""
    ctrl, hass = controller_with_target
    await ctrl.write_full(_make_decision(ev_charge_power_w=3000.0), ev_power_w=3000.0)
    select_calls = [
        c for c in hass.services.async_call.call_args_list if c[0][1] == "select_option"
    ]
    assert len(select_calls) == 1
    assert select_calls[0][0][2]["option"] == "power_control"


@pytest.mark.asyncio
async def test_inverter_stays_power_control_after_ev_stops(controller_with_target) -> None:
    """After confirmed charging, ev_power_w dropping to 0 leaves the inverter untouched."""
    ctrl, hass = controller_with_target
    await ctrl.write_full(_make_decision(ev_charge_power_w=3000.0), ev_power_w=3000.0)
    hass.services.async_call.reset_mock()

    await ctrl.write_full(_make_decision(ev_charge_power_w=3000.0), ev_power_w=0.0)
    select_calls = [
        c for c in hass.services.async_call.call_args_list if c[0][1] == "select_option"
    ]
    assert len(select_calls) == 0
