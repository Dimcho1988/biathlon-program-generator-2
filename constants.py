"""Константи и начални експертни параметри за демонстрационния MVP."""

from __future__ import annotations

from copy import deepcopy

COMPONENTS = ["Z1", "Z2", "Z3", "Z4", "Z5", "STR"]
AEROBIC_COMPONENTS = COMPONENTS[:5]

COMPONENT_LABELS = {
    "Z1": "Z1 · възстановителна / ниска интензивност",
    "Z2": "Z2 · основна аеробна издръжливост",
    "Z3": "Z3 · темпова / смесена издръжливост",
    "Z4": "Z4 · прагова / надпрагова работа",
    "Z5": "Z5 · VO₂max / висока интензивност",
    "STR": "STR · силова подготовка",
}

COMPONENT_SHORT = {
    "Z1": "Z1",
    "Z2": "Z2",
    "Z3": "Z3",
    "Z4": "Z4",
    "Z5": "Z5",
    "STR": "Сила",
}

DEFAULT_ZONE_PROFILE = [
    {
        "component": "Z1",
        "hr_low": 100,
        "hr_high": 125,
        "weight_low": 100.0,
        "weight_high": 120.0,
        "power": 1.00,
    },
    {
        "component": "Z2",
        "hr_low": 126,
        "hr_high": 145,
        "weight_low": 120.0,
        "weight_high": 150.0,
        "power": 1.10,
    },
    {
        "component": "Z3",
        "hr_low": 146,
        "hr_high": 162,
        "weight_low": 150.0,
        "weight_high": 220.0,
        "power": 1.15,
    },
    {
        "component": "Z4",
        "hr_low": 163,
        "hr_high": 177,
        "weight_low": 220.0,
        "weight_high": 300.0,
        "power": 1.20,
    },
    {
        "component": "Z5",
        "hr_low": 178,
        "hr_high": 195,
        "weight_low": 300.0,
        "weight_high": 420.0,
        "power": 1.10,
    },
]

DEFAULT_BASE_LOADS = {
    "Z1": 40.0,
    "Z2": 20.0,
    "Z3": 8.0,
    "Z4": 4.0,
    "Z5": 2.0,
    "STR": 8.0,
}

DEFAULT_RECOVERY = {
    "Z1": {"sensitivity": 0.55, "tau_days": 0.75, "fmax": 130.0},
    "Z2": {"sensitivity": 0.70, "tau_days": 1.00, "fmax": 135.0},
    "Z3": {"sensitivity": 0.88, "tau_days": 1.35, "fmax": 145.0},
    "Z4": {"sensitivity": 1.00, "tau_days": 1.65, "fmax": 155.0},
    "Z5": {"sensitivity": 1.12, "tau_days": 2.00, "fmax": 165.0},
    "STR": {"sensitivity": 0.95, "tau_days": 1.70, "fmax": 150.0},
}

# Редът е приемащ компонент, колоната е източник на директен товар.
DEFAULT_CASCADE = {
    receiver: {
        source: (
            1.0
            if receiver == source
            else 1.0
            if receiver in AEROBIC_COMPONENTS
            and source in AEROBIC_COMPONENTS
            and AEROBIC_COMPONENTS.index(source) > AEROBIC_COMPONENTS.index(receiver)
            else 0.0
        )
        for source in COMPONENTS
    }
    for receiver in COMPONENTS
}

DEFAULT_PARAMETERS = {
    "short_window_days": 7,
    "long_window_days": 40,
    "base_window_days": 50,
    "spill_threshold_fraction": 0.50,
    "spill_fraction": 0.20,
    "key_stimulus_fraction": 0.40,
    "key_readiness_threshold": 90.0,
    "practical_full_recovery": 95.0,
    "current_metric_weight": 0.60,
    "min_valid_days_7": 4,
    "min_valid_days_40": 20,
    "max_positive_test_adjustment": 0.05,
    "max_negative_test_adjustment": -0.10,
    "taper_volume_reduction": 0.35,
    "mesocycle_pattern": [0.96, 1.04, 1.10, 0.78],
    "base_loads": DEFAULT_BASE_LOADS,
    "recovery": DEFAULT_RECOVERY,
    "cascade": DEFAULT_CASCADE,
}

METRIC_DEFINITIONS = {
    "sleep_quality": {
        "label": "Качество на съня",
        "unit": "/10",
        "direction": 1,
        "good": 8.0,
        "bad": 4.0,
        "stabilizer": 1.0,
        "min_std": 0.7,
        "critical_low": 2.0,
        "influence": {c: 1.0 for c in COMPONENTS},
    },
    "fatigue": {
        "label": "Обща умора",
        "unit": "/10",
        "direction": -1,
        "good": 2.0,
        "bad": 8.0,
        "stabilizer": 1.0,
        "min_std": 0.7,
        "critical_high": 9.0,
        "influence": {c: 1.0 for c in COMPONENTS},
    },
    "soreness_legs": {
        "label": "Болезненост · крака",
        "unit": "/10",
        "direction": -1,
        "good": 1.5,
        "bad": 7.0,
        "stabilizer": 1.0,
        "min_std": 0.7,
        "critical_high": 8.0,
        "influence": {"Z1": 0.5, "Z2": 0.8, "Z3": 1.2, "Z4": 1.3, "Z5": 1.2, "STR": 0.9},
    },
    "soreness_upper": {
        "label": "Болезненост · горна част",
        "unit": "/10",
        "direction": -1,
        "good": 1.5,
        "bad": 7.0,
        "stabilizer": 1.0,
        "min_std": 0.7,
        "critical_high": 8.0,
        "influence": {"Z1": 0.3, "Z2": 0.5, "Z3": 0.8, "Z4": 0.9, "Z5": 1.0, "STR": 1.5},
    },
    "stress": {
        "label": "Психологически стрес",
        "unit": "/10",
        "direction": -1,
        "good": 2.0,
        "bad": 8.0,
        "stabilizer": 1.0,
        "min_std": 0.8,
        "critical_high": 9.0,
        "influence": {c: 0.8 for c in COMPONENTS},
    },
    "motivation": {
        "label": "Мотивация / готовност",
        "unit": "/10",
        "direction": 1,
        "good": 8.0,
        "bad": 4.0,
        "stabilizer": 1.0,
        "min_std": 0.7,
        "critical_low": 2.0,
        "influence": {c: 0.7 for c in COMPONENTS},
    },
    "pain": {
        "label": "Болка / симптом",
        "unit": "/10",
        "direction": -1,
        "good": 0.0,
        "bad": 6.0,
        "stabilizer": 0.5,
        "min_std": 0.5,
        "critical_high": 7.0,
        "influence": {c: 1.5 for c in COMPONENTS},
    },
    "morning_hr": {
        "label": "Сутрешен пулс",
        "unit": "уд./мин",
        "direction": -1,
        "mode": "baseline",
        "stabilizer": 20.0,
        "min_std": 2.0,
        "critical_z": -2.5,
        "influence": {c: 1.0 for c in COMPONENTS},
    },
    "hrv": {
        "label": "HRV",
        "unit": "ms",
        "direction": 1,
        "mode": "baseline",
        "stabilizer": 10.0,
        "min_std": 4.0,
        "critical_z": -2.5,
        "influence": {c: 1.0 for c in COMPONENTS},
    },
    "sleep_hours": {
        "label": "Продължителност на съня",
        "unit": "h",
        "direction": 1,
        "good": 8.0,
        "bad": 5.5,
        "stabilizer": 2.0,
        "min_std": 0.5,
        "critical_low": 4.5,
        "influence": {c: 0.9 for c in COMPONENTS},
    },
}

TEST_DEFINITIONS = {
    "SKIERG_3MIN": {
        "label": "3 мин максимално дърпане на тренажор",
        "primary_label": "Средна мощност",
        "primary_unit": "W",
        "primary_direction": 1,
        "secondary_label": "Спад на мощността",
        "secondary_unit": "%",
        "secondary_direction": -1,
        "weights": (0.75, 0.25),
        "components": {"Z5": 0.55, "STR": 0.45},
    },
    "Z3_20MIN": {
        "label": "20 мин субмаксимално около горна граница на Z3",
        "primary_label": "Средна скорост",
        "primary_unit": "km/h",
        "primary_direction": 1,
        "secondary_label": "Пулсов дрейф",
        "secondary_unit": "%",
        "secondary_direction": -1,
        "weights": (0.70, 0.30),
        "components": {"Z2": 0.35, "Z3": 0.65},
    },
    "SPRINT_200M": {
        "label": "200 м спринт срещу наклон с ролкови ски",
        "primary_label": "Време",
        "primary_unit": "s",
        "primary_direction": -1,
        "secondary_label": "Вариация между повторенията",
        "secondary_unit": "%",
        "secondary_direction": -1,
        "weights": (0.80, 0.20),
        "components": {"Z5": 0.45, "STR": 0.55},
    },
}

PERIODIZATION_CENTERS = {
    "Z1": (0.20, 0.28),
    "Z2": (0.35, 0.26),
    "Z3": (0.56, 0.23),
    "Z4": (0.74, 0.20),
    "Z5": (0.84, 0.17),
    "STR": (0.40, 0.25),
}

ROLE_LABELS = [
    "Главен треньор",
    "Личен треньор",
    "Спортист",
    "Наблюдател",
]

EDIT_ROLES = {"Главен треньор", "Личен треньор"}
EXPERT_ROLES = {"Главен треньор"}


def fresh_parameters() -> dict:
    """Връща независим набор от начални параметри."""

    return deepcopy(DEFAULT_PARAMETERS)
