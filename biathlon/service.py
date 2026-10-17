"""Оркестрация на целия изчислителен pipeline за един спортист."""

from __future__ import annotations

import hashlib
import json
from datetime import date
from typing import Any

import numpy as np
import pandas as pd

from .constants import COMPONENTS, STRENGTH_COEFFICIENTS, STRENGTH_LABELS, STRENGTH_TYPES
from .mesocycles import empty_camp_prescriptions, normalize_camp_prescriptions
from .methodology import methodology_snapshot_metadata
from .monitoring import analyze_wellness, integrate_component_readiness
from .physiology import (
    activities_to_activity_summaries,
    compute_daily_load_history,
    compute_load_statistics,
    compute_readiness_history,
    current_readiness,
    rolling_load_statistics,
)
from .planning import apply_plan_overrides, build_volume_trajectory, build_weekly_targets, generate_week_plan
from .preferences import annual_volume_context, default_planning_preferences, normalize_preferences
from .testing import aggregate_test_effects, analyze_tests


def _canonical_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): _canonical_value(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, (list, tuple, set)):
        return [_canonical_value(item) for item in value]
    if isinstance(value, (pd.Timestamp, date)):
        return pd.Timestamp(value).isoformat()
    if isinstance(value, np.generic):
        return _canonical_value(value.item())
    if value is None:
        return None
    if isinstance(value, float) and not np.isfinite(value):
        return None
    return value


def _canonical_frame(frame: pd.DataFrame) -> dict[str, Any]:
    columns = sorted(str(column) for column in frame.columns)
    if frame.empty:
        return {"columns": columns, "records": []}

    normalized = frame.reindex(columns=columns)
    records = [
        {
            column: _canonical_value(row[column])
            for column in columns
        }
        for _, row in normalized.iterrows()
    ]
    records.sort(
        key=lambda record: json.dumps(
            record,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return {"columns": columns, "records": records}


def _hash_inputs(
    bundle: dict[str, Any],
    athlete_id: str,
    *,
    effective_preferences: dict[str, Any] | None = None,
    effective_camp_prescriptions: pd.DataFrame | None = None,
) -> str:
    athlete_filter = bundle["athletes"]["athlete_id"].astype(str) == athlete_id
    activity_filter = bundle["activities"]["athlete_id"].astype(str) == athlete_id
    wellness_filter = bundle["wellness"]["athlete_id"].astype(str) == athlete_id
    test_filter = bundle["tests"]["athlete_id"].astype(str) == athlete_id
    calendar_filter = bundle["calendar"]["athlete_id"].astype(str) == athlete_id
    camp_prescriptions = normalize_camp_prescriptions(
        effective_camp_prescriptions
        if effective_camp_prescriptions is not None
        else bundle.get("camp_prescriptions")
    )
    camp_filter = (
        camp_prescriptions["athlete_id"].astype(str) == athlete_id
        if not camp_prescriptions.empty
        else pd.Series(False, index=camp_prescriptions.index, dtype=bool)
    )
    relevant_overrides = {
        str(key): value
        for key, value in bundle.get("plan_overrides", {}).items()
        if str(key).startswith(f"{athlete_id}|")
    }
    payload = {
        "athlete_id": athlete_id,
        "version": int(bundle.get("version", 1)),
        "athlete": _canonical_frame(bundle["athletes"].loc[athlete_filter]),
        "zone_profile": _canonical_frame(bundle["zone_profiles"][athlete_id]),
        "activities": _canonical_frame(bundle["activities"].loc[activity_filter]),
        "wellness": _canonical_frame(bundle["wellness"].loc[wellness_filter]),
        "tests": _canonical_frame(bundle["tests"].loc[test_filter]),
        "calendar": _canonical_frame(bundle["calendar"].loc[calendar_filter]),
        "camp_prescriptions": _canonical_frame(
            camp_prescriptions.loc[camp_filter]
        ),
        "methods": _canonical_frame(bundle["methods"]),
        "planning_preferences": _canonical_value(
            effective_preferences
            if effective_preferences is not None
            else bundle.get("planning_preferences", {}).get(athlete_id, {})
        ),
        "parameters": _canonical_value(bundle["parameters"]),
        "plan_overrides": _canonical_value(relevant_overrides),
    }
    serialized = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:16]


def _append_current_readiness_snapshot(
    readiness_history: pd.DataFrame,
    load_readiness: pd.DataFrame,
    load_stats: pd.DataFrame,
    today: pd.Timestamp,
) -> pd.DataFrame:
    """Add the current morning state to the historical recovery curve.

    Daily training load is considered complete through yesterday.  The current
    point therefore represents recovery up to today, with no invented training
    impulse.  This keeps the chart moving with the calendar even when the most
    recent activity was several days ago.
    """

    current_rows = []
    for component in COMPONENTS:
        fatigue = float(load_readiness.loc[component, "fatigue"])
        readiness = float(load_readiness.loc[component, "readiness"])
        current_rows.append(
            {
                "date": today,
                "component": component,
                "fatigue_before": fatigue,
                "fatigue_after": fatigue,
                "readiness_before": readiness,
                "readiness_after": readiness,
                "impulse": 0.0,
                "Tref": float(load_stats.loc[component, "Tref"]),
                "effective": 0.0,
            }
        )

    current = pd.DataFrame(current_rows)
    if readiness_history.empty:
        return current

    history = readiness_history.loc[
        pd.to_datetime(readiness_history["date"]).dt.normalize() < today
    ].copy()
    return pd.concat([history, current], ignore_index=True)


def analyze_athlete(
    bundle: dict[str, Any],
    athlete_id: str,
    as_of: date | pd.Timestamp | None = None,
    generate_plan: bool = True,
) -> dict[str, Any]:
    today = pd.Timestamp(as_of or date.today()).normalize()
    athlete_row = bundle["athletes"].loc[bundle["athletes"]["athlete_id"] == athlete_id]
    if athlete_row.empty:
        raise KeyError(f"Няма спортист с id={athlete_id}")
    athlete = athlete_row.iloc[0]
    zone_profile = bundle["zone_profiles"][athlete_id].copy()
    parameters = bundle["parameters"]
    profile_code = str(athlete.get("profile_code", "A"))
    raw_preferences = bundle.get("planning_preferences", {}).get(athlete_id)
    if raw_preferences is None:
        raw_preferences = default_planning_preferences(profile_code, today)
    planning_preferences = normalize_preferences(raw_preferences, profile_code, today)
    camp_prescriptions = normalize_camp_prescriptions(
        bundle.get("camp_prescriptions", empty_camp_prescriptions())
    )

    activities = bundle["activities"].loc[bundle["activities"]["athlete_id"] == athlete_id].copy()
    activity_summaries = activities_to_activity_summaries(activities, zone_profile)
    # A day without an activity is still a real recovery day.  Always extend
    # the zero-load series through yesterday instead of stopping the simulation
    # at the date of the last recorded workout.
    history_end = today - pd.Timedelta(days=1)
    daily_loads = compute_daily_load_history(activity_summaries, parameters, end_date=history_end)
    load_stats = compute_load_statistics(daily_loads, parameters, as_of=history_end)
    rolling_load = rolling_load_statistics(daily_loads, parameters)
    readiness_history = compute_readiness_history(daily_loads, parameters)
    load_readiness = current_readiness(readiness_history, parameters, target_date=today)
    readiness_history = _append_current_readiness_snapshot(
        readiness_history,
        load_readiness,
        load_stats,
        today,
    )

    metric_details, monitoring_by_component, hard_reasons = analyze_wellness(
        bundle["wellness"], athlete_id, parameters, as_of=today
    )
    test_details, test_effects = analyze_tests(bundle["tests"], athlete_id, parameters, as_of=today)
    readiness_test_adjustments = aggregate_test_effects(
        test_effects,
        parameters,
        channel="readiness",
    )
    planning_test_adjustments = aggregate_test_effects(
        test_effects,
        parameters,
        channel="planning",
    )
    integrated = integrate_component_readiness(
        load_readiness,
        monitoring_by_component,
        readiness_test_adjustments,
    )
    annual_context = annual_volume_context(activity_summaries, planning_preferences, as_of=today)

    weekly_targets = build_weekly_targets(
        load_stats,
        integrated,
        bundle["calendar"],
        athlete_id,
        parameters,
        start_date=today,
        minimum_weeks=16,
        planning_preferences=planning_preferences,
        annual_context=annual_context,
        test_effects=test_effects,
        camp_prescriptions=camp_prescriptions,
    )

    plan = pd.DataFrame()
    plan_comparison = pd.DataFrame()
    plan_snapshot: dict[str, Any] = {}
    if generate_plan:
        plan, plan_comparison, plan_snapshot = generate_week_plan(
            weekly_targets,
            load_stats,
            load_readiness,
            integrated,
            bundle["methods"],
            bundle["calendar"],
            athlete_id,
            parameters,
            start_date=today,
            planning_preferences=planning_preferences,
        )
        plan = apply_plan_overrides(plan, bundle.get("plan_overrides", {}), athlete_id)

    volume_trajectory = build_volume_trajectory(
        activity_summaries,
        weekly_targets,
        load_stats,
        parameters,
        planning_preferences,
        annual_context,
        as_of=today,
        generated_plan=plan if generate_plan else None,
    )

    global_readiness = float(integrated["integrated_readiness"].mean())
    hard_flag = bool(integrated["hard_flag"].any())
    if hard_flag:
        status = "Изисква преглед"
    elif global_readiness >= 85:
        status = "Готов за планирано изграждане"
    elif global_readiness >= 70:
        status = "Умерена готовност"
    elif global_readiness >= 55:
        status = "Намаляване / преразпределение"
    else:
        status = "Възстановяване"

    upcoming_events = bundle["calendar"].loc[
        (bundle["calendar"]["athlete_id"] == athlete_id)
        & (pd.to_datetime(bundle["calendar"]["start_date"]).dt.normalize() >= today)
    ].copy()
    upcoming_events["start_date"] = pd.to_datetime(upcoming_events["start_date"]).dt.normalize()
    next_event = upcoming_events.sort_values("start_date").iloc[0].to_dict() if not upcoming_events.empty else None

    latest_activity = activity_summaries.sort_values("date").iloc[-1] if not activity_summaries.empty else None
    strength_summary_rows: list[dict[str, Any]] = []
    for strength_type in STRENGTH_TYPES:
        real_col = f"real_{strength_type}"
        q_col = f"q_{strength_type}"
        real_total = (
            float(pd.to_numeric(activity_summaries[real_col], errors="coerce").fillna(0.0).clip(lower=0.0).sum())
            if real_col in activity_summaries
            else 0.0
        )
        q_total = (
            float(pd.to_numeric(activity_summaries[q_col], errors="coerce").fillna(0.0).clip(lower=0.0).sum())
            if q_col in activity_summaries
            else real_total * float(STRENGTH_COEFFICIENTS[strength_type])
        )
        strength_summary_rows.append(
            {
                "strength_type": strength_type,
                "label": STRENGTH_LABELS[strength_type],
                "coefficient": float(STRENGTH_COEFFICIENTS[strength_type]),
                "real_min": real_total,
                "equivalent_min": q_total,
            }
        )
    strength_summary = pd.DataFrame(strength_summary_rows)
    first_week = weekly_targets.loc[weekly_targets["week_no"] == 1].set_index("component")
    decision_reasons = []
    for component in COMPONENTS:
        decision_reasons.append(
            {
                "component": component,
                "current_index_7_40": float(load_stats.loc[component, "index_7_40"]),
                "Tref": float(load_stats.loc[component, "Tref"]),
                "load_readiness": float(load_readiness.loc[component, "readiness"]),
                "monitoring_score": float(monitoring_by_component.loc[component, "monitoring_score"]),
                "readiness_test_adjustment": float(readiness_test_adjustments.get(component, 0.0)),
                "planning_test_adjustment": float(planning_test_adjustments.get(component, 0.0)),
                "integrated_readiness": float(integrated.loc[component, "integrated_readiness"]),
                "adaptive_multiplier": float(integrated.loc[component, "adaptive_multiplier"]),
                "target_index": float(first_week.loc[component, "target_index"]),
                "target_effective_week": float(first_week.loc[component, "target_effective_week"]),
                "reason": str(integrated.loc[component, "reason"]),
            }
        )

    decision_snapshot = {
        "snapshot_type": "DecisionSnapshot",
        "created_at": str(pd.Timestamp.now()),
        "athlete_id": athlete_id,
        "athlete_name": str(athlete["name"]),
        "data_version": int(bundle.get("version", 1)),
        "algorithm_version": "streamlit-demo-0.6.0",
        "parameter_version": int(bundle.get("version", 1)),
        "planning_methodology": methodology_snapshot_metadata(),
        "inputs_hash": _hash_inputs(
            bundle,
            athlete_id,
            effective_preferences=planning_preferences,
            effective_camp_prescriptions=camp_prescriptions,
        ),
        "global_readiness": global_readiness,
        "status": status,
        "hard_reasons": hard_reasons,
        "annual_volume_context": annual_context,
        "planning_preferences": planning_preferences,
        "camp_prescriptions": camp_prescriptions.loc[
            camp_prescriptions["athlete_id"].astype(str) == athlete_id
        ].to_dict(orient="records"),
        "strength_model": {
            strength_type: {
                "label": STRENGTH_LABELS[strength_type],
                "coefficient": STRENGTH_COEFFICIENTS[strength_type],
            }
            for strength_type in STRENGTH_TYPES
        },
        "components": decision_reasons,
        "plan": plan_snapshot,
    }

    return {
        "athlete": athlete,
        "zone_profile": zone_profile,
        "activities": activities,
        "activity_summaries": activity_summaries,
        "daily_loads": daily_loads,
        "load_stats": load_stats,
        "rolling_load": rolling_load,
        "readiness_history": readiness_history,
        "load_readiness": load_readiness,
        "metric_details": metric_details,
        "monitoring_by_component": monitoring_by_component,
        "hard_reasons": hard_reasons,
        "test_details": test_details,
        "test_effects": test_effects,
        "test_readiness_adjustments": readiness_test_adjustments,
        "test_planning_adjustments": planning_test_adjustments,
        "test_adjustments": planning_test_adjustments,
        "integrated": integrated,
        "weekly_targets": weekly_targets,
        "planning_preferences": planning_preferences,
        "camp_prescriptions": camp_prescriptions.loc[
            camp_prescriptions["athlete_id"].astype(str) == athlete_id
        ].copy(),
        "annual_context": annual_context,
        "volume_trajectory": volume_trajectory,
        "plan": plan,
        "plan_comparison": plan_comparison,
        "decision_snapshot": decision_snapshot,
        "global_readiness": global_readiness,
        "status": status,
        "hard_flag": hard_flag,
        "next_event": next_event,
        "latest_activity": latest_activity,
        "strength_summary": strength_summary,
        "as_of": today,
    }


def team_summary(bundle: dict[str, Any], as_of: date | pd.Timestamp | None = None) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for _, athlete in bundle["athletes"].iterrows():
        athlete_id = str(athlete["athlete_id"])
        analysis = analyze_athlete(bundle, athlete_id, as_of=as_of, generate_plan=False)
        weakest_component = analysis["integrated"]["integrated_readiness"].idxmin()
        max_index_component = analysis["load_stats"]["index_7_40"].idxmax()
        rows.append(
            {
                "athlete_id": athlete_id,
                "Спортист": athlete["name"],
                "Профил": athlete["profile_name"],
                "Интегрирана готовност": round(analysis["global_readiness"], 1),
                "Статус": analysis["status"],
                "Най-слаб компонент": weakest_component,
                "Най-висок 7/40": f"{max_index_component} · {analysis['load_stats'].loc[max_index_component, 'index_7_40']:.2f}",
                "Твърд флаг": "Да" if analysis["hard_flag"] else "Не",
                "Следващо събитие": analysis["next_event"]["name"] if analysis["next_event"] else "—",
            }
        )
    return pd.DataFrame(rows)
