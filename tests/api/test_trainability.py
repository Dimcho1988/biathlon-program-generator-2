from copy import deepcopy

import numpy as np
import pytest

from apps.api.trainability import compute_trainability


def calculate(hrs, speeds, duration=900):
    return compute_trainability(
        [{"hrmod_final_bpm": hr, "dt_s": dt} for hr, dt in hrs],
        [{"vflat_b65_kmh": speed, "dt_s": dt, "grade_raw_pct": grade} for speed, dt, grade in speeds],
        zone_bounds_bpm=[50, 137, 147, 158, 170, 178], hrmax_bpm=178,
        activity_duration_s=duration, comparison_key="test", source_versions={},
    )


def test_general_band_preserves_rank_offset_and_is_not_mean_of_zone_indices():
    result = calculate([(175, 100), (150, 300), (120, 100)], [(5, 100, 0), (20, 300, 0), (100, 100, 0)])
    general = result["general"]
    assert general["lower_bpm"] == 133.5
    assert general["upper_bpm"] == pytest.approx(163.76)
    assert general["hr_seconds"] == general["speed_seconds"] == 300
    assert general["mean_vflat_kmh"] == 20
    assert general["index"] == 7.5
    assert general["index"] != np.mean([z["index"] for z in result["zones"] if z["valid"]])


@pytest.mark.parametrize("duration, valid", [(419.999, False), (420, True), (421, True)])
def test_seven_minute_activity_boundary(duration, valid):
    result = calculate([(150, 420)], [(20, 420, 0)], duration)
    assert result["general"]["valid"] is valid
    if not valid:
        assert all(z["index"] is None and z["invalid_reason"] == "ACTIVITY_BELOW_7MIN" for z in [*result["zones"], result["general"]])


@pytest.mark.parametrize("seconds, valid", [(59.999, False), (60, True), (60.001, True)])
def test_sixty_second_boundary_is_not_rounded(seconds, valid):
    result = calculate([(150, seconds)], [(20, 100, 0)])
    assert result["zones"][2]["valid"] is valid


def test_only_speed_pool_loses_downhills_and_exact_minus_three_stays():
    result = calculate([(150, 600)], [(80, 300, -8), (20, 300, -3)])
    assert result["hr_seconds"] == 600
    assert result["eligible_speed_seconds"] == 300
    assert result["downhill_excluded_seconds"] == 300
    assert result["general"]["index"] == 7.5


def test_speed_allocation_also_needs_sixty_seconds_and_small_zone_is_reserved():
    result = calculate([(174, 60), (163, 540)], [(40, 6, 0), (20, 54, 0)])
    assert result["zones"][4]["invalid_reason"] == "SPEED_TIME_BELOW_60S"
    assert result["zones"][3]["mean_vflat_kmh"] == 20
    assert result["zones"][3]["invalid_reason"] == "SPEED_TIME_BELOW_60S"


def test_fractional_speed_boundary_is_duration_weighted():
    result = calculate([(174, 60), (150, 60), (120, 60)], [(30, 90, 0), (10, 91, 0)])
    z3 = result["zones"][2]
    expected = ((90 - 181 / 3) * 30 + (362 / 3 - 90) * 10) / (181 / 3)
    assert z3["speed_seconds"] == pytest.approx(181 / 3)
    assert z3["mean_vflat_kmh"] == pytest.approx(expected)


def test_missing_and_zero_weights_do_not_invent_coverage_or_divide_by_zero():
    result = calculate([(150, 0), (None, 500), (150, 60)], [(0, 60, 0), (50, 500, None)])
    assert result["hr_seconds"] == 60
    assert result["general"]["index"] is None
    assert result["general"]["invalid_reason"] == "ZERO_SPEED"


def test_validity_is_separate_for_hr_and_vflat_and_inputs_are_immutable():
    hrs = [{"hrmod_final_bpm": 150, "dt_s": 600, "exclusion_reason": "WAVE_NOT_CORRECTED"}]
    speeds = [{"vflat_b65_kmh": 20, "dt_s": 300, "grade_raw_pct": 0},
              {"vflat_b65_kmh": 80, "dt_s": 300, "grade_raw_pct": 0, "exclusion_reason": "VFLAT_SAMPLE_EXCLUDED"}]
    before = deepcopy((hrs, speeds))
    result = compute_trainability(hrs, speeds, zone_bounds_bpm=[50,137,147,158,170,178],
        hrmax_bpm=178, activity_duration_s=600, comparison_key="test", source_versions={})
    assert result["hr_seconds"] == 600
    assert result["general"]["index"] == 7.5
    assert (hrs, speeds) == before
