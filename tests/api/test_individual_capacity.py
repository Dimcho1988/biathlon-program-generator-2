"""User-visible evidence contracts across history, curve and athlete profile."""
from copy import deepcopy
from datetime import datetime, timedelta

import pytest

from apps.api import model_service as service
from biathlon import preliminary_capacity, speed_duration
from tests.api.test_speed_history_window import Repository, NOW


@pytest.fixture(autouse=True)
def fixed_time(monkeypatch):
    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW.astimezone(tz)
    monkeypatch.setattr(service, "datetime", FixedDatetime)


def measured_test(repo, duration, speed):
    repo.test(duration_s=duration, speed_kmh=speed, source="MANUAL",
              maximal=True, comparable=True, vflat_version=None, vflat_config_version=None)


def test_preliminary_speed_needs_real_paired_evidence_and_never_invents_tests():
    repo = Repository()
    empty = service.speed_view(repo, "ath-test", "Run", duration_s=1800)
    assert empty["status"] == "UNAVAILABLE" and empty["points"] == []
    assert empty["functional_profile"]["status"] == "UNAVAILABLE"
    repo.add("paired", -1, value=5.)
    # One observed zone is enough for an explicitly estimated absolute scale.
    for band in repo.summaries["paired"]["trainability_index"]["zones"]:
        band["valid"] = band["name"] == "Z3"
    view = service.speed_view(repo, "ath-test", "Run", duration_s=1800)
    assert view["status"] == "PRELIMINARY" and view["prediction"]["evidence"] == "ESTIMATED"
    assert view["active_test_count"] == 0 and view["tests"] == []
    assert view["functional_profile"]["shape"]["orientation"] is None
    assert view["critical_speed"]["status"] == "INSUFFICIENT_TESTS"
    assert repo.saved == []


def test_single_test_scales_normative_shape_and_preserves_exact_anchor():
    repo = Repository()
    repo.add("paired", -1, value=5.)
    measured_test(repo, 180., 22.)
    view = service.speed_view(repo, "ath-test", "Run", duration_s=180)
    prior = preliminary_capacity.curve_from_summary(view["preliminary_capacity"])
    assert prior is not None
    assert view["prediction"]["speed_kmh"] == pytest.approx(22., abs=1e-8)
    reference = speed_duration.calibrated([])
    ratio = 22. / (reference.speed(180)*3.6)
    for point in view["points"]:
        assert point["speed_kmh"] == pytest.approx(reference.speed(point["duration_s"])*3.6*ratio)
    assert not view["calibration_diagnostics"]["prior_shape_used"]
    assert view["curve_metadata"]["cap_percent"] is None
    assert view["functional_profile"]["status"] == "SINGLE_TEST"
    assert view["correction_applied_fraction"] == 0
    repo.add("new-paired", 0, value=7.)
    changed = service.speed_view(repo, "ath-test", "Run")
    assert [p["speed_kmh"] for p in view["points"]] == [p["speed_kmh"] for p in changed["points"]]


def test_two_real_tests_use_checked_model_and_history_never_moves_them():
    repo = Repository()
    measured_test(repo, 180., 22.)
    measured_test(repo, 720., 18.46)
    first = service.speed_view(repo, "ath-test", "Run", duration_s=1800)
    repo.add("paired", -1, value=6.)
    second = service.speed_view(repo, "ath-test", "Run", duration_s=1800)
    assert first["prediction"]["speed_kmh"] == pytest.approx(16.6097838765, abs=1e-4)
    assert [p["speed_kmh"] for p in first["points"]] == [p["speed_kmh"] for p in second["points"]]
    assert second["curve_metadata"]["mode"] == "TWO_ANCHOR_5PCT"
    assert second["curve_metadata"]["cap_percent"] == 5
    measured = [p for p in second["points"] if p["evidence"] == "MEASURED"]
    assert [(p["duration_s"], p["speed_kmh"]) for p in measured] == pytest.approx([(180., 22.), (720., 18.46)])
    assert second["functional_profile"]["status"] == "TEST_SUPPORTED"


def test_conflicting_accepted_tests_remain_visible_without_a_fake_reference_fallback():
    repo = Repository()
    measured_test(repo, 180., 22.)
    measured_test(repo, 720., 24.)
    original = deepcopy(repo.rows)
    view = service.speed_view(repo, "ath-test", "Run", duration_s=1800)
    assert view["status"] == "CONFLICTING_TESTS"
    assert view["active_test_count"] == 2 and len(view["active_test_keys"]) == 2
    assert view["points"] == [] and view["prediction"] is None
    assert view["functional_profile"]["status"] == "UNAVAILABLE"
    assert repo.rows == original and repo.saved == []


@pytest.mark.parametrize("changes", [
    {"maximal": False}, {"comparable": False}, {"source": "MODEL_GENERATED"},
    {"is_estimated": True}, {"test_mode": "EXPLORATORY", "maximal": False},
])
def test_nonmaximal_or_generated_records_cannot_become_curve_measurements(changes):
    repo = Repository()
    measured_test(repo, 180., 22.)
    repo.rows[0]["payload"].update(changes)
    view = service.speed_view(repo, "ath-test", "Run")
    assert view["active_test_count"] == 0 and view["points"] == []
    assert len(view["tests"]) == 1


def test_multipoint_mode_retains_every_accepted_measurement():
    repo = Repository()
    reference = speed_duration.calibrated([])
    for t in (60., 180., 1200., 7200.):
        measured_test(repo, t, reference.speed(t)*3.6*.7)
    view = service.speed_view(repo, "ath-test", "Run")
    assert view["curve_metadata"]["mode"] == "MULTIPOINT_C1_5PCT"
    assert view["curve_metadata"]["cap_percent"] == 5
    assert len(view["functional_profile"]["test_points"]) == 4
    for t in (60., 180., 1200., 7200.):
        point = next(p for p in view["points"] if p["duration_s"] == t)
        assert point["speed_kmh"] == pytest.approx(reference.speed(t)*3.6*.7)
        assert point["evidence"] == "MEASURED" and not point["capped"]
    assert view["curve_metadata"]["measured_window_s"] == [60., 7200.]
    assert any(p["evidence"] == "INTERPOLATED" for p in view["points"])
    assert any(p["evidence"] == "EXTRAPOLATED" for p in view["points"])
    repo.add("paired", -1, value=6.)
    changed = service.speed_view(repo, "ath-test", "Run")
    assert [p["speed_kmh"] for p in view["points"]] == [p["speed_kmh"] for p in changed["points"]]
