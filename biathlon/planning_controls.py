"""Executable coach controls shared by the weekly planner and its outlook.

7/40 targets express intent. They never override readiness or method capacity.
All references below contain actual observations only, never synthetic history.
"""
from datetime import date, timedelta
import math
from .constants import COMPONENTS, fresh_parameters

VERSION = "planning-controls-v1"


def resolve(profile, day, period, automatic_accents):
    controls = profile.get("planning_controls")
    if not controls:
        return None
    anchor = date.fromisoformat(controls.get("mesocycle_anchor") or profile["program_start"])
    wave = controls["wave"]
    week = max(0, (day-anchor).days // 7) % len(wave)
    manual = controls["accents"]
    mode = controls["accent_mode"]
    accents = (manual if mode == "MANUAL" else [*manual, *[z for z in automatic_accents if z not in manual]]
               if mode == "HYBRID" else automatic_accents)[:controls["accent_limit"]]
    state = {"kind": "RECOVERY" if week == len(wave)-1 else "BUILD", "name": "Базов мезоцикъл",
             "accents": accents, "wave_factor": wave[week], "week": week+1, "length": len(wave),
             "target_index": controls["accent_index"], "maintenance_index": controls["maintenance_index"],
             "volume_factor": wave[week], "explicit": False}
    for cycle in controls["cycles"]:
        left, right = date.fromisoformat(cycle["start_date"]), date.fromisoformat(cycle["end_date"])
        if left <= day <= right:
            state.update(kind=cycle["kind"], name=cycle["name"], accents=cycle["accents"],
                         week=(day-left).days//7+1, length=math.ceil(((right-left).days+1)/7),
                         target_index=cycle["target_index"], wave_factor=1., volume_factor=cycle["volume_factor"], explicit=True)
        elif cycle["kind"] == "STRESS" and right < day <= right+timedelta(days=cycle["recovery_days"]):
            state.update(kind="RECOVERY", name="Разтоварване след " + cycle["name"], accents=cycle["accents"],
                         target_index=.78, wave_factor=1., volume_factor=.78, explicit=True,
                         week=(day-right-timedelta(days=1)).days//7+1, length=math.ceil(cycle["recovery_days"]/7))
    # A calendar entry cannot override entry, transition or race taper rules.
    if period in {"RE_ENTRY", "TRANSITION"}:
        ceiling = .8 if period == "RE_ENTRY" else .6
        state.update(target_index=min(1., state["target_index"]), wave_factor=min(ceiling, state["wave_factor"]),
                     volume_factor=min(ceiling, state["volume_factor"]), kind=period)
    return state


def reference(rows, today):
    result = {}
    for z in COMPONENTS:
        recent = [r["effective_load"] for r in rows if r["zone"] == z and
                  (today-timedelta(days=40)).isoformat() <= r["date"] < today.isoformat()]
        baseline = [r["effective_load"] for r in rows if r["zone"] == z and
                    (today-timedelta(days=50)).isoformat() <= r["date"] < today.isoformat()]
        result[z] = {"c40": sum(recent)/len(recent) if recent else 0., "known": len(recent) >= 20,
                     "b50": max(fresh_parameters()["base_loads"][z], .5*sum(baseline)/len(baseline) if baseline else 0.)}
    return result


def goals(profile, state, actual_base, legacy, *, limited, taper_factor):
    if state is None:
        return legacy
    result = {}
    for z in COMPONENTS:
        base = actual_base[z]
        accent = z in state["accents"]
        index = (state["target_index"] if accent else state["maintenance_index"])*state["wave_factor"]
        if state["kind"] == "RECOVERY":
            index = min(index, state["target_index"]*state["wave_factor"], .9)
        if limited:
            index = min(1., index)
        # Exact inversion of the displayed canonical ratio, including B50.
        target = max(0., 7*(index*(base["b50"]+base["c40"])-base["b50"]))
        # No automatic introduction of a previously untrained component.
        if base["c40"] == 0:
            target = 0.
        manual = profile.get("component_targets_weekly", {})
        if z in manual:
            target = manual[z]*state["wave_factor"]
        target *= taper_factor
        result[z] = {"target": target, "reference": base["c40"]*7,
                     "factor": target/max(1e-9, base["c40"]*7), "development": accent and index > 1,
                     "basis": "COACH_COMPONENT_GOAL" if z in manual else "COACH_7_40_TARGET",
                     "requested_index": index, "target_index": (base["b50"]+target/7)/(base["b50"]+base["c40"]),
                     "cycle": state, "version": VERSION, "readiness_permission": False}
    return result


def volume_history(source, today, covered_days):
    activities = [a for a in source.get("activities", []) if
                  (today-timedelta(days=28)).isoformat() <= a["date"] < today.isoformat()]
    by_sport = {}
    for a in activities:
        sport = a.get("sport", "Unknown")
        by_sport[sport] = by_sport.get(sport, 0.) + float(a.get("duration_min") or 0.)
    weekly = {s: round(v*7/max(1, covered_days), 3) for s,v in by_sport.items()}
    weeks = []
    daily = source.get("daily", [])
    zone_dates = [{r["date"] for r in daily if r["zone"] == z} for z in COMPONENTS if z != "STR"]
    zone_dates.append({r["date"] for r in source.get("strength", {}).get("daily", [])})
    complete_dates = set.intersection(*zone_dates)
    for offset in range(4, 0, -1):
        start, end = today-timedelta(days=7*offset), today-timedelta(days=7*(offset-1)+1)
        covered = len({d for d in complete_dates if start.isoformat() <= d <= end.isoformat()})
        selected = [a for a in activities if start.isoformat() <= a["date"] <= end.isoformat()]
        weeks.append({"start_date": start.isoformat(), "end_date": end.isoformat(), "covered_days": covered,
                      "actual_minutes": round(sum(float(a.get("duration_min") or 0) for a in selected), 3) if covered == 7 else None,
                      "planned_minutes": None, "plan_status": "ACTUAL_HISTORY_ONLY"})
    return {"basis": "ACTUAL_28_DAY_HISTORY", "covered_days": covered_days, "by_sport_weekly_minutes": weekly,
            "all_sports_weekly_minutes": round(sum(weekly.values()), 3), "weeks": weeks}
