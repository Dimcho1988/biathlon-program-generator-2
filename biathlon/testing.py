"""Контролни тестове, сравнимост и компонентни корекции."""

from __future__ import annotations

from datetime import date
from typing import Any

import numpy as np
import pandas as pd

from .constants import COMPONENTS, DEFAULT_TEST_SETTINGS, TEST_DEFINITIONS

EPS = 1e-9


def _change_percent(current: float, reference: float, direction: float) -> float:
    return 100.0 * direction * (current - reference) / max(abs(reference), EPS)


def resolved_test_settings(parameters: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Връща пълна и безопасно ограничена експертна конфигурация за тестовете."""

    configured = parameters.get("test_settings", {})
    result: dict[str, dict[str, Any]] = {}
    for test_code, defaults in DEFAULT_TEST_SETTINGS.items():
        raw = configured.get(test_code, {}) if isinstance(configured, dict) else {}
        settings = {**defaults, **{key: value for key, value in raw.items() if key != "component_multipliers"}}
        multipliers = {
            **defaults["component_multipliers"],
            **(
                raw.get("component_multipliers", {})
                if isinstance(raw, dict) and isinstance(raw.get("component_multipliers"), dict)
                else {}
            ),
        }
        primary_weight = max(0.0, float(settings.get("primary_weight", defaults["primary_weight"])))
        secondary_weight = max(0.0, float(settings.get("secondary_weight", defaults["secondary_weight"])))
        weight_total = primary_weight + secondary_weight
        if weight_total <= EPS:
            primary_weight = float(defaults["primary_weight"])
            secondary_weight = float(defaults["secondary_weight"])
            weight_total = primary_weight + secondary_weight
        result[test_code] = {
            "enabled": bool(settings.get("enabled", True)),
            "primary_weight": primary_weight / weight_total,
            "secondary_weight": secondary_weight / weight_total,
            "min_comparability": float(np.clip(settings.get("min_comparability", 0.50), 0.0, 1.0)),
            "readiness_strength": float(np.clip(settings.get("readiness_strength", 0.0), 0.0, 2.0)),
            "planning_strength": float(np.clip(settings.get("planning_strength", 1.0), 0.0, 2.0)),
            "max_age_days": max(0, int(settings.get("max_age_days", 56))),
            "half_life_days": max(1.0, float(settings.get("half_life_days", 28.0))),
            "max_positive_adjustment": float(
                np.clip(settings.get("max_positive_adjustment", 0.05), 0.0, 0.50)
            ),
            "max_negative_adjustment": float(
                np.clip(settings.get("max_negative_adjustment", -0.10), -0.50, 0.0)
            ),
            "component_multipliers": {
                component: float(np.clip(multipliers.get(component, 0.0), 0.0, 2.0))
                for component in COMPONENTS
            },
        }
    return result


def _empty_test_effects() -> pd.DataFrame:
    return pd.DataFrame(
        columns=[
            "test_code",
            "component",
            "test_date",
            "age_days",
            "max_age_days",
            "half_life_days",
            "component_multiplier",
            "signal",
            "readiness_base_adjustment",
            "planning_base_adjustment",
        ]
    )


def aggregate_test_effects(
    effects: pd.DataFrame,
    parameters: dict[str, Any],
    channel: str,
    future_days: int | float = 0,
) -> pd.Series:
    """Сумира компонентните ефекти с изтичане и half-life от датата на теста."""

    if channel not in {"readiness", "planning"}:
        raise ValueError("channel трябва да е 'readiness' или 'planning'")
    totals = pd.Series(0.0, index=COMPONENTS, name=f"{channel}_test_adjustment")
    if effects.empty:
        return totals

    base_column = f"{channel}_base_adjustment"
    horizon = max(0.0, float(future_days))
    for _, effect in effects.iterrows():
        component = str(effect["component"])
        if component not in totals.index:
            continue
        age_days = max(0.0, float(effect["age_days"]) + horizon)
        if age_days > float(effect["max_age_days"]):
            continue
        half_life = max(1.0, float(effect["half_life_days"]))
        recency = float(2.0 ** (-age_days / half_life))
        totals[component] += float(effect[base_column]) * recency

    lower = float(parameters.get("max_negative_test_adjustment", -0.10))
    upper = float(parameters.get("max_positive_test_adjustment", 0.05))
    return totals.clip(lower=lower, upper=upper)


def analyze_tests(
    tests: pd.DataFrame,
    athlete_id: str,
    parameters: dict[str, Any],
    as_of: date | pd.Timestamp | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    athlete = tests.loc[tests["athlete_id"] == athlete_id].copy()
    settings_by_test = resolved_test_settings(parameters)
    if athlete.empty:
        empty = pd.DataFrame(
            columns=[
                "test_code",
                "label",
                "date",
                "primary_value",
                "secondary_value",
                "reference_primary",
                "reference_secondary",
                "primary_change_pct",
                "secondary_change_pct",
                "raw_composite_change_pct",
                "effective_change_pct",
                "composite_change_pct",
                "comparability",
                "valid",
                "reliability",
                "age_days",
                "active",
                "note",
            ]
        ).set_index("test_code")
        return empty, _empty_test_effects()

    athlete["date"] = pd.to_datetime(athlete["date"]).dt.normalize()
    end = pd.Timestamp(as_of if as_of is not None else athlete["date"].max()).normalize()
    athlete = athlete.loc[athlete["date"] <= end].sort_values("date")
    rows: list[dict[str, Any]] = []
    effect_rows: list[dict[str, Any]] = []

    for test_code, definition in TEST_DEFINITIONS.items():
        settings = settings_by_test[test_code]
        subset = athlete.loc[athlete["test_code"] == test_code].sort_values("date")
        if subset.empty:
            continue
        latest = subset.iloc[-1]
        latest_date = pd.Timestamp(latest["date"]).normalize()
        age_days = max(0, int((end - latest_date).days))
        prior = subset.loc[
            (subset["date"] < latest["date"])
            & subset["valid"].astype(bool)
            & (subset["protocol_version"] == latest["protocol_version"])
        ].tail(3)
        valid = bool(latest["valid"])
        comparability = float(np.clip(latest.get("comparability", 0.0), 0.0, 1.0))
        eligible = bool(
            not prior.empty
            and valid
            and settings["enabled"]
            and comparability >= float(settings["min_comparability"])
        )

        if not eligible:
            ref_primary = np.nan
            ref_secondary = np.nan
            primary_change = 0.0
            secondary_change = 0.0
            raw_composite = 0.0
            effective_change = 0.0
            reliability = 0.0 if not valid else 0.25
        else:
            ref_primary = float(pd.to_numeric(prior["primary_value"], errors="coerce").median())
            ref_secondary = float(pd.to_numeric(prior["secondary_value"], errors="coerce").median())
            primary_change = _change_percent(
                float(latest["primary_value"]), ref_primary, float(definition["primary_direction"])
            )
            secondary_change = _change_percent(
                float(latest["secondary_value"]), ref_secondary, float(definition["secondary_direction"])
            )
            raw_composite = (
                float(settings["primary_weight"]) * primary_change
                + float(settings["secondary_weight"]) * secondary_change
            )
            reliability = comparability * min(1.0, len(prior) / 3.0)
            effective_change = raw_composite * reliability

        signal = float(
            np.clip(
                0.005 * effective_change,
                float(settings["max_negative_adjustment"]),
                float(settings["max_positive_adjustment"]),
            )
        )
        active = bool(
            eligible
            and reliability > 0
            and age_days <= int(settings["max_age_days"])
        )

        rows.append(
            {
                "test_code": test_code,
                "label": definition["label"],
                "date": latest_date,
                "primary_value": float(latest["primary_value"]),
                "secondary_value": float(latest["secondary_value"]),
                "reference_primary": ref_primary,
                "reference_secondary": ref_secondary,
                "primary_change_pct": primary_change,
                "secondary_change_pct": secondary_change,
                "raw_composite_change_pct": raw_composite,
                "effective_change_pct": effective_change,
                "composite_change_pct": effective_change,
                "comparability": comparability,
                "valid": valid,
                "reliability": reliability,
                "age_days": age_days,
                "active": active,
                "note": str(latest.get("note", "")),
            }
        )

        for component in COMPONENTS:
            multiplier = float(settings["component_multipliers"][component])
            effect_rows.append(
                {
                    "test_code": test_code,
                    "component": component,
                    "test_date": latest_date,
                    "age_days": age_days,
                    "max_age_days": int(settings["max_age_days"]),
                    "half_life_days": float(settings["half_life_days"]),
                    "component_multiplier": multiplier,
                    "signal": signal,
                    "readiness_base_adjustment": (
                        signal * float(settings["readiness_strength"]) * multiplier
                        if active
                        else 0.0
                    ),
                    "planning_base_adjustment": (
                        signal * float(settings["planning_strength"]) * multiplier
                        if active
                        else 0.0
                    ),
                }
            )

    details = pd.DataFrame(rows).set_index("test_code") if rows else pd.DataFrame()
    effects = pd.DataFrame(effect_rows) if effect_rows else _empty_test_effects()
    return details, effects


def tests_long_history(tests: pd.DataFrame, athlete_id: str, test_code: str) -> pd.DataFrame:
    definition = TEST_DEFINITIONS[test_code]
    data = tests.loc[(tests["athlete_id"] == athlete_id) & (tests["test_code"] == test_code)].copy()
    data["date"] = pd.to_datetime(data["date"]).dt.normalize()
    data = data.sort_values("date")
    data["primary_label"] = definition["primary_label"]
    data["secondary_label"] = definition["secondary_label"]
    return data
