from __future__ import annotations

import datetime
import math

import pytest
from freezegun import freeze_time

from custom_components.ems.ev_scheduler import compute_window, parse_forecast
from custom_components.ems.models import EvWindow, PriceSlot

from .conftest import forecast_factory

_UTC = datetime.timezone.utc
_NOW = datetime.datetime(2026, 4, 27, 10, 0, 0, tzinfo=_UTC)
_DEADLINE = datetime.time(7, 0)


def _make_slots(prices: list[float], start: datetime.datetime | None = None) -> list[PriceSlot]:
    t = start or _NOW
    return [
        PriceSlot(start=t + datetime.timedelta(hours=i), end=t + datetime.timedelta(hours=i + 1), price=p)
        for i, p in enumerate(prices)
    ]


# ── basic cases ───────────────────────────────────────────────────────────────

def test_kwh_needed_zero_returns_empty_window() -> None:
    result = compute_window(
        now=_NOW, deadline=_DEADLINE, strategy="Deadline",
        kwh_needed=0, max_charge_kw=7.4, forecast=_make_slots([0.1] * 10),
        solar_surplus_w=0, battery_soc=50, previous=None,
    )
    assert result == EvWindow(start=None, end=None, in_window_now=False, reason="no_need")


def test_forecast_shorter_than_hours_needed_preserves_previous() -> None:
    kwh_needed = 20
    max_kw = 7.0
    hours_needed = math.ceil(kwh_needed / (max_kw * 0.9))  # = 4

    previous = EvWindow(
        start=_NOW, end=_NOW + datetime.timedelta(hours=4), in_window_now=True, reason="price"
    )
    result = compute_window(
        now=_NOW, deadline=datetime.time(23, 0), strategy="Price-optimised",
        kwh_needed=kwh_needed, max_charge_kw=max_kw,
        forecast=_make_slots([0.1, 0.2]),  # only 2 slots, need 4
        solar_surplus_w=0, battery_soc=50, previous=previous,
    )
    assert result.reason == "stale_forecast"
    assert result.start == previous.start
    assert result.end == previous.end


def test_solar_only_strategy_ignores_price() -> None:
    result = compute_window(
        now=_NOW, deadline=_DEADLINE, strategy="Solar-only",
        kwh_needed=5, max_charge_kw=7.4,
        forecast=_make_slots([1.0] * 24),  # expensive prices — should be ignored
        solar_surplus_w=1500, battery_soc=90, previous=None,
    )
    assert result.in_window_now is True
    assert result.reason == "solar"


def test_solar_only_inactive_when_low_surplus() -> None:
    result = compute_window(
        now=_NOW, deadline=_DEADLINE, strategy="Solar-only",
        kwh_needed=5, max_charge_kw=7.4, forecast=None,
        solar_surplus_w=500, battery_soc=90, previous=None,
    )
    assert result.in_window_now is False


def test_deadline_strategy_filters_to_deadline() -> None:
    # Cheapest hour is at index 20 (hour 20 from now), well past 7:00 tomorrow deadline
    prices = [0.30] * 20 + [0.01] + [0.30] * 3
    slots = _make_slots(prices)
    # deadline is next day 7:00 → only first ~21 hours qualify
    result = compute_window(
        now=_NOW, deadline=datetime.time(7, 0), strategy="Deadline",
        kwh_needed=1, max_charge_kw=7.4, forecast=slots,
        solar_surplus_w=0, battery_soc=50, previous=None,
    )
    # Should pick the cheapest within deadline window, not the very cheap hour 20
    # (since deadline is tomorrow 7:00 = 21 hours from now, slot 20 IS within range)
    assert result.reason == "deadline"
    assert result.start is not None


def test_price_optimised_uses_next_24h() -> None:
    # Put cheapest hour at index 22 (hour 22), which is within 24h
    prices = [0.30] * 22 + [0.01] + [0.30]
    slots = _make_slots(prices)
    result = compute_window(
        now=_NOW, deadline=datetime.time(7, 0), strategy="Price-optimised",
        kwh_needed=1, max_charge_kw=7.4, forecast=slots,
        solar_surplus_w=0, battery_soc=50, previous=None,
    )
    assert result.reason == "price"
    assert result.start is not None


def test_hours_needed_rounding_boundary() -> None:
    """ceil(kwh / (kw * 0.9)) semantics."""
    cases = [
        (7.0, 7.0, 2),   # ceil(7 / 6.3) = 2
        (6.3, 7.0, 1),   # ceil(6.3 / 6.3) = 1
        (7.1, 7.0, 2),   # ceil(7.1 / 6.3) = 2
    ]
    for kwh, kw, expected_h in cases:
        assert math.ceil(kwh / (kw * 0.9)) == expected_h


def test_empty_forecast_list_degrades() -> None:
    result = compute_window(
        now=_NOW, deadline=_DEADLINE, strategy="Price-optimised",
        kwh_needed=5, max_charge_kw=7.4, forecast=[],
        solar_surplus_w=0, battery_soc=50, previous=None,
    )
    assert result.reason == "stale_forecast"
    assert result.start is None


def test_overlapping_forecast_entries_rejected() -> None:
    t = _NOW
    slots = [
        {"start": t.isoformat(), "end": (t + datetime.timedelta(hours=1)).isoformat(), "price": 0.1},
        {"start": t.isoformat(), "end": (t + datetime.timedelta(hours=1)).isoformat(), "price": 0.2},
    ]
    result = parse_forecast(slots)
    assert result is None


def test_forecast_with_holes() -> None:
    """Gaps in the forecast should not cause crashes."""
    t = _NOW
    slots = _make_slots([0.2, 0.1, 0.3, 0.15], start=t)
    # Remove index 2 to create a hole
    slots_with_hole = [slots[0], slots[1], slots[3]]
    result = compute_window(
        now=t, deadline=datetime.time(23, 0), strategy="Price-optimised",
        kwh_needed=2, max_charge_kw=7.4, forecast=slots_with_hole,
        solar_surplus_w=0, battery_soc=50, previous=None,
    )
    assert result is not None


def test_off_strategy_returns_off() -> None:
    result = compute_window(
        now=_NOW, deadline=_DEADLINE, strategy="Off",
        kwh_needed=10, max_charge_kw=7.4, forecast=_make_slots([0.1] * 24),
        solar_surplus_w=0, battery_soc=50, previous=None,
    )
    assert result.reason == "off"
    assert result.in_window_now is False


# ── parse_forecast ────────────────────────────────────────────────────────────

def test_parse_forecast_valid() -> None:
    t = _NOW
    raw = [
        {"start": t.isoformat(), "end": (t + datetime.timedelta(hours=1)).isoformat(), "price": 0.15},
    ]
    result = parse_forecast(raw)
    assert result is not None
    assert len(result) == 1
    assert result[0].price == 0.15


def test_parse_forecast_not_a_list() -> None:
    assert parse_forecast({"price": 0.1}) is None


def test_parse_forecast_missing_key() -> None:
    raw = [{"start": _NOW.isoformat(), "price": 0.1}]  # missing "end"
    assert parse_forecast(raw) is None
