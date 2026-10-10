"""Conservative learning of confirmed session completion, not training efficacy.

Beta posteriors describe finishing the intended, comparable dose. A method can
only influence selection within the observed sport, purpose and duration range.
These associations never establish that a method caused a fitness improvement.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from hashlib import sha256
import json
from math import isfinite

from fastapi import HTTPException
from scipy.stats import beta

from biathlon.training_methods import VERSION as CATALOG_VERSION, resolved_methods
from .response_monitoring import latest_entries

VERSION = "confirmed-method-tolerability-v1"
MAX_OBSERVATIONS, RETENTION_DAYS, MIN_OBSERVATIONS = 1000, 730, 4
MAX_MEMORY_BYTES = 64 * 1024
MAX_INTERVAL_WIDTH = .45


def _hash(value):
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


def _number(value):
    return float(value) if isinstance(value, (float, int)) and not isinstance(value, bool) and isfinite(value) else None


def method_descriptor(method, sport):
    """Freeze a server-resolved catalog definition, including personal profiles.

    Call before the planner mutates capacity/position/minimum dose. The full
    definition hash intentionally invalidates evidence after profile changes.
    """
    return {"id": method["id"], "zone": method["zone"], "sport": sport,
            "structure": method["structure"], "purpose": method["purpose"],
            "version": f"{CATALOG_VERSION}:{_hash(method)[:20]}",
            "catalog_version": CATALOG_VERSION, "position": method.get("position")}


def _methods(repository, alias):
    from .management_store import ManagementStore
    profile = ManagementStore(repository).profile(alias)
    return resolved_methods(profile.get("profile") or {})


def execution_catalog(repository, alias):
    return [{"id": m["id"], "title": m["title"], "zone": m["zone"],
             "sports": list(m["sports"]) + (["WeightTraining"] if m["zone"] == "STR" else [])}
            for m in _methods(repository, alias)]


def confirmed_descriptor(repository, alias, report, activity):
    """Never accept a descriptor or an inferred plan/activity link from clients."""
    if not report.method_confirmed:
        return None
    method = next((m for m in _methods(repository, alias) if m["id"] == report.executed_method_id), None)
    sport = activity.get("sport")
    if method is None or (sport not in method["sports"] and not (sport == "WeightTraining" and method["zone"] == "STR")):
        raise HTTPException(422, "Choose an available method for the actual activity sport")
    return method_descriptor(method, sport)


def _activity_map(source):
    rows = [*source.get("activities", []), *(source.get("calendar", {}).get("activities", []))]
    # Supplement the load snapshot with calendar metadata only by exact ref.
    result = {}
    for row in rows:
        key = row.get("activity_ref")
        if key:
            result[key] = {**result.get(key, {}), **row}
    return result


def _exclusions(selected, history, today):
    excluded = set()
    for (kind, key), entry in selected.items():
        p = entry["payload"]
        if kind == "DAILY" and p.get("pain_or_illness"):
            # Original DAILY records used the entry key as their date.
            try:
                day = date.fromisoformat(p.get("day") or key)
            except (TypeError, ValueError):
                continue
            if day <= today:
                excluded.add(day.isoformat())
    for day in (history or {}).get("days", []):
        if ((day.get("body_observations") or {}).get("context_status") == "REVIEW_LAB_REFERENCE"
                or (day.get("manual") or {}).get("pain_or_illness")):
            excluded.add(day["day"])
    # Lab flags are also available when the chart window does not cover a day.
    from .body_observations import lab_reports
    for lab in lab_reports(selected, today):
        if lab["outside_reference"]:
            excluded.add(lab["payload"]["day"])
    return excluded


def _observation(entry, activity, excluded, today):
    p = entry["payload"]
    descriptor = p.get("executed_method")
    if not (p.get("method_confirmed") is True and p.get("execution_comparable") is True
            and isinstance(descriptor, dict) and p.get("executed_method_id") == descriptor.get("id")):
        return None
    required = ("id", "zone", "sport", "structure", "purpose", "version", "catalog_version")
    if any(not isinstance(descriptor.get(k), str) or not descriptor[k] for k in required):
        return None
    day = activity.get("local_date") or activity.get("date")
    try:
        observed = date.fromisoformat(day)
    except (TypeError, ValueError):
        return None
    if (observed > today or observed < today-timedelta(days=RETENTION_DAYS) or day in excluded
            or descriptor["sport"] != activity.get("sport")
            or str(entry.get("recorded_at", ""))[:10] > today.isoformat()):
        return None
    planned, actual = _number(p.get("planned_duration_minutes")), _number(p.get("duration_minutes"))
    rpe = _number(p.get("rpe"))
    if not planned or not actual or not 0 < planned <= 1440 or not 0 < actual <= 1440:
        return None
    # A report must describe the activity actually recorded, with tolerance for
    # paused/device elapsed time. Gross contradictions do not become evidence.
    recorded_duration = _number(activity.get("duration_min"))
    if recorded_duration is None and _number(activity.get("elapsed_time_s")):
        recorded_duration = activity["elapsed_time_s"] / 60
    if recorded_duration and abs(actual-recorded_duration) > max(10, .25*recorded_duration):
        return None
    if rpe is None or not 0 <= rpe <= 10 or p.get("timing") not in ("IMMEDIATE", "DELAYED", "NEXT_DAY"):
        return None
    reason = p.get("execution_reason")
    if reason not in ("AS_PLANNED", "FATIGUE"):
        return None
    planned_speed, executed_speed = _number(p.get("planned_speed_kmh")), _number(p.get("executed_speed_kmh"))
    if (planned_speed is None) != (executed_speed is None):
        return None
    speed_ratio = executed_speed / planned_speed if planned_speed and executed_speed else None
    if reason == "AS_PLANNED" and actual/planned >= .95 and (speed_ratio is None or speed_ratio >= .95):
        success = True
    elif reason == "FATIGUE" and (actual/planned < .90 or (speed_ratio is not None and speed_ratio < .95)):
        success = False
    else:
        # High RPE alone, or fatigue without failure of intended dose, does not
        # mean that the workout method failed.
        return None
    return {"id": entry["entry_key"], "revision": entry["revision"], "payload_hash": _hash(p), "day": day,
            "method": dict(descriptor), "planned_minutes": planned, "actual_minutes": actual,
            "planned_speed_kmh": planned_speed, "rpe": rpe, "timing": p["timing"],
            "success": success, "execution_reason": reason}


def _posterior(rows):
    successes = sum(r["success"] for r in rows)
    failures = len(rows)-successes
    a, b = 2+successes, 2+failures
    low, high = beta.ppf([.025, .975], a, b)
    return {"observations": len(rows), "successes": successes, "failures": failures,
            "mean": a/(a+b), "low": float(low), "high": float(high)}


def _comparable(row, anchor):
    a, b = row["method"], anchor["method"]
    if any(a[k] != b[k] for k in ("sport", "zone", "purpose", "catalog_version")):
        return False
    if not .9 <= row["planned_minutes"]/anchor["planned_minutes"] <= 1.1:
        return False
    # Do not transfer observations between a lower and a higher dose within a
    # broad heart-rate zone, or between timed and unknown-speed prescriptions.
    pa, pb = _number(a.get("position")), _number(b.get("position"))
    if pa is None or pb is None or abs(pa-pb) > .100001:
        return False
    sa, sb = row.get("planned_speed_kmh"), anchor.get("planned_speed_kmh")
    if (sa is None) != (sb is None) or (sa is not None and not .95 <= sa/sb <= 1.05):
        return False
    return row["timing"] == anchor["timing"]


def _memory_window(observations, *, previously_limited=False):
    """Fit precisely the newest persisted window, before fitting preferences.

    Budget the whole memory with the larger ASCII/default-spaced JSON encoding;
    compact UTF-8 persistence also fits. Outcomes never affect inclusion. An
    individually oversized record is excluded; otherwise keep a newest suffix,
    without filling leftover bytes with selectively smaller older records.
    """
    def size(value):
        return len(json.dumps(value, ensure_ascii=True, sort_keys=True, allow_nan=False).encode("utf-8"))

    rows = sorted(observations.values(), key=lambda r: (r["day"], r["id"]))
    memory = {"version": VERSION, "observations": [], "size_limited": False}
    used, kept, oversized = size(memory), [], 0
    for row in reversed(rows[-MAX_OBSERVATIONS:]):
        try:
            # Reserve identical worst-case space for outcome-dependent fields.
            # A failure's longer boolean or a completed session's duration must
            # not decide which observation falls over the byte boundary.
            budgeted = {**row, "success": False, "execution_reason": "AS_PLANNED",
                        "actual_minutes": "0" * 24, "rpe": 10.0}
            cost = max(size(row), size(budgeted))
        except (TypeError, ValueError):
            oversized += 1
            continue
        if size(memory) + cost > MAX_MEMORY_BYTES:
            oversized += 1
            continue
        extra = cost + (2 if kept else 0)
        if used + extra > MAX_MEMORY_BYTES:
            break
        used += extra
        kept.append(row)
    memory["observations"] = list(reversed(kept))
    dropped = len(rows)-len(kept)
    memory["size_limited"] = bool(previously_limited or dropped)
    return memory, dropped, oversized, size(memory)


def assess_methods(*, entries, source, today, history=None, retained=None):
    """A bounded, revision-aware archive plus neutral-by-default preferences."""
    selected = latest_entries(entries)
    activities = _activity_map(source)
    excluded = _exclusions(selected, history, today)
    observations = {}
    retained = retained or {}
    if retained.get("version") == VERSION:
        for row in retained.get("observations", [])[-MAX_OBSERVATIONS:]:
            try:
                day = date.fromisoformat(row["day"])
                valid = (type(row["success"]) is bool and row["method"]["catalog_version"] == CATALOG_VERSION
                         and row["id"] not in activities and row["day"] not in excluded
                         and today-timedelta(days=RETENTION_DAYS) <= day < today-timedelta(days=89)
                         and row["planned_minutes"] > 0 and row["actual_minutes"] > 0)
            except (KeyError, TypeError, ValueError):
                continue
            latest = selected.get(("SESSION", row["id"]))
            if latest is not None:
                valid = valid and latest["revision"] == row["revision"] and _hash(latest["payload"]) == row["payload_hash"]
            if valid:
                observations[row["id"]] = dict(row)
    for (kind, key), entry in selected.items():
        if kind != "SESSION" or key not in activities:
            continue
        observation = _observation(entry, activities[key], excluded, today)
        if observation:
            observations[key] = observation
    memory, dropped, oversized, memory_bytes = _memory_window(observations,
        previously_limited=retained.get("version") == VERSION and retained.get("size_limited", False))
    rows = memory["observations"]
    grouped = defaultdict(list)
    for row in rows:
        m = row["method"]
        grouped[(m["id"], m["sport"], m["version"])].append(row)
    preferences = []
    for own_rows in grouped.values():
        anchor = own_rows[-1]
        method = anchor["method"]
        # Retain older observations for inspection; only the current duration
        # neighborhood receives a preference. Never pool 5- and 60-minute doses.
        cohort = [r for r in own_rows if _comparable(r, anchor)]
        peers = [r for r in rows if r["method"]["id"] != method["id"] and _comparable(r, anchor)]
        stats, other = _posterior(cohort), _posterior(peers)
        delta, confidence = 0., "LOW"
        reason = "Недостатъчно потвърдени изпълнения при сходна доза."
        if len(cohort) >= MIN_OBSERVATIONS:
            reason = "Няма достатъчно съпоставими алтернативни методи."
            if len(peers) >= MIN_OBSERVATIONS:
                reason = "Неопределеността остава висока; предпочитанието е неутрално."
                narrow = all(v["high"]-v["low"] <= MAX_INTERVAL_WIDTH for v in (stats, other))
                separated = stats["low"] > other["high"] or stats["high"] < other["low"]
                if narrow and separated:
                    delta = max(-.25, min(.25, .5*(stats["mean"]-other["mean"])))
                    confidence = "HIGH" if min(len(cohort), len(peers)) >= 20 and stats["high"]-stats["low"] <= .25 else "MEDIUM"
                    reason = ("По-често завършена предвидена доза при сходни условия." if delta > 0 else
                              "По-често прекъсната предвидена доза поради умора при сходни условия.")
        preferences.append({"method_id": method["id"], "sport": method["sport"], "component": method["zone"],
                            "purpose": method["purpose"], "version": method["version"],
                            "score_delta": round(delta, 6), "reason": reason, "confidence": confidence,
                            **{k: round(v, 6) if isinstance(v, float) else v for k, v in stats.items()},
                            "duration_min": round(.9*anchor["planned_minutes"], 3),
                            "duration_max": round(1.1*anchor["planned_minutes"], 3),
                            "planned_speed_kmh": anchor.get("planned_speed_kmh"),
                            "position": method.get("position"), "comparison_observations": len(peers)})
    return {"version": VERSION, "preferences": sorted(preferences, key=lambda p: (p["sport"], p["method_id"], p["version"])),
            "memory": memory,
            "summary": {"observations": len(rows), "methods": len(preferences),
                        "active_preferences": sum(p["score_delta"] != 0 for p in preferences),
                        "basis": "CONFIRMED_COMPARABLE_COMPLETION", "minimum_observations": MIN_OBSERVATIONS,
                        "memory_bytes": memory_bytes, "memory_max_bytes": MAX_MEMORY_BYTES,
                        "dropped_this_assessment": dropped, "oversized_observations": oversized,
                        "memory_window_reason": "SIZE_LIMIT" if memory["size_limited"] else "AVAILABLE_CONFIRMED_HISTORY",
                        "memory_window_start": rows[0]["day"] if rows else None,
                        "description": "Учи вероятността за изпълнение на предвидената доза; не оценява причинен тренировъчен ефект."}}
