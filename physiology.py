"""Физиологично изчислително ядро: T, Q, E, 7/40, Tref и readiness."""

from __future__ import annotations

from datetime import date
from typing import Any, Iterable

import numpy as np
import pandas as pd
from scipy.optimize import least_squares

from .constants import AEROBIC_COMPONENTS, COMPONENTS

EPS = 1e-9


def intrazone_coefficient(position: float, weight_low: float, weight_high: float, power: float) -> float:
    """Безразмерен коефициент k при нормализирана позиция 0–1 в зоната."""

    u = float(np.clip(position, 0.0, 1.0))
    if weight_low <= 0:
        raise ValueError("weight_low трябва да бъде положително число")
    weight = weight_low + (weight_high - weight_low) * (u**power)
    return float(weight / weight_low)


def hr_intrazone_values(hr: float, zone: pd.Series | dict[str, Any]) -> tuple[float, float, float]:
    """Връща u, W и k за конкретен пулс и зона."""

    z = dict(zone)
    width = max(float(z["hr_high"]) - float(z["hr_low"]), EPS)
    u = float(np.clip((float(hr) - float(z["hr_low"])) / width, 0.0, 1.0))
    w = float(z["weight_low"] + (z["weight_high"] - z["weight_low"]) * (u ** float(z["power"])))
    k = w / max(float(z["weight_low"]), EPS)
    return u, w, k


def analyze_activity_stream(stream: pd.DataFrame, zone_profile: pd.DataFrame) -> pd.DataFrame:
    """Анализира 1-секунден поток и връща T, Q и среден k по зона.

    Невалидни пулсови стойности или секунди с ``moving=False`` не участват в товара.
    """

    required = {"hr", "moving"}
    missing = required.difference(stream.columns)
    if missing:
        raise ValueError(f"Липсват колони в секундния поток: {sorted(missing)}")

    zones = zone_profile.sort_values("hr_low").copy()
    valid = stream.copy()
    valid["hr"] = pd.to_numeric(valid["hr"], errors="coerce")
    valid = valid.loc[valid["moving"].fillna(False) & valid["hr"].between(30, 240)].copy()

    rows: list[dict[str, float | str]] = []
    for _, zone in zones.iterrows():
        code = str(zone["component"])
        mask = valid["hr"].between(float(zone["hr_low"]), float(zone["hr_high"]), inclusive="both")
        subset = valid.loc[mask, "hr"]
        if subset.empty:
            rows.append({"component": code, "real_min": 0.0, "q_min": 0.0, "avg_k": np.nan, "valid_seconds": 0})
            continue
        width = max(float(zone["hr_high"]) - float(zone["hr_low"]), EPS)
        u = ((subset - float(zone["hr_low"])) / width).clip(0, 1)
        w = float(zone["weight_low"]) + (float(zone["weight_high"]) - float(zone["weight_low"])) * (
            u ** float(zone["power"])
        )
        k = w / max(float(zone["weight_low"]), EPS)
        real_min = len(subset) / 60.0
        q_min = float(k.sum() / 60.0)
        rows.append(
            {
                "component": code,
                "real_min": real_min,
                "q_min": q_min,
                "avg_k": q_min / real_min if real_min > 0 else np.nan,
                "valid_seconds": int(len(subset)),
            }
        )
    return pd.DataFrame(rows)


def activities_to_activity_summaries(
    activities: pd.DataFrame,
    zone_profile: pd.DataFrame,
) -> pd.DataFrame:
    """Преобразува реални минути и средна позиция в зоната до директен Q."""

    if activities.empty:
        columns = ["activity_id", "date", *[f"real_{c}" for c in COMPONENTS], *[f"q_{c}" for c in COMPONENTS]]
        return pd.DataFrame(columns=columns)

    profile = zone_profile.set_index("component")
    result = activities.copy()
    for component in AEROBIC_COMPONENTS:
        zone = profile.loc[component]
        position = pd.to_numeric(result.get(f"pos_{component}", 0.0), errors="coerce").fillna(0.0).clip(0, 1)
        k = (
            float(zone["weight_low"])
            + (float(zone["weight_high"]) - float(zone["weight_low"])) * (position ** float(zone["power"]))
        ) / max(float(zone["weight_low"]), EPS)
        real = pd.to_numeric(result.get(f"real_{component}", 0.0), errors="coerce").fillna(0.0).clip(lower=0)
        result[f"k_{component}"] = k
        result[f"q_{component}"] = real * k

    real_str = pd.to_numeric(result.get("real_STR", 0.0), errors="coerce").fillna(0.0).clip(lower=0)
    strength_k = pd.to_numeric(result.get("strength_k", 1.0), errors="coerce").fillna(1.0).clip(lower=0.5, upper=2.0)
    result["k_STR"] = strength_k
    result["q_STR"] = real_str * strength_k
    result["date"] = pd.to_datetime(result["date"]).dt.normalize()
    return result


def _cascade_matrix(parameters: dict[str, Any]) -> np.ndarray:
    cascade = parameters["cascade"]
    return np.array([[float(cascade[receiver][source]) for source in COMPONENTS] for receiver in COMPONENTS], dtype=float)


def effective_from_direct_vector(
    direct_q: Iterable[float] | dict[str, float] | pd.Series,
    tref: Iterable[float] | dict[str, float] | pd.Series,
    parameters: dict[str, Any],
) -> np.ndarray:
    """Изчислява E от Q чрез каскада и разлив към съседната по-висока зона."""

    if isinstance(direct_q, (dict, pd.Series)):
        q = np.array([float(direct_q.get(c, 0.0)) for c in COMPONENTS], dtype=float)
    else:
        q = np.asarray(list(direct_q), dtype=float)
    if isinstance(tref, (dict, pd.Series)):
        tref_values = np.array([float(tref.get(c, 0.0)) for c in COMPONENTS], dtype=float)
    else:
        tref_values = np.asarray(list(tref), dtype=float)

    q = np.clip(q, 0.0, None)
    tref_values = np.clip(tref_values, EPS, None)
    effective = _cascade_matrix(parameters) @ q
    theta = float(parameters["spill_threshold_fraction"])
    beta = float(parameters["spill_fraction"])
    for idx in range(len(AEROBIC_COMPONENTS) - 1):
        spill = beta * max(0.0, q[idx] - theta * tref_values[idx])
        effective[idx + 1] += spill
    return np.clip(effective, 0.0, None)


def compute_daily_load_history(
    activity_summaries: pd.DataFrame,
    parameters: dict[str, Any],
    end_date: date | pd.Timestamp | None = None,
) -> pd.DataFrame:
    """Създава пълна дневна времева редица на Q и E.

    Разливът за даден ден използва Tref от предходните дни, което премахва
    едновременната зависимост между текущия E и текущия Tref.
    """

    if activity_summaries.empty:
        return pd.DataFrame(columns=[*[f"q_{c}" for c in COMPONENTS], *[f"e_{c}" for c in COMPONENTS]])

    q_columns = [f"q_{component}" for component in COMPONENTS]
    grouped = activity_summaries.groupby("date", as_index=True)[q_columns].sum().sort_index()
    start = pd.Timestamp(grouped.index.min()).normalize()
    end = pd.Timestamp(end_date if end_date is not None else grouped.index.max()).normalize()
    all_dates = pd.date_range(start, end, freq="D")
    direct = grouped.reindex(all_dates, fill_value=0.0)
    direct.index.name = "date"

    base_loads = parameters["base_loads"]
    long_window = int(parameters["long_window_days"])
    effective_rows: list[np.ndarray] = []
    output_rows: list[dict[str, float | pd.Timestamp]] = []

    for current_date, row in direct.iterrows():
        if effective_rows:
            history = np.vstack(effective_rows[-long_window:])
            chronic = history.mean(axis=0)
            tref = np.where(chronic > EPS, 7.0 * chronic, np.array([7.0 * base_loads[c] for c in COMPONENTS]))
        else:
            tref = np.array([7.0 * base_loads[c] for c in COMPONENTS], dtype=float)

        q = np.array([float(row[f"q_{c}"]) for c in COMPONENTS], dtype=float)
        effective = effective_from_direct_vector(q, tref, parameters)
        effective_rows.append(effective)

        record: dict[str, float | pd.Timestamp] = {"date": current_date}
        for idx, component in enumerate(COMPONENTS):
            record[f"q_{component}"] = q[idx]
            record[f"e_{component}"] = effective[idx]
            record[f"tref_used_{component}"] = tref[idx]
        output_rows.append(record)

    return pd.DataFrame(output_rows).set_index("date")


def _window_mean(series: pd.Series, end: pd.Timestamp, days: int) -> float:
    start = end - pd.Timedelta(days=days - 1)
    window_index = pd.date_range(start, end, freq="D")
    return float(series.reindex(window_index, fill_value=0.0).mean())


def compute_load_statistics(
    daily_loads: pd.DataFrame,
    parameters: dict[str, Any],
    as_of: date | pd.Timestamp | None = None,
) -> pd.DataFrame:
    """Текущи E7, E40, E50, B, 7/40 и Tref по компоненти."""

    if daily_loads.empty:
        as_of_ts = pd.Timestamp(as_of or date.today())
    else:
        as_of_ts = pd.Timestamp(as_of if as_of is not None else daily_loads.index.max()).normalize()

    short_window = int(parameters["short_window_days"])
    long_window = int(parameters["long_window_days"])
    base_window = int(parameters["base_window_days"])
    base_loads = parameters["base_loads"]
    history_days = 0 if daily_loads.empty else max(0, (as_of_ts - pd.Timestamp(daily_loads.index.min())).days + 1)

    rows: list[dict[str, float | str]] = []
    for component in COMPONENTS:
        series = (
            daily_loads[f"e_{component}"].astype(float)
            if f"e_{component}" in daily_loads
            else pd.Series(dtype=float)
        )
        e7 = _window_mean(series, as_of_ts, short_window) if not series.empty else 0.0
        e40 = _window_mean(series, as_of_ts, long_window) if not series.empty else 0.0
        e50 = _window_mean(series, as_of_ts, base_window) if not series.empty else 0.0
        base = max(float(base_loads[component]), 0.5 * e50)
        index = (base + e7) / max(base + e40, EPS)
        tref = 7.0 * e40 if e40 > EPS else 7.0 * base
        reliability = min(1.0, history_days / float(long_window))
        rows.append(
            {
                "component": component,
                "E7_daily": e7,
                "E40_daily": e40,
                "E50_daily": e50,
                "base_load": base,
                "index_7_40": index,
                "Tref": tref,
                "reliability": reliability,
            }
        )
    return pd.DataFrame(rows).set_index("component")


def rolling_load_statistics(daily_loads: pd.DataFrame, parameters: dict[str, Any]) -> pd.DataFrame:
    """Дълга таблица с подвижни 7/40 и Tref за графики."""

    if daily_loads.empty:
        return pd.DataFrame(
            columns=["date", "component", "effective", "E7_daily", "E40_daily", "base_load", "index_7_40", "Tref"]
        )

    full = daily_loads.copy().sort_index()
    rows: list[pd.DataFrame] = []
    short_window = int(parameters["short_window_days"])
    long_window = int(parameters["long_window_days"])
    base_window = int(parameters["base_window_days"])

    for component in COMPONENTS:
        series = full[f"e_{component}"].astype(float)
        e7 = series.rolling(short_window, min_periods=1).mean()
        e40 = series.rolling(long_window, min_periods=1).mean()
        e50 = series.rolling(base_window, min_periods=1).mean()
        base = np.maximum(float(parameters["base_loads"][component]), 0.5 * e50)
        index = (base + e7) / np.maximum(base + e40, EPS)
        rows.append(
            pd.DataFrame(
                {
                    "date": full.index,
                    "component": component,
                    "effective": series.values,
                    "E7_daily": e7.values,
                    "E40_daily": e40.values,
                    "base_load": base,
                    "index_7_40": index,
                    "Tref": 7.0 * np.where(e40.values > EPS, e40.values, base),
                }
            )
        )
    return pd.concat(rows, ignore_index=True)


def compute_readiness_history(daily_loads: pd.DataFrame, parameters: dict[str, Any]) -> pd.DataFrame:
    """Симулира умората и readiness за всеки ден и компонент."""

    if daily_loads.empty:
        return pd.DataFrame(columns=["date", "component", "fatigue_before", "fatigue_after", "readiness_before", "readiness_after", "impulse", "Tref"])

    full = daily_loads.sort_index()
    long_window = int(parameters["long_window_days"])
    base_loads = parameters["base_loads"]
    fatigue = {component: 0.0 for component in COMPONENTS}
    rows: list[dict[str, float | str | pd.Timestamp]] = []

    for day_index, (current_date, row) in enumerate(full.iterrows()):
        for component in COMPONENTS:
            rec = parameters["recovery"][component]
            fatigue[component] *= float(np.exp(-1.0 / max(float(rec["tau_days"]), EPS)))
            fatigue_before = fatigue[component]
            history = full.iloc[max(0, day_index - long_window) : day_index][f"e_{component}"]
            chronic = float(history.mean()) if not history.empty else 0.0
            tref = 7.0 * chronic if chronic > EPS else 7.0 * float(base_loads[component])
            effective = float(row[f"e_{component}"])
            impulse = 100.0 * float(rec["sensitivity"]) * effective / max(tref, EPS)
            fatigue[component] = min(float(rec["fmax"]), fatigue[component] + impulse)
            rows.append(
                {
                    "date": current_date,
                    "component": component,
                    "fatigue_before": fatigue_before,
                    "fatigue_after": fatigue[component],
                    "readiness_before": float(np.clip(100.0 - fatigue_before, 0.0, 100.0)),
                    "readiness_after": float(np.clip(100.0 - fatigue[component], 0.0, 100.0)),
                    "impulse": impulse,
                    "Tref": tref,
                    "effective": effective,
                }
            )
    return pd.DataFrame(rows)


def current_readiness(
    readiness_history: pd.DataFrame,
    parameters: dict[str, Any],
    target_date: date | pd.Timestamp,
) -> pd.DataFrame:
    """Готовност по компоненти след затихване до target_date."""

    target = pd.Timestamp(target_date).normalize()
    rows: list[dict[str, float | str]] = []
    for component in COMPONENTS:
        subset = readiness_history.loc[readiness_history["component"] == component].sort_values("date")
        if subset.empty:
            fatigue = 0.0
            last_date = target
        else:
            last = subset.iloc[-1]
            fatigue = float(last["fatigue_after"])
            last_date = pd.Timestamp(last["date"]).normalize()
        delta_days = max(0.0, (target - last_date).total_seconds() / 86400.0)
        tau = float(parameters["recovery"][component]["tau_days"])
        fatigue *= float(np.exp(-delta_days / max(tau, EPS)))
        readiness = float(np.clip(100.0 - fatigue, 0.0, 100.0))
        threshold = float(parameters["practical_full_recovery"])
        residual_limit = max(100.0 - threshold, EPS)
        recovery_days = 0.0 if fatigue <= residual_limit else tau * np.log(fatigue / residual_limit)
        rows.append(
            {
                "component": component,
                "fatigue": fatigue,
                "readiness": readiness,
                "days_to_full": max(0.0, float(recovery_days)),
            }
        )
    return pd.DataFrame(rows).set_index("component")


def recover_fatigue(fatigue: dict[str, float], days: float, parameters: dict[str, Any]) -> dict[str, float]:
    return {
        component: float(value) * float(np.exp(-days / max(float(parameters["recovery"][component]["tau_days"]), EPS)))
        for component, value in fatigue.items()
    }


def apply_training_impulse(
    fatigue: dict[str, float],
    effective: dict[str, float] | np.ndarray,
    tref: dict[str, float] | pd.Series | np.ndarray,
    parameters: dict[str, Any],
) -> dict[str, float]:
    if not isinstance(effective, dict):
        effective_map = {component: float(np.asarray(effective)[idx]) for idx, component in enumerate(COMPONENTS)}
    else:
        effective_map = effective
    if isinstance(tref, (pd.Series, dict)):
        tref_map = {component: float(tref[component]) for component in COMPONENTS}
    else:
        tref_map = {component: float(np.asarray(tref)[idx]) for idx, component in enumerate(COMPONENTS)}

    updated: dict[str, float] = {}
    for component in COMPONENTS:
        rec = parameters["recovery"][component]
        impulse = 100.0 * float(rec["sensitivity"]) * float(effective_map.get(component, 0.0)) / max(tref_map[component], EPS)
        updated[component] = min(float(rec["fmax"]), float(fatigue.get(component, 0.0)) + impulse)
    return updated


def solve_direct_load(
    target_effective: dict[str, float] | pd.Series | np.ndarray,
    tref: dict[str, float] | pd.Series | np.ndarray,
    parameters: dict[str, Any],
) -> tuple[pd.Series, float]:
    """Намира неотрицателен Q, който апроксимира целевия E."""

    if isinstance(target_effective, (dict, pd.Series)):
        target = np.array([float(target_effective[c]) for c in COMPONENTS], dtype=float)
    else:
        target = np.asarray(target_effective, dtype=float)
    if isinstance(tref, (dict, pd.Series)):
        tref_vec = np.array([float(tref[c]) for c in COMPONENTS], dtype=float)
    else:
        tref_vec = np.asarray(tref, dtype=float)

    target = np.clip(target, 0.0, None)
    scale = np.maximum(tref_vec, 1.0)
    matrix = _cascade_matrix(parameters)
    try:
        initial = np.clip(np.linalg.lstsq(matrix, target, rcond=None)[0], 0.0, None)
    except np.linalg.LinAlgError:
        initial = np.clip(target / np.maximum(np.diag(matrix), 1.0), 0.0, None)

    def residual(q: np.ndarray) -> np.ndarray:
        return (effective_from_direct_vector(q, tref_vec, parameters) - target) / scale

    solution = least_squares(residual, initial, bounds=(0.0, np.inf), max_nfev=400, xtol=1e-9, ftol=1e-9, gtol=1e-9)
    q = np.clip(solution.x, 0.0, None)
    error = float(np.sqrt(np.mean(residual(q) ** 2)))
    return pd.Series(q, index=COMPONENTS, name="target_direct_q"), error


def target_weekly_effective(load_stats: pd.DataFrame, target_indices: dict[str, float]) -> pd.Series:
    """Преобразува целеви 7/40 индекси в седмични ефективни товари."""

    values = {}
    for component in COMPONENTS:
        row = load_stats.loc[component]
        target_daily = float(target_indices[component]) * (float(row["base_load"]) + float(row["E40_daily"])) - float(
            row["base_load"]
        )
        values[component] = 7.0 * max(0.0, target_daily)
    return pd.Series(values, name="target_effective_week")
