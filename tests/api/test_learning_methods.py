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
