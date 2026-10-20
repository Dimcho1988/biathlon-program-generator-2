from copy import deepcopy
from datetime import date, timedelta
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from apps.api import dependencies, management_service, race_duration, training_plan_engine as engine
from apps.api.cloud import AthletePlanningCalendar, AthleteModelSettings
from apps.api.main import app
from apps.api.schemas import PlanningCalendarEvent
from biathlon import adaptive_methods, execution_ranges, race_course, race_specific
from biathlon.training_methods import resolved_methods
from tests.api.test_management_schedule import body, high_capacity_history, run
from tests.api.test_training_plan_engine import TODAY, NOW
from tests.api.test_load_progression import configured
from tests.api.test_integrated_race_models import real_test


COURSE = {"distance_m": 6000, "surface": "TRAIL", "profile": "UPHILL", "gradient_percent": [5, 7],
          "expected_duration_min": [22, 26], "ascent_m": 360, "specific_sessions_percent": 70, "familiar_terrain": True}


def event(day="2026-10-24", course=COURSE, **changes):
    return {"event_id": "event-main-0001", "event_type": "MAIN_RACE", "name": "Планински старт",
            "start_date": day, "end_date": day, **({"race_course": deepcopy(course)} if course else {}), **changes}


@pytest.mark.parametrize("change", [{"distance_m": float("nan")}, {"surface": []}, {"profile": {}},
    {"gradient_percent": [7, 5]}, {"expected_duration_min": [22]}, {"ascent_m": -1},
    {"familiar_terrain": "yes"}, {"surface": "TRACK"}, {"specific_sessions_percent": 101}, {"ascent_m": None}])
def test_invalid_conditions_are_validation_errors(change):
    with pytest.raises(ValueError):
        race_course.validate({**COURSE, **change})


def test_calendar_old_shape_and_optional_course_roundtrip():
    for row in (event(course=None), event()):
        payload = {"schema_version": "planning-calendar-v1", "events": [row]}
        assert AthletePlanningCalendar.from_mapping(payload).to_payload() == payload
        assert PlanningCalendarEvent.model_validate(row).model_dump(mode="json") == row
    with pytest.raises(ValueError):
        PlanningCalendarEvent.model_validate(event(event_type="CAMP"))


def test_calendar_http_saves_and_reads_course_with_existing_envelope(monkeypatch):
    saved = {}
    repo = SimpleNamespace(athlete_settings=lambda _: AthleteModelSettings((100, 120, 140, 160, 180, 200), "Europe/Sofia"),
        athlete_planning_profile=lambda _: None, athlete_mesocycle_accent_preferences=lambda _: None,
        athlete_planning_calendar=lambda alias: saved.get(alias), latest=lambda _: None,
        save_athlete_planning_calendar=lambda alias, calendar: saved.update({alias: calendar}))
    monkeypatch.setenv("ONFLOWS_SERVICE_TOKEN", "test-token")
    monkeypatch.setattr(dependencies, "repository", lambda: repo)
    client = TestClient(app)
    headers = {"Authorization": "Bearer test-token", "X-OnFlows-Athlete-Alias": "athlete"}
    payload = {"schema_version": "planning-calendar-v1", "events": [event((date.today()+timedelta(days=10)).isoformat())]}
    response = client.put("/api/v2/athlete/planning-calendar", headers=headers, json=payload)
    assert response.status_code == 200, response.text
    loaded = client.get("/api/v2/athlete/planning-calendar", headers=headers)
    assert loaded.json()["calendar"] == response.json()["calendar"] == payload


def test_next_main_race_owns_distance_and_switches_after_it_without_mutating_profile():
    profile = {"sport": "Run", "discipline": "800 m", "race_duration_min": 2}
    original = deepcopy(profile)
    events = [event(), event("2026-11-01", {"distance_m": 10000, "surface": "TRAIL", "profile": "ROLLING", "ascent_m": 700}, event_id="next-main-0002")]
    selected = race_course.apply_event(profile, events, "2026-10-20")
    assert selected["discipline"] == "6000 m"
    evidence = race_duration.resolve(selected)
    assert evidence["duration_range_min"] == [22, 26]
    assert evidence["duration_min"] == 24 and evidence["source"] == "COURSE_EXPECTATION"
    assert race_course.apply_event(profile, events, "2026-10-25")["discipline"] == "10000 m"
    assert profile == original
    events[0].pop("race_course")
    assert race_course.apply_event(profile, events, "2026-10-20")["discipline"] == "800 m"


def test_course_without_time_does_not_call_flat_estimate_a_hill_prediction():
    course = {k: v for k, v in COURSE.items() if k != "expected_duration_min"}
    profile = race_course.apply_event({"sport": "Run", "race_duration_min": 20}, [event(course=course)], "2026-10-20")
    assert race_duration.resolve(profile)["duration_basis"] == "FLAT_REFERENCE_NOT_COURSE_PREDICTION"


def days(count=10):
    return [{"date": f"2026-10-{i+1:02}", "period": "SPECIAL_PREPARATION", "taper": False,
             "sessions": [{"sport": "Run", "purpose": "BUILDING", "zone": "Z3", "method_id": f"method-{i}",
                "blocks": [{"kind": "WORK", "zone": "Z3", "duration_min": 4, "instructions": "Контролирано усилие."}]}]} for i in range(count)]


def test_specific_share_prioritizes_models_and_keeps_dose_and_locked_tasks():
    plan = days()
    plan[-1]["sessions"][0]["method_id"] = "ONFLOWS-RACE-RACE-Z3-V2"
    original = deepcopy(plan)
    race_course.annotate_days(plan, [event()])
    selected = [d for d in plan if d["sessions"][0].get("race_course_guidance")]
    assert len(selected) == 7 and selected[-1] is plan[-1]
    assert "5–7%" in selected[0]["sessions"][0]["race_course_guidance"]["note"]
    assert all(d["sessions"][0]["blocks"][0]["duration_min"] == 4 for d in plan)
    original[0]["locked"] = True
    locked = deepcopy(original[0])
    race_course.annotate_days(original, [event()])
    assert original[0] == locked


def test_taper_never_introduces_unfamiliar_terrain_and_easy_strength_are_not_eligible():
    plan = days(3)
    plan[0]["taper"] = True
    plan[1]["sessions"][0].update(purpose="RECOVERY", zone="Z1")
    plan[2]["sessions"][0]["blocks"][0]["zone"] = "STR"
    race_course.annotate_days(plan, [event(course={**COURSE, "familiar_terrain": False})])
    assert plan[0]["sessions"][0]["race_course_guidance"]["status"] == "FAMILIAR_TERRAIN_REQUIRED"
    assert all("primary_control" not in d["sessions"][0]["blocks"][0] for d in plan)
    assert all("race_course_guidance" not in d["sessions"][0] for d in plan[1:])


def test_four_minutes_at_330_to_320_pace_has_correct_distance_bounds():
    assert execution_ranges.distance_range([240, 240], [3600/210, 3600/200]) == pytest.approx([1142.8571429, 1200])
    assert execution_ranges.distance_range([216, 240], [3600/210, 3600/200]) == pytest.approx([1028.5714286, 1200])


def test_execution_ranges_cannot_increase_budget_or_shorten_minimum_work_and_pauses():
    session = {"blocks": [{"kind": "WORK", "zone": "Z3", "duration_min": 4, "target_speed_kmh": 18,
                            "primary_control": "EFFORT_ON_TERRAIN"},
                           {"kind": "RECOVERY", "zone": "Z1", "duration_min": 3}],
               "dose_evidence": {"minimum_structure_work_minutes": 3.8}, "canonical_effective_load": {"Z3": 8}}
    original = deepcopy(session)
    execution_ranges.annotate(session)
    b = session["blocks"][0]
    assert b["duration_range_seconds"] == [228, 240]
    assert b["target_speed_range_kmh"] == [17.1, 18]
    assert b["distance_basis"] == "FLAT_EQUIVALENT"
    assert b["duration_min"] == 4 and session["canonical_effective_load"] == original["canonical_effective_load"]
    assert session["blocks"][1] == original["blocks"][1]


def test_model_work_time_changes_with_expected_race_time_not_literal_distance():
    profile = configured(discipline="4000 m", age_years=30, training_experience_years=5)
    tests = [real_test(165, 1000/165*3.6), real_test(1020, 5000/1020*3.6)]
    view = {"sport": "Run", "tests": [{"entry_key": str(i), "payload": t} for i, t in enumerate(tests)],
            "active_test_keys": ["0", "1"], "speed_zones": {"status": "AVAILABLE", "zones": [
                {"zone": f"Z{i+1}", "high_kmh": v} for i, v in enumerate([11, 13, 15, 17, None])]}}
    work = []
    for times in ([3, 4], [18, 22], [45, 55]):
        p = race_course.apply_event(profile, [event(course={**COURSE, "expected_duration_min": times})], "2026-10-01")
        ref = race_specific.reference(p, view, race_duration.resolve(p, view), TODAY)
        method = next(m for m in race_specific.methods(p, ref, resolved_methods(p)) if m["race_specific"]["role"] == "RACE")
        lo, hi = method["interval_template"]["work_seconds_range"]
        assert 0 < lo < hi <= 300
        assert method["race_specific"]["speed_range_kmh"][0] < method["race_specific"]["speed_range_kmh"][1]
        short = adaptive_methods.short_variant(method)["interval_template"]
        assert 0 < short["work_seconds_range"][0] < short["work_seconds_range"][1] == short["work_seconds"] <= 30
        work.append(hi)
    assert work[0] < work[1] < work[2]


def test_three_model_repetitions_keep_approved_time_range_and_relative_minimum():
    session = {"blocks": [{"kind": "WORK", "zone": "Z3", "duration_min": 4,
                          "race_specific": {"role": "RACE"}, "duration_range_seconds": [216, 240]} for _ in range(3)],
               "dose_evidence": {"minimum_structure_work_minutes": 12, "min_dose_fraction": .1,
                                 "applied_minimum_capacity_fraction": .3}}
    execution_ranges.annotate(session)
    assert len(session["blocks"]) == 3
    assert all(b["duration_range_seconds"] == [216, 240] for b in session["blocks"])
    session["dose_evidence"]["applied_minimum_capacity_fraction"] = .1
    execution_ranges.annotate(session)
    assert all(b["duration_range_seconds"] == [240, 240] for b in session["blocks"])


def test_generator_and_outlook_use_calendar_course_without_changing_saved_profile(monkeypatch):
    repo = high_capacity_history()
    repo.events = [event((TODAY+timedelta(days=14)).isoformat())]
    p = body()
    original = deepcopy(p)
    plan = run(monkeypatch, p, repo)
    assert plan["parameters"]["race_duration"]["duration_range_min"] == [22, 26]
    sessions = [s for d in plan["days"] for s in d["sessions"]]
    assert sessions
    assert all(s.get("execution_range_policy") or any(b["zone"] in {"STR", "NMS"} for b in s["blocks"]) for s in sessions)
    repo.active_analysis = lambda _: deepcopy(repo.envelope)
    monkeypatch.setattr(management_service, "ManagementStore", lambda _: SimpleNamespace(profile=lambda _: {"configured": True, "revision": 1, "profile": p}))
    outlook = management_service.outlook(repo, "athlete", now=NOW)
    assert outlook["outlook"]["race_duration"]["duration_range_min"] == [22, 26]
    assert p == original
