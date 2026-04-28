from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.ems.controller import Controller
from custom_components.ems.models import Decision, InverterVerb, VerbMapping

_MAPPING = VerbMapping(
    inverter_select_entity="select.inverter",
    self_use_option="Self Use",
    inverter_power_target_entity="number.inverter_power",
)

_MAPPING_NO_INVERTER = VerbMapping(
    inverter_select_entity=None,
    self_use_option=None,
    inverter_power_target_entity=None,
)


def _make_decision(**overrides) -> Decision:
    defaults = dict(
        strategy="ev_charging_solar",
        inverter_verb=InverterVerb.SELF_USE,
        inverter_power_target_w=None,
        ev_charge_power_w=3500.0,
    )
    defaults.update(overrides)
    return Decision(**defaults)


def _make_idle() -> Decision:
    return Decision(
        strategy="ev_idle",
        inverter_verb=None,
        inverter_power_target_w=None,
        ev_charge_power_w=0.0,
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


@pytest.mark.asyncio
async def test_idle_decision_skips_all_writes(controller) -> None:
    """write_full with ev_charge_power_w=0 must not write anything."""
    ctrl, hass = controller
    await ctrl.write_full(_make_idle())
    hass.services.async_call.assert_not_called()


@pytest.mark.asyncio
async def test_idle_decision_resets_cache(controller) -> None:
    """After an idle decision the cache is cleared so the next active tick fires."""
    ctrl, hass = controller
    # First active write sets cache
    await ctrl.write_full(_make_decision(inverter_verb=InverterVerb.SELF_USE))
    hass.services.async_call.reset_mock()

    # Idle clears cache
    await ctrl.write_full(_make_idle())
    # Next active tick must re-fire the select call
    await ctrl.write_full(_make_decision(inverter_verb=InverterVerb.SELF_USE))
    select_calls = [
        c for c in hass.services.async_call.call_args_list
        if c[0][0] == "select"
    ]
    assert len(select_calls) == 1


@pytest.mark.asyncio
async def test_idempotent_select_write(controller) -> None:
    """Two consecutive identical active ticks write the select once."""
    ctrl, hass = controller
    decision = _make_decision(inverter_verb=InverterVerb.SELF_USE)

    await ctrl.write_full(decision)
    await ctrl.write_full(decision)

    select_calls = [
        c for c in hass.services.async_call.call_args_list
        if c[0][0] == "select"
    ]
    assert len(select_calls) == 1


@pytest.mark.asyncio
async def test_power_target_written_when_provided(controller) -> None:
    """A decision with power_target writes the number entity."""
    ctrl, hass = controller
    decision = _make_decision(inverter_power_target_w=1500.0)

    await ctrl.write_full(decision)
    number_calls = [
        c for c in hass.services.async_call.call_args_list
        if c[0][0] == "number"
    ]
    assert len(number_calls) == 1
    # args: (domain, service, service_data); service_data is positional arg 2
    assert number_calls[0][0][2]["value"] == 1500.0


@pytest.mark.asyncio
async def test_power_only_skips_when_idle() -> None:
    """write_power_only with ev_charge_power_w=0 resets cache and does not write."""
    hass = _make_hass()
    ctrl = Controller(hass, _MAPPING)
    await ctrl.write_power_only(_make_idle())
    hass.services.async_call.assert_not_called()


@pytest.mark.asyncio
async def test_no_inverter_configured_no_writes() -> None:
    """When mapping has no inverter entities, no service calls are made."""
    hass = _make_hass()
    ctrl = Controller(hass, _MAPPING_NO_INVERTER)
    await ctrl.write_full(_make_decision())
    hass.services.async_call.assert_not_called()
