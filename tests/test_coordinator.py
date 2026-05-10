from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.ems.const import (
    CONF_EV_TARGET_SOC,
    CONF_MAX_GRID_IMPORT_W,
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
        mock_dt.utcnow.return_value = _NOW
        await coord._async_update_data()

    written_ev: EvDecision = coord._controller.write_fast.call_args[0][0]
    written_inv: InverterDecision = coord._controller.write_fast.call_args[0][1]
    assert written_ev.ev_charge_power_w == pytest.approx(2000.0)
    assert written_inv.active_power_w == pytest.approx(500.0)


@pytest.mark.asyncio
async def test_fast_loop_ema_second_tick_blends():
    """Second tick blends 30% new + 70% old."""
    second_ev = _ev_charging(3000.0)
    second_inv = InverterDecision(inverter_verb="power_control", active_power_w=1000.0)

    coord = _make_fast_coordinator(_EV_STATES)
    coord._state.last_result = _make_ladder_result()

    with patch(_DT_UTIL) as mock_dt:
        mock_dt.utcnow.return_value = _NOW
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
        mock_dt.utcnow.return_value = _NOW

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
        mock_dt.utcnow.return_value = _NOW
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
        mock_dt.utcnow.return_value = _NOW
        await coord._async_update_data()

    written_ev: EvDecision = coord._controller.write_fast.call_args[0][0]
    assert written_ev.ev_charge_power_w == pytest.approx(0.0)
    assert coord._ema_ev_w is None
    assert coord._ema_active_w is None


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
        mock_dt.utcnow.return_value = _NOW
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

    controller = MagicMock()
    controller.write_slow = AsyncMock()

    coord = EmsStrategyCoordinator.__new__(EmsStrategyCoordinator)
    coord.hass = hass
    coord._entry = entry
    coord._state = state
    coord._controller = controller

    with patch("custom_components.ems.coordinator.dt_util") as mock_dt:
        mock_dt.utcnow.return_value = now
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
        mock_dt.utcnow.return_value = _NOW
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
        mock_dt.utcnow.return_value = _NOW
        result = await coord._async_update_data()

    assert result.inputs.ev_plugged is False
    assert result.ev_decision.charging_state == "ev_not_connected"
