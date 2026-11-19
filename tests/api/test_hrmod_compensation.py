from copy import deepcopy

import numpy as np
import pytest

from apps.api.hrmod_compensation import allocate, compensate, interval_weights, MODEL_VERSION

BOUNDS = [84., 135., 148., 166., 176., 187.]


def test_high_donor_is_restored_and_low_zone_funds_the_unchanged_addition():
    final, report = allocate([165, 180, 120], [176, 169, 120], [30, 30, 30], BOUNDS)
    assert final.tolist() == pytest.approx([176, 180, 109])
    assert report['removed_area_bpm_s'] == pytest.approx(330)
    assert report['donors'][0]['removed_area_bpm_s'] == pytest.approx(330)
    assert not report['capacity_limited']


def test_priority_exhausts_lower_zones_without_crossing_floor_or_removal_cap():
    final, report = allocate([165, 90, 140, 155, 180], [185, 90, 140, 155, 160],
                             [4, 1, 1, 2, 1], BOUNDS)
    assert final.tolist() == pytest.approx([185, 84, 110, 133, 180])
    assert [r['removed_area_bpm_s'] for r in report['donors']] == pytest.approx([6, 30, 44, 0, 0])
    assert report['added_area_bpm_s'] == pytest.approx(80)


def test_insufficient_capacity_limits_additions_and_never_uses_a_receiver():
    final, report = allocate([165, 85], [176, 85], [1, 1], BOUNDS)
    assert final.tolist() == pytest.approx([166, 84])
    assert report['addition_scale'] == pytest.approx(1/11)
    assert report['unfunded_area_bpm_s'] == pytest.approx(10)
    assert report['capacity_limited']


def test_unsupported_or_missing_samples_cannot_fund_compensation():
    final, report = allocate([165, 120, np.nan], [176, 120, np.nan], [1, 0, 1], BOUNDS)
    assert final[:2].tolist() == pytest.approx([165, 120])
    assert np.isnan(final[2])
    assert report['added_area_bpm_s'] == 0


def test_smart_recording_uses_trapezoids_and_excludes_timer_pause_and_final_sample():
    def row(t, hr, kind, end):
        return {'elapsed_s': t, 'hr_raw_bpm': hr, 'hr_clean_bpm': hr,
                'hr_interval_classification': kind, 'hr_interval_end_s': end}
    rows = [row(0, 120, 'smart_recording', 4), row(4, 140, 'recording_stop', 40),
            row(40, 150, 'original_1hz', 41), row(41, 160, None, None)]
    assert interval_weights(rows, 41).tolist() == pytest.approx([2, 2, 1, 0])
    assert interval_weights(rows, 2).tolist() == pytest.approx([1.5, .5, 0, 0])
    rows[1]['hr_raw_bpm'] = None
    assert interval_weights(rows, 41).tolist() == pytest.approx([0, 0, 1, 0])


def test_recording_summary_uses_final_signal_and_preserves_raw_and_candidate():
    rows = [{'elapsed_s': i, 'hr_raw_bpm': raw, 'hr_clean_bpm': raw,
             'hrmod_candidate_bpm': old, 'hrmod_final_bpm': old,
             'hr_interval_classification': 'original_1hz', 'hr_interval_end_s': i+1}
            for i, (raw, old) in enumerate([(165, 176), (180, 169), (120, 120), (120, 120)])]
    original = deepcopy(rows)
    result = compensate(rows, {'status':'computed', 'diagnostics':{}, 'waves':[]},
                         bounds=BOUNDS, sport='NordicSki', duration_s=3)
    assert [r['hr_raw_bpm'] for r in rows] == [r['hr_raw_bpm'] for r in original]
    assert [r['hrmod_candidate_bpm'] for r in rows] == [r['hrmod_candidate_bpm'] for r in original]
    assert [r['hrmod_final_bpm'] for r in rows] == pytest.approx([176,180,109,120])
    assert rows[2]['donor_flag'] and not rows[1]['donor_flag']
    assert result['zones'][-1]['hrmod_final_seconds'] == pytest.approx(2)
    assert result['model_version'] == MODEL_VERSION


def test_area_and_limits_hold_for_weighted_recordings():
    rng = np.random.default_rng(717)
    for _ in range(20):
        raw = rng.uniform(84, 187, 300)
        prior = np.clip(raw+rng.uniform(-20, 20, 300), 84, 187)
        weights = rng.uniform(.1, 5, 300)
        final, report = allocate(raw, prior, weights, BOUNDS)
        assert np.dot(final-raw, weights) == pytest.approx(0, abs=1e-8)
        assert np.all(final >= 84-1e-9) and np.all(final <= 187+1e-9)
        assert np.all(raw-final <= 30+1e-9)
        if not report['capacity_limited']:
            assert final[prior > raw] == pytest.approx(prior[prior > raw])
