"""Race duration is derived for planning, never persisted over manual input."""
from copy import deepcopy
from datetime import timedelta
from types import SimpleNamespace
import math
import pytest
from apps.api import race_duration, load_adaptation, management_service, training_plan_engine as engine
from apps.api.management_schemas import ManagementProfile, LoadProgression, PlanningControls
from tests.api.test_training_plan_engine import Repository, TODAY, NOW, profile, reference_speed


def calibrated(repo, alias, sport):
    return {**reference_speed(repo, alias, sport), "status": "CALIBRATED", "active_test_count": 2,
            "active_test_keys": ["test-a", "test-b"], "exploratory_test_count": 0,
            "points": [{"distance_m": 1000, "duration_s": 180}, {"distance_m": 2000, "duration_s": 420},
                       {"distance_m": 10000, "duration_s": 2400}]}


@pytest.mark.parametrize("text,expected", [("7.5 km sprint", 7500), ("7,5 км спринт",7500), ("1500 m",1500),
    ("Маратон",42195), ("half marathon",21097.5), ("4x7.5 km",None), ("5 km / 10 km",None),
    ("sprint",None), ("-5 km",None), ("0 km",None), ("5 km U18",None)])
def test_single_distance_only(text, expected):
    assert race_duration.distance_m(text) == expected


def test_interpolates_calibrated_curve_without_reapplying_correction_or_overwriting_fallback():
    p = profile(discipline="1500 m", race_duration_min=99)
    before = deepcopy(p)
    result = race_duration.resolve(p, calibrated(Repository(),"athlete","Run"))
    expected = math.exp(math.log(180)+math.log(1.5)/math.log(2)*math.log(420/180))/60
    assert result["duration_min"] == pytest.approx(expected, abs=.0001)
    assert result["source"] == "SPEED_DURATION" and result["active_test_keys"] == ["test-a","test-b"]
    assert race_duration.applied(p,result)["race_duration_min"] != 99
    assert p == before


@pytest.mark.parametrize("patch", [{"status":"REFERENCE_ONLY"},{"active_test_count":0},{"exploratory_test_count":1},
    {"sport":"NordicSki"},{"points":[]},{"points":[{"distance_m":1000,"duration_s":float('nan')}]}])
def test_unusable_curve_uses_explicit_manual_fallback(patch):
    p=profile(discipline="1500 m",race_duration_min=5)
    view={**calibrated(Repository(),"athlete","Run"),**patch}
    assert race_duration.resolve(p,view)["source"] == "MANUAL"
    assert race_duration.resolve({**p,"race_duration_min":None},view)["source"] == "UNAVAILABLE"


def test_no_extrapolation_beyond_curve():
    result=race_duration.resolve(profile(discipline="marathon",race_duration_min=150),calibrated(Repository(),"athlete","Run"))
    assert result["source"] == "MANUAL" and result["reason"] == "OUTSIDE_MODEL_RANGE"


def test_draft_and_outlook_use_same_race_sport_estimate_without_mutating_profile(monkeypatch):
    repo=Repository(); repo.active_analysis=lambda _: deepcopy(repo.envelope)
    p=ManagementProfile.model_validate(profile(discipline="1500 m",sport="NordicSki",actual_sport="Run",race_duration_min=90, load_progression=LoadProgression().model_dump(), planning_controls=PlanningControls(accent_mode="AUTO",accent_limit=2).model_dump(mode="json"))).model_dump(mode="json")
    original=deepcopy(p)
    monkeypatch.setattr(engine.model_service,"speed_view",calibrated)
    monkeypatch.setattr(management_service,"ManagementStore",lambda _:SimpleNamespace(profile=lambda _:{"configured":True,"revision":1,"profile":deepcopy(p)}))
    plan=engine.generate_plan(repo,"athlete",p,start_date=TODAY,now=NOW)
    outlook=management_service.outlook(repo,"athlete",now=NOW)["outlook"]
    assert plan["parameters"]["race_duration"] == outlook["race_duration"]
    assert outlook["race_duration"]["sport"] == "NordicSki"
    assert outlook["race_duration"]["duration_min"] < 8
    assert not any(w["code"]=="RACE_DURATION_MISSING" for w in plan["warnings"])
    special = [w for w in outlook["long_term"]["weeks"] if w["phases"] == ["SPECIAL_PREPARATION"]]
    assert special and any(w["accents"] == ["Z5","Z4"] for w in special)
    assert any(w["accents"] != ["Z5","Z4"] for w in special)
    assert plan["long_term"]["weeks"] == outlook["long_term"]["weeks"]
    assert p==original


def report(day, flag, revision=1):
    return {"kind":"DAILY","entry_key":day,"revision":revision,"payload":{"day":day,"pain_or_illness":flag}}


def test_symptom_context_dates_old_reports_and_respects_edits_and_new_reports():
    old=(TODAY-timedelta(days=9)).isoformat()
    entries=[report(old,True)]
    context=load_adaptation.symptom_context(entries,TODAY)
    assert context["report_age_days"]==9 and context["hold_for_reported_illness_or_pain"]
    assert "12.09.2026" in load_adaptation.symptom_message(context)
    assert load_adaptation.assess(entries,TODAY)["hold_for_reported_illness_or_pain"]
    for extra in [report(old,False,2),report(TODAY.isoformat(),False)]:
        assert not load_adaptation.symptom_context(entries+[extra],TODAY)["hold_for_reported_illness_or_pain"]
        assert not load_adaptation.assess(entries+[extra],TODAY)["hold_for_reported_illness_or_pain"]
    assert load_adaptation.symptom_context(entries+[report((TODAY+timedelta(days=1)).isoformat(),False)],TODAY)==context


def test_missing_athlete_settings_preserves_manual_fallback_in_preview_and_outlook(monkeypatch):
    repo=Repository(); repo.settings=None; repo.active_analysis=lambda _:deepcopy(repo.envelope)
    p=ManagementProfile.model_validate(profile(discipline="5000 m",race_duration_min=22)).model_dump(mode="json")
    monkeypatch.setattr(engine.model_service,"speed_view",lambda *args: pytest.fail("No model evaluation without its prerequisites"))
    monkeypatch.setattr(management_service,"ManagementStore",lambda _:SimpleNamespace(profile=lambda _:{"configured":True,"revision":1,"profile":deepcopy(p)}))
    result=race_duration.preview(repo,"athlete",p)
    assert result["source"]=="MANUAL" and result["duration_min"]==22
    assert management_service.outlook(repo,"athlete",now=NOW)["outlook"]["race_duration"]==result
    assert race_duration.preview(repo,"athlete",{**p,"race_duration_min":None})["source"]=="UNAVAILABLE"
