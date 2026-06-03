from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from . import inverter_control as inverter_control_module
from . import strategy as strategy_module
from .const import (
    CONF_ACTIVE_POWER_ENTITY,
    CONF_BATTERY_CAPACITY,
    CONF_BATTERY_SOC,
    CONF_EV_CHARGE_TARGET_ENTITY,
    CONF_EV_MAX_CHARGE_W,
    CONF_EV_PLUG,
    CONF_EV_POWER,
    CONF_EV_SOC,
    CONF_EV_TARGET_SOC,
    CONF_GRID_IMPORT_POWER,
    CONF_HOUSE_LOAD,
    CONF_HOUSE_LOAD_FORECAST_JSON_ENTITY,
    CONF_INVERTER_DISABLED_OPTION,
    CONF_INVERTER_POWER_CONTROL_OPTION,
    CONF_INVERTER_SELECT,
    CONF_MAX_GRID_IMPORT_W,
    CONF_PRICE_SIGNAL_ENTITY,
    CONF_PRICES_JSON_ENTITY,
    CONF_PV_POWER,
    CONF_SOLAR_FORECAST_JSON_ENTITY,
    DEFAULT_BATTERY_TARGET_SOC,
    DEFAULT_EV_CHARGE_ADVISED_CHEAP_HOURS,
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

_EV_SMOOTHING_ALPHA: dict[str, float] = {"Fast": 0.3, "Normal": 0.2, "Slow": 0.1}
_ACTIVE_POWER_EMA_ALPHA: float = 0.3


@dataclass
class EmsState:
    last_result: LadderResult | None = None
    startup_grace_until: datetime | None = None


class EmsStrategyCoordinator(DataUpdateCoordinator[LadderResult]):
    """10 s default. Builds Inputs → strategy.decide + inverter_control.decide → write_slow."""

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
        self._state.startup_grace_until = dt_util.now() + timedelta(seconds=60)
        self._controller.reset_write_cache()

        watched: list[str] = [
            "select.ems_ev_charging_mode",
            "select.ems_inverter_control_mode",
            "select.ems_ev_smoothing",
            "number.ems_ev_min_charge_w",
            "number.ems_battery_target_soc",
            "number.ems_ev_charge_advised_cheap_hours",
        ]
        data = self._entry.data
        for key in (
            CONF_GRID_IMPORT_POWER, CONF_PV_POWER, CONF_HOUSE_LOAD,
            CONF_EV_POWER, CONF_EV_PLUG, CONF_EV_SOC,
            CONF_EV_TARGET_SOC, CONF_EV_MAX_CHARGE_W,
            CONF_BATTERY_SOC, CONF_BATTERY_CAPACITY,
            CONF_PRICE_SIGNAL_ENTITY, CONF_PRICES_JSON_ENTITY,
            CONF_MAX_GRID_IMPORT_W,
            CONF_SOLAR_FORECAST_JSON_ENTITY, CONF_HOUSE_LOAD_FORECAST_JSON_ENTITY,
        ):
            entity_id = data.get(key)
            if entity_id:
                watched.append(entity_id)

        @callback
        def _on_input_change(_event) -> None:
            self.hass.async_create_task(self.async_request_refresh())

        self._entry.async_on_unload(
            async_track_state_change_event(self.hass, watched, _on_input_change)
        )

    async def _async_update_data(self) -> LadderResult:
        try:
            inputs = await self._build_inputs()
        except Exception as exc:
            raise UpdateFailed(f"EMS input build failed: {exc}") from exc

        ev_decision = strategy_module.decide(inputs)
        prior_inv_status = (
            self._state.last_result.inverter_decision.status
            if self._state.last_result is not None
            else None
        )
        inverter_decision = inverter_control_module.decide(
            inputs,
            commanded_ev_charge_w=ev_decision.ev_charge_power_w,
            currently_peak_shaving=(prior_inv_status == "peak_shaving"),
        )

        in_grace = (
            self._state.startup_grace_until is not None
            and inputs.now < self._state.startup_grace_until
        )
        if in_grace:
            ev_decision = replace(ev_decision, charging_state="starting_up")
            inverter_decision = replace(inverter_decision, status="starting_up")
        try:
            await self._controller.write_slow(
                ev_decision, inverter_decision, suppress_writes=in_grace
            )
        except Exception:
            _LOGGER.exception("EMS controller write failed; decision still reported")

        result = LadderResult(
            ev_decision=ev_decision,
            inverter_decision=inverter_decision,
            inputs=inputs,
            computed_at=inputs.now,
            should_advise_ev_charge=strategy_module.should_advise_ev_charge(inputs),
        )
        self._state.last_result = result
        return result

    async def _build_inputs(self) -> Inputs:
        hass = self.hass
        data = self._entry.data

        now = dt_util.now()

        ev_strategy = _read_select_state(hass, "select.ems_ev_charging_mode", "Off")
        inverter_strategy = _read_select_state(hass, "select.ems_inverter_control_mode", "Off")

        grid_import_w = _read_float(hass, data.get(CONF_GRID_IMPORT_POWER), 0.0)
        pv_w = _read_float(hass, data.get(CONF_PV_POWER), 0.0)
        house_load_w = _read_float(hass, data.get(CONF_HOUSE_LOAD), 0.0)

        ev_plugged = _read_bool(hass, data.get(CONF_EV_PLUG), True)
        ev_power_w = _read_float(hass, data.get(CONF_EV_POWER), 0.0)
        ev_soc = _read_float_optional(hass, data.get(CONF_EV_SOC))
        is_cheap_hour = _read_bool(hass, data.get(CONF_PRICE_SIGNAL_ENTITY), True)

        thresholds = Thresholds(
            max_grid_import_w=_read_float(
                hass, data.get(CONF_MAX_GRID_IMPORT_W), DEFAULT_MAX_GRID_IMPORT_W
            ),
            ev_target_soc=_read_float(
                hass, data.get(CONF_EV_TARGET_SOC), DEFAULT_EV_TARGET_SOC
            ),
            ev_min_charge_w=_read_number(hass, "number.ems_ev_min_charge_w", 1400.0),
            max_ev_charge_w=_read_float_optional(hass, data.get(CONF_EV_MAX_CHARGE_W)),
            ev_charge_advised_cheap_hours=int(_read_number(
                hass, "number.ems_ev_charge_advised_cheap_hours",
                DEFAULT_EV_CHARGE_ADVISED_CHEAP_HOURS,
            )),
            max_active_power_w=_read_entity_max_abs(hass, data.get(CONF_ACTIVE_POWER_ENTITY)),
        )

        mapping = VerbMapping(
            inverter_select_entity=data.get(CONF_INVERTER_SELECT),
            disabled_option=data.get(CONF_INVERTER_DISABLED_OPTION) or None,
            power_control_option=data.get(CONF_INVERTER_POWER_CONTROL_OPTION) or None,
            active_power_entity=data.get(CONF_ACTIVE_POWER_ENTITY),
            ev_charge_target_entity=data.get(CONF_EV_CHARGE_TARGET_ENTITY),
        )

        last_ev_target_w = (
            self._state.last_result.ev_decision.ev_charge_power_w
            if self._state.last_result is not None
            else None
        )

        battery_soc = _read_float_optional(hass, data.get(CONF_BATTERY_SOC))
        battery_capacity_kwh = _read_kwh_optional(hass, data.get(CONF_BATTERY_CAPACITY))
        battery_target_soc = _read_number(
            hass, "number.ems_battery_target_soc", DEFAULT_BATTERY_TARGET_SOC
        )
        solar_forecast_today = _read_solar_forecast_json(
            hass, data.get(CONF_SOLAR_FORECAST_JSON_ENTITY)
        )
        solar_remaining = strategy_module.solar_remaining_wh(solar_forecast_today, now)
        solar_forecast_remaining_kwh = (
            solar_remaining / 1000.0 if solar_remaining is not None else None
        )
        house_load_forecast_today = _read_solar_forecast_json(
            hass, data.get(CONF_HOUSE_LOAD_FORECAST_JSON_ENTITY)
        )
        prices_today = _read_prices_json(hass, data.get(CONF_PRICES_JSON_ENTITY))

        return Inputs(
            now=now,
            grid_import_w=grid_import_w,
            pv_w=pv_w,
            house_load_w=house_load_w,
            ev_plugged=ev_plugged,
            ev_power_w=ev_power_w,
            ev_soc=ev_soc,
            ev_strategy=ev_strategy,  # type: ignore[arg-type]
            is_cheap_hour=is_cheap_hour,
            inverter_strategy=inverter_strategy,  # type: ignore[arg-type]
            thresholds=thresholds,
            mapping=mapping,
            last_ev_target_w=last_ev_target_w,
            battery_soc=battery_soc,
            battery_capacity_kwh=battery_capacity_kwh,
            battery_target_soc=battery_target_soc,
            solar_forecast_remaining_kwh=solar_forecast_remaining_kwh,
            prices_today=prices_today,
            solar_forecast_today=solar_forecast_today,
            house_load_forecast_today=house_load_forecast_today,
        )


class EmsFastLoopCoordinator(DataUpdateCoordinator["LadderResult | None"]):
    """1 s default. Re-runs strategy + inverter_control with fresh power readings every second."""

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
        self._ema_ev_w: float | None = None
        self._ema_active_w: float | None = None
        self._prev_ev_power_w: float = 0.0
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_fast_loop",
            update_interval=timedelta(seconds=1),
        )

    async def _async_update_data(self) -> LadderResult | None:
        if self._state.last_result is None:
            return None
        grace_until = self._state.startup_grace_until
        if grace_until is not None and dt_util.now() < grace_until:
            return self._state.last_result

        data = self._entry.data
        pv_w = _read_float(self.hass, data.get(CONF_PV_POWER), 0.0)
        house_load_w = _read_float(self.hass, data.get(CONF_HOUSE_LOAD), 0.0)
        ev_power_w = _read_float(self.hass, data.get(CONF_EV_POWER), 0.0)
        grid_import_w = _read_float(self.hass, data.get(CONF_GRID_IMPORT_POWER), 0.0)

        prior_inputs = self._state.last_result.inputs

        ev_strategy = _read_select_state(self.hass, "select.ems_ev_charging_mode", "Off")
        inverter_strategy = _read_select_state(self.hass, "select.ems_inverter_control_mode", "Off")
        is_cheap_hour = _read_bool(self.hass, data.get(CONF_PRICE_SIGNAL_ENTITY), True)
        ev_plugged = _read_bool(self.hass, data.get(CONF_EV_PLUG), True)

        # Battery inputs are re-read every 10 s by the strategy loop; reuse from prior state.
        fast_inputs_for_eod = replace(
            prior_inputs,
            now=dt_util.now(),
            pv_w=pv_w,
            house_load_w=house_load_w,
            ev_power_w=ev_power_w,
            grid_import_w=grid_import_w,
            is_cheap_hour=is_cheap_hour,
            ev_plugged=ev_plugged,
        )
        eod_suppress = strategy_module.is_eod_suppress_hour(fast_inputs_for_eod)
        shortfall_kwh = strategy_module.solar_shortfall_kwh(fast_inputs_for_eod)

        ev_fast = strategy_module.recompute(
            pv_w=pv_w,
            house_load_w=house_load_w,
            ev_power_w=ev_power_w,
            grid_import_w=grid_import_w,
            ev_plugged=ev_plugged,
            ev_soc=prior_inputs.ev_soc,
            ev_strategy=ev_strategy,
            is_cheap_hour=is_cheap_hour,
            thresholds=prior_inputs.thresholds,
            last_ev_target_w=self._ema_ev_w,
            shortfall_kwh=shortfall_kwh,
        )

        session_start = self._prev_ev_power_w == 0.0 and ev_power_w > 0.0
        self._prev_ev_power_w = ev_power_w

        # Apply EV-target EMA before the inverter decision so commanded_ev_charge_w
        # reflects the smoothed value that will actually be written to the wallbox.
        if ev_strategy == "Off":
            self._ema_ev_w = None
            self._ema_active_w = None
        elif ev_fast.strategy != "ev_charging":
            # Strategy returned an idle decision (EV unplugged, outside cheap hours,
            # target SoC reached, etc.). Snap the EMA to exactly 0.0 instead of
            # smoothly decaying — the decay underflows to a denormalized double
            # that fails the commanded_ev_charge_w == 0.0 check in inverter_control,
            # keeping the inverter stuck in "controlling" indefinitely.
            self._ema_ev_w = 0.0
            ev_fast = replace(ev_fast, ev_charge_power_w=0.0)
        else:
            ev_alpha = _EV_SMOOTHING_ALPHA.get(
                _read_select_state(self.hass, "select.ems_ev_smoothing", "Fast"), 0.3
            )

            if session_start or self._ema_ev_w is None:
                self._ema_ev_w = ev_fast.ev_charge_power_w
            else:
                self._ema_ev_w = (
                    ev_alpha * ev_fast.ev_charge_power_w
                    + (1.0 - ev_alpha) * self._ema_ev_w
                )

            ev_fast = replace(ev_fast, ev_charge_power_w=self._ema_ev_w)

        inverter_fast = inverter_control_module.recompute(
            pv_w=pv_w,
            house_load_w=house_load_w,
            ev_power_w=ev_power_w,
            grid_import_w=grid_import_w,
            inverter_strategy=inverter_strategy,
            mapping=prior_inputs.mapping,
            eod_suppress=eod_suppress,
            max_grid_import_w=prior_inputs.thresholds.max_grid_import_w,
            commanded_ev_charge_w=ev_fast.ev_charge_power_w,
            battery_soc=prior_inputs.battery_soc,
            battery_target_soc=prior_inputs.battery_target_soc,
            ev_strategy=ev_strategy,
            solar_shortfall_kwh_result=shortfall_kwh,
            max_active_power_w=prior_inputs.thresholds.max_active_power_w,
            currently_peak_shaving=(
                self._state.last_result.inverter_decision.status == "peak_shaving"
            ),
        )

        if ev_strategy != "Off":
            if inverter_fast.active_power_w is None:
                self._ema_active_w = None
            elif inverter_fast.active_power_w is not None:
                if session_start or self._ema_active_w is None:
                    self._ema_active_w = inverter_fast.active_power_w
                else:
                    self._ema_active_w = (
                        _ACTIVE_POWER_EMA_ALPHA * inverter_fast.active_power_w
                        + (1.0 - _ACTIVE_POWER_EMA_ALPHA) * self._ema_active_w
                    )
                inverter_fast = replace(inverter_fast, active_power_w=self._ema_active_w)

        await self._controller.write_fast(ev_fast, inverter_fast)

        now = dt_util.now()
        fast_inputs = replace(fast_inputs_for_eod, now=now)
        result = LadderResult(
            ev_decision=ev_fast,
            inverter_decision=inverter_fast,
            inputs=fast_inputs,
            computed_at=now,
            should_advise_ev_charge=strategy_module.should_advise_ev_charge(fast_inputs),
        )
        self._state.last_result = result
        return result


class EmsForecastCoordinator(DataUpdateCoordinator["Inputs | None"]):
    """No-poll coordinator; refreshes at startup then on forecast/battery-SoC entity changes."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self._entry = entry
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_forecast",
            update_interval=None,
        )

    async def async_setup(self) -> None:
        data = self._entry.data
        watched: list[str] = ["number.ems_battery_target_soc"]
        for key in (
            CONF_BATTERY_SOC,
            CONF_BATTERY_CAPACITY,
            CONF_SOLAR_FORECAST_JSON_ENTITY,
            CONF_HOUSE_LOAD_FORECAST_JSON_ENTITY,
        ):
            entity_id = data.get(key)
            if entity_id:
                watched.append(entity_id)

        @callback
        def _on_forecast_change(_event) -> None:
            self.hass.async_create_task(self.async_request_refresh())

        self._entry.async_on_unload(
            async_track_state_change_event(self.hass, watched, _on_forecast_change)
        )

    async def _async_update_data(self) -> Inputs:
        data = self._entry.data
        return Inputs(
            now=dt_util.now(),
            grid_import_w=0.0,
            pv_w=0.0,
            house_load_w=0.0,
            ev_plugged=False,
            ev_power_w=0.0,
            ev_soc=None,
            ev_strategy="Off",
            is_cheap_hour=False,
            inverter_strategy="Off",
            thresholds=Thresholds(max_grid_import_w=0.0, ev_target_soc=80.0, ev_min_charge_w=0.0),
            mapping=VerbMapping(
                inverter_select_entity=None,
                disabled_option=None,
                power_control_option=None,
                active_power_entity=None,
                ev_charge_target_entity=None,
            ),
            battery_soc=_read_float_optional(self.hass, data.get(CONF_BATTERY_SOC)),
            battery_capacity_kwh=_read_kwh_optional(self.hass, data.get(CONF_BATTERY_CAPACITY)),
            battery_target_soc=_read_number(
                self.hass, "number.ems_battery_target_soc", DEFAULT_BATTERY_TARGET_SOC
            ),
            solar_forecast_remaining_kwh=_derive_solar_remaining_kwh(
                self.hass, data.get(CONF_SOLAR_FORECAST_JSON_ENTITY), dt_util.now()
            ),
            solar_forecast_today=_read_solar_forecast_json(
                self.hass, data.get(CONF_SOLAR_FORECAST_JSON_ENTITY)
            ),
            house_load_forecast_today=_read_solar_forecast_json(
                self.hass, data.get(CONF_HOUSE_LOAD_FORECAST_JSON_ENTITY)
            ),
        )


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


def _read_kwh_optional(hass: HomeAssistant, entity_id: str | None) -> float | None:
    """Read a capacity entity and normalise to kWh, honouring unit_of_measurement."""
    if not entity_id:
        return None
    state = hass.states.get(entity_id)
    if state is None or state.state in ("unavailable", "unknown"):
        return None
    try:
        value = float(state.state)
    except (ValueError, TypeError):
        return None
    unit = (state.attributes.get("unit_of_measurement") or "").strip().lower()
    if unit == "wh":
        return value / 1000.0
    return value  # kWh or unspecified — assume kWh


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


def _read_solar_forecast_json(
    hass: HomeAssistant, entity_id: str | None
) -> tuple[float, ...] | None:
    if not entity_id:
        return None
    state = hass.states.get(entity_id)
    if state is None or state.state in ("unavailable", "unknown"):
        return None
    try:
        import json
        values = json.loads(state.state)
        if isinstance(values, list) and len(values) == 24:
            return tuple(float(v) for v in values)
    except Exception:
        _LOGGER.warning(
            "EMS: could not parse solar_forecast_json_entity '%s' as 24-element list", entity_id
        )
    return None


def _derive_solar_remaining_kwh(
    hass: HomeAssistant, entity_id: str | None, now: datetime
) -> float | None:
    forecast = _read_solar_forecast_json(hass, entity_id)
    remaining = strategy_module.solar_remaining_wh(forecast, now)
    return (remaining / 1000.0) if remaining is not None else None


def _read_entity_max_abs(hass: HomeAssistant, entity_id: str | None) -> float | None:
    """Return min(|min_attr|, |max_attr|) from a number entity, or None if unavailable.

    Used to auto-detect the inverter active_power entity's accepted magnitude so that
    inverter_control can clamp computed values before writing — preventing
    ServiceValidationError when PV/house_load exceed the entity's range.
    """
    if not entity_id:
        return None
    state = hass.states.get(entity_id)
    if state is None:
        return None
    try:
        min_val = state.attributes.get("min")
        max_val = state.attributes.get("max")
        if min_val is None or max_val is None:
            return None
        return min(abs(float(min_val)), abs(float(max_val)))
    except (ValueError, TypeError):
        return None


def _read_prices_json(hass: HomeAssistant, entity_id: str | None) -> tuple[float, ...] | None:
    if not entity_id:
        return None
    state = hass.states.get(entity_id)
    if state is None or state.state in ("unavailable", "unknown"):
        return None
    try:
        import json
        prices = json.loads(state.state)
        if isinstance(prices, list) and len(prices) == 24:
            return tuple(float(p) for p in prices)
    except Exception:
        _LOGGER.warning(
            "EMS: could not parse prices_json_entity '%s' as 24-element list", entity_id
        )
    return None
