from __future__ import annotations

import pytest

# ── EmsEvChargingMode ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_ev_charging_mode_options():
    from custom_components.ems.select import EmsEvChargingMode

    entity = EmsEvChargingMode()
    assert entity._attr_options == ["Grid-optimised", "Price-optimised", "Off"]


@pytest.mark.asyncio
async def test_ev_charging_mode_default():
    from custom_components.ems.select import EmsEvChargingMode

    entity = EmsEvChargingMode()
    assert entity._attr_current_option == "Off"


@pytest.mark.asyncio
async def test_ev_charging_mode_migrates_energy_optimised_to_grid_optimised():
    from unittest.mock import AsyncMock, MagicMock

    from custom_components.ems.select import EmsEvChargingMode

    entity = EmsEvChargingMode()
    last = MagicMock()
    last.state = "Energy-optimised"
    entity.async_get_last_state = AsyncMock(return_value=last)

    await entity.async_added_to_hass()

    assert entity._attr_current_option == "Grid-optimised"


@pytest.mark.asyncio
async def test_ev_charging_mode_ignores_invalid_restored_state():
    from unittest.mock import AsyncMock, MagicMock

    from custom_components.ems.select import EmsEvChargingMode

    entity = EmsEvChargingMode()
    last = MagicMock()
    last.state = "unknown"
    entity.async_get_last_state = AsyncMock(return_value=last)

    await entity.async_added_to_hass()

    assert entity._attr_current_option == "Off"


@pytest.mark.asyncio
async def test_ev_smoothing_default_option():
    from custom_components.ems.select import EmsEvSmoothing

    entity = EmsEvSmoothing()
    assert entity._attr_current_option == "Fast"
    assert entity._attr_options == ["Fast", "Normal", "Slow"]


@pytest.mark.asyncio
async def test_ev_smoothing_select_option():
    from unittest.mock import MagicMock

    from custom_components.ems.select import EmsEvSmoothing

    entity = EmsEvSmoothing()
    entity.async_write_ha_state = MagicMock()

    await entity.async_select_option("Slow")

    assert entity._attr_current_option == "Slow"
    entity.async_write_ha_state.assert_called_once()


@pytest.mark.asyncio
async def test_ev_smoothing_restores_state():
    from unittest.mock import AsyncMock, MagicMock

    from custom_components.ems.select import EmsEvSmoothing

    entity = EmsEvSmoothing()
    last = MagicMock()
    last.state = "Normal"
    entity.async_get_last_state = AsyncMock(return_value=last)

    await entity.async_added_to_hass()

    assert entity._attr_current_option == "Normal"


@pytest.mark.asyncio
async def test_ev_smoothing_ignores_invalid_restored_state():
    from unittest.mock import AsyncMock, MagicMock

    from custom_components.ems.select import EmsEvSmoothing

    entity = EmsEvSmoothing()
    last = MagicMock()
    last.state = "unknown"
    entity.async_get_last_state = AsyncMock(return_value=last)

    await entity.async_added_to_hass()

    assert entity._attr_current_option == "Fast"


# ── EmsInverterControlMode ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_inverter_control_mode_options():
    from custom_components.ems.select import EmsInverterControlMode

    entity = EmsInverterControlMode()
    assert entity._attr_options == ["Control Inverter", "Off"]


@pytest.mark.asyncio
async def test_inverter_control_mode_default_is_off():
    from custom_components.ems.select import EmsInverterControlMode

    entity = EmsInverterControlMode()
    assert entity._attr_current_option == "Off"


@pytest.mark.asyncio
async def test_inverter_control_mode_restores_state():
    from unittest.mock import AsyncMock, MagicMock

    from custom_components.ems.select import EmsInverterControlMode

    entity = EmsInverterControlMode()
    last = MagicMock()
    last.state = "Control Inverter"
    entity.async_get_last_state = AsyncMock(return_value=last)

    await entity.async_added_to_hass()

    assert entity._attr_current_option == "Control Inverter"


@pytest.mark.asyncio
async def test_inverter_control_mode_ignores_invalid_restored_state():
    from unittest.mock import AsyncMock, MagicMock

    from custom_components.ems.select import EmsInverterControlMode

    entity = EmsInverterControlMode()
    last = MagicMock()
    last.state = "unknown"
    entity.async_get_last_state = AsyncMock(return_value=last)

    await entity.async_added_to_hass()

    assert entity._attr_current_option == "Off"


@pytest.mark.asyncio
async def test_inverter_control_mode_select_option():
    from unittest.mock import MagicMock

    from custom_components.ems.select import EmsInverterControlMode

    entity = EmsInverterControlMode()
    entity.async_write_ha_state = MagicMock()

    await entity.async_select_option("Control Inverter")

    assert entity._attr_current_option == "Control Inverter"
    entity.async_write_ha_state.assert_called_once()
