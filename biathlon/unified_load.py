"""Integrate one HR/speed choice per supported interval, preserving unknown time."""
import math

from . import speed_equivalence
from .sport_heart_rate import reference_offset

VERSION = "unified-leading-indicator-load-v3-explicit-speed-validity"
HR_INTERVAL_VERSION = "normalized-hr-recording-intervals-v1"
LEGACY_SHORT_SMART_RECORDING_S = 5.
LEGACY_EXTENDED_SMART_RECORDING_S = 10.


def finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _smart_span(row, following):
    """Keep validated recording spans; never extend over arbitrary gaps."""
    if following is None:
        return False
    dt = following["elapsed_s"]-row["elapsed_s"]
    if "hr_interval_classification" in row:
        return (row["hr_interval_classification"] == "smart_recording"
                and row.get("hr_interval_end_s") == following["elapsed_s"]
                and 1. < dt <= LEGACY_EXTENDED_SMART_RECORDING_S)
    if dt <= 1. or dt > LEGACY_EXTENDED_SMART_RECORDING_S:
        return False
    # Older pinned runs retain original timestamps but no interval annotation.
    # Apply the normalizer's conservative short/extended recording rules.
    if any("recording_stop" in str(flag) for r in (row, following) for flag in r.get("quality_flags", ())):
        return False
    if 1. < dt <= LEGACY_SHORT_SMART_RECORDING_S:
        return True
    return (LEGACY_SHORT_SMART_RECORDING_S < dt <= LEGACY_EXTENDED_SMART_RECORDING_S
            and all(finite(r.get("speed_raw_kmh")) and r["speed_raw_kmh"] > 0 for r in (row, following)))


def integrate(shadow, *, bounds, sport, parameters=None, leading_indicator="HEART_RATE", duration_s=None):
    if leading_indicator not in {"HEART_RATE", "SPEED"}:
        raise ValueError("Unsupported leading indicator")
    hr_rows = sorted((r for r in shadow.get("timeseries", ()) if finite(r.get("elapsed_s"))),
                     key=lambda r: r["elapsed_s"])
    speed_rows = shadow.get("speed_test_series")
    speed_rows = sorted((r for r in (speed_rows if speed_rows is not None else hr_rows)
                         if finite(r.get("elapsed_s"))), key=lambda r: r["elapsed_s"])
    hr, speeds = [], []
    offset = reference_offset(sport)
    calibrated_bounds = (shadow.get("trainability_index") or {}).get("zone_bounds_bpm")
    adjusted_hr_compatible = calibrated_bounds is None or list(calibrated_bounds) == list(bounds)
    previous = 0.
    for i, row in enumerate(hr_rows):
        following = hr_rows[i+1] if i+1 < len(hr_rows) else None
        left = max(0., row["elapsed_s"], previous)
        smart = _smart_span(row, following)
        right = row["elapsed_s"]+1.
        if following is not None:
            right = following["elapsed_s"] if smart else min(right, following["elapsed_s"])
        if right <= left:
            continue
        raw = row.get("hr_raw_bpm")
        # Derived HR never makes a missing raw observation become measured.
        value = row.get("hrmod_final_bpm") if adjusted_hr_compatible else None
        value = value if finite(value) and value > 0 else raw
        estimate = speed_equivalence.evaluate_hr(parameters, value, bounds=bounds, offset_bpm=offset) if finite(raw) and raw > 0 else None
        if (row.get("hr_interval_classification") not in {None, "original_1hz", "smart_recording"}
                and following is not None):
            estimate = None
        if smart:
            next_raw = following.get("hr_raw_bpm")
            next_value = following.get("hrmod_final_bpm") if adjusted_hr_compatible else None
            next_value = next_value if finite(next_value) and next_value > 0 else next_raw
            if estimate and finite(next_raw) and next_raw > 0 and finite(next_value):
                cuts = [0., 1.]
                if next_value != value:
                    cuts += [(edge-offset-value)/(next_value-value) for edge in bounds
                             if 0. < (edge-offset-value)/(next_value-value) < 1.]
                cuts.sort()
                for start, end in zip(cuts, cuts[1:]):
                    hr.append((left+start*(right-left), left+end*(right-left),
                               {"_linear_hr": (value+start*(next_value-value), value+end*(next_value-value))}))
            else:
                hr.append((left, right, None))
        else:
            hr.append((left, right, estimate))
        previous = right
    previous = 0.
    for i, row in enumerate(speed_rows):
        t, dt = row["elapsed_s"], row.get("dt_s")
        independent = shadow.get("speed_test_series") is not None
        if independent:
            if not finite(dt) or not 0 < dt <= 10:
                continue
            left, right = max(0., t-dt, previous), t
        else:
            left, right = max(0., t, previous), t+1.
        if right <= left:
            continue
        value = row.get("load_speed_kmh")
        eligible = row.get("load_status") in {"ACTIVE", "REST"} and finite(value) and value >= 0
        if "load_status" not in row:
            # Legacy rows have no load annotation. An explicit unavailable
            # result (for example a negative descent residual) must never be
            # replaced by a Vflat value rejected for load accounting.
            value = row.get("vflat_b65_kmh")
            eligible = finite(value) and value >= 0 and not row.get("exclusion_reason")
        estimate = None
        if eligible and parameters:
            if value == 0:
                estimate = {"status": "REST", "zone": None, "coefficient": 0.}
            else:
                estimate = speed_equivalence.evaluate(parameters, value)
                if estimate.get("reason") == "ABOVE_V10_UPDATE_ANCHOR":
                    # Retain observed time. Saturation is explicit and never
                    # promoted to a measured maximum or calibration point.
                    top = parameters["zones"][-1]["high_kmh"]
                    estimate = {**speed_equivalence.evaluate(parameters, top), "saturated_above_v10": True}
        speeds.append((left, right, estimate))
        previous = right
    endpoints = {0.}
    for left, right, _ in (*hr, *speeds):
        endpoints.update((left, right))
    if finite(duration_s) and duration_s > 0:
        # The final timestamp is a sample at the end of the recording, not
        # an extra second after it. Preserve pauses inside the elapsed window.
        endpoints = {t for t in endpoints if t <= duration_s}
        endpoints.add(float(duration_s))
    points = sorted(endpoints)
    totals = {z: {"zone": z, "raw_time_min": 0., "equivalent_time_min": 0.} for z in speed_equivalence.ZONES}
    sources = {"HEART_RATE": 0., "SPEED": 0.}
    fallback = rest = unknown = saturated = 0.
    hi = si = 0
    for left, right in zip(points, points[1:]):
        if right <= left:
            continue
        while hi < len(hr) and hr[hi][1] <= left:
            hi += 1
        while si < len(speeds) and speeds[si][1] <= left:
            si += 1
        h = hr[hi][2] if hi < len(hr) and hr[hi][0] <= left < hr[hi][1] else None
        if h and "_linear_hr" in h:
            start, end = h["_linear_hr"]
            fraction = ((left+right)/2-hr[hi][0])/(hr[hi][1]-hr[hi][0])
            h = speed_equivalence.evaluate_hr(parameters, start+fraction*(end-start),
                bounds=bounds, offset_bpm=offset)
        s = speeds[si][2] if si < len(speeds) and speeds[si][0] <= left < speeds[si][1] else None
        order = (("HEART_RATE", h), ("SPEED", s)) if leading_indicator == "HEART_RATE" else (("SPEED", s), ("HEART_RATE", h))
        chosen = next(((kind, value) for kind, value in order if value and value.get("status") in {"AVAILABLE", "REST"}), None)
        dt = right-left
        if not chosen:
            unknown += dt
            continue
        kind, value = chosen
        sources[kind] += dt
        if kind != leading_indicator:
            fallback += dt
        if value["status"] == "REST":
            rest += dt
        else:
            row = totals[value["zone"]]
            row["raw_time_min"] += dt/60.
            row["equivalent_time_min"] += dt/60.*value["coefficient"]
            if value.get("saturated_above_v10"):
                saturated += dt
    recorded = points[-1] if points else 0.
    active = math.fsum(r["raw_time_min"]*60 for r in totals.values())
    return {"version": VERSION, "equivalence_version": speed_equivalence.VERSION,
            "leading_indicator": leading_indicator, "zones": list(totals.values()),
            "recorded_seconds": recorded, "active_seconds": active,
            "rest_seconds": rest, "unknown_seconds": unknown, "fallback_seconds": fallback,
            "source_seconds": sources, "saturated_above_v10_seconds": saturated,
            "hr_coordinate": "CURRENT_HRMOD_WHEN_AVAILABLE" if adjusted_hr_compatible else "RAW_HR_SETTINGS_CHANGED",
            "profile_version": (parameters or {}).get("parameters_version"),
            "calibration_eligible": False}


def apply_to_summary(summary, shadow, context, *, bounds, sport, leading_indicator, duration_s=None):
    """Replace direct Q once while retaining the causal Tref diagnostics."""
    from .component_load import calculate_component_load
    ledger = integrate(shadow, bounds=bounds, sport=sport,
        parameters=context.get("comparison_parameters"), leading_indicator=leading_indicator, duration_s=duration_s)
    ledger["capacity_context"] = {k: context[k] for k in ("minutes", "sources", "references", "fingerprint") if k in context}
    direct = {r["zone"]: r["equivalent_time_min"] for r in ledger["zones"]}
    component = calculate_component_load(direct, context.get("minutes"), capacity_sources=context.get("sources"))
    comparison = summary.get("shadow_model_comparison") or {}
    old = {r["zone"]: r for r in (comparison.get("experimental") or {}).get("rows", ())}
    rows = []
    for row in ledger["zones"]:
        zone = row["zone"]
        rows.append({**old.get(zone, {}), "zone": zone, "T_z": row["raw_time_min"],
            "T_eq_z": row["equivalent_time_min"], "E_z": component["effective"][zone],
            "direct_ratio": component["direct_ratio"][zone],
            "cascade": component["downward_received"][zone],
            "spillover_received": component["spill_received"][zone],
            "tmax_minutes": component["capacity_minutes"][zone],
            "tmax_source": component["capacity_sources"][zone],
            "mean_effective_hr_bpm": None, "average_minute_value_percent": None})
    comparison["experimental"] = {**(comparison.get("experimental") or {}), "rows": rows,
                                  "equivalence_version": speed_equivalence.VERSION}
    comparison.setdefault("baseline", {"rows": []})
    summary["shadow_model_comparison"] = comparison
    summary["unified_load"] = ledger
    summary["model_status"] = {"status": "limited" if ledger["unknown_seconds"] > 1e-6 else "valid",
                               "reason": "Общ товар от избрания водещ показател и наличните измервания."}
    summary.setdefault("onflows_load_analysis", {})["available"] = True
    return ledger
