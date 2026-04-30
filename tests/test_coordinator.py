from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from custom_components.ems.const import (
    CONF_EV_TARGET_SOC,
    CONF_MAX_GRID_IMPORT_W,
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
        # all entity-id keys absent
    })

    hass = _make_hass({
        "sensor.grid": 0, "sensor.pv": 0, "sensor.load": 0, "sensor.ev": 0,
        "number.ems_ev_min_charge_w": 1400,
        "select.ems_ev_charging_mode": "Off",
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
async def test_build_inputs_battery_control_option_from_inverter_options():
    """battery_control_option is extracted from CONF_INVERTER_OPTIONS['BATTERY_CONTROL']."""
    from custom_components.ems.coordinator import EmsStrategyCoordinator

    entry = _make_entry({
        "grid_import_power": "sensor.grid",
        "pv_power": "sensor.pv",
        "house_load": "sensor.load",
        "ev_power": "sensor.ev",
        "inverter_select": "select.inv",
        "inverter_options": {"SELF_USE": "Self Use", "BATTERY_CONTROL": "Bat Ctrl"},
        "battery_discharge_cap_entity": "number.bat_cap",
    })

    hass = _make_hass({
        "sensor.grid": 0, "sensor.pv": 0, "sensor.load": 0, "sensor.ev": 0,
        "number.ems_ev_min_charge_w": 1400,
        "select.ems_ev_charging_mode": "Off",
    })

    state = MagicMock()
    state.startup_grace_until = None

    coordinator = EmsStrategyCoordinator.__new__(EmsStrategyCoordinator)
    coordinator.hass = hass
    coordinator._entry = entry
    coordinator._state = state

    with patch("custom_components.ems.coordinator.dt_util"):
        inputs = await coordinator._build_inputs()

    assert inputs.mapping.battery_control_option == "Bat Ctrl"
    assert inputs.mapping.battery_discharge_cap_entity == "number.bat_cap"
    assert inputs.mapping.self_use_option == "Self Use"
