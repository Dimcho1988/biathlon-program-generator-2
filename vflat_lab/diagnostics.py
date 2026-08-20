"""Segment aggregation and objective diagnostics for expert calibration."""

from __future__ import annotations

import numpy as np
import pandas as pd

from .core import VFlatConfig


def _safe_corr(first: pd.Series, second: pd.Series) -> float:
    frame = pd.concat([first, second], axis=1).dropna()
    if len(frame) < 5 or frame.iloc[:, 0].std() == 0 or frame.iloc[:, 1].std() == 0:
        return np.nan
    return float(frame.iloc[:, 0].corr(frame.iloc[:, 1]))


def segment_timeseries(timeseries: pd.DataFrame, config: VFlatConfig) -> pd.DataFrame:
    """Aggregate fixed elapsed-time segments and preserve coverage explicitly."""

    data = timeseries.copy()
    data["segment_in_block"] = (data.sec_in_block // config.segment_s).astype(int)
    rows: list[dict[str, object]] = []
    for (block, segment), part in data.groupby(["block", "segment_in_block"], sort=True):
        valid = part[part.valid]
        total_s = len(part)
        valid_s = len(valid)
        coverage = valid_s / max(total_s, 1)
        row: dict[str, object] = {
            "filename": part.filename.iloc[0],
            "block": int(block),
            "lap": int(round(float(part.lap.median()))) if "lap" in part else 0,
            "segment_in_block": int(segment),
            "elapsed_min": float(part.elapsed_s.mean() / 60.0),
            "start_time": part.time.iloc[0],
            "total_s": int(total_s),
            "valid_s": int(valid_s),
            "coverage": float(coverage),
            "segment_valid": bool(coverage >= config.min_segment_coverage and valid_s >= 3),
        }
        source = valid if valid_s else part
        for column in (
            "grade_pct",
            "speed_kmh",
            "hr_bpm",
            "accel_mps2",
            "vflat_stationary_kmh",
            "vflat_empirical_raw_kmh",
            "vflat_anchored_kmh",
            "vflat_final_kmh",
            "acceleration_term_kmh",
            "deceleration_term_kmh",
            "descent_memory_term_kmh",
            "climb_memory_term_kmh",
            "transition_weight",
        ):
            row[column] = float(source[column].median()) if column in source and source[column].notna().any() else np.nan
        rows.append(row)
    return pd.DataFrame(rows)


def recommended_work_laps(timeseries: pd.DataFrame) -> list[int]:
    """Suggest repeated work laps without silently discarding a continuous file.

    This is a UI convenience, not a model feature.  Repeated roller-ski work
    laps in the calibration files are typically 3–15 minutes and faster than
    recovery laps.  If the pattern is not clear, every lap remains selected.
    """

    if "lap" not in timeseries or timeseries.empty:
        return []
    stats = timeseries.groupby("lap", as_index=False).agg(
        duration_s=("time", "size"),
        median_speed_kmh=("speed_kmh", "median"),
        median_hr_bpm=("hr_bpm", "median"),
    )
    candidates = stats[
        stats.duration_s.between(180, 900)
        & (stats.median_speed_kmh >= 10.0)
    ].copy()
    if len(candidates) >= 2 and candidates.median_hr_bpm.notna().sum() >= 2:
        work_hr_floor = float(candidates.median_hr_bpm.median()) - 8.0
        candidates = candidates[
            candidates.median_hr_bpm.isna()
            | (candidates.median_hr_bpm >= work_hr_floor)
        ]
    if len(candidates) >= 2:
        return candidates.lap.astype(int).tolist()
    all_laps = stats.lap.astype(int).tolist()
    return candidates.lap.astype(int).tolist() if len(candidates) == 1 and len(all_laps) > 1 else all_laps


def activity_summary(segments: pd.DataFrame) -> dict[str, float | int | str]:
    """Compute diagnostics for stationary and final Vflat on accepted segments."""

    valid = segments[segments.segment_valid].copy()
    if valid.empty:
        return {
            "filename": str(segments.filename.iloc[0]) if not segments.empty else "",
            "segments": 0,
            "valid_seconds": 0,
            "mean_segment_coverage_pct": np.nan,
            "stationary_median_kmh": np.nan,
            "stationary_p05_kmh": np.nan,
            "stationary_p95_kmh": np.nan,
            "stationary_central90_width_kmh": np.nan,
            "stationary_cv_pct": np.nan,
            "final_median_kmh": np.nan,
            "final_p05_kmh": np.nan,
            "final_p95_kmh": np.nan,
            "final_central90_width_kmh": np.nan,
            "final_cv_pct": np.nan,
            "final_grade_corr": np.nan,
            "final_accel_corr": np.nan,
            "median_hr_bpm": np.nan,
            "central90_width_kmh": np.nan,
            "target_5kmh_met": "Не",
        }

    stationary = valid.vflat_stationary_kmh.dropna()
    final = valid.vflat_final_kmh.dropna()

    def metrics(values: pd.Series, prefix: str) -> dict[str, float]:
        median = float(values.median())
        p05 = float(values.quantile(0.05))
        p95 = float(values.quantile(0.95))
        return {
            f"{prefix}_median_kmh": median,
            f"{prefix}_p05_kmh": p05,
            f"{prefix}_p95_kmh": p95,
            f"{prefix}_central90_width_kmh": p95 - p05,
            f"{prefix}_cv_pct": float(values.std(ddof=1) / median * 100.0) if median > 0 and len(values) > 1 else np.nan,
        }

    result: dict[str, float | int | str] = {
        "filename": str(valid.filename.iloc[0]),
        "segments": int(len(valid)),
        "valid_seconds": int(valid.valid_s.sum()),
        "mean_segment_coverage_pct": float(valid.coverage.mean() * 100.0),
        **metrics(stationary, "stationary"),
        **metrics(final, "final"),
        "final_grade_corr": _safe_corr(valid.vflat_final_kmh, valid.grade_pct),
        "final_accel_corr": _safe_corr(valid.vflat_final_kmh, valid.accel_mps2),
        "median_hr_bpm": float(valid.hr_bpm.median()) if valid.hr_bpm.notna().any() else np.nan,
    }
    result["central90_width_kmh"] = result["final_central90_width_kmh"]
    result["target_5kmh_met"] = "Да" if float(result["final_central90_width_kmh"]) <= 5.0 else "Не"
    return result
