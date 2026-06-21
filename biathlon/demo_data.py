"""Детерминирани синтетични данни за демонстрационното приложение."""

from __future__ import annotations

from copy import deepcopy
from datetime import date, timedelta
from typing import Any

import numpy as np
import pandas as pd

from .constants import (
    AEROBIC_COMPONENTS,
    COMPONENTS,
    DEFAULT_ZONE_PROFILE,
    STRENGTH_COEFFICIENTS,
    STRENGTH_TYPES,
    fresh_parameters,
)
from .preferences import default_planning_preferences

DEMO_SEED = 20260620


def _zone_profile_for_athlete(athlete_id: str) -> pd.DataFrame:
    profile = pd.DataFrame(deepcopy(DEFAULT_ZONE_PROFILE))
    shifts = {"A": 0, "B": -3, "C": 2}
    shift = shifts.get(athlete_id, 0)
    profile["hr_low"] = profile["hr_low"] + shift
    profile["hr_high"] = profile["hr_high"] + shift
    profile["athlete_id"] = athlete_id
    profile["valid_from"] = pd.Timestamp(date.today() - timedelta(days=365))
    profile["method"] = "Демо индивидуален профил"
    profile["version"] = 1
    return profile


def _base_day_load(weekday: int, phase: float, rng: np.random.Generator) -> dict[str, float]:
    """Връща реални минути по компоненти за типичен тренировъчен ден."""

    load = {component: 0.0 for component in COMPONENTS}
    volume_wave = 0.92 + 0.18 * np.sin(np.pi * np.clip(phase, 0, 1))

    if weekday == 0:  # понеделник
        if rng.random() < 0.55:
            load["Z1"] = rng.normal(42, 7) * volume_wave
    elif weekday == 1:  # вторник — ключова работа
        load["Z1"] = rng.normal(30, 5)
        if phase < 0.45:
            load["Z3"] = rng.normal(28, 5)
        elif phase < 0.78:
            load["Z3"] = rng.normal(35, 6)
            load["Z4"] = max(0.0, rng.normal(8, 3))
        else:
            load["Z4"] = rng.normal(22, 4)
            load["Z5"] = max(0.0, rng.normal(6, 2))
    elif weekday == 2:  # сряда — обем + сила
        load["Z1"] = rng.normal(45, 8) * volume_wave
        load["Z2"] = rng.normal(38, 7) * volume_wave
        load["STR"] = max(0.0, rng.normal(28, 5))
    elif weekday == 3:  # четвъртък — праг/специфичност
        load["Z1"] = rng.normal(28, 5)
        if phase < 0.35:
            load["Z2"] = rng.normal(45, 7)
            load["Z3"] = rng.normal(15, 4)
        elif phase < 0.75:
            load["Z3"] = rng.normal(32, 6)
            load["Z4"] = max(0.0, rng.normal(10, 3))
        else:
            load["Z4"] = rng.normal(20, 4)
            load["Z5"] = max(0.0, rng.normal(8, 2))
    elif weekday == 4:  # петък — възстановяване
        if rng.random() < 0.8:
            load["Z1"] = rng.normal(48, 8)
    elif weekday == 5:  # събота — дълга/моделна
        load["Z1"] = rng.normal(65, 10) * volume_wave
        load["Z2"] = rng.normal(55, 9) * volume_wave
        if phase > 0.35:
            load["Z3"] = max(0.0, rng.normal(18, 5))
        if phase > 0.72:
            load["Z4"] = max(0.0, rng.normal(7, 2))
    else:  # неделя — умерена или почивка
        if rng.random() < 0.75:
            load["Z1"] = rng.normal(48, 9) * volume_wave
            load["Z2"] = rng.normal(30, 7) * volume_wave

    for key, value in load.items():
        load[key] = float(max(0.0, round(value, 1)))
    return load


def _strength_type_distribution(
    total_minutes: float,
    phase: float,
    rng: np.random.Generator,
) -> dict[str, float]:
    """Разпределя синтетичния силов обем по четирите вида."""

    total = max(0.0, float(total_minutes))
    values = {strength_type: 0.0 for strength_type in STRENGTH_TYPES}
    if total <= 0:
        return values

    if phase < 0.22:
        mix = {"STR_STAB": 0.35, "STR_END": 0.65, "STR_MAX": 0.0, "STR_PLY": 0.0}
    elif phase < 0.55:
        mix = {"STR_STAB": 0.15, "STR_END": 0.65, "STR_MAX": 0.20, "STR_PLY": 0.0}
    elif phase < 0.80:
        mix = {"STR_STAB": 0.10, "STR_END": 0.45, "STR_MAX": 0.35, "STR_PLY": 0.10}
    else:
        mix = {"STR_STAB": 0.15, "STR_END": 0.20, "STR_MAX": 0.30, "STR_PLY": 0.35}

    # Малка детерминирана вариация, без да се променя общото реално време.
    raw = {
        strength_type: max(0.0, weight * float(np.clip(rng.normal(1.0, 0.06), 0.82, 1.18)))
        for strength_type, weight in mix.items()
    }
    denominator = sum(raw.values()) or 1.0
    remaining = total
    for strength_type in STRENGTH_TYPES[:-1]:
        value = round(total * raw[strength_type] / denominator, 1)
        values[strength_type] = max(0.0, value)
        remaining -= values[strength_type]
    values[STRENGTH_TYPES[-1]] = max(0.0, round(remaining, 1))
    return values


def _activity_positions(load: dict[str, float], phase: float, rng: np.random.Generator) -> dict[str, float]:
    positions: dict[str, float] = {}
    means = {"Z1": 0.28, "Z2": 0.42, "Z3": 0.60, "Z4": 0.66, "Z5": 0.72}
    for component in AEROBIC_COMPONENTS:
        if load[component] <= 0:
            positions[component] = 0.0
            continue
        shift = 0.06 * max(0.0, phase - 0.55) if component in {"Z3", "Z4", "Z5"} else 0.0
        positions[component] = float(np.clip(rng.normal(means[component] + shift, 0.09), 0.05, 0.95))
    return positions


def _sport_for_day(weekday: int, phase: float) -> str:
    if weekday == 2:
        return "Ролкови ски + сила"
    if phase < 0.25:
        return "Бягане / колело"
    if phase < 0.8:
        return "Ролкови ски"
    return "Ролкови ски · специфична"


def _generate_activities(
    athletes: pd.DataFrame,
    start_date: date,
    end_date: date,
    seed: int,
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    dates = pd.date_range(start_date, end_date, freq="D")
    n_days = max(1, len(dates) - 1)

    for athlete_index, athlete in athletes.iterrows():
        athlete_id = str(athlete["athlete_id"])
        rng = np.random.default_rng(seed + 1000 * (athlete_index + 1))
        for day_index, timestamp in enumerate(dates):
            phase = day_index / n_days
            load = _base_day_load(timestamp.weekday(), phase, rng)

            # Минимални индивидуални вариации на изпълненото външно натоварване.
            scale = {"A": 1.02, "B": 0.98, "C": 1.00}[athlete_id]
            for component in COMPONENTS:
                load[component] = round(load[component] * scale, 1)

            if sum(load.values()) < 12:
                continue

            positions = _activity_positions(load, phase, rng)
            strength_real = _strength_type_distribution(load["STR"], phase, rng)
            strength_q = sum(
                strength_real[strength_type] * STRENGTH_COEFFICIENTS[strength_type]
                for strength_type in STRENGTH_TYPES
            )
            strength_k = strength_q / load["STR"] if load["STR"] > 0 else 0.0
            moving = sum(load[c] for c in AEROBIC_COMPONENTS) + load["STR"]
            elapsed = moving + (5 if moving > 45 else 2)
            intensity = (
                load["Z1"]
                + 1.3 * load["Z2"]
                + 2.0 * load["Z3"]
                + 3.0 * load["Z4"]
                + 4.2 * load["Z5"]
                + 2.2 * strength_q
            ) / max(moving, 1)
            rpe = float(np.clip(1.5 + 1.25 * intensity + rng.normal(0, 0.6), 1, 10))

            row: dict[str, Any] = {
                "activity_id": f"{athlete_id}-{timestamp.strftime('%Y%m%d')}",
                "athlete_id": athlete_id,
                "date": timestamp.normalize(),
                "sport": _sport_for_day(timestamp.weekday(), phase),
                "source": "demo_generator",
                "status": "Изпълнена",
                "moving_min": round(moving, 1),
                "elapsed_min": round(elapsed, 1),
                "rpe": round(rpe, 1),
                "strength_k": round(strength_k, 4),
                "quality_score": round(float(np.clip(rng.normal(0.95, 0.035), 0.78, 1.0)), 2),
                "notes": "Синтетична активност с едносекунден профил при отваряне.",
            }
            for component in COMPONENTS:
                row[f"real_{component}"] = load[component]
            for strength_type in STRENGTH_TYPES:
                row[f"real_{strength_type}"] = strength_real[strength_type]
            for component in AEROBIC_COMPONENTS:
                row[f"pos_{component}"] = round(positions[component], 3)
            rows.append(row)

    return pd.DataFrame(rows).sort_values(["athlete_id", "date"]).reset_index(drop=True)


def _daily_load_lookup(activities: pd.DataFrame, athlete_id: str) -> pd.DataFrame:
    columns = [f"real_{component}" for component in COMPONENTS]
    athlete = activities.loc[activities["athlete_id"] == athlete_id, ["date", *columns]].copy()
    if athlete.empty:
        return pd.DataFrame(columns=columns)
    return athlete.groupby("date", as_index=True)[columns].sum()


def _generate_wellness(
    athletes: pd.DataFrame,
    activities: pd.DataFrame,
    start_date: date,
    end_date: date,
    seed: int,
) -> pd.DataFrame:
    dates = pd.date_range(start_date, end_date + timedelta(days=1), freq="D")
    rows: list[dict[str, Any]] = []

    for athlete_index, athlete in athletes.iterrows():
        athlete_id = str(athlete["athlete_id"])
        profile = str(athlete["profile_code"])
        rng = np.random.default_rng(seed + 5000 + athlete_index * 1000)
        loads = _daily_load_lookup(activities, athlete_id).reindex(dates, fill_value=0.0)

        fatigue_memory = 0.0
        upper_memory = 0.0
        leg_memory = 0.0
        base_hrv = {"A": 74.0, "B": 65.0, "C": 70.0}[profile]
        base_rhr = {"A": 48.0, "B": 52.0, "C": 50.0}[profile]
        base_weight = float(athlete["weight_kg"])

        for idx, timestamp in enumerate(dates):
            prev = loads.iloc[max(0, idx - 1)] if len(loads) else pd.Series(dtype=float)
            z3 = float(prev.get("real_Z3", 0.0))
            z4 = float(prev.get("real_Z4", 0.0))
            z5 = float(prev.get("real_Z5", 0.0))
            strength = float(prev.get("real_STR", 0.0))
            z2 = float(prev.get("real_Z2", 0.0))

            mid_stress = (z3 + 1.35 * z4) / 45.0
            high_stress = (1.5 * z5 + 0.9 * strength) / 35.0
            volume_stress = z2 / 100.0
            if profile == "A":
                impulse = 0.60 * mid_stress + 0.65 * high_stress + 0.45 * volume_stress
            elif profile == "B":
                impulse = 1.25 * mid_stress + 0.70 * high_stress + 0.45 * volume_stress
            else:
                impulse = 0.65 * mid_stress + 1.35 * high_stress + 0.40 * volume_stress

            fatigue_memory = fatigue_memory * 0.62 + impulse
            leg_memory = leg_memory * 0.55 + 0.70 * mid_stress + 0.85 * high_stress
            upper_memory = upper_memory * 0.58 + 0.95 * high_stress + strength / 50.0

            # Съзнателно неблагоприятни последни дни за профили B/C.
            days_from_end = (pd.Timestamp(end_date + timedelta(days=1)) - timestamp).days
            recent_penalty = 0.0
            if profile == "B" and days_from_end <= 3:
                recent_penalty = 1.8
            if profile == "C" and days_from_end <= 2:
                recent_penalty = 1.35

            fatigue = np.clip(2.1 + 1.25 * fatigue_memory + recent_penalty + rng.normal(0, 0.65), 0, 10)
            soreness_legs = np.clip(1.4 + 1.15 * leg_memory + 0.55 * recent_penalty + rng.normal(0, 0.55), 0, 10)
            soreness_upper = np.clip(1.2 + 1.20 * upper_memory + 0.45 * recent_penalty + rng.normal(0, 0.55), 0, 10)
            stress = np.clip(2.6 + 0.25 * fatigue_memory + 0.35 * recent_penalty + rng.normal(0, 0.9), 0, 10)
            sleep_quality = np.clip(8.1 - 0.55 * fatigue_memory - 0.55 * recent_penalty + rng.normal(0, 0.65), 1, 10)
            sleep_hours = np.clip(8.0 - 0.22 * fatigue_memory - 0.30 * recent_penalty + rng.normal(0, 0.45), 4.0, 9.5)
            motivation = np.clip(8.2 - 0.50 * fatigue_memory - 0.45 * recent_penalty + rng.normal(0, 0.75), 1, 10)
            pain = np.clip(rng.normal(0.7 + 0.20 * leg_memory + 0.18 * upper_memory, 0.45), 0, 6.5)
            morning_hr = base_rhr + 1.45 * fatigue_memory + 1.35 * recent_penalty + rng.normal(0, 1.4)
            hrv = base_hrv - 4.2 * fatigue_memory - 3.4 * recent_penalty + rng.normal(0, 3.4)
            weight = base_weight + rng.normal(0, 0.35)

            rows.append(
                {
                    "athlete_id": athlete_id,
                    "date": timestamp.normalize(),
                    "sleep_quality": round(float(sleep_quality), 1),
                    "fatigue": round(float(fatigue), 1),
                    "soreness_legs": round(float(soreness_legs), 1),
                    "soreness_upper": round(float(soreness_upper), 1),
                    "stress": round(float(stress), 1),
                    "motivation": round(float(motivation), 1),
                    "pain": round(float(pain), 1),
                    "illness": False,
                    "morning_hr": round(float(morning_hr), 0),
                    "hrv": round(float(max(15.0, hrv)), 1),
                    "sleep_hours": round(float(sleep_hours), 1),
                    "weight_kg": round(float(weight), 1),
                    "session_rpe": round(float(np.clip(fatigue + rng.normal(0, 1.0), 0, 10)), 1),
                    "execution_quality": int(np.clip(round(5.0 - 0.25 * fatigue + rng.normal(0, 0.4)), 1, 5)),
                    "source": "demo_manual",
                    "reliability": 1.0,
                    "note": "Синтетичен сутрешен запис.",
                }
            )

    return pd.DataFrame(rows).sort_values(["athlete_id", "date"]).reset_index(drop=True)


def _test_trend(profile: str, test_code: str, index: int) -> tuple[float, float]:
    if test_code == "SKIERG_3MIN":
        base_primary, base_secondary = 285.0, 10.5
        trend = {"A": 2.2, "B": 0.7, "C": -1.7}[profile]
        secondary_trend = {"A": -0.20, "B": -0.05, "C": 0.30}[profile]
    elif test_code == "Z3_20MIN":
        base_primary, base_secondary = 18.0, 4.8
        trend = {"A": 0.13, "B": -0.03, "C": 0.06}[profile]
        secondary_trend = {"A": -0.12, "B": 0.18, "C": -0.03}[profile]
    else:
        base_primary, base_secondary = 30.5, 2.7
        trend = {"A": -0.12, "B": -0.04, "C": 0.18}[profile]
        secondary_trend = {"A": -0.05, "B": 0.00, "C": 0.12}[profile]
    return base_primary + trend * index, max(0.1, base_secondary + secondary_trend * index)


def _generate_tests(athletes: pd.DataFrame, end_date: date, seed: int) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    rng = np.random.default_rng(seed + 9000)
    test_codes = ["SKIERG_3MIN", "Z3_20MIN", "SPRINT_200M"]
    for _, athlete in athletes.iterrows():
        athlete_id = str(athlete["athlete_id"])
        profile = str(athlete["profile_code"])
        for test_offset, test_code in enumerate(test_codes):
            for index in range(5):
                test_date = end_date - timedelta(days=118 - index * 27 - test_offset * 3)
                primary, secondary = _test_trend(profile, test_code, index)
                if test_code == "SKIERG_3MIN":
                    primary += rng.normal(0, 2.3)
                    secondary += rng.normal(0, 0.35)
                elif test_code == "Z3_20MIN":
                    primary += rng.normal(0, 0.08)
                    secondary += rng.normal(0, 0.20)
                else:
                    primary += rng.normal(0, 0.10)
                    secondary += rng.normal(0, 0.12)

                rows.append(
                    {
                        "test_id": f"{athlete_id}-{test_code}-{index+1}",
                        "athlete_id": athlete_id,
                        "date": pd.Timestamp(test_date),
                        "test_code": test_code,
                        "protocol_version": "1.0",
                        "primary_value": round(float(primary), 2),
                        "secondary_value": round(float(max(0.0, secondary)), 2),
                        "valid": True,
                        "comparability": round(float(0.78 if index == 1 else np.clip(rng.normal(0.94, 0.025), 0.82, 1.0)), 2),
                        "conditions": "Стандартизиран демо протокол",
                        "note": "Синтетичен контролен тест.",
                    }
                )
    return pd.DataFrame(rows).sort_values(["athlete_id", "test_code", "date"]).reset_index(drop=True)


def _generate_calendar(athletes: pd.DataFrame, today: date) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for _, athlete in athletes.iterrows():
        athlete_id = str(athlete["athlete_id"])
        events = [
            ("TEST", "Тестов ден", today + timedelta(days=21), today + timedelta(days=21), "B", "Контролни тестове"),
            ("CAMP", "Подготвителен лагер", today + timedelta(days=28), today + timedelta(days=38), "B", "Обем и специфична работа"),
            ("UNAVAILABLE", "Пътуване / ограничена наличност", today + timedelta(days=51), today + timedelta(days=51), "C", "Максимум 45 мин"),
            ("CONTROL_RACE", "Контролен старт", today + timedelta(days=70), today + timedelta(days=70), "B", "Проверка на специфична готовност"),
            ("MAIN_RACE", "Основен старт · демо", today + timedelta(days=112), today + timedelta(days=112), "A", "Пикова готовност"),
        ]
        for event_index, (event_type, name, start, end, priority, goal) in enumerate(events):
            rows.append(
                {
                    "event_id": f"{athlete_id}-EV-{event_index+1}",
                    "athlete_id": athlete_id,
                    "type": event_type,
                    "name": name,
                    "start_date": pd.Timestamp(start),
                    "end_date": pd.Timestamp(end),
                    "priority": priority,
                    "goal": goal,
                    "locked": event_type == "MAIN_RACE",
                    "note": "Синтетично календарно събитие.",
                }
            )
    return pd.DataFrame(rows).sort_values(["athlete_id", "start_date"]).reset_index(drop=True)


def _training_methods() -> pd.DataFrame:
    rows = [
        {
            "method_code": "Z1_REC",
            "title": "Възстановителна аеробна сесия",
            "component": "Z1",
            "phase_min": 0.0,
            "phase_max": 1.0,
            "min_readiness": 55,
            "expected_k": 1.06,
            "warmup_min": 0,
            "cooldown_min": 5,
            "description": "Равномерна работа в долната и средната част на Z1.",
        },
        {
            "method_code": "Z1_LONG",
            "title": "Продължителна нискоинтензивна работа",
            "component": "Z1",
            "phase_min": 0.0,
            "phase_max": 0.75,
            "min_readiness": 65,
            "expected_k": 1.08,
            "warmup_min": 0,
            "cooldown_min": 5,
            "description": "Продължителна равномерна сесия с технически акценти.",
        },
        {
            "method_code": "Z2_BASE",
            "title": "Основна аеробна издръжливост",
            "component": "Z2",
            "phase_min": 0.0,
            "phase_max": 0.85,
            "min_readiness": 70,
            "expected_k": 1.12,
            "warmup_min": 15,
            "cooldown_min": 10,
            "description": "Равномерна работа в Z2 с контрол на техниката и пулса.",
        },
        {
            "method_code": "Z2_TEMPO_CHANGES",
            "title": "Аеробна работа с промени на терена",
            "component": "Z2",
            "phase_min": 0.25,
            "phase_max": 1.0,
            "min_readiness": 72,
            "expected_k": 1.15,
            "warmup_min": 15,
            "cooldown_min": 10,
            "description": "Z2 върху променлив терен с кратки технически ускорения.",
        },
        {
            "method_code": "Z3_5X",
            "title": "Интервали в Z3",
            "component": "Z3",
            "phase_min": 0.15,
            "phase_max": 0.90,
            "min_readiness": 82,
            "expected_k": 1.28,
            "warmup_min": 20,
            "cooldown_min": 12,
            "description": "Пет повторения в Z3 с кратко активно възстановяване.",
        },
        {
            "method_code": "Z3_CONT",
            "title": "Продължителна субмаксимална работа",
            "component": "Z3",
            "phase_min": 0.35,
            "phase_max": 0.88,
            "min_readiness": 86,
            "expected_k": 1.31,
            "warmup_min": 20,
            "cooldown_min": 12,
            "description": "Един или два продължителни блока около горната граница на Z3.",
        },
        {
            "method_code": "Z4_4X",
            "title": "Прагова интервална работа",
            "component": "Z4",
            "phase_min": 0.42,
            "phase_max": 1.0,
            "min_readiness": 88,
            "expected_k": 1.24,
            "warmup_min": 22,
            "cooldown_min": 12,
            "description": "Четири до пет прагови повторения с пълно методическо дозиране.",
        },
        {
            "method_code": "Z4_UPHILL",
            "title": "Прагова работа срещу наклон",
            "component": "Z4",
            "phase_min": 0.55,
            "phase_max": 0.95,
            "min_readiness": 90,
            "expected_k": 1.27,
            "warmup_min": 22,
            "cooldown_min": 12,
            "description": "Интервали срещу наклон с контрол на техниката и скоростта.",
        },
        {
            "method_code": "Z5_6X3",
            "title": "VO₂max · 6 × 3 мин",
            "component": "Z5",
            "phase_min": 0.55,
            "phase_max": 1.0,
            "min_readiness": 90,
            "expected_k": 1.23,
            "warmup_min": 25,
            "cooldown_min": 15,
            "description": "Кратки високоинтензивни интервали с контролирано възстановяване.",
        },
        {
            "method_code": "Z5_SHORT",
            "title": "Кратки специфични ускорения",
            "component": "Z5",
            "phase_min": 0.70,
            "phase_max": 1.0,
            "min_readiness": 86,
            "expected_k": 1.18,
            "warmup_min": 22,
            "cooldown_min": 12,
            "description": "Кратки повторения за скорост и специфична мощност без голям обем.",
        },
        {
            "method_code": "STR_STAB",
            "title": "Стабилизация / кор",
            "component": "STR",
            "strength_type": "STR_STAB",
            "phase_min": 0.0,
            "phase_max": 1.0,
            "min_readiness": 60,
            "expected_k": 0.80,
            "warmup_min": 8,
            "cooldown_min": 5,
            "description": "Стабилизация, кор, профилактика и контролираща работа.",
        },
        {
            "method_code": "STR_END",
            "title": "Обща силова издръжливост",
            "component": "STR",
            "strength_type": "STR_END",
            "phase_min": 0.0,
            "phase_max": 0.78,
            "min_readiness": 72,
            "expected_k": 1.00,
            "warmup_min": 12,
            "cooldown_min": 8,
            "description": "Кръгова общоразвиваща силова работа с контролирано темпо.",
        },
        {
            "method_code": "STR_MAX",
            "title": "Максимална сила",
            "component": "STR",
            "strength_type": "STR_MAX",
            "phase_min": 0.22,
            "phase_max": 0.94,
            "min_readiness": 82,
            "expected_k": 1.20,
            "warmup_min": 15,
            "cooldown_min": 10,
            "description": "Висока относителна тежест, малък брой повторения и пълно възстановяване.",
        },
        {
            "method_code": "STR_PLY",
            "title": "Плиометрия",
            "component": "STR",
            "strength_type": "STR_PLY",
            "phase_min": 0.58,
            "phase_max": 1.0,
            "min_readiness": 88,
            "expected_k": 1.40,
            "warmup_min": 18,
            "cooldown_min": 10,
            "description": "Кратка скокова и експлозивна работа с пълен контрол на качеството.",
        },
    ]
    return pd.DataFrame(rows)


def generate_demo_bundle(seed: int = DEMO_SEED, history_days: int = 150) -> dict[str, Any]:
    today = date.today()
    end_date = today - timedelta(days=1)
    start_date = end_date - timedelta(days=history_days - 1)

    athletes = pd.DataFrame(
        [
            {
                "athlete_id": "A",
                "name": "Демо спортист A",
                "profile_code": "A",
                "profile_name": "Висока аеробна поносимост",
                "category": "Жени / демонстрационен профил",
                "age": 24,
                "height_cm": 171,
                "weight_kg": 61.5,
                "experience_years": 10,
                "availability": "6–7 дни седмично",
                "status": "Активен",
            },
            {
                "athlete_id": "B",
                "name": "Демо спортист B",
                "profile_code": "B",
                "profile_name": "Чувствителност към Z3–Z4",
                "category": "Мъже / демонстрационен профил",
                "age": 22,
                "height_cm": 181,
                "weight_kg": 72.0,
                "experience_years": 8,
                "availability": "6 дни седмично",
                "status": "Активен",
            },
            {
                "athlete_id": "C",
                "name": "Демо спортист C",
                "profile_code": "C",
                "profile_name": "Чувствителност към Z5/сила",
                "category": "Младежи / демонстрационен профил",
                "age": 19,
                "height_cm": 178,
                "weight_kg": 68.0,
                "experience_years": 6,
                "availability": "5–6 дни седмично",
                "status": "Активен",
            },
        ]
    )

    zone_profiles = {athlete_id: _zone_profile_for_athlete(athlete_id) for athlete_id in athletes["athlete_id"]}
    activities = _generate_activities(athletes, start_date, end_date, seed)
    wellness = _generate_wellness(athletes, activities, start_date, end_date, seed)
    tests = _generate_tests(athletes, end_date, seed)
    calendar = _generate_calendar(athletes, today)
    planning_preferences = {
        str(row["athlete_id"]): default_planning_preferences(str(row["profile_code"]), today)
        for _, row in athletes.iterrows()
    }

    return {
        "seed": seed,
        "generated_at": pd.Timestamp.now(),
        "version": 1,
        "athletes": athletes,
        "zone_profiles": zone_profiles,
        "activities": activities,
        "wellness": wellness,
        "tests": tests,
        "calendar": calendar,
        "methods": _training_methods(),
        "parameters": fresh_parameters(),
        "planning_preferences": planning_preferences,
        "audit_log": [],
        "plan_overrides": {},
    }


def generate_activity_stream(
    activity: pd.Series | dict[str, Any],
    zone_profile: pd.DataFrame,
    seed: int = DEMO_SEED,
) -> pd.DataFrame:
    """Генерира детерминиран 1-секунден пулсов поток от обобщена активност."""

    row = dict(activity)
    rng = np.random.default_rng(seed + abs(hash(str(row.get("activity_id", "activity")))) % 100_000)
    profile = zone_profile.set_index("component")

    segments: list[tuple[str, int]] = []
    z1_seconds = int(round(float(row.get("real_Z1", 0.0)) * 60))
    if z1_seconds:
        segments.append(("Z1", z1_seconds // 2))
    for component in ["Z2", "Z3", "Z4", "Z5"]:
        seconds = int(round(float(row.get(f"real_{component}", 0.0)) * 60))
        if seconds > 0:
            segments.append((component, seconds))
    if z1_seconds:
        segments.append(("Z1", z1_seconds - z1_seconds // 2))

    records: list[pd.DataFrame] = []
    offset = 0
    for component, seconds in segments:
        if seconds <= 0:
            continue
        zone = profile.loc[component]
        position = float(np.clip(row.get(f"pos_{component}", 0.5), 0.0, 1.0))
        base_hr = float(zone["hr_low"] + position * (zone["hr_high"] - zone["hr_low"]))
        local_t = np.arange(seconds)
        wave = 1.4 * np.sin(2 * np.pi * local_t / max(45, seconds / 3))
        drift = np.linspace(-0.6, 0.9, seconds)
        noise = rng.normal(0, 0.9, size=seconds)
        hr = np.clip(base_hr + wave + drift + noise, zone["hr_low"], zone["hr_high"])
        records.append(
            pd.DataFrame(
                {
                    "offset_sec": np.arange(offset, offset + seconds),
                    "hr": np.round(hr, 1),
                    "moving": True,
                    "expected_component": component,
                    "quality": "original_demo_1s",
                }
            )
        )
        offset += seconds

    if not records:
        return pd.DataFrame(columns=["offset_sec", "hr", "moving", "expected_component", "quality"])
    return pd.concat(records, ignore_index=True)
