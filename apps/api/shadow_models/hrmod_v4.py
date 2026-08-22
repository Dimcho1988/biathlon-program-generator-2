"""Production adapter for the numerically unchanged HRmod v4 candidate.

Everything added here is observational.  Diagnostics are calculated after the
HR-only candidate and post-core terrain result already exist.
"""

from __future__ import annotations

from dataclasses import asdict
import math
from typing import Any, Sequence

from hrmod_lab.hrmod_core import compute_hrmod_hr_only
from hrmod_lab.schemas import (
    AthleteHRProfile,
    HRInputSample,
    HRmodConfig,
)
from hrmod_lab.tcx_adapter import ReferenceChannels
from hrmod_lab.terrain_gate import (
    TERRAIN_MODEL_VERSION,
    TerrainGateConfig,
    apply_terrain_gate,
)


EXTREME_DELTA_THRESHOLD_BPM = 20.0
SOURCE_COMMIT = "40b7d17a61c0bce0c3ffcf7b9381a6fe5645373f"


def _finite(value: float | None) -> float | None:
    if value is None:
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _reference_rows(reference_channels: ReferenceChannels, count: int):
    rows = list(reference_channels.samples)
    if len(rows) != count:
        raise ValueError("HRmod reference channels must align with HR samples")
    return rows


def run_hrmod_v4_shadow(
    *,
    hr_samples: Sequence[HRInputSample],
    athlete_profile: AthleteHRProfile,
    reference_channels: ReferenceChannels,
    config: HRmodConfig | None = None,
    terrain_config: TerrainGateConfig | None = None,
) -> dict[str, Any]:
    """Return stored-ready HRmod fields without changing the v4 candidate."""
    if athlete_profile.hrmax_bpm is None:
        return {
            "status": "excluded",
            "exclusion_reason": "EXPLICIT_HRMAX_MISSING",
            "experimental": True,
            "affects_canonical_load": False,
        }
    selected_config = config or HRmodConfig()
    core = compute_hrmod_hr_only(
        hr_samples=tuple(hr_samples),
        athlete_profile=athlete_profile,
        config=selected_config,
    )
    terrain = apply_terrain_gate(
        core,
        reference_channels,
        terrain_config or TerrainGateConfig(),
    )
    references = _reference_rows(reference_channels, len(core.timeseries))
    terrain_by_wave = {wave.wave_id: wave for wave in terrain.wave_summary}
    core_by_wave = {wave.wave_id: wave for wave in core.wave_summary}

    records: list[dict[str, Any]] = []
    added_values: list[float] = []
    removed_values: list[float] = []
    hrmax_seconds = 0.0
    valid_final_seconds = 0.0
    extreme_count = 0

    for point, final_point, reference in zip(
        core.timeseries, terrain.timeseries, references, strict=True
    ):
        clean = _finite(point.clean_hr_bpm)
        final = _finite(final_point.hrmod_final_bpm)
        candidate = _finite(point.hrmod_bpm)
        added = max(0.0, (final or 0.0) - (clean or 0.0)) if clean is not None and final is not None else 0.0
        removed = max(0.0, (clean or 0.0) - (final or 0.0)) if clean is not None and final is not None else 0.0
        delta = final - clean if clean is not None and final is not None else None
        model_flags = set(point.model_flags)
        if delta is not None and abs(delta) > EXTREME_DELTA_THRESHOLD_BPM:
            model_flags.add("HRMOD_EXTREME_DELTA")
            extreme_count += 1
        if final is not None:
            valid_final_seconds += point.dt_s
            if final >= athlete_profile.hrmax_bpm - 1e-9:
                hrmax_seconds += point.dt_s
        added_values.append(added)
        removed_values.append(removed)
        wave = core_by_wave.get(point.wave_id)
        exclusion_reason = None
        if point.raw_hr_bpm is None:
            exclusion_reason = "MISSING_HR"
        elif wave is not None and not wave.corrected:
            exclusion_reason = wave.skip_reason
        records.append(
            {
                "timestamp": point.timestamp.isoformat(),
                "elapsed_s": point.elapsed_s,
                "dt_s": point.dt_s,
                "speed_raw_kmh": (
                    _finite(reference.speed_mps) * 3.6
                    if _finite(reference.speed_mps) is not None
                    else None
                ),
                "hr_raw_bpm": _finite(point.raw_hr_bpm),
                "hr_clean_bpm": clean,
                "hrmod_candidate_bpm": candidate,
                "hrmod_final_bpm": final,
                "hrmod_delta_bpm": delta,
                "grade_raw_pct": _finite(reference.grade),
                "grade_smoothed_pct": _finite(final_point.smoothed_grade_pct),
                "receiver_flag": point.receiver_flag,
                "donor_flag": point.donor_flag,
                "added_bpm": added,
                "removed_bpm": removed,
                "wave_id": point.wave_id,
                "quality_flags": list(point.quality_flags),
                "model_flags": sorted(model_flags),
                "exclusion_reason": exclusion_reason,
                "hrmod_model_version": core.model_version,
                "hrmod_config_version": selected_config.config_version,
                "terrain_model_version": TERRAIN_MODEL_VERSION,
            }
        )

    waves: list[dict[str, Any]] = []
    receiver_overlap_total = 0.0
    receiver_duration_total = 0.0
    receiver_overlap_wave_count = 0
    for wave in core.wave_summary:
        receiver_points = [
            (core_point, terrain_point)
            for core_point, terrain_point in zip(
                core.timeseries, terrain.timeseries, strict=True
            )
            if core_point.wave_id == wave.wave_id and core_point.receiver_flag
        ]
        receiver_duration = sum(point.dt_s for point, _ in receiver_points)
        overlap = sum(
            point.dt_s
            for point, terrain_point in receiver_points
            if terrain_point.downhill_mask
        )
        flags = set(wave.flags)
        if overlap > 0.0:
            flags.add("RECEIVER_DOWNHILL_OVERLAP")
            receiver_overlap_wave_count += 1
        receiver_overlap_total += overlap
        receiver_duration_total += receiver_duration
        row = wave.to_dict()
        row.update(
            {
                "receiver_downhill_overlap_s": overlap,
                "receiver_downhill_overlap_fraction": (
                    overlap / receiver_duration if receiver_duration > 0.0 else 0.0
                ),
                "flags": sorted(flags),
                "terrain": (
                    terrain_by_wave[wave.wave_id].to_dict()
                    if wave.wave_id in terrain_by_wave
                    else None
                ),
            }
        )
        waves.append(row)

    diagnostics = core.diagnostics.to_dict()
    diagnostic_flags = set(diagnostics.get("flags", ()))
    if extreme_count:
        diagnostic_flags.add("HRMOD_EXTREME_DELTA")
    if receiver_overlap_total > 0.0:
        diagnostic_flags.add("RECEIVER_DOWNHILL_OVERLAP")
    diagnostics.update(
        {
            "flags": sorted(diagnostic_flags),
            "max_added_bpm": max(added_values, default=0.0),
            "max_removed_bpm": max(removed_values, default=0.0),
            "fraction_at_hrmax": (
                hrmax_seconds / valid_final_seconds if valid_final_seconds > 0.0 else 0.0
            ),
            "receiver_downhill_overlap_s": receiver_overlap_total,
            "receiver_downhill_overlap_fraction": (
                receiver_overlap_total / receiver_duration_total
                if receiver_duration_total > 0.0
                else 0.0
            ),
            "receiver_downhill_overlap_wave_count": receiver_overlap_wave_count,
            "extreme_delta_sample_count": extreme_count,
            "capacity_limited": diagnostics.get("capacity_limited_wave_count", 0) > 0,
            "moved_area_bpm_s": diagnostics.get("total_moved_area_bpm_s", 0.0),
            "wave_status": {
                "corrected": diagnostics.get("corrected_wave_count", 0),
                "skipped": diagnostics.get("skipped_wave_count", 0),
                "incomplete": diagnostics.get("incomplete_wave_count", 0),
            },
            "experimental": True,
            "affects_canonical_load": False,
        }
    )
    return {
        "status": "computed",
        "experimental": True,
        "affects_canonical_load": False,
        "model_version": core.model_version,
        "config_version": selected_config.config_version,
        "terrain_model_version": TERRAIN_MODEL_VERSION,
        "source_commit": SOURCE_COMMIT,
        "hr_input_hash": core.hr_input_hash,
        "terrain_input_hash": terrain.terrain_input_hash,
        "final_result_hash": terrain.final_result_hash,
        "timeseries": records,
        "waves": waves,
        "zones": [zone.to_dict() for zone in terrain.zone_summary],
        "diagnostics": diagnostics,
        "config": selected_config.to_dict(),
        "terrain_config": asdict(terrain.config),
    }
