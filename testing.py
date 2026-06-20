"""Контролни тестове, сравнимост и компонентни корекции."""

from __future__ import annotations

from datetime import date
from typing import Any

import numpy as np
import pandas as pd

from .constants import COMPONENTS, TEST_DEFINITIONS

EPS = 1e-9


def _change_percent(current: float, reference: float, direction: float) -> float:
    return 100.0 * direction * (current - reference) / max(abs(reference), EPS)


def analyze_tests(
    tests: pd.DataFrame,
    athlete_id: str,
    parameters: dict[str, Any],
    as_of: date | pd.Timestamp | None = None,
) -> tuple[pd.DataFrame, pd.Series]:
    athlete = tests.loc[tests["athlete_id"] == athlete_id].copy()
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
                "composite_change_pct",
                "comparability",
                "valid",
                "reliability",
                "note",
            ]
        ).set_index("test_code")
        return empty, pd.Series(0.0, index=COMPONENTS, name="test_adjustment")

    athlete["date"] = pd.to_datetime(athlete["date"]).dt.normalize()
    end = pd.Timestamp(as_of if as_of is not None else athlete["date"].max()).normalize()
    athlete = athlete.loc[athlete["date"] <= end].sort_values("date")
    rows: list[dict[str, Any]] = []

    for test_code, definition in TEST_DEFINITIONS.items():
        subset = athlete.loc[athlete["test_code"] == test_code].sort_values("date")
        if subset.empty:
            continue
        latest = subset.iloc[-1]
        prior = subset.loc[
            (subset["date"] < latest["date"])
            & subset["valid"].astype(bool)
            & (subset["protocol_version"] == latest["protocol_version"])
        ].tail(3)
        valid = bool(latest["valid"])
        comparability = float(np.clip(latest.get("comparability", 0.0), 0.0, 1.0))

        if prior.empty or not valid:
            ref_primary = np.nan
            ref_secondary = np.nan
            primary_change = 0.0
            secondary_change = 0.0
            composite = 0.0
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
            w_primary, w_secondary = definition["weights"]
            composite_raw = float(w_primary) * primary_change + float(w_secondary) * secondary_change
            composite = composite_raw * comparability
            reliability = comparability * min(1.0, len(prior) / 3.0)

        rows.append(
            {
                "test_code": test_code,
                "label": definition["label"],
                "date": latest["date"],
                "primary_value": float(latest["primary_value"]),
                "secondary_value": float(latest["secondary_value"]),
                "reference_primary": ref_primary,
                "reference_secondary": ref_secondary,
                "primary_change_pct": primary_change,
                "secondary_change_pct": secondary_change,
                "composite_change_pct": composite,
                "comparability": comparability,
                "valid": valid,
                "reliability": reliability,
                "note": str(latest.get("note", "")),
            }
        )

    details = pd.DataFrame(rows).set_index("test_code") if rows else pd.DataFrame()
    raw_component = {component: 0.0 for component in COMPONENTS}
    component_weight = {component: 0.0 for component in COMPONENTS}

    if not details.empty:
        for test_code, row in details.iterrows():
            definition = TEST_DEFINITIONS[test_code]
            if not bool(row["valid"]) or float(row["reliability"]) <= 0:
                continue
            # 0.5 превръща процентната промяна в малка корекция на целта.
            proposed = 0.005 * float(row["composite_change_pct"])
            for component, weight in definition["components"].items():
                raw_component[component] += proposed * float(weight) * float(row["reliability"])
                component_weight[component] += float(weight) * float(row["reliability"])

    max_positive = float(parameters["max_positive_test_adjustment"])
    max_negative = float(parameters["max_negative_test_adjustment"])
    adjustments = {}
    for component in COMPONENTS:
        value = raw_component[component] / component_weight[component] if component_weight[component] > 0 else 0.0
        adjustments[component] = float(np.clip(value, max_negative, max_positive))
    return details, pd.Series(adjustments, name="test_adjustment")


def tests_long_history(tests: pd.DataFrame, athlete_id: str, test_code: str) -> pd.DataFrame:
    definition = TEST_DEFINITIONS[test_code]
    data = tests.loc[(tests["athlete_id"] == athlete_id) & (tests["test_code"] == test_code)].copy()
    data["date"] = pd.to_datetime(data["date"]).dt.normalize()
    data = data.sort_values("date")
    data["primary_label"] = definition["primary_label"]
    data["secondary_label"] = definition["secondary_label"]
    return data
