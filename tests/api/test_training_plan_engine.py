"""Behavioral gates for the first current-model draft generator."""
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from apps.api import training_plan_engine as engine
from biathlon import recovery_v2, speed_duration
from biathlon.training_methods import METHODS

NOW = datetime(2026, 9, 21, 9, tzinfo=timezone.utc)
TODAY = NOW.date()
BOUNDS = (100, 125, 145, 160, 175, 190)
ZONES = recovery_v2.ZONES


class Repository:
    def __init__(self):
        self.settings = SimpleNamespace(timezone="Europe/Sofia", hrmax_bpm=190, zone_bounds_bpm=BOUNDS)
        self.entries = []
        self.preferences = {"sessions_per_week": 5, "rest_days": [], "intensity_days": [],
                            "max_key_sessions_per_week": 2, "mesocycle_anchor_date": TODAY.isoformat()}
        self.accents = {"accent_mode": "AUTO", "accent_limit": 2, "manual_components": []}
        self.events = [{"event_type": "MAIN_RACE", "event_id": "race", "name": "Основен старт",
                        "start_date": (TODAY + timedelta(days=120)).isoformat(),
                        "end_date": (TODAY + timedelta(days=120)).isoformat()}]
        values = dict(zip(ZONES, (60, 30, 20, 10, 8, 8)))
        days = [TODAY - timedelta(days=n) for n in range(49, -1, -1)]
        daily = [{"date": d.isoformat(), "zone": z, "effective_load": values[z] if (TODAY-d).days > 7 else 0.}
                 for d in days for z in ZONES if z != "STR"]
        strength = [{"date": d.isoformat(), "effective_load": values["STR"] if (TODAY-d).days > 7 else 0.} for d in days]
        activities = [{"activity_ref": f"act_{i}", "date": d.isoformat(), "sport": "Run", "duration_min": 60,
                       "zones": [{"zone": z, "raw_time_min": 10} for z in ZONES if z != "STR"]}
                      for i, d in enumerate(days) if (TODAY-d).days > 7]
        self.envelope = {"generation_id": "generation-one", "revision": 1,
                         "activities": [{**a, "local_date": a["date"]} for a in activities],
                         "snapshot_payload": {"load_history": {
                             "period_start": days[0].isoformat(), "period_end": TODAY.isoformat(),
                             "quality": {"limited_activities": 0, "excluded_activities": 0},
                             "daily": daily, "activities": activities, "strength": {"daily": strength}}}}

    def athlete_settings(self, alias):
        assert alias == "athlete"
        return self.settings

    def active_activity_calendar(self, alias, start, end):
        assert alias == "athlete"
        return deepcopy(self.envelope)

    def athlete_planning_calendar(self, alias):
        return {"events": deepcopy(self.events)}

    def athlete_planning_profile(self, alias):
        return deepcopy(self.preferences)

    def athlete_mesocycle_accent_preferences(self, alias):
        return deepcopy(self.accents)

    def _request(self, method, path, **kwargs):
        assert method == "GET"
        return deepcopy(self.entries)

    def _json(self, value):
        return value


def profile(**changes):
    return {"schema_version": "management-profile-v1", "sport": "Run", "actual_sport": "Run",
            "program_start": (TODAY-timedelta(days=30)).isoformat(),
            "program_end": (TODAY+timedelta(days=120)).isoformat(),
            "available_minutes": [75] * 7, "taper_days": 7, "recent_weekly_hours": None,
            "building_fraction": .5, "maintenance_fraction": .3, "reentry_fraction": .4,
            "max_key_sessions_per_week": 2, "recovery_session_cap_min": 30,
            "allow_expert_fallback": True, **changes}


def reference_speed(repo, alias, sport):
    return {"status": "REFERENCE_ONLY", "sport": sport, "model_version": speed_duration.VERSION,
            "source_generation_id": repo.envelope.get("generation_id"), "source_revision": repo.envelope.get("revision"),
            "active_test_keys": [], "tests": [], "index_summary": {}}


@pytest.fixture(autouse=True)
def stub_speed(monkeypatch):
    monkeypatch.setattr(engine.model_service, "speed_view", reference_speed)


def generate(repo=None, body=None, start=None):
    return engine.generate_plan(repo or Repository(), "athlete", body or profile(), start_date=start or TODAY+timedelta(days=1), now=NOW)


def sessions(result):
    return [day["session"] for day in result["days"] if day["session"]]


def supported_speed(settings):
    tests = [{"duration_s": 600., "speed_kmh": 22., "maximal": True, "test_mode": "STRICT"},
             {"duration_s": 7200., "speed_kmh": 15., "maximal": True, "test_mode": "STRICT"}]
    curve = speed_duration.calibrated(tests)
    index = (100*settings.zone_bounds_bpm[3]/settings.hrmax_bpm)/(curve.speed(3000)*3.6)
    return {"status": "CALIBRATED", "model_version": speed_duration.VERSION,
            "active_test_keys": ["a", "b"], "tests": [{"entry_key": key, "payload": t} for key, t in zip(("a", "b"), tests)],
            "index_window": {"last_activity_date": TODAY.isoformat()},
            "index_summary": {"Z3": {"index": index, "count": 3}}, "zone_corrections": {}}


def test_generates_real_blocks_and_all_loads_without_mutating_source():
    repo = Repository()
    original = deepcopy(repo.envelope)
    result = generate(repo)
    assert result["status"] == "DRAFT"
    assert len(result["days"]) == 7
    assert sessions(result)
    assert repo.envelope == original
    for session in sessions(result):
        assert session["total_minutes"] == pytest.approx(sum(b["duration_min"] for b in session["blocks"]), abs=.002)
        assert session["main_work_minutes"] == pytest.approx(sum(b["duration_min"] for b in session["blocks"] if b["kind"] == "WORK"), abs=.002)
        assert session["dose_evidence"]["capacity_source"] == "EXPERT_CONTINUOUS_TREF"
        assert session["canonical_effective_load"]["STR"] == 0
        assert session["dose_evidence"]["technical_spill_reference_role"] == "CANONICAL_E_ONLY_NOT_DOSE_CAPACITY"


def test_supported_speed_duration_wins_and_not_multiplied_by_expert_tref():
    settings = Repository().settings
    speed = supported_speed(settings)
    method = next(m for m in METHODS if m["zone"] == "Z3")
    context = engine._capacity_context(speed, settings)
    capacity = engine.capacity_for(method, settings, speed, context, TODAY)
    assert capacity["capacity_source"] == "SPEED_DURATION"
    assert capacity["fallback_reasons"] == []
    assert capacity["capacity_minutes"] == pytest.approx(context[0].duration(capacity["target_hr_bpm"])/60, abs=.002)
    assert capacity["target_speed_kmh"] > 0


def test_one_short_test_cannot_authorize_long_duration_or_sport_transfer():
    settings = Repository().settings
    speed = supported_speed(settings)
    speed["tests"] = speed["tests"][:1]
    method = next(m for m in METHODS if m["zone"] == "Z3")
    capacity = engine.capacity_for(method, settings, speed, engine._capacity_context(speed, settings), TODAY)
    assert capacity["capacity_source"] == "EXPERT_CONTINUOUS_TREF"
    assert "INSUFFICIENT_INDEPENDENT_TEST_DURATIONS" in capacity["fallback_reasons"]
    assert engine.capacity_for(method, settings, speed, engine._capacity_context(speed, settings), TODAY, False) is None


def test_outside_observed_support_and_stale_index_fall_back():
    settings = Repository().settings
    speed = supported_speed(settings)
    speed["index_window"]["last_activity_date"] = (TODAY-timedelta(days=15)).isoformat()
    method = next(m for m in METHODS if m["zone"] == "Z3")
    capacity = engine.capacity_for(method, settings, speed, engine._capacity_context(speed, settings), TODAY)
    assert capacity["capacity_source"] == "EXPERT_CONTINUOUS_TREF"
    assert "STALE_HR_SPEED_INDEX" in capacity["fallback_reasons"]
    speed["index_window"]["last_activity_date"] = TODAY.isoformat()
    method = next(m for m in METHODS if m["zone"] == "Z1")
    capacity = engine.capacity_for(method, settings, speed, engine._capacity_context(speed, settings), TODAY)
    assert "OUTSIDE_OBSERVED_TEST_DURATION_SUPPORT" in capacity["fallback_reasons"]


def test_existing_today_is_not_added_twice_and_counts_in_weekly_budget():
    repo = Repository()
    activity = {"activity_ref": "done", "date": TODAY.isoformat(), "local_date": TODAY.isoformat(),
                "sport": "Run", "duration_min": 80, "zones": []}
    repo.envelope["activities"].append(activity)
    repo.envelope["snapshot_payload"]["load_history"]["activities"].append(activity)
    result = generate(repo, start=TODAY)
    assert result["days"][0]["status"] == "EXISTING_ACTIVITY"
    assert result["days"][0]["session"] is None
    assert result["summary"]["planned_minutes"] + 80 <= result["parameters"]["weekly_minutes_ceiling"] + .002


def test_weekly_report_never_invents_daily_history_or_known_100_percent_recovery():
    repo = Repository()
    repo.envelope = {}
    result = generate(repo, profile(recent_weekly_hours=[3, 3, 3, 3]))
    assert result["status"] == "LIMITED_DRAFT"
    assert result["source"]["history_days"] == 0
    assert sessions(result)
    for day in result["days"]:
        assert all(value is None for value in day["readiness_before"].values())
        if day["session"]:
            assert day["session"]["zone"] == "Z1"
            assert day["session"]["total_minutes"] <= 30


def test_stale_snapshot_blocks_instead_of_projecting_unknown_days_as_rest():
    repo = Repository()
    repo.envelope["snapshot_payload"]["load_history"]["period_end"] = (TODAY-timedelta(days=3)).isoformat()
    result = generate(repo)
    assert result["status"] == "BLOCKED"
    assert not sessions(result)


def test_no_fallback_means_no_guessed_training():
    result = generate(body=profile(allow_expert_fallback=False))
    assert not sessions(result)
    assert any(r["code"] == "CAPACITY_UNAVAILABLE" for day in result["days"] for r in day["rejected_alternatives"])


def test_future_start_does_not_assume_unknown_intervening_rest():
    result = generate(start=TODAY+timedelta(days=4))
    assert result["status"] == "LIMITED_DRAFT"
    assert any(w["code"] == "UNKNOWN_INTERVENING_LOAD" for w in result["warnings"])
    assert all(d["readiness_before"]["Z1"] is None for d in result["days"])


def test_race_load_is_not_treated_as_zero_and_later_days_require_regeneration():
    repo = Repository()
    race_day = TODAY+timedelta(days=3)
    repo.events = [{"event_type": "MAIN_RACE", "event_id": "race", "name": "Race", "start_date": race_day.isoformat(), "end_date": race_day.isoformat()}]
    result = generate(repo)
    race_index = next(i for i, d in enumerate(result["days"]) if d["status"] == "RACE")
    assert all(d["status"] == "REVIEW_REQUIRED" for d in result["days"][race_index+1:])
    assert all(d["session"] is None for d in result["days"][race_index:])
    assert all(v is None for v in result["days"][race_index]["readiness_after"].values())
    assert all(v is None for d in result["days"][race_index+1:] for v in d["readiness_before"].values())


def test_other_sport_volume_does_not_authorize_running_volume():
    repo = Repository()
    for activity in repo.envelope["snapshot_payload"]["load_history"]["activities"]:
        activity["sport"] = "Ride"
    result = generate(repo)
    assert not sessions(result)
    assert result["parameters"]["baseline_weekly_minutes"] == 0
    assert any(w["code"] == "NO_ACTUAL_MODE_EXPOSURE" for w in result["warnings"])


def test_every_prescription_respects_component_budget_after_cascade():
    result = generate()
    assert sessions(result)
    for day in result["days"]:
        if day["session"]:
            for z in ZONES:
                assert day["session"]["canonical_effective_load"][z] <= day["load_budget"]["components"][z]["deficit_effective"] + .002


def test_single_canonical_cascade_and_separate_strength():
    settings = Repository().settings
    blocks = [engine._block("WORK", "Work", "Z3", 10, 160, "Controlled")]
    direct, effective, _ = engine._canonical_load(blocks, settings, [], TODAY)
    assert direct["Z3"] == 10
    assert effective["Z1"] == 10 and effective["Z2"] == 10 and effective["Z3"] == 10
    assert effective["Z4"] == 0 and effective["STR"] == 0
    direct, effective, _ = engine._canonical_load([engine._block("WORK", "Strength", "STR", 10, None, "")], settings, [], TODAY)
    assert effective["STR"] == 10
    assert all(effective[z] == 0 for z in ZONES if z != "STR")


def test_readiness_90_is_permission_not_capacity_fraction_and_low_zone_blocks():
    result = generate()
    for day in result["days"]:
        if day["session"]:
            z = day["session"]["zone"]
            assert day["readiness_before"][z] >= 90
            assert day["session"]["dose_evidence"]["fraction"] in {.3, .4, .5}
    repo = Repository()
    for row in repo.envelope["snapshot_payload"]["load_history"]["daily"]:
        if row["date"] == TODAY.isoformat() and row["zone"] == "Z1":
            row["effective_load"] = 1000.
    fatigued = generate(repo)
    assert any(r["code"] in {"RECOVERY_BELOW_90", "WARMUP_NOT_READY"} for day in fatigued["days"] for r in day["rejected_alternatives"])


def test_taper_is_explicit_cap_not_extra_load_to_refill():
    repo = Repository()
    race_day = TODAY+timedelta(days=7)
    repo.events = [{"event_type": "MAIN_RACE", "event_id": "race", "name": "Race", "start_date": race_day.isoformat(), "end_date": race_day.isoformat()}]
    result = generate(repo)
    assert result["parameters"]["weekly_minutes_ceiling"] <= result["parameters"]["baseline_weekly_minutes"]*.5
    for day in result["days"]:
        if day["session"]:
            assert day["session"]["purpose"] != "BUILDING"
            assert any(limit["code"] == "TAPER_DAILY_WORK_CAP" for limit in day["session"]["dose_evidence"]["limits"])


def test_version_fingerprint_changes_when_coach_fraction_changes():
    a = generate(body=profile(building_fraction=.5))
    b = generate(body=profile(building_fraction=.6))
    assert a["fingerprint"] != b["fingerprint"]


def test_real_z1_classification_floor_is_not_used_as_exercise_target():
    repo = Repository()
    repo.settings = SimpleNamespace(timezone="Europe/Sofia", hrmax_bpm=178,
                                    zone_bounds_bpm=(50, 137, 147, 158, 170, 178))
    result = generate(repo)
    assert sessions(result)
    for session in sessions(result):
        for block in session["blocks"]:
            if block["zone"] == "Z1":
                assert 117 <= block["target_hr_bpm"] < 137
        assert session["canonical_effective_load"]["Z1"] > 0
        assert session["dose_evidence"]["capacity_minutes"] < 600


def test_missing_strength_history_remains_unknown_not_recovered():
    repo = Repository()
    repo.envelope["snapshot_payload"]["load_history"].pop("strength")
    result = generate(repo)
    assert result["status"] == "LIMITED_DRAFT"
    assert result["source"]["component_history_days"]["STR"] == 0
    assert all(d["readiness_before"]["STR"] is None for d in result["days"])


def test_three_week_mesocycle_unloads_in_third_week():
    repo = Repository()
    repo.preferences.update(mesocycle_length_weeks=3,
                            mesocycle_anchor_date=(TODAY-timedelta(days=14)).isoformat())
    result = generate(repo)
    assert result["parameters"]["mesocycle_length_weeks"] == 3
    assert result["parameters"]["mesocycle_factor"] == .78
    assert result["days"][0]["load_budget"]["mesocycle_factor"] == .78
    assert result["days"][-1]["load_budget"]["mesocycle_factor"] == 1.


def test_actual_key_session_preserves_minimum_spacing():
    repo = Repository()
    repo.envelope["snapshot_payload"]["load_history"]["activities"].append(
        {"activity_ref": "key-today", "date": TODAY.isoformat(), "sport": "Run", "duration_min": 30,
         "zones": [{"zone": "Z3", "raw_time_min": 10}]})
    result = generate(repo)
    first = result["days"][0]
    assert first["session"] is None or first["session"]["zone"] != "Z3"
    assert any(r["code"] == "KEY_SESSION_LIMIT" for r in first["rejected_alternatives"])


def test_positive_actual_load_cannot_be_erased_when_calendar_metadata_is_missing():
    repo = Repository()
    for row in repo.envelope["snapshot_payload"]["load_history"]["daily"]:
        if row["date"] == TODAY.isoformat() and row["zone"] == "Z1":
            row["effective_load"] = 30.
    result = generate(repo, start=TODAY)
    assert result["days"][0]["status"] == "EXISTING_ACTIVITY"
    assert result["days"][0]["session"] is None
    assert all(d["status"] == "REVIEW_REQUIRED" for d in result["days"][1:])
    assert any(w["code"] == "ACTUAL_LOAD_WITHOUT_SESSION_METADATA" for w in result["warnings"])
