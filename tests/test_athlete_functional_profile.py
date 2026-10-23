import math

import pytest

from biathlon.athlete_functional_profile import build_functional_profile
from biathlon.speed_duration import Curve, REFERENCE_SPEEDS, REFERENCE_TIMES


REFERENCE = Curve(REFERENCE_TIMES, REFERENCE_SPEEDS)


def test_point(duration, ratio):
    return {"duration_s": duration, "speed_kmh": REFERENCE.speed(duration) * 3.6 * ratio,
            "test_mode": "STRICT", "source": "MANUAL"}


# This is a test-data builder, not a pytest test.
test_point.__test__ = False


class RelativeCurve:
    def __init__(self, ratio, slope=0):
        self.ratio = ratio
        self.slope = slope

    def speed(self, duration):
        return REFERENCE.speed(duration) * self.ratio * (duration / 180) ** self.slope


def test_uniform_performance_gap_changes_level_without_inventing_shape():
    result = build_functional_profile(RelativeCurve(.8), [test_point(180, .8), test_point(720, .8)])
    assert result["overall_level"]["difference_percent"] == pytest.approx(-20)
    assert result["shape"]["orientation"] == "NO_RELATIVE_DIFFERENCE"
    assert result["shape"]["endurance_contrast_percent"] == pytest.approx(0, abs=1e-10)
    assert all(abs(point["shape_difference_percent"]) < 1e-10 for point in result["points"])


def test_signed_shape_contrast_is_independent_of_absolute_level():
    first = build_functional_profile(RelativeCurve(.8), [test_point(180, .8), test_point(1200, .88)])
    second = build_functional_profile(RelativeCurve(.4), [test_point(180, .4), test_point(1200, .44)])
    assert first["shape"]["orientation"] == second["shape"]["orientation"]
    assert first["shape"]["endurance_contrast_percent"] == pytest.approx(second["shape"]["endurance_contrast_percent"])
    assert first["shape"]["orientation"] == "LONGER_DURATION_ADVANTAGE"
    assert first["shape"]["endurance_contrast_percent"] == pytest.approx(10)
    assert first["overall_level"]["ratio_to_reference"] == pytest.approx(math.sqrt(.8 * .88))


@pytest.mark.parametrize("tests,status", [([], "ESTIMATED"), ([test_point(180, .8)], "SINGLE_TEST")])
def test_priors_and_single_test_cannot_determine_measured_orientation(tests, status):
    result = build_functional_profile(RelativeCurve(.8, .02), tests)
    assert result["status"] == status
    assert result["shape"]["orientation"] is None
    assert result["shape"]["endurance_contrast_percent"] is None
    assert not any(point["used_for_shape"] for point in result["points"])
    assert not any(point["used_for_shape"] for point in result["test_points"])


def test_capped_extrapolation_never_drives_level_or_shape():
    tests = [test_point(180, .8), test_point(720, .82)]
    normal = build_functional_profile(RelativeCurve(.8), tests)
    exaggerated = build_functional_profile(RelativeCurve(2, .1), tests,
        point_metadata=lambda t: {"extrapolation_capped": t > 720 or t < 180})
    assert exaggerated["overall_level"] == normal["overall_level"]
    assert exaggerated["shape"] == normal["shape"]
    tails = [point for point in exaggerated["points"] if point["extrapolation_capped"]]
    assert tails
    assert all(point["evidence"] == "EXTRAPOLATED" and not point["used_for_shape"] for point in tails)
    assert "CORRIDOR_LIMIT_IS_MODEL_CONSTRAINT_NOT_ATHLETE_TRAIT" in exaggerated["warnings"]


def test_all_measured_opinions_retained_and_interpolation_is_not_measurement():
    tests = [test_point(180, .8), test_point(720, .84), test_point(1800, .86)]
    result = build_functional_profile(RelativeCurve(.8), tests)
    assert len(result["test_points"]) == 3
    assert all(point["evidence"] == "MEASURED_TEST" for point in result["test_points"])
    assert result["test_points"][1]["speed_kmh"] == tests[1]["speed_kmh"]
    between = next(point for point in result["points"] if point["duration_s"] == 1200)
    assert between["evidence"] == "TEST_SUPPORTED_ESTIMATE"
    assert not between["used_for_shape"]


def test_generated_and_exploratory_observations_cannot_upgrade_evidence():
    tests = [test_point(180, .8), {**test_point(720, .84), "source": "MODEL_GENERATED"},
             {**test_point(1200, .86), "test_mode": "EXPLORATORY"},
             {**test_point(1800, .87), "is_maximal": False},
             {**test_point(3600, .88), "kind": "ESTIMATE", "is_maximal_test": False},
             {**test_point(7200, .9), "generated": True}]
    result = build_functional_profile(RelativeCurve(.8), tests)
    assert result["status"] == "SINGLE_TEST"
    assert result["shape"]["accepted_test_count"] == 1
    assert result["shape"]["orientation"] is None


def test_missing_absolute_speed_is_unavailable_not_reference_athlete():
    result = build_functional_profile(None, [])
    assert result["status"] == "UNAVAILABLE"
    assert result["overall_level"] is None
    assert result["shape"] is None
    assert result["points"] == []


def test_exposure_is_context_and_unknown_zones_are_not_assumed_zero():
    profile = build_functional_profile(RelativeCurve(.8), [test_point(180, .8), test_point(720, .82)],
                                       zone_weekly_min={"Z1": 120, "Z2": 0}, exposure_source="HR_MEASURED")
    assert profile["training_context"]["interpretation"] == "ASSOCIATION_ONLY"
    assert profile["training_context"]["source"] == "HR_MEASURED"
    assert profile["training_context"]["zones"][2]["weekly_minutes"] is None
    assert all(row["share_percent"] is None for row in profile["training_context"]["zones"])
    changed = build_functional_profile(RelativeCurve(.8), [test_point(180, .8), test_point(720, .82)],
                                       zone_weekly_min={"Z1": 0, "Z2": 120, "Z3": 50, "Z4": 20, "Z5": 10})
    assert changed["shape"] == profile["shape"]
    assert changed["overall_level"] == profile["overall_level"]
    assert sum(row["share_percent"] for row in changed["training_context"]["zones"]) == pytest.approx(100)


def test_conflicting_accepted_tests_are_not_silently_averaged():
    with pytest.raises(ValueError, match="Conflicting accepted tests"):
        build_functional_profile(RelativeCurve(.8), [test_point(180, .8), test_point(180, .9)])
