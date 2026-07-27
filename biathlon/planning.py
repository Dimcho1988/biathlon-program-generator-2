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
    STRENGTH_COEFFICIENTS,
    STRENGTH_LABELS,
    STRENGTH_TYPES,
)
from .mesocycles import (
    build_mesocycle_schedule,
    normalize_camp_prescriptions,
)
from .physiology import (
    apply_training_impulse,
    effective_from_direct_vector,
    recover_fatigue,
    solve_direct_load,
    target_weekly_effective,
)
from .preferences import WEEKDAY_LABELS, build_week_structure, normalize_preferences
from .testing import aggregate_test_effects

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
    if 0 <= weeks_to_race <= 2:
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
    return (
        subset.loc[
            (subset["start_date"] <= week_end)
            & (subset["end_date"] >= week_start)
        ]
        .drop_duplicates(subset=["athlete_id", "event_id"], keep="last")
        .copy()
    )


def _resolved_accent_components(
    schedule_row: pd.Series,
    envelopes: dict[str, float],
) -> tuple[list[str], list[str], dict[str, float]]:
    """Resolve manual, automatic, or complementary component priorities."""

    limit = int(np.clip(schedule_row.get("accent_limit", 2), 1, len(COMPONENTS)))
    manual_weights = {
        component: float(
            np.clip(schedule_row.get(f"accent_{component}", 0.0), 0.0, 1.0)
        )
        for component in COMPONENTS
    }
    manual = [
        component
        for component in COMPONENTS
        if manual_weights[component] > 0.0
    ]
    manual.sort(
        key=lambda component: (
            -manual_weights[component],
            COMPONENTS.index(component),
        )
    )
    automatic = sorted(
        COMPONENTS,
        key=lambda component: (
            -float(envelopes[component]),
            COMPONENTS.index(component),
        ),
    )

    strategy = str(schedule_row.get("accent_strategy", "AUTO"))
    mode = str(schedule_row.get("accent_mode", "AUTO"))
    if strategy == "COMPLEMENT":
        excluded = (
            manual[:limit]
            if mode == "MANUAL" and manual
            else automatic[:limit]
        )
        candidates = [component for component in automatic if component not in excluded]
        if not candidates:
            candidates = automatic
        accents = candidates[:limit]
        return accents, excluded, manual_weights

    if strategy == "CAMP" and mode == "MANUAL" and manual:
        accents = manual[:limit]
        return accents, [], manual_weights
    return automatic[:limit], [], manual_weights


def _structured_component_factor(
    component: str,
    accents: list[str],
    manual_weights: dict[str, float],
    schedule_row: pd.Series,
) -> float:
    if component not in accents:
        return float(schedule_row.get("maintenance_factor", 1.0))
    weight = float(manual_weights.get(component, 0.0))
    if str(schedule_row.get("accent_mode", "AUTO")) != "MANUAL" or weight <= 0.0:
        weight = 1.0
    target = float(
        schedule_row.get(
            "volume_factor"
            if component in {"Z1", "Z2", "Z3"}
            else "stress_factor",
            1.0,
        )
    )
    return 1.0 + weight * (target - 1.0)


def build_weekly_targets(
    load_stats: pd.DataFrame,
    integrated: pd.DataFrame,
    calendar: pd.DataFrame,
    athlete_id: str,
    parameters: dict[str, Any],
    start_date: date | pd.Timestamp,
    minimum_weeks: int = 16,
    planning_preferences: dict[str, Any] | None = None,
    annual_context: dict[str, Any] | None = None,
    test_effects: pd.DataFrame | None = None,
    camp_prescriptions: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Изгражда компонентна вълна до основния старт."""

    start = pd.Timestamp(start_date).normalize()
    preferences = normalize_preferences(planning_preferences, today=start)
    annual = annual_context or {}
    annual_volume_factor = float(annual.get("volume_factor", 1.0))
    goal_weights = preferences.get("annual_goal_component_weights", {component: 0.0 for component in COMPONENTS})
    main_event = _future_main_event(calendar, athlete_id, start)
    if main_event is None:
        main_date = start + pd.Timedelta(weeks=minimum_weeks)
        main_name = "Виртуален основен старт"
    else:
        main_date = pd.Timestamp(main_event["start_date"]).normalize()
        main_name = str(main_event["name"])
    weeks = max(minimum_weeks, int(np.ceil((main_date - start).days / 7.0)) + 1)
    week_starts = [
        start + pd.Timedelta(days=7 * week_index)
        for week_index in range(weeks)
    ]
    normalized_camp_prescriptions = normalize_camp_prescriptions(
        camp_prescriptions
    )
    normalized_camp_prescriptions = normalized_camp_prescriptions.loc[
        normalized_camp_prescriptions["athlete_id"].astype(str)
        == str(athlete_id)
    ].copy()
    prescribed_camp_ids = set(
        normalized_camp_prescriptions["event_id"].astype(str)
    )
    future_main_events = calendar.loc[
        (calendar["athlete_id"].astype(str) == str(athlete_id))
        & (calendar["type"].astype(str) == "MAIN_RACE")
    ].copy()
    if not future_main_events.empty:
        future_main_events["start_date"] = pd.to_datetime(
            future_main_events["start_date"],
            errors="coerce",
        ).dt.normalize()
        future_main_events = (
            future_main_events.dropna(subset=["start_date"])
            .sort_values(["start_date", "event_id"], kind="stable")
            .reset_index(drop=True)
        )
    mesocycle_schedule = build_mesocycle_schedule(
        week_starts,
        calendar,
        normalized_camp_prescriptions,
        athlete_id,
        parameters,
        preferences,
    )

    rows: list[dict[str, Any]] = []
    for week_index, week_start in enumerate(week_starts):
        upcoming_main = future_main_events.loc[
            future_main_events["start_date"] >= week_start
        ]
        if upcoming_main.empty:
            active_main_date = main_date
            active_main_name = main_name
        else:
            active_main = upcoming_main.iloc[0]
            active_main_date = pd.Timestamp(
                active_main["start_date"]
            ).normalize()
            active_main_name = str(active_main["name"])
        weeks_to_race = int(
            np.ceil((active_main_date - week_start).days / 7.0)
        )
        taper_active = 0 <= weeks_to_race <= 2
        progress = float(
            np.clip(
                (week_start - start).days
                / max((main_date - start).days, 1),
                0.0,
                1.0,
            )
        )
        global_factor = _global_progression(prog := progress)
        mesocycle_row = mesocycle_schedule.iloc[week_index]
        meso_factor = float(mesocycle_row["mesocycle_factor"])
        mesocycle_type = str(mesocycle_row["mesocycle_type"])
        envelopes = {component: _component_envelope(component, progress) for component in COMPONENTS}
        accent_components, previous_accents, manual_accent_weights = (
            _resolved_accent_components(mesocycle_row, envelopes)
        )
        base_schedule_row = mesocycle_row.copy()
        base_schedule_row["accent_strategy"] = "AUTO"
        base_schedule_row["accent_mode"] = "AUTO"
        base_accent_components, _, _ = _resolved_accent_components(
            base_schedule_row,
            envelopes,
        )
        events = _events_in_week(calendar, athlete_id, week_start)
        event_names = ", ".join(events["name"].astype(str).tolist()) if not events.empty else ""
        planning_test_adjustments = aggregate_test_effects(
            test_effects if test_effects is not None else pd.DataFrame(),
            parameters,
            channel="planning",
            future_days=7 * week_index,
        )

        indices: dict[str, float] = {}
        metadata: dict[str, dict[str, Any]] = {}
        for component in COMPONENTS:
            hard_flag = bool(
                integrated.loc[component].get("hard_flag", False)
            )
            component_role = (
                "Допълващ акцент"
                if str(mesocycle_row["accent_strategy"]) == "COMPLEMENT"
                and component in accent_components
                else "Акцент"
                if component in accent_components
                else "Поддържане"
            )
            status = component_role
            if (
                envelopes[component] < 0.98
                and str(mesocycle_row["accent_strategy"]) == "AUTO"
            ):
                status = "Относително възстановяване"

            applied_meso_factor = meso_factor
            applied_mesocycle_type = mesocycle_type
            if (
                hard_flag
                and bool(mesocycle_row["camp_event_id"])
                and not bool(mesocycle_row["taper_locked"])
            ):
                applied_mesocycle_type = str(
                    mesocycle_row["base_mesocycle_type"]
                )
                applied_meso_factor = min(
                    applied_meso_factor,
                    float(
                        mesocycle_row.get(
                            "base_mesocycle_factor",
                            applied_meso_factor,
                        )
                    ),
                )
            index = (
                global_factor
                * applied_meso_factor
                * envelopes[component]
            )
            if applied_mesocycle_type == "RECOVERY":
                recovery_accents = (
                    base_accent_components
                    if hard_flag and bool(mesocycle_row["camp_event_id"])
                    else accent_components
                )
                recovery_status = status
                if hard_flag and bool(mesocycle_row["camp_event_id"]):
                    recovery_status = (
                        "Акцент"
                        if component in base_accent_components
                        else "Поддържане"
                    )
                    if envelopes[component] < 0.98:
                        recovery_status = "Относително възстановяване"
                if str(mesocycle_row["accent_strategy"]) == "COMPLEMENT":
                    if component in recovery_accents:
                        index *= 0.98
                        status = "Възстановителна седмица · допълващ акцент"
                    elif component in previous_accents:
                        index *= 0.72
                        status = "Възстановителна седмица · разтоварване след лагер"
                    else:
                        index *= 0.85
                        status = "Възстановителна седмица · поддържане"
                else:
                    if component in recovery_accents:
                        index *= 0.72
                    elif recovery_status == "Поддържане":
                        index *= 0.85
                    else:
                        index *= 0.98
                    status = (
                        "Възстановителна седмица · "
                        + recovery_status.lower()
                    )
            elif str(mesocycle_row["camp_event_id"]):
                status = (
                    "Лагер · изграждащ · " + component_role.lower()
                    if mesocycle_type == "BUILD"
                    else "Лагер · поддържащ · " + component_role.lower()
                )

            calendar_factor = 1.0
            if not events.empty:
                for _, event in events.iterrows():
                    event_type = str(event["type"])
                    if event_type == "CAMP":
                        event_id = str(event.get("event_id", ""))
                        if bool(mesocycle_row["taper_locked"]):
                            continue
                        if (
                            event_id in prescribed_camp_ids
                            and event_id == str(mesocycle_row["camp_event_id"])
                        ):
                            raw_factor = _structured_component_factor(
                                component,
                                accent_components,
                                manual_accent_weights,
                                mesocycle_row,
                            )
                            coverage = float(
                                np.clip(
                                    float(mesocycle_row["camp_overlap_days"]) / 7.0,
                                    0.0,
                                    1.0,
                                )
                            )
                            calendar_factor *= 1.0 + (raw_factor - 1.0) * coverage
                        elif event_id not in prescribed_camp_ids:
                            calendar_factor *= (
                                1.07
                                if component in {"Z1", "Z2", "Z3", "STR"}
                                else 0.98
                            )
                    elif event_type == "CONTROL_RACE":
                        calendar_factor *= 0.90 if component in {"Z1", "Z2", "STR"} else 0.98
                    elif event_type == "UNAVAILABLE":
                        calendar_factor *= 0.85
                    elif event_type == "TEST":
                        calendar_factor *= 0.96
            if hard_flag and bool(mesocycle_row["camp_event_id"]):
                calendar_factor = min(calendar_factor, 1.0)

            accent_factor = 1.0
            if (
                str(mesocycle_row["accent_strategy"]) == "COMPLEMENT"
                and mesocycle_type not in {"RECOVERY", "TAPER"}
            ):
                accent_factor = _structured_component_factor(
                    component,
                    accent_components,
                    manual_accent_weights,
                    mesocycle_row,
                )
            if hard_flag and bool(mesocycle_row["camp_event_id"]):
                accent_factor = min(accent_factor, 1.0)
                status = "Ограничение по wellness · " + status.lower()

            # Тейпър: редуцира обема, но пази специфичните компоненти.
            taper_factor = 1.0
            if weeks_to_race == 2:
                taper_factor = 0.84 if component in {"Z1", "Z2", "STR"} else 0.94
            elif 0 <= weeks_to_race <= 1:
                taper_factor = 0.66 if component in {"Z1", "Z2", "STR"} else 0.90
            if taper_active:
                status = "Тейпър"

            # Годишната цел е ограничен контекст, а не заместител на 7/40.
            # В предишната версия влиянието затихваше много бързо и различни
            # годишни цели изглеждаха почти еднакво. Тук то остава видимо в
            # целия показан хоризонт, но се намалява в тейпъра.
            goal_horizon_weight = 1.0 if week_index < 8 else 0.85 + 0.15 * float(np.exp(-(week_index - 8) / 12.0))
            goal_taper_damping = 0.25 if taper_active else 1.0
            goal_factor = (
                1.0
                + (annual_volume_factor - 1.0)
                * float(goal_weights.get(component, 0.0))
                * goal_horizon_weight
                * goal_taper_damping
            )

            adaptive = float(integrated.loc[component, "adaptive_multiplier"])
            adaptive_near_term = 1.0 + (adaptive - 1.0) * float(np.exp(-week_index / 2.0))
            test_adjustment = float(planning_test_adjustments.get(component, 0.0))
            test_factor = 1.0 + test_adjustment
            index *= (
                calendar_factor
                * accent_factor
                * taper_factor
                * goal_factor
                * adaptive_near_term
                * test_factor
            )
            # Компонентно специфичен горен лимит: нискоинтензивният обем може
            # да реагира по-видимо на сезонната цел, докато високите зони
            # остават по-строго ограничени.
            upper_index = {
                "Z1": 1.55,
                "Z2": 1.50,
                "Z3": 1.40,
                "Z4": 1.35,
                "Z5": 1.30,
                "STR": 1.40,
            }[component]
            index = float(np.clip(index, 0.65, upper_index))
            indices[component] = index
            metadata[component] = {
                "status": status,
                "component_role": component_role,
                "calendar_factor": calendar_factor,
                "accent_factor": accent_factor,
                "taper_factor": taper_factor,
                "annual_goal_factor": goal_factor,
                "adaptive_near_term": adaptive_near_term,
                "test_factor": test_factor,
                "envelope": envelopes[component],
                "applied_mesocycle_factor": applied_meso_factor,
                "applied_mesocycle_type": applied_mesocycle_type,
                "safety_limited": bool(
                    hard_flag
                    and bool(mesocycle_row["camp_event_id"])
                ),
            }

        weekly_effective = target_weekly_effective(load_stats, indices)
        for component in COMPONENTS:
            row = {
                "week_start": week_start,
                "week_no": week_index + 1,
                "weeks_to_main_race": weeks_to_race,
                "main_race": active_main_name,
                "main_race_date": active_main_date,
                "phase_progress": progress,
                "phase": _phase_name(progress, weeks_to_race),
                "mesocycle_id": str(mesocycle_row["mesocycle_id"]),
                "base_mesocycle_week": int(
                    mesocycle_row["base_mesocycle_week"]
                ),
                "mesocycle_week": int(mesocycle_row["mesocycle_week"]),
                "base_mesocycle_type": str(
                    mesocycle_row["base_mesocycle_type"]
                ),
                "base_mesocycle_factor": float(
                    mesocycle_row.get(
                        "base_mesocycle_factor",
                        meso_factor,
                    )
                ),
                "mesocycle_type": mesocycle_type,
                "mesocycle_length_weeks": int(
                    mesocycle_row["mesocycle_length_weeks"]
                ),
                "mesocycle_factor": meso_factor,
                "applied_mesocycle_factor": metadata[component][
                    "applied_mesocycle_factor"
                ],
                "applied_mesocycle_type": metadata[component][
                    "applied_mesocycle_type"
                ],
                "component_role": metadata[component]["component_role"],
                "accent_components": ", ".join(accent_components),
                "complemented_components": ", ".join(previous_accents),
                "accent_strategy": str(mesocycle_row["accent_strategy"]),
                "camp_event_id": str(mesocycle_row["camp_event_id"]),
                "camp_ids": str(mesocycle_row["camp_ids"]),
                "camp_overlap_days": int(mesocycle_row["camp_overlap_days"]),
                "camp_has_prescription": bool(
                    mesocycle_row.get("camp_has_prescription", False)
                ),
                "camp_directive_note": str(
                    mesocycle_row["camp_directive_note"]
                ),
                "recovery_displaced": bool(
                    mesocycle_row["recovery_displaced"]
                ),
                "recovery_origin": str(mesocycle_row["recovery_origin"]),
                "override_reason": str(mesocycle_row["override_reason"]),
                "taper_locked": bool(mesocycle_row["taper_locked"]),
                "safety_limited": metadata[component]["safety_limited"],
                "component": component,
                "target_index": indices[component],
                "target_effective_week": float(weekly_effective[component]),
                "status": metadata[component]["status"],
                "global_factor": global_factor,
                "component_envelope": metadata[component]["envelope"],
                "calendar_factor": metadata[component]["calendar_factor"],
                "accent_factor": metadata[component]["accent_factor"],
                "taper_factor": metadata[component]["taper_factor"],
                "annual_goal_factor": metadata[component]["annual_goal_factor"],
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
        strength_type = str(method.get("strength_type", "STR_END"))
        strength_label = STRENGTH_LABELS.get(strength_type, str(method.get("title", "Силова работа")))
        coefficient = float(method.get("expected_k", STRENGTH_COEFFICIENTS.get(strength_type, 1.0)))
        equivalent = main_real * coefficient
        if strength_type == "STR_STAB":
            main = (
                f"Основна част: около {main_real:.0f} реални мин стабилизация/кор; "
                f"{main_real:.0f} × {coefficient:.1f} = {equivalent:.1f} силови еквивалентни минути."
            )
        elif strength_type == "STR_END":
            rounds = max(3, int(round(main_real / 8.0)))
            main = (
                f"Основна част: {rounds} кръга обща силова издръжливост, около {main_real:.0f} реални мин; "
                f"{main_real:.0f} × {coefficient:.1f} = {equivalent:.1f} силови еквивалентни минути."
            )
        elif strength_type == "STR_MAX":
            main = (
                f"Основна част: {strength_label}, около {main_real:.0f} реални мин работа с пълни почивки; "
                f"{main_real:.0f} × {coefficient:.1f} = {equivalent:.1f} силови еквивалентни минути."
            )
        else:
            main = (
                f"Основна част: {strength_label}, около {main_real:.0f} реални мин висококачествена работа; "
                f"{main_real:.0f} × {coefficient:.1f} = {equivalent:.1f} силови еквивалентни минути."
            )
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
    return {"Z1": 1.06, "Z2": 1.12, "Z3": 1.28, "Z4": 1.24, "Z5": 1.22, "STR": 1.00}[component]


def _strength_k_for_progress(progress: float, historical_k: float) -> float:
    """Очакван силов коефициент за бъдещите седмици.

    Историческият профил се смесва с методическия акцент на фазата, за да
    може сезонната линия да превръща STR Q в реални минути по-реалистично.
    """

    if progress < 0.20:
        phase_k = 0.95  # стабилизация + обща силова издръжливост
    elif progress < 0.55:
        phase_k = 1.05  # обща силова издръжливост + максимална сила
    elif progress < 0.80:
        phase_k = 1.18  # максимална сила с ограничена плиометрия
    else:
        phase_k = 1.28  # по-голям дял плиометрия/експлозивна работа
    return float(np.clip(0.45 * historical_k + 0.55 * phase_k, 0.80, 1.40))


def _individual_k_profile(
    activity_summaries: pd.DataFrame,
    as_of: date | pd.Timestamp,
    lookback_days: int = 120,
) -> dict[str, float]:
    """Оценява индивидуален Q/реално време коефициент за сезонната прогноза."""

    current = pd.Timestamp(as_of).normalize()
    data = activity_summaries.copy()
    if not data.empty and "date" in data:
        data["date"] = pd.to_datetime(data["date"]).dt.normalize()
        data = data.loc[data["date"] >= current - pd.Timedelta(days=lookback_days - 1)]

    profile: dict[str, float] = {}
    for component in COMPONENTS:
        default = _default_k(component)
        real_col = f"real_{component}"
        q_col = f"q_{component}"
        if data.empty or real_col not in data or q_col not in data:
            profile[component] = default
            continue
        real_total = float(pd.to_numeric(data[real_col], errors="coerce").fillna(0.0).clip(lower=0.0).sum())
        q_total = float(pd.to_numeric(data[q_col], errors="coerce").fillna(0.0).clip(lower=0.0).sum())
        if real_total <= 1.0 or q_total <= 0.0:
            profile[component] = default
            continue
        historical = float(np.clip(q_total / real_total, 0.80, 1.80))
        history_weight = float(np.clip(real_total / 360.0, 0.0, 1.0))
        profile[component] = (1.0 - history_weight) * default + history_weight * historical
    return profile


def build_volume_trajectory(
    activity_summaries: pd.DataFrame,
    weekly_targets: pd.DataFrame,
    load_stats: pd.DataFrame,
    parameters: dict[str, Any],
    planning_preferences: dict[str, Any],
    annual_context: dict[str, Any],
    as_of: date | pd.Timestamp,
    generated_plan: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Създава обща времева линия „реално → план → сезонна цел“.

    Историческата линия се изчислява само от реалните минути и не се променя
    при редакция на целта. Плановата линия се получава от бъдещите компонентни
    цели E, обратното преобразуване E→Q и индивидуалните Q/реално време
    коефициенти.
    """

    current = pd.Timestamp(as_of).normalize()
    prefs = normalize_preferences(planning_preferences, today=current)
    season_start = pd.Timestamp(prefs["season_start"]).normalize()
    season_end = pd.Timestamp(prefs["season_end"]).normalize()
    total_days = max(1, (season_end - season_start).days + 1)
    target_hours = float(annual_context.get("target_hours", prefs["annual_target_hours"]))
    required_weekly_hours = float(annual_context.get("required_weekly_hours", 0.0))
    target_average_weekly_hours = float(annual_context.get("target_average_weekly_hours", 0.0))

    rows: list[dict[str, Any]] = []
    data = activity_summaries.copy()
    if not data.empty and "date" in data:
        data["date"] = pd.to_datetime(data["date"]).dt.normalize()
        real_cols = [f"real_{component}" for component in COMPONENTS if f"real_{component}" in data]
        for col in real_cols:
            data[col] = pd.to_numeric(data[col], errors="coerce").fillna(0.0).clip(lower=0.0)
        data["real_total"] = data[real_cols].sum(axis=1) if real_cols else 0.0
        data = data.loc[(data["date"] >= season_start) & (data["date"] < current)]
        if not data.empty:
            data["week_start"] = data["date"] - pd.to_timedelta(data["date"].dt.weekday, unit="D")
            first_week = pd.Timestamp(data["week_start"].min()).normalize()
            last_week = pd.Timestamp(data["week_start"].max()).normalize()
            complete_index = pd.date_range(first_week, last_week, freq="7D")
            weekly_actual = (
                data.groupby("week_start", as_index=True)["real_total"].sum()
                .reindex(complete_index, fill_value=0.0)
                .rename_axis("week_start")
            )
            cumulative = 0.0
            for week_start, minutes in weekly_actual.items():
                hours = float(minutes) / 60.0
                cumulative += hours
                plot_date = min(pd.Timestamp(week_start) + pd.Timedelta(days=6), current)
                target_cumulative = target_hours * float(np.clip((plot_date - season_start).days + 1, 0, total_days)) / total_days
                rows.append(
                    {
                        "date": plot_date,
                        "week_start": pd.Timestamp(week_start),
                        "series_type": "actual",
                        "actual_weekly_hours": hours,
                        "planned_weekly_hours": np.nan,
                        "actual_cumulative_hours": cumulative,
                        "planned_cumulative_hours": np.nan,
                        "target_cumulative_hours": target_cumulative,
                        "required_weekly_hours": required_weekly_hours,
                        "target_average_weekly_hours": target_average_weekly_hours,
                        "inversion_error": np.nan,
                    }
                )

    completed_hours = float(annual_context.get("completed_hours", 0.0))
    target_at_current = target_hours * float(np.clip((current - season_start).days + 1, 0, total_days)) / total_days
    rows.append(
        {
            "date": current,
            "week_start": current,
            "series_type": "anchor",
            "actual_weekly_hours": np.nan,
            "planned_weekly_hours": np.nan,
            "actual_cumulative_hours": completed_hours,
            "planned_cumulative_hours": completed_hours,
            "target_cumulative_hours": target_at_current,
            "required_weekly_hours": required_weekly_hours,
            "target_average_weekly_hours": target_average_weekly_hours,
            "inversion_error": 0.0,
        }
    )

    k_profile = _individual_k_profile(activity_summaries, current)
    tref = load_stats["Tref"].reindex(COMPONENTS).astype(float)
    generated_first_week_hours = np.nan
    if generated_plan is not None and not generated_plan.empty and "total_real_min" in generated_plan:
        generated_first_week_hours = float(
            pd.to_numeric(generated_plan["total_real_min"], errors="coerce").fillna(0.0).clip(lower=0.0).sum()
        ) / 60.0

    cumulative_plan = completed_hours
    future = weekly_targets.copy()
    if not future.empty:
        future["week_start"] = pd.to_datetime(future["week_start"]).dt.normalize()
        future = future.loc[(future["week_start"] >= current) & (future["week_start"] <= season_end)]
        for plan_index, (week_start, group) in enumerate(future.groupby("week_start", sort=True)):
            target_e = (
                group.set_index("component")["target_effective_week"]
                .reindex(COMPONENTS)
                .fillna(0.0)
                .astype(float)
            )
            target_q, inversion_error = solve_direct_load(target_e, tref, parameters)
            phase_progress = float(group["phase_progress"].iloc[0]) if "phase_progress" in group else 0.5
            future_k = dict(k_profile)
            future_k["STR"] = _strength_k_for_progress(phase_progress, k_profile["STR"])
            planned_hours = float(
                sum(float(target_q[component]) / max(future_k[component], EPS) for component in COMPONENTS)
            ) / 60.0
            if plan_index == 0 and np.isfinite(generated_first_week_hours):
                planned_hours = generated_first_week_hours
            cumulative_plan += planned_hours
            plot_date = min(pd.Timestamp(week_start) + pd.Timedelta(days=6), season_end)
            target_cumulative = target_hours * float(np.clip((plot_date - season_start).days + 1, 0, total_days)) / total_days
            rows.append(
                {
                    "date": plot_date,
                    "week_start": pd.Timestamp(week_start),
                    "series_type": "plan",
                    "actual_weekly_hours": np.nan,
                    "planned_weekly_hours": planned_hours,
                    "actual_cumulative_hours": np.nan,
                    "planned_cumulative_hours": cumulative_plan,
                    "target_cumulative_hours": target_cumulative,
                    "required_weekly_hours": required_weekly_hours,
                    "target_average_weekly_hours": target_average_weekly_hours,
                    "inversion_error": float(inversion_error),
                }
            )

    return pd.DataFrame(rows).sort_values(["date", "series_type"]).reset_index(drop=True)


def _ranked_high_components(target_q: pd.Series, tref: pd.Series) -> list[str]:
    candidates = [
        component
        for component in ["Z3", "Z4", "Z5"]
        if float(target_q.get(component, 0.0)) >= max(4.0, 0.05 * float(tref.get(component, 1.0)))
    ]
    return sorted(
        candidates,
        key=lambda component: float(target_q[component]) / max(float(tref[component]), 1.0),
        reverse=True,
    )


def _focus_schedule_for_structure(
    structure: pd.DataFrame,
    target_q: pd.Series,
    tref: pd.Series,
    preferences: dict[str, Any],
    progress: float,
    integrated: pd.DataFrame,
    key_readiness_threshold: float,
) -> tuple[dict[int, str], bool]:
    """Задава методически фокус на всеки тренировъчен слот."""

    prefs = normalize_preferences(preferences)
    assignments: dict[int, str] = {}
    training_indices = structure.index[structure["planned_training"]].tolist()
    high = _ranked_high_components(target_q, tref)
    high_pointer = 0
    key_count = 0

    threshold_components = [
        component
        for component in prefs["double_threshold_components"]
        if component in {"Z3", "Z4"}
        and min(
            float(target_q.get(component, 0.0)),
            0.30 * float(tref.get(component, 0.0)),
        )
        >= 4.0
    ]
    threshold_components = sorted(
        threshold_components,
        key=lambda component: float(target_q[component]) / max(float(tref[component]), 1.0),
        reverse=True,
    )
    threshold_ready = min(
        [float(integrated.loc[component, "integrated_readiness"]) for component in threshold_components]
        or [0.0]
    )
    threshold_hard = any(bool(integrated.loc[component, "hard_flag"]) for component in threshold_components)
    double_threshold_active = bool(
        prefs["double_threshold_enabled"]
        and prefs["double_threshold_phase_min"] <= progress <= prefs["double_threshold_phase_max"]
        and threshold_ready
        >= max(
            float(prefs["double_threshold_min_readiness"]),
            float(key_readiness_threshold),
        )
        and not threshold_hard
        and len(structure.loc[structure["slot_type"] == "double_threshold"]) >= 2
        and prefs["max_key_sessions_per_week"] >= 2
    )

    if double_threshold_active:
        dt_indices = structure.index[structure["slot_type"] == "double_threshold"].tolist()[:2]
        for offset, idx in enumerate(dt_indices):
            assignments[idx] = threshold_components[offset % len(threshold_components)]
            key_count += 1

    # Силовите дни получават силов слот, когато има значима цел за сила.
    if float(target_q.get("STR", 0.0)) > 4.0:
        for day in prefs["strength_days"]:
            candidates = [
                idx
                for idx in training_indices
                if int(structure.loc[idx, "day_index"]) == day and idx not in assignments
            ]
            if candidates:
                # При двусесиен ден силата се поставя във втория слот.
                idx = candidates[-1]
                assignments[idx] = "STR"

    # Предпочитаните интензивни дни получават Z3/Z4/Z5 до зададения лимит.
    for day in prefs["intensity_days"]:
        if key_count >= prefs["max_key_sessions_per_week"] or not high:
            break
        candidates = [
            idx
            for idx in training_indices
            if int(structure.loc[idx, "day_index"]) == day and idx not in assignments
        ]
        if not candidates:
            continue
        idx = candidates[0]
        assignments[idx] = high[high_pointer % len(high)]
        high_pointer += 1
        key_count += 1

    # Ако все още има капацитет за ключови сесии, използваме свободните слотове.
    for idx in training_indices:
        if key_count >= prefs["max_key_sessions_per_week"] or not high:
            break
        if idx in assignments:
            continue
        slot_type = str(structure.loc[idx, "slot_type"])
        if slot_type not in {"intensity", "double"}:
            continue
        assignments[idx] = high[high_pointer % len(high)]
        high_pointer += 1
        key_count += 1

    # Дългата сесия е основно Z2, освен ако слотът вече е резервиран.
    for idx in training_indices:
        if idx in assignments:
            continue
        if int(structure.loc[idx, "day_index"]) == prefs["long_session_day"]:
            assignments[idx] = "Z2"
            break

    # Останалите слотове разпределят нискоинтензивния обем.
    z1_need = float(target_q.get("Z1", 0.0)) / max(float(tref.get("Z1", 1.0)), 1.0)
    z2_need = float(target_q.get("Z2", 0.0)) / max(float(tref.get("Z2", 1.0)), 1.0)
    low_cycle = ["Z2", "Z1", "Z2", "Z1"] if z2_need >= z1_need else ["Z1", "Z2", "Z1", "Z2"]
    low_pointer = 0
    for idx in training_indices:
        if idx not in assignments:
            assignments[idx] = low_cycle[low_pointer % len(low_cycle)]
            low_pointer += 1

    return assignments, double_threshold_active


def _rest_plan_row(
    slot: pd.Series,
    readiness_map: dict[str, float],
    events: pd.DataFrame,
    explanation: str,
) -> dict[str, Any]:
    overall_readiness = float(np.mean(list(readiness_map.values()))) if readiness_map else 100.0
    row: dict[str, Any] = {
        "session_id": f"{pd.Timestamp(slot['date']).date().isoformat()}-REST",
        "date": pd.Timestamp(slot["date"]).normalize(),
        "day": str(slot["day"]),
        "session_no": 0,
        "time_of_day": "Почивка",
        "slot_type": "rest",
        "planned_focus": "REST",
        "focus": "REST",
        "focus_label": "Пълна почивка / активно възстановяване",
        "method_code": "REST",
        "method": "Почивка",
        "strength_type": "",
        "strength_label": "",
        "strength_coefficient": 0.0,
        "strength_real_min": 0.0,
        "strength_equivalent_min": 0.0,
        "description": "Пълна почивка. Допуска се само кратка мобилност, разходка или възстановителна процедура според треньорското решение.",
        "total_real_min": 0.0,
        "readiness_before": round(overall_readiness, 1),
        "readiness_after": round(overall_readiness, 1),
        "key_stimulus": False,
        "double_threshold": False,
        "status": "Предложена",
        "locked": False,
        "coach_note": "",
        "explanation": explanation,
        "events": ", ".join(events["name"].astype(str).tolist()) if not events.empty else "",
    }
    for component in COMPONENTS:
        row[f"q_{component}"] = 0.0
        row[f"e_{component}"] = 0.0
        row[f"real_{component}"] = 0.0
    for strength_component in STRENGTH_TYPES:
        row[f"real_{strength_component}"] = 0.0
        row[f"q_{strength_component}"] = 0.0
    return row


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
    planning_preferences: dict[str, Any] | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    """Генерира седмица с точен брой сесии, почивни дни и двойни тренировки."""

    plan_start = pd.Timestamp(start_date).normalize()
    preferences = normalize_preferences(planning_preferences, today=plan_start)
    first_week_start = pd.Timestamp(weekly_targets["week_start"].min()).normalize()
    first_week = weekly_targets.loc[weekly_targets["week_start"] == first_week_start].set_index("component")
    target_e = first_week["target_effective_week"].reindex(COMPONENTS).astype(float)
    tref = load_stats["Tref"].reindex(COMPONENTS).astype(float)
    key_readiness_threshold = float(parameters["key_readiness_threshold"])
    target_q, inversion_error = solve_direct_load(target_e, tref, parameters)
    remaining = target_q.copy()

    progress = float(first_week["phase_progress"].iloc[0])
    phase = str(first_week["phase"].iloc[0])
    mesocycle_type = str(first_week.get("mesocycle_type", pd.Series(["—"])).iloc[0])
    mesocycle_week = int(first_week.get("mesocycle_week", pd.Series([1])).iloc[0])
    mesocycle_length = int(
        first_week.get("mesocycle_length_weeks", pd.Series([4])).iloc[0]
    )
    accent_summary = str(
        first_week.get("accent_components", pd.Series([""])).iloc[0]
    )
    mesocycle_reason = str(
        first_week.get("override_reason", pd.Series([""])).iloc[0]
    )
    structure = build_week_structure(plan_start, preferences)
    focus_assignments, double_threshold_active = _focus_schedule_for_structure(
        structure,
        target_q,
        tref,
        preferences,
        progress,
        integrated,
        key_readiness_threshold,
    )
    occurrence_source = [focus_assignments[idx] for idx in structure.index if idx in focus_assignments]
    occurrences_left = {component: occurrence_source.count(component) for component in COMPONENTS}
    fatigue = {component: float(load_readiness.loc[component, "fatigue"]) for component in COMPONENTS}
    rows: list[dict[str, Any]] = []
    planned_q_total = pd.Series(0.0, index=COMPONENTS)
    planned_e_total = pd.Series(0.0, index=COMPONENTS)
    generated_key_count = 0
    key_limit_reductions: list[str] = []
    training_slots = int(structure["planned_training"].sum())
    processed_training_slots = 0
    last_day_offset: int | None = None

    calendar_data = calendar.loc[calendar["athlete_id"].astype(str) == str(athlete_id)].copy()
    if not calendar_data.empty:
        calendar_data["start_date"] = pd.to_datetime(calendar_data["start_date"]).dt.normalize()
        calendar_data["end_date"] = pd.to_datetime(calendar_data["end_date"]).dt.normalize()

    for structure_index, slot in structure.iterrows():
        current_date = pd.Timestamp(slot["date"]).normalize()
        day_index = int(slot["day_index"])
        day_offset = int(slot.get("day_offset", (current_date - plan_start).days))
        session_no = int(slot["session_no"])

        if last_day_offset is not None:
            if day_offset > last_day_offset:
                fatigue = recover_fatigue(fatigue, float(day_offset - last_day_offset), parameters)
            elif day_offset == last_day_offset and session_no > 1:
                fatigue = recover_fatigue(
                    fatigue,
                    float(preferences["between_sessions_recovery_days"]),
                    parameters,
                )
        last_day_offset = day_offset
        readiness_map = {
            component: float(np.clip(100.0 - fatigue[component], 0.0, 100.0))
            for component in COMPONENTS
        }

        day_events = calendar_data.loc[
            (calendar_data["start_date"] <= current_date)
            & (calendar_data["end_date"] >= current_date)
        ] if not calendar_data.empty else pd.DataFrame()
        event_types = set(day_events["type"].astype(str)) if not day_events.empty else set()
        race_event = next(
            (
                row
                for _, row in day_events.iterrows()
                if str(row["type"]) in {"MAIN_RACE", "CONTROL_RACE"}
            ),
            None,
        ) if not day_events.empty else None
        unavailable = "UNAVAILABLE" in event_types

        # Състезание върху предварително зададен почивен ден създава един състезателен слот.
        is_training = bool(slot["planned_training"])
        if race_event is not None and session_no == 0:
            is_training = True
            session_no = 1
            slot = slot.copy()
            slot["time_of_day"] = "Старт"
            slot["slot_type"] = "race"

        if not is_training:
            rows.append(
                _rest_plan_row(
                    slot,
                    readiness_map,
                    day_events,
                    "Почивният ден е зададен в седмичната структура на спортиста.",
                )
            )
            continue

        processed_training_slots += 1
        planned_focus = focus_assignments.get(structure_index, "Z2")
        if race_event is not None and session_no == 1:
            planned_focus = "Z5" if str(race_event["type"]) == "MAIN_RACE" else "Z4"
        focus = planned_focus
        hard_flag = bool(integrated.loc[focus, "hard_flag"])
        is_double_threshold = bool(
            double_threshold_active
            and str(slot["slot_type"]) == "double_threshold"
            and focus in {"Z3", "Z4"}
        )

        if unavailable and session_no > 1:
            rows.append(
                _rest_plan_row(
                    slot,
                    readiness_map,
                    day_events,
                    "Вторият слот е премахнат поради календарна недостъпност.",
                )
            )
            continue
        if hard_flag and focus in {"Z3", "Z4", "Z5", "STR"} and race_event is None:
            focus = "Z1"
        if readiness_map[focus] < 65 and focus in {"Z3", "Z4", "Z5", "STR"} and race_event is None:
            focus = "Z1"
        if is_double_threshold and focus not in {"Z3", "Z4"}:
            is_double_threshold = False
        if is_double_threshold:
            required = max(
                float(preferences["double_threshold_min_readiness"]),
                key_readiness_threshold,
            )
            if readiness_map[focus] < required:
                focus = "Z1"
                is_double_threshold = False
        if unavailable:
            focus = "Z1"

        method = _select_method(methods, focus, progress, readiness_map[focus])
        session_q = pd.Series(0.0, index=COMPONENTS)

        if focus in {"Z3", "Z4", "Z5"}:
            count = max(1, occurrences_left.get(focus, 1))
            ideal_share = float(remaining[focus]) / count
            reduced_readiness_threshold = max(65.0, key_readiness_threshold - 10.0)
            readiness_factor = (
                1.0
                if readiness_map[focus] >= key_readiness_threshold
                else 0.75
                if readiness_map[focus] >= reduced_readiness_threshold
                else 0.45
            )
            cap = _phase_key_fraction(progress) * float(tref[focus]) * readiness_factor
            if is_double_threshold:
                cap = min(cap, (0.34 if session_no == 1 else 0.30) * float(tref[focus]))
            if race_event is not None:
                cap = max(cap, 0.45 * float(tref[focus]))
            main_q = min(float(remaining[focus]), ideal_share, cap)
            if main_q < 4.0 and race_event is None:
                focus = "Z1"
                method = _select_method(methods, focus, progress, readiness_map[focus])
                main_q = 0.0
            else:
                session_q[focus] = max(0.0, main_q)
                remaining[focus] = max(0.0, float(remaining[focus]) - main_q)
                occurrences_left[focus] = max(0, count - 1)
        elif focus == "STR":
            count = max(1, occurrences_left.get("STR", 1))
            ideal_share = float(remaining["STR"]) / count
            main_q = min(float(remaining["STR"]), max(6.0, ideal_share), 0.45 * float(tref["STR"]))
            if readiness_map["STR"] < max(65.0, key_readiness_threshold - 15.0) or hard_flag:
                main_q *= 0.55
            if main_q < 3.0:
                focus = "Z1"
                method = _select_method(methods, focus, progress, readiness_map[focus])
            else:
                session_q["STR"] = max(0.0, main_q)
                remaining["STR"] = max(0.0, float(remaining["STR"]) - session_q["STR"])
                occurrences_left["STR"] = max(0, count - 1)
        else:
            count = max(1, occurrences_left.get(focus, 1))
            main_q = min(
                float(remaining[focus]),
                float(remaining[focus]) / count if count else float(remaining[focus]),
            )
            if str(slot["slot_type"]) == "long" or day_index == preferences["long_session_day"]:
                main_q = min(float(remaining[focus]), max(main_q, 35.0 if focus == "Z2" else 30.0))
            session_q[focus] = max(0.0, main_q)
            remaining[focus] = max(0.0, float(remaining[focus]) - session_q[focus])
            occurrences_left[focus] = max(0, count - 1)

        # Загрявка/разпускане се добавят като директен нискоинтензивен товар.
        if focus in {"Z3", "Z4", "Z5", "STR"}:
            support_z1_q = (float(method.get("warmup_min", 0)) + float(method.get("cooldown_min", 0))) * 1.06
            session_q["Z1"] += support_z1_q
            remaining["Z1"] = max(0.0, float(remaining["Z1"]) - support_z1_q)
            if focus in {"Z3", "Z4", "Z5"}:
                support_z2_q = 8.0 * 1.12
                session_q["Z2"] += support_z2_q
                remaining["Z2"] = max(0.0, float(remaining["Z2"]) - support_z2_q)

        # Оставащият нискоинтензивен обем се разпределя по останалите сесии.
        slots_left = max(1, training_slots - processed_training_slots + 1)
        for low_component in ["Z1", "Z2"]:
            if remaining[low_component] <= 0:
                continue
            share = float(remaining[low_component]) / slots_left
            if focus == low_component:
                role_factor = 1.20
            elif str(slot["slot_type"]) == "long":
                role_factor = 1.65
            elif focus in {"Z3", "Z4", "Z5"}:
                role_factor = 0.30
            elif focus == "STR":
                role_factor = 0.45
            else:
                role_factor = 0.80
            addition = min(float(remaining[low_component]), share * role_factor)
            session_q[low_component] += addition
            remaining[low_component] = max(0.0, float(remaining[low_component]) - addition)

        if unavailable:
            max_q = 45.0 * 1.06
            if session_q.sum() > max_q:
                session_q *= max_q / max(session_q.sum(), EPS)

        if is_double_threshold and focus not in {"Z3", "Z4"}:
            is_double_threshold = False

        readiness_before_focus = readiness_map[focus]
        key_q_threshold = float(parameters["key_stimulus_fraction"]) * float(tref[focus])
        if (
            session_q[focus] >= key_q_threshold
            and readiness_before_focus < key_readiness_threshold
        ):
            reduced_q = max(0.0, key_q_threshold * (1.0 - 1e-3))
            returned_q = max(0.0, float(session_q[focus]) - reduced_q)
            session_q[focus] = reduced_q
            remaining[focus] = float(remaining[focus]) + returned_q

        key_stimulus = bool(
            session_q[focus] >= key_q_threshold
            and readiness_before_focus >= key_readiness_threshold
            and not hard_flag
        )
        key_limit_applied = False
        if (
            key_stimulus
            and generated_key_count >= int(preferences["max_key_sessions_per_week"])
        ):
            reduced_q = max(0.0, key_q_threshold * (1.0 - 1e-3))
            returned_q = max(0.0, float(session_q[focus]) - reduced_q)
            session_q[focus] = reduced_q
            remaining[focus] = float(remaining[focus]) + returned_q
            key_stimulus = False
            key_limit_applied = True
            key_limit_reductions.append(
                f"{current_date.date().isoformat()} · сесия {session_no} · {focus}"
            )
        elif key_stimulus:
            generated_key_count += 1

        effective = effective_from_direct_vector(session_q.values, tref.values, parameters)
        fatigue = apply_training_impulse(fatigue, effective, tref, parameters)
        readiness_after_focus = float(np.clip(100.0 - fatigue[focus], 0.0, 100.0))

        real_by_component: dict[str, float] = {}
        for component in COMPONENTS:
            k = float(method["expected_k"]) if component == focus else _default_k(component)
            real_by_component[component] = float(session_q[component] / max(k, EPS))
        total_real = sum(real_by_component.values())
        main_real = real_by_component[focus]
        description = _method_description(method, focus, main_real, readiness_before_focus)
        if is_double_threshold:
            description = f"Двойна прагова сесия · {slot['time_of_day']}. " + description
        if race_event is not None and session_no == 1:
            race_name = str(race_event["name"])
            description = f"{race_name}. Загрявката, стартовата работа и разпускането се отчитат в компонентния товар. " + description

        planned_q_total += session_q
        planned_e_total += pd.Series(effective, index=COMPONENTS)
        target_index = float(first_week.loc[focus, "target_index"])
        explanation_parts = [
            f"{focus} целеви 7/40 = {target_index:.2f}",
            f"интегрирана готовност = {float(integrated.loc[focus, 'integrated_readiness']):.0f}/100",
            f"адаптивен множител = {float(integrated.loc[focus, 'adaptive_multiplier']):.2f}",
            f"седмична структура = {preferences['sessions_per_week']} сесии",
            (
                f"мезоцикъл = {mesocycle_type}, седмица "
                f"{mesocycle_week}/{mesocycle_length}"
            ),
        ]
        if accent_summary:
            explanation_parts.append(f"мезоциклични акценти = {accent_summary}")
        if mesocycle_reason:
            explanation_parts.append(f"мезоциклична причина = {mesocycle_reason}")
        if is_double_threshold:
            explanation_parts.append("слотът е част от разрешена двойна прагова тренировка")
        if focus != planned_focus:
            explanation_parts.append(f"първоначалният фокус {planned_focus} е заменен с {focus}")
        if hard_flag:
            explanation_parts.append("има твърд флаг за първоначално планирания компонент")
        if key_limit_applied:
            explanation_parts.append(
                "дозата е ограничена от максималния брой ключови сесии"
            )
        if unavailable:
            explanation_parts.append("дозата е ограничена от календарна недостъпност")
        if race_event is not None:
            explanation_parts.append(f"състезателно събитие: {race_event['name']}")
        if not day_events.empty:
            explanation_parts.append("календар: " + ", ".join(day_events["name"].astype(str).tolist()))

        strength_type = ""
        strength_label = ""
        strength_coefficient = 0.0
        strength_real_min = 0.0
        strength_equivalent_min = 0.0
        if focus == "STR":
            strength_type = str(method.get("strength_type", "STR_END"))
            strength_label = STRENGTH_LABELS.get(strength_type, str(method.get("title", "Силова работа")))
            strength_coefficient = float(method.get("expected_k", STRENGTH_COEFFICIENTS.get(strength_type, 1.0)))
            strength_real_min = float(real_by_component["STR"])
            strength_equivalent_min = float(session_q["STR"])
            explanation_parts.append(
                f"силов вид = {strength_label}; {strength_real_min:.1f} реални мин × "
                f"{strength_coefficient:.1f} = {strength_equivalent_min:.1f} екв. мин"
            )

        row: dict[str, Any] = {
            "session_id": f"{current_date.date().isoformat()}-{session_no}",
            "date": current_date,
            "day": WEEKDAY_LABELS[current_date.weekday()],
            "session_no": session_no,
            "time_of_day": str(slot["time_of_day"]),
            "slot_type": str(slot["slot_type"]),
            "planned_focus": planned_focus,
            "focus": focus,
            "focus_label": COMPONENT_LABELS[focus],
            "method_code": str(method["method_code"]),
            "method": str(race_event["name"]) if race_event is not None and session_no == 1 else str(method["title"]),
            "strength_type": strength_type,
            "strength_label": strength_label,
            "strength_coefficient": round(strength_coefficient, 2),
            "strength_real_min": round(strength_real_min, 1),
            "strength_equivalent_min": round(strength_equivalent_min, 1),
            "description": description,
            "total_real_min": round(total_real, 1),
            "readiness_before": round(readiness_before_focus, 1),
            "readiness_after": round(readiness_after_focus, 1),
            "key_stimulus": key_stimulus,
            "double_threshold": is_double_threshold,
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
        for strength_component in STRENGTH_TYPES:
            row[f"real_{strength_component}"] = (
                round(strength_real_min, 1) if strength_component == strength_type else 0.0
            )
            row[f"q_{strength_component}"] = (
                round(strength_equivalent_min, 2) if strength_component == strength_type else 0.0
            )
        rows.append(row)

    plan = pd.DataFrame(rows).sort_values(["date", "session_no"]).reset_index(drop=True)
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
        "mesocycle_type": mesocycle_type,
        "mesocycle_week": mesocycle_week,
        "mesocycle_length_weeks": mesocycle_length,
        "accent_components": accent_summary,
        "mesocycle_reason": mesocycle_reason,
        "target_indices": first_week["target_index"].to_dict(),
        "target_effective": target_e.to_dict(),
        "target_direct_q": target_q.to_dict(),
        "inversion_error": inversion_error,
        "unallocated_direct_q": remaining.to_dict(),
        "sessions_requested": int(preferences["sessions_per_week"]),
        "sessions_generated": int((plan["focus"] != "REST").sum()),
        "key_sessions_generated": int(plan["key_stimulus"].sum()),
        "max_key_sessions_per_week": int(preferences["max_key_sessions_per_week"]),
        "rest_days": [WEEKDAY_LABELS[day] for day in preferences["rest_days"]],
        "double_threshold_requested": bool(preferences["double_threshold_enabled"]),
        "double_threshold_active": double_threshold_active,
        "double_threshold_day": WEEKDAY_LABELS[preferences["double_threshold_day"]],
        "strength_model": {
            strength_type: {
                "label": STRENGTH_LABELS[strength_type],
                "coefficient": STRENGTH_COEFFICIENTS[strength_type],
            }
            for strength_type in STRENGTH_TYPES
        },
        "warnings": [
            f"Остатък {component}: {value:.1f} екв. мин"
            for component, value in remaining.items()
            if value > max(3.0, 0.05 * float(target_q[component]))
        ],
    }
    if preferences["double_threshold_enabled"] and not double_threshold_active:
        snapshot["warnings"].append(
            "Двойната прагова тренировка е заявена, но не е активирана поради фаза, readiness, твърд флаг или лимит за ключови сесии."
        )
    if key_limit_reductions:
        snapshot["warnings"].append(
            "Лимитът за ключови сесии намали дозата на: "
            + "; ".join(key_limit_reductions)
            + "."
        )
    return plan, comparison, snapshot


def apply_plan_overrides(plan: pd.DataFrame, overrides: dict[str, Any], athlete_id: str) -> pd.DataFrame:
    """Прилага треньорски промени; ключът включва номер на сесията."""

    if not overrides or plan.empty:
        return plan
    result = plan.copy()
    for idx, row in result.iterrows():
        session_no = int(row.get("session_no", 1))
        date_key = pd.Timestamp(row["date"]).date().isoformat()
        key = f"{athlete_id}|{date_key}|{session_no}"
        # Поддържа overrides от версия 0.2, където ключът беше само по дата.
        override = overrides.get(key) or overrides.get(f"{athlete_id}|{date_key}")
        if not override:
            continue
        for field in ["status", "locked", "coach_note", "method", "description", "total_real_min"]:
            if field in override:
                result.at[idx, field] = override[field]
    return result
