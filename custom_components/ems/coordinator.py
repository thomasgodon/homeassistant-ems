from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from . import strategy as strategy_module
from .const import (
    CONF_BATTERY_DISCHARGE_CAP_ENTITY,
    CONF_EV_CHARGE_TARGET_ENTITY,
    CONF_EV_PLUG,
    CONF_EV_POWER,
    CONF_EV_SOC,
    CONF_EV_TARGET_SOC,
    CONF_GRID_IMPORT_POWER,
    CONF_HOUSE_LOAD,
    CONF_INVERTER_OPTIONS,
    CONF_INVERTER_SELECT,
    CONF_MAX_GRID_IMPORT_W,
    CONF_PV_POWER,
    DEFAULT_EV_TARGET_SOC,
    DEFAULT_MAX_GRID_IMPORT_W,
    DOMAIN,
)
from .controller import Controller
from .models import (
    Inputs,
    LadderResult,
    Thresholds,
    VerbMapping,
)

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry

_LOGGER = logging.getLogger(__name__)


@dataclass
class EmsState:
    last_result: LadderResult | None = None
    startup_grace_until: datetime | None = None


class EmsStrategyCoordinator(DataUpdateCoordinator[LadderResult]):
    """10 s default. Builds Inputs → strategy.decide → controller.write_full."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        state: EmsState,
        controller: Controller,
    ) -> None:
        self._entry = entry
        self._state = state
        self._controller = controller
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_strategy",
            update_interval=timedelta(seconds=10),
        )

    async def async_setup(self) -> None:
        """Set startup grace window."""
        self._state.startup_grace_until = dt_util.utcnow() + timedelta(seconds=60)
        self._controller.reset_write_cache()

    async def _async_update_data(self) -> LadderResult:
        try:
            inputs = await self._build_inputs()
        except Exception as exc:
            raise UpdateFailed(f"EMS input build failed: {exc}") from exc

        decision = strategy_module.decide(inputs)

        in_grace = (
            self._state.startup_grace_until is not None
            and inputs.now < self._state.startup_grace_until
        )
        await self._controller.write_full(decision, suppress_writes=in_grace)

        result = LadderResult(decision=decision, inputs=inputs, computed_at=inputs.now)
        self._state.last_result = result
        return result

    async def _build_inputs(self) -> Inputs:
        hass = self.hass
        data = self._entry.data

        now = dt_util.utcnow()

        ev_strategy = _read_select_state(hass, "select.ems_ev_charging_mode", "Off")

        grid_import_w = _read_float(hass, data.get(CONF_GRID_IMPORT_POWER), 0.0)
        pv_w = _read_float(hass, data.get(CONF_PV_POWER), 0.0)
        house_load_w = _read_float(hass, data.get(CONF_HOUSE_LOAD), 0.0)

        ev_plugged = _read_bool(hass, data.get(CONF_EV_PLUG), False)
        ev_power_w = _read_float(hass, data.get(CONF_EV_POWER), 0.0)
        ev_soc = _read_float_optional(hass, data.get(CONF_EV_SOC))

        thresholds = Thresholds(
            max_grid_import_w=_read_float(
                hass, data.get(CONF_MAX_GRID_IMPORT_W), DEFAULT_MAX_GRID_IMPORT_W
            ),
            ev_target_soc=_read_float(
                hass, data.get(CONF_EV_TARGET_SOC), DEFAULT_EV_TARGET_SOC
            ),
            ev_min_charge_w=_read_number(hass, "number.ems_ev_min_charge_w", 1400.0),
        )

        inverter_options_raw: dict = data.get(CONF_INVERTER_OPTIONS, {})

        mapping = VerbMapping(
            inverter_select_entity=data.get(CONF_INVERTER_SELECT),
            self_use_option=inverter_options_raw.get("SELF_USE") or None,
            battery_control_option=inverter_options_raw.get("BATTERY_CONTROL") or None,
            battery_discharge_cap_entity=data.get(CONF_BATTERY_DISCHARGE_CAP_ENTITY),
            ev_charge_target_entity=data.get(CONF_EV_CHARGE_TARGET_ENTITY),
        )

        return Inputs(
            now=now,
            grid_import_w=grid_import_w,
            pv_w=pv_w,
            house_load_w=house_load_w,
            ev_plugged=ev_plugged,
            ev_power_w=ev_power_w,
            ev_soc=ev_soc,
            ev_strategy=ev_strategy,  # type: ignore[arg-type]
            thresholds=thresholds,
            mapping=mapping,
        )


class EmsFastLoopCoordinator(DataUpdateCoordinator[None]):
    """1 s default. Re-runs strategy with fresh power readings every second."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        state: EmsState,
        controller: Controller,
    ) -> None:
        self._entry = entry
        self._state = state
        self._controller = controller
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_fast_loop",
            update_interval=timedelta(seconds=1),
        )

    async def _async_update_data(self) -> None:
        if self._state.last_result is None:
            return
        grace_until = self._state.startup_grace_until
        if grace_until is not None and dt_util.utcnow() < grace_until:
            return

        data = self._entry.data
        grid_import_w = _read_float(self.hass, data.get(CONF_GRID_IMPORT_POWER), 0.0)
        pv_w = _read_float(self.hass, data.get(CONF_PV_POWER), 0.0)
        house_load_w = _read_float(self.hass, data.get(CONF_HOUSE_LOAD), 0.0)
        ev_power_w = _read_float(self.hass, data.get(CONF_EV_POWER), 0.0)

        prior_inputs = self._state.last_result.inputs

        fast_decision = strategy_module.recompute(
            grid_import_w=grid_import_w,
            pv_w=pv_w,
            house_load_w=house_load_w,
            ev_power_w=ev_power_w,
            ev_plugged=prior_inputs.ev_plugged,
            ev_soc=prior_inputs.ev_soc,
            ev_strategy=prior_inputs.ev_strategy,
            thresholds=prior_inputs.thresholds,
        )
        await self._controller.write_full(fast_decision)


# ── state helpers ─────────────────────────────────────────────────────────────

def _read_float(hass: HomeAssistant, entity_id: str | None, default: float) -> float:
    if not entity_id:
        return default
    state = hass.states.get(entity_id)
    if state is None or state.state in ("unavailable", "unknown"):
        return default
    try:
        return float(state.state)
    except (ValueError, TypeError):
        return default


def _read_float_optional(hass: HomeAssistant, entity_id: str | None) -> float | None:
    if not entity_id:
        return None
    state = hass.states.get(entity_id)
    if state is None or state.state in ("unavailable", "unknown"):
        return None
    try:
        return float(state.state)
    except (ValueError, TypeError):
        return None


def _read_bool(hass: HomeAssistant, entity_id: str | None, default: bool) -> bool:
    if not entity_id:
        return default
    state = hass.states.get(entity_id)
    if state is None:
        return default
    return state.state in ("on", "true", "True", "1", "home", "open")


def _read_select_state(hass: HomeAssistant, entity_id: str, default: str) -> str:
    state = hass.states.get(entity_id)
    if state is None or state.state in ("unavailable", "unknown"):
        return default
    return state.state


def _read_number(hass: HomeAssistant, entity_id: str, default: float) -> float:
    return _read_float(hass, entity_id, default)
