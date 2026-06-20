"""Потребителски цели, седмична структура и помощни функции за ръчна история.

Модулът е нарочно отделен от физиологичното ядро. Така експертните ограничения
(брой сесии, почивни дни, двойни тренировки и годишна цел) могат да се променят,
без да се смесват с формулите за Q, E, 7/40 и възстановяване.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import date
from typing import Any, Iterable

import numpy as np
import pandas as pd

from .constants import AEROBIC_COMPONENTS, COMPONENTS

WEEKDAY_LABELS = {
    0: "Понеделник",
    1: "Вторник",
    2: "Сряда",
    3: "Четвъртък",
    4: "Петък",
    5: "Събота",
    6: "Неделя",
}

WEEKDAY_BY_LABEL = {label: code for code, label in WEEKDAY_LABELS.items()}

EVENT_TYPE_LABELS = {
    "MAIN_RACE": "Основен старт",
    "CONTROL_RACE": "Контролен старт",
    "CAMP": "Лагер",
    "TEST": "Контролен тест",
    "UNAVAILABLE": "Недостъпност / пътуване",
}

DEFAULT_MANUAL_POSITIONS = {
    "Z1": 0.35,
    "Z2": 0.50,
    "Z3": 0.72,
    "Z4": 0.68,
    "Z5": 0.62,
}


def default_planning_preferences(profile_code: str = "A", today: date | pd.Timestamp | None = None) -> dict[str, Any]:
    """Връща независим начален профил на сезонни и седмични предпочитания."""

    current = pd.Timestamp(today or date.today()).normalize()
    season_start = pd.Timestamp(year=current.year, month=1, day=1)
    season_end = pd.Timestamp(year=current.year, month=12, day=31)

    annual_targets = {"A": 600.0, "B": 560.0, "C": 500.0}
    sessions = {"A": 9, "B": 8, "C": 8}

    return {
        "season_start": season_start,
        "season_end": season_end,
        "annual_target_hours": annual_targets.get(str(profile_code), 550.0),
        "annual_goal_influence": 0.35,
        "min_volume_factor": 0.88,
        "max_volume_factor": 1.15,
        "annual_goal_component_weights": {
            "Z1": 1.00,
            "Z2": 0.80,
            "Z3": 0.35,
            "Z4": 0.10,
            "Z5": 0.05,
            "STR": 0.25,
        },
        "sessions_per_week": sessions.get(str(profile_code), 8),
        "rest_days": [0],
        "double_session_days": [2, 5],
        "long_session_day": 6,
        "intensity_days": [2, 5],
        "strength_days": [1, 4],
        "max_key_sessions_per_week": 3,
        "double_threshold_enabled": False,
        "double_threshold_day": 2,
        "double_threshold_components": ["Z3", "Z4"],
        "double_threshold_min_readiness": 88.0,
        "double_threshold_phase_min": 0.30,
        "double_threshold_phase_max": 0.88,
        "between_sessions_recovery_days": 0.30,
        "manual_positions": deepcopy(DEFAULT_MANUAL_POSITIONS),
    }


def normalize_preferences(
    preferences: dict[str, Any] | None,
    profile_code: str = "A",
    today: date | pd.Timestamp | None = None,
) -> dict[str, Any]:
    """Добавя липсващи полета и нормализира типовете след редакция от интерфейса."""

    result = default_planning_preferences(profile_code, today)
    if preferences:
        for key, value in preferences.items():
            if key in {"annual_goal_component_weights", "manual_positions"} and isinstance(value, dict):
                result[key].update(value)
            else:
                result[key] = value

    result["season_start"] = pd.Timestamp(result["season_start"]).normalize()
    result["season_end"] = pd.Timestamp(result["season_end"]).normalize()
    if result["season_end"] <= result["season_start"]:
        result["season_end"] = result["season_start"] + pd.Timedelta(days=364)

    result["annual_target_hours"] = max(1.0, float(result.get("annual_target_hours", 550.0)))
    result["annual_goal_influence"] = float(np.clip(result.get("annual_goal_influence", 0.35), 0.0, 1.0))
    result["min_volume_factor"] = float(np.clip(result.get("min_volume_factor", 0.88), 0.50, 1.0))
    result["max_volume_factor"] = float(np.clip(result.get("max_volume_factor", 1.15), 1.0, 1.50))

    result["rest_days"] = sorted({int(day) for day in result.get("rest_days", []) if 0 <= int(day) <= 6})
    # Оставяме поне един активен ден. Това предпазва генератора от невъзможна
    # конфигурация „7 почивни дни и поне 1 заявена тренировка“.
    if len(result["rest_days"]) >= 7:
        result["rest_days"] = [0]
    result["double_session_days"] = sorted(
        {int(day) for day in result.get("double_session_days", []) if 0 <= int(day) <= 6}
    )
    result["intensity_days"] = sorted({int(day) for day in result.get("intensity_days", []) if 0 <= int(day) <= 6})
    result["strength_days"] = sorted({int(day) for day in result.get("strength_days", []) if 0 <= int(day) <= 6})
    result["long_session_day"] = int(np.clip(result.get("long_session_day", 6), 0, 6))
    result["double_threshold_day"] = int(np.clip(result.get("double_threshold_day", 2), 0, 6))

    max_possible = max(1, 2 * (7 - len(result["rest_days"])))
    result["sessions_per_week"] = int(np.clip(result.get("sessions_per_week", 8), 1, max_possible))
    result["max_key_sessions_per_week"] = int(np.clip(result.get("max_key_sessions_per_week", 3), 0, 8))
    result["double_threshold_enabled"] = bool(result.get("double_threshold_enabled", False))
    result["double_threshold_min_readiness"] = float(
        np.clip(result.get("double_threshold_min_readiness", 88.0), 60.0, 100.0)
    )
    result["double_threshold_phase_min"] = float(
        np.clip(result.get("double_threshold_phase_min", 0.30), 0.0, 1.0)
    )
    result["double_threshold_phase_max"] = float(
        np.clip(result.get("double_threshold_phase_max", 0.88), result["double_threshold_phase_min"], 1.0)
    )
    result["between_sessions_recovery_days"] = float(
        np.clip(result.get("between_sessions_recovery_days", 0.30), 0.05, 0.75)
    )

    components = [c for c in result.get("double_threshold_components", ["Z3", "Z4"]) if c in {"Z3", "Z4"}]
    result["double_threshold_components"] = components or ["Z3", "Z4"]
    result["annual_goal_component_weights"] = {
        component: float(np.clip(result["annual_goal_component_weights"].get(component, 0.0), 0.0, 1.5))
        for component in COMPONENTS
    }
    result["manual_positions"] = {
        component: float(np.clip(result["manual_positions"].get(component, DEFAULT_MANUAL_POSITIONS[component]), 0.0, 1.0))
        for component in AEROBIC_COMPONENTS
    }
    return result


def annual_volume_context(
    activity_summaries: pd.DataFrame,
    preferences: dict[str, Any],
    as_of: date | pd.Timestamp | None = None,
) -> dict[str, Any]:
    """Изчислява изпълнение и безопасен коригиращ фактор към годишната обемна цел.

    Годишната цел не замества 7/40. Тя създава ограничен контекстен фактор, който
    влияе най-силно върху Z1/Z2 и много слабо върху високите интензивности.
    """

    prefs = normalize_preferences(preferences, today=as_of)
    current = pd.Timestamp(as_of or date.today()).normalize()
    start = pd.Timestamp(prefs["season_start"]).normalize()
    end = pd.Timestamp(prefs["season_end"]).normalize()
    target_minutes = float(prefs["annual_target_hours"]) * 60.0

    total_season_days = max(1, (end - start).days + 1)
    elapsed_days = int(np.clip((current - start).days + 1, 0, total_season_days))
    remaining_days = max(0, (end - max(current, start)).days)

    completed_minutes = 0.0
    completed_sessions = 0
    unique_history_days = 0
    season_history_coverage = 0.0
    recent_weekly_minutes = 0.0
    if not activity_summaries.empty and "date" in activity_summaries:
        data = activity_summaries.copy()
        data["date"] = pd.to_datetime(data["date"]).dt.normalize()
        real_cols = [f"real_{component}" for component in COMPONENTS if f"real_{component}" in data]
        for col in real_cols:
            data[col] = pd.to_numeric(data[col], errors="coerce").fillna(0.0).clip(lower=0.0)
        data["real_total"] = data[real_cols].sum(axis=1) if real_cols else 0.0
        season = data.loc[(data["date"] >= start) & (data["date"] <= min(current, end))]
        completed_minutes = float(season["real_total"].sum())
        completed_sessions = int((season["real_total"] > 0).sum())
        positive_season = season.loc[season["real_total"] > 0]
        unique_history_days = int(positive_season["date"].nunique())
        if not positive_season.empty and elapsed_days > 0:
            earliest_history = max(start, pd.Timestamp(positive_season["date"].min()).normalize())
            history_span_days = max(1, (min(current, end) - earliest_history).days + 1)
            season_history_coverage = float(np.clip(history_span_days / elapsed_days, 0.0, 1.0))

        recent_start = current - pd.Timedelta(days=27)
        recent = data.loc[(data["date"] >= recent_start) & (data["date"] <= current)]
        recent_weekly_minutes = float(recent["real_total"].sum()) / 4.0

    expected_to_date = target_minutes * elapsed_days / total_season_days
    remaining_target = max(0.0, target_minutes - completed_minutes)
    remaining_weeks = max(1.0, remaining_days / 7.0)
    required_weekly = remaining_target / remaining_weeks if remaining_days > 0 else 0.0
    target_average_weekly = target_minutes / (total_season_days / 7.0)

    if recent_weekly_minutes > 1.0 and remaining_days > 0:
        raw_ratio = required_weekly / recent_weekly_minutes
        bounded_ratio = float(np.clip(raw_ratio, 0.70, 1.30))
        reliability = float(np.clip(unique_history_days / 40.0, 0.0, 1.0))
        # Ако потребителят е въвел само последните 40 дни, не приемаме, че
        # липсващите месеци са били нулеви. Покритието на сезона намалява
        # влиянието на часовата цел, докато не бъде въведена по-пълна история.
        coverage_weight = 0.20 + 0.80 * season_history_coverage
        influence = float(prefs["annual_goal_influence"]) * (0.35 + 0.65 * reliability) * coverage_weight
        factor = 1.0 + influence * (bounded_ratio - 1.0)
    else:
        raw_ratio = 1.0
        reliability = float(np.clip(unique_history_days / 40.0, 0.0, 1.0))
        factor = 1.0

    factor = float(np.clip(factor, prefs["min_volume_factor"], prefs["max_volume_factor"]))
    forecast_minutes = completed_minutes + recent_weekly_minutes * max(0.0, remaining_days / 7.0)
    progress_pct = 100.0 * completed_minutes / max(target_minutes, 1e-9)
    expected_pct = 100.0 * expected_to_date / max(target_minutes, 1e-9)
    gap_hours = (completed_minutes - expected_to_date) / 60.0

    if gap_hours > max(5.0, 0.03 * prefs["annual_target_hours"]):
        status = "Над линейната цел"
    elif gap_hours < -max(5.0, 0.03 * prefs["annual_target_hours"]):
        status = "Под линейната цел"
    else:
        status = "В рамките на целта"

    return {
        "season_start": start,
        "season_end": end,
        "target_hours": float(prefs["annual_target_hours"]),
        "completed_hours": completed_minutes / 60.0,
        "completed_sessions": completed_sessions,
        "progress_pct": progress_pct,
        "expected_pct": expected_pct,
        "expected_hours_to_date": expected_to_date / 60.0,
        "gap_hours": gap_hours,
        "remaining_hours": remaining_target / 60.0,
        "remaining_weeks": remaining_weeks,
        "required_weekly_hours": required_weekly / 60.0,
        "target_average_weekly_hours": target_average_weekly / 60.0,
        "recent_weekly_hours": recent_weekly_minutes / 60.0,
        "forecast_hours": forecast_minutes / 60.0,
        "raw_required_to_recent_ratio": raw_ratio,
        "volume_factor": factor,
        "history_reliability": reliability,
        "season_history_coverage": season_history_coverage,
        "status": status,
    }


def _priority_days(preferences: dict[str, Any]) -> list[int]:
    prefs = normalize_preferences(preferences)
    ordered: list[int] = []
    candidates: Iterable[int] = [
        prefs["double_threshold_day"],
        *prefs["intensity_days"],
        *prefs["strength_days"],
        prefs["long_session_day"],
        *range(7),
    ]
    for day in candidates:
        if day in prefs["rest_days"] or day in ordered:
            continue
        ordered.append(day)
    return ordered


def build_week_structure(
    week_start: date | pd.Timestamp,
    preferences: dict[str, Any],
    sessions_override: int | None = None,
) -> pd.DataFrame:
    """Създава точни слотове за следващите 7 дни от избраната дата.

    Предпочитанията са по реален ден от седмицата, независимо дали планът започва
    в понеделник или в друг ден. Връщат се и редове за пълна почивка.
    """

    prefs = normalize_preferences(preferences, today=week_start)
    start = pd.Timestamp(week_start).normalize()
    offsets = list(range(7))
    weekday_for_offset = {offset: int((start + pd.Timedelta(days=offset)).weekday()) for offset in offsets}
    offset_for_weekday = {weekday: offset for offset, weekday in weekday_for_offset.items()}
    rest_days = set(prefs["rest_days"])
    active_offsets = [offset for offset in offsets if weekday_for_offset[offset] not in rest_days]
    max_sessions = max(1, 2 * len(active_offsets))
    desired = int(np.clip(sessions_override or prefs["sessions_per_week"], 1, max_sessions))

    counts = {offset: 0 for offset in offsets}
    priority_weekdays = _priority_days(prefs)
    priority_offsets = [offset_for_weekday[weekday] for weekday in priority_weekdays if weekday in offset_for_weekday]

    for offset in priority_offsets[: min(desired, len(active_offsets))]:
        counts[offset] = 1

    dt_weekday = prefs["double_threshold_day"]
    dt_offset = offset_for_weekday.get(dt_weekday)
    if prefs["double_threshold_enabled"] and dt_offset in active_offsets and desired >= 2:
        counts[dt_offset] = max(1, counts[dt_offset])
        current_total = sum(counts.values())
        if current_total < desired:
            counts[dt_offset] = 2
        elif counts[dt_offset] < 2:
            removable = [offset for offset in reversed(priority_offsets) if offset != dt_offset and counts[offset] == 1]
            if removable:
                counts[removable[0]] = 0
                counts[dt_offset] = 2

    extra_weekdays: list[int] = []
    for weekday in [
        *prefs["double_session_days"],
        *prefs["intensity_days"],
        *prefs["strength_days"],
        prefs["long_session_day"],
        *range(7),
    ]:
        if weekday in rest_days or weekday in extra_weekdays:
            continue
        extra_weekdays.append(weekday)
    extra_offsets = [offset_for_weekday[weekday] for weekday in extra_weekdays if weekday in offset_for_weekday]

    while sum(counts.values()) < desired:
        changed = False
        for offset in extra_offsets:
            if offset in active_offsets and counts[offset] < 2:
                counts[offset] += 1
                changed = True
                if sum(counts.values()) >= desired:
                    break
        if not changed:
            break

    rows: list[dict[str, Any]] = []
    for offset in offsets:
        current_date = start + pd.Timedelta(days=offset)
        weekday = weekday_for_offset[offset]
        count = counts[offset]
        if count == 0:
            rows.append(
                {
                    "date": current_date,
                    "day_offset": offset,
                    "day_index": weekday,
                    "day": WEEKDAY_LABELS[weekday],
                    "session_no": 0,
                    "time_of_day": "Почивка",
                    "slot_type": "rest",
                    "planned_training": False,
                }
            )
            continue
        for session_no in range(1, count + 1):
            if count == 1:
                time_of_day = "Основна"
            else:
                time_of_day = "Сутрин" if session_no == 1 else "Следобед"
            slot_type = "normal"
            if prefs["double_threshold_enabled"] and weekday == prefs["double_threshold_day"] and count == 2:
                slot_type = "double_threshold"
            elif session_no == 2:
                slot_type = "double"
            elif weekday == prefs["long_session_day"]:
                slot_type = "long"
            elif weekday in prefs["intensity_days"]:
                slot_type = "intensity"
            elif weekday in prefs["strength_days"]:
                slot_type = "strength"
            rows.append(
                {
                    "date": current_date,
                    "day_offset": offset,
                    "day_index": weekday,
                    "day": WEEKDAY_LABELS[weekday],
                    "session_no": session_no,
                    "time_of_day": time_of_day,
                    "slot_type": slot_type,
                    "planned_training": True,
                }
            )
    return pd.DataFrame(rows).sort_values(["date", "session_no"]).reset_index(drop=True)


def daily_history_from_activities(activities: pd.DataFrame, athlete_id: str) -> pd.DataFrame:
    """Агрегира активностите до удобна за ръчна редакция дневна таблица."""

    columns = ["date", "sport", "rpe", *COMPONENTS, "note"]
    if activities.empty or "athlete_id" not in activities:
        return pd.DataFrame(columns=columns)
    data = activities.loc[activities["athlete_id"].astype(str) == str(athlete_id)].copy()
    if data.empty:
        return pd.DataFrame(columns=columns)
    data["date"] = pd.to_datetime(data["date"]).dt.normalize()
    for component in COMPONENTS:
        data[f"real_{component}"] = pd.to_numeric(data.get(f"real_{component}", 0.0), errors="coerce").fillna(0.0)
    data["rpe"] = pd.to_numeric(data.get("rpe", np.nan), errors="coerce")
    data["sport"] = data.get("sport", "Ръчно въведена").fillna("Ръчно въведена").astype(str)
    data["notes"] = data.get("notes", "").fillna("").astype(str)

    grouped = data.groupby("date", as_index=False).agg(
        sport=("sport", lambda values: " + ".join(dict.fromkeys(values))[:80]),
        rpe=("rpe", "mean"),
        note=("notes", lambda values: " | ".join(v for v in values if v)[:180]),
        **{component: (f"real_{component}", "sum") for component in COMPONENTS},
    )
    grouped["rpe"] = grouped["rpe"].fillna(4.0).round(1)
    return grouped[columns].sort_values("date").reset_index(drop=True)


def _manual_activity_row(
    athlete_id: str,
    row: pd.Series | dict[str, Any],
    positions: dict[str, float],
    suffix: str,
) -> dict[str, Any]:
    values = dict(row)
    timestamp = pd.Timestamp(values["date"]).normalize()
    real = {component: max(0.0, float(values.get(component, values.get(f"real_{component}", 0.0)) or 0.0)) for component in COMPONENTS}
    moving = sum(real.values())
    rpe = float(np.clip(values.get("rpe", 4.0) or 4.0, 0.0, 10.0))
    result: dict[str, Any] = {
        "activity_id": f"{athlete_id}-MAN-{timestamp.strftime('%Y%m%d')}-{suffix}",
        "athlete_id": str(athlete_id),
        "date": timestamp,
        "sport": str(values.get("sport", "Ръчно въведена")) or "Ръчно въведена",
        "source": str(values.get("source", "manual_entry")) or "manual_entry",
        "status": str(values.get("status", "Изпълнена")) or "Изпълнена",
        "moving_min": round(moving, 2),
        "elapsed_min": round(moving + (5.0 if moving >= 45 else 2.0 if moving > 0 else 0.0), 2),
        "rpe": rpe,
        "strength_k": float(np.clip(values.get("strength_k", 1.0 + 0.06 * max(0.0, rpe - 5.0)), 0.5, 2.0)),
        "quality_score": float(np.clip(values.get("quality_score", 1.0), 0.0, 1.0)),
        "notes": str(values.get("note", values.get("notes", "Ръчно въведена история."))),
    }
    for component in COMPONENTS:
        result[f"real_{component}"] = round(real[component], 2)
    for component in AEROBIC_COMPONENTS:
        result[f"pos_{component}"] = float(
            np.clip(values.get(f"pos_{component}", positions.get(component, DEFAULT_MANUAL_POSITIONS[component])), 0.0, 1.0)
        )
    return result


def daily_table_to_activities(
    table: pd.DataFrame,
    athlete_id: str,
    preferences: dict[str, Any],
    source: str = "manual_daily_table",
) -> pd.DataFrame:
    """Преобразува една дневна редица в нормализирани активности."""

    prefs = normalize_preferences(preferences)
    if table is None or table.empty:
        return pd.DataFrame()
    data = table.copy()
    rename = {
        "Дата": "date",
        "Спорт": "sport",
        "Бележка": "note",
        "RPE": "rpe",
        **{f"real_{component}": component for component in COMPONENTS},
    }
    data = data.rename(columns={key: value for key, value in rename.items() if key in data.columns})
    if "date" not in data:
        raise ValueError("Липсва колона date/Дата.")
    data["date"] = pd.to_datetime(data["date"], errors="coerce").dt.normalize()
    data = data.dropna(subset=["date"])
    rows: list[dict[str, Any]] = []
    for index, row in data.iterrows():
        if sum(max(0.0, float(row.get(component, 0.0) or 0.0)) for component in COMPONENTS) <= 0:
            continue
        values = row.to_dict()
        values["source"] = source
        rows.append(_manual_activity_row(athlete_id, values, prefs["manual_positions"], f"D{index+1:03d}"))
    return pd.DataFrame(rows)


def weekly_totals_to_activities(
    weekly_table: pd.DataFrame,
    athlete_id: str,
    preferences: dict[str, Any],
) -> pd.DataFrame:
    """Разпределя седмични тотали в детерминирана начална дневна история.

    Това е onboarding удобство. Маркира се с отделен source, защото дневното
    разпределение е приблизително и не трябва да се представя като секундни данни.
    """

    prefs = normalize_preferences(preferences)
    if weekly_table is None or weekly_table.empty:
        return pd.DataFrame()
    data = weekly_table.copy().rename(columns={"Начало на седмицата": "week_start", "Сесии": "sessions"})
    if "week_start" not in data:
        raise ValueError("Липсва колона week_start/Начало на седмицата.")
    data["week_start"] = pd.to_datetime(data["week_start"], errors="coerce").dt.normalize()
    data = data.dropna(subset=["week_start"])

    rows: list[dict[str, Any]] = []
    for week_index, weekly in data.iterrows():
        sessions = int(np.clip(weekly.get("sessions", prefs["sessions_per_week"]) or prefs["sessions_per_week"], 1, 14))
        structure = build_week_structure(weekly["week_start"], prefs, sessions_override=sessions)
        slots = structure.loc[structure["planned_training"]].copy().reset_index(drop=True)
        if slots.empty:
            continue

        weights: dict[str, np.ndarray] = {}
        day_idx = slots["day_index"].to_numpy(dtype=int)
        slot_type = slots["slot_type"].astype(str).to_numpy()
        weights["Z1"] = np.ones(len(slots), dtype=float)
        weights["Z2"] = np.where(day_idx == prefs["long_session_day"], 2.4, 1.0)
        weights["Z2"] *= np.where(np.isin(slot_type, ["intensity", "double_threshold"]), 0.55, 1.0)
        intensity_mask = np.isin(day_idx, prefs["intensity_days"]) | np.isin(slot_type, ["intensity", "double_threshold"])
        weights["Z3"] = np.where(intensity_mask, 1.0, 0.08)
        weights["Z4"] = np.where(intensity_mask, 1.0, 0.03)
        weights["Z5"] = np.where(intensity_mask & (slots["session_no"].to_numpy() == 1), 1.0, 0.01)
        strength_mask = np.isin(day_idx, prefs["strength_days"]) | (slot_type == "strength")
        weights["STR"] = np.where(strength_mask, 1.0, 0.03)

        allocated = pd.DataFrame(0.0, index=slots.index, columns=COMPONENTS)
        for component in COMPONENTS:
            total = max(0.0, float(weekly.get(component, weekly.get(f"real_{component}", 0.0)) or 0.0))
            component_weights = weights[component]
            denominator = float(component_weights.sum())
            if total > 0 and denominator > 0:
                allocated[component] = total * component_weights / denominator

        for slot_index, slot in slots.iterrows():
            values: dict[str, Any] = {
                "date": slot["date"],
                "sport": str(weekly.get("sport", "Седмична ръчна история")),
                "rpe": float(weekly.get("rpe", 4.5) or 4.5),
                "note": str(weekly.get("note", "Разпределено от седмичен обем.")),
                "source": "manual_weekly_distribution",
            }
            for component in COMPONENTS:
                values[component] = float(allocated.loc[slot_index, component])
            rows.append(
                _manual_activity_row(
                    athlete_id,
                    values,
                    prefs["manual_positions"],
                    f"W{week_index+1:03d}S{int(slot['session_no'])}",
                )
            )
    return pd.DataFrame(rows)


def history_template() -> pd.DataFrame:
    """Минимален CSV шаблон за ръчно въвеждане на дневна история."""

    today = pd.Timestamp.today().normalize() - pd.Timedelta(days=1)
    return pd.DataFrame(
        [
            {
                "date": today.date().isoformat(),
                "sport": "Ролкови ски",
                "rpe": 4.0,
                "Z1": 25.0,
                "Z2": 65.0,
                "Z3": 0.0,
                "Z4": 0.0,
                "Z5": 0.0,
                "STR": 0.0,
                "note": "Примерен ред — изтрий или промени.",
            }
        ]
    )
