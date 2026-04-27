from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from .const import DOMAIN
from .controller import Controller
from .coordinator import EmsState, EmsStrategyCoordinator, EmsPeakShaveCoordinator, EvSchedulerOrchestrator
from .models import EvVerb, InverterVerb, VerbMapping

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [
    Platform.SELECT,
    Platform.NUMBER,
    Platform.SENSOR,
    Platform.BINARY_SENSOR,
    Platform.TIME,
]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    hass.data.setdefault(DOMAIN, {})

    data = entry.data
    inverter_options_raw: dict = data.get("inverter_options", {})
    inverter_options = {
        InverterVerb[k]: (v if v else None)
        for k, v in inverter_options_raw.items()
        if k in InverterVerb.__members__
    }
    for verb in InverterVerb:
        inverter_options.setdefault(verb, None)

    ev_options_raw: dict = data.get("ev_options", {})
    ev_options = {
        EvVerb[k]: (v if v else None)
        for k, v in ev_options_raw.items()
        if k in EvVerb.__members__
    }
    for verb in EvVerb:
        ev_options.setdefault(verb, None)

    mapping = VerbMapping(
        inverter_select_entity=data.get("inverter_select"),
        inverter_options=inverter_options,
        inverter_power_target_entity=data.get("inverter_power_target"),
        ev_select_entity=data.get("ev_select"),
        ev_options=ev_options,
        ev_max_power_entity=data.get("ev_max_power_entity"),
    )

    state = EmsState()
    controller = Controller(hass, mapping)

    strategy_coordinator = EmsStrategyCoordinator(hass, entry, state, controller)
    peak_shave_coordinator = EmsPeakShaveCoordinator(hass, entry, state, controller)
    ev_orchestrator = EvSchedulerOrchestrator(hass, entry, state)

    await strategy_coordinator.async_setup()

    await strategy_coordinator.async_config_entry_first_refresh()
    await peak_shave_coordinator.async_config_entry_first_refresh()
    await ev_orchestrator.async_start()

    hass.data[DOMAIN][entry.entry_id] = {
        "state": state,
        "controller": controller,
        "strategy_coordinator": strategy_coordinator,
        "peak_shave_coordinator": peak_shave_coordinator,
        "ev_orchestrator": ev_orchestrator,
    }

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    _register_services(hass, entry)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        domain_data = hass.data[DOMAIN].pop(entry.entry_id, {})
        ev_orch = domain_data.get("ev_orchestrator")
        if ev_orch:
            ev_orch.async_stop()
        _unregister_services(hass)
    return unload_ok


def _register_services(hass: HomeAssistant, entry: ConfigEntry) -> None:
    async def handle_set_mode(call: ServiceCall) -> None:
        mode = call.data.get("mode", "Off")
        state_obj = hass.states.get("select.ems_mode")
        if state_obj:
            await hass.services.async_call(
                "select", "select_option",
                {"entity_id": "select.ems_mode", "option": mode},
                blocking=True,
            )

    async def handle_recompute_ev_window(call: ServiceCall) -> None:
        domain_data = hass.data[DOMAIN].get(entry.entry_id, {})
        orch = domain_data.get("ev_orchestrator")
        if orch:
            await orch._recompute()

    async def handle_simulate(call: ServiceCall) -> dict:
        domain_data = hass.data[DOMAIN].get(entry.entry_id, {})
        coordinator = domain_data.get("strategy_coordinator")
        if coordinator is None:
            return {"error": "coordinator not ready"}
        try:
            inputs = await coordinator._build_inputs()
        except Exception as exc:
            return {"error": str(exc)}

        from . import strategy as strategy_module
        decision = strategy_module.decide(inputs)
        state = domain_data.get("state")

        return {
            "strategy": decision.strategy,
            "inverter_verb": decision.inverter_verb.value if decision.inverter_verb else None,
            "inverter_option": (
                inputs.mapping.inverter_options.get(decision.inverter_verb)
                if decision.inverter_verb else None
            ),
            "inverter_power_target_w": decision.inverter_power_target_w,
            "ev_verb": decision.ev_verb.value if decision.ev_verb else None,
            "ev_option": (
                inputs.mapping.ev_options.get(decision.ev_verb)
                if decision.ev_verb else None
            ),
            "ev_max_power_w": decision.ev_max_power_w,
            "rule_chain": list(decision.rule_chain),
            "unsupported_verbs": list(decision.unsupported_verbs),
            "would_write": inputs.ems_mode == "Auto",
            "ems_mode": inputs.ems_mode,
            "peak_risk_latched": state.peak_risk_latched if state else False,
            "computed_at": inputs.now.isoformat(),
        }

    hass.services.async_register(DOMAIN, "set_mode", handle_set_mode)
    hass.services.async_register(DOMAIN, "recompute_ev_window", handle_recompute_ev_window)
    hass.services.async_register(
        DOMAIN, "simulate", handle_simulate,
        supports_response=SupportsResponse.ONLY,
    )


def _unregister_services(hass: HomeAssistant) -> None:
    if not hass.data.get(DOMAIN):
        for service in ("set_mode", "recompute_ev_window", "simulate"):
            hass.services.async_remove(DOMAIN, service)
