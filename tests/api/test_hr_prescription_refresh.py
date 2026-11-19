"""HR calibration replaces stale current-day doses through the canonical planner."""
from copy import deepcopy

import pytest

from apps.api import training_plan_engine as engine
from tests.api.test_management_schedule import body, completed_activity, high_capacity_history, run
from tests.api.test_training_plan_engine import NOW, TODAY


@pytest.mark.parametrize("change", ["maximum", "bounds", "history", "missing_evidence"])
@pytest.mark.parametrize("completed", [False, True])
def test_stale_current_day_uses_new_doses_and_keeps_recorded_execution(monkeypatch, change, completed):
    repo = high_capacity_history()
    repo.settings.hr_settings_revision = "calibration-1"
    if completed:
        completed_activity(repo, "morning")
    profile = body(sessions_per_week=13, sessions_by_day=[2, 2, 2, 2, 2, 2, 1])
    profile["max_key_sessions_per_week"] = 0
    locked = deepcopy(run(monkeypatch, profile, repo)["days"][0])
    assert locked["sessions"]
    if change == "maximum":
        repo.settings.hrmax_bpm = 187
    elif change == "bounds":
        repo.settings.zone_bounds_bpm = (96, 121, 141, 156, 171, 190)
    elif change == "history":
        repo.settings.hr_settings_revision = "calibration-2"
    else:
        for session in locked["sessions"]:
            session["dose_evidence"].pop("dose_settings")
    before, original = deepcopy(locked), deepcopy(repo.envelope)
    expected = engine.generate_plan(repo, "athlete", profile, start_date=TODAY, now=NOW)
    actual = engine.generate_plan(repo, "athlete", profile, start_date=TODAY, now=NOW, locked_day=locked)
    assert actual == expected
    today = actual["days"][0]
    assert not today.get("locked") and today["sessions"]
    assert actual["summary"]["actual_sessions"] == int(completed)
    assert today.get("activity_refs", []) == (["morning"] if completed else [])
    for day in actual["days"]:
        for session in day["sessions"]:
            assert session["dose_evidence"]["dose_settings"]["hrmax_bpm"] == repo.settings.hrmax_bpm
            assert session["dose_evidence"]["dose_settings"]["zone_bounds_bpm"] == list(repo.settings.zone_bounds_bpm)
            assert session["dose_evidence"]["dose_settings"]["hr_settings_revision"] == repo.settings.hr_settings_revision
    assert locked == before and repo.envelope == original


def test_unchanged_calibration_still_preserves_the_current_day(monkeypatch):
    repo = high_capacity_history()
    profile = body(sessions_per_week=7)
    profile["max_key_sessions_per_week"] = 0
    locked = run(monkeypatch, profile, repo)["days"][0]
    actual = engine.generate_plan(repo, "athlete", profile, start_date=TODAY, now=NOW, locked_day=locked)
    assert actual["days"][0]["locked"]
    assert actual["days"][0]["sessions"] == locked["sessions"]
