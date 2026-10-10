import pytest

from apps.api.activity_shadow_pipeline import compute_activity_shadow, activity_shadow_configuration_fingerprint
from apps.api.speed_segments import sample_intervals
from intervals_inspector.stream_normalizer import NormalizerInput, normalize_stream_intervals
from vflat_b65.sports import speed_model_versions


def compute(sport, *, grade=None, speed=True, noisy_elevation=False):
    offsets = list(range(901))
    metrics = {"heartrate": [150.] * len(offsets), "distance": [3. * t for t in offsets]}
    if speed:
        metrics["velocity_smooth"] = [3.] * len(offsets)
    if grade is not None:
        metrics["gradient"] = [grade] * len(offsets)
    if noisy_elevation:
        metrics["altitude"] = [100. + t * .3 for t in offsets]
    normalized = normalize_stream_intervals(NormalizerInput(offsets=offsets, metrics=metrics))
    return compute_activity_shadow(
        detail={"type": sport, "start_date": "2026-09-26T08:00:00Z", "moving_time": 900},
        normalized=normalized, zone_bounds_bpm=(100,120,140,160,180,200), explicit_hrmax_bpm=200,
    )


def test_missing_treadmill_incline_uses_recorded_speed_and_discloses_assumption():
    original, treadmill = compute("VirtualRun")
    outdoor_input, outdoor = compute("Run")
    assert original == outdoor_input  # Raw observations remain unchanged.
    assert all(s["grade_raw_pct"] is None for s in original["samples"])
    assert treadmill["vflat_model_version"] == speed_model_versions("VirtualRun")[0]
    valid = [(right-left, speed) for left,right,speed,reason in sample_intervals(treadmill) if not reason]
    assert sum(dt for dt,_ in valid) == pytest.approx(900)
    assert all(speed == pytest.approx(10.8) for _,speed in valid)
    assert all(s["grade_assumed_flat"] for s in treadmill["speed_test_series"])
    assert treadmill["trainability_index"]["general"]["valid"]
    assert all(reason for *_,reason in sample_intervals(outdoor))


@pytest.mark.parametrize("grade,multiplier", [(0.,1.),(4.,1.12),(10.,1.5)])
def test_treadmill_recorded_incline_takes_precedence_over_stationary_altimeter(grade,multiplier):
    _, result = compute("VirtualRun", grade=grade, noisy_elevation=True)
    assert not any(s["grade_assumed_flat"] for s in result["speed_test_series"])
    assert all(s["grade_smoothed_pct"] == grade for s in result["speed_test_series"])
    assert result["trainability_index"]["general"]["mean_vflat_kmh"] == pytest.approx(10.8*multiplier)


def test_treadmill_does_not_invent_speed_from_total_distance_or_remove_grade_limits():
    _, absent = compute("VirtualRun", speed=False)
    assert not any(not reason for *_,reason in sample_intervals(absent))
    _, steep = compute("VirtualRun", grade=40.)
    assert not any(not reason for *_,reason in sample_intervals(steep))


def test_only_treadmill_model_identity_changes():
    kwargs = {"zone_bounds_bpm": (124,144,164,182,190,205), "explicit_hrmax_bpm":205}
    assert activity_shadow_configuration_fingerprint(**kwargs,sport="NordicSki") == "30100e84ef63bda169b5da6f2f07ffb137bd658d083f32e2a68b4dd5240fea52"
    assert speed_model_versions("Run") == speed_model_versions("TrailRun")
    assert speed_model_versions("VirtualRun") != speed_model_versions("Run")
    assert speed_model_versions("TreadmillRunning") == speed_model_versions("VirtualRun")
