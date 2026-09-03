from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from apps.api.shadow_models.hrmod_v4 import run_hrmod_v4_shadow
from hrmod_lab.hrmod_core import compute_hrmod_hr_only
from hrmod_lab.schemas import AthleteHRProfile, HRmodConfig, HRSample, HRZone
from hrmod_lab.tcx_adapter import ReferenceChannels, ReferenceSample
from hrmod_lab.terrain_gate import TerrainGateConfig


START = datetime(2026, 1, 1, tzinfo=UTC)


def _profile(*, hrmax: float = 200.0, z5_upper: float = 200.0):
    bounds = (50.0, 100.0, 120.0, 140.0, 160.0, z5_upper)
    return AthleteHRProfile(
        hrmax_bpm=hrmax,
        hr_floor_bpm=50.0,
        zones=tuple(
            HRZone(f"Z{index + 1}", bounds[index], bounds[index + 1])
            for index in range(5)
        ),
    )


def _values(peak: float = 190.0):
    baseline = 120.0
    rise = [baseline + (peak - baseline) * index / 25 for index in range(1, 26)]
    fall = [peak - (peak - baseline) * index / 35 for index in range(1, 36)]
    return [baseline] * 20 + rise + fall + [baseline] * 25


def _inputs(grades=None):
    values = _values()
    samples = tuple(
        HRSample(START + timedelta(seconds=index), value)
        for index, value in enumerate(values)
    )
    grades = grades if grades is not None else [0.0] * len(values)
    references = ReferenceChannels(
        samples=tuple(
            ReferenceSample(
                timestamp=START + timedelta(seconds=index),
                elapsed_s=float(index),
                dt_s=0.0 if index == 0 else 1.0,
                speed_mps=5.0,
                grade=grades[index],
            )
            for index in range(len(values))
        ),
        available_channels=("speed_mps", "grade"),
    )
    return samples, references


def test_production_adapter_preserves_streamlit_v4_candidate_and_raw_hr() -> None:
    samples, references = _inputs()
    profile = _profile()
    config = HRmodConfig()
    golden = compute_hrmod_hr_only(
        hr_samples=samples, athlete_profile=profile, config=config
    )
    result = run_hrmod_v4_shadow(
        hr_samples=samples,
        athlete_profile=profile,
        reference_channels=references,
        config=config,
        terrain_config=TerrainGateConfig(grade_smoothing_window_s=1.0),
    )
    assert [row["hrmod_candidate_bpm"] for row in result["timeseries"]] == pytest.approx(
        [point.hrmod_bpm for point in golden.timeseries]
    )
    assert [row["hr_raw_bpm"] for row in result["timeseries"]] == pytest.approx(
        [sample.heart_rate_bpm for sample in samples]
    )
    assert result["model_version"] == "hrmod_mirror_area_shift_v6"
    assert result["config_version"] == "hrmod_config_v6"
    assert result["source_commit"] == "35df9b2a8a38779039c4dcf65bcdf117f24966ae"
    assert result["affects_canonical_load"] is False


def test_zone_payload_combines_core_clean_candidate_and_terrain_final() -> None:
    samples, references = _inputs()
    profile = _profile()
    config = HRmodConfig()
    golden = compute_hrmod_hr_only(
        hr_samples=samples, athlete_profile=profile, config=config
    )
    result = run_hrmod_v4_shadow(
        hr_samples=samples,
        athlete_profile=profile,
        reference_channels=references,
        config=config,
        terrain_config=TerrainGateConfig(grade_smoothing_window_s=1.0),
    )

    golden_by_zone = {zone.zone_name: zone for zone in golden.zone_summary}
    assert len(result["zones"]) == 5
    for zone in result["zones"]:
        golden_zone = golden_by_zone[zone["zone_name"]]
        assert zone["raw_seconds"] == pytest.approx(golden_zone.raw_seconds)
        assert zone["clean_seconds"] == pytest.approx(golden_zone.clean_seconds)
        assert zone["hrmod_candidate_seconds"] == pytest.approx(
            golden_zone.hrmod_seconds
        )
        assert zone["candidate_minus_clean_seconds"] == pytest.approx(
            golden_zone.hrmod_minus_clean_seconds
        )
        assert zone["hrmod_final_seconds"] >= 0.0
        assert zone["final_minus_clean_seconds"] == pytest.approx(
            zone["hrmod_final_seconds"] - zone["clean_seconds"]
        )


def test_diagnostics_do_not_change_candidate_and_area_is_exact() -> None:
    samples, references = _inputs()
    result = run_hrmod_v4_shadow(
        hr_samples=samples,
        athlete_profile=_profile(),
        reference_channels=references,
        terrain_config=TerrainGateConfig(grade_smoothing_window_s=1.0),
    )
    assert result["diagnostics"]["area_conservation_passed"] is True
    for wave in result["waves"]:
        if wave["corrected"]:
            assert wave["added_area_bpm_s"] == pytest.approx(
                wave["removed_area_bpm_s"], abs=1e-9
            )
    assert result["diagnostics"]["max_added_bpm"] >= 0.0
    assert result["diagnostics"]["max_removed_bpm"] >= 0.0
    assert 0.0 <= result["diagnostics"]["fraction_at_hrmax"] <= 1.0


def test_receiver_downhill_overlap_is_warning_only() -> None:
    samples, flat_references = _inputs()
    profile = _profile()
    config = HRmodConfig()
    core = compute_hrmod_hr_only(
        hr_samples=samples, athlete_profile=profile, config=config
    )
    corrected = next(wave for wave in core.wave_summary if wave.corrected)
    grades = [
        -4.0 if corrected.rise_start_elapsed_s <= point.elapsed_s <= corrected.peak_elapsed_s else 0.0
        for point in core.timeseries
    ]
    _, downhill_references = _inputs(grades)
    flat = run_hrmod_v4_shadow(
        hr_samples=samples,
        athlete_profile=profile,
        reference_channels=flat_references,
        config=config,
        terrain_config=TerrainGateConfig(grade_smoothing_window_s=1.0),
    )
    downhill = run_hrmod_v4_shadow(
        hr_samples=samples,
        athlete_profile=profile,
        reference_channels=downhill_references,
        config=config,
        terrain_config=TerrainGateConfig(
            grade_smoothing_window_s=1.0,
            min_sustained_downhill_s=3.0,
        ),
    )
    assert [row["hrmod_candidate_bpm"] for row in downhill["timeseries"]] == pytest.approx(
        [row["hrmod_candidate_bpm"] for row in flat["timeseries"]]
    )
    assert [row["hrmod_final_bpm"] for row in downhill["timeseries"]] == pytest.approx(
        [row["hrmod_final_bpm"] for row in flat["timeseries"]]
    )
    assert "RECEIVER_DOWNHILL_OVERLAP" in downhill["diagnostics"]["flags"]
    assert downhill["diagnostics"]["receiver_downhill_overlap_s"] > 0.0


def test_terrain_uses_explicit_hrmax_not_z5_upper() -> None:
    samples, references = _inputs()
    result = run_hrmod_v4_shadow(
        hr_samples=samples,
        athlete_profile=_profile(hrmax=200.0, z5_upper=170.0),
        reference_channels=references,
        terrain_config=TerrainGateConfig(grade_smoothing_window_s=1.0),
    )
    maximum = max(row["hrmod_final_bpm"] or 0.0 for row in result["timeseries"])
    assert maximum <= 200.0
    assert maximum > 170.0


def test_default_production_guardrails_prevent_extreme_delta() -> None:
    samples, references = _inputs()
    profile = _profile()
    core = compute_hrmod_hr_only(
        hr_samples=samples, athlete_profile=profile, config=HRmodConfig()
    )
    result = run_hrmod_v4_shadow(
        hr_samples=samples,
        athlete_profile=profile,
        reference_channels=references,
    )
    assert [row["hrmod_candidate_bpm"] for row in result["timeseries"]] == pytest.approx(
        [point.hrmod_bpm for point in core.timeseries]
    )
    flagged = [
        row for row in result["timeseries"]
        if row["hrmod_delta_bpm"] is not None
        and abs(row["hrmod_delta_bpm"]) > 20.0 + 1e-9
    ]
    assert flagged == []
    assert result["diagnostics"]["max_added_bpm"] <= 20.0 + 1e-9
    assert result["diagnostics"]["max_removed_bpm"] <= 20.0 + 1e-9
    assert result["diagnostics"]["extreme_delta_sample_count"] == 0
