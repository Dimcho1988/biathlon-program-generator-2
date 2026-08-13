from __future__ import annotations

from dataclasses import fields, replace
from datetime import UTC, datetime, timedelta
import inspect
import math

import numpy as np
import pytest

from hrmod_lab.hrmod_core import compute_hrmod_hr_only
from hrmod_lab.schemas import (
    CONFIG_VERSION,
    LEGACY_MODEL_VERSION,
    MODEL_VERSION,
    AthleteHRProfile,
    HRmodConfig,
    HRSample,
    HRZone,
)
from hrmod_lab.wave_area_shift import shift_wave_areas
from hrmod_lab.wave_detection import DetectedWave


START = datetime(2026, 1, 1, tzinfo=UTC)


def _profile(
    *,
    floor: float = 50.0,
    hrmax: float = 200.0,
    boundaries: tuple[float, ...] = (50.0, 100.0, 120.0, 140.0, 160.0, 200.0),
) -> AthleteHRProfile:
    assert boundaries[0] == floor
    assert boundaries[-1] == hrmax
    return AthleteHRProfile(
        hrmax_bpm=hrmax,
        hr_floor_bpm=floor,
        zones=tuple(
            HRZone(f"Z{index + 1}", boundaries[index], boundaries[index + 1])
            for index in range(5)
        ),
    )


def _samples(
    values: list[float | None], times_s: list[float] | None = None
) -> tuple[HRSample, ...]:
    if times_s is None:
        times_s = [float(index) for index in range(len(values))]
    return tuple(
        HRSample(START + timedelta(seconds=elapsed), value)
        for elapsed, value in zip(times_s, values, strict=True)
    )


def _triangle(
    *,
    baseline: float = 100.0,
    peak: float = 120.0,
    pre: int = 20,
    rise_seconds: int = 20,
    fall_seconds: int = 20,
    post: int = 25,
) -> list[float]:
    rise = [
        baseline + (peak - baseline) * index / rise_seconds
        for index in range(1, rise_seconds + 1)
    ]
    fall = [
        peak - (peak - baseline) * index / fall_seconds
        for index in range(1, fall_seconds + 1)
    ]
    return [baseline] * pre + rise + fall + [baseline] * post


def _sustained_wave(
    *, hold_seconds: int, fall_seconds: int = 12
) -> list[float]:
    return (
        [100.0] * 25
        + [100.0 + 40.0 * index / 20.0 for index in range(1, 21)]
        + [140.0] * hold_seconds
        + [
            140.0 - 40.0 * index / fall_seconds
            for index in range(1, fall_seconds + 1)
        ]
        + [100.0] * 30
    )


def _repeated_sustained_wave(
    *, recovery_seconds: int, next_rise_seconds: int = 20
) -> list[float]:
    """Long hold, sharp fall, low recovery, then another sustained wave."""

    return (
        [100.0] * 25
        + [100.0 + 40.0 * index / 20.0 for index in range(1, 21)]
        + [140.0] * 40
        + [140.0 - 25.0 * index / 10.0 for index in range(1, 11)]
        + [115.0] * recovery_seconds
        + [
            115.0 + 25.0 * index / next_rise_seconds
            for index in range(1, next_rise_seconds + 1)
        ]
        + [140.0] * 20
        + [140.0 - 40.0 * index / 12.0 for index in range(1, 13)]
        + [100.0] * 30
    )


def _config(**overrides: object) -> HRmodConfig:
    values: dict[str, object] = {
        "smoothing_window_s": 5.0,
        "smoothing_min_points": 3,
        "min_sustained_rise_s": 3.0,
        "min_sustained_fall_s": 3.0,
        "neutral_trough_timeout_s": 7.0,
        "baseline_lookback_s": 15.0,
        "baseline_min_points": 3,
        "return_sustain_s": 3.0,
    }
    values.update(overrides)
    return HRmodConfig(**values)


def _corrected_waves(result):
    return [wave for wave in result.wave_summary if wave.corrected]


def test_v3_core_signature_schema_and_model_are_strictly_hr_only() -> None:
    parameters = inspect.signature(compute_hrmod_hr_only).parameters
    assert tuple(parameters) == ("hr_samples", "athlete_profile", "config")
    forbidden_inputs = {
        "speed",
        "power",
        "grade",
        "altitude",
        "distance",
        "cadence",
        "laps",
        "intervals",
        "sport",
    }
    assert forbidden_inputs.isdisjoint(HRSample.__dataclass_fields__)
    assert MODEL_VERSION == "hrmod_wave_area_shift_v3"
    assert CONFIG_VERSION == "hrmod_config_v3"
    assert LEGACY_MODEL_VERSION == "hrmod_wave_area_shift_v2"
    with pytest.raises(TypeError):
        compute_hrmod_hr_only(  # type: ignore[call-arg]
            hr_samples=_samples([100.0] * 20),
            athlete_profile=_profile(),
            speed=[1.0] * 20,
        )


def test_v3_config_defaults_and_no_v1_inverse_lobe_balance_fields() -> None:
    config = HRmodConfig()
    assert config.alpha == 1.0
    assert config.rise_threshold_bpm_s == 0.15
    assert config.min_rise_bpm == 5.0
    assert config.smoothing_window_s == 5.0
    assert config.model_variant == "v3_auto"
    assert config.compact_full_v2_s == 30.0
    assert config.sustained_full_v3_s == 45.0
    assert config.terminal_recovery_min_s == 3.0
    assert config.terminal_rebound_guard_s == 10.0
    names = {item.name for item in fields(HRmodConfig)}
    forbidden = {
        "kernel_model",
        "delay_s",
        "tau_on_s",
        "tau_off_s",
        "correction_deadband_bpm",
        "min_lobe_duration_s",
        "min_lobe_area_bpm_s",
        "episode_neutral_gap_s",
        "episode_balance_tolerance_bpm_s",
        "max_episode_duration_s",
        "edge_episode_policy",
    }
    assert names.isdisjoint(forbidden)


def test_alpha_zero_returns_clean_hr_exactly() -> None:
    result = compute_hrmod_hr_only(
        hr_samples=_samples(_triangle()),
        athlete_profile=_profile(),
        config=_config(alpha=0.0),
    )
    assert [point.hrmod_bpm for point in result.timeseries] == [
        point.clean_hr_bpm for point in result.timeseries
    ]
    assert all(point.added_bpm == 0.0 for point in result.timeseries)
    assert all(point.removed_bpm == 0.0 for point in result.timeseries)
    assert result.wave_summary
    assert all(wave.skip_reason == "alpha_zero" for wave in result.wave_summary)


def test_constant_hr_creates_no_wave_or_correction() -> None:
    result = compute_hrmod_hr_only(
        hr_samples=_samples([125.0] * 120),
        athlete_profile=_profile(),
        config=_config(),
    )
    assert not result.wave_summary
    assert result.diagnostics.detected_wave_count == 0
    assert all(point.hrmod_bpm == pytest.approx(125.0) for point in result.timeseries)


def test_rise_below_slope_threshold_creates_no_wave() -> None:
    values = [100.0] * 20
    values += [100.0 + 0.10 * index for index in range(1, 101)]
    values += [110.0 - 0.10 * index for index in range(1, 101)]
    values += [100.0] * 20
    result = compute_hrmod_hr_only(
        hr_samples=_samples(values),
        athlete_profile=_profile(),
        config=_config(rise_threshold_bpm_s=0.15),
    )
    assert not result.wave_summary


def test_total_rise_below_min_rise_creates_no_wave() -> None:
    values = [100.0] * 20
    values += [100.0 + 0.5 * index for index in range(1, 9)]
    values += [104.0 - 0.5 * index for index in range(1, 9)]
    values += [100.0] * 20
    result = compute_hrmod_hr_only(
        hr_samples=_samples(values),
        athlete_profile=_profile(),
        config=_config(min_rise_bpm=5.0),
    )
    assert not result.wave_summary


def test_synthetic_rise_peak_fall_detects_expected_boundaries() -> None:
    result = compute_hrmod_hr_only(
        hr_samples=_samples(_triangle()),
        athlete_profile=_profile(),
        config=_config(),
    )
    wave = result.wave_summary[0]
    assert wave.rise_start_elapsed_s == pytest.approx(19.0, abs=2.0)
    assert wave.peak_elapsed_s == pytest.approx(39.0, abs=2.0)
    assert wave.tail_end_elapsed_s == pytest.approx(58.0, abs=3.0)
    assert wave.end_reason == "return_to_baseline"
    assert wave.complete and wave.corrected


def test_compact_30_second_endpoint_is_numerically_identical_to_v2() -> None:
    values = _sustained_wave(hold_seconds=10)
    v3 = compute_hrmod_hr_only(
        hr_samples=_samples(values),
        athlete_profile=_profile(),
        config=_config(model_variant="v3_auto"),
    )
    v2 = compute_hrmod_hr_only(
        hr_samples=_samples(values),
        athlete_profile=_profile(),
        config=_config(model_variant="v2_legacy"),
    )
    assert v3.model_version == MODEL_VERSION
    assert v2.model_version == LEGACY_MODEL_VERSION
    assert v3.wave_summary[0].transition_weight == 0.0
    assert v3.wave_summary[0].morphology == "compact"
    assert v3.wave_summary[0].correction_strategy == "v2_full_tail"
    assert [point.added_bpm for point in v3.timeseries] == [
        point.added_bpm for point in v2.timeseries
    ]
    assert [point.removed_bpm for point in v3.timeseries] == [
        point.removed_bpm for point in v2.timeseries
    ]
    assert [point.hrmod_bpm for point in v3.timeseries] == [
        point.hrmod_bpm for point in v2.timeseries
    ]
    assert [point.receiver_flag for point in v3.timeseries] == [
        point.receiver_flag for point in v2.timeseries
    ]
    assert [point.donor_flag for point in v3.timeseries] == [
        point.donor_flag for point in v2.timeseries
    ]
    legacy_numeric_fields = (
        "rise_start_elapsed_s",
        "peak_elapsed_s",
        "tail_end_elapsed_s",
        "baseline_hr_bpm",
        "donor_floor_bpm",
        "rise_bpm",
        "fall_bpm",
        "receiver_duration_s",
        "donor_duration_s",
        "donor_available_area_bpm_s",
        "requested_area_bpm_s",
        "receiver_capacity_bpm_s",
        "moved_area_bpm_s",
        "added_area_bpm_s",
        "removed_area_bpm_s",
        "area_balance_error_bpm_s",
    )
    for name in legacy_numeric_fields:
        assert getattr(v3.wave_summary[0], name) == getattr(v2.wave_summary[0], name)


def test_v2_legacy_moved_area_retains_exact_preallocation_target() -> None:
    result = compute_hrmod_hr_only(
        hr_samples=_samples(_triangle()),
        athlete_profile=_profile(),
        config=_config(model_variant="v2_legacy", alpha=0.73),
    )
    wave = result.wave_summary[0]

    assert wave.requested_area_bpm_s == 137.97
    assert wave.moved_area_bpm_s == wave.requested_area_bpm_s
    # The exported added/removed integrals remain their independently summed
    # diagnostics and may differ from the allocation target in the final bit.
    assert wave.added_area_bpm_s == pytest.approx(wave.moved_area_bpm_s, abs=1e-12)


@pytest.mark.parametrize(
    "hold_seconds,expected_weight,expected_strategy",
    [
        (10, 0.0, "v2_full_tail"),
        (15, 1.0 / 3.0, "v3_transition"),
        (20, 2.0 / 3.0, "v3_transition"),
        (25, 1.0, "v3_terminal_fall"),
    ],
)
def test_v3_transition_is_continuous_at_30_and_45_seconds(
    hold_seconds: int, expected_weight: float, expected_strategy: str
) -> None:
    result = compute_hrmod_hr_only(
        hr_samples=_samples(_sustained_wave(hold_seconds=hold_seconds)),
        athlete_profile=_profile(),
        config=_config(),
    )
    wave = result.wave_summary[0]
    assert wave.transition_weight == pytest.approx(expected_weight)
    assert wave.correction_strategy == expected_strategy
    assert wave.added_area_bpm_s == pytest.approx(wave.removed_area_bpm_s, abs=1e-9)
    assert all(
        not (point.receiver_flag and point.donor_flag)
        for point in result.timeseries
    )


def test_sustained_v3_uses_only_sharp_terminal_fall_and_hold_ceiling() -> None:
    result = compute_hrmod_hr_only(
        hr_samples=_samples(_sustained_wave(hold_seconds=40)),
        athlete_profile=_profile(),
        config=_config(),
    )
    wave = result.wave_summary[0]
    assert wave.morphology == "sustained"
    assert wave.correction_strategy == "v3_terminal_fall"
    assert wave.hold_target_hr_bpm == pytest.approx(140.0)
    assert wave.hold_start_elapsed_s is not None
    assert wave.terminal_fall_start_elapsed_s is not None
    assert wave.terminal_fall_end_elapsed_s is not None
    donors = [point for point in result.timeseries if point.donor_flag]
    receivers = [point for point in result.timeseries if point.receiver_flag]
    assert donors and receivers
    assert all(
        wave.terminal_fall_start_elapsed_s
        <= point.elapsed_s
        <= wave.terminal_fall_end_elapsed_s
        for point in donors
    )
    assert all(
        (point.hrmod_bpm or 0.0) <= (wave.hold_target_hr_bpm or 0.0) + 1e-9
        for point in receivers
        if point.added_bpm > 0.0
    )
    assert all(
        (point.hrmod_bpm or 0.0) >= (wave.donor_floor_bpm or 0.0) - 1e-9
        for point in donors
    )
    assert wave.added_area_bpm_s == pytest.approx(wave.removed_area_bpm_s, abs=1e-9)


def test_long_early_peak_without_sharp_terminal_fall_fails_closed() -> None:
    values = (
        [100.0] * 25
        + [100.0 + 40.0 * index / 20.0 for index in range(1, 21)]
        + [140.0] * 60
        + [140.0 - 40.0 * index / 267.0 for index in range(1, 268)]
        + [100.0] * 30
    )
    result = compute_hrmod_hr_only(
        hr_samples=_samples(values), athlete_profile=_profile(), config=_config()
    )
    wave = result.wave_summary[0]
    assert wave.morphology == "ambiguous"
    assert wave.correction_strategy == "none"
    assert wave.skip_reason == "ambiguous_long_wave"
    assert not wave.corrected
    assert all(point.added_bpm == point.removed_bpm == 0.0 for point in result.timeseries)


def test_early_sharp_dip_followed_by_rerise_is_not_terminal() -> None:
    values = (
        [100.0] * 25
        + [100.0 + 40.0 * index / 20.0 for index in range(1, 21)]
        + [140.0] * 30
        + [140.0 - 10.0 * index / 5.0 for index in range(1, 6)]
        + [130.0 + 10.0 * index / 5.0 for index in range(1, 6)]
        + [140.0] * 30
        + [140.0 - 40.0 * index / 12.0 for index in range(1, 13)]
        + [100.0] * 30
    )
    result = compute_hrmod_hr_only(
        hr_samples=_samples(values), athlete_profile=_profile(), config=_config()
    )
    first = result.wave_summary[0]
    assert first.end_reason == "new_rise_trough"
    assert first.morphology == "ambiguous"
    assert first.morphology_reason == "ambiguous_terminal_vs_transient_dip"
    assert first.correction_strategy == "none"
    assert first.terminal_fall_start_elapsed_s is None
    assert not first.corrected


def test_terminal_fall_with_low_recovery_dwell_before_next_rise_is_accepted() -> None:
    result = compute_hrmod_hr_only(
        hr_samples=_samples(_repeated_sustained_wave(recovery_seconds=6)),
        athlete_profile=_profile(),
        config=_config(),
    )
    first = result.wave_summary[0]

    assert first.end_reason == "new_rise_trough"
    assert first.morphology == "sustained"
    assert first.morphology_reason == "confirmed_hold_and_terminal_fall"
    assert first.correction_strategy == "v3_terminal_fall"
    assert first.terminal_fall_start_elapsed_s is not None
    assert first.corrected


def test_terminal_fall_with_immediate_next_rise_is_explicitly_ambiguous() -> None:
    result = compute_hrmod_hr_only(
        hr_samples=_samples(_repeated_sustained_wave(recovery_seconds=0)),
        athlete_profile=_profile(),
        config=_config(),
    )
    first = result.wave_summary[0]

    assert first.end_reason == "new_rise_trough"
    assert first.morphology == "ambiguous"
    assert first.morphology_reason == "ambiguous_terminal_vs_transient_dip"
    assert first.correction_strategy == "none"
    assert first.skip_reason == "ambiguous_long_wave"
    assert not first.corrected


def test_correction_requires_confirmed_fall() -> None:
    values = [100.0] * 20 + [100.0 + index for index in range(1, 21)]
    values += [120.0] * 10
    result = compute_hrmod_hr_only(
        hr_samples=_samples(values),
        athlete_profile=_profile(),
        config=_config(),
    )
    assert result.wave_summary
    assert all(not wave.complete for wave in result.wave_summary)
    assert all(not wave.corrected for wave in result.wave_summary)
    assert all(point.added_bpm == point.removed_bpm == 0.0 for point in result.timeseries)


def test_receiver_and_donor_do_not_overlap() -> None:
    result = compute_hrmod_hr_only(
        hr_samples=_samples(_triangle()),
        athlete_profile=_profile(),
        config=_config(),
    )
    assert all(
        not (point.receiver_flag and point.donor_flag) for point in result.timeseries
    )
    wave = result.wave_summary[0]
    receiver_times = {
        point.elapsed_s
        for point in result.timeseries
        if point.wave_id == wave.wave_id and point.receiver_flag
    }
    donor_times = {
        point.elapsed_s
        for point in result.timeseries
        if point.wave_id == wave.wave_id and point.donor_flag
    }
    assert receiver_times and donor_times and receiver_times.isdisjoint(donor_times)
    assert max(receiver_times) < min(donor_times)


@pytest.mark.parametrize("alpha, expected_fraction", [(1.0, 1.0), (0.5, 0.5)])
def test_alpha_moves_requested_fraction_when_capacity_is_sufficient(
    alpha: float, expected_fraction: float
) -> None:
    result = compute_hrmod_hr_only(
        hr_samples=_samples(_triangle()),
        athlete_profile=_profile(),
        config=_config(alpha=alpha),
    )
    wave = _corrected_waves(result)[0]
    assert not wave.capacity_limited
    assert wave.moved_area_bpm_s == pytest.approx(
        expected_fraction * wave.donor_available_area_bpm_s, abs=1e-9
    )
    assert wave.moved_fraction_of_donor == pytest.approx(expected_fraction)


def test_each_corrected_wave_conserves_area_and_respects_local_donor_floor() -> None:
    result = compute_hrmod_hr_only(
        hr_samples=_samples(_triangle()),
        athlete_profile=_profile(),
        config=_config(alpha=1.0),
    )
    for wave in _corrected_waves(result):
        assert wave.added_area_bpm_s == pytest.approx(
            wave.removed_area_bpm_s, abs=1e-9
        )
        assert wave.moved_area_bpm_s == pytest.approx(
            wave.added_area_bpm_s, abs=1e-9
        )
        assert abs(wave.area_balance_error_bpm_s) <= 1e-9
        points = [
            point for point in result.timeseries if point.wave_id == wave.wave_id
        ]
        area_delta = sum(
            ((point.hrmod_bpm or 0.0) - (point.clean_hr_bpm or 0.0))
            * point.dt_s
            for point in points
        )
        assert area_delta == pytest.approx(0.0, abs=1e-9)
        donor_points = [point for point in points if point.donor_flag]
        assert donor_points
        assert all(
            (point.hrmod_bpm or 0.0) >= (wave.donor_floor_bpm or 0.0) - 1e-9
            for point in donor_points
        )
        # alpha=1 and ample receiver capacity removes every positive donor
        # excess, landing those samples exactly on F=max(B, HR_floor).
        positive_donors = [
            point
            for point in donor_points
            if (point.clean_hr_bpm or 0.0) > (wave.donor_floor_bpm or 0.0)
        ]
        assert positive_donors
        assert all(
            point.hrmod_bpm == pytest.approx(wave.donor_floor_bpm, abs=1e-9)
            for point in positive_donors
        )


def test_donor_sample_already_below_local_floor_is_left_unchanged() -> None:
    clean_hr = np.asarray([100.0, 105.0, 110.0, 120.0, 110.0, 95.0])
    shifted = shift_wave_areas(
        clean_hr=clean_hr,
        dt_s=np.asarray([0.0, 1.0, 1.0, 1.0, 1.0, 1.0]),
        waves=(
            DetectedWave(
                wave_id=1,
                segment_id=1,
                rise_start_index=1,
                peak_index=3,
                tail_end_index=5,
                baseline_hr_bpm=100.0,
                complete=True,
                end_reason="return_to_baseline",
            ),
        ),
        athlete_profile=_profile(),
        config=_config(
            alpha=1.0,
            min_receiver_duration_s=1.0,
            min_donor_duration_s=1.0,
        ),
    )

    assert shifted.removed_bpm[5] == 0.0
    assert shifted.hrmod[5] == 95.0
    wave = shifted.wave_results[0]
    assert wave.corrected
    assert wave.added_area_bpm_s == pytest.approx(wave.removed_area_bpm_s)


def test_receiver_capacity_limits_addition_and_removal_symmetrically() -> None:
    values = _triangle(
        baseline=100.0,
        peak=120.0,
        rise_seconds=20,
        fall_seconds=40,
        post=25,
    )
    profile = _profile(
        floor=99.0,
        hrmax=121.0,
        boundaries=(99.0, 104.0, 108.0, 112.0, 116.0, 121.0),
    )
    result = compute_hrmod_hr_only(
        hr_samples=_samples(values),
        athlete_profile=profile,
        config=_config(alpha=1.0),
    )
    wave = _corrected_waves(result)[0]
    assert wave.capacity_limited
    assert wave.moved_area_bpm_s < wave.requested_area_bpm_s
    assert wave.moved_area_bpm_s == pytest.approx(
        wave.receiver_capacity_bpm_s, abs=1e-9
    )
    assert wave.added_area_bpm_s == pytest.approx(
        wave.removed_area_bpm_s, abs=1e-9
    )
    assert max(point.hrmod_bpm or -math.inf for point in result.timeseries) <= 121.0
    assert min(point.hrmod_bpm or math.inf for point in result.timeseries) >= 99.0


def test_duration_skip_is_not_misreported_as_capacity_limited() -> None:
    result = compute_hrmod_hr_only(
        hr_samples=_samples(_triangle()),
        athlete_profile=_profile(),
        config=_config(min_receiver_duration_s=100.0),
    )
    wave = result.wave_summary[0]
    assert wave.skip_reason == "receiver_too_short"
    assert not wave.corrected
    assert not wave.capacity_limited
    assert wave.capacity_limited_area_bpm_s == 0.0


def test_optional_per_sample_addition_cap_is_symmetric_and_reported() -> None:
    result = compute_hrmod_hr_only(
        hr_samples=_samples(_triangle()),
        athlete_profile=_profile(),
        config=_config(max_addition_bpm=0.25),
    )
    wave = _corrected_waves(result)[0]
    assert wave.capacity_limited
    assert wave.added_area_bpm_s == pytest.approx(wave.removed_area_bpm_s, abs=1e-9)
    assert max(point.added_bpm for point in result.timeseries) <= 0.25 + 1e-12


def test_new_rise_closes_prior_tail_at_trough_and_uses_new_baseline() -> None:
    values = [100.0] * 20
    values += [100.0 + index for index in range(1, 21)]
    values += [120.0 - index for index in range(1, 11)]
    values += [110.0 + index for index in range(1, 16)]
    values += [125.0 - index for index in range(1, 26)]
    values += [100.0] * 20
    result = compute_hrmod_hr_only(
        hr_samples=_samples(values),
        athlete_profile=_profile(),
        config=_config(),
    )
    assert len(result.wave_summary) == 2
    first, second = result.wave_summary
    assert first.end_reason == "new_rise_trough"
    assert first.tail_end_elapsed_s < second.rise_start_elapsed_s
    assert first.baseline_hr_bpm == pytest.approx(100.0)
    assert second.baseline_hr_bpm is not None
    assert second.baseline_hr_bpm > first.baseline_hr_bpm


def test_sustained_plateau_above_baseline_is_excluded_from_donor_tail() -> None:
    values = [100.0] * 20
    values += [100.0 + index for index in range(1, 21)]
    values += [120.0 - index for index in range(1, 11)]
    plateau_start = len(values)
    values += [110.0] * 35
    result = compute_hrmod_hr_only(
        hr_samples=_samples(values),
        athlete_profile=_profile(),
        config=_config(neutral_trough_timeout_s=7.0),
    )
    wave = result.wave_summary[0]
    assert wave.end_reason == "neutral_trough"
    assert wave.tail_end_elapsed_s <= plateau_start + 2
    assert all(
        not point.donor_flag
        for point in result.timeseries[plateau_start + 3 :]
    )


def test_edge_wave_without_baseline_is_incomplete_and_skipped() -> None:
    values = [100.0 + index for index in range(0, 21)]
    values += [120.0 - index for index in range(1, 21)]
    values += [100.0] * 15
    result = compute_hrmod_hr_only(
        hr_samples=_samples(values),
        athlete_profile=_profile(),
        config=_config(),
    )
    wave = result.wave_summary[0]
    assert not wave.complete and not wave.corrected
    assert wave.skip_reason == "insufficient_baseline_history"
    assert "INCOMPLETE_WAVE_START" in wave.flags
    assert all(point.added_bpm == point.removed_bpm == 0.0 for point in result.timeseries)


def test_long_gap_splits_segments_and_marks_gap_terminated_wave() -> None:
    first = [100.0] * 20 + [100.0 + index for index in range(1, 21)]
    first += [120.0 - index for index in range(1, 9)]
    second = [100.0] * 40
    times = [float(index) for index in range(len(first))]
    second_start = times[-1] + 31.0
    times.extend(second_start + index for index in range(len(second)))
    result = compute_hrmod_hr_only(
        hr_samples=_samples(first + second, times),
        athlete_profile=_profile(),
        config=_config(long_gap_threshold_s=10.0),
    )
    assert result.diagnostics.segment_count == 2
    assert result.diagnostics.long_gap_count == 1
    wave = result.wave_summary[0]
    assert wave.end_reason == "long_gap"
    assert not wave.corrected
    assert "LONG_GAP" in wave.flags
    assert {point.segment_id for point in result.timeseries if point.segment_id} == {1, 2}


def test_irregular_timestamps_use_real_dt_in_timeseries_and_areas() -> None:
    times = [0.0]
    steps = (0.7, 1.4, 0.9, 1.8, 0.6, 1.1)
    while times[-1] < 90.0:
        times.append(times[-1] + steps[(len(times) - 1) % len(steps)])

    def value_at(time_s: float) -> float:
        if time_s < 15.0:
            return 100.0
        if time_s < 35.0:
            return 100.0 + time_s - 15.0
        if time_s < 55.0:
            return 120.0 - (time_s - 35.0)
        return 100.0

    result = compute_hrmod_hr_only(
        hr_samples=_samples([value_at(value) for value in times], times),
        athlete_profile=_profile(),
        config=_config(),
    )
    expected_dt = [0.0] + [right - left for left, right in zip(times, times[1:])]
    assert [point.dt_s for point in result.timeseries] == pytest.approx(expected_dt)
    wave = _corrected_waves(result)[0]
    points = [point for point in result.timeseries if point.wave_id == wave.wave_id]
    assert sum(point.added_bpm * point.dt_s for point in points) == pytest.approx(
        wave.added_area_bpm_s, abs=1e-9
    )
    assert sum(point.removed_bpm * point.dt_s for point in points) == pytest.approx(
        wave.removed_area_bpm_s, abs=1e-9
    )


def test_small_noise_around_constant_hr_creates_no_false_waves() -> None:
    values = [120.0 + 0.12 * math.sin(index / 3.0) for index in range(300)]
    result = compute_hrmod_hr_only(
        hr_samples=_samples(values),
        athlete_profile=_profile(),
        config=_config(),
    )
    assert not result.wave_summary
    assert all(point.hrmod_bpm == pytest.approx(point.clean_hr_bpm) for point in result.timeseries)


def test_short_missing_hr_and_spike_are_transparently_interpolated() -> None:
    values = [100.0, 101.0, 180.0, 103.0, 104.0, None, 106.0, 107.0]
    result = compute_hrmod_hr_only(
        hr_samples=_samples(values),
        athlete_profile=_profile(),
        config=_config(smoothing_min_points=2),
    )
    spike = result.timeseries[2]
    missing = result.timeseries[5]
    assert spike.raw_hr_bpm == 180.0
    assert spike.clean_hr_bpm == pytest.approx(102.0)
    assert {"HR_ARTIFACT", "INTERPOLATED_HR"}.issubset(spike.quality_flags)
    assert missing.raw_hr_bpm is None
    assert missing.clean_hr_bpm == pytest.approx(105.0)
    assert "INTERPOLATED_HR" in missing.quality_flags
    assert result.diagnostics.artifact_samples == 1
    assert result.diagnostics.interpolated_samples == 2


def test_identical_hr_and_config_are_deterministic() -> None:
    samples = _samples(_triangle())
    config = _config()
    first = compute_hrmod_hr_only(
        hr_samples=samples, athlete_profile=_profile(), config=config
    )
    second = compute_hrmod_hr_only(
        hr_samples=samples, athlete_profile=_profile(), config=config
    )
    assert first == second
    assert first.hr_input_hash == second.hr_input_hash


def test_zone_classification_uses_unrounded_values() -> None:
    values = [119.999, 120.001, 119.999, 120.001, 119.999, 120.001]
    result = compute_hrmod_hr_only(
        hr_samples=_samples(values),
        athlete_profile=_profile(),
        config=_config(smoothing_min_points=2),
    )
    expected = [
        "Z2",
        "Z3",
        "Z2",
        "Z3",
        "Z2",
        "Z3",
    ]
    assert [point.clean_hr_zone for point in result.timeseries] == expected
    assert [point.hrmod_zone for point in result.timeseries] == expected


@pytest.mark.parametrize(
    "rise_duration,fall_duration,steps",
    [
        (12.0, 18.0, (0.6, 1.1, 1.7, 0.8)),
        (20.0, 12.0, (1.4, 0.7, 1.0, 1.9)),
        (27.0, 31.0, (0.9, 1.3, 0.5, 1.6, 1.1)),
    ],
)
def test_area_conservation_property_for_irregular_synthetic_waves(
    rise_duration: float, fall_duration: float, steps: tuple[float, ...]
) -> None:
    rise_start = 18.0
    peak_time = rise_start + rise_duration
    fall_end = peak_time + fall_duration
    times = [0.0]
    while times[-1] < fall_end + 25.0:
        times.append(times[-1] + steps[(len(times) - 1) % len(steps)])

    def value_at(time_s: float) -> float:
        if time_s < rise_start:
            return 100.0
        if time_s < peak_time:
            return 100.0 + 20.0 * (time_s - rise_start) / rise_duration
        if time_s < fall_end:
            return 120.0 - 20.0 * (time_s - peak_time) / fall_duration
        return 100.0

    result = compute_hrmod_hr_only(
        hr_samples=_samples([value_at(value) for value in times], times),
        athlete_profile=_profile(),
        config=_config(alpha=0.73),
    )
    assert _corrected_waves(result)
    for wave in _corrected_waves(result):
        points = [point for point in result.timeseries if point.wave_id == wave.wave_id]
        assert wave.added_area_bpm_s == pytest.approx(
            wave.removed_area_bpm_s, abs=1e-8
        )
        assert sum(
            ((point.hrmod_bpm or 0.0) - (point.clean_hr_bpm or 0.0))
            * point.dt_s
            for point in points
        ) == pytest.approx(0.0, abs=1e-8)
    assert result.diagnostics.area_conservation_passed


def test_config_profile_and_empty_input_validation() -> None:
    with pytest.raises(ValueError, match="alpha"):
        HRmodConfig(alpha=1.01)
    with pytest.raises(ValueError, match="within HR_floor"):
        AthleteHRProfile(
            hrmax_bpm=200.0,
            hr_floor_bpm=50.0,
            zones=(
                HRZone("Z1", 49.0, 100.0),
                HRZone("Z2", 100.0, 120.0),
                HRZone("Z3", 120.0, 140.0),
                HRZone("Z4", 140.0, 160.0),
                HRZone("Z5", 160.0, 200.0),
            ),
        )
    with pytest.raises(ValueError, match="at least one"):
        compute_hrmod_hr_only(
            hr_samples=(), athlete_profile=_profile(), config=_config()
        )


def test_clean_hr_outside_explicit_profile_is_not_silently_clipped() -> None:
    profile = _profile(
        floor=90.0,
        hrmax=130.0,
        boundaries=(90.0, 98.0, 106.0, 114.0, 122.0, 130.0),
    )
    with pytest.raises(ValueError, match="outside"):
        compute_hrmod_hr_only(
            hr_samples=_samples([131.0] * 20),
            athlete_profile=profile,
            config=_config(),
        )
