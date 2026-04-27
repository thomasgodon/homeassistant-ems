from __future__ import annotations

from .models import (
    Decision,
    EvVerb,
    Inputs,
    InverterVerb,
    Thresholds,
    VerbMapping,
)


def decide(inputs: Inputs) -> Decision:
    """Run the priority ladder. Returns a Decision. Never raises."""
    if inputs.ems_mode != "Auto":
        return Decision(
            strategy="off",
            inverter_verb=None,
            inverter_power_target_w=None,
            ev_verb=None,
            ev_max_power_w=None,
            new_peak_risk=False,
            rule_chain=("off",),
            unsupported_verbs=(),
        )

    unsupported: list[str] = []
    rule_chain: list[str] = []

    rules = [
        _rule_peak_shave,
        _rule_battery_floor,
        _rule_ev_coupling,
        _rule_export_curtail,
        _rule_proactive_charge,
        _rule_solar,
        _rule_expensive,
    ]

    for rule in rules:
        candidate = rule(inputs)
        if candidate is None:
            rule_chain.append(f"skip_{rule.__name__[6:]}")
            continue
        rule_chain.append(candidate.strategy)
        resolved = _resolve_unsupported(candidate, inputs.mapping, unsupported)
        if resolved is not None:
            base = resolved
            break
    else:
        base = _rule_fallback(inputs)
        rule_chain.append("fallback")

    final = _ev_overlay(inputs, base)

    return Decision(
        strategy=final.strategy,
        inverter_verb=final.inverter_verb,
        inverter_power_target_w=final.inverter_power_target_w,
        ev_verb=final.ev_verb,
        ev_max_power_w=final.ev_max_power_w,
        new_peak_risk=_peak_risk(inputs),
        rule_chain=tuple(rule_chain),
        unsupported_verbs=tuple(unsupported),
    )


def recompute_peak_shave_power(
    prior: Decision,
    grid_import_w: float,
    grid_import_15m_avg_w: float | None,
    house_load_non_ev_w: float,
    thresholds: Thresholds,
) -> tuple[float, float]:
    """Fast-path helper for the 1 s peak-shave loop.
    Returns (inverter_power_target_w, ev_max_power_w), both non-negative."""
    cap = thresholds.max_grid_import_w
    batt_max = thresholds.battery_max_discharge_w

    # How much surplus power the battery discharge provides
    surge = max(0.0, grid_import_w - cap)
    inverter_power_w = min(batt_max, surge) if surge > 0 else (prior.inverter_power_target_w or 0.0)

    # EV cap: what the EV is allowed to draw without breaching the grid cap
    ev_cap = max(0.0, cap + batt_max - house_load_non_ev_w)

    return inverter_power_w, ev_cap


# ── hysteresis ────────────────────────────────────────────────────────────────

def _peak_risk(inp: Inputs) -> bool:
    """Hysteresis: on at cap − headroom, off at cap − headroom − 200."""
    avg = inp.grid_import_15m_avg_w
    if avg is None:
        return False
    cap = inp.thresholds.max_grid_import_w
    headroom = inp.thresholds.peak_shave_headroom_w
    on_threshold = cap - headroom
    off_threshold = cap - (headroom + 200)

    if inp.peak_risk_latched:
        return avg > off_threshold
    return avg >= on_threshold


# ── rule functions ─────────────────────────────────────────────────────────────

def _rule_peak_shave(inp: Inputs) -> Decision | None:
    if not inp.features.peak_shaving:
        return None
    if inp.grid_import_15m_avg_w is None:
        return None
    if not _peak_risk(inp):
        return None
    if inp.battery_soc <= inp.thresholds.battery_reserve_soc:
        return None

    cap = inp.thresholds.max_grid_import_w
    batt_max = inp.thresholds.battery_max_discharge_w
    surge = max(0.0, inp.grid_import_w - cap)
    power_target = min(batt_max, surge) if surge > 0 else batt_max

    house_non_ev = (
        inp.house_load_non_ev_w
        if inp.house_load_non_ev_w is not None
        else (inp.house_load_w - inp.ev_power_w)
    )
    ev_cap = max(0.0, cap + batt_max - house_non_ev)

    return Decision(
        strategy="peak_shave",
        inverter_verb=InverterVerb.FORCE_DISCHARGE,
        inverter_power_target_w=power_target,
        ev_verb=None,  # ev_overlay will decide
        ev_max_power_w=ev_cap,
        new_peak_risk=True,
        rule_chain=(),
        unsupported_verbs=(),
    )


def _rule_battery_floor(inp: Inputs) -> Decision | None:
    if inp.battery_soc <= inp.thresholds.battery_reserve_soc:
        return Decision(
            strategy="battery_floor",
            inverter_verb=InverterVerb.IDLE,
            inverter_power_target_w=None,
            ev_verb=None,
            ev_max_power_w=None,
            new_peak_risk=False,
            rule_chain=(),
            unsupported_verbs=(),
        )
    return None


def _rule_ev_coupling(inp: Inputs) -> Decision | None:
    if inp.ev_power_w >= 100:
        return Decision(
            strategy="ev_charging_coupling",
            inverter_verb=InverterVerb.SELF_USE,
            inverter_power_target_w=None,
            ev_verb=None,
            ev_max_power_w=None,
            new_peak_risk=False,
            rule_chain=(),
            unsupported_verbs=(),
        )
    return None


def _rule_export_curtail(inp: Inputs) -> Decision | None:
    if not inp.features.export_curtail:
        return None
    if inp.battery_soc < inp.thresholds.battery_full_soc:
        return None
    if inp.pv_w <= 0:
        return None
    if inp.export_price is None:
        return None
    if inp.export_price > inp.thresholds.export_curtail_price:
        return None

    solar_surplus = max(0.0, inp.pv_w - inp.house_load_w)
    if solar_surplus <= 0:
        return None

    # Pre-set EV verb when absorption is enabled
    ev_verb: EvVerb | None = None
    if (
        inp.features.ev_absorbs_curtail
        and inp.ev_plugged
        and (inp.ev_soc is None or inp.ev_soc < inp.thresholds.ev_target_soc)
    ):
        ev_verb = EvVerb.CHARGE_NOW

    return Decision(
        strategy="export_curtail",
        inverter_verb=InverterVerb.LIMIT_EXPORT,
        inverter_power_target_w=max(0.0, inp.house_load_w),
        ev_verb=ev_verb,
        ev_max_power_w=None,
        new_peak_risk=False,
        rule_chain=(),
        unsupported_verbs=(),
    )


def _rule_proactive_charge(inp: Inputs) -> Decision | None:
    if not inp.features.proactive_grid_charge:
        return None
    if inp.import_price is None:
        return None
    if inp.import_price >= inp.thresholds.proactive_grid_charge_price:
        return None
    if inp.battery_soc >= inp.thresholds.battery_target_soc_sunset:
        return None
    solar_surplus = max(0.0, inp.pv_w - inp.house_load_w)
    if solar_surplus > 0:
        return None

    return Decision(
        strategy="proactive_charge",
        inverter_verb=InverterVerb.FORCE_CHARGE,
        inverter_power_target_w=None,  # coordinator/controller uses battery_max
        ev_verb=None,
        ev_max_power_w=None,
        new_peak_risk=False,
        rule_chain=(),
        unsupported_verbs=(),
    )


def _rule_solar(inp: Inputs) -> Decision | None:
    solar_surplus = inp.pv_w - inp.house_load_w
    if solar_surplus < inp.thresholds.solar_surplus_threshold_w:
        return None

    if inp.battery_soc >= inp.thresholds.battery_full_soc and inp.ev_plugged:
        return Decision(
            strategy="solar_to_ev",
            inverter_verb=InverterVerb.SELF_USE,
            inverter_power_target_w=None,
            ev_verb=EvVerb.SOLAR_ONLY,
            ev_max_power_w=None,
            new_peak_risk=False,
            rule_chain=(),
            unsupported_verbs=(),
        )

    return Decision(
        strategy="solar_to_battery",
        inverter_verb=InverterVerb.SELF_USE,
        inverter_power_target_w=None,
        ev_verb=None,
        ev_max_power_w=None,
        new_peak_risk=False,
        rule_chain=(),
        unsupported_verbs=(),
    )


def _rule_expensive(inp: Inputs) -> Decision | None:
    if not inp.is_daytime:
        return None
    if inp.import_price is None or inp.avg_24h_price is None:
        return None
    threshold = inp.avg_24h_price * inp.thresholds.expensive_price_multiplier
    if inp.import_price <= threshold:
        return None

    return Decision(
        strategy="expensive_self_use",
        inverter_verb=InverterVerb.SELF_USE,
        inverter_power_target_w=None,
        ev_verb=None,
        ev_max_power_w=None,
        new_peak_risk=False,
        rule_chain=(),
        unsupported_verbs=(),
    )


def _rule_fallback(inp: Inputs) -> Decision:
    return Decision(
        strategy="fallback",
        inverter_verb=InverterVerb.SELF_USE,
        inverter_power_target_w=None,
        ev_verb=None,
        ev_max_power_w=None,
        new_peak_risk=False,
        rule_chain=(),
        unsupported_verbs=(),
    )


# ── EV overlay ────────────────────────────────────────────────────────────────

def _ev_overlay(inp: Inputs, base: Decision) -> Decision:
    """Adds ev_verb after inverter verb is chosen.
    peak_shave and export_curtail may pre-set ev_verb; overlay respects that."""
    # If EV verb was already forced by a rule (e.g. export_curtail), keep it
    if base.ev_verb is not None:
        return base

    # Off / unplugged / satisfied / scheduling disabled
    if (
        inp.ev_strategy == "Off"
        or not inp.ev_plugged
        or (inp.ev_soc is not None and inp.ev_soc >= inp.thresholds.ev_target_soc)
        or not inp.features.ev_scheduling
    ):
        return Decision(
            strategy=base.strategy,
            inverter_verb=base.inverter_verb,
            inverter_power_target_w=base.inverter_power_target_w,
            ev_verb=EvVerb.IDLE,
            ev_max_power_w=base.ev_max_power_w,
            new_peak_risk=base.new_peak_risk,
            rule_chain=base.rule_chain,
            unsupported_verbs=base.unsupported_verbs,
        )

    if inp.in_cheap_window:
        ev_verb = EvVerb.CHARGE_NOW
    else:
        solar_surplus = inp.pv_w - inp.house_load_w
        if (
            solar_surplus >= inp.thresholds.solar_surplus_threshold_w
            and inp.battery_soc >= inp.thresholds.battery_full_soc
        ):
            ev_verb = EvVerb.SOLAR_ONLY
        else:
            ev_verb = EvVerb.IDLE

    return Decision(
        strategy=base.strategy,
        inverter_verb=base.inverter_verb,
        inverter_power_target_w=base.inverter_power_target_w,
        ev_verb=ev_verb,
        ev_max_power_w=base.ev_max_power_w,
        new_peak_risk=base.new_peak_risk,
        rule_chain=base.rule_chain,
        unsupported_verbs=base.unsupported_verbs,
    )


# ── unsupported verb fallthrough ──────────────────────────────────────────────

def _resolve_unsupported(
    d: Decision, mapping: VerbMapping, accumulator: list[str]
) -> Decision | None:
    """If d.inverter_verb maps to None in mapping.inverter_options,
    appends 'mode_unsupported_<verb>' to accumulator and returns None
    so the rule loop falls through."""
    if d.inverter_verb is None:
        return d
    option = mapping.inverter_options.get(d.inverter_verb)
    if option is None and d.inverter_verb in mapping.inverter_options:
        # Explicitly mapped to None = unsupported
        accumulator.append(f"mode_unsupported_{d.inverter_verb.value}")
        return None
    return d
