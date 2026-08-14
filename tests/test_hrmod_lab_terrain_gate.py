from __future__ import annotations

from dataclasses import asdict, replace
from datetime import UTC, datetime, timedelta

import numpy as np
import pytest

from hrmod_lab import AthleteHRProfile, HRmodConfig, HRSample, HRZone
from hrmod_lab.hrmod_core import compute_hrmod_hr_only
from hrmod_lab.tcx_adapter import ReferenceChannels, ReferenceSample
from hrmod_lab.terrain_gate import (
    TerrainGateConfig,
    TerrainTimeseriesPoint,
    _terrain_zone_summaries,
    apply_terrain_gate,
    prepare_terrain,
)


START = datetime(2026, 1, 1, tzinfo=UTC)


def _profile() -> AthleteHRProfile:
    bounds = (50.0, 100.0, 120.0, 140.0, 160.0, 200.0)
    return AthleteHRProfile(
        hrmax_bpm=200.0,
        hr_floor_bpm=50.0,
        zones=tuple(
            HRZone(f"Z{index + 1}", bounds[index], bounds[index + 1])
            for index in range(5)
        ),
    )


def _triangle(*, offset: float = 0.0) -> list[float]:
    baseline = 100.0 + offset
    peak = 120.0 + offset
    rise = [baseline + (peak - baseline) * i / 20 for i in range(1, 21)]
    fall = [peak - (peak - baseline) * i / 20 for i in range(1, 21)]
    return [baseline] * 20 + rise + fall + [baseline] * 25


def _core(values: list[float] | None = None):
    values = values or _triangle()
    samples = tuple(
        HRSample(START + timedelta(seconds=index), value)
        for index, value in enumerate(values)
    )
    result = compute_hrmod_hr_only(
        hr_samples=samples,
        athlete_profile=_profile(),
        config=HRmodConfig(
            mirror_min_peak_fraction_hrmax=0.50,
            smoothing_window_s=5.0,
            smoothing_min_points=3,
            min_sustained_rise_s=3.0,
            min_sustained_fall_s=3.0,
            neutral_trough_timeout_s=7.0,
            baseline_lookback_s=15.0,
            baseline_min_points=3,
            return_sustain_s=3.0,
        ),
    )
    assert result.wave_summary
    return result


def _references(
    count: int,
    *,
    grades: list[float | None] | None = None,
    altitudes: list[float | None] | None = None,
    distances: list[float | None] | None = None,
) -> ReferenceChannels:
    grades = grades if grades is not None else [None] * count
    altitudes = altitudes if altitudes is not None else [None] * count
    distances = distances if distances is not None else [None] * count
    return ReferenceChannels(
        samples=tuple(
            ReferenceSample(
                timestamp=START + timedelta(seconds=index),
                elapsed_s=float(index),
                dt_s=0.0 if index == 0 else 1.0,
                grade=grades[index],
                altitude_m=altitudes[index],
                distance_m=distances[index],
            )
            for index in range(count)
        ),
        available_channels=tuple(
            name
            for name, values in (
                ("grade", grades),
                ("altitude_m", altitudes),
                ("distance_m", distances),
            )
            if any(value is not None for value in values)
        ),
    )


def _grade_for_range(result, start: float, end: float, value: float = -4.0):
    grades: list[float] = []
    for point in result.timeseries:
        grades.append(value if start <= point.elapsed_s <= end else 0.0)
    return grades


def _config(**overrides) -> TerrainGateConfig:
    values = {
        "grade_smoothing_window_s": 1.0,
    }
    values.update(overrides)
    return TerrainGateConfig(**values)


def _v4_core():
    values = _triangle(offset=50.0)
    samples = tuple(
        HRSample(START + timedelta(seconds=index), value)
        for index, value in enumerate(values)
    )
    return compute_hrmod_hr_only(
        hr_samples=samples,
        athlete_profile=_profile(),
        config=HRmodConfig(mirror_min_peak_fraction_hrmax=0.80),
    )


def test_v4_terrain_excludes_only_downhill_donor_and_reallocates_exactly() -> None:
    core = _v4_core()
    wave = next(wave for wave in core.wave_summary if wave.corrected)
    grades = _grade_for_range(
        core,
        wave.peak_elapsed_s + 2.0,
        wave.tail_end_elapsed_s - 2.0,
        -4.0,
    )
    terrain = apply_terrain_gate(
        core,
        _references(len(core.timeseries), grades=grades),
        TerrainGateConfig(
            grade_smoothing_window_s=1.0,
            downhill_threshold_pct=-3.0,
            min_sustained_downhill_s=3.0,
        ),
    )
    summary = next(item for item in terrain.wave_summary if item.wave_id == wave.wave_id)
    assert summary.terrain_status == "terrain_adjusted"
    assert 0.0 < summary.moved_area_final_bpm_s < summary.moved_area_candidate_bpm_s
    assert summary.downhill_donor_excluded_s > 0.0
    assert terrain.diagnostics.terrain_adjusted_wave_count == 1
    final_added = sum(point.hrmod_final_added_bpm * point.dt_s for point in terrain.timeseries)
    final_removed = sum(point.hrmod_final_removed_bpm * point.dt_s for point in terrain.timeseries)
    assert final_added == pytest.approx(final_removed, abs=1e-9)
    assert core.hr_input_hash == terrain.hr_input_hash


def test_v4_downhill_in_receiver_does_not_change_candidate() -> None:
    core = _v4_core()
    wave = next(wave for wave in core.wave_summary if wave.corrected)
    grades = _grade_for_range(
        core,
        wave.rise_start_elapsed_s,
        wave.peak_elapsed_s,
        -4.0,
    )
    terrain = apply_terrain_gate(
        core,
        _references(len(core.timeseries), grades=grades),
        TerrainGateConfig(
            grade_smoothing_window_s=1.0,
            downhill_threshold_pct=-3.0,
            min_sustained_downhill_s=3.0,
        ),
    )
    summary = next(item for item in terrain.wave_summary if item.wave_id == wave.wave_id)
    assert summary.terrain_status == "accepted"
    assert summary.moved_area_final_bpm_s == pytest.approx(
        summary.moved_area_candidate_bpm_s
    )
    assert [point.hrmod_final_bpm for point in terrain.timeseries] == [
        point.hrmod_bpm for point in core.timeseries
    ]


def test_flat_grade_accepts_candidate_wave_and_final_equals_candidate() -> None:
    core = _core()
    terrain = apply_terrain_gate(
        core, _references(len(core.timeseries), grades=[0.0] * len(core.timeseries)), _config()
    )

    assert all(wave.terrain_status == "accepted" for wave in terrain.wave_summary)
    assert [point.hrmod_final_bpm for point in terrain.timeseries] == [
        point.hrmod_bpm for point in core.timeseries
    ]
    assert terrain.diagnostics.terrain_rejected_wave_count == 0


def test_single_one_second_grade_spike_does_not_reject() -> None:
    core = _core()
    grades = [0.0] * len(core.timeseries)
    grades[int(core.wave_summary[0].peak_elapsed_s)] = -20.0
    terrain = apply_terrain_gate(
        core, _references(len(core.timeseries), grades=grades), _config()
    )

    assert terrain.wave_summary[0].terrain_status == "accepted"
    assert terrain.diagnostics.sustained_downhill_interval_count == 0


def test_downhill_threshold_is_lower_inclusive() -> None:
    core = _core()
    wave = core.wave_summary[0]
    grades = _grade_for_range(
        core, wave.peak_elapsed_s + 1.0, wave.peak_elapsed_s + 7.0, -3.0
    )
    terrain = apply_terrain_gate(
        core, _references(len(core.timeseries), grades=grades), _config()
    )

    assert terrain.wave_summary[0].terrain_status == "terrain_adjusted"
    assert any(point.downhill_mask for point in terrain.timeseries)


def test_missing_unreliable_grade_is_unavailable_without_fabricated_values() -> None:
    core = _core()
    terrain = apply_terrain_gate(core, _references(len(core.timeseries)), _config())

    assert terrain.diagnostics.terrain_gate_applied is False
    assert terrain.diagnostics.grade_source == "unavailable"
    assert "TERRAIN_GATE_UNAVAILABLE" in terrain.diagnostics.flags
    assert all(point.smoothed_grade_pct is None for point in terrain.timeseries)
    assert all(wave.terrain_status == "terrain_gate_unavailable" for wave in terrain.wave_summary)
    assert [point.hrmod_final_bpm for point in terrain.timeseries] == [
        point.hrmod_bpm for point in core.timeseries
    ]
    assert all(
        zone.hrmod_final_seconds == pytest.approx(zone.hrmod_candidate_seconds)
        and zone.hrmod_final_percent == pytest.approx(zone.hrmod_candidate_percent)
        and zone.final_minus_candidate_seconds == pytest.approx(0.0)
        for zone in terrain.zone_summary
    )


def test_accepted_wave_preserves_area_balance_and_candidate_area() -> None:
    core = _core()
    terrain = apply_terrain_gate(
        core, _references(len(core.timeseries), grades=[0.0] * len(core.timeseries)), _config()
    )
    candidate = core.wave_summary[0]
    gated = terrain.wave_summary[0]

    assert candidate.added_area_bpm_s == pytest.approx(candidate.removed_area_bpm_s)
    assert gated.moved_area_final_bpm_s == candidate.moved_area_bpm_s
    assert gated.moved_area_candidate_bpm_s == candidate.moved_area_bpm_s


def test_grade_changes_only_terrain_final_and_hash_not_core_candidate_or_hr_hash() -> None:
    core = _core()
    snapshot = asdict(core)
    wave = core.wave_summary[0]
    flat = apply_terrain_gate(
        core, _references(len(core.timeseries), grades=[0.0] * len(core.timeseries)), _config()
    )
    downhill = apply_terrain_gate(
        core,
        _references(
            len(core.timeseries),
            grades=_grade_for_range(
                core, wave.rise_start_elapsed_s, wave.tail_end_elapsed_s
            ),
        ),
        _config(),
    )

    assert asdict(core) == snapshot
    assert flat.hr_input_hash == downhill.hr_input_hash == core.hr_input_hash
    assert flat.terrain_input_hash != downhill.terrain_input_hash
    assert flat.final_result_hash != downhill.final_result_hash
    assert [p.hrmod_candidate_bpm for p in flat.timeseries] == [
        p.hrmod_candidate_bpm for p in downhill.timeseries
    ]
    assert [p.hrmod_final_bpm for p in flat.timeseries] != [
        p.hrmod_final_bpm for p in downhill.timeseries
    ]


@pytest.mark.parametrize(
    ("altitude_step", "expected_grade"),
    ((0.0, 0.0), (1.0, 10.0), (-1.0, -10.0)),
)
def test_altitude_distance_derivation_has_expected_sign_and_slope(
    altitude_step: float, expected_grade: float
) -> None:
    count = 30
    prepared = prepare_terrain(
        _references(
            count,
            altitudes=[100.0 + altitude_step * index for index in range(count)],
            distances=[10.0 * index for index in range(count)],
        ),
        _config(),
    )

    finite = np.asarray(
        [value for value in prepared.smoothed_grade_pct if value is not None]
    )
    assert prepared.available
    assert prepared.grade_source == "derived_altitude_distance"
    assert float(np.median(finite[5:-5])) == pytest.approx(expected_grade, abs=0.25)


def test_prepared_terrain_is_reusable_and_gate_disabled_keeps_candidate() -> None:
    core = _core()
    config = _config(terrain_gate_enabled=False)
    prepared = prepare_terrain(
        _references(len(core.timeseries), grades=[-10.0] * len(core.timeseries)),
        config,
    )
    first = apply_terrain_gate(core, prepared_terrain=prepared)
    second = apply_terrain_gate(core, prepared_terrain=prepared)

    assert first == second
    assert first.diagnostics.terrain_gate_applied is False
    assert all(wave.terrain_status == "terrain_gate_disabled" for wave in first.wave_summary)
    assert [point.hrmod_final_bpm for point in first.timeseries] == [
        point.hrmod_bpm for point in core.timeseries
    ]
    assert first.zones == first.zone_summary
    assert all(
        zone.hrmod_final_seconds == pytest.approx(zone.hrmod_candidate_seconds)
        and zone.hrmod_final_percent == pytest.approx(zone.hrmod_candidate_percent)
        and zone.final_minus_candidate_seconds == pytest.approx(0.0)
        for zone in first.zone_summary
    )
    assert [zone.hrmod_candidate_seconds for zone in first.zone_summary] == pytest.approx(
        [zone.hrmod_seconds for zone in core.zone_summary]
    )
    assert [zone.raw_seconds for zone in first.zone_summary] == pytest.approx(
        [zone.raw_seconds for zone in core.zone_summary]
    )


def test_adjusted_wave_recomputes_final_zones_from_terrain_final_signal() -> None:
    core = _core()
    wave = core.wave_summary[0]
    terrain = apply_terrain_gate(
        core,
        _references(
            len(core.timeseries),
            grades=_grade_for_range(
                core, wave.rise_start_elapsed_s, wave.tail_end_elapsed_s
            ),
        ),
        _config(),
    )

    assert terrain.wave_summary[0].terrain_status == "terrain_adjusted"
    assert any(
        zone.hrmod_final_seconds != pytest.approx(zone.hrmod_candidate_seconds)
        for zone in terrain.zone_summary
    )
    assert [zone.hrmod_candidate_seconds for zone in terrain.zone_summary] == pytest.approx(
        [zone.hrmod_seconds for zone in core.zone_summary]
    )
    assert [zone.hrmod_final_seconds for zone in terrain.zone_summary] == pytest.approx(
        [zone.raw_seconds for zone in core.zone_summary]
    )


def test_terrain_zone_seconds_and_percentages_use_unrounded_sample_durations() -> None:
    core = _core()
    terrain = apply_terrain_gate(
        core,
        _references(len(core.timeseries), grades=[0.0] * len(core.timeseries)),
        _config(),
    )
    valid_duration = sum(
        point.dt_s for point in terrain.timeseries if point.hrmod_final_bpm is not None
    )

    assert sum(zone.hrmod_final_seconds for zone in terrain.zone_summary) == pytest.approx(
        valid_duration
    )
    assert sum(zone.hrmod_candidate_seconds for zone in terrain.zone_summary) == pytest.approx(
        valid_duration
    )
    assert sum(zone.hrmod_final_percent for zone in terrain.zone_summary) == pytest.approx(
        100.0
    )
    assert sum(zone.hrmod_candidate_percent for zone in terrain.zone_summary) == pytest.approx(
        100.0
    )
    for zone in terrain.zone_summary:
        assert zone.hrmod_final_seconds - zone.hrmod_candidate_seconds == pytest.approx(
            zone.final_minus_candidate_seconds
        )
        assert zone.hrmod_final_seconds - zone.raw_seconds == pytest.approx(
            zone.final_minus_raw_seconds
        )


def test_terrain_zones_use_lower_inclusive_final_upper_inclusive_and_irregular_dt() -> None:
    core = _core()
    values = (
        99.999,
        100.0,
        119.999,
        120.0,
        120.001,
        139.999,
        140.0,
        159.999,
        160.0,
        200.0,
    )
    durations = (0.5, 1.25, 2.0, 3.5, 4.75, 6.0, 7.25, 8.5, 9.75, 11.0)
    points = tuple(
        TerrainTimeseriesPoint(
            timestamp=START + timedelta(seconds=index),
            elapsed_s=float(index),
            dt_s=durations[index],
            raw_hr_bpm=value,
            hrmod_candidate_bpm=value,
            hrmod_final_bpm=value,
            smoothed_grade_pct=0.0,
            downhill_mask=False,
            buffered_downhill_mask=False,
            terrain_status="accepted",
            wave_id=None,
        )
        for index, value in enumerate(values)
    )

    zones = _terrain_zone_summaries(core, points)

    assert [zone.hrmod_final_seconds for zone in zones] == pytest.approx(
        [0.5, 3.25, 14.25, 15.75, 20.75]
    )
    assert sum(zone.hrmod_final_seconds for zone in zones) == pytest.approx(
        sum(durations)
    )


def test_zone_definitions_are_included_in_final_result_hash() -> None:
    core = _core()
    bounds = (50.0, 90.0, 110.0, 130.0, 150.0, 200.0)
    alternate_core = replace(
        core,
        zone_summary=tuple(
            replace(zone, lower_bpm=bounds[index], upper_bpm=bounds[index + 1])
            for index, zone in enumerate(core.zone_summary)
        ),
    )
    references = _references(
        len(core.timeseries), grades=[0.0] * len(core.timeseries)
    )
    original = apply_terrain_gate(
        core,
        references,
        _config(),
    )
    alternate = apply_terrain_gate(
        alternate_core,
        references,
        _config(),
    )

    assert original.hr_input_hash == alternate.hr_input_hash == core.hr_input_hash
    assert [point.hrmod_final_bpm for point in original.timeseries] == [
        point.hrmod_final_bpm for point in alternate.timeseries
    ]
    assert original.zone_summary != alternate.zone_summary
    assert original.final_result_hash != alternate.final_result_hash


def test_core_model_and_config_are_included_in_final_result_hash() -> None:
    core = _core()
    references = _references(
        len(core.timeseries), grades=[0.0] * len(core.timeseries)
    )
    original = apply_terrain_gate(core, references, _config())
    alternate_model = apply_terrain_gate(
        replace(core, model_version="alternate_core_model"),
        references,
        _config(),
    )
    alternate_config = apply_terrain_gate(
        replace(
            core,
            config=replace(
                core.config,
                sampling_regularity_tolerance_s=(
                    core.config.sampling_regularity_tolerance_s + 0.01
                ),
            ),
        ),
        references,
        _config(),
    )

    for alternate in (alternate_model, alternate_config):
        assert alternate.hr_input_hash == original.hr_input_hash
        assert [point.hrmod_final_bpm for point in alternate.timeseries] == [
            point.hrmod_final_bpm for point in original.timeseries
        ]
        assert alternate.wave_summary == original.wave_summary
        assert alternate.zone_summary == original.zone_summary
        assert alternate.final_result_hash != original.final_result_hash


def test_large_sampling_gap_breaks_downhill_continuity() -> None:
    elapsed = (0.0, 1.0, 100.0, 101.0)
    references = ReferenceChannels(
        samples=tuple(
            ReferenceSample(
                timestamp=START + timedelta(seconds=value),
                elapsed_s=value,
                dt_s=0.0 if index == 0 else value - elapsed[index - 1],
                grade=-4.0,
            )
            for index, value in enumerate(elapsed)
        ),
        available_channels=("grade",),
    )

    prepared = prepare_terrain(references, _config(max_terrain_sample_gap_s=5.0))

    assert prepared.available
    assert prepared.downhill_intervals == ()
    assert not any(prepared.downhill_mask)


def test_terrain_hash_includes_elapsed_time_used_by_sustained_detection() -> None:
    def references(elapsed: tuple[float, ...]) -> ReferenceChannels:
        return ReferenceChannels(
            samples=tuple(
                ReferenceSample(
                    timestamp=START + timedelta(seconds=index),
                    elapsed_s=value,
                    dt_s=0.0 if index == 0 else value - elapsed[index - 1],
                    grade=-4.0,
                )
                for index, value in enumerate(elapsed)
            ),
            available_channels=("grade",),
        )

    first = prepare_terrain(references((0, 1, 2, 3, 4, 5)), _config())
    second = prepare_terrain(references((0, 0.5, 1, 1.5, 2, 2.5)), _config())

    assert first.terrain_input_hash != second.terrain_input_hash
    assert bool(first.downhill_intervals) is True
    assert bool(second.downhill_intervals) is False


def test_public_terrain_result_is_serialisable() -> None:
    core = _core()
    terrain = apply_terrain_gate(
        core,
        _references(len(core.timeseries), grades=[0.0] * len(core.timeseries)),
        _config(),
    )

    payload = terrain.to_dict()
    assert payload["diagnostics"]["status_counts"] == {"accepted": 1}
    assert payload["terrain_input_hash"] == terrain.terrain_input_hash
    assert payload["zone_summary"] == [zone.to_dict() for zone in terrain.zone_summary]
    assert set(payload["zone_summary"][0]) == {
        "zone_name",
        "lower_bpm",
        "upper_bpm",
        "raw_seconds",
        "raw_percent",
        "hrmod_candidate_seconds",
        "hrmod_candidate_percent",
        "hrmod_final_seconds",
        "hrmod_final_percent",
        "final_minus_candidate_seconds",
        "final_minus_raw_seconds",
    }
