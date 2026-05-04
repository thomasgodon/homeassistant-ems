from __future__ import annotations

import pytest


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
