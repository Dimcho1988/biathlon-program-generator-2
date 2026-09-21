from copy import deepcopy
from datetime import timedelta

import pytest
from apps.api import training_plan_engine as engine
from biathlon import training_targets
from biathlon.periodization import build_periodization
from tests.api.test_training_plan_engine import Repository, TODAY, profile


def inputs(repo=None, body=None, limited=False):
    repo, body = repo or Repository(), body or profile()
    rows = engine._daily_rows(repo.envelope["snapshot_payload"]["load_history"], TODAY)
    periodization = build_periodization(body["program_start"], body["program_end"], repo.events, taper_days=body["taper_days"])
    reference = training_targets.development_reference(rows, TODAY, TODAY, 4)
    result = engine._long_term_outlook(body, periodization, reference, repo.accents, repo.preferences, rows, TODAY, limited)
    return result, rows, reference, periodization, body, repo


def test_outlook_reuses_daily_component_goals_without_compounding_or_mutation():
    result, rows, ref, phases, body, repo = inputs()
    original_rows = deepcopy(rows)
    for week in result["weeks"]:
        left = engine.date.fromisoformat(week["start_date"])
        goals = []
        for n in range(week["days"]):
            day = left + timedelta(days=n)
            phase, taper = engine._phase(phases, day)
            index = (day - TODAY).days // 7 % 4
            goals.append(training_targets.component_targets(ref, body, engine._accents(phase, repo.accents), index, 4, phase, taper, False, engine._taper_factor(phases, day)))
        for z in engine.COMPONENTS:
            target = sum(g[z]["target"] for g in goals) / len(goals)
            assert week["components"][z]["target_weekly_effective"] == pytest.approx(target, abs=.001)
            baseline = result["baseline"][z]
            assert week["components"][z]["target_index_7_40"] == pytest.approx((baseline["b50"] + target / 7) / (baseline["b50"] + baseline["c40"]), abs=.001)
    assert rows == original_rows
    assert result["readiness_forecast"] is False
    assert all(w["start_date"] >= TODAY.isoformat() for w in result["weeks"])


def test_camp_does_not_grant_extra_load_but_control_race_reduces_targets():
    plain, *_ = inputs()
    repo = Repository()
    repo.events.append({"event_id": "camp", "event_type": "CAMP", "name": "Лагер", "start_date": TODAY.isoformat(), "end_date": (TODAY + timedelta(days=10)).isoformat()})
    camp, *_ = inputs(repo)
    assert camp["weeks"] == plain["weeks"]
    repo.events.append({"event_id": "control", "event_type": "CONTROL_RACE", "name": "Контролен старт", "start_date": (TODAY + timedelta(days=6)).isoformat(), "end_date": (TODAY + timedelta(days=6)).isoformat()})
    race, *_ = inputs(repo)
    assert race["weeks"][0]["components"]["Z2"]["target_weekly_effective"] < plain["weeks"][0]["components"]["Z2"]["target_weekly_effective"]


def test_missing_history_has_unknown_targets_and_index_not_fake_zero_or_one():
    repo = Repository()
    repo.envelope["snapshot_payload"]["load_history"]["daily"] = []
    repo.envelope["snapshot_payload"]["load_history"]["strength"]["daily"] = []
    result, *_ = inputs(repo, profile(component_targets_weekly={"STR": 30}), limited=True)
    first = result["weeks"][0]["components"]
    assert first["Z1"]["target_weekly_effective"] is None
    assert first["Z1"]["target_index_7_40"] is None
    assert first["STR"]["target_weekly_effective"] is not None
    assert first["STR"]["target_index_7_40"] is None


def test_partial_last_week_is_clipped_to_program_end():
    result, *_ = inputs(body=profile(program_end=(TODAY + timedelta(days=8)).isoformat()))
    assert [w["days"] for w in result["weeks"]] == [7, 2]
    assert result["weeks"][-1]["end_date"] == (TODAY + timedelta(days=8)).isoformat()
