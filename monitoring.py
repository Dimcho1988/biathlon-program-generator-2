"""Анализ на субективни/обективни показатели и интегрирана готовност."""

from __future__ import annotations

from datetime import date
from typing import Any

import numpy as np
import pandas as pd

from .constants import COMPONENTS, METRIC_DEFINITIONS

EPS = 1e-9


def _window(series: pd.Series, end: pd.Timestamp, days: int) -> pd.Series:
    start = end - pd.Timedelta(days=days - 1)
    return series.loc[(series.index >= start) & (series.index <= end)]


def _absolute_score(value: float, definition: dict[str, Any], favorable_z: float) -> float:
    if definition.get("mode") == "baseline":
        return float(np.clip(70.0 + 15.0 * favorable_z, 0.0, 100.0))
    good = float(definition["good"])
    bad = float(definition["bad"])
    if int(definition["direction"]) > 0:
        score = 100.0 * (value - bad) / max(good - bad, EPS)
    else:
        score = 100.0 * (bad - value) / max(bad - good, EPS)
    score = float(np.clip(score, 0.0, 100.0))
    baseline_score = float(np.clip(70.0 + 15.0 * favorable_z, 0.0, 100.0))
    return 0.65 * score + 0.35 * baseline_score


def analyze_metric(
    athlete_wellness: pd.DataFrame,
    metric: str,
    parameters: dict[str, Any],
    as_of: date | pd.Timestamp | None = None,
) -> dict[str, Any]:
    definition = METRIC_DEFINITIONS[metric]
    data = athlete_wellness.copy()
    data["date"] = pd.to_datetime(data["date"]).dt.normalize()
    end = pd.Timestamp(as_of if as_of is not None else data["date"].max()).normalize()
    series = (
        data.set_index("date")[metric]
        .pipe(pd.to_numeric, errors="coerce")
        .dropna()
        .sort_index()
    )
    series = series.loc[series.index <= end]

    if series.empty:
        return {
            "metric": metric,
            "label": definition["label"],
            "unit": definition["unit"],
            "current": np.nan,
            "mean7": np.nan,
            "mean40": np.nan,
            "std40": np.nan,
            "index_7_40": np.nan,
            "z_favorable": np.nan,
            "current_score": 70.0,
            "trend_score": 70.0,
            "score": 70.0,
            "reliability": 0.0,
            "critical": False,
            "n7": 0,
            "n40": 0,
        }

    current = float(series.iloc[-1])
    s7 = _window(series, end, 7)
    s40 = _window(series, end, 40)
    mean7 = float(s7.mean()) if not s7.empty else current
    mean40 = float(s40.mean()) if not s40.empty else current
    std40 = float(s40.std(ddof=0)) if len(s40) > 1 else float(definition["min_std"])
    std_used = max(std40, float(definition["min_std"]))
    direction = float(definition["direction"])
    favorable_z = direction * (current - mean40) / std_used
    stabilizer = float(definition["stabilizer"])
    index_7_40 = (stabilizer + mean7) / max(stabilizer + mean40, EPS)

    current_score = _absolute_score(current, definition, favorable_z)
    trend_score = float(np.clip(75.0 + 200.0 * direction * (index_7_40 - 1.0), 0.0, 100.0))
    alpha = float(parameters["current_metric_weight"])
    raw_score = alpha * current_score + (1.0 - alpha) * trend_score

    n7 = int(s7.count())
    n40 = int(s40.count())
    reliability = min(1.0, n7 / max(float(parameters["min_valid_days_7"]), 1.0)) * min(
        1.0, n40 / max(float(parameters["min_valid_days_40"]), 1.0)
    )
    if "reliability" in data.columns:
        recent_reliability = pd.to_numeric(
            data.loc[data["date"].between(end - pd.Timedelta(days=39), end), "reliability"], errors="coerce"
        ).dropna()
        if not recent_reliability.empty:
            reliability *= float(np.clip(recent_reliability.mean(), 0.0, 1.0))
    score = 70.0 + (raw_score - 70.0) * reliability

    critical = False
    if "critical_high" in definition and current >= float(definition["critical_high"]):
        critical = True
    if "critical_low" in definition and current <= float(definition["critical_low"]):
        critical = True
    if "critical_z" in definition and favorable_z <= float(definition["critical_z"]):
        critical = True

    return {
        "metric": metric,
        "label": definition["label"],
        "unit": definition["unit"],
        "current": current,
        "mean7": mean7,
        "mean40": mean40,
        "std40": std40,
        "index_7_40": index_7_40,
        "z_favorable": favorable_z,
        "current_score": current_score,
        "trend_score": trend_score,
        "score": float(np.clip(score, 0.0, 100.0)),
        "reliability": float(np.clip(reliability, 0.0, 1.0)),
        "critical": critical,
        "n7": n7,
        "n40": n40,
    }


def analyze_wellness(
    wellness: pd.DataFrame,
    athlete_id: str,
    parameters: dict[str, Any],
    as_of: date | pd.Timestamp | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame, list[str]]:
    athlete = wellness.loc[wellness["athlete_id"] == athlete_id].copy()
    if athlete.empty:
        metric_rows = [analyze_metric(athlete, metric, parameters, as_of) for metric in METRIC_DEFINITIONS]
    else:
        metric_rows = [analyze_metric(athlete, metric, parameters, as_of) for metric in METRIC_DEFINITIONS]
    metric_details = pd.DataFrame(metric_rows).set_index("metric")

    end = pd.Timestamp(as_of if as_of is not None else athlete["date"].max()).normalize() if not athlete.empty else pd.Timestamp(date.today())
    hard_reasons: list[str] = []
    if not athlete.empty:
        latest = athlete.loc[pd.to_datetime(athlete["date"]).dt.normalize() <= end].sort_values("date").tail(1)
        if not latest.empty and bool(latest.iloc[0].get("illness", False)):
            hard_reasons.append("Отбелязани симптоми/заболяване")
    for metric, row in metric_details.iterrows():
        if bool(row["critical"]):
            hard_reasons.append(f"Критична стойност: {row['label']}")

    component_rows: list[dict[str, Any]] = []
    for component in COMPONENTS:
        weighted_sum = 0.0
        weight_total = 0.0
        critical_for_component = False
        critical_labels: list[str] = []
        for metric, definition in METRIC_DEFINITIONS.items():
            influence = float(definition["influence"].get(component, 0.0))
            if influence <= 0:
                continue
            row = metric_details.loc[metric]
            reliability_weight = max(0.15, float(row["reliability"]))
            weighted_sum += float(row["score"]) * influence * reliability_weight
            weight_total += influence * reliability_weight
            if bool(row["critical"]) and influence >= 0.8:
                critical_for_component = True
                critical_labels.append(str(row["label"]))
        score = weighted_sum / weight_total if weight_total > 0 else 70.0
        if hard_reasons and any("заболяване" in reason.lower() for reason in hard_reasons):
            critical_for_component = True
            critical_labels.append("симптоми/заболяване")
        component_rows.append(
            {
                "component": component,
                "monitoring_score": float(np.clip(score, 0.0, 100.0)),
                "hard_flag": critical_for_component,
                "hard_reasons": ", ".join(sorted(set(critical_labels))),
            }
        )
    return metric_details, pd.DataFrame(component_rows).set_index("component"), hard_reasons


def adaptive_multiplier_from_score(score: float, hard_flag: bool = False) -> float:
    score = float(np.clip(score, 0.0, 100.0))
    if score >= 85:
        multiplier = 1.0 + min(0.05, (score - 85.0) / 300.0)
    elif score >= 70:
        multiplier = 0.90 + (score - 70.0) / 150.0
    elif score >= 55:
        multiplier = 0.75 + (score - 55.0) / 100.0
    else:
        multiplier = 0.50 + score / 220.0
    if hard_flag:
        multiplier = min(multiplier, 0.65)
    return float(np.clip(multiplier, 0.50, 1.05))


def integrate_component_readiness(
    load_readiness: pd.DataFrame,
    monitoring_by_component: pd.DataFrame,
    test_adjustments: pd.Series,
) -> pd.DataFrame:
    """Прозрачна интеграция на товарна готовност, мониторинг и тестове."""

    rows: list[dict[str, Any]] = []
    for component in COMPONENTS:
        load_score = float(load_readiness.loc[component, "readiness"])
        monitoring_score = float(monitoring_by_component.loc[component, "monitoring_score"])
        adjustment = float(test_adjustments.get(component, 0.0))
        test_score = float(np.clip(75.0 + 250.0 * adjustment, 45.0, 90.0))
        integrated = 0.55 * load_score + 0.35 * monitoring_score + 0.10 * test_score
        hard_flag = bool(monitoring_by_component.loc[component, "hard_flag"])
        if hard_flag:
            integrated = min(integrated, 45.0)
        multiplier = adaptive_multiplier_from_score(integrated, hard_flag)

        reasons = [
            f"товарна readiness {load_score:.0f}%",
            f"вътрешен мониторинг {monitoring_score:.0f}/100",
        ]
        if adjustment > 0.001:
            reasons.append(f"положителна тестова корекция +{adjustment*100:.1f}%")
        elif adjustment < -0.001:
            reasons.append(f"отрицателна тестова корекция {adjustment*100:.1f}%")
        if hard_flag:
            hard_reason = str(monitoring_by_component.loc[component, "hard_reasons"])
            reasons.append(f"твърд флаг: {hard_reason or 'изисква преглед'}")

        rows.append(
            {
                "component": component,
                "load_readiness": load_score,
                "monitoring_score": monitoring_score,
                "test_score": test_score,
                "test_adjustment": adjustment,
                "integrated_readiness": float(np.clip(integrated, 0.0, 100.0)),
                "adaptive_multiplier": multiplier,
                "hard_flag": hard_flag,
                "reason": "; ".join(reasons),
            }
        )
    return pd.DataFrame(rows).set_index("component")
