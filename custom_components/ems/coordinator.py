from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from homeassistant.components.sun import STATE_ABOVE_HORIZON
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import event as ha_event
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .const import (
    CONF_BATTERY_POWER,
    CONF_BATTERY_SOC,
    CONF_EV_CAPACITY_KWH,
    CONF_EV_MAX_CHARGE_KW,
    CONF_EV_MAX_POWER_ENTITY,
    CONF_EV_OPTIONS,
    CONF_EV_PLUG,
    CONF_EV_POWER,
    CONF_EV_SELECT,
    CONF_EV_SOC,
    CONF_EXPORT_PRICE,
    CONF_FORECAST_PATH_IMPORT,
    CONF_GRID_IMPORT_15M_AVG,
    CONF_GRID_IMPORT_POWER,
    CONF_HOUSE_LOAD,
    CONF_HOUSE_LOAD_NON_EV,
    CONF_IMPORT_PRICE,
    CONF_INVERTER_OPTIONS,
    CONF_INVERTER_POWER_TARGET,
    CONF_INVERTER_SELECT,
    CONF_LOAD_FORECAST_KWH,
    CONF_PV_FORECAST_KWH,
    CONF_PV_POWER,
    DEFAULT_PEAK_SHAVE_INTERVAL_S,
    DEFAULT_STARTUP_GRACE_S,
    DEFAULT_STRATEGY_INTERVAL_S,
    DOMAIN,
    STORAGE_KEY,
    STORAGE_VERSION,
)
from .controller import Controller
from .ev_scheduler import compute_window, parse_forecast
from .models import (
    EvVerb,
    EvWindow,
    Features,
    Inputs,
    InverterVerb,
    LadderResult,
    Thresholds,
    VerbMapping,
)
from . import strategy as strategy_module

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry

_LOGGER = logging.getLogger(__name__)

_DISPATCHER_EV_WINDOW = f"{DOMAIN}_ev_window_updated"


@dataclass
class EmsState:
    last_result: LadderResult | None = None
    peak_risk_latched: bool = False
    last_inverter_verb_written: InverterVerb | None = None
    last_ev_verb_written: EvVerb | None = None
    last_power_target_written_w: float | None = None
    last_ev_max_power_written_w: float | None = None
    cached_window: EvWindow | None = None
    startup_grace_until: datetime | None = None
    forecast_parse_warned: bool = False
    last_unsupported_warned: dict[tuple[str, str], dict] = field(default_factory=dict)


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
        self._store: Store = Store(hass, STORAGE_VERSION, STORAGE_KEY)
        interval_s = DEFAULT_STRATEGY_INTERVAL_S
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_strategy",
            update_interval=timedelta(seconds=interval_s),
        )

    async def async_setup(self) -> None:
        """Load persisted state and set startup grace window."""
        stored = await self._store.async_load()
        if stored:
            self._state.peak_risk_latched = stored.get("peak_risk_latched", False)
            window_data = stored.get("cached_window")
            if window_data:
                self._state.cached_window = EvWindow(
                    start=_parse_stored_dt(window_data.get("start")),
                    end=_parse_stored_dt(window_data.get("end")),
                    in_window_now=window_data.get("in_window_now", False),
                    reason=window_data.get("reason", "stale_forecast"),
                )
        grace_s = entry_option(self._entry, "startup_grace_s", DEFAULT_STARTUP_GRACE_S)
        self._state.startup_grace_until = dt_util.utcnow() + timedelta(seconds=grace_s)
        self._controller.reset_write_cache()

    async def _async_update_data(self) -> LadderResult:
        # Live-tune interval
        interval_s = _read_number_state(
            self.hass, "number.ems_strategy_interval_s", DEFAULT_STRATEGY_INTERVAL_S
        )
        new_interval = timedelta(seconds=interval_s)
        if new_interval != self.update_interval:
            self.update_interval = new_interval

        try:
            inputs = await self._build_inputs()
        except Exception as exc:
            raise UpdateFailed(f"EMS input build failed: {exc}") from exc

        decision = strategy_module.decide(inputs)
        self._state.peak_risk_latched = decision.new_peak_risk

        for verb_label in decision.unsupported_verbs:
            self._controller.log_unsupported(verb_label, inputs.now)

        in_grace = (
            self._state.startup_grace_until is not None
            and inputs.now < self._state.startup_grace_until
        )
        await self._controller.write_full(decision, suppress_writes=in_grace)

        result = LadderResult(decision=decision, inputs=inputs, computed_at=inputs.now)
        self._state.last_result = result

        await self._persist()
        return result

    async def _build_inputs(self) -> Inputs:
        hass = self.hass
        data = self._entry.data

        now = dt_util.utcnow()

        ev_strategy = _read_select_state(hass, "select.ems_ev_scheduling_strategy", "Off")

        grid_import_w = _read_float(hass, data.get(CONF_GRID_IMPORT_POWER), 0.0)
        grid_import_15m = _read_float_optional(hass, data.get(CONF_GRID_IMPORT_15M_AVG))
        pv_w = _read_float(hass, data.get(CONF_PV_POWER), 0.0)
        house_load_w = _read_float(hass, data.get(CONF_HOUSE_LOAD), 0.0)
        house_load_non_ev = _read_float_optional(hass, data.get(CONF_HOUSE_LOAD_NON_EV))
        battery_soc = _read_float(hass, data.get(CONF_BATTERY_SOC), 50.0)
        battery_power_w = _read_float(hass, data.get(CONF_BATTERY_POWER), 0.0)

        ev_plugged = _read_bool(hass, data.get(CONF_EV_PLUG), False)
        ev_power_w = _read_float(hass, data.get(CONF_EV_POWER), 0.0)
        ev_soc = _read_float_optional(hass, data.get(CONF_EV_SOC))
        ev_capacity_kwh = float(data.get(CONF_EV_CAPACITY_KWH, 60.0))
        ev_max_charge_kw = float(data.get(CONF_EV_MAX_CHARGE_KW, 7.4))

        import datetime as _dt
        ev_ready_by_state = _read_time_state(hass, "time.ems_ev_ready_by")
        ev_ready_by = ev_ready_by_state or _dt.time(7, 0)

        import_price = _read_float_optional(hass, data.get(CONF_IMPORT_PRICE))
        export_price = _read_float_optional(hass, data.get(CONF_EXPORT_PRICE))

        # Rolling 24h avg price from sensor
        avg_24h_price = _read_float_optional(hass, "sensor.ems_24h_avg_price")

        in_cheap_window = (
            self._state.cached_window.in_window_now
            if self._state.cached_window is not None
            else False
        )

        pv_forecast_kwh = _read_float_optional(hass, data.get(CONF_PV_FORECAST_KWH))
        house_forecast_kwh = _read_float_optional(hass, data.get(CONF_LOAD_FORECAST_KWH))

        is_daytime = _read_select_state(hass, "sun.sun", "below_horizon") == STATE_ABOVE_HORIZON

        features = Features(
            peak_shaving=_read_bool(hass, "switch.ems_peak_shaving", True),
            proactive_grid_charge=_read_bool(hass, "switch.ems_battery_grid_charging", True),
            ev_charging=_read_bool(hass, "switch.ems_ev_charging", True),
            forecast_planning=_read_bool(hass, "switch.ems_forecast_planning", False),
            export_curtail=_read_bool(hass, "switch.ems_export_curtailment", False),
            ev_absorbs_curtail=_read_bool(hass, "switch.ems_ev_absorbs_curtailment", True),
        )

        thresholds = Thresholds(
            max_grid_import_w=_read_number(hass, "number.ems_max_grid_import_w", 4500.0),
            battery_reserve_soc=_read_number(hass, "number.ems_battery_reserve_soc", 10.0),
            battery_target_soc_sunset=_read_number(
                hass, "number.ems_battery_target_soc_sunset", 80.0
            ),
            battery_capacity_kwh=_read_number(hass, "number.ems_battery_capacity_kwh", 10.0),
            battery_max_discharge_w=_read_number(
                hass, "number.ems_battery_max_discharge_w", 3000.0
            ),
            battery_full_soc=_read_number(hass, "number.ems_battery_full_soc", 95.0),
            proactive_grid_charge_price=_read_number(
                hass, "number.ems_proactive_grid_charge_price", 0.05
            ),
            expensive_price_multiplier=_read_number(
                hass, "number.ems_expensive_price_multiplier", 1.3
            ),
            export_curtail_price=_read_number(hass, "number.ems_export_curtail_price", 0.00),
            ev_target_soc=_read_number(hass, "number.ems_ev_target_soc", 80.0),
            peak_shave_headroom_w=_read_number(hass, "number.ems_peak_shave_headroom_w", 300.0),
            solar_surplus_threshold_w=_read_number(
                hass, "number.ems_solar_surplus_threshold_w", 100.0
            ),
        )

        inverter_options_raw: dict = data.get(CONF_INVERTER_OPTIONS, {})
        inverter_options = {
            InverterVerb[k]: (v if v else None)
            for k, v in inverter_options_raw.items()
            if k in InverterVerb.__members__
        }
        # Ensure all verbs present (missing = not configured = unsupported)
        for verb in InverterVerb:
            inverter_options.setdefault(verb, None)

        ev_options_raw: dict = data.get(CONF_EV_OPTIONS, {})
        ev_options = {
            EvVerb[k]: (v if v else None)
            for k, v in ev_options_raw.items()
            if k in EvVerb.__members__
        }
        for verb in EvVerb:
            ev_options.setdefault(verb, None)

        mapping = VerbMapping(
            inverter_select_entity=data.get(CONF_INVERTER_SELECT),
            inverter_options=inverter_options,
            inverter_power_target_entity=data.get(CONF_INVERTER_POWER_TARGET),
            ev_select_entity=data.get(CONF_EV_SELECT),
            ev_options=ev_options,
            ev_max_power_entity=data.get(CONF_EV_MAX_POWER_ENTITY),
        )

        return Inputs(
            now=now,
            grid_import_w=grid_import_w,
            grid_import_15m_avg_w=grid_import_15m,
            pv_w=pv_w,
            house_load_w=house_load_w,
            house_load_non_ev_w=house_load_non_ev,
            battery_soc=battery_soc,
            battery_power_w=battery_power_w,
            ev_plugged=ev_plugged,
            ev_power_w=ev_power_w,
            ev_soc=ev_soc,
            ev_capacity_kwh=ev_capacity_kwh,
            ev_max_charge_kw=ev_max_charge_kw,
            ev_ready_by=ev_ready_by,
            ev_strategy=ev_strategy,  # type: ignore[arg-type]
            import_price=import_price,
            export_price=export_price,
            avg_24h_price=avg_24h_price,
            in_cheap_window=in_cheap_window,
            pv_forecast_remaining_kwh=pv_forecast_kwh,
            house_load_forecast_kwh=house_forecast_kwh,
            features=features,
            thresholds=thresholds,
            mapping=mapping,
            peak_risk_latched=self._state.peak_risk_latched,
            is_daytime=is_daytime,
        )

    async def _persist(self) -> None:
        window = self._state.cached_window
        await self._store.async_save({
            "peak_risk_latched": self._state.peak_risk_latched,
            "cached_window": {
                "start": window.start.isoformat() if window and window.start else None,
                "end": window.end.isoformat() if window and window.end else None,
                "in_window_now": window.in_window_now if window else False,
                "reason": window.reason if window else "stale_forecast",
            } if window else None,
        })


class EmsPeakShaveCoordinator(DataUpdateCoordinator[None]):
    """1 s default. Active only when peak_risk_latched."""

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
            name=f"{DOMAIN}_peak_shave",
            update_interval=timedelta(seconds=DEFAULT_PEAK_SHAVE_INTERVAL_S),
        )

    async def _async_update_data(self) -> None:
        # Live-tune interval
        interval_s = _read_number_state(
            self.hass, "number.ems_peak_shave_interval_s", DEFAULT_PEAK_SHAVE_INTERVAL_S
        )
        new_interval = timedelta(seconds=interval_s)
        if new_interval != self.update_interval:
            self.update_interval = new_interval

        if not self._state.peak_risk_latched:
            return
        if self._state.last_result is None:
            return
        grace_until = self._state.startup_grace_until
        if grace_until is not None and dt_util.utcnow() < grace_until:
            return

        data = self._entry.data
        grid_import_w = _read_float(self.hass, data.get(CONF_GRID_IMPORT_POWER), 0.0)
        grid_import_15m = _read_float_optional(self.hass, data.get(CONF_GRID_IMPORT_15M_AVG))
        house_load_w = _read_float(self.hass, data.get(CONF_HOUSE_LOAD), 0.0)
        ev_power_w = _read_float(self.hass, data.get(CONF_EV_POWER), 0.0)
        house_non_ev_entity = data.get(CONF_HOUSE_LOAD_NON_EV)
        house_non_ev = _read_float_optional(self.hass, house_non_ev_entity)
        if house_non_ev is None:
            house_non_ev = house_load_w - ev_power_w

        thresholds = self._state.last_result.inputs.thresholds
        prior = self._state.last_result.decision

        power_target_w, ev_max_power_w = strategy_module.recompute_peak_shave_power(
            prior=prior,
            grid_import_w=grid_import_w,
            grid_import_15m_avg_w=grid_import_15m,
            house_load_non_ev_w=house_non_ev,
            thresholds=thresholds,
        )

        from .models import Decision as _Decision
        fast_decision = _Decision(
            strategy=prior.strategy,
            inverter_verb=prior.inverter_verb,
            inverter_power_target_w=power_target_w,
            ev_verb=prior.ev_verb,
            ev_max_power_w=ev_max_power_w,
            new_peak_risk=prior.new_peak_risk,
            rule_chain=prior.rule_chain,
            unsupported_verbs=prior.unsupported_verbs,
        )
        await self._controller.write_power_only(fast_decision)


class EvSchedulerOrchestrator:
    """5-minute timer + debounced state-change listener for EV window computation."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        state: EmsState,
    ) -> None:
        self._hass = hass
        self._entry = entry
        self._state = state
        self._unsub_timer: list = []
        self._unsub_events: list = []
        self._debounce_handle = None

    async def async_start(self) -> None:
        self._unsub_timer.append(
            ha_event.async_track_time_interval(
                self._hass, self._recompute, timedelta(minutes=5)
            )
        )
        data = self._entry.data
        watch_entities = [
            e for e in [
                data.get(CONF_EV_PLUG),
                data.get(CONF_EV_SOC),
                "time.ems_ev_ready_by",
                "select.ems_ev_scheduling_strategy",
                data.get(CONF_IMPORT_PRICE),
            ]
            if e
        ]
        if watch_entities:
            self._unsub_events.append(
                ha_event.async_track_state_change_event(
                    self._hass, watch_entities, self._on_change
                )
            )
        await self._recompute()

    def async_stop(self) -> None:
        for unsub in self._unsub_timer + self._unsub_events:
            unsub()
        self._unsub_timer.clear()
        self._unsub_events.clear()

    async def _recompute(self, _now=None) -> None:
        data = self._entry.data
        hass = self._hass

        ev_plugged = _read_bool(hass, data.get(CONF_EV_PLUG), False)
        if not ev_plugged:
            self._state.cached_window = EvWindow(
                start=None, end=None, in_window_now=False, reason="no_need"
            )
            return

        ev_soc = _read_float_optional(hass, data.get(CONF_EV_SOC)) or 0.0
        ev_target_soc = _read_number(hass, "number.ems_ev_target_soc", 80.0)
        ev_capacity_kwh = float(data.get(CONF_EV_CAPACITY_KWH, 60.0))
        ev_max_charge_kw = float(data.get(CONF_EV_MAX_CHARGE_KW, 7.4))

        kwh_needed = max(0.0, (ev_target_soc - ev_soc) / 100.0 * ev_capacity_kwh)

        import datetime as _dt
        ev_ready_by = _read_time_state(hass, "time.ems_ev_ready_by") or _dt.time(7, 0)
        ev_strategy = _read_select_state(hass, "select.ems_ev_scheduling_strategy", "Off")

        # Parse price forecast
        forecast = None
        path = data.get(CONF_FORECAST_PATH_IMPORT)
        price_entity = data.get(CONF_IMPORT_PRICE)
        if path and price_entity:
            state_obj = hass.states.get(price_entity)
            if state_obj:
                raw = _walk_attr(state_obj, path)
                if raw is not None:
                    forecast = parse_forecast(raw)
                    if forecast is None and not self._state.forecast_parse_warned:
                        _LOGGER.warning(
                            "EMS: could not parse price forecast from %s.%s",
                            price_entity,
                            path,
                        )
                        self._state.forecast_parse_warned = True

        pv_w = _read_float(hass, data.get(CONF_PV_POWER), 0.0)
        house_load_w = _read_float(hass, data.get(CONF_HOUSE_LOAD), 0.0)
        battery_soc = _read_float(hass, data.get(CONF_BATTERY_SOC), 50.0)
        solar_surplus_w = max(0.0, pv_w - house_load_w)

        self._state.cached_window = compute_window(
            now=dt_util.utcnow(),
            deadline=ev_ready_by,
            strategy=ev_strategy,  # type: ignore[arg-type]
            kwh_needed=kwh_needed,
            max_charge_kw=ev_max_charge_kw,
            forecast=forecast,
            solar_surplus_w=solar_surplus_w,
            battery_soc=battery_soc,
            previous=self._state.cached_window,
        )

    @callback
    def _on_change(self, event) -> None:
        if self._debounce_handle is not None:
            self._debounce_handle()
        self._debounce_handle = ha_event.async_call_later(
            self._hass, 5, self._debounce_fire
        )

    @callback
    def _debounce_fire(self, _now) -> None:
        self._hass.async_create_task(self._recompute())


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


def _read_number_state(hass: HomeAssistant, entity_id: str, default: float) -> float:
    return _read_float(hass, entity_id, default)


def _read_time_state(hass: HomeAssistant, entity_id: str):
    """Read a time entity and return a datetime.time or None."""
    import datetime as dt_module
    state = hass.states.get(entity_id)
    if state is None or state.state in ("unavailable", "unknown"):
        return None
    try:
        parts = state.state.split(":")
        return dt_module.time(int(parts[0]), int(parts[1]))
    except (ValueError, IndexError):
        return None


def _walk_attr(state_obj, path: str):
    """Walk state.attributes using dot-separated path."""
    parts = path.split(".")
    current = state_obj.attributes
    for part in parts:
        if not isinstance(current, dict):
            return None
        current = current.get(part)
        if current is None:
            return None
    return current


def _parse_stored_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
        return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)
    except (ValueError, TypeError):
        return None


def entry_option(entry: ConfigEntry, key: str, default):
    return entry.options.get(key, entry.data.get(key, default))
