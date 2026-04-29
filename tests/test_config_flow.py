from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.ems.config_flow import EmsConfigFlow, EmsOptionsFlow
from custom_components.ems.const import (
    CONF_EV_CAPACITY_KWH,
    CONF_EV_TARGET_SOC,
    CONF_MAX_GRID_IMPORT_W,
)


def _make_hass():
    hass = MagicMock()
    hass.config_entries = MagicMock()
    hass.config_entries.async_update_entry = MagicMock()
    return hass


# ---------------------------------------------------------------------------
# Config flow — field presence
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_required_inputs_contains_max_grid_import():
    flow = EmsConfigFlow()
    result = await flow.async_step_required_inputs(user_input=None)
    assert result["type"] == "form"
    schema_keys = {str(k) for k in result["data_schema"].schema}
    assert CONF_MAX_GRID_IMPORT_W in schema_keys


@pytest.mark.asyncio
async def test_ev_charger_contains_ev_target_soc_and_entity_selectors():
    flow = EmsConfigFlow()
    flow._data = {}
    result = await flow.async_step_ev_charger(user_input=None)
    assert result["type"] == "form"
    schema_keys = {str(k) for k in result["data_schema"].schema}
    assert CONF_EV_TARGET_SOC in schema_keys
    assert CONF_EV_CAPACITY_KWH in schema_keys


@pytest.mark.asyncio
async def test_full_config_flow_stores_entity_ids():
    """Walk the full config flow and assert entity-id strings are stored."""
    flow = EmsConfigFlow()
    flow.hass = _make_hass()
    flow.context = {}

    _inv_result = {"type": "form", "step_id": "inverter", "data_schema": MagicMock(schema={})}
    with patch.object(flow, "async_step_inverter", new=AsyncMock(return_value=_inv_result)):
        await flow.async_step_required_inputs(user_input={
            "grid_import_power": "sensor.grid",
            "pv_power": "sensor.pv",
            "house_load": "sensor.load",
            "ev_power": "sensor.ev",
            CONF_MAX_GRID_IMPORT_W: "input_number.max_grid_import",
        })

    assert flow._data[CONF_MAX_GRID_IMPORT_W] == "input_number.max_grid_import"

    with patch.object(
        flow, "async_step_price_sources", new=AsyncMock(return_value={"type": "form"})
    ):
        await flow.async_step_ev_charger(user_input={
            CONF_EV_TARGET_SOC: "input_number.ev_target_soc",
            CONF_EV_CAPACITY_KWH: "input_number.ev_capacity",
        })

    assert flow._data[CONF_EV_TARGET_SOC] == "input_number.ev_target_soc"
    assert flow._data[CONF_EV_CAPACITY_KWH] == "input_number.ev_capacity"


# ---------------------------------------------------------------------------
# Options flow — all 4 steps, prefilled defaults
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_options_flow_required_inputs_step_shows_current_entities():
    entry = MagicMock()
    entry.data = {
        "grid_import_power": "sensor.grid",
        "pv_power": "sensor.pv",
        "house_load": "sensor.load",
        "ev_power": "sensor.ev",
        CONF_MAX_GRID_IMPORT_W: "input_number.max_grid",
    }
    flow = EmsOptionsFlow(entry)
    result = await flow.async_step_required_inputs(user_input=None)
    assert result["type"] == "form"
    assert result["step_id"] == "required_inputs"


@pytest.mark.asyncio
async def test_options_flow_ev_charger_step_shows_entity_selectors():
    entry = MagicMock()
    entry.data = {
        CONF_EV_CAPACITY_KWH: "input_number.ev_cap",
        CONF_EV_TARGET_SOC: "input_number.ev_target_soc",
    }
    flow = EmsOptionsFlow(entry)
    result = await flow.async_step_ev_charger(user_input=None)
    assert result["type"] == "form"
    assert result["step_id"] == "ev_charger"
    schema_keys = {str(k) for k in result["data_schema"].schema}
    assert CONF_EV_CAPACITY_KWH in schema_keys
    assert CONF_EV_TARGET_SOC in schema_keys


@pytest.mark.asyncio
async def test_options_flow_writes_back_all_steps():
    entry = MagicMock()
    entry.data = {
        "grid_import_power": "sensor.grid",
        "pv_power": "sensor.pv",
        "house_load": "sensor.load",
        "ev_power": "sensor.ev",
        CONF_MAX_GRID_IMPORT_W: "input_number.max_grid",
        CONF_EV_TARGET_SOC: "input_number.ev_target_soc",
        CONF_EV_CAPACITY_KWH: "input_number.ev_cap",
    }
    flow = EmsOptionsFlow(entry)
    flow.hass = _make_hass()

    # Step 1 — required inputs
    with patch.object(flow, "async_step_inverter", new=AsyncMock(return_value={"type": "form"})):
        await flow.async_step_required_inputs(user_input={
            "grid_import_power": "sensor.grid2",
            "pv_power": "sensor.pv",
            "house_load": "sensor.load",
            "ev_power": "sensor.ev",
            CONF_MAX_GRID_IMPORT_W: "input_number.max_grid_new",
        })
    assert flow._data[CONF_MAX_GRID_IMPORT_W] == "input_number.max_grid_new"
    assert flow._data["grid_import_power"] == "sensor.grid2"

    # Step 3 — EV charger
    _ps_result = {"type": "form"}
    with patch.object(flow, "async_step_price_sources", new=AsyncMock(return_value=_ps_result)):
        await flow.async_step_ev_charger(user_input={
            CONF_EV_TARGET_SOC: "input_number.ev_target_new",
            CONF_EV_CAPACITY_KWH: "input_number.ev_cap_new",
        })
    assert flow._data[CONF_EV_TARGET_SOC] == "input_number.ev_target_new"
    assert flow._data[CONF_EV_CAPACITY_KWH] == "input_number.ev_cap_new"

    # Step 4 — price sources (final step triggers write-back)
    flow.hass.config_entries.async_update_entry = MagicMock()
    with patch.object(flow, "async_create_entry", return_value={"type": "create_entry"}):
        await flow.async_step_price_sources(user_input={})

    flow.hass.config_entries.async_update_entry.assert_called_once_with(
        entry, data=flow._data
    )


# ---------------------------------------------------------------------------
# Migration — async_migrate_entry
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_migrate_entry_v1_to_v2_reads_restore_state():
    from custom_components.ems import async_migrate_entry

    hass = _make_hass()

    last_grid = MagicMock()
    last_grid.state = MagicMock()
    last_grid.state.state = "5500.0"

    last_soc = MagicMock()
    last_soc.state = MagicMock()
    last_soc.state.state = "70.0"

    restore_data = MagicMock()
    restore_data.last_states = {
        "number.ems_max_grid_import_w": last_grid,
        "number.ems_ev_target_soc": last_soc,
    }

    entry = MagicMock()
    entry.version = 1
    entry.data = {}

    with patch(
        "custom_components.ems.RestoreStateData.async_get_instance",
        new=AsyncMock(return_value=restore_data),
    ):
        result = await async_migrate_entry(hass, entry)

    assert result is True
    hass.config_entries.async_update_entry.assert_called_once()


@pytest.mark.asyncio
async def test_migrate_entry_v2_to_v3_drops_scalar_values():
    from custom_components.ems import async_migrate_entry

    hass = _make_hass()
    entry = MagicMock()
    entry.version = 2
    entry.data = {
        CONF_MAX_GRID_IMPORT_W: 4500.0,   # scalar — must be dropped by v2→v3
        "ev_capacity_kwh": 60.0,           # scalar — must be dropped by v2→v3
        "ev_max_charge_kw": 7.4,           # dropped by v2→v3 (also listed in v4 cleanup)
        CONF_EV_TARGET_SOC: 80.0,          # scalar — dropped by v3→v4
        "grid_import_power": "sensor.grid",
    }

    result = await async_migrate_entry(hass, entry)

    assert result is True
    hass.config_entries.async_update_entry.assert_called_once()
    call_kwargs = hass.config_entries.async_update_entry.call_args
    new_data = call_kwargs.kwargs["data"]

    # v2→v3 dropped entity-id keys whose values were scalars
    assert CONF_MAX_GRID_IMPORT_W not in new_data
    assert "ev_capacity_kwh" not in new_data
    assert "ev_max_charge_kw" not in new_data
    # v3→v4 dropped ev_target_soc scalar
    assert CONF_EV_TARGET_SOC not in new_data
    # Unrelated keys kept
    assert new_data["grid_import_power"] == "sensor.grid"


@pytest.mark.asyncio
async def test_migrate_entry_v2_to_v3_keeps_entity_ids():
    """If entry already holds entity_id strings, they must survive migration."""
    from custom_components.ems import async_migrate_entry

    hass = _make_hass()
    entry = MagicMock()
    entry.version = 2
    entry.data = {
        CONF_MAX_GRID_IMPORT_W: "input_number.max_grid",  # already entity_id — keep
        "ev_capacity_kwh": "input_number.ev_cap",
        "ev_max_charge_kw": "input_number.ev_max",  # removed by v3→v4 anyway
        CONF_EV_TARGET_SOC: "input_number.ev_target_soc",  # entity_id — kept through v3→v4
    }

    result = await async_migrate_entry(hass, entry)

    assert result is True
    call_kwargs = hass.config_entries.async_update_entry.call_args
    new_data = call_kwargs.kwargs["data"]

    assert new_data[CONF_MAX_GRID_IMPORT_W] == "input_number.max_grid"
    assert new_data["ev_capacity_kwh"] == "input_number.ev_cap"
    # ev_max_charge_kw is removed by v3→v4 regardless of value type
    assert "ev_max_charge_kw" not in new_data
    # ev_target_soc entity_id is preserved (not a scalar, so not dropped)
    assert new_data[CONF_EV_TARGET_SOC] == "input_number.ev_target_soc"


@pytest.mark.asyncio
async def test_migrate_entry_v3_to_v4_drops_scalar_ev_target_soc():
    """v3→v4: ev_target_soc scalar and inverter_power_target are removed."""
    from custom_components.ems import async_migrate_entry

    hass = _make_hass()
    entry = MagicMock()
    entry.version = 3
    entry.data = {
        CONF_MAX_GRID_IMPORT_W: "input_number.max_grid",
        CONF_EV_TARGET_SOC: 80.0,           # scalar — must be dropped
        "inverter_power_target": "number.inverter",  # removed field — must be dropped
        "ev_max_charge_kw": "input_number.ev_max",   # removed field — must be dropped
        "grid_import_power": "sensor.grid",
    }

    result = await async_migrate_entry(hass, entry)

    assert result is True
    call_kwargs = hass.config_entries.async_update_entry.call_args
    new_data = call_kwargs.kwargs["data"]

    assert CONF_EV_TARGET_SOC not in new_data
    assert "inverter_power_target" not in new_data
    assert "ev_max_charge_kw" not in new_data
    assert new_data[CONF_MAX_GRID_IMPORT_W] == "input_number.max_grid"
    assert new_data["grid_import_power"] == "sensor.grid"


@pytest.mark.asyncio
async def test_migrate_entry_already_v4_is_noop():
    from custom_components.ems import async_migrate_entry

    hass = _make_hass()
    entry = MagicMock()
    entry.version = 4
    entry.data = {
        CONF_MAX_GRID_IMPORT_W: "input_number.max_grid",
        CONF_EV_TARGET_SOC: "input_number.ev_target_soc",
    }

    result = await async_migrate_entry(hass, entry)

    assert result is True
    hass.config_entries.async_update_entry.assert_not_called()
