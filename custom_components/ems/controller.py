from __future__ import annotations

import logging

from homeassistant.core import HomeAssistant

from .models import Decision, VerbMapping

_LOGGER = logging.getLogger(__name__)

_INVERTER_DISABLED = "disabled"
_INVERTER_POWER_CONTROL = "power_control"


class Controller:
    def __init__(self, hass: HomeAssistant, mapping: VerbMapping) -> None:
        self._hass = hass
        self._mapping = mapping
        self._last_inverter_option: str | None = None
        self._last_ev_charge_w: float | None = None
        self._last_active_power: float | None = None

    def update_mapping(self, mapping: VerbMapping) -> None:
        self._mapping = mapping

    async def write_full(self, decision: Decision, *, suppress_writes: bool = False) -> None:
        """Called by 10 s and 1 s coordinators. Idempotent."""
        if decision.ev_charge_power_w == 0.0:
            self.reset_write_cache()
            if not suppress_writes:
                await self._write_inverter_disabled()
                await self._write_ev_target(0.0)
            return

        if suppress_writes:
            _LOGGER.debug("Startup grace: would write strategy=%s", decision.strategy)
            return

        await self._write_inverter_power_control()
        await self._write_active_power(decision.active_power_w)
        await self._write_ev_target(decision.ev_charge_power_w)

    # ── private helpers ───────────────────────────────────────────────────────

    async def _write_inverter_disabled(self) -> None:
        option = self._mapping.disabled_option or _INVERTER_DISABLED
        await self._select_inverter_option(option)

    async def _write_inverter_power_control(self) -> None:
        option = self._mapping.power_control_option or _INVERTER_POWER_CONTROL
        await self._select_inverter_option(option)

    async def _select_inverter_option(self, option: str) -> None:
        if option == self._last_inverter_option:
            return
        entity = self._mapping.inverter_select_entity
        if not entity:
            return
        _LOGGER.info("Inverter → %s via %s", option, entity)
        await self._hass.services.async_call(
            entity.split(".", 1)[0],
            "select_option",
            {"entity_id": entity, "option": option},
            blocking=True,
        )
        self._last_inverter_option = option

    async def _write_active_power(self, active_power_w: float) -> None:
        entity = self._mapping.active_power_entity
        if not entity:
            return
        if active_power_w == self._last_active_power:
            return
        _LOGGER.debug("Active power → %.0f W via %s", active_power_w, entity)
        await self._hass.services.async_call(
            "number",
            "set_value",
            {"entity_id": entity, "value": active_power_w},
            blocking=True,
        )
        self._last_active_power = active_power_w

    async def _write_ev_target(self, watts: float) -> None:
        entity = self._mapping.ev_charge_target_entity
        if not entity:
            return
        if watts == self._last_ev_charge_w:
            return
        await self._hass.services.async_call(
            "number",
            "set_value",
            {"entity_id": entity, "value": watts},
            blocking=True,
        )
        self._last_ev_charge_w = watts

    def reset_write_cache(self) -> None:
        self._last_inverter_option = None
        self._last_ev_charge_w = None
        self._last_active_power = None
