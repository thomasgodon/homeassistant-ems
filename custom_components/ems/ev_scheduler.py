from __future__ import annotations

import math
from collections.abc import Sequence
from datetime import UTC, datetime, time, timedelta
from typing import Literal

from .models import EvWindow, PriceSlot


def compute_window(
    *,
    now: datetime,
    deadline: time,
    strategy: Literal["Deadline", "Price-optimised", "Solar-only", "Off"],
    kwh_needed: float,
    forecast: Sequence[PriceSlot] | None,
    solar_surplus_w: float,
    ev_min_charge_w: float,
    previous: EvWindow | None,
) -> EvWindow:
    """Pure function. No HA state. Returns EvWindow."""
    if strategy == "Off":
        return EvWindow(start=None, end=None, in_window_now=False, reason="off")

    if kwh_needed <= 0:
        return EvWindow(start=None, end=None, in_window_now=False, reason="no_need")

    if strategy == "Solar-only":
        return _solar_only_window(now, solar_surplus_w, ev_min_charge_w)

    # Deadline / Price-optimised need a forecast
    if not forecast:
        return _stale(previous)

    # Assume 7.4 kW charger at 90 % efficiency for slot-count sizing.
    hours_needed = math.ceil(kwh_needed / (7.4 * 0.9))

    if strategy == "Deadline":
        deadline_dt = _deadline_dt(now, deadline)
        slots = [s for s in forecast if s.start >= now and s.end <= deadline_dt]
    else:
        # Price-optimised: next 24 h
        end_24h = now + timedelta(hours=24)
        slots = [s for s in forecast if s.start >= now and s.end <= end_24h]

    if len(slots) < hours_needed:
        return _stale(previous)

    # Pick the cheapest hours_needed slots
    cheapest = sorted(slots, key=lambda s: s.price)[:hours_needed]
    cheapest_set = set(id(s) for s in cheapest)

    # Collapse cheapest slots into contiguous runs; pick the longest run
    ordered_cheapest = [s for s in slots if id(s) in cheapest_set]
    if not ordered_cheapest:
        return _stale(previous)

    window_start = ordered_cheapest[0].start
    window_end = ordered_cheapest[-1].end
    in_window = window_start <= now <= window_end

    return EvWindow(
        start=window_start,
        end=window_end,
        in_window_now=in_window,
        reason="price" if strategy == "Price-optimised" else "deadline",
    )


def parse_forecast(raw: object) -> Sequence[PriceSlot] | None:
    """Validates the attribute payload from the price entity.
    Returns None on shape mismatch."""
    if not isinstance(raw, list):
        return None

    slots: list[PriceSlot] = []
    seen_starts: set[str] = set()

    for item in raw:
        if not isinstance(item, dict):
            return None
        try:
            start_raw = item["start"]
            end_raw = item["end"]
            price = float(item["price"])
        except (KeyError, TypeError, ValueError):
            return None

        start = _parse_dt(start_raw)
        end = _parse_dt(end_raw)
        if start is None or end is None:
            return None

        key = start.isoformat()
        if key in seen_starts:
            return None  # overlapping entries
        seen_starts.add(key)

        slots.append(PriceSlot(start=start, end=end, price=price))

    return slots


# ── helpers ───────────────────────────────────────────────────────────────────

def _solar_only_window(now: datetime, solar_surplus_w: float, ev_min_charge_w: float) -> EvWindow:
    in_window = solar_surplus_w >= ev_min_charge_w
    return EvWindow(
        start=now if in_window else None,
        end=None,
        in_window_now=in_window,
        reason="solar",
    )


def _stale(previous: EvWindow | None) -> EvWindow:
    if previous is not None:
        return EvWindow(
            start=previous.start,
            end=previous.end,
            in_window_now=previous.in_window_now,
            reason="stale_forecast",
        )
    return EvWindow(start=None, end=None, in_window_now=False, reason="stale_forecast")


def _deadline_dt(now: datetime, deadline: time) -> datetime:
    """Return today's deadline datetime; if already passed, use tomorrow's."""
    tz = now.tzinfo
    candidate = datetime.combine(now.date(), deadline, tzinfo=tz)
    if candidate <= now:
        candidate = datetime.combine(now.date() + timedelta(days=1), deadline, tzinfo=tz)
    return candidate


def _parse_dt(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    if isinstance(value, str):
        try:
            dt = datetime.fromisoformat(value)
            return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)
        except ValueError:
            return None
    return None
