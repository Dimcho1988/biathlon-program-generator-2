"""Policy changes must invalidate persisted and in-process load identities."""
from datetime import date
from types import SimpleNamespace

import pytest

from apps.api import actual_load_projection, model_service
from biathlon import speed_anchor_policy, speed_equivalence
from tests.test_unified_zone_load import BOUNDS, profile, streams
from tests.api.test_management_lifecycle import system, activate, NOW


@pytest.mark.parametrize("module,attribute", [
    (speed_anchor_policy, "VERSION"), (model_service, "HISTORICAL_SPEED_POLICY"),
])
def test_read_projection_invalidates_when_selection_policy_changes(monkeypatch, module, attribute):
    settings = SimpleNamespace(zone_bounds_bpm=BOUNDS, hrmax_bpm=200, leading_indicator="SPEED")
    calls = []

    def samples(alias, keys):
        calls.append(keys)
        return {"key": {"activity_ref": "run", "shadow_payload": streams(None, 16.)}}

    repo = SimpleNamespace(athlete_settings=lambda _: settings, activity_speed_history_samples=samples,
        speed_load_cache_namespace=attribute)
    inputs = {"alias": "policy-test", "today": date(2026, 10, 24), "entries": [],
        "calendar": {"activities": [{"activity_ref": "run", "sport": "Run", "local_date": "2026-10-23",
                                     "latest_shadow_run_key": "key", "elapsed_time_s": 60.}]}}
    source = {"period_start": "2026-10-23", "period_end": "2026-10-23", "activities": [
        {"activity_ref": "run", "sport": "Run", "date": "2026-10-23", "duration_min": 1.,
         "zones": [{"zone": z, "effective_load": 0.} for z in speed_equivalence.ZONES]}]}
    monkeypatch.setattr(model_service, "speed_view", lambda *a, **k: {
        "sport": "Run", "speed_zones": profile(), "status": "UNAVAILABLE"})
    before = actual_load_projection.reconstruct_history(repo, "policy-test", source, speed_inputs=inputs)
    assert actual_load_projection.reconstruct_history(repo, "policy-test", before, speed_inputs=inputs) is before
    assert len(calls) == 1
    monkeypatch.setattr(module, attribute, getattr(module, attribute)+"-revised")
    after = actual_load_projection.reconstruct_history(repo, "policy-test", before, speed_inputs=inputs)
    assert len(calls) == 2
    assert before["load_selection"]["fingerprint"] != after["load_selection"]["fingerprint"]
    assert before["load_selection"]["selection_policy"] != after["load_selection"]["selection_policy"]
    assert before["activities"] == after["activities"]


def test_full_refresh_policy_change_invalidates_canonical_and_dataset_identity(monkeypatch):
    from apps.api.cloud import InMemorySnapshotRepository
    from apps.api.real_service import refresh
    from intervals_inspector import real_data_source
    from tests.api.test_real_cloud_pipeline import Client, ENV

    dataset_keys = []
    original = real_data_source.load_real_history

    def capture(*args, **kwargs):
        dataset = original(*args, **kwargs)
        dataset_keys.append(dataset.cache_key)
        return dataset

    monkeypatch.setattr(real_data_source, "load_real_history", capture)
    repo = InMemorySnapshotRepository()
    day = date(2026, 8, 15)
    refresh(repo, environ=ENV, client=Client(), period_end=day)
    before = repo.activity_calendar("pilot", day, day)[0]
    old_policy = repo.latest("pilot")["load_history"]["load_selection"]["selection_policy"]
    refresh(repo, environ=ENV, client=Client(), period_end=day)
    assert repo.activity_calendar("pilot", day, day)[0]["latest_canonical_run_key"] == before["latest_canonical_run_key"]
    assert dataset_keys[0] == dataset_keys[1]
    monkeypatch.setattr(speed_anchor_policy, "VERSION", speed_anchor_policy.VERSION+"-revised")
    refresh(repo, environ=ENV, client=Client(), period_end=day)
    after = repo.activity_calendar("pilot", day, day)[0]
    assert after["latest_canonical_run_key"] != before["latest_canonical_run_key"]
    assert dataset_keys[2] != dataset_keys[1]
    assert after["canonical_summary"] == before["canonical_summary"]
    assert repo.latest("pilot")["load_history"]["load_selection"]["selection_policy"] != old_policy


@pytest.mark.parametrize("change", ["source", "policy"])
def test_automatic_adaptation_reconsiders_legacy_empty_plan_once(monkeypatch, system, change):
    from copy import deepcopy
    from apps.api import management_lifecycle as lifecycle

    activate(system)
    # A persisted, already approved empty plan must be reconsidered after a
    # scientific-policy update without requiring a fresh provider generation.
    for day in system.plans.records[-1]["payload"]["plan"]["days"]:
        day.update(session=None, sessions=[], status="REST")
    before = deepcopy(system.plans.records[-1])
    if change == "policy":
        monkeypatch.setattr(speed_anchor_policy, "VERSION", speed_anchor_policy.VERSION+"-revised")
    else:
        system.repo.envelope.update(generation_id="updated-generation", revision=2)
    after = lifecycle.refresh(system.repo, "athlete", automatic=True, now=NOW)
    assert after["revision"] == 2
    if change == "policy":
        # Scientific rule changes preserve the existing approval boundary.
        assert after["operation"] == "REVIEW" and after["payload"]["status"] == "REVIEW_REQUIRED"
        assert after["payload"]["plan"] == before["payload"]["plan"]
        candidate = after["payload"]["proposal"]
    else:
        assert after["operation"] == "ADAPT" and after["payload"]["status"] == "ACTIVE"
        assert after["payload"]["proposal"] is None
        candidate = after["payload"]["plan"]
        assert candidate["automatically_published"] is True
    assert any(lifecycle.day_sessions(d) for d in candidate["days"])
    assert after["payload"]["processed_input_fingerprint"] != before["payload"]["processed_input_fingerprint"]
    assert system.plans.records[0] == before
    assert lifecycle.refresh(system.repo, "athlete", automatic=True, now=NOW) == after
    assert len(system.plans.records) == 2 and system.plans.deferred == 1
