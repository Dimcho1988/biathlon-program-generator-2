"""Оркестрация на целия изчислителен pipeline за един спортист."""

from __future__ import annotations

import hashlib
import json
from datetime import date
from typing import Any

import numpy as np
import pandas as pd

from .constants import COMPONENTS
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
from .testing import analyze_tests


def _hash_inputs(bundle: dict[str, Any], athlete_id: str) -> str:
    payload = {
        "athlete_id": athlete_id,
        "version": int(bundle.get("version", 1)),
        "activity_rows": int((bundle["activities"]["athlete_id"] == athlete_id).sum()),
        "wellness_rows": int((bundle["wellness"]["athlete_id"] == athlete_id).sum()),
        "test_rows": int((bundle["tests"]["athlete_id"] == athlete_id).sum()),
        "calendar_rows": int((bundle["calendar"]["athlete_id"] == athlete_id).sum()),
        "planning_preferences": bundle.get("planning_preferences", {}).get(athlete_id, {}),
        "parameters": bundle["parameters"],
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode("utf-8")).hexdigest()[:16]


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
    raw_preferences = bundle.setdefault("planning_preferences", {}).get(athlete_id)
    if raw_preferences is None:
        raw_preferences = default_planning_preferences(profile_code, today)
        bundle["planning_preferences"][athlete_id] = raw_preferences
    planning_preferences = normalize_preferences(raw_preferences, profile_code, today)

    activities = bundle["activities"].loc[bundle["activities"]["athlete_id"] == athlete_id].copy()
    activity_summaries = activities_to_activity_summaries(activities, zone_profile)
    if activity_summaries.empty:
        history_end = today - pd.Timedelta(days=1)
    else:
        latest_history = pd.Timestamp(activity_summaries["date"].max())
        history_end = today - pd.Timedelta(days=1) if pd.isna(latest_history) else min(today - pd.Timedelta(days=1), latest_history.normalize())
    daily_loads = compute_daily_load_history(activity_summaries, parameters, end_date=history_end)
    load_stats = compute_load_statistics(daily_loads, parameters, as_of=history_end)
    rolling_load = rolling_load_statistics(daily_loads, parameters)
    readiness_history = compute_readiness_history(daily_loads, parameters)
    load_readiness = current_readiness(readiness_history, parameters, target_date=today)

    metric_details, monitoring_by_component, hard_reasons = analyze_wellness(
        bundle["wellness"], athlete_id, parameters, as_of=today
    )
    test_details, test_adjustments = analyze_tests(bundle["tests"], athlete_id, parameters, as_of=today)
    integrated = integrate_component_readiness(load_readiness, monitoring_by_component, test_adjustments)
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
                "test_adjustment": float(test_adjustments.get(component, 0.0)),
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
        "algorithm_version": "streamlit-demo-0.4.0",
        "parameter_version": int(bundle.get("version", 1)),
        "inputs_hash": _hash_inputs(bundle, athlete_id),
        "global_readiness": global_readiness,
        "status": status,
        "hard_reasons": hard_reasons,
        "annual_volume_context": annual_context,
        "planning_preferences": planning_preferences,
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
        "test_adjustments": test_adjustments,
        "integrated": integrated,
        "weekly_targets": weekly_targets,
        "planning_preferences": planning_preferences,
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
