from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse

from .const import (
    CONF_ACTIVE_POWER_ENTITY,
    CONF_EV_CHARGE_TARGET_ENTITY,
    CONF_INVERTER_DISABLED_OPTION,
    CONF_INVERTER_POWER_CONTROL_OPTION,
    CONF_INVERTER_SELECT,
    DOMAIN,
)
from .controller import Controller
from .coordinator import (
    EmsFastLoopCoordinator,
    EmsState,
    EmsStrategyCoordinator,
)
from .models import VerbMapping

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [
    Platform.SELECT,
    Platform.NUMBER,
    Platform.SENSOR,
]


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    if entry.version >= 9:
        return True

    new_data = {**entry.data}

    # v1–v4 → v5: drop all old keys that no longer exist
    _DROPPED_KEYS = (
        "grid_import_15m_avg",
        "ev_capacity_kwh",
        "ev_max_charge_kw",
        "inverter_power_target",
        "import_price",
        "forecast_path_import",
        "ev_ready_by",
    )
    dropped = [k for k in _DROPPED_KEYS if k in new_data]
    for key in dropped:
        del new_data[key]
    if dropped:
        _LOGGER.warning(
            "EMS v5 migration: removed obsolete key(s) %s — go to Settings → "
            "Devices & Services → EMS → Configure to re-attach entities.",
            dropped,
        )

    # v6 → v7: inverter option strings are now hardcoded; drop stored options dict
    new_data.pop("inverter_options", None)

    # v8 → v9: add price signal entity (optional, defaults to None)
    new_data.setdefault("price_signal_entity", None)

    hass.config_entries.async_update_entry(entry, data=new_data, version=9)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    hass.data.setdefault(DOMAIN, {})

    data = entry.data

    mapping = VerbMapping(
        inverter_select_entity=data.get(CONF_INVERTER_SELECT),
        disabled_option=data.get(CONF_INVERTER_DISABLED_OPTION) or None,
        power_control_option=data.get(CONF_INVERTER_POWER_CONTROL_OPTION) or None,
        active_power_entity=data.get(CONF_ACTIVE_POWER_ENTITY),
        ev_charge_target_entity=data.get(CONF_EV_CHARGE_TARGET_ENTITY),
    )

    state = EmsState()
    controller = Controller(hass, mapping)

    strategy_coordinator = EmsStrategyCoordinator(hass, entry, state, controller)
    fast_loop_coordinator = EmsFastLoopCoordinator(hass, entry, state, controller)

    await strategy_coordinator.async_setup()

    await strategy_coordinator.async_config_entry_first_refresh()
    await fast_loop_coordinator.async_config_entry_first_refresh()

    hass.data[DOMAIN][entry.entry_id] = {
        "state": state,
        "controller": controller,
        "strategy_coordinator": strategy_coordinator,
        "fast_loop_coordinator": fast_loop_coordinator,
    }

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    _register_services(hass, entry)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id, {})
        _unregister_services(hass)
    return unload_ok


def _register_services(hass: HomeAssistant, entry: ConfigEntry) -> None:
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
            "active_power_w": decision.active_power_w,
            "ev_charge_power_w": decision.ev_charge_power_w,
            "computed_at": inputs.now.isoformat(),
        }

    hass.services.async_register(
        DOMAIN, "simulate", handle_simulate,
        supports_response=SupportsResponse.ONLY,
    )


def _unregister_services(hass: HomeAssistant) -> None:
    if not hass.data.get(DOMAIN):
        hass.services.async_remove(DOMAIN, "simulate")
