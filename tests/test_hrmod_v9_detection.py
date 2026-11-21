"""Morphology regressions independent of the private TCX examples."""
from datetime import UTC, datetime, timedelta

import numpy as np
import pytest

from hrmod_lab.hrmod_core import compute_hrmod_hr_only
from hrmod_lab.schemas import AthleteHRProfile, HRmodConfig, HRSample, HRZone


START = datetime(2026, 1, 1, tzinfo=UTC)
PROFILE = AthleteHRProfile(180, 30, tuple(
    HRZone(f"Z{i + 1}", low, high)
    for i, (low, high) in enumerate(zip([80, 137, 148, 160, 170], [137, 148, 160, 170, 180]))
))


def run(values, times=None, **settings):
    times = list(range(len(values))) if times is None else times
    return compute_hrmod_hr_only(
        hr_samples=tuple(HRSample(START + timedelta(seconds=float(t)), float(h))
                         for t, h in zip(times, values, strict=True)),
        athlete_profile=PROFILE, config=HRmodConfig(**settings),
    )


def assert_bounds_and_area(result):
    for w in result.wave_summary:
        assert w.added_area_bpm_s == pytest.approx(w.removed_area_bpm_s, abs=1e-6)
    for p in result.timeseries:
        assert not (p.receiver_flag and p.donor_flag)
        assert p.hrmod_bpm <= 180 + 1e-9
        assert p.added_bpm <= 30 + 1e-9
        assert p.removed_bpm <= 30 + 1e-9


@pytest.mark.parametrize('spacing', [1, 2, 3])
def test_ten_staircase_intervals_remain_separate_with_sparse_sampling(spacing):
    # Every rise totals 10 bpm; no individual step exceeds 2 bpm.
    cycle = sum(([h] * 6 for h in [145, 147, 149, 151, 153, 155, 153, 151, 149, 147]), [])
    values = [145] * 30 + cycle * 10 + [145] * 30
    times = list(range(0, len(values), spacing))
    result = run([values[i] for i in times], times)
    corrected = [w for w in result.wave_summary if w.corrected]
    assert len(corrected) == 10
    assert all(w.tail_end_elapsed_s - w.rise_start_elapsed_s < 90 for w in corrected)
    assert all(a.tail_end_elapsed_s < b.rise_start_elapsed_s
               for a, b in zip(result.wave_summary, result.wave_summary[1:]))
    assert_bounds_and_area(result)


def test_lower_noisy_flat_top_remains_receiver_until_real_fall():
    values = [110] * 25 + list(np.linspace(110, 169, 50))
    plateau_start = len(values)
    values += [168, 168, 169, 168, 168, 168] * 8
    plateau_end = len(values) - 1
    values += list(np.linspace(167, 110, 75)) + [110] * 20
    result = run(values)
    wave = next(w for w in result.wave_summary if w.corrected)
    assert wave.peak_elapsed_s >= plateau_end - 3
    assert wave.peak_elapsed_s <= plateau_end + 3
    assert all(not p.donor_flag for p in result.timeseries[plateau_start:plateau_end - 3])
    assert any(p.receiver_flag for p in result.timeseries[plateau_start:plateau_end])
    assert_bounds_and_area(result)


def test_208_second_wave_is_accepted_but_very_long_wave_is_rejected():
    short = run([110] * 25 + list(np.linspace(110, 169, 70)) + [169] * 40
                + list(np.linspace(169, 110, 100)) + [110] * 20)
    assert any(w.corrected and w.tail_end_elapsed_s - w.rise_start_elapsed_s > 180
               for w in short.wave_summary)
    long = run([110] * 25 + list(np.linspace(110, 169, 110)) + [169] * 140
               + list(np.linspace(169, 110, 110)) + [110] * 20)
    assert any(w.skip_reason == 'mirror_wave_duration_above_limit' for w in long.wave_summary)
    assert not any(w.corrected for w in long.wave_summary)
    assert_bounds_and_area(short)


def test_high_hr_noise_and_unfinished_plateau_cannot_donate():
    noise = run(([149, 150, 151, 150] * 60))
    assert not noise.wave_summary
    unfinished = run([110] * 25 + list(np.linspace(110, 169, 60)) + [168, 169] * 60)
    assert unfinished.wave_summary
    assert not any(w.corrected for w in unfinished.wave_summary)
    assert all(p.added_bpm == p.removed_bpm == 0 for p in unfinished.timeseries)


@pytest.mark.parametrize('spacing', [1, 2, 3])
def test_short_descending_holds_do_not_extend_the_top(spacing):
    values = [135] * 30 + list(np.linspace(135, 155, 25)) + [155] * 3
    top_end = len(values) - 1
    values += [154] * 6 + [153] * 6 + [152] * 6
    values += list(np.linspace(151, 135, 20)) + [135] * 20
    times = list(range(0, len(values), spacing))
    result = run([values[i] for i in times], times)
    wave = next(w for w in result.wave_summary if w.corrected)
    assert wave.peak_elapsed_s <= top_end + spacing
    assert any(p.donor_flag for p in result.timeseries
               if top_end + spacing < p.elapsed_s < top_end + 12)
    assert_bounds_and_area(result)


@pytest.mark.parametrize('spacing', [1, 2, 3])
def test_accumulated_four_bpm_fall_separates_two_rises(spacing):
    values = [140] * 30 + list(np.linspace(140, 155, 30)) + [155] * 8
    first_top_end = len(values) - 1
    values += [154] * 8 + [153] * 8 + [152] * 8 + [151] * 8
    second_start = len(values)
    values += list(np.linspace(151, 163, 30))
    values += list(np.linspace(163, 140, 30)) + [140] * 30
    times = list(range(0, len(values), spacing))
    result = run([values[i] for i in times], times)
    waves = [w for w in result.wave_summary if w.corrected]
    assert len(waves) == 2
    assert waves[0].peak_elapsed_s <= first_top_end + spacing
    assert waves[0].tail_end_elapsed_s < second_start
    assert waves[1].rise_start_elapsed_s >= first_top_end
    assert_bounds_and_area(result)


@pytest.mark.parametrize('field', ['plateau_min_duration_s', 'plateau_range_bpm'])
@pytest.mark.parametrize('value', [0, -1, float('nan'), float('inf')])
def test_plateau_controls_require_finite_positive_values(field, value):
    with pytest.raises(ValueError):
        HRmodConfig(**{field: value})
