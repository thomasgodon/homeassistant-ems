from __future__ import annotations

import logging
from datetime import datetime, timedelta

from homeassistant.core import HomeAssistant

from .models import Decision, EvVerb, InverterVerb, VerbMapping

_LOGGER = logging.getLogger(__name__)

# How long before repeating a mode_unsupported warning (24 h)
_WARN_REPEAT_AFTER = timedelta(hours=24)
_WARN_DEDUP_MAX = 64


class Controller:
    def __init__(self, hass: HomeAssistant, mapping: VerbMapping) -> None:
        self._hass = hass
        self._mapping = mapping
        self._last_inverter_verb: InverterVerb | None = None
        self._last_ev_verb: EvVerb | None = None
        self._last_power_target_w: float | None = None
        self._last_ev_max_power_w: float | None = None
        # dedup key → (first_warned_at, count, last_warned_at)
        self._unsupported_warned: dict[str, tuple[datetime, int, datetime]] = {}

    def update_mapping(self, mapping: VerbMapping) -> None:
        self._mapping = mapping

    async def write_full(self, decision: Decision, *, suppress_writes: bool = False) -> None:
        """Called by 10 s coord. Idempotent. Off-mode → no-op."""
        if decision.strategy == "off":
            return

        if suppress_writes:
            _LOGGER.debug("Startup grace: would write strategy=%s", decision.strategy)
            return

        await self._write_inverter(decision)
        await self._write_ev(decision)
        await self._write_ev_max_power(decision)

    async def write_power_only(self, decision: Decision) -> None:
        """Called by 1 s coord. Writes inverter_power_target and ev_max_power only."""
        await self._write_power_target(decision.inverter_power_target_w)
        await self._write_ev_max_power(decision)

    # ── private helpers ───────────────────────────────────────────────────────

    async def _write_inverter(self, decision: Decision) -> None:
        verb = decision.inverter_verb
        if verb is None:
            return
        option = self._resolve_inverter(verb)
        if option is None:
            return

        if verb != self._last_inverter_verb:
            entity = self._mapping.inverter_select_entity
            if entity:
                _LOGGER.info("Inverter → %s (%s) via %s", verb.value, option, entity)
                await self._hass.services.async_call(
                    "select",
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

    async def _write_ev(self, decision: Decision) -> None:
        verb = decision.ev_verb
        if verb is None:
            return
        option = self._resolve_ev(verb)
        if option is None:
            return

        if verb != self._last_ev_verb:
            entity = self._mapping.ev_select_entity
            if entity:
                _LOGGER.info("EV charger → %s (%s) via %s", verb.value, option, entity)
                await self._hass.services.async_call(
                    "select",
                    "select_option",
                    {"entity_id": entity, "option": option},
                    blocking=True,
                )
                self._last_ev_verb = verb

    async def _write_ev_max_power(self, decision: Decision) -> None:
        power_w = decision.ev_max_power_w
        if power_w is None:
            return
        entity = self._mapping.ev_max_power_entity
        if not entity:
            return
        if power_w != self._last_ev_max_power_w:
            _LOGGER.info("EV max power → %.0f W via %s", power_w, entity)
            await self._hass.services.async_call(
                "number",
                "set_value",
                {"entity_id": entity, "value": power_w},
                blocking=True,
            )
            self._last_ev_max_power_w = power_w

    def _resolve_inverter(self, verb: InverterVerb) -> str | None:
        return self._mapping.inverter_options.get(verb)

    def _resolve_ev(self, verb: EvVerb) -> str | None:
        return self._mapping.ev_options.get(verb)

    def log_unsupported(self, verb_label: str, now: datetime) -> None:
        """Log mode_unsupported warnings with 24 h dedup."""
        if len(self._unsupported_warned) >= _WARN_DEDUP_MAX:
            oldest = min(self._unsupported_warned, key=lambda k: self._unsupported_warned[k][0])
            del self._unsupported_warned[oldest]

        if verb_label not in self._unsupported_warned:
            _LOGGER.warning(
                "EMS: inverter does not support %s — rule will fall through. "
                "Map the verb in the config flow to suppress this warning.",
                verb_label,
            )
            self._unsupported_warned[verb_label] = (now, 1, now)
        else:
            first, count, last_warned = self._unsupported_warned[verb_label]
            if now - last_warned >= _WARN_REPEAT_AFTER:
                _LOGGER.warning(
                    "EMS: %s still unsupported (%d occurrences since %s).",
                    verb_label,
                    count,
                    first.isoformat(),
                )
                self._unsupported_warned[verb_label] = (first, count + 1, now)
            else:
                self._unsupported_warned[verb_label] = (first, count + 1, last_warned)

    def reset_write_cache(self) -> None:
        """Re-sync cache against live HA state at startup."""
        self._last_inverter_verb = None
        self._last_ev_verb = None
        self._last_power_target_w = None
        self._last_ev_max_power_w = None
