"""Dated structure changes preserve the current canonical dosing constraints."""
from copy import deepcopy
from datetime import timedelta

import pytest
from pydantic import ValidationError

from apps.api.training_structure_schemas import TrainingStructureDirective
from biathlon import training_structure as structure
from tests.api.test_training_plan_engine import (Repository, TODAY, engine, generate, profile,
                                                reference_speed)


def directive(*, days=7, mode="REPEAT", start=None, method="END-THR-LONG-01"):
    first = start or TODAY+timedelta(days=1)
    value = {"id": "repeat-1", "name": "Състезателна подготовка", "mode": mode,
             "start_date": first.isoformat(),
             "days": [{"offset": n, "sessions": [{"slot": 1, "sport": "Run", "method_ids": [method]}]
                       if n % 7 < 5 else []} for n in range(days)]}
    if mode != "SESSION":
        source_start = TODAY-timedelta(days=days)
        value["source"] = {"start_date": source_start.isoformat(), "end_date": (TODAY-timedelta(days=1)).isoformat(),
                           "plan_revisions": [3, 4] if days == 14 else [4]}
    return TrainingStructureDirective.model_validate(value).model_dump(mode="json")


@pytest.mark.parametrize("days", [7, 14])
def test_source_structure_has_finite_scope_and_preserves_rest_and_session_sequence(days):
    request = directive(days=days)
    request["days"][0]["sessions"].append({"slot": 2, "sport": "Run", "method_ids": ["END-Z1-01"]})
    TrainingStructureDirective.model_validate(request)
    original = deepcopy(request)
    dated = structure.requests({"training_structure": [request]})
    first = TODAY+timedelta(days=1)
    schedule = [(first+timedelta(days=n), 0, 1) for n in range(days+3)]
    scheduled = structure.slots(schedule, dated, {}, [90]*7)
    counts = {d: count for d, _, count in scheduled}
    assert counts[first] == 2
    assert counts[first+timedelta(days=5)] == 0
    assert counts[first+timedelta(days=days)] == 1
    assert first+timedelta(days=days) not in dated
    assert request == original


@pytest.mark.parametrize("change", [
    lambda d: d["days"][0]["sessions"][0].update(total_minutes=60),
    lambda d: d["days"][0]["sessions"][0].update(speed_kmh=15),
    lambda d: d["days"].pop(),
    lambda d: d["days"][1].update(offset=0),
    lambda d: d["days"][0]["sessions"][0].update(slot=2),
    lambda d: d["source"].update(plan_revisions=[4, 4]),
])
def test_structure_rejects_doses_and_ambiguous_calendar(change):
    value = directive()
    change(value)
    with pytest.raises(ValidationError):
        TrainingStructureDirective.model_validate(value)


def test_session_intent_preserves_other_slots_and_current_availability_limits():
    value = directive(days=1, mode="SESSION")
    value["days"][0]["sessions"][0]["slot"] = 2
    dated = structure.requests({"training_structure": [value]})
    day = TODAY+timedelta(days=1)
    schedule = [(day, 0, 1)]
    assert structure.slots(schedule, dated, {}, [90]*7) == [(day, 0, 2), (day, 1, 2)]
    assert structure.requested_session(dated, day, 0) is None
    assert structure.slots(schedule, dated, {"planning_controls": {"sessions_by_day": [1]*7}}, [90]*7) == schedule
    assert structure.slots(schedule, dated, {}, [0]*7) == [(day, 0, 0)]


def test_session_updates_can_preserve_other_explicit_slots_on_same_date():
    value = directive(days=1, mode="SESSION")
    value["days"][0]["sessions"].append({"slot": 3, "method_ids": ["another"], "sport": "Run"})
    TrainingStructureDirective.model_validate(value)
    value["days"][0]["sessions"].append({"slot": 3, "method_ids": ["duplicate-slot"], "sport": "Run"})
    with pytest.raises(ValidationError, match="distinct, ordered"):
        TrainingStructureDirective.model_validate(value)


def test_current_method_identity_keeps_source_and_personal_ids_and_ordered_combination():
    first = {"id": "LIB-PERSONAL-fast-GENERAL", "method_library_id": "PERSONAL-fast", "structure": "LIBRARY_INTERVALS"}
    second = {"id": "interval-SHORT", "structure": "MODEL_INTERVALS", "developmental_variant": True}
    combo = {"id": "combo", "structure": "COMBINED_METHODS", "combined_methods": [first, second]}
    assert structure.matching_method(first, ["PERSONAL-fast"])
    assert structure.matching_method(second, ["interval"])
    assert not structure.matching_method(first, ["PERSONAL"])
    assert structure.matching_method(combo, ["PERSONAL-fast", "interval"])
    assert not structure.matching_method(combo, ["interval", "PERSONAL-fast"])
    session = {"method_id": "old-combo", "total_minutes": 999,
               "method_library": {"components": [{"id": "PERSONAL-fast"}, {"id": "interval"}]}}
    assert structure.session_method_ids(session) == ["PERSONAL-fast", "interval"]
    pair = {"id": "threshold-DOUBLE-threshold", "structure": "THRESHOLD_LONG", "double_threshold": True,
            "method_library": {"id": "threshold"}}
    assert not structure.matching_method(pair, ["threshold"])
    split = {"method_id": "threshold-DT-SHORT", "double_threshold_part": 2,
             "method_library": {"id": "threshold"}}
    assert structure.session_method_ids(split) == ["threshold-DT-SHORT"]


def test_overlapping_requests_are_rejected_even_outside_api():
    item = directive()
    with pytest.raises(ValueError, match="Overlapping"):
        structure.requests({"training_structure": [item, item]})


@pytest.mark.parametrize("requested,selected,introductory", [("interval-SHORT", "interval", False), ("interval", "interval-SHORT", True)])
def test_changed_introductory_form_is_disclosed_even_when_parent_identity_matches(requested, selected, introductory):
    request = directive(days=1, mode="SESSION", method=requested)
    dated = structure.requests({"training_structure": [request]})
    preference = request["days"][0]["sessions"][0]
    method = {"id": selected, "structure": "MODEL_INTERVALS", "developmental_variant": introductory}
    session = {"method_id": selected, "sport": "Run", "method_library": {"id": selected}}
    structure.mark_session(session, preference, 1, method)
    day = {"date": request["start_date"], "status": "TRAINING", "sessions": [session], "explanation": "Текуща доза."}
    structure.annotate([day], dated)
    assert session["structure_identity_matches"]
    assert day["structure_request"]["status"] == "ADJUSTED"
    assert day["structure_request"]["adjustments"][0]["code"] == "METHOD_FORM_ADAPTED"


@pytest.fixture(autouse=True)
def current_speed(monkeypatch):
    monkeypatch.setattr(engine.model_service, "speed_view", reference_speed)


def test_current_availability_recomputes_dose_for_requested_method_without_old_dose():
    request = directive(days=1, mode="SESSION")
    normal = generate(body=profile(training_structure=[request]))["days"][0]
    constrained = generate(body=profile(training_structure=[request], available_minutes=[40]*7))["days"][0]
    assert normal["structure_request"]["status"] == "APPLIED"
    assert constrained["structure_request"]["status"] == "APPLIED"
    assert normal["session"]["method_id"] == constrained["session"]["method_id"] == "END-THR-LONG-01"
    assert 0 < constrained["session"]["total_minutes"] <= 40 < normal["session"]["total_minutes"]
    assert constrained["session"]["canonical_effective_load"] != normal["session"]["canonical_effective_load"]
    assert constrained["structure_request"]["dose_policy"] == "RECOMPUTED_FROM_CURRENT_STATE"
    assert all("structure_request" not in day for day in generate()["days"])


@pytest.mark.parametrize("priority", ["UNAVAILABLE", "MAIN_RACE", "RECORDED"])
def test_calendar_and_recorded_activity_outrank_structure(priority):
    repo = Repository()
    target = TODAY
    if priority == "RECORDED":
        repo.envelope["activities"].append({"activity_ref": "done", "local_date": target.isoformat(), "sport": "Run"})
    else:
        repo.events.append({"event_id": "override", "event_type": priority, "name": "Текущ календар",
                            "start_date": target.isoformat(), "end_date": target.isoformat()})
    plan = generate(repo, profile(training_structure=[directive(days=1, mode="SESSION", start=target)]), start=target)
    day = plan["days"][0]
    assert day["session"] is None
    assert day["status"] == {"RECORDED": "EXISTING_ACTIVITY", "MAIN_RACE": "RACE", "UNAVAILABLE": "UNAVAILABLE"}[priority]
    assert day["structure_request"]["status"] == "ADJUSTED"
    assert day["structure_request"]["adjustments"][0]["reason"]


def test_removed_or_other_athlete_method_is_explicitly_adjusted_without_fabricated_dose():
    request = directive(days=1, mode="SESSION", method="PERSONAL-other-athlete")
    day = generate(body=profile(training_structure=[request]))["days"][0]
    assert day["session"] is None
    assert day["structure_request"]["status"] == "ADJUSTED"
    assert day["structure_request"]["adjustments"][0]["code"] == "STRUCTURE_SESSION_UNAVAILABLE"
    assert any(r["code"] == "STRUCTURE_METHOD_UNAVAILABLE"
               for r in day["structure_request"]["adjustments"][0]["constraints"])


def test_fixed_full_week_preserves_rest_and_only_uses_requested_execution_methods():
    original = generate()
    request = directive(mode="FIX")
    request["days"] = [{"offset": n, "sessions": [
        {"slot": s.get("slot", slot), "method_ids": structure.session_method_ids(s), "sport": s["sport"]}
        for slot, s in enumerate(day["sessions"], 1)]} for n, day in enumerate(original["days"])]
    changed = generate(body=profile(training_structure=[request]))
    for offset, (source, day) in enumerate(zip(original["days"], changed["days"])):
        assert day["structure_request"]["dose_policy"] == "RECOMPUTED_FROM_CURRENT_STATE"
        if not source["sessions"]:
            assert not day["sessions"]
        for session in day["sessions"]:
            expected = request["days"][offset]["sessions"][session["slot"]-1]
            assert structure.session_method_ids(session) == expected["method_ids"]
            assert session["sport"] == expected["sport"]


def test_requesting_third_slot_cannot_bypass_microcycle_session_limit():
    from tests.api.test_management_schedule import body, high_capacity_history
    request = directive(days=1, mode="SESSION", start=TODAY)
    request["days"][0]["sessions"][0]["slot"] = 3
    current = body(sessions_per_week=1, sessions_by_day=[3]*7)
    current["training_structure"] = [request]
    plan = generate(high_capacity_history(), current, start=TODAY)
    first = plan["days"][0]
    assert len(first["sessions"]) <= 1
    assert first["structure_request"]["status"] == "ADJUSTED"
    assert "максимумът" in first["structure_request"]["adjustments"][0]["reason"]
    assert "structure_outcomes" not in first
    assert all(s.get("structure_slot") != 3 for s in first["sessions"])
    counts = {}
    anchor = TODAY.fromisoformat(current["program_start"])
    for day in plan["days"]:
        cycle = (TODAY.fromisoformat(day["date"])-anchor).days//7
        counts[cycle] = counts.get(cycle, 0)+len(day["sessions"])
    assert max(counts.values()) <= 1
