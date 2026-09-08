from copy import deepcopy

import numpy as np
import pytest

from apps.api.trainability import MIN_SECONDS_BY_BAND, compute_trainability


def calculate(hrs, speeds, duration=3600, hrmax=178):
    return compute_trainability(
        [{"hrmod_final_bpm": hr, "dt_s": dt} for hr, dt in hrs],
        [{"vflat_b65_kmh": speed, "dt_s": dt, "grade_raw_pct": grade} for speed, dt, grade in speeds],
        zone_bounds_bpm=[50, 137, 147, 158, 170, 178], hrmax_bpm=hrmax,
        activity_duration_s=duration, comparison_key="test", source_versions={},
    )


def named(result, name):
    return result["general"] if name == "GENERAL" else result["zones"][int(name[1]) - 1]


def test_general_band_preserves_rank_offset_and_is_not_mean_of_zone_indices():
    result = calculate([(175, 420), (150, 840), (120, 420)], [(5, 420, 0), (20, 840, 0), (100, 420, 0)])
    general = result["general"]
    assert general["lower_bpm"] == 133.5
    assert general["upper_bpm"] == pytest.approx(163.76)
    assert general["hr_seconds"] == general["speed_seconds"] == 840
    assert general["mean_vflat_kmh"] == 20
    assert general["index"] == pytest.approx((150 / 178 * 100) / 20)
    assert general["index"] != np.mean([z["index"] for z in result["zones"] if z["valid"]])


def test_normalized_index_uses_percentage_points_and_individual_hrmax():
    result = calculate([(154, 420)], [(20, 420, 0)])
    assert result["normalization"] == "percent_hrmax"
    assert result["general"]["mean_hrmax_percent"] == pytest.approx(86.51685393258427)
    assert result["general"]["index"] == pytest.approx(4.325842696629214)
    # Equal relative HR and speed are equal even with different absolute HR.
    other = calculate([(173.03370786516854, 420)], [(20, 420, 0)], hrmax=200)
    assert other["general"]["index"] == pytest.approx(result["general"]["index"])


@pytest.mark.parametrize("duration, valid", [(419.999, False), (420, True), (421, True)])
def test_seven_minute_activity_boundary(duration, valid):
    result = calculate([(150, 420)], [(20, 420, 0)], duration)
    assert result["general"]["valid"] is valid
    if not valid:
        assert all(z["index"] is None and z["invalid_reason"] == "ACTIVITY_BELOW_7MIN" for z in [*result["zones"], result["general"]])


BANDS = [("Z1", 120), ("Z2", 142), ("Z3", 150), ("Z4", 163), ("Z5", 174), ("GENERAL", 150)]


@pytest.mark.parametrize("name, hr", BANDS)
@pytest.mark.parametrize("offset, valid", [(-0.001, False), (0, True), (0.001, True)])
def test_each_band_hr_minimum_uses_unrounded_cumulative_time(name, hr, offset, valid):
    seconds = MIN_SECONDS_BY_BAND[name] + offset
    # Several separate pieces count cumulatively; no continuous-bout requirement.
    result = calculate([(hr, seconds / 4)] * 4, [(20, 600, 0)])
    band = named(result, name)
    assert band["minimum_seconds"] == MIN_SECONDS_BY_BAND[name]
    assert band["valid"] is valid
    if not valid:
        assert band["invalid_reason"] == "HR_TIME_BELOW_MINIMUM"
        assert band["index"] is None


@pytest.mark.parametrize("name, hr", BANDS)
@pytest.mark.parametrize("offset, valid", [(-0.001, False), (0, True), (0.001, True)])
def test_each_band_allocated_speed_minimum_is_independent(name, hr, offset, valid):
    result = calculate([(hr, 600)], [(20, MIN_SECONDS_BY_BAND[name] + offset, 0)])
    band = named(result, name)
    assert band["valid"] is valid
    if not valid:
        assert band["invalid_reason"] == "SPEED_TIME_BELOW_MINIMUM"
        assert band["index"] is None


def test_only_speed_pool_loses_downhills_and_exact_minus_three_stays():
    result = calculate([(150, 840)], [(80, 420, -8), (20, 420, -3)])
    assert result["hr_seconds"] == 840
    assert result["eligible_speed_seconds"] == 420
    assert result["downhill_excluded_seconds"] == 420
    assert result["general"]["index"] == pytest.approx(150 / 178 * 100 / 20)


def test_invalid_small_zone_keeps_its_reserved_fastest_speeds():
    result = calculate([(174, 180), (163, 600)], [(40, 180, 0), (20, 600, 0)])
    assert result["zones"][4]["invalid_reason"] == "HR_TIME_BELOW_MINIMUM"
    assert result["zones"][3]["mean_vflat_kmh"] == 20
    assert result["zones"][3]["valid"] is True


def test_fractional_speed_boundary_is_duration_weighted():
    result = calculate([(174, 420), (150, 420), (120, 420)], [(30, 630, 0), (10, 631, 0)])
    z3 = result["zones"][2]
    expected = ((630 - 1261 / 3) * 30 + (2522 / 3 - 630) * 10) / (1261 / 3)
    assert z3["speed_seconds"] == pytest.approx(1261 / 3)
    assert z3["mean_vflat_kmh"] == pytest.approx(expected)
    assert z3["index"] == pytest.approx(150 / 178 * 100 / expected)


def test_missing_and_zero_weights_do_not_invent_coverage_or_divide_by_zero():
    result = calculate([(150, 0), (None, 500), (150, 420)], [(0, 420, 0), (50, 500, None)])
    assert result["hr_seconds"] == 420
    assert result["general"]["index"] is None
    assert result["general"]["invalid_reason"] == "ZERO_SPEED"
    missing = calculate([(150, 600)], [(20, 600, 0)], hrmax=None)
    assert all(z["index"] is None and z["invalid_reason"] == "HRMAX_MISSING" for z in [*missing["zones"], missing["general"]])


def test_validity_is_separate_for_hr_and_vflat_and_inputs_are_immutable():
    hrs = [{"hrmod_final_bpm": 150, "dt_s": 840, "exclusion_reason": "WAVE_NOT_CORRECTED"}]
    speeds = [{"vflat_b65_kmh": 20, "dt_s": 420, "grade_raw_pct": 0},
              {"vflat_b65_kmh": 80, "dt_s": 420, "grade_raw_pct": 0, "exclusion_reason": "VFLAT_SAMPLE_EXCLUDED"}]
    before = deepcopy((hrs, speeds))
    result = compute_trainability(hrs, speeds, zone_bounds_bpm=[50,137,147,158,170,178],
        hrmax_bpm=178, activity_duration_s=840, comparison_key="test", source_versions={})
    assert result["hr_seconds"] == 840
    assert result["general"]["index"] == pytest.approx(150 / 178 * 100 / 20)
    assert (hrs, speeds) == before
