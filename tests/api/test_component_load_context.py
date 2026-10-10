from copy import deepcopy

import pytest

from apps.api import component_load_context as capacity
from biathlon import dosing_curve, hr_speed, speed_duration
from tests.test_dosing_curve import TESTS, example


def curve_view():
    curve = speed_duration.calibrated(TESTS)
    seconds = {"Z1": 21600., "Z2": 12000., "Z3": 4000., "Z4": 1800.}
    return {
        "status": "CALIBRATED", "sport": "Run",
        "active_test_keys": ["a", "b"],
        "tests": [{"entry_key": key, "payload": deepcopy(test)} for key, test in zip(("a", "b"), TESTS)],
        "hr_model": {"zones": [{"zone": zone, "duration_s": 1.,
            "speed_kmh": curve.speed(duration) * 3.6, "source": "INDEX"}
            for zone, duration in seconds.items()]},
    }, seconds


def test_curve_inverse_is_authoritative_without_expert_or_history_clamping():
    view, expected = curve_view()
    result = capacity.context_from_speed_view(view)
    assert result["status"] == "CURVE"
    for zone, seconds in expected.items():
        assert result["minutes"][zone] == pytest.approx(seconds / 60)
        assert result["sources"][zone] == "SPEED_DURATION"
    assert result["minutes"]["Z1"] > hr_speed.TMAX_RANGES_S["Z1"][1] / 60
    assert result["minutes"]["Z5"] == result["minutes"]["Z4"]
    assert result["references"]["Z5"]["reference_edge"] == "LOWER_SHARED_Z4"


def test_blended_curve_uses_its_own_references_and_retains_expert_mapping_provenance():
    view = example()
    unchanged = deepcopy(view)
    curve = dosing_curve.from_view(view)
    result = capacity.context_from_speed_view(view)
    for row in view["dosing_model"]["hr_model"]["zones"][:4]:
        assert result["minutes"][row["zone"]] == pytest.approx(curve.inverse(row["speed_kmh"] / 3.6) / 60)
        assert result["sources"][row["zone"]] == "BLENDED_DOSING_CURVE"
        assert result["references"][row["zone"]]["reference_source"] == row["index_hr_source"]
    assert view == unchanged


def test_no_curve_uses_explicit_expert_times_never_historical_tref_or_weekly_q():
    result = capacity.context_from_speed_view({
        "status": "UNAVAILABLE", "volume_weekly_min": {"Z1": 9999},
        "tref": {"Z1": 8888}, "preliminary_capacity": {"anchors": [
            {"zone": "Z1", "duration_s": 12600, "duration_source": "COACH_POSITION"}]},
    })
    assert result["minutes"]["Z1"] == 210
    assert result["minutes"]["Z2"] == sum(hr_speed.TMAX_RANGES_S["Z2"]) / 120
    assert result["sources"]["Z1"] == "EXPERT_CONTINUOUS_TMAX"
    assert result["references"]["Z1"]["reference_source"] == "COACH_POSITION"
    assert result["status"] == "EXPERT"


def test_available_curve_missing_reference_does_not_silently_become_expert():
    view, _ = curve_view()
    view["hr_model"]["zones"].pop()
    with pytest.raises(capacity.ComponentCapacityUnavailable, match="референтна скорост за Z4"):
        capacity.context_from_speed_view(view)


def test_available_curve_without_hr_mapping_requests_hrmax_and_boundaries():
    view, _ = curve_view()
    view["hr_model"] = None
    with pytest.raises(capacity.ComponentCapacityUnavailable) as caught:
        capacity.context_from_speed_view(view)
    assert caught.value.code == "COMPONENT_CAPACITY_REFERENCE_REQUIRED"
    assert "HRmax" in caught.value.user_message
    assert "пулсовите граници" in caught.value.user_message


def test_capacity_fingerprint_changes_with_curve_reference_not_generation_id():
    view, _ = curve_view()
    first = capacity.context_from_speed_view(view)
    view.update(source_generation_id="new", source_revision=4)
    assert capacity.context_from_speed_view(view)["fingerprint"] == first["fingerprint"]
    view["hr_model"]["zones"][0]["speed_kmh"] *= 1.01
    assert capacity.context_from_speed_view(view)["fingerprint"] != first["fingerprint"]


def test_context_reader_deduplicates_sports_and_does_not_hide_service_failures(monkeypatch):
    from apps.api import model_service
    calls = []
    def unavailable(repository, alias, sport):
        calls.append(sport)
        return {"sport": sport, "status": "UNAVAILABLE"}
    monkeypatch.setattr(model_service, "speed_view", unavailable)
    assert set(capacity.read_contexts(None, "athlete", ["Run", "Run", "NordicSki"])) == {"Run", "NordicSki"}
    assert calls == ["Run", "NordicSki"]
    def failed(*args):
        raise RuntimeError("Storage unavailable")
    monkeypatch.setattr(model_service, "speed_view", failed)
    with pytest.raises(RuntimeError, match="Storage unavailable"):
        capacity.read_contexts(None, "athlete", ["Run"])
