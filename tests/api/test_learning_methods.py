from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
import json

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from biathlon.training_methods import resolved_methods
from apps.api.learning_methods import assess_methods, method_descriptor, MAX_OBSERVATIONS, MAX_MEMORY_BYTES
from apps.api.response_monitoring import SessionReport, session_rows, latest_entries
from apps.api.response_service import save_report

TODAY = date(2026, 9, 27)
METHODS = {m["id"]: m for m in resolved_methods({})}
A, B = "END-THR-TIME-01", "END-THR-TIME-01-FLEX"


def observation(n, *, method=A, success=True, duration=40., age=None, **changes):
    key = f"act_{n:032x}"
    day = (TODAY-timedelta(days=age if age is not None else n)).isoformat()
    p = {"activity_ref": key, "rpe": 10, "timing": "DELAYED", "duration_minutes": duration if success else duration*.6,
         "planned_duration_minutes": duration, "execution_reason": "AS_PLANNED" if success else "FATIGUE",
         "execution_comparable": True, "executed_method_id": method, "method_confirmed": True,
         "executed_method": method_descriptor(METHODS[method], "Run"), **changes}
    return ({"kind": "SESSION", "entry_key": key, "revision": 1, "payload": p, "recorded_at": f"{day}T12:00:00Z"},
            {"activity_ref": key, "local_date": day, "sport": "Run", "duration_min": p["duration_minutes"]})


def assess(pairs, **kwargs):
    return assess_methods(entries=[p[0] for p in pairs], source={"activities": [p[1] for p in pairs]}, today=TODAY, **kwargs)


def comparison(duration=40):
    return [observation(i, duration=duration) for i in range(1, 17)] + [observation(i, method=B, success=False, duration=duration) for i in range(17, 33)]


def test_repeated_comparable_completion_produces_bounded_reversible_preference():
    pairs = comparison()
    untouched = deepcopy(pairs)
    output = assess(pairs)
    prefs = {p["method_id"]: p for p in output["preferences"]}
    assert 0 < prefs[A]["score_delta"] <= .25
    assert -.25 <= prefs[B]["score_delta"] < 0
    assert prefs[A]["observations"] == prefs[B]["observations"] == 16
    assert prefs[A]["low"] > prefs[B]["high"]
    assert prefs[A]["duration_min"] == 36 and prefs[A]["duration_max"] == 44
    assert output["summary"]["basis"] == "CONFIRMED_COMPARABLE_COMPLETION"
    assert pairs == untouched


@pytest.mark.parametrize("change", [
    {"method_confirmed": False}, {"executed_method": None}, {"execution_comparable": False},
    {"execution_reason": "TIME"}, {"execution_reason": "CONDITIONS"}, {"execution_reason": "COACH"},
    {"execution_reason": "UNKNOWN"}, {"rpe": None}, {"timing": "UNKNOWN"},
    {"planned_duration_minutes": None}, {"planned_speed_kmh": 12},
])
def test_unconfirmed_noncomparable_missing_and_nontraining_reasons_do_not_teach(change):
    assert assess([observation(1, **change)])["memory"]["observations"] == []


def test_high_rpe_is_not_failure_and_unfinished_as_planned_is_not_success():
    assert assess([observation(1)])["memory"]["observations"][0]["success"] is True
    assert not assess([observation(1, execution_reason="FATIGUE")])["memory"]["observations"]
    assert not assess([observation(1, success=False, execution_reason="AS_PLANNED")])["memory"]["observations"]


def test_future_and_missing_actual_activity_are_not_training_evidence():
    pair = observation(1, age=-1)
    assert not assess([pair])["memory"]["observations"]
    pair = observation(1)
    assert not assess_methods(entries=[pair[0]], source={}, today=TODAY)["memory"]["observations"]
    pair[1]["sport"] = "Ride"
    assert not assess([pair])["memory"]["observations"]


def test_duplicate_revisions_retract_prior_confirmation_and_cannot_inflate_counts():
    pairs = comparison()
    revised = deepcopy(pairs[0][0])
    revised["revision"] = 2
    revised["payload"]["method_confirmed"] = False
    result = assess_methods(entries=[p[0] for p in pairs]*3+[revised], source={"activities": [p[1] for p in pairs]}, today=TODAY)
    assert result["summary"]["observations"] == 31
    assert next(p for p in result["preferences"] if p["method_id"] == A)["observations"] == 15


@pytest.mark.parametrize("difference", ["duration", "speed", "zone", "purpose", "position", "timing"])
def test_different_dose_or_purpose_cannot_create_a_method_preference(difference):
    pairs = comparison()
    for entry, activity in pairs[16:]:
        p = entry["payload"]
        if difference == "duration":
            p["planned_duration_minutes"] = 5
            p["duration_minutes"] = activity["duration_min"] = 3
        elif difference == "speed":
            p["planned_speed_kmh"], p["executed_speed_kmh"] = 15, 12
        elif difference in ("zone", "purpose", "position"):
            p["executed_method"][difference] = {"zone": "Z2", "purpose": "MAINTENANCE", "position": .5}[difference]
        else:
            p["timing"] = "IMMEDIATE"
    output = assess(pairs)
    assert len(output["preferences"]) == 2
    assert all(p["score_delta"] == 0 for p in output["preferences"])


def test_small_samples_and_no_peer_remain_neutral_even_if_all_completed():
    for pairs in ([observation(i) for i in range(1, 25)], [observation(i) for i in range(1, 5)]+[observation(i, method=B, success=False) for i in range(5, 9)]):
        assert all(p["score_delta"] == 0 for p in assess(pairs)["preferences"])


def test_illness_and_lab_flags_exclude_observations_without_becoming_method_failure():
    entry, activity = observation(1)
    illness = {"kind": "DAILY", "entry_key": activity["local_date"], "revision": 1,
               "payload": {"day": activity["local_date"], "pain_or_illness": True}}
    result = assess_methods(entries=[entry, illness], source={"activities": [activity]}, today=TODAY)
    assert not result["memory"]["observations"]
    assert not assess([(entry, activity)], history={"days": [{"day": activity["local_date"],
        "body_observations": {"context_status": "REVIEW_LAB_REFERENCE"}}]})["memory"]["observations"]


def test_legacy_illness_date_comes_from_entry_key_and_invalid_date_is_ignored():
    entry, activity = observation(1)
    illness = {"kind": "DAILY", "entry_key": activity["local_date"], "revision": 1,
               "payload": {"pain_or_illness": True}}
    result = assess_methods(entries=[entry, illness], source={"activities": [activity]}, today=TODAY)
    assert not result["memory"]["observations"]
    illness["entry_key"] = "invalid-date"
    result = assess_methods(entries=[entry, illness], source={"activities": [activity]}, today=TODAY)
    assert result["summary"]["observations"] == 1


def test_retained_observations_survive_rolling_import_but_edits_and_deletion_in_window_retract():
    pair = observation(1, age=120)
    prior = assess([pair])["memory"]
    result = assess_methods(entries=[pair[0]], source={}, today=TODAY, retained=prior)
    assert result["summary"]["observations"] == 1
    revision = deepcopy(pair[0])
    revision["revision"] = 2
    revision["payload"]["method_confirmed"] = False
    result = assess_methods(entries=[revision], source={}, today=TODAY, retained=prior)
    assert result["summary"]["observations"] == 0
    recent = observation(2)
    assert not assess_methods(entries=[recent[0]], source={}, today=TODAY, retained=assess([recent])["memory"])["memory"]["observations"]


def test_retained_archive_is_bounded_and_changed_catalog_does_not_match():
    pairs = [observation(i+1, age=100+i%600) for i in range(MAX_OBSERVATIONS+20)]
    output = assess(pairs)
    assert 0 < output["summary"]["observations"] < MAX_OBSERVATIONS
    assert len(json.dumps(output["memory"]).encode("utf-8")) <= MAX_MEMORY_BYTES
    changed = deepcopy(METHODS[A])
    changed["position"] = .8
    assert method_descriptor(changed, "Run")["version"] != method_descriptor(METHODS[A], "Run")["version"]


def test_archive_byte_budget_keeps_newest_outcome_independent_window_and_replays_exactly():
    pairs = [observation(i+1, age=100+i%600, method=A if i%2 else B, success=bool(i%2))
             for i in range(MAX_OBSERVATIONS+20)]
    # Future-proof the byte limit against long persisted metadata and multibyte
    # values; count limits alone do not protect the enclosing plan JSON limit.
    for entry, _ in pairs:
        entry["payload"]["executed_method"]["extra_context"] = "Контекст 🏔" * 10
    output = assess(pairs)
    memory = output["memory"]
    assert len(json.dumps(memory, ensure_ascii=True).encode("utf-8")) <= MAX_MEMORY_BYTES
    assert len(json.dumps(memory, ensure_ascii=False).encode("utf-8")) <= MAX_MEMORY_BYTES
    newest_ids = [e["entry_key"] for e, a in sorted(pairs, key=lambda pair: (pair[1]["local_date"], pair[0]["entry_key"]))]
    assert [r["id"] for r in memory["observations"]] == newest_ids[-len(memory["observations"]):]
    assert output["summary"]["dropped_this_assessment"] == len(pairs)-len(memory["observations"])
    assert output["summary"]["memory_window_reason"] == "SIZE_LIMIT"
    replay = assess_methods(entries=[p[0] for p in pairs], source={}, today=TODAY, retained=memory)
    assert replay["memory"] == memory
    assert replay["preferences"] == output["preferences"]
    reversed_output = assess(list(reversed(pairs)))
    assert reversed_output["memory"] == memory
    for entry, _ in pairs:
        p = entry["payload"]
        p["execution_reason"] = "FATIGUE" if p["execution_reason"] == "AS_PLANNED" else "AS_PLANNED"
        p["duration_minutes"] = 24 if p["execution_reason"] == "FATIGUE" else 40
    # Keep actual duration consistent after swapping outcomes.
    for entry, activity in pairs:
        activity["duration_min"] = entry["payload"]["duration_minutes"]
    swapped = assess(pairs)
    assert [r["id"] for r in swapped["memory"]["observations"]] == [r["id"] for r in memory["observations"]]


def test_individually_oversized_observation_is_excluded_before_fitting():
    pairs = [observation(1, age=100), observation(2, age=101)]
    pairs[0][0]["payload"]["executed_method"]["extra_context"] = "я" * MAX_MEMORY_BYTES
    output = assess(pairs)
    assert output["summary"]["oversized_observations"] == 1
    assert output["summary"]["observations"] == 1
    assert output["memory"]["observations"][0]["id"] == pairs[1][0]["entry_key"]
    assert output["preferences"][0]["observations"] == 1


class Repository:
    def athlete_settings(self, alias):
        return SimpleNamespace(timezone="Europe/Sofia")

    def activity_detail(self, alias, activity_ref):
        return {"activity_ref": activity_ref, "local_date": "2026-09-26", "sport": "Run"}

    def _json(self, value):
        return value

    def _request(self, method, path, **kwargs):
        if method == "GET":
            assert path.startswith("/onflows_management_entries")
            return []
        self.saved = kwargs["json"]["p_payload"]
        return {"saved": True, "revision": 1}


def test_report_freezes_server_descriptor_and_clears_it_on_unconfirmation():
    repo = Repository()
    payload = {"activity_ref": f"act_{1:032x}", "duration_minutes": 40, "executed_method_id": A, "method_confirmed": True}
    now = datetime(2026, 9, 27, 12, tzinfo=timezone.utc)
    save_report(repo, "ath-test", "SESSION", SessionReport(**payload), "actor", now)
    assert repo.saved["executed_method"] == method_descriptor(METHODS[A], "Run")
    save_report(repo, "ath-test", "SESSION", SessionReport(**{**payload, "method_confirmed": False}), "actor", now)
    assert repo.saved["executed_method"] is None
    with pytest.raises(ValidationError):
        SessionReport(**payload, executed_method={"id": "client-forgery"})
    with pytest.raises(ValidationError):
        SessionReport(activity_ref=payload["activity_ref"], duration_minutes=40, method_confirmed=True)


@pytest.mark.parametrize("method", ["NOT-IN-CATALOG", "ONFLOWS-CONTROLLED-Z4-V2", "END-CROSS-TRAIN-01-RECOVERY"])
def test_confirmed_method_must_be_available_for_current_profile_and_actual_sport(method):
    with pytest.raises(HTTPException) as exc:
        save_report(Repository(), "ath-test", "SESSION", SessionReport(activity_ref=f"act_{1:032x}", duration_minutes=40,
            executed_method_id=method, method_confirmed=True), "actor", datetime(2026, 9, 27, tzinfo=timezone.utc))
    assert exc.value.status_code == 422


def test_execution_metadata_roundtrips_for_ui_and_old_reports_remain_valid():
    entry, activity = observation(1)
    activity["elapsed_time_s"] = 2400
    rows = session_rows([activity], latest_entries([entry]))
    assert rows[0]["execution"]["method_confirmed"] is True
    assert rows[0]["execution"]["executed_method"]["id"] == A
    assert SessionReport(activity_ref=entry["entry_key"], duration_minutes=40).method_confirmed is False


@pytest.fixture
def library_feedback(monkeypatch):
    from apps.api import management_store, model_service
    from biathlon import method_library
    from tests.api.test_sourced_method_library import recipe, view
    profile = {"sport": "NordicSki", "actual_sport": "RollerSki", "discipline": "5000 m",
               "planning_controls": {"training_sports": ["NordicSki", "RollerSki"]}}
    record = recipe()
    calls = []
    monkeypatch.setattr(management_store.ManagementStore, "profile", lambda self, alias: {
        "configured": True, "profile": deepcopy(profile), "revision": 1,
    })
    monkeypatch.setattr(method_library, "entries", lambda: [deepcopy(record)])
    def speed_view(repository, alias, sport):
        calls.append((alias, sport))
        return view(sport)
    monkeypatch.setattr(model_service, "speed_view", speed_view)
    repo = Repository()
    repo.activity_detail = lambda alias, activity_ref: {
        "activity_ref": activity_ref, "local_date": "2026-09-26", "sport": "RollerSki", "duration_min": 40,
    }
    return repo, profile, record, calls


def test_execution_catalog_includes_library_phase_ids_for_each_actual_means(library_feedback):
    from apps.api.learning_methods import execution_catalog
    repo, _, _, calls = library_feedback
    catalog = execution_catalog(repo, "ath-test")
    library = [item for item in catalog if item["id"].startswith("LIB-")]
    assert len(library) == 6
    assert {item["id"] for item in library} == {
        "LIB-TEST-GENERAL_PREPARATION", "LIB-TEST-SPECIAL_PREPARATION", "LIB-TEST-PRECOMPETITION",
    }
    assert {tuple(item["sports"]) for item in library} == {("NordicSki",), ("RollerSki",)}
    assert any("Специално подготвителен" in item["title"] for item in library)
    assert calls == [("ath-test", "NordicSki"), ("ath-test", "RollerSki")]


def test_library_report_freezes_matching_sport_phase_descriptor_and_teaches(library_feedback):
    from biathlon import method_library
    from tests.api.test_sourced_method_library import view
    repo, profile, _, calls = library_feedback
    identifier = "LIB-TEST-SPECIAL_PREPARATION"
    body = SessionReport(activity_ref=f"act_{19:032x}", duration_minutes=40,
                         planned_duration_minutes=40, rpe=7, timing="DELAYED", execution_reason="AS_PLANNED",
                         execution_comparable=True, executed_method_id=identifier, method_confirmed=True)
    save_report(repo, "ath-test", "SESSION", body, "actor", datetime(2026, 9, 27, tzinfo=timezone.utc))
    method = method_library.executable_methods(profile, view("RollerSki"), phase="SPECIAL_PREPARATION")[0]
    assert repo.saved["executed_method"] == method_descriptor(method, "RollerSki")
    assert repo.saved["executed_method"]["id"] == identifier
    assert calls == [("ath-test", "RollerSki")]
    entry = {"kind": "SESSION", "entry_key": body.activity_ref, "revision": 1,
             "payload": deepcopy(repo.saved), "recorded_at": "2026-09-27T00:00:00Z"}
    result = assess_methods(entries=[entry], source={"activities": [repo.activity_detail("ath-test", body.activity_ref)]}, today=TODAY)
    assert result["summary"]["observations"] == 1
    assert result["memory"]["observations"][0]["method"] == repo.saved["executed_method"]
    assert result["preferences"][0]["method_id"] == identifier


def test_library_descriptor_survives_curve_updates_but_not_recipe_or_coach_changes(library_feedback):
    from biathlon import method_library
    from tests.api.test_sourced_method_library import view
    _, profile, _, _ = library_feedback
    method = method_library.executable_methods(profile, view("RollerSki"), phase="SPECIAL_PREPARATION")[0]
    original = method_descriptor(method, "RollerSki")
    updated = deepcopy(method)
    updated["min_work_min"] *= .99
    updated["max_work_min"] *= .99
    updated["interval_template"]["work_seconds"] *= .99
    updated["interval_template"]["total_capacity_ratio"] *= 1.01
    updated["method_library"]["resolution"]["target_speed_kmh"] *= 1.01
    updated["method_library"]["resolution"]["active_test_count"] += 1
    assert method_descriptor(updated, "RollerSki") == original
    edited = deepcopy(method)
    edited["method_library"]["resolution"]["expert_override"] = {"speed_multiplier": [1.02, 1.04]}
    assert method_descriptor(edited, "RollerSki")["version"] != original["version"]
    edited = deepcopy(method)
    edited["method_library"]["prescription"]["recovery"]["seconds"] = [120, 120]
    assert method_descriptor(edited, "RollerSki")["version"] != original["version"]


def test_legacy_confirmation_does_not_read_library_curves(library_feedback):
    from apps.api.learning_methods import confirmed_descriptor
    repo, _, _, calls = library_feedback
    report = SessionReport(activity_ref=f"act_{20:032x}", duration_minutes=40,
                           executed_method_id=A, method_confirmed=True)
    descriptor = confirmed_descriptor(repo, "ath-test", report, {"sport": "Run"})
    assert descriptor["id"] == A and not calls


@pytest.mark.parametrize("blocked", ["global", "method", "intervals", "reference"])
def test_unavailable_library_choices_do_not_fetch_curves(library_feedback, blocked):
    from apps.api.learning_methods import execution_catalog
    repo, profile, record, calls = library_feedback
    if blocked == "global":
        profile["method_library"] = {"enabled": False}
    elif blocked == "method":
        profile["method_library"] = {"overrides": {"TEST": {"enabled": False}}}
    elif blocked == "intervals":
        profile["planning_controls"]["automatic_intervals"] = False
    else:
        record["prescription"] = None
    assert not any(item["id"].startswith("LIB-") for item in execution_catalog(repo, "ath-test"))
    assert not calls


def test_continuous_library_feedback_remains_available_when_automatic_intervals_disabled(library_feedback):
    from apps.api.learning_methods import execution_catalog
    repo, profile, record, calls = library_feedback
    profile["planning_controls"]["automatic_intervals"] = False
    record["prescription"].update(structure="CONTINUOUS", work={"unit": "seconds", "range": [300, 600]},
                                  speed_multiplier=[.85, .85], repetitions=[1, 1])
    assert any(item["id"].startswith("LIB-") for item in execution_catalog(repo, "ath-test"))
    assert calls == [("ath-test", "NordicSki"), ("ath-test", "RollerSki")]


def test_unknown_library_id_never_fetches_curve_or_accepts_descriptor(library_feedback):
    from apps.api.learning_methods import confirmed_descriptor
    repo, _, _, calls = library_feedback
    report = SessionReport(activity_ref=f"act_{21:032x}", duration_minutes=40,
                           executed_method_id="LIB-UNKNOWN-SPECIAL_PREPARATION", method_confirmed=True)
    with pytest.raises(HTTPException) as exc:
        confirmed_descriptor(repo, "ath-test", report, {"sport": "RollerSki"})
    assert exc.value.status_code == 422 and not calls


def test_historical_catalog_archive_survives_new_catalog_without_cross_version_learning():
    pair = observation(1, age=120)
    pair[0]["payload"]["executed_method"]["catalog_version"] = "training-methods-v12"
    pair[0]["payload"]["executed_method"]["version"] = "training-methods-v12:historical-recipe"
    original = assess([pair])["memory"]
    result = assess_methods(entries=[pair[0]], source={}, today=TODAY, retained=original)
    assert result["memory"] == original
    assert result["preferences"][0]["version"] == "training-methods-v12:historical-recipe"
    newer = [observation(index, age=index, method=B, success=False) for index in range(2, 18)]
    combined = assess_methods(entries=[pair[0], *[entry for entry, _ in newer]],
                             source={"activities": [activity for _, activity in newer]}, today=TODAY, retained=original)
    historical = next(item for item in combined["preferences"] if item["method_id"] == A)
    assert historical["observations"] == 1 and historical["comparison_observations"] == 0
    assert historical["score_delta"] == 0
