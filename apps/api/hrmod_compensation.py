"""Preserve mirror additions; compensate from the lowest observed HR zones.

This is an explicit accounting policy, not an oxygen-debt estimate. Wave and
terrain detection remain unchanged. Compensation spans one recording only and
uses the same supported intervals as the production HR load ledger.
"""
from copy import deepcopy
from hashlib import sha256
import json
import math

import numpy as np

from biathlon.unified_load import finite, observed_hr_valid, _smart_span, hr_zone_times
from hrmod_lab.schemas import MODEL_VERSION as MIRROR_MODEL_VERSION

MODEL_VERSION = "hrmod_low_zone_compensation_v10"
MAX_REMOVAL_BPM = 30.0
AREA_TOLERANCE = 1e-6


def interval_weights(rows, duration_s=None):
    """Exact integral weights: held 1 Hz HR; trapezoids for smart recording.

    Pauses, missing HR and unsupported gaps never fund compensation. The final
    observation does not invent time after the end of the recording.
    """
    weights = np.zeros(len(rows), dtype=float)
    duration = duration_s if finite(duration_s) and duration_s > 0 else (
        rows[-1]["elapsed_s"] if rows else 0.)
    previous = 0.
    for i, row in enumerate(rows):
        following = rows[i+1] if i+1 < len(rows) else None
        left = max(0., row["elapsed_s"], previous)
        smart = _smart_span(row, following)
        right = row["elapsed_s"]+1.
        if following is not None:
            right = following["elapsed_s"] if smart else min(right, following["elapsed_s"])
        previous = right
        supported = (row.get("hr_interval_classification") in {None, "original_1hz", "smart_recording"}
                     or following is None)
        if (not supported or not observed_hr_valid(row) or not finite(row.get("hr_clean_bpm"))
                or min(right, duration) <= left):
            continue
        dt = min(right, duration)-left
        if smart:
            if not observed_hr_valid(following) or not finite(following.get("hr_clean_bpm")):
                continue
            fraction = dt/(right-left)
            weights[i] += dt*(1.-fraction/2.)
            weights[i+1] += dt*fraction/2.
        else:
            weights[i] += dt
    return weights


def allocate(clean, previous_final, weights, bounds, *, max_removal=MAX_REMOVAL_BPM):
    """Allocate proportionally to capacity within each ORIGINAL zone, low first.

    Receivers never fund their own additions. If all zones lack capacity,
    proportionally limit additions and report it; never exceed removal/floor
    limits or silently violate area balance.
    """
    clean, previous_final, weights = (np.asarray(v, dtype=float) for v in (clean, previous_final, weights))
    if (clean.shape != previous_final.shape or clean.shape != weights.shape or clean.ndim != 1
            or len(bounds) != 6 or not all(finite(v) for v in bounds)
            or any(a >= b for a, b in zip(bounds, bounds[1:]))
            or not finite(max_removal) or max_removal < 0
            or not np.all(np.isfinite(weights)) or np.any(weights < 0)):
        raise ValueError("Invalid HR compensation inputs")
    supported = np.isfinite(clean) & np.isfinite(previous_final) & (weights > 0)
    added = np.where(supported, np.maximum(0., previous_final-clean), 0.)
    # Sub-picobpm allocator noise must not turn an unchanged sample into a receiver.
    added[added < 1e-8] = 0.
    target = float(np.dot(added, weights))
    removed = np.zeros(len(clean))
    eligible = supported & (added == 0.)
    remaining = target
    donors = []
    for i, (low, high) in enumerate(zip(bounds, bounds[1:])):
        mask = eligible & (clean >= low) & ((clean < high) if i < 4 else (clean <= high))
        caps = np.where(mask, np.minimum(max_removal, np.maximum(0., clean-bounds[0])), 0.)
        capacity = float(np.dot(caps, weights))
        take = min(remaining, capacity)
        if capacity > 0 and take > 0:
            removed += caps*(take/capacity)
        donors.append({"zone": f"Z{i+1}", "available_area_bpm_s": capacity,
                       "removed_area_bpm_s": take})
        remaining = max(0., remaining-take)
    funded = target-remaining
    scale = funded/target if target > 0 else 1.
    added *= scale
    final = previous_final.copy()
    final[supported] = clean[supported]+added[supported]-removed[supported]
    added_area = float(np.dot(added, weights))
    removed_area = float(np.dot(removed, weights))
    error = added_area-removed_area
    if abs(error) > AREA_TOLERANCE:
        raise ArithmeticError("HR compensation area balance failed")
    return final, {
        "version": MODEL_VERSION, "scope": "WHOLE_RECORDING", "priority": "ORIGINAL_ZONE_ASCENDING",
        "floor_bpm": float(bounds[0]), "max_removal_bpm": max_removal,
        "requested_added_area_bpm_s": target, "added_area_bpm_s": added_area,
        "removed_area_bpm_s": removed_area, "area_balance_error_bpm_s": error,
        "addition_scale": scale, "capacity_limited": remaining > AREA_TOLERANCE,
        "unfunded_area_bpm_s": remaining, "donors": donors,
        "time_basis": "SHARED_HR_RECORDING_INTERVALS",
    }


def compensate(rows, hrmod, *, bounds, sport, duration_s=None, hrmax_bpm=None):
    """Finalize combined rows before TI, summaries, hashes and load integration."""
    result = deepcopy(hrmod)
    result["model_version"] = MODEL_VERSION
    for row in rows:
        row["hrmod_model_version"] = MODEL_VERSION
    if hrmod.get("status") != "computed":
        return result
    from biathlon.sport_heart_rate import reference_offset
    raw_bounds = [b-reference_offset(sport) for b in bounds]
    clean = [r.get("hr_clean_bpm") if finite(r.get("hr_clean_bpm")) else np.nan for r in rows]
    prior = [r.get("hrmod_final_bpm") if finite(r.get("hrmod_final_bpm")) else np.nan for r in rows]
    weights = interval_weights(rows, duration_s)
    final, accounting = allocate(clean, prior, weights, raw_bounds)
    for i, row in enumerate(rows):
        if not math.isfinite(final[i]) or not math.isfinite(clean[i]):
            continue
        row["hrmod_final_bpm"] = float(final[i])
        row["hrmod_delta_bpm"] = float(final[i]-clean[i])
        row["added_bpm"] = max(0., row["hrmod_delta_bpm"])
        row["removed_bpm"] = max(0., -row["hrmod_delta_bpm"])
        row["receiver_flag"] = row["added_bpm"] > 1e-8
        row["donor_flag"] = row["removed_bpm"] > 1e-8
        flags = set(row.get("model_flags") or ())
        if row["donor_flag"]:
            flags.add("LOW_ZONE_COMPENSATION")
        row["model_flags"] = sorted(flags)
    diagnostics = result.setdefault("diagnostics", {})
    # Wave tables describe the unchanged amplitude estimator, not final donors.
    diagnostics["mirror_model_version"] = MIRROR_MODEL_VERSION
    diagnostics["mirror_fraction_at_hrmax"] = diagnostics.get("fraction_at_hrmax")
    raw_hrmax = (hrmax_bpm if finite(hrmax_bpm) else bounds[-1])-reference_offset(sport)
    valid = np.isfinite(final) & (weights > 0)
    valid_seconds = float(weights[valid].sum())
    diagnostics["fraction_at_hrmax"] = (float(weights[valid & (final >= raw_hrmax-1e-9)].sum())
        / valid_seconds if valid_seconds else 0.)
    diagnostics["compensation"] = accounting
    diagnostics.update(total_added_area_bpm_s=accounting["added_area_bpm_s"],
        total_removed_area_bpm_s=accounting["removed_area_bpm_s"],
        total_moved_area_bpm_s=accounting["added_area_bpm_s"], moved_area_bpm_s=accounting["added_area_bpm_s"],
        total_area_balance_error_bpm_s=accounting["area_balance_error_bpm_s"], area_conservation_passed=True,
        max_added_bpm=max((r.get("added_bpm") or 0. for r in rows), default=0.),
        max_removed_bpm=max((r.get("removed_bpm") or 0. for r in rows), default=0.),
        capacity_limited=accounting["capacity_limited"] or diagnostics.get("capacity_limited", False))
    flags = set(diagnostics.get("flags") or ()) | {"LOW_ZONE_COMPENSATION", "AREA_CONSERVATION_PASSED"}
    if accounting["capacity_limited"]:
        flags.add("COMPENSATION_CAPACITY_LIMITED")
    diagnostics["flags"] = sorted(flags)
    for wave in result.get("waves", ()):
        wave["area_accounting_scope"] = "MIRROR_ESTIMATOR_BEFORE_GLOBAL_COMPENSATION"
    result["zones"] = hr_zone_times({"timeseries": rows}, bounds=bounds, sport=sport,
                                    duration_s=duration_s)["rows"]
    result["final_result_hash"] = sha256(json.dumps({"prior": hrmod.get("final_result_hash"),
        "compensation": accounting, "final": [r.get("hrmod_final_bpm") for r in rows]},
        sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()
    return result
