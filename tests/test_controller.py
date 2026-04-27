from __future__ import annotations

import datetime
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.ems.controller import Controller
from custom_components.ems.models import Decision, EvVerb, InverterVerb, VerbMapping

_UTC = datetime.timezone.utc
_NOW = datetime.datetime(2026, 4, 27, 12, 0, 0, tzinfo=_UTC)

_MAPPING = VerbMapping(
    inverter_select_entity="select.inverter",
    inverter_options={
        InverterVerb.SELF_USE: "Self Use",
        InverterVerb.FORCE_CHARGE: "Force Charge",
        InverterVerb.FORCE_DISCHARGE: "Force Discharge",
        InverterVerb.LIMIT_EXPORT: "Limit Export",
        InverterVerb.IDLE: "Idle",
    },
    inverter_power_target_entity="number.inverter_power",
    ev_select_entity="select.ev",
    ev_options={
        EvVerb.CHARGE_NOW: "Charge",
        EvVerb.SOLAR_ONLY: "Solar Only",
        EvVerb.SOLAR_PLUS_BATTERY: "Solar + Battery",
        EvVerb.IDLE: "Idle",
    },
    ev_max_power_entity="number.ev_max_power",
)


def _make_decision(**overrides) -> Decision:
    defaults = dict(
        strategy="fallback",
        inverter_verb=InverterVerb.SELF_USE,
        inverter_power_target_w=None,
        ev_verb=EvVerb.IDLE,
        ev_max_power_w=None,
        new_peak_risk=False,
        rule_chain=(),
        unsupported_verbs=(),
    )
    defaults.update(overrides)
    return Decision(**defaults)


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
async def test_idempotent_select_write(controller) -> None:
    ctrl, hass = controller
    decision = _make_decision(inverter_verb=InverterVerb.SELF_USE)

    await ctrl.write_full(decision)
    await ctrl.write_full(decision)

    calls = [
        c for c in hass.services.async_call.call_args_list
        if c[0][0] == "select"
    ]
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_power_target_only_when_required(controller) -> None:
    ctrl, hass = controller

    # SELF_USE: no power target call
    await ctrl.write_full(_make_decision(inverter_verb=InverterVerb.SELF_USE))
    number_calls = [
        c for c in hass.services.async_call.call_args_list
        if c[0][0] == "number"
    ]
    assert len(number_calls) == 0

    hass.services.async_call.reset_mock()
    ctrl.reset_write_cache()

    # FORCE_DISCHARGE with power target
    await ctrl.write_full(_make_decision(
        inverter_verb=InverterVerb.FORCE_DISCHARGE,
        inverter_power_target_w=2000.0,
    ))
    number_calls = [
        c for c in hass.services.async_call.call_args_list
        if c[0][0] == "number"
    ]
    assert len(number_calls) == 1


@pytest.mark.asyncio
async def test_peak_shave_fast_loop_writes_only_power_target(controller) -> None:
    ctrl, hass = controller

    # 10 s loop sets FORCE_DISCHARGE
    await ctrl.write_full(_make_decision(
        strategy="peak_shave",
        inverter_verb=InverterVerb.FORCE_DISCHARGE,
        inverter_power_target_w=2000.0,
    ))
    hass.services.async_call.reset_mock()

    # 1 s fast loop with updated power target
    fast_decision = _make_decision(
        strategy="peak_shave",
        inverter_verb=InverterVerb.FORCE_DISCHARGE,
        inverter_power_target_w=2500.0,
    )
    await ctrl.write_power_only(fast_decision)

    calls = hass.services.async_call.call_args_list
    select_calls = [c for c in calls if c[0][0] == "select"]
    number_calls = [c for c in calls if c[0][0] == "number"]

    assert len(select_calls) == 0
    assert len(number_calls) == 1


@pytest.mark.asyncio
async def test_off_mode_no_op(controller) -> None:
    ctrl, hass = controller
    decision = _make_decision(strategy="off", inverter_verb=None, ev_verb=None)
    await ctrl.write_full(decision)
    hass.services.async_call.assert_not_called()


def test_mode_unsupported_logs_once(controller, caplog) -> None:
    ctrl, _ = controller
    with caplog.at_level(logging.WARNING, logger="custom_components.ems.controller"):
        ctrl.log_unsupported("mode_unsupported_FORCE_DISCHARGE", _NOW)
        ctrl.log_unsupported("mode_unsupported_FORCE_DISCHARGE", _NOW)
        ctrl.log_unsupported("mode_unsupported_FORCE_DISCHARGE", _NOW)

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
