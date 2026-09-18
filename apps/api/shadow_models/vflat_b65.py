"""Production adapter for the versioned Vflat B65 shadow model."""

from __future__ import annotations

from typing import Any, Mapping

import pandas as pd

from vflat_b65 import (
    CONFIG_VERSION,
    MODEL_VERSION,
    SPRINT_STR_CONFIG_VERSION,
    SPRINT_STR_MODEL_VERSION,
    SprintSTRConfig,
    VFlatB65Config,
    apply_vflat_b65,
    detect_sprint_str,
)


def run_vflat_b65_shadow(
    prepared_timeseries: pd.DataFrame,
    *,
    config: VFlatB65Config | None = None,
    sprint_config: SprintSTRConfig | None = None,
    activity_detail: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    selected = config or VFlatB65Config()
    result = apply_vflat_b65(prepared_timeseries, selected, activity_detail=activity_detail)
    sprint_detection = detect_sprint_str(result, sprint_config)
    rows = []
    previous_timestamp = previous_block = None
    for position, row in enumerate(result.to_dict(orient="records")):
        timestamp = row.get("timestamp")
        block = row.get("block")
        dt_s = (
            float((timestamp - previous_timestamp).total_seconds())
            if previous_timestamp is not None and block == previous_block and block != -1
            else 0.0
        )
        previous_timestamp, previous_block = timestamp, block
        raw_speed_mps = row.get("speed_raw_mps")
        raw_speed_kmh = (
            float(raw_speed_mps) * 3.6
            if raw_speed_mps is not None and pd.notna(raw_speed_mps)
            else None
        )
        rows.append(
            {
                "timestamp": (
                    row.get("timestamp").isoformat()
                    if hasattr(row.get("timestamp"), "isoformat")
                    else str(row.get("timestamp"))
                ),
                "speed_raw_kmh": raw_speed_kmh,
                "dt_s": dt_s if 0 < dt_s <= 1.0 else 0.0,
                "vflat_b65_kmh": row.get("vflat_b65_kmh"),
                "vflat_before_terrain_kmh": row.get("vflat_before_terrain_kmh"),
                "vflat_delta_kmh": (
                    row.get("vflat_b65_kmh") - raw_speed_kmh
                    if raw_speed_kmh is not None and pd.notna(row.get("vflat_b65_kmh"))
                    else None
                ),
                "grade_raw_pct": row.get("grade_actual_pct"),
                "grade_smoothed_pct": row.get("grade_pct"),
                "grade_effective_pct": row.get("grade_effective_pct"),
                "grade_stationary_pct": row.get("grade_stationary_pct"),
                "sprint_str_flag": sprint_detection.sample_mask[position],
                "sprint_str_reference_kmh": (
                    sprint_detection.local_reference_kmh[position]
                ),
                "sprint_str_rise_kmh": sprint_detection.speed_rise_kmh[position],
                "quality_flags": list(row.get("quality_flags") or ()),
                "exclusion_reason": None if row.get("valid") else "VFLAT_SAMPLE_EXCLUDED",
                "vflat_model_version": MODEL_VERSION,
                "vflat_config_version": CONFIG_VERSION,
            }
        )
    return {
        "status": "computed",
        "experimental": True,
        "affects_canonical_load": False,
        "model_version": MODEL_VERSION,
        "config_version": CONFIG_VERSION,
        "sprint_str_model_version": SPRINT_STR_MODEL_VERSION,
        "sprint_str_config_version": SPRINT_STR_CONFIG_VERSION,
        "config": selected.to_dict(),
        "terrain_correction": result.attrs["terrain_correction"],
        "sprint_str_summary": sprint_detection.summary,
        "sprint_str_intervals": list(sprint_detection.intervals),
        "timeseries": rows,
    }
