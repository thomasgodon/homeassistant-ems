from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from custom_components.ems.const import (
    CONF_EV_TARGET_SOC,
    CONF_MAX_GRID_IMPORT_W,
    DEFAULT_EV_CAPACITY_KWH,
    DEFAULT_EV_MAX_CHARGE_KW,
    DEFAULT_EV_TARGET_SOC,
    DEFAULT_MAX_GRID_IMPORT_W,
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
# _build_inputs reads max_grid_import_w / ev_capacity_kwh / ev_max_charge_kw
# from entities (entity_ids stored in entry.data)
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
        CONF_EV_TARGET_SOC: 75.0,
    })

    hass = _make_hass({
        "sensor.grid": 500,
        "sensor.pv": 3000,
        "sensor.load": 1000,
        "sensor.ev": 0,
        "input_number.max_grid": 6000,
        "number.ems_ev_min_charge_w": 1400,
        "switch.ems_ev_charging": "on",
        "select.ems_ev_scheduling_strategy": "Off",
    })

    state = MagicMock()
    state.startup_grace_until = None
    state.cached_window = None

    coordinator = EmsStrategyCoordinator.__new__(EmsStrategyCoordinator)
    coordinator.hass = hass
    coordinator._entry = entry
    coordinator._state = state

    with patch("custom_components.ems.coordinator.dt_util"):
        inputs = await coordinator._build_inputs()

    assert inputs.thresholds.max_grid_import_w == 6000.0
    assert inputs.thresholds.ev_target_soc == 75.0


@pytest.mark.asyncio
async def test_build_inputs_reads_ev_capacity_and_max_charge_from_entities():
    from custom_components.ems.coordinator import EmsStrategyCoordinator

    entry = _make_entry({
        "grid_import_power": "sensor.grid",
        "pv_power": "sensor.pv",
        "house_load": "sensor.load",
        "ev_power": "sensor.ev",
        CONF_MAX_GRID_IMPORT_W: "input_number.max_grid",
        CONF_EV_TARGET_SOC: 80.0,
        "ev_capacity_kwh": "input_number.ev_cap",
        "ev_max_charge_kw": "input_number.ev_max",
    })

    hass = _make_hass({
        "sensor.grid": 0,
        "sensor.pv": 0,
        "sensor.load": 0,
        "sensor.ev": 0,
        "input_number.max_grid": 4500,
        "input_number.ev_cap": 77.0,
        "input_number.ev_max": 11.0,
        "number.ems_ev_min_charge_w": 1400,
        "switch.ems_ev_charging": "on",
        "select.ems_ev_scheduling_strategy": "Off",
    })

    state = MagicMock()
    state.startup_grace_until = None
    state.cached_window = None

    coordinator = EmsStrategyCoordinator.__new__(EmsStrategyCoordinator)
    coordinator.hass = hass
    coordinator._entry = entry
    coordinator._state = state

    with patch("custom_components.ems.coordinator.dt_util"):
        inputs = await coordinator._build_inputs()

    assert inputs.ev_capacity_kwh == 77.0
    assert inputs.ev_max_charge_kw == 11.0


@pytest.mark.asyncio
async def test_build_inputs_uses_defaults_when_keys_absent():
    from custom_components.ems.coordinator import EmsStrategyCoordinator

    entry = _make_entry({
        "grid_import_power": "sensor.grid",
        "pv_power": "sensor.pv",
        "house_load": "sensor.load",
        "ev_power": "sensor.ev",
        # all three entity-id keys absent
    })

    hass = _make_hass({
        "sensor.grid": 0, "sensor.pv": 0, "sensor.load": 0, "sensor.ev": 0,
        "number.ems_ev_min_charge_w": 1400,
        "switch.ems_ev_charging": "on",
        "select.ems_ev_scheduling_strategy": "Off",
    })

    state = MagicMock()
    state.startup_grace_until = None
    state.cached_window = None

    coordinator = EmsStrategyCoordinator.__new__(EmsStrategyCoordinator)
    coordinator.hass = hass
    coordinator._entry = entry
    coordinator._state = state

    with patch("custom_components.ems.coordinator.dt_util"):
        inputs = await coordinator._build_inputs()

    assert inputs.thresholds.max_grid_import_w == DEFAULT_MAX_GRID_IMPORT_W
    assert inputs.thresholds.ev_target_soc == DEFAULT_EV_TARGET_SOC
    assert inputs.ev_capacity_kwh == DEFAULT_EV_CAPACITY_KWH
    assert inputs.ev_max_charge_kw == DEFAULT_EV_MAX_CHARGE_KW


@pytest.mark.asyncio
async def test_build_inputs_uses_defaults_when_entity_unavailable():
    from custom_components.ems.coordinator import EmsStrategyCoordinator

    entry = _make_entry({
        "grid_import_power": "sensor.grid",
        "pv_power": "sensor.pv",
        "house_load": "sensor.load",
        "ev_power": "sensor.ev",
        CONF_MAX_GRID_IMPORT_W: "input_number.max_grid",  # entity present but unavailable
        "ev_capacity_kwh": "input_number.ev_cap",
        "ev_max_charge_kw": "input_number.ev_max",
    })

    def _unavailable_state(entity_id):
        base_map = {
            "sensor.grid": "0", "sensor.pv": "0", "sensor.load": "0", "sensor.ev": "0",
            "number.ems_ev_min_charge_w": "1400",
            "switch.ems_ev_charging": "on",
            "select.ems_ev_scheduling_strategy": "Off",
        }
        if entity_id in base_map:
            s = MagicMock()
            s.state = base_map[entity_id]
            return s
        if entity_id in ("input_number.max_grid", "input_number.ev_cap", "input_number.ev_max"):
            s = MagicMock()
            s.state = "unavailable"
            return s
        return None

    hass = MagicMock()
    hass.states.get = MagicMock(side_effect=_unavailable_state)

    state = MagicMock()
    state.startup_grace_until = None
    state.cached_window = None

    coordinator = EmsStrategyCoordinator.__new__(EmsStrategyCoordinator)
    coordinator.hass = hass
    coordinator._entry = entry
    coordinator._state = state

    with patch("custom_components.ems.coordinator.dt_util"):
        inputs = await coordinator._build_inputs()

    assert inputs.thresholds.max_grid_import_w == DEFAULT_MAX_GRID_IMPORT_W
    assert inputs.ev_capacity_kwh == DEFAULT_EV_CAPACITY_KWH
    assert inputs.ev_max_charge_kw == DEFAULT_EV_MAX_CHARGE_KW


# ---------------------------------------------------------------------------
# ev_min_charge_w still comes from number.ems_ev_min_charge_w
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_build_inputs_ev_min_charge_w_still_from_number_entity():
    from custom_components.ems.coordinator import EmsStrategyCoordinator

    entry = _make_entry({
        "grid_import_power": "sensor.grid",
        "pv_power": "sensor.pv",
        "house_load": "sensor.load",
        "ev_power": "sensor.ev",
        CONF_EV_TARGET_SOC: 80.0,
    })

    hass = _make_hass({
        "sensor.grid": 0, "sensor.pv": 0, "sensor.load": 0, "sensor.ev": 0,
        "number.ems_ev_min_charge_w": 2000,
        "switch.ems_ev_charging": "on",
        "select.ems_ev_scheduling_strategy": "Off",
    })

    state = MagicMock()
    state.startup_grace_until = None
    state.cached_window = None

    coordinator = EmsStrategyCoordinator.__new__(EmsStrategyCoordinator)
    coordinator.hass = hass
    coordinator._entry = entry
    coordinator._state = state

    with patch("custom_components.ems.coordinator.dt_util"):
        inputs = await coordinator._build_inputs()

    assert inputs.thresholds.ev_min_charge_w == 2000.0


# ---------------------------------------------------------------------------
# EvSchedulerOrchestrator._recompute reads ev_capacity_kwh / ev_max_charge_kw
# from entities
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_ev_scheduler_recompute_reads_ev_capacity_from_entities():
    from custom_components.ems.coordinator import EvSchedulerOrchestrator

    entry = _make_entry({
        "ev_plug": "binary_sensor.plug",
        "ev_soc": "sensor.soc",
        "ev_capacity_kwh": "input_number.ev_cap",
        "ev_max_charge_kw": "input_number.ev_max",
        CONF_EV_TARGET_SOC: 65.0,
        "pv_power": "sensor.pv",
        "house_load": "sensor.load",
        "ev_power": "sensor.ev",
    })

    states = {
        "binary_sensor.plug": "on",
        "sensor.soc": "50",
        "sensor.pv": "2000",
        "sensor.load": "800",
        "sensor.ev": "0",
        "input_number.ev_cap": "80.0",
        "input_number.ev_max": "11.0",
        "number.ems_ev_min_charge_w": "1400",
        "select.ems_ev_scheduling_strategy": "Off",
    }

    hass = MagicMock()
    hass.states.get = MagicMock(side_effect=lambda eid: (
        lambda s: (lambda m: (setattr(m, "state", s), m)[1])(MagicMock())
    )(states[eid]) if eid in states else None)

    state = MagicMock()
    state.cached_window = None
    state.forecast_parse_warned = False

    orch = EvSchedulerOrchestrator.__new__(EvSchedulerOrchestrator)
    orch._hass = hass
    orch._entry = entry
    orch._state = state

    with (
        patch("custom_components.ems.coordinator.dt_util"),
        patch("custom_components.ems.coordinator.compute_window") as mock_cw,
    ):
        mock_cw.return_value = MagicMock()
        await orch._recompute()

    call_kwargs = mock_cw.call_args.kwargs
    # soc=50, target=65, capacity=80 → needed = (65-50)/100 * 80 = 12.0 kWh
    assert call_kwargs["kwh_needed"] == pytest.approx(12.0)
    # max_charge_kw=11 → passed through
    assert call_kwargs["max_charge_kw"] == pytest.approx(11.0)
