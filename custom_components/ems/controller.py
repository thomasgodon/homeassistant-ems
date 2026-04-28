from __future__ import annotations

import logging

from homeassistant.core import HomeAssistant

from .models import Decision, InverterVerb, VerbMapping

_LOGGER = logging.getLogger(__name__)


class Controller:
    def __init__(self, hass: HomeAssistant, mapping: VerbMapping) -> None:
        self._hass = hass
        self._mapping = mapping
        self._last_inverter_verb: InverterVerb | None = None
        self._last_power_target_w: float | None = None

    def update_mapping(self, mapping: VerbMapping) -> None:
        self._mapping = mapping

    async def write_full(self, decision: Decision, *, suppress_writes: bool = False) -> None:
        """Called by 10 s and 1 s coords. Idempotent. No-op when EV is idle."""
        if decision.ev_charge_power_w == 0.0:
            self.reset_write_cache()
            return

        if suppress_writes:
            _LOGGER.debug("Startup grace: would write strategy=%s", decision.strategy)
            return

        await self._write_inverter(decision)

    async def write_power_only(self, decision: Decision) -> None:
        """Called by 1 s fast loop. Writes inverter_power_target only."""
        if decision.ev_charge_power_w == 0.0:
            self.reset_write_cache()
            return
        await self._write_power_target(decision.inverter_power_target_w)

    # ── private helpers ───────────────────────────────────────────────────────

    async def _write_inverter(self, decision: Decision) -> None:
        verb = decision.inverter_verb
        if verb is None:
            return
        option = self._mapping.self_use_option
        if not option:
            return

        if verb != self._last_inverter_verb:
            entity = self._mapping.inverter_select_entity
            if entity:
                _LOGGER.info("Inverter → %s (%s) via %s", verb.value, option, entity)
                await self._hass.services.async_call(
                    entity.split(".", 1)[0],
                    "select_option",
                    {"entity_id": entity, "option": option},
                    blocking=True,
                )
                self._last_inverter_verb = verb

        await self._write_power_target(decision.inverter_power_target_w)

    async def _write_power_target(self, power_w: float | None) -> None:
        if power_w is None:
            return
        entity = self._mapping.inverter_power_target_entity
        if not entity:
            return
        if power_w != self._last_power_target_w:
            _LOGGER.info("Inverter power target → %.0f W via %s", power_w, entity)
            await self._hass.services.async_call(
                "number",
                "set_value",
                {"entity_id": entity, "value": power_w},
                blocking=True,
            )
            self._last_power_target_w = power_w

    def reset_write_cache(self) -> None:
        self._last_inverter_verb = None
        self._last_power_target_w = None
