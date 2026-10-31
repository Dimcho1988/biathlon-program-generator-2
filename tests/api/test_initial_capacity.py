"""Real initial-profile reconstruction through both component capacity paths."""
from copy import deepcopy
from datetime import datetime

import pytest

from apps.api import component_load_context as capacity, model_service as service
from apps.api.read_session import ReadSession
from apps.api.speed_load_cache import SpeedLoadCache
from biathlon import initial_speed_profile
from tests.api.test_initial_threshold_integration import NOW, Repository


@pytest.fixture(autouse=True)
def fixed_time(monkeypatch):
    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW.astimezone(tz)
    monkeypatch.setattr(service, "datetime", FixedDatetime)


@pytest.mark.parametrize("sport", ["Run", "NordicSki", "Ride"])
def test_full_and_capacity_views_reconstruct_identical_initial_curve_and_profile(sport):
    repo = Repository()
    for row in repo.calendar["activities"]:
        if row["sport"] == "Run":
            row["sport"] = sport
    session = ReadSession(repo)
    full = service.speed_view(session, "ath-test", sport)
    fast = service.speed_view(session, "ath-test", sport, _capacity_only=True)
    first = capacity.context_from_speed_view(full)
    second = capacity.context_from_speed_view(fast)
    assert first == second
    assert first["status"] == "CURVE"
    assert first["speed_role"] == "RAW_MOVING_SPEED"
    assert first["initial_heuristic"] is True
    assert first["calibration_source"] == "INITIAL_HEURISTIC"
    assert first["zone_profile"] == full["speed_zones"]
    assert first["comparison_parameters"]["speed_role"] == "RAW_MOVING_SPEED"
    assert first["minutes"]["Z5"] == first["minutes"]["Z4"]
    curve = initial_speed_profile.curve_from_view(full)
    for zone, origin in first["sources"].items():
        ref = first["references"][zone]
        if origin == "INITIAL_HEURISTIC":
            assert first["minutes"][zone] == pytest.approx(curve.inverse(ref["reference_speed_kmh"]/3.6)/60)
        else:
            assert ref["reference_reason"] == "BOUNDARY_OUTSIDE_CURVE_DOMAIN"
    assert repo.reads.count("profile") == 1


def test_changed_training_years_refresh_context_and_exact_curve_reconstruction(monkeypatch):
    repo = Repository()
    repo.speed_load_cache_namespace = "initial-profile-integration"
    monkeypatch.setattr(capacity, "_context_cache", SpeedLoadCache())
    first = capacity.read_contexts(repo, "ath-test", ["Run"])["Run"]
    original = service.speed_view(repo, "ath-test", "Run", _capacity_only=True)
    original_curve = initial_speed_profile.curve_from_view(original)
    repo.profile["training_experience_years"] = 12
    second = capacity.read_contexts(repo, "ath-test", ["Run"])["Run"]
    changed = service.speed_view(repo, "ath-test", "Run", _capacity_only=True)
    changed_curve = initial_speed_profile.curve_from_view(changed)
    assert changed["initial_threshold"]["inputs"]["S"] == 12
    assert second["fingerprint"] != first["fingerprint"]
    assert changed_curve.speed(3000) > original_curve.speed(3000)
    assert second["zone_profile"]["v50_kmh"] == pytest.approx(changed_curve.speed(3000)*3.6)
    assert original["initial_threshold"]["inputs"]["S"] == 4
    assert first["zone_profile"]["v50_kmh"] == pytest.approx(original_curve.speed(3000)*3.6)


@pytest.mark.parametrize("mutation", ["wrong_frame", "inactive_policy", "wrong_version"])
def test_component_capacity_rejects_corrupted_initial_provenance(mutation):
    model = deepcopy(service.speed_view(Repository(), "ath-test", "Run", _capacity_only=True))
    if mutation == "wrong_frame":
        model["speed_role"] = "FLAT_EQUIVALENT"
    elif mutation == "inactive_policy":
        model["initial_threshold"]["used_for_zones"] = False
    else:
        model["initial_speed_profile"]["zone_policy_version"] = "future"
    with pytest.raises(ValueError, match="explicit heuristic provenance"):
        capacity.context_from_speed_view(model)
