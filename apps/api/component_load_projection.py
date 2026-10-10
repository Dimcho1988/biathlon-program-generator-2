"""Re-evaluate stored direct activity Q without downloading provider streams."""
from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import json
import math

import pandas as pd

from biathlon.component_load import VERSION, calculate_component_load
from biathlon.constants import COMPONENTS, fresh_parameters
from biathlon.physiology import compute_load_statistics, rolling_load_statistics
from .component_load_context import ZONES, context_from_speed_view, read_contexts


class ComponentLoadRefreshRequired(ValueError):
    code = "COMPONENT_LOAD_ACTIVITY_Q_REQUIRED"
    user_message = (
        "Запазената история няма пълни данни за прекия товар на отделните тренировки. "
        "Обновете реалните данни от Intervals.icu, за да се преизчислят натоварването и възстановяването."
    )


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value) or value < 0:
        raise ComponentLoadRefreshRequired("Stored activity Q is incomplete; refresh real data")
    return float(value)


def _fingerprint(value):
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _daily_frame(source):
    rows = source.get("daily") or []
    keys = [(row["date"], row["zone"]) for row in rows]
    if len(set(keys)) != len(keys):
        raise ComponentLoadRefreshRequired("Duplicate daily component history")
    days = sorted({day for day, _ in keys})
    if any({zone for day, zone in keys if day == current} != set(ZONES) for current in days):
        raise ComponentLoadRefreshRequired("Incomplete covered daily component history")
    frame = pd.DataFrame(0., index=pd.to_datetime(days), columns=[f"e_{z}" for z in COMPONENTS])
    frame.index.name = "date"
    for row in rows:
        frame.loc[pd.Timestamp(row["date"]), f"e_{row['zone']}"] = _number(row["effective_load"])
    for row in (source.get("strength") or {}).get("daily", []):
        day = pd.Timestamp(row["date"])
        if day in frame.index:
            frame.loc[day, "e_STR"] = _number(row["effective_load"])
    return frame


def project_history(source, contexts_by_sport):
    """Apply one versioned rule per activity; preserve coverage, raw Q and STR."""
    if not source:
        return source
    original = _daily_frame(source)
    if original.empty:
        return source
    activities = source.get("activities") or []
    covered = {day.date().isoformat() for day in original.index}
    activity_inputs = []
    per_activity = []
    observed_q_days = set()
    for activity in activities:
        day = activity.get("date")
        if day not in covered:
            raise ComponentLoadRefreshRequired("Activity lies outside covered component history")
        zones = activity.get("zones") or []
        if len(zones) != len(ZONES) or {row.get("zone") for row in zones} != set(ZONES):
            raise ComponentLoadRefreshRequired("Stored activity Q is incomplete; refresh real data")
        q = {row["zone"]: _number(row.get("equivalent_time_min")) for row in zones}
        sport = activity.get("sport")
        context = contexts_by_sport.get(sport)
        if context is None:
            raise ValueError(f"Missing component capacity context for {sport}")
        result = calculate_component_load(q, context["minutes"], capacity_sources=context["sources"])
        if any(q.values()):
            observed_q_days.add(day)
        per_activity.append(result["effective"])
        activity_inputs.append({"ref": activity.get("activity_ref"), "date": day, "sport": sport, "q": q})
    for day, row in original.iterrows():
        if any(row[f"e_{z}"] > 0 for z in ZONES) and day.date().isoformat() not in observed_q_days:
            raise ComponentLoadRefreshRequired("Nonzero historical load has no reconstructible activity Q; refresh real data")
    fingerprint = _fingerprint({"version": VERSION, "scope": "ACTIVITY", "days": sorted(covered),
        "activities": activity_inputs, "contexts": {sport: context["fingerprint"] for sport, context in contexts_by_sport.items()}})
    old_model = source.get("component_load_model") or {}
    if old_model.get("version") == VERSION and old_model.get("fingerprint") == fingerprint:
        return source
    out = deepcopy(source)
    daily = original.copy()
    daily[[f"e_{z}" for z in ZONES]] = 0.
    for activity, load in zip(out.get("activities", []), per_activity):
        for row in activity["zones"]:
            row["effective_load"] = load[row["zone"]]
        day = pd.Timestamp(activity["date"])
        for zone in ZONES:
            daily.loc[day, f"e_{zone}"] += load[zone]
    # Calendar gaps remain unknown (NaN), never invented zero-load rest days.
    expanded = daily.reindex(pd.date_range(daily.index.min(), daily.index.max(), freq="D"))
    expanded.index.name = "date"
    parameters = fresh_parameters()
    rolling = rolling_load_statistics(expanded, parameters).set_index(["date", "component"])
    for row in out["daily"]:
        stats = rolling.loc[(pd.Timestamp(row["date"]), row["zone"])]
        row.update(effective_load=float(stats["effective"]), e7_daily=float(stats["E7_daily"]),
                   e40_daily=float(stats["E40_daily"]), status_7_40=float(stats["index_7_40"]),
                   tref_used_min=float(stats["Tref"]))
    summary = compute_load_statistics(daily, parameters, pd.Timestamp(source.get("period_end") or daily.index.max()))
    for row in out.get("zones", []):
        stats = summary.loc[row["zone"]]
        row.update(e7_daily=float(stats["E7_daily"]), e40_daily=float(stats["E40_daily"]),
                   status_7_40=float(stats["index_7_40"]), tref_min=float(stats["Tref"]))
    out["component_load_model"] = {"version": VERSION, "contexts_by_sport": deepcopy(contexts_by_sport),
                                   "fingerprint": fingerprint, "scope": "ACTIVITY"}
    return out


def project_snapshot(repository, alias, snapshot, *, contexts_by_sport=None, speed_by_sport=None):
    if not snapshot or not snapshot.get("load_history"):
        return snapshot
    source = snapshot["load_history"]
    sports = list(dict.fromkeys(a["sport"] for a in source.get("activities", [])))
    if contexts_by_sport is None:
        if callable(getattr(repository, "_request", None)) or speed_by_sport is not None:
            contexts_by_sport = read_contexts(repository, alias, sports, speed_by_sport=speed_by_sport)
        else:
            # The file-only pilot has no model store or personal curves. Frozen
            # evidence, when present, is preferable to inventing new settings.
            frozen = (source.get("component_load_model") or {}).get("contexts_by_sport") or {}
            contexts_by_sport = {sport: frozen.get(sport) or context_from_speed_view({"sport": sport}) for sport in sports}
    projected = project_history(source, contexts_by_sport)
    if projected is source:
        return snapshot
    out = deepcopy(snapshot)
    out["load_history"] = projected
    summaries = {row["zone"]: row for row in projected.get("zones", [])}
    for row in (out.get("training_status") or {}).get("zones", []):
        if row["zone"] in summaries:
            current = summaries[row["zone"]]
            row["status_7_40"] = current["status_7_40"]
            row["tref_min"] = current["tref_min"]
    return out


def project_legacy_recovery(snapshot):
    """Keep the pilot's legacy Recovery coherent when Recovery v2 is disabled."""
    from biathlon.physiology import compute_readiness_history, current_readiness
    source = (snapshot or {}).get("load_history")
    if not source:
        raise ComponentLoadRefreshRequired("Recovery requires complete load history; refresh real data")
    frame = _daily_frame(source)
    if frame.empty:
        return snapshot
    if len(frame) != (frame.index.max() - frame.index.min()).days + 1:
        raise ComponentLoadRefreshRequired("Recovery requires complete calendar coverage")
    for row in source["daily"]:
        frame.loc[pd.Timestamp(row["date"]), f"tref_used_{row['zone']}"] = row["tref_used_min"]
    for row in (source.get("strength") or {}).get("daily", []):
        if pd.Timestamp(row["date"]) in frame.index and row.get("tref_used_min") is not None:
            frame.loc[pd.Timestamp(row["date"]), "tref_used_STR"] = row["tref_used_min"]
    parameters = fresh_parameters()
    history = compute_readiness_history(frame, parameters, use_supplied_tref=True)
    current = current_readiness(history, parameters, target_date=source["period_end"])
    out = deepcopy(snapshot)
    for row in (out.get("training_status") or {}).get("zones", []):
        row["recovery_readiness_percent"] = float(current.loc[row["zone"], "readiness"])
        row["recovery_days_to_full"] = float(current.loc[row["zone"], "days_to_full"])
    recovery = out.get("recovery_history")
    if not recovery or recovery.get("schema_version") != "recovery-history-v1":
        out["recovery_history"] = None
        return out
    for row in recovery["current"]:
        stats = current.loc[row["zone"]]
        row.update(readiness_percent=float(stats["readiness"]), residual_fatigue=float(stats["fatigue"]),
                   days_to_practical_recovery=float(stats["days_to_full"]))
    sums = {r["zone"]: r for r in source.get("zones", [])}
    for row in recovery.get("settings", []):
        if row["zone"] in sums:
            row["tref_min"] = sums[row["zone"]]["tref_min"]
    indexed = history.set_index(["date", "component"])
    for row in recovery["daily"]:
        stats = indexed.loc[(pd.Timestamp(row["date"]), row["zone"])]
        row.update(readiness_before_percent=float(stats["readiness_before"]), readiness_after_percent=float(stats["readiness_after"]),
                   residual_fatigue_after=float(stats["fatigue_after"]), impulse=float(stats["impulse"]),
                   effective_load=float(stats["effective"]), tref_min=float(stats["Tref"]))
    return out
