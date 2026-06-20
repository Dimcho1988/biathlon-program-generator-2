"""Периодизация, седмични цели и генератор на конкретен микроцикъл."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import numpy as np
import pandas as pd

from .constants import (
    AEROBIC_COMPONENTS,
    COMPONENT_LABELS,
    COMPONENTS,
    PERIODIZATION_CENTERS,
)
from .physiology import (
    apply_training_impulse,
    effective_from_direct_vector,
    recover_fatigue,
    solve_direct_load,
    target_weekly_effective,
)

EPS = 1e-9


def _future_main_event(calendar: pd.DataFrame, athlete_id: str, start: pd.Timestamp) -> pd.Series | None:
    subset = calendar.loc[
        (calendar["athlete_id"] == athlete_id)
        & (calendar["type"] == "MAIN_RACE")
        & (pd.to_datetime(calendar["start_date"]).dt.normalize() >= start)
    ].copy()
    if subset.empty:
        return None
    subset["start_date"] = pd.to_datetime(subset["start_date"]).dt.normalize()
    return subset.sort_values("start_date").iloc[0]


def _phase_name(progress: float, weeks_to_race: int) -> str:
    if weeks_to_race <= 2:
        return "Тейпър"
    if progress < 1 / 3:
        return "Първа третина"
    if progress < 2 / 3:
        return "Втора третина"
    return "Трета третина"


def _global_progression(progress: float) -> float:
    peak = 0.60
    if progress <= peak:
        return 1.00 + 0.10 * (progress / max(peak, EPS))
    return 1.10 - 0.07 * ((progress - peak) / max(1.0 - peak, EPS))


def _component_envelope(component: str, progress: float) -> float:
    center, width = PERIODIZATION_CENTERS[component]
    return 0.94 + 0.20 * float(np.exp(-0.5 * ((progress - center) / width) ** 2))


def _events_in_week(calendar: pd.DataFrame, athlete_id: str, week_start: pd.Timestamp) -> pd.DataFrame:
    week_end = week_start + pd.Timedelta(days=6)
    subset = calendar.loc[calendar["athlete_id"] == athlete_id].copy()
    subset["start_date"] = pd.to_datetime(subset["start_date"]).dt.normalize()
    subset["end_date"] = pd.to_datetime(subset["end_date"]).dt.normalize()
    return subset.loc[(subset["start_date"] <= week_end) & (subset["end_date"] >= week_start)]


def build_weekly_targets(
    load_stats: pd.DataFrame,
    integrated: pd.DataFrame,
    calendar: pd.DataFrame,
    athlete_id: str,
    parameters: dict[str, Any],
    start_date: date | pd.Timestamp,
    minimum_weeks: int = 16,
) -> pd.DataFrame:
    """Изгражда компонентна вълна до основния старт."""

    start = pd.Timestamp(start_date).normalize()
    main_event = _future_main_event(calendar, athlete_id, start)
    if main_event is None:
        main_date = start + pd.Timedelta(weeks=minimum_weeks)
        main_name = "Виртуален основен старт"
    else:
        main_date = pd.Timestamp(main_event["start_date"]).normalize()
        main_name = str(main_event["name"])
    weeks = max(minimum_weeks, int(np.ceil((main_date - start).days / 7.0)) + 1)

    rows: list[dict[str, Any]] = []
    for week_index in range(weeks):
        week_start = start + pd.Timedelta(days=7 * week_index)
        weeks_to_race = max(0, int(np.ceil((main_date - week_start).days / 7.0)))
        progress = float(np.clip((week_start - start).days / max((main_date - start).days, 1), 0.0, 1.0))
        global_factor = _global_progression(prog := progress)
        meso_position = week_index % 4
        meso_factor = float(parameters["mesocycle_pattern"][meso_position])
        envelopes = {component: _component_envelope(component, progress) for component in COMPONENTS}
        accent_components = sorted(COMPONENTS, key=lambda c: envelopes[c], reverse=True)[:2]
        events = _events_in_week(calendar, athlete_id, week_start)
        event_names = ", ".join(events["name"].astype(str).tolist()) if not events.empty else ""

        indices: dict[str, float] = {}
        metadata: dict[str, dict[str, Any]] = {}
        for component in COMPONENTS:
            status = "Акцент" if component in accent_components else "Поддържане"
            if envelopes[component] < 0.98:
                status = "Относително възстановяване"

            index = global_factor * meso_factor * envelopes[component]
            if meso_position == 3:
                if status == "Акцент":
                    index *= 0.72
                elif status == "Поддържане":
                    index *= 0.85
                else:
                    index *= 0.98
                status = "Възстановителна седмица · " + status.lower()

            calendar_factor = 1.0
            if not events.empty:
                for _, event in events.iterrows():
                    event_type = str(event["type"])
                    if event_type == "CAMP":
                        calendar_factor *= 1.07 if component in {"Z1", "Z2", "Z3", "STR"} else 0.98
                    elif event_type == "CONTROL_RACE":
                        calendar_factor *= 0.90 if component in {"Z1", "Z2", "STR"} else 0.98
                    elif event_type == "UNAVAILABLE":
                        calendar_factor *= 0.85
                    elif event_type == "TEST":
                        calendar_factor *= 0.96

            # Тейпър: редуцира обема, но пази специфичните компоненти.
            taper_factor = 1.0
            if weeks_to_race == 2:
                taper_factor = 0.84 if component in {"Z1", "Z2", "STR"} else 0.94
            elif weeks_to_race <= 1:
                taper_factor = 0.66 if component in {"Z1", "Z2", "STR"} else 0.90
            if weeks_to_race <= 2:
                status = "Тейпър"

            adaptive = float(integrated.loc[component, "adaptive_multiplier"])
            adaptive_near_term = 1.0 + (adaptive - 1.0) * float(np.exp(-week_index / 2.0))
            test_adjustment = float(integrated.loc[component, "test_adjustment"])
            test_factor = 1.0 + test_adjustment * float(np.exp(-week_index / 4.0))
            index *= calendar_factor * taper_factor * adaptive_near_term * test_factor
            index = float(np.clip(index, 0.65, 1.35))
            indices[component] = index
            metadata[component] = {
                "status": status,
                "calendar_factor": calendar_factor,
                "taper_factor": taper_factor,
                "adaptive_near_term": adaptive_near_term,
                "test_factor": test_factor,
                "envelope": envelopes[component],
            }

        weekly_effective = target_weekly_effective(load_stats, indices)
        for component in COMPONENTS:
            row = {
                "week_start": week_start,
                "week_no": week_index + 1,
                "weeks_to_main_race": weeks_to_race,
                "main_race": main_name,
                "main_race_date": main_date,
                "phase_progress": progress,
                "phase": _phase_name(progress, weeks_to_race),
                "mesocycle_week": meso_position + 1,
                "component": component,
                "target_index": indices[component],
                "target_effective_week": float(weekly_effective[component]),
                "status": metadata[component]["status"],
                "global_factor": global_factor,
                "component_envelope": metadata[component]["envelope"],
                "calendar_factor": metadata[component]["calendar_factor"],
                "taper_factor": metadata[component]["taper_factor"],
                "adaptive_factor": metadata[component]["adaptive_near_term"],
                "test_factor": metadata[component]["test_factor"],
                "events": event_names,
            }
            rows.append(row)
    return pd.DataFrame(rows)


def _phase_key_fraction(progress: float) -> float:
    if progress < 1 / 3:
        return 0.45
    if progress < 2 / 3:
        return 0.55
    return 0.65


def _select_method(
    methods: pd.DataFrame,
    component: str,
    progress: float,
    readiness: float,
) -> pd.Series:
    subset = methods.loc[
        (methods["component"] == component)
        & (methods["phase_min"] <= progress)
        & (methods["phase_max"] >= progress)
    ].copy()
    if subset.empty:
        subset = methods.loc[methods["component"] == component].copy()
    allowed = subset.loc[subset["min_readiness"] <= readiness]
    if not allowed.empty:
        # По-високото минимално изискване избира по-развиващ метод при висока готовност.
        return allowed.sort_values(["min_readiness", "phase_min"], ascending=False).iloc[0]
    return subset.sort_values("min_readiness").iloc[0]


def _method_description(method: pd.Series, focus: str, main_real: float, readiness: float) -> str:
    main_real = max(0.0, main_real)
    if focus == "Z3":
        repeats = 5
        work = main_real / repeats if repeats else 0
        main = f"Основна част: {repeats} × {work:.1f} мин Z3, 1–2 мин активно възстановяване."
    elif focus == "Z4":
        repeats = 4 if main_real <= 32 else 5
        work = main_real / repeats if repeats else 0
        main = f"Основна част: {repeats} × {work:.1f} мин Z4, 2–3 мин активно възстановяване."
    elif focus == "Z5":
        repeats = 6
        work = main_real / repeats if repeats else 0
        main = f"Основна част: {repeats} × {work:.1f} мин Z5, контролирано възстановяване."
    elif focus == "STR":
        rounds = max(3, int(round(main_real / 8.0)))
        main = f"Основна част: {rounds} кръга/серии, общо около {main_real:.0f} мин силова работа."
    elif focus == "Z2":
        main = f"Основна част: {main_real:.0f} мин равномерно в Z2 с технически контрол."
    else:
        main = f"Основна част: {main_real:.0f} мин равномерно в Z1."

    warmup = int(method.get("warmup_min", 0))
    cooldown = int(method.get("cooldown_min", 0))
    parts = []
    if warmup > 0:
        parts.append(f"Загрявка: {warmup} мин Z1–Z2.")
    parts.append(main)
    if cooldown > 0:
        parts.append(f"Разпускане: {cooldown} мин Z1.")
    parts.append(f"Контролна readiness преди сесията: {readiness:.0f}%.")
    return " ".join(parts)


def _focus_schedule(target_q: pd.Series, tref: pd.Series) -> list[str]:
    high = [c for c in ["Z3", "Z4", "Z5"] if float(target_q[c]) >= max(5.0, 0.08 * float(tref[c]))]
    high = sorted(high, key=lambda c: float(target_q[c]) / max(float(tref[c]), 1.0), reverse=True)
    first = high[0] if high else "Z2"
    second = high[1] if len(high) > 1 else first
    third = high[2] if len(high) > 2 else first
    return ["Z1", first, "STR" if target_q["STR"] > 5 else "Z2", second, "Z1", third if high else "Z2", "Z2"]


def _default_k(component: str) -> float:
    return {"Z1": 1.06, "Z2": 1.12, "Z3": 1.28, "Z4": 1.24, "Z5": 1.22, "STR": 1.10}[component]


def generate_week_plan(
    weekly_targets: pd.DataFrame,
    load_stats: pd.DataFrame,
    load_readiness: pd.DataFrame,
    integrated: pd.DataFrame,
    methods: pd.DataFrame,
    calendar: pd.DataFrame,
    athlete_id: str,
    parameters: dict[str, Any],
    start_date: date | pd.Timestamp,
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    """Генерира 7-дневна програма с конкретни методи и симулирана readiness."""

    plan_start = pd.Timestamp(start_date).normalize()
    first_week_start = pd.Timestamp(weekly_targets["week_start"].min()).normalize()
    first_week = weekly_targets.loc[weekly_targets["week_start"] == first_week_start].set_index("component")
    target_e = first_week["target_effective_week"].reindex(COMPONENTS).astype(float)
    tref = load_stats["Tref"].reindex(COMPONENTS).astype(float)
    target_q, inversion_error = solve_direct_load(target_e, tref, parameters)
    remaining = target_q.copy()

    progress = float(first_week["phase_progress"].iloc[0])
    phase = str(first_week["phase"].iloc[0])
    schedule = _focus_schedule(target_q, tref)
    occurrences_left = {component: schedule.count(component) for component in COMPONENTS}
    fatigue = {component: float(load_readiness.loc[component, "fatigue"]) for component in COMPONENTS}
    rows: list[dict[str, Any]] = []
    planned_q_total = pd.Series(0.0, index=COMPONENTS)
    planned_e_total = pd.Series(0.0, index=COMPONENTS)

    for day_index in range(7):
        current_date = plan_start + pd.Timedelta(days=day_index)
        if day_index > 0:
            fatigue = recover_fatigue(fatigue, 1.0, parameters)
        readiness_map = {component: float(np.clip(100.0 - fatigue[component], 0.0, 100.0)) for component in COMPONENTS}
        focus = schedule[day_index]
        hard_flag = bool(integrated.loc[focus, "hard_flag"])

        # Календарно ограничение за деня.
        day_events = calendar.loc[
            (calendar["athlete_id"] == athlete_id)
            & (pd.to_datetime(calendar["start_date"]).dt.normalize() <= current_date)
            & (pd.to_datetime(calendar["end_date"]).dt.normalize() >= current_date)
        ]
        unavailable = bool((day_events["type"] == "UNAVAILABLE").any()) if not day_events.empty else False

        if hard_flag and focus in {"Z3", "Z4", "Z5", "STR"}:
            focus = "Z1"
        if readiness_map[focus] < 65 and focus in {"Z3", "Z4", "Z5", "STR"}:
            focus = "Z1"
        if unavailable:
            focus = "Z1"

        method = _select_method(methods, focus, progress, readiness_map[focus])
        session_q = pd.Series(0.0, index=COMPONENTS)

        # Основен компонент.
        if focus in {"Z3", "Z4", "Z5"}:
            count = max(1, occurrences_left.get(focus, 1))
            ideal_share = float(remaining[focus]) / count
            readiness_factor = 1.0 if readiness_map[focus] >= 90 else 0.75 if readiness_map[focus] >= 80 else 0.45
            cap = _phase_key_fraction(progress) * float(tref[focus]) * readiness_factor
            main_q = min(float(remaining[focus]), ideal_share, cap)
            if main_q < 5:
                focus = "Z1"
                method = _select_method(methods, focus, progress, readiness_map[focus])
                main_q = 0.0
            else:
                session_q[focus] = main_q
                remaining[focus] = max(0.0, float(remaining[focus]) - main_q)
                occurrences_left[focus] = max(0, count - 1)
        elif focus == "STR":
            main_q = min(float(remaining["STR"]), max(8.0, 0.45 * float(tref["STR"])))
            if readiness_map["STR"] < 75 or hard_flag:
                main_q *= 0.55
            session_q["STR"] = max(0.0, main_q)
            remaining["STR"] = max(0.0, float(remaining["STR"]) - session_q["STR"])
            occurrences_left["STR"] = max(0, occurrences_left.get("STR", 1) - 1)
        else:
            count = max(1, occurrences_left.get(focus, 1))
            main_q = min(float(remaining[focus]), float(remaining[focus]) / count if count else float(remaining[focus]))
            if focus == "Z1" and day_index in {0, 4}:
                main_q = min(float(remaining[focus]), max(main_q, 25.0))
            if focus == "Z2" and day_index in {5, 6}:
                main_q = min(float(remaining[focus]), max(main_q, 35.0))
            session_q[focus] = max(0.0, main_q)
            remaining[focus] = max(0.0, float(remaining[focus]) - session_q[focus])
            occurrences_left[focus] = max(0, count - 1)

        # Загрявка и разпускане за ключови/силови сесии.
        if focus in {"Z3", "Z4", "Z5", "STR"}:
            support_z1_q = (float(method.get("warmup_min", 0)) + float(method.get("cooldown_min", 0))) * 1.06
            session_q["Z1"] += support_z1_q
            remaining["Z1"] = max(0.0, float(remaining["Z1"]) - support_z1_q)
            if focus in {"Z3", "Z4", "Z5"}:
                support_z2_q = 8.0 * 1.12
                session_q["Z2"] += support_z2_q
                remaining["Z2"] = max(0.0, float(remaining["Z2"]) - support_z2_q)

        # Разпределя оставащия нискоинтензивен товар плавно до края на седмицата.
        days_left = max(1, 7 - day_index)
        for low_component in ["Z1", "Z2"]:
            if remaining[low_component] <= 0:
                continue
            share = float(remaining[low_component]) / days_left
            role_factor = 1.45 if day_index in {5, 6} else 0.75 if day_index in {1, 3} else 1.0
            addition = min(float(remaining[low_component]), share * role_factor)
            session_q[low_component] += addition
            remaining[low_component] = max(0.0, float(remaining[low_component]) - addition)

        # Ограничение при пътуване/недостъпност.
        if unavailable:
            max_q = 45.0 * 1.06
            if session_q.sum() > max_q:
                session_q *= max_q / max(session_q.sum(), EPS)

        effective = effective_from_direct_vector(session_q.values, tref.values, parameters)
        readiness_before_focus = readiness_map[focus]
        fatigue = apply_training_impulse(fatigue, effective, tref, parameters)
        readiness_after_focus = float(np.clip(100.0 - fatigue[focus], 0.0, 100.0))

        real_by_component = {}
        for component in COMPONENTS:
            k = float(method["expected_k"]) if component == focus else _default_k(component)
            real_by_component[component] = float(session_q[component] / max(k, EPS))
        total_real = sum(real_by_component.values())
        main_real = real_by_component[focus]
        description = _method_description(method, focus, main_real, readiness_before_focus)

        planned_q_total += session_q
        planned_e_total += pd.Series(effective, index=COMPONENTS)
        target_index = float(first_week.loc[focus, "target_index"])
        explanation_parts = [
            f"{focus} целеви 7/40 = {target_index:.2f}",
            f"интегрирана готовност = {float(integrated.loc[focus, 'integrated_readiness']):.0f}/100",
            f"адаптивен множител = {float(integrated.loc[focus, 'adaptive_multiplier']):.2f}",
        ]
        if hard_flag:
            explanation_parts.append("първоначалният акцент е заменен поради твърд флаг")
        if unavailable:
            explanation_parts.append("дозата е ограничена от календарна недостъпност")
        if not day_events.empty:
            explanation_parts.append("календар: " + ", ".join(day_events["name"].astype(str).tolist()))

        row: dict[str, Any] = {
            "date": current_date,
            "day": ["Понеделник", "Вторник", "Сряда", "Четвъртък", "Петък", "Събота", "Неделя"][current_date.weekday()],
            "focus": focus,
            "focus_label": COMPONENT_LABELS[focus],
            "method_code": str(method["method_code"]),
            "method": str(method["title"]),
            "description": description,
            "total_real_min": round(total_real, 1),
            "readiness_before": round(readiness_before_focus, 1),
            "readiness_after": round(readiness_after_focus, 1),
            "key_stimulus": bool(session_q[focus] >= float(parameters["key_stimulus_fraction"]) * float(tref[focus])),
            "status": "Предложена",
            "locked": False,
            "coach_note": "",
            "explanation": "; ".join(explanation_parts),
            "events": ", ".join(day_events["name"].astype(str).tolist()) if not day_events.empty else "",
        }
        for component in COMPONENTS:
            row[f"q_{component}"] = round(float(session_q[component]), 2)
            row[f"e_{component}"] = round(float(effective[COMPONENTS.index(component)]), 2)
            row[f"real_{component}"] = round(float(real_by_component[component]), 1)
        rows.append(row)

    plan = pd.DataFrame(rows)
    comparison_rows = []
    for component in COMPONENTS:
        comparison_rows.append(
            {
                "component": component,
                "target_effective": float(target_e[component]),
                "planned_effective": float(planned_e_total[component]),
                "target_direct_q": float(target_q[component]),
                "planned_direct_q": float(planned_q_total[component]),
                "remaining_direct_q": float(max(0.0, remaining[component])),
                "completion_pct": float(
                    100.0 * planned_e_total[component] / max(float(target_e[component]), EPS)
                    if target_e[component] > EPS
                    else 100.0
                ),
            }
        )
    comparison = pd.DataFrame(comparison_rows).set_index("component")
    snapshot = {
        "plan_start": str(plan_start.date()),
        "phase": phase,
        "phase_progress": progress,
        "target_indices": first_week["target_index"].to_dict(),
        "target_effective": target_e.to_dict(),
        "target_direct_q": target_q.to_dict(),
        "inversion_error": inversion_error,
        "unallocated_direct_q": remaining.to_dict(),
        "warnings": [
            f"Остатък {component}: {value:.1f} екв. мин"
            for component, value in remaining.items()
            if value > max(3.0, 0.05 * float(target_q[component]))
        ],
    }
    return plan, comparison, snapshot


def apply_plan_overrides(plan: pd.DataFrame, overrides: dict[str, Any], athlete_id: str) -> pd.DataFrame:
    """Прилага запазени треньорски промени върху текущата версия на плана."""

    if not overrides:
        return plan
    result = plan.copy()
    for idx, row in result.iterrows():
        key = f"{athlete_id}|{pd.Timestamp(row['date']).date().isoformat()}"
        override = overrides.get(key)
        if not override:
            continue
        for field in ["status", "locked", "coach_note", "method", "description", "total_real_min"]:
            if field in override:
                result.at[idx, field] = override[field]
    return result
