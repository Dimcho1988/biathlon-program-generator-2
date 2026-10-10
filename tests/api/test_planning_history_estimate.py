from copy import deepcopy
from datetime import timedelta

import pytest

from apps.api import planning_history_estimate as estimate, training_plan_engine as engine
from apps.api.management_lifecycle import _eligible
from biathlon import load_progression
from tests.api.test_load_progression import configured, observed
from tests.api.test_training_plan_engine import TODAY, NOW, reference_speed, profile
from tests.api.test_readiness_adaptive_plan_v2 import assert_readiness_dose


def incomplete(count=5):
    repo, source, _ = observed()
    for activity in source["activities"][:count]:
        activity.update(quality_status="limited", hr_coverage_percent=50.)
        for row in activity["zones"]:
            row["raw_time_min"] /= 2
            row["equivalent_time_min"] /= 2
    source["quality"]["limited_activities"] = count
    return repo, source


def test_partial_hr_preserves_measured_data_and_adds_only_missing_minutes():
    repo, source = incomplete()
    original = deepcopy(source)
    ledger, info = estimate.prepare(source, repo.envelope["activities"], configured(), TODAY)
    assert info["supported"] and info["estimated"]
    assert info["estimated_activity_count"] == 5
    for evidence in info["activities"]:
        assert sum(evidence["estimated_q"].values()) == pytest.approx(evidence["missing_minutes"])
        estimated = next(a for a in ledger["activities"] if a["activity_ref"] == evidence["activity_ref"])
        measured = next(a for a in source["activities"] if a["activity_ref"] == evidence["activity_ref"])
        for new, old in zip(estimated["zones"], measured["zones"]):
            assert new["equivalent_time_min"] == pytest.approx(old["equivalent_time_min"] + new["planning_estimated_q"])
            assert new["effective_load"] == estimated["component_load"]["effective"][new["zone"]]
            assert new["effective_load"] == pytest.approx(
                new["planning_measured_effective_load"] + new["planning_estimated_effective_load"])
    assert source == original
    assert ledger["quality"] == source["quality"]
    assert any(r.get("planning_estimated_effective_load", 0) > 0 for r in ledger["daily"])
    again, repeat = estimate.prepare(source, repo.envelope["activities"], configured(), TODAY)
    assert again == ledger and repeat == info


def test_dimitar_style_partial_history_produces_activatable_estimated_draft(monkeypatch):
    monkeypatch.setattr(engine.model_service, "speed_view", reference_speed)
    repo, source = incomplete()
    original = deepcopy(repo.envelope)
    result = engine.generate_plan(repo, "athlete", configured(), start_date=TODAY+timedelta(days=1), now=NOW)
    assert result["summary"]["sessions"] > 0
    assert result["activation_eligible"] and _eligible(result)
    assert result["source"]["readiness_known"] is False
    assert result["source"]["readiness_basis"] == "ESTIMATED_LOAD"
    assert result["long_term"]["basis"] == "ESTIMATED_PLANNING_REFERENCE_FROZEN"
    assert any(w["components"]["Z1"]["target_weekly_q"] for w in result["long_term"]["weeks"])
    components = result["parameters"]["load_progression"]["components"]
    assert components["Z1"]["recent_observed_q"] > 0
    assert components["Z1"]["recent_estimated_q"] >= components["Z1"]["recent_observed_q"]
    assert result["parameters"]["load_progression"]["history_usable"] is True
    assert components["Z1"]["recent_estimated_q"] > 0
    assert repo.envelope == original


def test_estimated_automatic_history_starts_with_recorded_time_not_expert_q_total(monkeypatch):
    monkeypatch.setattr(engine.model_service, "speed_view", reference_speed)
    repo, _ = incomplete(30)
    body = configured(availability_mode="AUTO_HISTORY")
    result = engine.generate_plan(repo, "athlete", body, start_date=TODAY+timedelta(days=1), now=NOW)
    parameters = result["parameters"]
    budget = parameters["time_budget"]
    assert budget["basis"] == "RECORDED_HISTORY_INITIAL_ENVELOPE"
    assert budget["period_limit_minutes"] == parameters["historical_training_weekly_minutes"]
    assert 0 < result["summary"]["planned_minutes"] <= budget["period_limit_minutes"] + .002
    assert parameters["volume_governor"] == "COMPONENT_7_40"
    # Time is a scheduling envelope. Expert references and Recovery remain
    # authoritative within it; no Q or capacity is rescaled to fit the clock.
    assert parameters["load_progression"]["components"]["Z1"]["reference_q"] >= load_progression.WEEKLY_Q_BOUNDS["Z1"][0]
    for day in result["days"]:
        for session in day["sessions"]:
            assert_readiness_dose(session)


@pytest.mark.parametrize("hours", [1., 10.])
def test_explicit_weekly_target_replaces_estimated_history_time_envelope(monkeypatch, hours):
    monkeypatch.setattr(engine.model_service, "speed_view", reference_speed)
    repo, _ = incomplete()
    body = configured(availability_mode="AUTO_HISTORY")
    automatic = engine.generate_plan(repo, "athlete", body, start_date=TODAY+timedelta(days=1), now=NOW)
    body["planning_controls"]["weekly_target_hours"] = hours
    explicit = engine.generate_plan(repo, "athlete", body, start_date=TODAY+timedelta(days=1), now=NOW)
    assert explicit["parameters"]["time_budget"]["period_limit_minutes"] == hours*60
    assert explicit["summary"]["planned_minutes"] <= hours*60 + .002
    assert "basis" not in explicit["parameters"]["time_budget"]
    assert explicit["parameters"]["load_progression"]["components"] == automatic["parameters"]["load_progression"]["components"]


def test_initial_estimated_time_envelope_is_prorated_and_counts_actual_activity(monkeypatch):
    monkeypatch.setattr(engine.model_service, "speed_view", reference_speed)
    repo, source = incomplete(30)
    actual = {"activity_ref": "today", "date": TODAY.isoformat(), "sport": "Run", "duration_min": 45,
              "zones": [{"zone": z, "raw_time_min": 45. if z == "Z1" else 0.,
                         "equivalent_time_min": 45. if z == "Z1" else 0.} for z in estimate.ZONES]}
    source["activities"].append(actual)
    repo.envelope["activities"].append({**actual, "local_date": actual["date"]})
    body = configured(availability_mode="AUTO_HISTORY", horizon_mode="MANUAL", program_end=(TODAY+timedelta(days=2)).isoformat())
    result = engine.generate_plan(repo, "athlete", body, start_date=TODAY, now=NOW)
    budget = result["parameters"]["time_budget"]
    assert budget["period_limit_minutes"] == pytest.approx(result["parameters"]["historical_training_weekly_minutes"]*3/7, abs=.001)
    assert budget["actual_minutes"] == 45
    assert budget["actual_minutes"] + budget["planned_minutes"] <= budget["period_limit_minutes"] + .002


@pytest.mark.parametrize("partial_count", [0,5])
def test_measured_automatic_history_keeps_component_governance_without_time_envelope(monkeypatch, partial_count):
    monkeypatch.setattr(engine.model_service, "speed_view", reference_speed)
    repo, _ = incomplete(partial_count)
    result = engine.generate_plan(repo, "athlete", configured(availability_mode="AUTO_HISTORY"), start_date=TODAY+timedelta(days=1), now=NOW)
    assert result["parameters"]["time_budget"] is None
    assert result["parameters"]["weekly_minutes_ceiling"] is None


def test_estimated_history_preserves_lower_manual_availability(monkeypatch):
    monkeypatch.setattr(engine.model_service, "speed_view", reference_speed)
    repo, _ = incomplete()
    result = engine.generate_plan(repo, "athlete", configured(availability_mode="MANUAL", available_minutes=[30]*7),
                                  start_date=TODAY+timedelta(days=1), now=NOW)
    assert result["parameters"]["time_budget"]["period_limit_minutes"] == 210
    assert result["summary"]["planned_minutes"] <= 210
    assert all(sum(s["total_minutes"] for s in d["sessions"]) <= 30 for d in result["days"])


@pytest.mark.parametrize("changes", [{"duration_min": None}, {"quality_status": "provider_missing"}, {"hr_coverage_percent": None}])
def test_unknown_duration_provider_or_coverage_never_becomes_known(changes, monkeypatch):
    monkeypatch.setattr(engine.model_service, "speed_view", reference_speed)
    repo, source = incomplete(1)
    source["activities"][0].update(changes)
    result = engine.generate_plan(repo, "athlete", configured(), start_date=TODAY+timedelta(days=1), now=NOW)
    assert not result["activation_eligible"] and not _eligible(result)
    assert result["source"]["readiness_basis"] == "UNKNOWN"


def test_duration_only_excluded_activity_is_restored_for_planning_not_measurement():
    repo, source = incomplete(0)
    missing = deepcopy(source["activities"].pop())
    ref = missing["activity_ref"]
    source["quality"]["excluded_activities"] = 1
    catalog = [{**missing, "local_date": missing["date"], "quality_status": "excluded",
                "hr_coverage_percent": 0., "quality_reason": "Липсва надежден HR поток", "zones": []}]
    ledger, info = estimate.prepare(source, catalog, configured(), TODAY)
    assert info["supported"] and info["estimated"]
    assert any(a["activity_ref"] == ref for a in ledger["activities"])
    assert not any(a["activity_ref"] == ref for a in source["activities"])
    bad, failure = estimate.prepare(source, [{**catalog[0], "quality_reason": "API обработката не завърши безопасно"}], configured(), TODAY)
    assert not failure["supported"]


def test_raw_pinned_catalog_supplies_excluded_recorded_duration_without_losing_run_key():
    repo, source = incomplete(0)
    missing = deepcopy(source["activities"].pop())
    source["quality"]["excluded_activities"] = 1
    payload = {**missing, "duration_min": None, "canonical_summary": {"duration_min": 60.},
               "quality_status": "excluded", "hr_coverage_percent": 25.,
               "quality_reason": "Липсва надежден HR поток", "zones": []}
    catalog = [{"catalog_payload": payload, "activity_ref": missing["activity_ref"],
                "local_date": missing["date"], "shadow_run_key": "pinned-run"}]
    normalized = estimate.normalized_calendar(catalog)
    assert normalized[0]["duration_min"] == 60
    assert normalized[0]["latest_shadow_run_key"] == "pinned-run"
    _, info = estimate.prepare(source, catalog, configured(), TODAY)
    assert info["supported"]
    # None of this excluded recording contributed canonical Q, including its
    # nominal sensor-covered fraction; all 60 minutes require an estimate.
    assert info["estimated_minutes"] == 60


def test_estimated_history_does_not_fabricate_calendar_coverage():
    repo, source = incomplete(1)
    day = source["activities"][0]["date"]
    source["daily"] = [r for r in source["daily"] if r["date"] != day]
    _, info = estimate.prepare(source, repo.envelope["activities"], configured(), TODAY)
    assert not info["supported"]


def test_speed_estimate_keeps_q_distinct_and_only_fills_uncovered_remainder():
    repo, source = incomplete(1)
    _, base = estimate.prepare(source, repo.envelope["activities"], configured(), TODAY)
    evidence = base["activities"][0]
    covered = evidence["missing_minutes"] / 2
    speed = {evidence["activity_ref"]: {"covered_missing_minutes": covered,
        "zones": [{"zone": "Z2", "raw_time_min": covered, "equivalent_time_min": covered*.7}],
        "provenance": {"speed": "MEASURED_VFLAT", "hr": "PAIRED_INDEX_ESTIMATE", "load": "ESTIMATED_NOT_MEASURED_HR"}}}
    ledger, info = estimate.prepare(source, repo.envelope["activities"], configured(), TODAY, speed_estimates=speed)
    item = info["activities"][0]
    assert info["speed_estimated_activity_count"] == 1
    assert item["speed_covered_minutes"] == item["expert_covered_minutes"] == covered
    assert sum(item["estimated_q"].values()) == pytest.approx(covered+covered*.7)
    with pytest.raises(ValueError, match="cannot become"):
        estimate.prepare(ledger, repo.envelope["activities"], configured(), TODAY)


def test_recovery_method_absolute_minimum_keeps_recovery_cap():
    method = deepcopy(next(m for m in engine.METHODS if m["purpose"] == "RECOVERY"))
    assert engine._minimum_work(method, {"capacity_minutes": 300}, None) == method["min_work_min"]
    assert method["max_work_min"] == 30
    unsupported = {"activation_eligible": True, "source": {"readiness_known": False}}
    assert not _eligible(unsupported)


def test_partial_activity_spill_does_not_merge_with_other_sessions_on_same_day(monkeypatch):
    day = (TODAY - timedelta(days=1)).isoformat()
    # Two separate 30-minute Z3 activities stay below a 50-minute threshold.
    # One activity initially has 15 measured + 15 missing minutes.
    source = {"period_start": day, "period_end": day,
              "quality": {"limited_activities": 1},
              "activities": [
                  {"activity_ref": "partial", "date": day, "sport": "Run", "duration_min": 30.,
                   "quality_status": "limited", "hr_coverage_percent": 50.,
                   "zones": [{"zone": "Z3", "raw_time_min": 15., "equivalent_time_min": 15.}]},
                  {"activity_ref": "other", "date": day, "sport": "Run", "duration_min": 30.,
                   "quality_status": "valid", "hr_coverage_percent": 100.,
                   "zones": [{"zone": "Z3", "raw_time_min": 30., "equivalent_time_min": 30.}]}],
              "daily": [{"date": day, "zone": z, "effective_load": 45. if z == "Z3" else 0.}
                        for z in estimate.ZONES],
              "strength": {"daily": [{"date": day, "effective_load": 0.}]}}
    speed = {"partial": {"covered_missing_minutes": 15.,
                         "zones": [{"zone": "Z3", "raw_time_min": 15., "equivalent_time_min": 15.}],
                         "provenance": {"load": "ESTIMATED_NOT_MEASURED_HR"}}}
    result, info = estimate.prepare(source, [], configured(), TODAY, speed_estimates=speed,
                                    zone_tmax_minutes={z: 100. for z in estimate.ZONES})
    assert info["supported"]
    values = {row["zone"]: row["effective_load"] for row in result["daily"]}
    assert values["Z3"] == 60.
    assert values["Z2"] == values["Z4"] == 0.


@pytest.mark.parametrize("sport,expected_down,expected_up", [("Run", 6., 12.), ("NordicSki", 0., 0.)])
def test_partial_activity_crossing_threshold_spills_its_entire_completed_q(sport, expected_down, expected_up):
    day = (TODAY - timedelta(days=1)).isoformat()
    source = {"period_start": day, "period_end": day,
              "quality": {"limited_activities": 1},
              "activities": [{"activity_ref": "partial", "date": day, "sport": sport, "duration_min": 60.,
                              "quality_status": "limited", "hr_coverage_percent": 50.,
                              "zones": [{"zone": "Z3", "raw_time_min": 30., "equivalent_time_min": 30.}]}],
              "daily": [{"date": day, "zone": z, "effective_load": 30. if z == "Z3" else 0.}
                        for z in estimate.ZONES],
              "strength": {"daily": [{"date": day, "effective_load": 0.}]}}
    speed = {"partial": {"covered_missing_minutes": 30.,
                         "zones": [{"zone": "Z3", "raw_time_min": 30., "equivalent_time_min": 30.}],
                         "provenance": {"load": "ESTIMATED_NOT_MEASURED_HR"}}}
    result, _ = estimate.prepare(source, [], configured(), TODAY, speed_estimates=speed,
                                 capacity_contexts_by_sport={
                                     "Run": {"minutes": {z: 100. for z in estimate.ZONES}},
                                     "NordicSki": {"minutes": {z: 200. for z in estimate.ZONES}}})
    values = {row["zone"]: row["effective_load"] for row in result["daily"]}
    assert values["Z3"] == 60.
    assert values["Z2"] == expected_down
    assert values["Z4"] == expected_up
    assert values["Z1"] == values["Z5"] == 0.


def test_estimated_activity_and_daily_ledger_reconcile_and_invalidate_measured_fingerprint():
    from apps.api.component_load_projection import project_history
    from tests.api.test_component_load_projection import context, source as canonical_source

    day = (TODAY - timedelta(days=1)).isoformat()
    contexts = context()
    source = canonical_source(work=(30.,), days=(day,))
    source["quality"] = {"limited_activities": 1}
    source["activities"][0].update(duration_min=60., quality_status="limited", hr_coverage_percent=50.)
    measured = project_history(source, contexts)
    fingerprint = measured["component_load_model"]["fingerprint"]
    speed = {"0": {"covered_missing_minutes": 30.,
                   "zones": [{"zone": "Z2", "raw_time_min": 30., "equivalent_time_min": 30.}],
                   "provenance": {"load": "ESTIMATED_NOT_MEASURED_HR"}}}
    result, info = estimate.prepare(measured, [], configured(), TODAY, speed_estimates=speed,
                                    capacity_contexts_by_sport=contexts)
    assert info["supported"]
    assert "fingerprint" not in result["component_load_model"]
    assert result["component_load_model"]["measured_source_fingerprint"] == fingerprint
    assert measured["component_load_model"]["fingerprint"] == fingerprint
    activity_values = {row["zone"]: row["effective_load"] for row in result["activities"][0]["zones"]}
    daily_values = {row["zone"]: row["effective_load"] for row in result["daily"]}
    assert activity_values == daily_values == {"Z1": 6., "Z2": 60., "Z3": 12., "Z4": 0., "Z5": 0.}
    projected = project_history(result, contexts)
    assert projected["component_load_model"]["fingerprint"] != fingerprint
    assert {row["zone"]: row["effective_load"] for row in projected["daily"]} == daily_values
