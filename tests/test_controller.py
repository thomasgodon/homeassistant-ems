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
        ev_charge_intent=True,
    )
    defaults.update(overrides)
    return Decision(**defaults)


def _make_idle() -> Decision:
    return Decision(
        strategy="ev_idle",
        active_power_w=0.0,
        ev_charge_power_w=0.0,
        ev_charge_intent=False,
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
async def test_idle_decision_writes_disabled_and_zero(controller_with_target) -> None:
    """Idle writes 'disabled' to inverter and 0 to EV target."""
    ctrl, hass = controller_with_target
    await ctrl.write_full(_make_idle())
    select_calls = [
        c for c in hass.services.async_call.call_args_list if c[0][1] == "select_option"
    ]
    number_calls = [c for c in hass.services.async_call.call_args_list if c[0][1] == "set_value"]
    assert len(select_calls) == 1
    assert select_calls[0][0][2]["option"] == "disabled"
    assert len(number_calls) == 1
    assert number_calls[0][0][2]["value"] == 0.0


@pytest.mark.asyncio
async def test_idle_no_inverter_no_select_call(controller) -> None:
    """Idle with no target entity: only select_option for disabled, no set_value."""
    ctrl, hass = controller
    await ctrl.write_full(_make_idle())
    number_calls = [c for c in hass.services.async_call.call_args_list if c[0][1] == "set_value"]
    assert len(number_calls) == 0


@pytest.mark.asyncio
async def test_active_writes_power_control_and_cap(controller_with_target) -> None:
    """Active decision selects 'power_control', writes cap and EV target."""
    ctrl, hass = controller_with_target
    await ctrl.write_full(_make_decision(active_power_w=150.0, ev_charge_power_w=3000.0))
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
    await ctrl.write_full(_make_decision())
    hass.services.async_call.assert_not_called()


@pytest.mark.asyncio
async def test_idempotent_select_write(controller_with_target) -> None:
    """Two consecutive identical active ticks write the select once."""
    ctrl, hass = controller_with_target
    decision = _make_decision()
    await ctrl.write_full(decision)
    await ctrl.write_full(decision)
    select_calls = [
        c for c in hass.services.async_call.call_args_list if c[0][1] == "select_option"
    ]
    assert len(select_calls) == 1


@pytest.mark.asyncio
async def test_idempotent_cap_write(controller_with_target) -> None:
    """Same battery discharge cap written twice calls set_value once for cap entity."""
    ctrl, hass = controller_with_target
    decision = _make_decision(active_power_w=200.0)
    await ctrl.write_full(decision)
    await ctrl.write_full(decision)
    cap_calls = [
        c for c in hass.services.async_call.call_args_list
        if c[0][1] == "set_value" and c[0][2].get("entity_id") == "number.battery_cap"
    ]
    assert len(cap_calls) == 1


@pytest.mark.asyncio
async def test_idle_resets_cache_so_next_active_fires(controller_with_target) -> None:
    """After idle (cache reset), the next active tick re-writes all entities."""
    ctrl, hass = controller_with_target
    await ctrl.write_full(_make_decision(ev_charge_power_w=3500.0))
    hass.services.async_call.reset_mock()

    await ctrl.write_full(_make_idle())
    hass.services.async_call.reset_mock()

    await ctrl.write_full(_make_decision(ev_charge_power_w=3500.0))
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
    await ctrl.write_full(_make_decision(), suppress_writes=True)
    hass.services.async_call.assert_not_called()


@pytest.mark.asyncio
async def test_suppress_writes_idle(controller_with_target) -> None:
    """Startup grace suppresses writes on idle too."""
    ctrl, hass = controller_with_target
    await ctrl.write_full(_make_idle(), suppress_writes=True)
    hass.services.async_call.assert_not_called()


@pytest.mark.asyncio
async def test_disabled_selected_on_idle_after_active(controller_with_target) -> None:
    """After an active tick, going idle selects 'disabled' on the inverter."""
    ctrl, hass = controller_with_target
    await ctrl.write_full(_make_decision())
    hass.services.async_call.reset_mock()

    await ctrl.write_full(_make_idle())
    select_calls = [
        c for c in hass.services.async_call.call_args_list if c[0][1] == "select_option"
    ]
    assert len(select_calls) == 1
    assert select_calls[0][0][2]["option"] == "disabled"
