from copy import deepcopy

import pytest

from apps.api import management_lifecycle as lifecycle, management_service as service
from apps.api.planning_snapshot import for_planning, wellness_for_planning
from tests.api.test_management_lifecycle import system, activate
from tests.api.test_training_plan_engine import NOW, TODAY


def wellness(steps=100):
    return [{"date": TODAY.isoformat(), "metrics": {
        "steps": {"value": steps, "unit": "count"},
        "resting_hr": {"value": 50, "unit": "bpm"},
    }}]


@pytest.mark.parametrize("change", ["count", "added_day", "missing", "unit"])
def test_step_only_sync_preserves_current_plan_without_build_or_write(system, monkeypatch, change):
    snapshot = system.repo.envelope["snapshot_payload"]
    snapshot["wellness_calendar"] = wellness()
    activate(system)
    before = deepcopy(system.plans.records[-1])
    if change == "count":
        snapshot["wellness_calendar"][0]["metrics"]["steps"]["value"] = 999
    elif change == "missing":
        del snapshot["wellness_calendar"][0]["metrics"]["steps"]
    elif change == "unit":
        snapshot["wellness_calendar"][0]["metrics"]["steps"]["unit"] = "steps"
    else:
        snapshot["wellness_calendar"].append({"date": "2026-01-01", "metrics": {"steps": {"value": 10, "unit": "count"}}})
    actual_snapshot = deepcopy(snapshot)
    system.repo.envelope.update(generation_id="step-only-reimport", revision=99)
    monkeypatch.setattr(service, "build_draft", lambda *a, **kw: pytest.fail("Steps regenerated a programme"))
    assert lifecycle.current(system.repo, "athlete", now=NOW)["active"]["stale"] is False
    assert lifecycle.refresh(system.repo, "athlete", automatic=True, now=NOW) == before
    assert system.plans.records == [before]
    assert snapshot == actual_snapshot, "Display/energy observations must remain intact"


@pytest.mark.parametrize("metric", ["resting_hr", "hrv", "sleep_duration", "weight", "illness", "future_metric"])
def test_other_daily_observations_still_invalidate_plan(system, metric):
    snapshot = system.repo.envelope["snapshot_payload"]
    snapshot["wellness_calendar"] = wellness()
    activate(system)
    snapshot["wellness_calendar"][0]["metrics"][metric] = {"value": 75, "unit": "test"}
    system.repo.envelope.update(generation_id="changed-observation", revision=99)
    assert lifecycle.current(system.repo, "athlete", now=NOW)["active"]["stale"] is True


def test_projection_preserves_unknown_shapes_and_all_other_snapshot_components():
    calendar = [None, {"date": "2026-01-01", "metrics": None}, {"legacy": 2},
                {"date": "2026-01-01", "metrics": {"steps": 3}, "future": True}]
    snapshot = {"wellness_calendar": calendar, "load_history": {"daily": [2]}, "future": 3}
    before = deepcopy(snapshot)
    result = for_planning(snapshot)
    assert result == {**snapshot, "wellness_calendar": calendar[:3] + [{**calendar[3], "metrics": {}}]}
    assert snapshot == before
    assert wellness_for_planning(None) is None
    assert wellness_for_planning({"unknown": 1}) == {"unknown": 1}


def test_identity_version_invalidates_legacy_full_snapshot_hints(system):
    system.repo.envelope["snapshot_payload"]["wellness_calendar"] = wellness()
    activate(system)
    old = deepcopy(system.plans.records[-1]["payload"]["plan"]["input_snapshot"])
    old["rule_versions"].pop("planning_inputs")
    old["snapshot_fingerprint"] = service._hash(system.repo.envelope["snapshot_payload"])
    current = system.plans.records[-1]["payload"]["plan"]["input_snapshot"]
    assert not lifecycle._inputs_current(current, service._hash(old), old["evaluation_date"], analysis_hints=[old])
