from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.ems.const import (
    CONF_BATTERY_SOC,
    CONF_EV_TARGET_SOC,
    CONF_GRID_IMPORT_POWER,
    CONF_MAX_GRID_IMPORT_W,
    CONF_SOLAR_FORECAST_JSON_ENTITY,
    DEFAULT_EV_TARGET_SOC,
    DEFAULT_MAX_GRID_IMPORT_W,
)
from custom_components.ems.models import (
    EvDecision,
    Inputs,
    InverterDecision,
    LadderResult,
    Thresholds,
    VerbMapping,
)


def _make_entry(data: dict) -> MagicMock:
    entry = MagicMock()
    entry.data = data
    return entry


def _make_hass(states: dict | None = None) -> MagicMock:
    hass = MagicMock()
    states = states or {}

    def get_state(entity_id):
        if entity_id in states:
            s = MagicMock()
            s.state = str(states[entity_id])
            return s
        return None

    hass.states.get = MagicMock(side_effect=get_state)
    return hass


def _make_state(value: str, unit: str | None = None) -> MagicMock:
    """Fake HA state with .state and .attributes.get('unit_of_measurement')."""
    s = MagicMock()
    s.state = value
    s.attributes = {"unit_of_measurement": unit} if unit is not None else {}
    return s


def _make_hass_with_attr(entity_id: str, value: str, unit: str | None = None) -> MagicMock:
    hass = MagicMock()
    entity_state = _make_state(value, unit)

    def get_state(eid):
        return entity_state if eid == entity_id else None

    hass.states.get = MagicMock(side_effect=get_state)
    return hass


# ---------------------------------------------------------------------------
# _read_kwh_optional — unit-aware capacity conversion
# ---------------------------------------------------------------------------

def test_read_kwh_optional_wh_converts_to_kwh():
    from custom_components.ems.coordinator import _read_kwh_optional
    hass = _make_hass_with_attr("input_number.battery_capacity", "5800", "Wh")
    assert _read_kwh_optional(hass, "input_number.battery_capacity") == pytest.approx(5.8)


def test_read_kwh_optional_kwh_passthrough():
    from custom_components.ems.coordinator import _read_kwh_optional
    hass = _make_hass_with_attr("input_number.battery_capacity", "5.8", "kWh")
    assert _read_kwh_optional(hass, "input_number.battery_capacity") == pytest.approx(5.8)


def test_read_kwh_optional_no_unit_assumes_kwh():
    from custom_components.ems.coordinator import _read_kwh_optional
    hass = _make_hass_with_attr("input_number.battery_capacity", "5.8", None)
    assert _read_kwh_optional(hass, "input_number.battery_capacity") == pytest.approx(5.8)


def test_read_kwh_optional_wh_case_insensitive():
    from custom_components.ems.coordinator import _read_kwh_optional
    for unit in ("wh", "WH", "Wh", "wH"):
        hass = _make_hass_with_attr("input_number.battery_capacity", "5800", unit)
        result = _read_kwh_optional(hass, "input_number.battery_capacity")
        assert result == pytest.approx(5.8), f"failed for unit={unit!r}"


def test_read_kwh_optional_unavailable_returns_none():
    from custom_components.ems.coordinator import _read_kwh_optional
    s = MagicMock()
    s.state = "unavailable"
    hass = MagicMock()
    hass.states.get = MagicMock(return_value=s)
    assert _read_kwh_optional(hass, "input_number.battery_capacity") is None


def test_read_kwh_optional_entity_absent_returns_none():
    from custom_components.ems.coordinator import _read_kwh_optional
    hass = MagicMock()
    hass.states.get = MagicMock(return_value=None)
    assert _read_kwh_optional(hass, "input_number.battery_capacity") is None


def test_read_kwh_optional_no_entity_id_returns_none():
    from custom_components.ems.coordinator import _read_kwh_optional
    hass = MagicMock()
    assert _read_kwh_optional(hass, None) is None


# ---------------------------------------------------------------------------
# _read_entity_max_abs — symmetric active-power cap from entity attributes
# ---------------------------------------------------------------------------

def _make_hass_with_range(entity_id: str, min_val: float, max_val: float) -> MagicMock:
    hass = MagicMock()
    s = MagicMock()
    s.state = "0"
    s.attributes = {"min": min_val, "max": max_val}

    def get_state(eid):
        return s if eid == entity_id else None

    hass.states.get = MagicMock(side_effect=get_state)
    return hass


def test_read_entity_max_abs_symmetric_range():
    from custom_components.ems.coordinator import _read_entity_max_abs
    hass = _make_hass_with_range("number.knx_inverter_active_power", -5000.0, 5000.0)
    assert _read_entity_max_abs(hass, "number.knx_inverter_active_power") == pytest.approx(5000.0)


def test_read_entity_max_abs_asymmetric_returns_tighter_bound():
    from custom_components.ems.coordinator import _read_entity_max_abs
    hass = _make_hass_with_range("number.knx_inverter_active_power", -3000.0, 5000.0)
    # min(|−3000|, |5000|) = 3000 — use tighter bound so neither side exceeds it
    assert _read_entity_max_abs(hass, "number.knx_inverter_active_power") == pytest.approx(3000.0)


def test_read_entity_max_abs_entity_absent_returns_none():
    from custom_components.ems.coordinator import _read_entity_max_abs
    hass = MagicMock()
    hass.states.get = MagicMock(return_value=None)
    assert _read_entity_max_abs(hass, "number.knx_inverter_active_power") is None


def test_read_entity_max_abs_no_entity_id_returns_none():
    from custom_components.ems.coordinator import _read_entity_max_abs
    hass = MagicMock()
    assert _read_entity_max_abs(hass, None) is None


def test_read_entity_max_abs_missing_attrs_returns_none():
    from custom_components.ems.coordinator import _read_entity_max_abs
    hass = MagicMock()
    s = MagicMock()
    s.state = "0"
    s.attributes = {}  # no min/max
    hass.states.get = MagicMock(return_value=s)
    assert _read_entity_max_abs(hass, "number.knx_inverter_active_power") is None


# ---------------------------------------------------------------------------
# _build_inputs reads max_active_power_w from active_power_entity range
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_build_inputs_reads_max_active_power_from_entity_range():
    """_build_inputs populates thresholds.max_active_power_w from the entity min/max attrs."""
    from custom_components.ems.coordinator import EmsStrategyCoordinator

    entry = _make_entry({
        "grid_import_power": "sensor.grid",
        "pv_power": "sensor.pv",
        "house_load": "sensor.load",
        "ev_power": "sensor.ev",
        "active_power_entity": "number.inverter_active_power",
    })

    inv_state = MagicMock()
    inv_state.state = "0"
    inv_state.attributes = {"min": -5000.0, "max": 5000.0}

    def _get_state(eid):
        if eid == "number.inverter_active_power":
            return inv_state
        base = {
            "sensor.grid": "0", "sensor.pv": "0", "sensor.load": "0", "sensor.ev": "0",
            "number.ems_ev_min_charge_w": "1400",
            "select.ems_ev_charging_mode": "Off",
            "select.ems_inverter_control_mode": "Off",
        }
        if eid in base:
            s = MagicMock()
            s.state = base[eid]
            return s
        return None

    hass = MagicMock()
    hass.states.get = MagicMock(side_effect=_get_state)

    state = MagicMock()
    state.startup_grace_until = None

    coordinator = EmsStrategyCoordinator.__new__(EmsStrategyCoordinator)
    coordinator.hass = hass
    coordinator._entry = entry
    coordinator._state = state

    with patch("custom_components.ems.coordinator.dt_util"):
        inputs = await coordinator._build_inputs()

    assert inputs.thresholds.max_active_power_w == pytest.approx(5000.0)


# ---------------------------------------------------------------------------
# _build_inputs reads max_grid_import_w / ev_target_soc from entities
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_build_inputs_reads_max_grid_import_from_entity():
    from custom_components.ems.coordinator import EmsStrategyCoordinator

    entry = _make_entry({
        "grid_import_power": "sensor.grid",
        "pv_power": "sensor.pv",
        "house_load": "sensor.load",
        "ev_power": "sensor.ev",
        CONF_MAX_GRID_IMPORT_W: "input_number.max_grid",
        CONF_EV_TARGET_SOC: "input_number.ev_target_soc",
    })

    hass = _make_hass({
        "sensor.grid": 500,
        "sensor.pv": 3000,
        "sensor.load": 1000,
        "sensor.ev": 0,
        "input_number.max_grid": 6000,
        "input_number.ev_target_soc": 75,
        "number.ems_ev_min_charge_w": 1400,
        "select.ems_ev_charging_mode": "Off",
        "select.ems_inverter_control_mode": "Off",
    })

    state = MagicMock()
    state.startup_grace_until = None

    coordinator = EmsStrategyCoordinator.__new__(EmsStrategyCoordinator)
    coordinator.hass = hass
    coordinator._entry = entry
    coordinator._state = state

    with patch("custom_components.ems.coordinator.dt_util"):
        inputs = await coordinator._build_inputs()

    assert inputs.thresholds.max_grid_import_w == 6000.0
    assert inputs.thresholds.ev_target_soc == 75.0


@pytest.mark.asyncio
async def test_build_inputs_uses_defaults_when_keys_absent():
    from custom_components.ems.coordinator import EmsStrategyCoordinator

    entry = _make_entry({
        "grid_import_power": "sensor.grid",
        "pv_power": "sensor.pv",
        "house_load": "sensor.load",
        "ev_power": "sensor.ev",
    })

    hass = _make_hass({
        "sensor.grid": 0, "sensor.pv": 0, "sensor.load": 0, "sensor.ev": 0,
        "number.ems_ev_min_charge_w": 1400,
        "select.ems_ev_charging_mode": "Off",
        "select.ems_inverter_control_mode": "Off",
    })

    state = MagicMock()
    state.startup_grace_until = None

    coordinator = EmsStrategyCoordinator.__new__(EmsStrategyCoordinator)
    coordinator.hass = hass
    coordinator._entry = entry
    coordinator._state = state

    with patch("custom_components.ems.coordinator.dt_util"):
        inputs = await coordinator._build_inputs()

    assert inputs.thresholds.max_grid_import_w == DEFAULT_MAX_GRID_IMPORT_W
    assert inputs.thresholds.ev_target_soc == DEFAULT_EV_TARGET_SOC


@pytest.mark.asyncio
async def test_build_inputs_uses_defaults_when_entity_unavailable():
    from custom_components.ems.coordinator import EmsStrategyCoordinator

    entry = _make_entry({
        "grid_import_power": "sensor.grid",
        "pv_power": "sensor.pv",
        "house_load": "sensor.load",
        "ev_power": "sensor.ev",
        CONF_MAX_GRID_IMPORT_W: "input_number.max_grid",
        CONF_EV_TARGET_SOC: "input_number.ev_target_soc",
    })

    def _unavailable_state(entity_id):
        base_map = {
            "sensor.grid": "0", "sensor.pv": "0", "sensor.load": "0", "sensor.ev": "0",
            "number.ems_ev_min_charge_w": "1400",
            "select.ems_ev_charging_mode": "Off",
            "select.ems_inverter_control_mode": "Off",
        }
        if entity_id in base_map:
            s = MagicMock()
            s.state = base_map[entity_id]
            return s
        if entity_id in ("input_number.max_grid", "input_number.ev_target_soc"):
            s = MagicMock()
            s.state = "unavailable"
            return s
        return None

    hass = MagicMock()
    hass.states.get = MagicMock(side_effect=_unavailable_state)

    state = MagicMock()
    state.startup_grace_until = None

    coordinator = EmsStrategyCoordinator.__new__(EmsStrategyCoordinator)
    coordinator.hass = hass
    coordinator._entry = entry
    coordinator._state = state

    with patch("custom_components.ems.coordinator.dt_util"):
        inputs = await coordinator._build_inputs()

    assert inputs.thresholds.max_grid_import_w == DEFAULT_MAX_GRID_IMPORT_W
    assert inputs.thresholds.ev_target_soc == DEFAULT_EV_TARGET_SOC


@pytest.mark.asyncio
async def test_build_inputs_ev_min_charge_w_from_number_entity():
    from custom_components.ems.coordinator import EmsStrategyCoordinator

    entry = _make_entry({
        "grid_import_power": "sensor.grid",
        "pv_power": "sensor.pv",
        "house_load": "sensor.load",
        "ev_power": "sensor.ev",
    })

    hass = _make_hass({
        "sensor.grid": 0, "sensor.pv": 0, "sensor.load": 0, "sensor.ev": 0,
        "number.ems_ev_min_charge_w": 2000,
        "select.ems_ev_charging_mode": "Off",
        "select.ems_inverter_control_mode": "Off",
    })

    state = MagicMock()
    state.startup_grace_until = None

    coordinator = EmsStrategyCoordinator.__new__(EmsStrategyCoordinator)
    coordinator.hass = hass
    coordinator._entry = entry
    coordinator._state = state

    with patch("custom_components.ems.coordinator.dt_util"):
        inputs = await coordinator._build_inputs()

    assert inputs.thresholds.ev_min_charge_w == 2000.0


@pytest.mark.asyncio
async def test_build_inputs_reads_grid_import():
    """grid_import_w is passed through from the sensor entity."""
    from custom_components.ems.coordinator import EmsStrategyCoordinator

    entry = _make_entry({
        "grid_import_power": "sensor.grid",
        "pv_power": "sensor.pv",
        "house_load": "sensor.load",
        "ev_power": "sensor.ev",
    })

    hass = _make_hass({
        "sensor.grid": 1234,
        "sensor.pv": 500,
        "sensor.load": 800,
        "sensor.ev": 0,
        "number.ems_ev_min_charge_w": 1400,
        "select.ems_ev_charging_mode": "Off",
        "select.ems_inverter_control_mode": "Off",
    })

    state = MagicMock()
    state.startup_grace_until = None

    coordinator = EmsStrategyCoordinator.__new__(EmsStrategyCoordinator)
    coordinator.hass = hass
    coordinator._entry = entry
    coordinator._state = state

    with patch("custom_components.ems.coordinator.dt_util"):
        inputs = await coordinator._build_inputs()

    assert inputs.grid_import_w == 1234.0


@pytest.mark.asyncio
async def test_build_inputs_mapping_from_inverter_select():
    """VerbMapping is built from inverter_select, option strings, and active_power_entity."""
    from custom_components.ems.coordinator import EmsStrategyCoordinator

    entry = _make_entry({
        "grid_import_power": "sensor.grid",
        "pv_power": "sensor.pv",
        "house_load": "sensor.load",
        "ev_power": "sensor.ev",
        "inverter_select": "select.inv",
        "inverter_disabled_option": "disabled",
        "inverter_power_control_option": "power_control",
        "active_power_entity": "number.bat_cap",
    })

    hass = _make_hass({
        "sensor.grid": 0, "sensor.pv": 0, "sensor.load": 0, "sensor.ev": 0,
        "number.ems_ev_min_charge_w": 1400,
        "select.ems_ev_charging_mode": "Off",
        "select.ems_inverter_control_mode": "Off",
    })

    state = MagicMock()
    state.startup_grace_until = None

    coordinator = EmsStrategyCoordinator.__new__(EmsStrategyCoordinator)
    coordinator.hass = hass
    coordinator._entry = entry
    coordinator._state = state

    with patch("custom_components.ems.coordinator.dt_util"):
        inputs = await coordinator._build_inputs()

    assert inputs.mapping.inverter_select_entity == "select.inv"
    assert inputs.mapping.disabled_option == "disabled"
    assert inputs.mapping.power_control_option == "power_control"
    assert inputs.mapping.active_power_entity == "number.bat_cap"


@pytest.mark.asyncio
async def test_build_inputs_reads_inverter_strategy():
    """inverter_strategy is read from select.ems_inverter_control_mode."""
    from custom_components.ems.coordinator import EmsStrategyCoordinator

    entry = _make_entry({
        "grid_import_power": "sensor.grid",
        "pv_power": "sensor.pv",
        "house_load": "sensor.load",
        "ev_power": "sensor.ev",
    })

    hass = _make_hass({
        "sensor.grid": 0, "sensor.pv": 0, "sensor.load": 0, "sensor.ev": 0,
        "number.ems_ev_min_charge_w": 1400,
        "select.ems_ev_charging_mode": "Off",
        "select.ems_inverter_control_mode": "Control Inverter",
    })

    state = MagicMock()
    state.startup_grace_until = None

    coordinator = EmsStrategyCoordinator.__new__(EmsStrategyCoordinator)
    coordinator.hass = hass
    coordinator._entry = entry
    coordinator._state = state

    with patch("custom_components.ems.coordinator.dt_util"):
        inputs = await coordinator._build_inputs()

    assert inputs.inverter_strategy == "Control Inverter"


# ---------------------------------------------------------------------------
# EmsFastLoopCoordinator EMA smoothing
# ---------------------------------------------------------------------------

def _make_ladder_result(ev_power_w: float = 0.0) -> LadderResult:
    now = datetime(2024, 1, 1, tzinfo=UTC)
    thresholds = Thresholds(max_grid_import_w=6000, ev_target_soc=80, ev_min_charge_w=1400)
    mapping = VerbMapping(
        inverter_select_entity=None,
        disabled_option=None,
        power_control_option=None,
        active_power_entity=None,
        ev_charge_target_entity=None,
    )
    inputs = Inputs(
        now=now,
        grid_import_w=0.0,
        pv_w=0.0,
        house_load_w=0.0,
        ev_plugged=True,
        ev_power_w=ev_power_w,
        ev_soc=None,
        ev_strategy="Grid-optimised",
        is_cheap_hour=True,
        inverter_strategy="Control Inverter",
        thresholds=thresholds,
        mapping=mapping,
    )
    ev_decision = EvDecision(
        strategy="ev_idle",
        ev_charge_power_w=0.0,
        charging_state="ev_not_connected",
    )
    inverter_decision = InverterDecision(inverter_verb=None, active_power_w=None)
    return LadderResult(
        ev_decision=ev_decision, inverter_decision=inverter_decision, inputs=inputs, computed_at=now
    )


def _make_fast_coordinator(hass_states: dict | None = None):
    from custom_components.ems.coordinator import EmsFastLoopCoordinator

    hass = MagicMock()
    hass_states = hass_states or {}

    def get_state(entity_id):
        if entity_id in hass_states:
            s = MagicMock()
            s.state = str(hass_states[entity_id])
            return s
        return None

    hass.states.get = MagicMock(side_effect=get_state)

    entry = MagicMock()
    entry.data = {
        "pv_power": "sensor.pv",
        "house_load": "sensor.load",
        "ev_power": "sensor.ev",
        "grid_import_power": "sensor.grid",
    }

    state = MagicMock()
    state.startup_grace_until = None
    state.ev_below_min_since = None

    controller = MagicMock()
    controller.write_fast = AsyncMock()

    coord = EmsFastLoopCoordinator.__new__(EmsFastLoopCoordinator)
    coord.hass = hass
    coord._entry = entry
    coord._state = state
    coord._controller = controller
    coord._ema_ev_w = None
    coord._ema_active_w = None
    coord._prev_ev_power_w = 0.0
    return coord


_RECOMPUTE_EV = "custom_components.ems.coordinator.strategy_module.recompute"
_RECOMPUTE_INV = "custom_components.ems.coordinator.inverter_control_module.recompute"
_DT_UTIL = "custom_components.ems.coordinator.dt_util"
_NOW = datetime(2024, 1, 1, tzinfo=UTC)

# States where EV is charging and inverter control is active
_EV_STATES = {
    "sensor.pv": 0,
    "sensor.load": 0,
    "sensor.ev": 100,
    "sensor.grid": 0,
    "select.ems_ev_charging_mode": "Grid-optimised",
    "select.ems_inverter_control_mode": "Control Inverter",
}

_EV_DECISION_CHARGING = EvDecision(
    strategy="ev_charging",
    ev_charge_power_w=2000.0,
    charging_state="charging",
)
_INV_DECISION_ACTIVE = InverterDecision(inverter_verb="power_control", active_power_w=500.0)


def _ev_charging(ev_charge_power_w: float = 2000.0) -> EvDecision:
    return EvDecision(strategy="ev_charging", ev_charge_power_w=ev_charge_power_w, charging_state="charging")  # noqa: E501


@pytest.mark.asyncio
async def test_fast_loop_ema_first_tick_uses_raw_value():
    """First tick initialises EMA to the raw recompute output (no blending)."""
    coord = _make_fast_coordinator(_EV_STATES)
    coord._state.last_result = _make_ladder_result()

    with patch(_RECOMPUTE_EV, return_value=_EV_DECISION_CHARGING), \
         patch(_RECOMPUTE_INV, return_value=_INV_DECISION_ACTIVE), \
         patch(_DT_UTIL) as mock_dt:
        mock_dt.now.return_value = _NOW
        await coord._async_update_data()

    written_ev: EvDecision = coord._controller.write_fast.call_args[0][0]
    written_inv: InverterDecision = coord._controller.write_fast.call_args[0][1]
    assert written_ev.ev_charge_power_w == pytest.approx(2000.0)
    assert written_inv.active_power_w == pytest.approx(500.0)


@pytest.mark.asyncio
async def test_fast_loop_passes_slow_loop_surplus_override_to_recompute():
    """The fast loop must reuse EmsState.ev_energy_surplus_w (slow-loop-sampled)
    instead of recomputing the skew-prone house_load - ev_power subtraction. Assert the
    value is threaded into strategy.recompute as energy_surplus_override_w (flap fix)."""
    coord = _make_fast_coordinator(_EV_STATES)
    coord._state.last_result = _make_ladder_result()
    coord._state.ev_below_min_since = None
    coord._state.ev_grid_overload_since = None
    coord._state.ev_energy_surplus_w = 1234.0

    with patch(_RECOMPUTE_EV, return_value=_EV_DECISION_CHARGING) as mock_rc, \
         patch(_RECOMPUTE_INV, return_value=_INV_DECISION_ACTIVE), \
         patch(_DT_UTIL) as mock_dt:
        mock_dt.now.return_value = _NOW
        await coord._async_update_data()

    assert mock_rc.call_args.kwargs["energy_surplus_override_w"] == pytest.approx(1234.0)


@pytest.mark.asyncio
async def test_fast_loop_ema_second_tick_blends():
    """Second tick blends 30% new + 70% old."""
    second_ev = _ev_charging(3000.0)
    second_inv = InverterDecision(inverter_verb="power_control", active_power_w=1000.0)

    coord = _make_fast_coordinator(_EV_STATES)
    coord._state.last_result = _make_ladder_result()

    with patch(_DT_UTIL) as mock_dt:
        mock_dt.now.return_value = _NOW
        with patch(_RECOMPUTE_EV, return_value=_EV_DECISION_CHARGING), \
             patch(_RECOMPUTE_INV, return_value=_INV_DECISION_ACTIVE):
            await coord._async_update_data()
        with patch(_RECOMPUTE_EV, return_value=second_ev), \
             patch(_RECOMPUTE_INV, return_value=second_inv):
            await coord._async_update_data()

    written_ev: EvDecision = coord._controller.write_fast.call_args[0][0]
    written_inv: InverterDecision = coord._controller.write_fast.call_args[0][1]
    assert written_ev.ev_charge_power_w == pytest.approx(0.3 * 3000.0 + 0.7 * 2000.0)
    assert written_inv.active_power_w == pytest.approx(0.3 * 1000.0 + 0.7 * 500.0)


@pytest.mark.asyncio
async def test_fast_loop_ema_resets_on_session_start():
    """After ev_power_w drops to 0, the next non-zero ev_power_w resets EMA to raw target."""
    new_session_ev = _ev_charging(3500.0)
    new_session_inv = InverterDecision(inverter_verb="power_control", active_power_w=800.0)

    coord = _make_fast_coordinator(_EV_STATES)
    coord._state.last_result = _make_ladder_result()

    with patch(_DT_UTIL) as mock_dt:
        mock_dt.now.return_value = _NOW

        # Tick 1: ev charging — EMA initialised to raw values
        with patch(_RECOMPUTE_EV, return_value=_EV_DECISION_CHARGING), \
             patch(_RECOMPUTE_INV, return_value=_INV_DECISION_ACTIVE):
            await coord._async_update_data()

        # Tick 2: ev_power_w drops to 0 (charger stopped)
        coord.hass.states.get = MagicMock(side_effect=lambda _: _mock_state("0"))
        _inv_disabled = InverterDecision(inverter_verb="disabled", active_power_w=None)
        with patch(_RECOMPUTE_EV, return_value=_EV_DECISION_CHARGING), \
             patch(_RECOMPUTE_INV, return_value=_inv_disabled):
            await coord._async_update_data()

        # Tick 3: new charging session (ev_power_w > 0) — EMA must reset immediately
        coord.hass.states.get = MagicMock(
            side_effect=lambda eid: _mock_state("150") if "ev" in eid else _mock_state("0")
        )
        with patch(_RECOMPUTE_EV, return_value=new_session_ev), \
             patch(_RECOMPUTE_INV, return_value=new_session_inv):
            await coord._async_update_data()

    written_ev: EvDecision = coord._controller.write_fast.call_args[0][0]
    written_inv: InverterDecision = coord._controller.write_fast.call_args[0][1]
    assert written_ev.ev_charge_power_w == pytest.approx(3500.0)
    assert written_inv.active_power_w == pytest.approx(800.0)


def _mock_state(value: str) -> MagicMock:
    s = MagicMock()
    s.state = value
    return s


@pytest.mark.asyncio
async def test_fast_loop_ema_slow_mode_uses_lower_alpha():
    """Slow smoothing (α=0.1) blends less aggressively than Fast (α=0.3)."""
    first_ev = _ev_charging(1000.0)
    second_ev = _ev_charging(2000.0)
    inv = InverterDecision(inverter_verb="power_control", active_power_w=0.0)

    def states_with_smoothing(smoothing_value: str):
        def get(eid):
            if "smoothing" in eid:
                return _mock_state(smoothing_value)
            if "ev" in eid:
                return _mock_state("100")
            if "inverter_control" in eid:
                return _mock_state("Control Inverter")
            if "charging_mode" in eid:
                return _mock_state("Grid-optimised")
            return _mock_state("0")
        return MagicMock(side_effect=get)

    coord = _make_fast_coordinator(_EV_STATES)
    coord._state.last_result = _make_ladder_result()
    coord.hass.states.get = states_with_smoothing("Slow")

    with patch(_DT_UTIL) as mock_dt:
        mock_dt.now.return_value = _NOW
        with patch(_RECOMPUTE_EV, return_value=first_ev), patch(_RECOMPUTE_INV, return_value=inv):
            await coord._async_update_data()
        with patch(_RECOMPUTE_EV, return_value=second_ev), patch(_RECOMPUTE_INV, return_value=inv):
            await coord._async_update_data()

    written_ev: EvDecision = coord._controller.write_fast.call_args[0][0]
    assert written_ev.ev_charge_power_w == pytest.approx(0.1 * 2000.0 + 0.9 * 1000.0)


@pytest.mark.asyncio
async def test_fast_loop_off_mode_bypasses_ema_and_resets_state():
    """When ev mode is Off, written ev target is 0 and EMA state is cleared."""
    off_ev = EvDecision(
        strategy="ev_idle", ev_charge_power_w=0.0, charging_state="charging_disabled"
    )
    off_inv = InverterDecision(inverter_verb=None, active_power_w=None)

    off_states = {
        **_EV_STATES,
        "select.ems_ev_charging_mode": "Off",
    }

    coord = _make_fast_coordinator(off_states)
    coord._state.last_result = _make_ladder_result()
    coord._ema_ev_w = 500.0
    coord._ema_active_w = 200.0

    with patch(_RECOMPUTE_EV, return_value=off_ev), \
         patch(_RECOMPUTE_INV, return_value=off_inv), \
         patch(_DT_UTIL) as mock_dt:
        mock_dt.now.return_value = _NOW
        await coord._async_update_data()

    written_ev: EvDecision = coord._controller.write_fast.call_args[0][0]
    assert written_ev.ev_charge_power_w == pytest.approx(0.0)
    assert coord._ema_ev_w is None
    assert coord._ema_active_w is None


@pytest.mark.asyncio
async def test_fast_loop_idle_strategy_snaps_ema_to_zero():
    """Regression: EV-target EMA decaying toward 0 underflows to a denormalized float
    (~2.47e-323) rather than exactly 0.0, causing commanded_ev_charge_w == 0.0 to fail
    in inverter_control._compute and keeping the inverter stuck in 'controlling'.
    When strategy returns any idle decision, the EMA must snap to exactly 0.0 so the
    idle branch fires and active_power is not written.
    """
    idle_ev = EvDecision(
        strategy="ev_idle", ev_charge_power_w=0.0, charging_state="ev_not_connected"
    )
    idle_inv = InverterDecision(inverter_verb="disabled", active_power_w=None, status="idle")

    coord = _make_fast_coordinator(_EV_STATES)
    coord._state.last_result = _make_ladder_result()
    coord._ema_ev_w = 2.47e-323  # simulates denormalized residual after long decay

    with patch(_RECOMPUTE_EV, return_value=idle_ev), \
         patch(_RECOMPUTE_INV, return_value=idle_inv), \
         patch(_DT_UTIL) as mock_dt:
        mock_dt.now.return_value = _NOW
        await coord._async_update_data()

    assert coord._ema_ev_w == 0.0
    written_ev: EvDecision = coord._controller.write_fast.call_args[0][0]
    written_inv: InverterDecision = coord._controller.write_fast.call_args[0][1]
    assert written_ev.ev_charge_power_w == 0.0
    assert written_inv.active_power_w is None


@pytest.mark.asyncio
async def test_fast_loop_passes_raw_grid_to_recompute():
    """The fast loop feeds the integrator the raw instantaneous grid (no avg kwarg)."""
    from custom_components.ems.coordinator import EmsState

    coord = _make_fast_coordinator()
    coord.hass.states.get = MagicMock(
        side_effect=lambda eid: _mock_state("6000") if "grid" in eid else _mock_state("100")
    )
    coord._state = EmsState(last_result=_make_ladder_result())

    captured: dict = {}

    def fake_recompute(**kw):
        captured.update(kw)
        return _ev_charging(2000.0)

    with patch(_DT_UTIL) as mock_dt, \
         patch(_RECOMPUTE_EV, side_effect=fake_recompute), \
         patch(_RECOMPUTE_INV, return_value=_INV_DECISION_ACTIVE):
        mock_dt.now.return_value = _NOW
        await coord._async_update_data()

    assert captured["grid_import_w"] == pytest.approx(6000.0)
    assert "grid_import_avg_w" not in captured


@pytest.mark.asyncio
async def test_fast_loop_stop_debounce_holds_then_idles():
    """A charging session dipping below min is held at min for the debounce window,
    then idles once the dip is sustained past _EV_STOP_DEBOUNCE_S."""
    from custom_components.ems.strategy import _EV_STOP_DEBOUNCE_S

    not_enough = EvDecision(
        strategy="ev_idle", ev_charge_power_w=0.0, charging_state="not_enough_power"
    )

    coord = _make_fast_coordinator(_EV_STATES)
    coord._state.last_result = _make_ladder_result()
    coord._ema_ev_w = 2000.0   # session was charging
    coord._prev_ev_power_w = 100.0  # not a session start

    t_expire = _NOW + timedelta(seconds=_EV_STOP_DEBOUNCE_S + 1)

    with patch(_RECOMPUTE_EV, return_value=not_enough), \
         patch(_RECOMPUTE_INV, return_value=_INV_DECISION_ACTIVE), \
         patch(_DT_UTIL) as mock_dt:
        mock_dt.now.return_value = _NOW
        await coord._async_update_data()   # tick 1: dip begins → held at min
        held: EvDecision = coord._controller.write_fast.call_args[0][0]
        assert held.charging_state == "holding_through_dip"
        assert held.ev_charge_power_w == pytest.approx(1400.0)
        assert coord._state.ev_below_min_since == _NOW

        mock_dt.now.return_value = t_expire
        await coord._async_update_data()   # tick 2: dwell expired → idle
        stopped: EvDecision = coord._controller.write_fast.call_args[0][0]
        assert stopped.ev_charge_power_w == 0.0


@pytest.mark.asyncio
async def test_fast_loop_energy_optimised_battery_drain_holds_past_60s():
    """Energy-optimised holds a sub-min dip at min past the generic 60 s window,
    stopping only after the 120 s battery-drain window elapses."""
    not_enough = EvDecision(
        strategy="ev_idle", ev_charge_power_w=0.0, charging_state="not_enough_power"
    )
    states = {**_EV_STATES, "select.ems_ev_charging_mode": "Energy-optimised"}
    coord = _make_fast_coordinator(states)
    coord._state.last_result = _make_ladder_result()
    coord._state.ev_grid_overload_since = None
    coord._ema_ev_w = 2000.0
    coord._prev_ev_power_w = 100.0

    with patch(_RECOMPUTE_EV, return_value=not_enough), \
         patch(_RECOMPUTE_INV, return_value=_INV_DECISION_ACTIVE), \
         patch(_DT_UTIL) as mock_dt:
        mock_dt.now.return_value = _NOW
        await coord._async_update_data()   # dip begins → held at min
        held: EvDecision = coord._controller.write_fast.call_args[0][0]
        assert held.charging_state == "holding_through_dip"
        assert coord._state.ev_below_min_since == _NOW

        # 61 s: the generic 60 s debounce would have stopped; 120 s still holds.
        mock_dt.now.return_value = _NOW + timedelta(seconds=61)
        await coord._async_update_data()
        still: EvDecision = coord._controller.write_fast.call_args[0][0]
        assert still.charging_state == "holding_through_dip"

        # 120 s: sustained shortfall finally stops the session.
        mock_dt.now.return_value = _NOW + timedelta(seconds=120)
        await coord._async_update_data()
        stopped: EvDecision = coord._controller.write_fast.call_args[0][0]
        assert stopped.ev_charge_power_w == 0.0


@pytest.mark.asyncio
async def test_fast_loop_energy_optimised_grid_overload_stops_after_30s():
    """Energy-optimised: sustained grid import > 100 W stops the session after 30 s."""
    states = {
        **_EV_STATES, "select.ems_ev_charging_mode": "Energy-optimised", "sensor.grid": 500,
    }
    coord = _make_fast_coordinator(states)
    coord._state.last_result = _make_ladder_result()
    coord._state.ev_grid_overload_since = None
    coord._ema_ev_w = 2000.0
    coord._prev_ev_power_w = 100.0

    with patch(_RECOMPUTE_EV, return_value=_ev_charging(2000.0)), \
         patch(_RECOMPUTE_INV, return_value=_INV_DECISION_ACTIVE), \
         patch(_DT_UTIL) as mock_dt:
        mock_dt.now.return_value = _NOW
        await coord._async_update_data()   # overload begins → still charging
        first: EvDecision = coord._controller.write_fast.call_args[0][0]
        assert first.strategy == "ev_charging"
        assert coord._state.ev_grid_overload_since == _NOW

        mock_dt.now.return_value = _NOW + timedelta(seconds=30)
        await coord._async_update_data()   # 30 s sustained → stop
        stopped: EvDecision = coord._controller.write_fast.call_args[0][0]
        assert stopped.charging_state == "grid_overloaded"
        assert stopped.ev_charge_power_w == 0.0


@pytest.mark.asyncio
async def test_fast_loop_solar_only_holds_120s_and_ignores_grid_import():
    """Solar-only: 120 s battery-drain hold, and grid import never stops the session."""
    not_enough = EvDecision(
        strategy="ev_idle", ev_charge_power_w=0.0, charging_state="not_enough_power"
    )
    states = {
        **_EV_STATES, "select.ems_ev_charging_mode": "Solar-only", "sensor.grid": 3000,
    }
    coord = _make_fast_coordinator(states)
    coord._state.last_result = _make_ladder_result()
    coord._state.ev_grid_overload_since = _NOW  # stale value from a prior Energy session
    coord._ema_ev_w = 2000.0
    coord._prev_ev_power_w = 100.0

    with patch(_RECOMPUTE_EV, return_value=_ev_charging(2000.0)), \
         patch(_RECOMPUTE_INV, return_value=_INV_DECISION_ACTIVE), \
         patch(_DT_UTIL) as mock_dt:
        mock_dt.now.return_value = _NOW + timedelta(seconds=60)
        await coord._async_update_data()   # heavy grid import → still charging
        charging: EvDecision = coord._controller.write_fast.call_args[0][0]
        assert charging.strategy == "ev_charging"
        assert coord._state.ev_grid_overload_since is None

    with patch(_RECOMPUTE_EV, return_value=not_enough), \
         patch(_RECOMPUTE_INV, return_value=_INV_DECISION_ACTIVE), \
         patch(_DT_UTIL) as mock_dt:
        mock_dt.now.return_value = _NOW
        await coord._async_update_data()   # dip begins → held at min
        assert coord._controller.write_fast.call_args[0][0].charging_state == (
            "holding_through_dip"
        )
        mock_dt.now.return_value = _NOW + timedelta(seconds=61)
        await coord._async_update_data()
        assert coord._controller.write_fast.call_args[0][0].charging_state == (
            "holding_through_dip"
        )
        mock_dt.now.return_value = _NOW + timedelta(seconds=120)
        await coord._async_update_data()
        assert coord._controller.write_fast.call_args[0][0].ev_charge_power_w == 0.0


@pytest.mark.asyncio
async def test_strategy_coordinator_solar_only_uses_solar_guards():
    """Slow loop routes Solar-only through apply_solar_stop_guards and clears the
    grid-overload timer."""
    from dataclasses import replace as dc_replace

    from custom_components.ems.coordinator import EmsStrategyCoordinator

    entry = _make_entry({
        "grid_import_power": "sensor.grid",
        "pv_power": "sensor.pv",
        "house_load": "sensor.load",
        "ev_power": "sensor.ev",
    })
    hass = _make_hass({
        "sensor.grid": 3000, "sensor.pv": 500, "sensor.load": 3500, "sensor.ev": 1400,
        "number.ems_ev_min_charge_w": 1400,
        "select.ems_ev_charging_mode": "Solar-only",
        "select.ems_inverter_control_mode": "Control Inverter",
    })

    not_enough = EvDecision(
        strategy="ev_idle", ev_charge_power_w=0.0, charging_state="not_enough_power"
    )
    charging = EvDecision(
        strategy="ev_charging", ev_charge_power_w=1400.0, charging_state="charging"
    )
    prior = dc_replace(_make_ladder_result(), ev_decision=charging)  # was_charging=True

    state = MagicMock()
    state.startup_grace_until = None
    state.last_result = prior
    state.ev_below_min_since = None
    state.ev_grid_overload_since = datetime(2024, 1, 1, tzinfo=UTC)

    controller = MagicMock()
    controller.write_slow = AsyncMock()

    coord = EmsStrategyCoordinator.__new__(EmsStrategyCoordinator)
    coord.hass = hass
    coord._entry = entry
    coord._state = state
    coord._controller = controller

    t0 = datetime(2024, 1, 1, tzinfo=UTC)
    with patch(
             "custom_components.ems.coordinator.strategy_module.decide",
             return_value=not_enough,
         ), \
         patch(
             "custom_components.ems.coordinator.inverter_control_module.decide",
             return_value=InverterDecision(inverter_verb=None, active_power_w=None),
         ), \
         patch("custom_components.ems.coordinator.dt_util") as mock_dt:
        mock_dt.now.return_value = t0
        res = await coord._async_update_data()

    assert res.ev_decision.charging_state == "holding_through_dip"
    assert state.ev_grid_overload_since is None
    assert state.ev_energy_surplus_w is None


@pytest.mark.asyncio
async def test_fast_loop_non_energy_mode_clears_grid_overload_timer():
    """Non-Energy modes never engage the grid-overload guard; the timer stays cleared
    even with grid import that would trip it under Energy-optimised."""
    states = {**_EV_STATES, "sensor.grid": 500}  # _EV_STATES is Grid-optimised
    coord = _make_fast_coordinator(states)
    coord._state.last_result = _make_ladder_result()
    coord._state.ev_grid_overload_since = _NOW  # stale value from a prior Energy session
    coord._ema_ev_w = 2000.0
    coord._prev_ev_power_w = 100.0

    with patch(_RECOMPUTE_EV, return_value=_ev_charging(2000.0)), \
         patch(_RECOMPUTE_INV, return_value=_INV_DECISION_ACTIVE), \
         patch(_DT_UTIL) as mock_dt:
        mock_dt.now.return_value = _NOW
        await coord._async_update_data()

    assert coord._state.ev_grid_overload_since is None


@pytest.mark.asyncio
async def test_strategy_coordinator_energy_optimised_grid_overload_stop():
    """Slow loop routes Energy-optimised through the grid-overload guard and threads
    ev_grid_overload_since, stopping the session after 30 s of grid import."""
    from dataclasses import replace as dc_replace

    from custom_components.ems.coordinator import EmsStrategyCoordinator

    entry = _make_entry({
        "grid_import_power": "sensor.grid",
        "pv_power": "sensor.pv",
        "house_load": "sensor.load",
        "ev_power": "sensor.ev",
    })
    hass = _make_hass({
        "sensor.grid": 500, "sensor.pv": 0, "sensor.load": 2000, "sensor.ev": 1400,
        "number.ems_ev_min_charge_w": 1400,
        "select.ems_ev_charging_mode": "Energy-optimised",
        "select.ems_inverter_control_mode": "Control Inverter",
    })

    charging = EvDecision(
        strategy="ev_charging", ev_charge_power_w=1400.0, charging_state="charging"
    )
    prior = dc_replace(_make_ladder_result(), ev_decision=charging)  # was_charging=True

    state = MagicMock()
    state.startup_grace_until = None
    state.last_result = prior
    state.ev_below_min_since = None
    state.ev_grid_overload_since = None

    controller = MagicMock()
    controller.write_slow = AsyncMock()

    coord = EmsStrategyCoordinator.__new__(EmsStrategyCoordinator)
    coord.hass = hass
    coord._entry = entry
    coord._state = state
    coord._controller = controller

    t0 = datetime(2024, 1, 1, tzinfo=UTC)
    with patch("custom_components.ems.coordinator.strategy_module.decide", return_value=charging), \
         patch(
             "custom_components.ems.coordinator.inverter_control_module.decide",
             return_value=InverterDecision(inverter_verb=None, active_power_w=None),
         ), \
         patch("custom_components.ems.coordinator.dt_util") as mock_dt:
        mock_dt.now.return_value = t0
        res1 = await coord._async_update_data()   # overload begins → still charging
        assert res1.ev_decision.strategy == "ev_charging"
        assert state.ev_grid_overload_since == t0

        mock_dt.now.return_value = t0 + timedelta(seconds=30)
        res2 = await coord._async_update_data()   # 30 s sustained → stop
        assert res2.ev_decision.charging_state == "grid_overloaded"


@pytest.mark.asyncio
async def test_fast_loop_inverter_off_mode_no_active_power_ema():
    """When inverter_strategy=Off, active_power EMA state is cleared and active_power is None."""
    ev = EvDecision(strategy="ev_charging", ev_charge_power_w=2000.0, charging_state="charging")
    inv_off = InverterDecision(inverter_verb=None, active_power_w=None)

    states = {
        **_EV_STATES,
        "select.ems_inverter_control_mode": "Off",
    }

    coord = _make_fast_coordinator(states)
    coord._state.last_result = _make_ladder_result()
    coord._ema_active_w = 500.0

    with patch(_RECOMPUTE_EV, return_value=ev), \
         patch(_RECOMPUTE_INV, return_value=inv_off), \
         patch(_DT_UTIL) as mock_dt:
        mock_dt.now.return_value = _NOW
        await coord._async_update_data()

    written_inv: InverterDecision = coord._controller.write_fast.call_args[0][1]
    assert written_inv.active_power_w is None
    assert coord._ema_active_w is None


# ---------------------------------------------------------------------------
# EmsStrategyCoordinator startup grace — inverter_decision status override
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_strategy_coordinator_sets_inverter_status_starting_up_during_grace():
    """During startup grace the coordinator overrides inverter_decision.status to 'starting_up'."""
    from custom_components.ems.coordinator import EmsStrategyCoordinator

    entry = _make_entry({
        "grid_import_power": "sensor.grid",
        "pv_power": "sensor.pv",
        "house_load": "sensor.load",
        "ev_power": "sensor.ev",
    })

    hass = _make_hass({
        "sensor.grid": 0, "sensor.pv": 5000, "sensor.load": 1000, "sensor.ev": 2000,
        "number.ems_ev_min_charge_w": 1400,
        "select.ems_ev_charging_mode": "Grid-optimised",
        "select.ems_inverter_control_mode": "Control Inverter",
    })

    grace_end = datetime(2024, 1, 1, 0, 1, 0, tzinfo=UTC)
    now = datetime(2024, 1, 1, 0, 0, 30, tzinfo=UTC)

    state = MagicMock()
    state.startup_grace_until = grace_end
    state.last_result = None
    state.ev_below_min_since = None

    controller = MagicMock()
    controller.write_slow = AsyncMock()

    coord = EmsStrategyCoordinator.__new__(EmsStrategyCoordinator)
    coord.hass = hass
    coord._entry = entry
    coord._state = state
    coord._controller = controller

    with patch("custom_components.ems.coordinator.dt_util") as mock_dt:
        mock_dt.now.return_value = now
        result = await coord._async_update_data()

    assert result.inverter_decision.status == "starting_up"


# ---------------------------------------------------------------------------
# Fast loop ev_plugged regression: stale prior_inputs must not win
# ---------------------------------------------------------------------------

def _make_stale_unplugged_result() -> LadderResult:
    """LadderResult whose inputs carry ev_plugged=False (simulating prior unplug)."""
    now = datetime(2024, 1, 1, tzinfo=UTC)
    thresholds = Thresholds(max_grid_import_w=6000, ev_target_soc=80, ev_min_charge_w=1400)
    mapping = VerbMapping(
        inverter_select_entity=None,
        disabled_option=None,
        power_control_option=None,
        active_power_entity=None,
        ev_charge_target_entity=None,
    )
    inputs = Inputs(
        now=now,
        grid_import_w=0.0,
        pv_w=0.0,
        house_load_w=0.0,
        ev_plugged=False,
        ev_power_w=0.0,
        ev_soc=None,
        ev_strategy="Price-optimised",
        is_cheap_hour=True,
        inverter_strategy="Off",
        thresholds=thresholds,
        mapping=mapping,
    )
    return LadderResult(
        ev_decision=EvDecision(
            strategy="ev_idle", ev_charge_power_w=0.0, charging_state="ev_not_connected"
        ),
        inverter_decision=InverterDecision(inverter_verb=None, active_power_w=None),
        inputs=inputs,
        computed_at=now,
    )


@pytest.mark.asyncio
async def test_fast_loop_reads_ev_plug_fresh_on_replug():
    """Regression: prior_inputs.ev_plugged=False (stale) is overridden by a fresh entity read."""
    states = {
        "sensor.pv": 3000,
        "sensor.load": 1000,
        "sensor.ev": 0,
        "sensor.grid": 0,
        "binary_sensor.ev_plug": "on",
        "select.ems_ev_charging_mode": "Price-optimised",
        "select.ems_inverter_control_mode": "Off",
        "select.ems_ev_smoothing": "Fast",
    }
    coord = _make_fast_coordinator(states)
    coord._entry.data["ev_plug"] = "binary_sensor.ev_plug"
    coord._state.last_result = _make_stale_unplugged_result()

    with patch(_DT_UTIL) as mock_dt:
        mock_dt.now.return_value = _NOW
        result = await coord._async_update_data()

    assert result.inputs.ev_plugged is True
    assert result.ev_decision.charging_state != "ev_not_connected"


@pytest.mark.asyncio
async def test_fast_loop_reads_ev_plug_fresh_on_unplug():
    """Sanity inverse: prior_inputs.ev_plugged=True is overridden when entity reports off."""
    states = {
        "sensor.pv": 3000,
        "sensor.load": 1000,
        "sensor.ev": 0,
        "sensor.grid": 0,
        "binary_sensor.ev_plug": "off",
        "select.ems_ev_charging_mode": "Price-optimised",
        "select.ems_inverter_control_mode": "Off",
        "select.ems_ev_smoothing": "Fast",
    }
    base = _make_stale_unplugged_result()
    from dataclasses import replace as dc_replace
    stale_plugged_inputs = dc_replace(base.inputs, ev_plugged=True)
    plugged_result = LadderResult(
        ev_decision=EvDecision(
            strategy="ev_charging", ev_charge_power_w=2000.0, charging_state="charging"
        ),
        inverter_decision=base.inverter_decision,
        inputs=stale_plugged_inputs,
        computed_at=base.computed_at,
    )

    coord = _make_fast_coordinator(states)
    coord._entry.data["ev_plug"] = "binary_sensor.ev_plug"
    coord._state.last_result = plugged_result

    with patch(_DT_UTIL) as mock_dt:
        mock_dt.now.return_value = _NOW
        result = await coord._async_update_data()

    assert result.inputs.ev_plugged is False
    assert result.ev_decision.charging_state == "ev_not_connected"


# ---------------------------------------------------------------------------
# EmsStrategyCoordinator async_setup — listener wiring
# ---------------------------------------------------------------------------

_TRACK_STATE = (
    "custom_components.ems.coordinator.async_track_state_change_event"
)


def _make_setup_coordinator(entry_data: dict):
    from custom_components.ems.coordinator import EmsStrategyCoordinator

    entry = _make_entry(entry_data)
    entry.async_on_unload = MagicMock()

    hass = MagicMock()
    hass.async_create_task = MagicMock()

    state = MagicMock()
    controller = MagicMock()
    controller.reset_write_cache = MagicMock()

    coord = EmsStrategyCoordinator.__new__(EmsStrategyCoordinator)
    coord.hass = hass
    coord._entry = entry
    coord._state = state
    coord._controller = controller
    return coord


@pytest.mark.asyncio
async def test_async_setup_watches_number_entities():
    """number.ems_* knobs are included in the state-change watch list."""
    coord = _make_setup_coordinator({"grid_import_power": "sensor.grid"})
    captured: list[str] = []

    def mock_track(hass, entities, cb):
        captured.extend(entities)
        return MagicMock()

    with patch(_TRACK_STATE, side_effect=mock_track), \
         patch("custom_components.ems.coordinator.dt_util"):
        await coord.async_setup()

    assert "number.ems_ev_min_charge_w" in captured
    assert "number.ems_battery_target_soc" in captured
    assert "select.ems_ev_smoothing" in captured


@pytest.mark.asyncio
async def test_async_setup_watches_configured_sensor():
    """Configured sensor entity IDs from entry.data appear in the watch list."""
    coord = _make_setup_coordinator({
        CONF_GRID_IMPORT_POWER: "sensor.grid",
        CONF_BATTERY_SOC: "sensor.battery_soc",
    })
    captured: list[str] = []

    def mock_track(hass, entities, cb):
        captured.extend(entities)
        return MagicMock()

    with patch(_TRACK_STATE, side_effect=mock_track), \
         patch("custom_components.ems.coordinator.dt_util"):
        await coord.async_setup()

    assert "sensor.grid" in captured
    assert "sensor.battery_soc" in captured


@pytest.mark.asyncio
async def test_async_setup_omits_unconfigured_optional_sensors():
    """None values for optional entities must not enter the watch list."""
    coord = _make_setup_coordinator({
        CONF_GRID_IMPORT_POWER: "sensor.grid",
        # CONF_BATTERY_SOC intentionally absent
    })
    captured: list[str] = []

    def mock_track(hass, entities, cb):
        captured.extend(entities)
        return MagicMock()

    with patch(_TRACK_STATE, side_effect=mock_track), \
         patch("custom_components.ems.coordinator.dt_util"):
        await coord.async_setup()

    assert None not in captured
    assert all(isinstance(e, str) for e in captured)


@pytest.mark.asyncio
async def test_async_setup_input_change_calls_request_refresh():
    """State-change event on any watched entity triggers async_request_refresh."""
    coord = _make_setup_coordinator({CONF_GRID_IMPORT_POWER: "sensor.grid"})
    captured_cb = None

    def mock_track(hass, entities, cb):
        nonlocal captured_cb
        captured_cb = cb
        return MagicMock()

    with patch(_TRACK_STATE, side_effect=mock_track), \
         patch("custom_components.ems.coordinator.dt_util"):
        await coord.async_setup()

    coord.async_request_refresh = AsyncMock()
    captured_cb(MagicMock())  # simulate a state-change event
    coord.hass.async_create_task.assert_called_once()
    # the task wraps async_request_refresh — verify the coroutine was created
    call_arg = coord.hass.async_create_task.call_args[0][0]
    assert call_arg is not None


# ---------------------------------------------------------------------------
# _read_solar_forecast_json
# ---------------------------------------------------------------------------

_SOLAR_FORECAST_24 = list(float(h * 100) for h in range(24))


def test_read_solar_forecast_json_happy_path():
    from custom_components.ems.coordinator import _read_solar_forecast_json
    hass = _make_hass_with_attr("sensor.solar_forecast", json.dumps(_SOLAR_FORECAST_24))
    result = _read_solar_forecast_json(hass, "sensor.solar_forecast")
    assert result == tuple(_SOLAR_FORECAST_24)


def test_read_solar_forecast_json_none_on_no_entity_id():
    from custom_components.ems.coordinator import _read_solar_forecast_json
    hass = MagicMock()
    assert _read_solar_forecast_json(hass, None) is None


def test_read_solar_forecast_json_none_on_unavailable():
    from custom_components.ems.coordinator import _read_solar_forecast_json
    s = MagicMock()
    s.state = "unavailable"
    hass = MagicMock()
    hass.states.get = MagicMock(return_value=s)
    assert _read_solar_forecast_json(hass, "sensor.solar_forecast") is None


def test_read_solar_forecast_json_none_on_parse_failure():
    from custom_components.ems.coordinator import _read_solar_forecast_json
    hass = _make_hass_with_attr("sensor.solar_forecast", "not-json")
    assert _read_solar_forecast_json(hass, "sensor.solar_forecast") is None


def test_read_solar_forecast_json_none_on_wrong_length():
    from custom_components.ems.coordinator import _read_solar_forecast_json
    hass = _make_hass_with_attr("sensor.solar_forecast", json.dumps([100.0] * 23))
    assert _read_solar_forecast_json(hass, "sensor.solar_forecast") is None


# ---------------------------------------------------------------------------
# _build_inputs populates solar_forecast_today and derives solar_forecast_remaining_kwh
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_build_inputs_solar_forecast_json_populates_fields():
    from custom_components.ems.coordinator import EmsStrategyCoordinator

    forecast = [0.0] * 13 + [2000.0, 3000.0] + [0.0] * 9  # non-zero only at hours 13 and 14
    entry = _make_entry({
        "grid_import_power": "sensor.grid",
        "pv_power": "sensor.pv",
        "house_load": "sensor.load",
        "ev_power": "sensor.ev",
        CONF_SOLAR_FORECAST_JSON_ENTITY: "sensor.solar_forecast",
    })

    hass = _make_hass({
        "sensor.grid": 0, "sensor.pv": 0, "sensor.load": 0, "sensor.ev": 0,
        "number.ems_ev_min_charge_w": 1400,
        "select.ems_ev_charging_mode": "Off",
        "select.ems_inverter_control_mode": "Off",
        "sensor.solar_forecast": json.dumps(forecast),
    })

    state = MagicMock()
    state.startup_grace_until = None

    coordinator = EmsStrategyCoordinator.__new__(EmsStrategyCoordinator)
    coordinator.hass = hass
    coordinator._entry = entry
    coordinator._state = state

    fixed_now = datetime(2026, 5, 29, 13, 0, 0, tzinfo=UTC)

    with patch("custom_components.ems.coordinator.dt_util") as mock_dt:
        mock_dt.now.return_value = fixed_now
        inputs = await coordinator._build_inputs()

    assert inputs.solar_forecast_today == tuple(forecast)
    # At 13:00:00 — hour 13 fully remaining (60/60), hour 14 fully remaining: 2000+3000=5000 Wh
    assert inputs.solar_forecast_remaining_kwh == pytest.approx(5.0)


@pytest.mark.asyncio
async def test_build_inputs_solar_forecast_json_missing_entity():
    from custom_components.ems.coordinator import EmsStrategyCoordinator

    entry = _make_entry({
        "grid_import_power": "sensor.grid",
        "pv_power": "sensor.pv",
        "house_load": "sensor.load",
        "ev_power": "sensor.ev",
        # CONF_SOLAR_FORECAST_JSON_ENTITY intentionally absent
    })

    hass = _make_hass({
        "sensor.grid": 0, "sensor.pv": 0, "sensor.load": 0, "sensor.ev": 0,
        "number.ems_ev_min_charge_w": 1400,
        "select.ems_ev_charging_mode": "Off",
        "select.ems_inverter_control_mode": "Off",
    })

    state = MagicMock()
    state.startup_grace_until = None

    coordinator = EmsStrategyCoordinator.__new__(EmsStrategyCoordinator)
    coordinator.hass = hass
    coordinator._entry = entry
    coordinator._state = state

    with patch("custom_components.ems.coordinator.dt_util"):
        inputs = await coordinator._build_inputs()

    assert inputs.solar_forecast_today is None
    assert inputs.solar_forecast_remaining_kwh is None


# ---------------------------------------------------------------------------
# EmsForecastCoordinator watchlist includes solar_forecast_json_entity
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_forecast_coordinator_watchlist_includes_solar_forecast_json():
    from custom_components.ems.coordinator import EmsForecastCoordinator

    entry = _make_entry({CONF_SOLAR_FORECAST_JSON_ENTITY: "sensor.my_solar_forecast"})
    entry.async_on_unload = MagicMock()

    hass = MagicMock()
    captured: list[str] = []

    def mock_track(h, entities, cb):
        captured.extend(entities)
        return MagicMock()

    _TRACK = "custom_components.ems.coordinator.async_track_state_change_event"
    with patch(_TRACK, side_effect=mock_track):
        coord = EmsForecastCoordinator.__new__(EmsForecastCoordinator)
        coord.hass = hass
        coord._entry = entry
        await coord.async_setup()

    assert "sensor.my_solar_forecast" in captured
