from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.helpers.restore_state import RestoreStateData

from .const import (
    CONF_EV_TARGET_SOC,
    CONF_INVERTER_OPTIONS,
    CONF_INVERTER_POWER_TARGET,
    CONF_INVERTER_SELECT,
    CONF_MAX_GRID_IMPORT_W,
    DEFAULT_EV_TARGET_SOC,
    DEFAULT_MAX_GRID_IMPORT_W,
    DOMAIN,
)
from .controller import Controller
from .coordinator import (
    EmsFastLoopCoordinator,
    EmsState,
    EmsStrategyCoordinator,
    EvSchedulerOrchestrator,
)
from .models import VerbMapping

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [
    Platform.SELECT,
    Platform.SWITCH,
    Platform.NUMBER,
    Platform.SENSOR,
    Platform.BINARY_SENSOR,
    Platform.TIME,
]


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    if entry.version >= 3:
        return True

    new_data = {**entry.data}

    if entry.version < 2:
        # v1 → v2: read max_grid_import_w / ev_target_soc from old number entities
        restore = await RestoreStateData.async_get_instance(hass)
        for key, entity_id, default in (
            (CONF_MAX_GRID_IMPORT_W, "number.ems_max_grid_import_w", DEFAULT_MAX_GRID_IMPORT_W),
            (CONF_EV_TARGET_SOC, "number.ems_ev_target_soc", DEFAULT_EV_TARGET_SOC),
        ):
            if key in new_data:
                continue
            last = restore.last_states.get(entity_id)
            try:
                new_data[key] = float(last.state.state) if last else default
            except (ValueError, AttributeError):
                new_data[key] = default

    # v2 → v3: max_grid_import_w / ev_capacity_kwh / ev_max_charge_kw are now entity_ids
    # Drop any scalar values — user must re-attach via Configure.
    _SCALAR_KEYS = (CONF_MAX_GRID_IMPORT_W, "ev_capacity_kwh", "ev_max_charge_kw")
    dropped = []
    for key in _SCALAR_KEYS:
        val = new_data.get(key)
        if isinstance(val, (int, float)):
            del new_data[key]
            dropped.append(key)
    if dropped:
        _LOGGER.warning(
            "EMS v3 migration: removed scalar value(s) %s — go to Settings → "
            "Devices & Services → EMS → Configure to re-attach number entities.",
            dropped,
        )

    hass.config_entries.async_update_entry(entry, data=new_data, version=3)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    hass.data.setdefault(DOMAIN, {})

    data = entry.data
    inverter_options_raw: dict = data.get(CONF_INVERTER_OPTIONS, {})
    self_use_option = inverter_options_raw.get("SELF_USE") or None

    mapping = VerbMapping(
        inverter_select_entity=data.get(CONF_INVERTER_SELECT),
        self_use_option=self_use_option,
        inverter_power_target_entity=data.get(CONF_INVERTER_POWER_TARGET),
    )

    state = EmsState()
    controller = Controller(hass, mapping)

    strategy_coordinator = EmsStrategyCoordinator(hass, entry, state, controller)
    fast_loop_coordinator = EmsFastLoopCoordinator(hass, entry, state, controller)
    ev_orchestrator = EvSchedulerOrchestrator(hass, entry, state)

    await strategy_coordinator.async_setup()

    await strategy_coordinator.async_config_entry_first_refresh()
    await fast_loop_coordinator.async_config_entry_first_refresh()
    await ev_orchestrator.async_start()

    hass.data[DOMAIN][entry.entry_id] = {
        "state": state,
        "controller": controller,
        "strategy_coordinator": strategy_coordinator,
        "fast_loop_coordinator": fast_loop_coordinator,
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

        return {
            "strategy": decision.strategy,
            "inverter_verb": decision.inverter_verb.value if decision.inverter_verb else None,
            "inverter_option": inputs.mapping.self_use_option if decision.inverter_verb else None,
            "inverter_power_target_w": decision.inverter_power_target_w,
            "ev_charge_power_w": decision.ev_charge_power_w,
            "computed_at": inputs.now.isoformat(),
        }

    hass.services.async_register(DOMAIN, "recompute_ev_window", handle_recompute_ev_window)
    hass.services.async_register(
        DOMAIN, "simulate", handle_simulate,
        supports_response=SupportsResponse.ONLY,
    )


def _unregister_services(hass: HomeAssistant) -> None:
    if not hass.data.get(DOMAIN):
        for service in ("recompute_ev_window", "simulate"):
            hass.services.async_remove(DOMAIN, service)
