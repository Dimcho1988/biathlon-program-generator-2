"""Planning uses recorded HR/speed Q without a normative volume floor."""
from copy import deepcopy
from datetime import timedelta

import pytest

from apps.api import planning_history_estimate
from biathlon import load_progression as policy
from biathlon.learning_provenance import eligible_load_rows
from tests.api.test_load_progression import configured
from tests.api.test_training_plan_engine import TODAY, NOW
from apps.api import training_plan_engine as engine


def recording(*, indicator="SPEED", initial=False, unknown=0.):
    source = {"load_selection": {"version": "test"}, "activities": [], "daily": [],
              "strength": {"daily": []}, "quality": {},
              "period_start": (TODAY-timedelta(days=28)).isoformat(), "period_end": TODAY.isoformat()}
    for n in range(1, 29):
        day = (TODAY-timedelta(days=n)).isoformat()
        zones = [{"zone": z, "raw_time_min": (30.-unknown)/5,
                  "equivalent_time_min": (30.-unknown)/5, "effective_load": (30.-unknown)/5}
                 for z in policy.WEEKLY_Q_BOUNDS]
        source["activities"].append({"activity_ref": str(n), "date": day, "sport": "Run",
            "duration_min": 30., "quality_status": "valid" if indicator == "HEART_RATE" else "limited",
            "hr_coverage_percent": 100 if indicator == "HEART_RATE" else 0,
            "zones": zones, "load_source": {"initial_heuristic": initial,
                "recorded_seconds": 1800., "active_seconds": (30.-unknown)*60,
                "rest_seconds": 0., "unknown_seconds": unknown*60,
                "source_seconds": {indicator: (30.-unknown)*60}}})
        source["daily"].extend({"date": day, "zone": z["zone"], "effective_load": z["effective_load"]} for z in zones)
        source["strength"]["daily"].append({"date": day, "effective_load": 0., "real_time_min": 0., "equivalent_time_min": 0.})
    return source


@pytest.mark.parametrize("initial", [False, True])
def test_speed_and_hr_share_recorded_planning_reference_without_hr_coverage(initial):
    p = configured()
    hr, speed = recording(indicator="HEART_RATE"), recording(initial=initial)
    before = deepcopy(speed)
    expected = policy.context(p, hr, engine._daily_rows(hr, TODAY), TODAY)
    actual = policy.context(p, speed, engine._daily_rows(speed, TODAY), TODAY)
    assert actual["history_usable"]
    for z in policy.WEEKLY_Q_BOUNDS:
        assert actual["components"][z]["reference_q"] == expected["components"][z]["reference_q"] == 42.
        assert actual["components"][z]["recorded_coverage_percent"] == 100
        assert actual["components"][z]["reference_selection"] == "OBSERVED"
    if initial:
        assert eligible_load_rows(speed, engine._daily_rows(speed, TODAY)) == []
        assert actual["components"]["Z1"]["measured_coverage_percent"] == 0
    assert speed == before


def test_partial_ledger_keeps_recorded_reference_separate_from_residual_estimate():
    source = recording(unknown=1.5)
    before = deepcopy(source)
    p = configured()
    prepared, evidence = planning_history_estimate.prepare(source, [], p, TODAY)
    assert evidence["supported"] and evidence["estimated"]
    ctx = policy.context(p, prepared, engine._daily_rows(prepared, TODAY), TODAY)
    assert ctx["history_usable"]
    for z in policy.WEEKLY_Q_BOUNDS:
        assert ctx["components"][z]["reference_q"] == pytest.approx(39.9)
        assert ctx["components"][z]["recorded_coverage_percent"] == 95
        assert ctx["completed_cycle"]["components"][z]["weekly_q"] > 39.9
    assert source == before


@pytest.mark.parametrize("damage", ["unbalanced", "missing", "provider"])
def test_invalid_or_missing_ledger_never_becomes_reliable_speed_history(damage):
    source = recording()
    for a in source["activities"]:
        if damage == "unbalanced": a["load_source"]["active_seconds"] += 60
        elif damage == "missing": a.pop("load_source")
        else: a["quality_status"] = "provider_missing"
    ctx = policy.context(configured(), source, engine._daily_rows(source, TODAY), TODAY)
    assert not ctx["history_usable"]
    assert all(ctx["components"][z]["weekly_q"] is None for z in policy.WEEKLY_Q_BOUNDS)


def test_previous_expert_only_anchor_is_filled_from_recorded_speed_after_upgrade():
    p = configured()
    source = recording()
    rows = engine._daily_rows(source, TODAY)
    anchor = policy.context(p, source, rows, TODAY)["anchor"]
    anchor["version"] = "load-progression-v11-common-zone-q"
    for z in policy.WEEKLY_Q_BOUNDS:
        anchor["components"][z].update(weekly_q=None, reference_q=500., source="EXPERT_ONLY")
    original = deepcopy(anchor)
    ctx = policy.context(p, source, rows, TODAY, retained=anchor)
    assert ctx["anchor_reused"] and ctx["history_usable"]
    assert all(ctx["components"][z]["reference_q"] == 42 for z in policy.WEEKLY_Q_BOUNDS)
    assert anchor == original


def test_upgrade_removes_old_floor_without_erasing_saved_observation():
    p = configured()
    source = recording()
    rows = engine._daily_rows(source, TODAY)
    anchor = policy.context(p, source, rows, TODAY)["anchor"]
    anchor["version"] = "load-progression-v11-common-zone-q"
    anchor["components"]["Z1"].update(weekly_q=42., reference_q=240., reference_selection="LOWER_BOUND")
    ctx = policy.context(p, source, rows, TODAY, retained=anchor)
    assert ctx["components"]["Z1"]["reference_q"] == 42
    assert not ctx["reference_is_clamped"]


def test_observed_duration_guides_distribution_without_overriding_component_targets(monkeypatch):
    from tests.api.test_training_plan_engine import Repository, reference_speed
    monkeypatch.setattr(engine.model_service, "speed_view", reference_speed)
    repo = Repository()
    plan = engine.generate_plan(repo, "athlete", configured(availability_mode="AUTO_HISTORY"),
                                start_date=TODAY+timedelta(days=1), now=NOW)
    sessions = [(d,s) for d in plan["days"] for s in d["sessions"]]
    assert sessions
    assert plan["parameters"]["volume_evidence"]["max_session_minutes_by_sport"]["Run"] > 0
    assert plan["parameters"]["time_budget"] is None
    for d,s in sessions:
        assert not any(limit['code'] == 'ACTUAL_SPORT_SESSION_EXPOSURE' for limit in s['dose_evidence']['limits'])
        from tests.api.test_readiness_adaptive_plan_v2 import assert_readiness_dose
        assert_readiness_dose(s)
