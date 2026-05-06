from __future__ import annotations

import logging

from homeassistant.core import HomeAssistant

from .models import EvDecision, InverterDecision, VerbMapping

_LOGGER = logging.getLogger(__name__)


class Controller:
    def __init__(self, hass: HomeAssistant, mapping: VerbMapping) -> None:
        self._hass = hass
        self._mapping = mapping
        self._last_ev_charge_w: float | None = None
        self._last_active_power: float | None = None

    def update_mapping(self, mapping: VerbMapping) -> None:
        self._mapping = mapping

    async def write_slow(
        self,
        ev_decision: EvDecision,
        inverter_decision: InverterDecision,
        *,
        suppress_writes: bool = False,
    ) -> None:
        """Called by the 10 s strategy loop. Writes the inverter verb on every call.

        The verb is always asserted (no idempotency cache) so user manual overrides
        are corrected within one slow-loop cycle.
        """
        if suppress_writes:
            _LOGGER.debug(
                "Startup grace: would write strategy=%s",
                ev_decision.strategy,
            )
            return

        if inverter_decision.inverter_verb is not None:
            await self._select_inverter_option(inverter_decision.inverter_verb)
        if inverter_decision.active_power_w is not None:
            await self._write_active_power(inverter_decision.active_power_w)
        await self._write_ev_target(ev_decision.ev_charge_power_w)

    async def write_fast(
        self,
        ev_decision: EvDecision,
        inverter_decision: InverterDecision,
    ) -> None:
        """Called by the 1 s fast loop. Never writes the inverter verb."""
        if inverter_decision.active_power_w is not None:
            await self._write_active_power(inverter_decision.active_power_w)
        await self._write_ev_target(ev_decision.ev_charge_power_w)

    # ── private helpers ───────────────────────────────────────────────────────

    async def _select_inverter_option(self, option: str) -> None:
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

    async def _write_active_power(self, active_power_w: float) -> None:
        entity = self._mapping.active_power_entity
        if not entity:
            return
        if active_power_w == self._last_active_power:
            return
        _LOGGER.debug("Active power → %.0f W via %s", active_power_w, entity)
        await self._hass.services.async_call(
            entity.split(".", 1)[0],
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
            entity.split(".", 1)[0],
            "set_value",
            {"entity_id": entity, "value": watts},
            blocking=True,
        )
        self._last_ev_charge_w = watts

    def reset_write_cache(self) -> None:
        self._last_ev_charge_w = None
        self._last_active_power = None
