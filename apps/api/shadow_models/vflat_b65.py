"""Production adapter for the versioned Vflat B65 shadow model."""

from __future__ import annotations

from typing import Any

import pandas as pd

from vflat_b65 import CONFIG_VERSION, MODEL_VERSION, VFlatB65Config, apply_vflat_b65


def run_vflat_b65_shadow(
    prepared_timeseries: pd.DataFrame,
    *,
    config: VFlatB65Config | None = None,
) -> dict[str, Any]:
    selected = config or VFlatB65Config()
    result = apply_vflat_b65(prepared_timeseries, selected)
    rows = []
    for row in result.to_dict(orient="records"):
        raw_speed_mps = row.get("speed_raw_mps")
        raw_speed_kmh = (
            float(raw_speed_mps) * 3.6
            if raw_speed_mps is not None and pd.notna(raw_speed_mps)
            else None
        )
        quality_flags = set(row.get("quality_flags") or ())
        inertia_extrapolated = bool(row.get("inertia_extrapolated"))
        if inertia_extrapolated:
            quality_flags.add("VFLAT_INERTIA_EXTRAPOLATED")
        inertia_reason = None
        if bool(row.get("steep_descent_inertia")):
            inertia_reason = "STEEP_DESCENT_OR_15S_MARGIN"
        elif bool(row.get("mild_descent_inertia")):
            inertia_reason = "ACCELERATING_MILD_DESCENT_OR_MARGIN"
        rows.append(
            {
                "timestamp": (
                    row.get("timestamp").isoformat()
                    if hasattr(row.get("timestamp"), "isoformat")
                    else str(row.get("timestamp"))
                ),
                "speed_raw_kmh": raw_speed_kmh,
                "vflat_b65_kmh": row.get("vflat_b65_kmh"),
                "vflat_delta_kmh": (
                    row.get("vflat_b65_kmh") - raw_speed_kmh
                    if raw_speed_kmh is not None and pd.notna(row.get("vflat_b65_kmh"))
                    else None
                ),
                "grade_raw_pct": row.get("grade_actual_pct"),
                "grade_smoothed_pct": row.get("grade_pct"),
                "quality_flags": sorted(quality_flags),
                "exclusion_reason": None if row.get("valid") else "VFLAT_SAMPLE_EXCLUDED",
                "inertia_extrapolated": inertia_extrapolated,
                "inertia_reason": inertia_reason,
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
        "config": selected.to_dict(),
        "diagnostics": {
            "inertia_extrapolated_sample_count": int(
                result["inertia_extrapolated"].sum()
            ),
            "steep_descent_inertia_sample_count": int(
                result["steep_descent_inertia"].sum()
            ),
            "mild_descent_inertia_sample_count": int(
                result["mild_descent_inertia"].sum()
            ),
        },
        "timeseries": rows,
    }
