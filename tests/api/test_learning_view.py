from copy import deepcopy
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from apps.api import dependencies, learning_view, main, prepared_learning
from apps.api.oauth_store import PersistentStoreFailure
from biathlon.constants import COMPONENTS
from tests.api.test_load_progression import configured


NOW = datetime(2026, 11, 3, 12, tzinfo=timezone.utc)


def component_assessment(component):
    return {"component": component, "recent_weekly_q": 70., "reference_weekly_q": 56.,
        "recent_observed_days": 7, "reference_observed_days": 40, "accent_ratio": 1.25,
        "evidence_count": 0, "isolated_changes": 0, "validation_status": "WARMUP",
        "response_status": "INSUFFICIENT_DATA", "test_step_percent": 5,
        "model_scope": None, "management_basis": "HOLD",
        "predicted_performance_percent": None, "predicted_performance_low": None,
        "predicted_performance_high": None, "proposed_change_percent": None,
        "applied_change_percent": None, "action_status": "COLLECTING",
        "baseline_actual_weekly_q": None, "target_weekly_q": None,
        "execution_weekly_q_ceiling": None, "effective_from": None, "expires_on": None,
        "reason": "Нужни са независими съпоставими наблюдения.",
        "raw_samples": [{"private": 99}], "posterior": {"private": 99}}


def report():
    return {"mode": "SHADOW", "status": "COLLECTING", "summary": "Събираме наблюдения.",
        "evidence_count": 0, "validation": {"status": "WARMUP", "evaluated": 0},
        "memory": {"private": "not for transport"}, "recovery_policy": {
            "series": [{"day": "2026-11-02", "actual_q": 10., "stress": 55., "baseline": 50.,
                        "actual_q_by_component": {"Z1": 4., "Z2": 3., "Z3": 2., "Z4": 1., "Z5": 0., "STR": 0., "private_source": "secret"},
                        "stress_weights": {"hrv": 100*8/15, "resting_hr": 100*7/15, "raw_hrv": 35.},
                        "stress_basis": "FROZEN_BLOCK", "stress_reference": "actual:2026-10-15:2026-10-28",
                        "raw_hrv": 35., "private_source": "secret"},
                       {"day": "2026-11-01", "actual_q": None, "stress": None, "baseline": None,
                        "actual_q_by_component": {z: None for z in COMPONENTS},
                        "stress_weights": {}, "stress_basis": None, "stress_reference": None}],
            "episodes": [{"id": "one", "start": "2026-10-15", "load_end": "2026-10-28",
                "end": "2026-11-02", "loading_days": 14, "recovery_limit_days": 5., "recovery_days": None,
                "recovered": False, "ratio": None, "status": "OBSERVING", "proposed_change_percent": 0.,
                "reason": "Няма потвърдено връщане.", "performance_change_percent": None, "private_note": "hidden"}],
            "decision": {"component": None, "status": "COLLECTING", "proposed_change_percent": 0.,
                         "applied_change_percent": 0., "reason": "Нужни са данни.", "raw_lab": 99},
            "observations": {"stress_days": 0, "load_days": 1, "private_raw": 99},
            "channels": [{"key": "hrv", "label": "HRV", "observed_days": 0, "nominal_weight": 8, "raw_values": [1, 2]}],
            "component_assessments": [component_assessment(z) for z in COMPONENTS],
            "model_summary": {"private": "not for transport"},
        }}


class Repository:
    def capture_planning_inputs(self, alias):
        return None

    def athlete_settings(self, alias):
        return SimpleNamespace(timezone="Europe/Sofia")

    def active_planning_calendar(self, alias, first, today):
        return {"generation_id": "g", "revision": 1, "activities": [], "snapshot_payload": {
            "load_history": {"period_end": "2026-11-03", "daily": []}}}

    def active_analysis(self, alias):
        return {"generation_id": "g", "revision": 1}

    def athlete_planning_calendar(self, alias):
        return {"events": []}


def test_view_transports_only_dedicated_observation_fields_and_never_publishes(monkeypatch):
    repo = Repository()
    original = report()
    monkeypatch.setattr(learning_view.ReadSession, "planning", lambda _: repo)
    monkeypatch.setattr(learning_view, "ManagementStore", lambda _: SimpleNamespace(profile=lambda alias: {
        "configured": True, "profile": configured(discipline="5000 m")}))
    monkeypatch.setattr(learning_view, "project_snapshot", lambda _, alias, snapshot: snapshot)
    monkeypatch.setattr(learning_view.learning_service, "context", lambda *args, **kwargs: deepcopy(original))
    result = learning_view.view(repo, "ath-test", now=NOW)
    assert result["schema_version"] == "recovery-learning-view-v1"
    assert result["mode"] == "SHADOW" and result["decision"]["applied_change_percent"] == 0
    by_day = {row["day"]: row for row in result["series"]}
    scored, gap = by_day["2026-11-02"], by_day["2026-11-01"]
    assert scored["stress"] == 55.
    assert scored["actual_q_by_component"] == {"Z1": 4., "Z2": 3., "Z3": 2., "Z4": 1., "Z5": 0., "STR": 0.}
    assert sum(scored["actual_q_by_component"].values()) == scored["actual_q"]
    assert set(scored["stress_weights"]) == {"hrv", "resting_hr"}
    assert sum(scored["stress_weights"].values()) == pytest.approx(100.)
    assert scored["stress_basis"] == "FROZEN_BLOCK"
    assert scored["stress_reference"] == "actual:2026-10-15:2026-10-28"
    assert gap["actual_q"] is None and gap["stress"] is None
    assert gap["actual_q_by_component"] == {z: None for z in COMPONENTS}
    assert gap["stress_weights"] == {} and gap["stress_basis"] is None and gap["stress_reference"] is None
    assert "raw_hrv" not in result["series"][0]
    assert "private_note" not in result["episodes"][0]
    assert "raw_lab" not in result["decision"]
    assert "private_raw" not in result["observations"]
    assert "raw_values" not in result["channels"][0]
    assert result["channels"][0]["nominal_weight"] == 8
    assert [row["component"] for row in result["components"]] == list(COMPONENTS)
    assert all(row["response_status"] == "INSUFFICIENT_DATA" for row in result["components"])
    assert all(row["model_scope"] is None and row["management_basis"] == "HOLD" for row in result["components"])
    assert all(row["predicted_performance_percent"] is None and row["proposed_change_percent"] is None for row in result["components"])
    assert all("raw_samples" not in row and "posterior" not in row for row in result["components"])
    assert "memory" not in result
    assert "model_summary" not in result
    assert original == report()


def test_view_keeps_hypothetical_response_separate_from_one_gated_proposal(monkeypatch):
    repo = Repository()
    original = report()
    selected = original["recovery_policy"]["component_assessments"][0]
    selected.update(evidence_count=8, isolated_changes=2, validation_status="PASSED",
        response_status="SUPPORTED_POSITIVE", predicted_performance_percent=2.,
        predicted_performance_low=.5, predicted_performance_high=3.5,
        model_scope="GLOBAL", management_basis="POLICY_REDUCTION",
        proposed_change_percent=-8., applied_change_percent=0., action_status="SELECTED",
        baseline_actual_weekly_q=140., target_weekly_q=128.8,
        execution_weekly_q_ceiling=35., effective_from="2026-11-03", expires_on="2026-11-16")
    original["recovery_policy"]["decision"].update(component="Z1", status="SHADOW",
        proposed_change_percent=-8., applied_change_percent=0.)
    monkeypatch.setattr(learning_view.ReadSession, "planning", lambda _: repo)
    monkeypatch.setattr(learning_view, "ManagementStore", lambda _: SimpleNamespace(profile=lambda alias: {
        "configured": True, "profile": configured(discipline="5000 m")}))
    monkeypatch.setattr(learning_view, "project_snapshot", lambda _, alias, snapshot: snapshot)
    monkeypatch.setattr(learning_view.learning_service, "context", lambda *args, **kwargs: deepcopy(original))
    result = learning_view.view(repo, "ath-test", now=NOW)
    selected = next(row for row in result["components"] if row["component"] == "Z1")
    assert selected["test_step_percent"] == 5
    assert selected["predicted_performance_percent"] == 2.
    assert selected["predicted_performance_low"] == .5
    assert selected["predicted_performance_high"] == 3.5
    assert selected["proposed_change_percent"] == -8.
    assert selected["applied_change_percent"] == 0.
    assert selected["action_status"] == "SELECTED"
    assert selected["model_scope"] == "GLOBAL" and selected["management_basis"] == "POLICY_REDUCTION"
    assert selected["baseline_actual_weekly_q"] == 140.
    assert selected["target_weekly_q"] == 128.8
    assert selected["execution_weekly_q_ceiling"] == 35.
    assert selected["effective_from"] == "2026-11-03" and selected["expires_on"] == "2026-11-16"
    assert result["decision"]["component"] == "Z1"
    assert all(row["proposed_change_percent"] is None and row["applied_change_percent"] is None
               for row in result["components"] if row["component"] != "Z1")
    assert all(row[key] is None for row in result["components"] if row["component"] != "Z1"
               for key in ("baseline_actual_weekly_q", "target_weekly_q", "execution_weekly_q_ceiling", "effective_from", "expires_on"))


def test_generation_change_is_not_silently_joined(monkeypatch):
    repo = Repository()
    repo.active_analysis = lambda alias: {"generation_id": "new", "revision": 2}
    monkeypatch.setattr(learning_view.ReadSession, "planning", lambda _: repo)
    with pytest.raises(PersistentStoreFailure, match="changed"):
        learning_view.view(repo, "ath-test", now=NOW)


def test_learning_http_requires_service_and_explicit_athlete_and_no_store(monkeypatch):
    monkeypatch.setenv("ONFLOWS_SERVICE_TOKEN", "test-service")
    monkeypatch.setattr(dependencies, "repository", lambda: object())
    monkeypatch.setattr(prepared_learning, "read_view", lambda repo, alias, days: {"alias": alias, "days": days})
    headers = {"Authorization": "Bearer test-service", "X-OnFlows-Athlete-Alias": "ath-test"}
    with TestClient(main.app) as client:
        assert client.get("/api/v2/athlete/learning").status_code == 401
        assert client.get("/api/v2/athlete/learning", headers={"Authorization": "Bearer test-service"}).status_code == 401
        response = client.get("/api/v2/athlete/learning?days=30", headers=headers)
        assert response.status_code == 200 and response.json()["days"] == 30
        assert response.headers["cache-control"] == "no-store"
        assert client.get("/api/v2/athlete/learning?days=365", headers=headers).status_code == 422


def accounting_source():
    days = ["2026-10-30", "2026-10-31", "2026-11-01", "2026-11-02"]
    source = {"period_start": days[0], "period_end": days[-1], "activities": [],
        "daily": [{"date": day, "zone": z, "effective_load": 0., "status_7_40": 1.234+offset/10}
                  for offset, day in enumerate(days) for z in COMPONENTS[:-1]],
        "strength": {"daily": [{"date": day, "effective_load": 0., "equivalent_time_min": 0.,
            "status_7_40": .873+offset/10} for offset, day in enumerate(days)]}}
    return source


def add_recording(source, *, day="2026-10-31", unknown=0., modelled=False):
    source["load_selection"] = {"version": "unified"}
    source["activities"].append({"activity_ref": "act-known", "date": day, "sport": "Run",
        "quality_status": "limited" if unknown else "valid",
        "zones": [{"zone": z, "raw_time_min": 1., "equivalent_time_min": 2.} for z in COMPONENTS[:-1]],
        "load_source": {"recorded_seconds": 300.+unknown, "active_seconds": 300.,
            "rest_seconds": 0., "unknown_seconds": unknown, "initial_heuristic": modelled}})
    for row in source["daily"]:
        if row["date"] == day:
            row["effective_load"] = 3.


def test_existing_7_40_is_transported_exactly_even_when_learning_rejects_recording():
    source = accounting_source()
    add_recording(source, unknown=90.)
    original = deepcopy(source)
    accounting = learning_view._accounting_series(source)
    partial = accounting["2026-10-31"]
    assert partial["load_status"] == "PARTIAL"
    assert partial["load_unknown_minutes"] == 1.5
    assert partial["load_activity_count"] == 1
    assert partial["index_7_40_by_component"]["Z1"] == source["daily"][5]["status_7_40"]
    assert partial["index_7_40_by_component"]["STR"] == source["strength"]["daily"][1]["status_7_40"]
    assert partial["recorded_q_by_component"] == {**dict.fromkeys(COMPONENTS[:-1], 2.), "STR": 0.}
    row = learning_view._series_row({"day": "2026-10-31", "actual_q": None}, partial)
    assert row["learning_load_eligible"] is False
    assert row["actual_q"] is None
    assert row["index_7_40_by_component"] == partial["index_7_40_by_component"]
    assert "непълни" in row["load_reason"]
    assert accounting["2026-11-02"]["load_status"] == "REST"
    assert accounting["2026-11-02"]["index_load_status"] == "PARTIAL"
    assert source == original


def test_initial_speed_mapping_remains_visible_with_its_modelled_basis():
    source = accounting_source()
    add_recording(source, modelled=True)
    accounting = learning_view._accounting_series(source)
    assert accounting["2026-10-31"]["load_status"] == "MODELLED"
    assert accounting["2026-10-31"]["load_unknown_minutes"] == 0.
    assert accounting["2026-10-31"]["index_7_40_by_component"]["Z2"] == 1.334
    assert accounting["2026-11-02"]["index_load_status"] == "MODELLED"
    assert "скорост" in accounting["2026-10-31"]["load_reason"]


def test_missing_canonical_day_never_becomes_a_rest_or_zero_index():
    source = accounting_source()
    source["daily"] = [r for r in source["daily"] if r["date"] != "2026-10-31"]
    source["strength"]["daily"] = [r for r in source["strength"]["daily"] if r["date"] != "2026-10-31"]
    accounting = learning_view._accounting_series(source)
    row = learning_view._series_row({"day": "2026-10-31", "actual_q": None}, accounting.get("2026-10-31"))
    assert row["load_status"] == "UNAVAILABLE"
    assert all(value is None for value in row["index_7_40_by_component"].values())
    assert all(value is None for value in row["recorded_q_by_component"].values())
    assert accounting["2026-11-01"]["index_load_status"] == "PARTIAL"
    assert accounting["2026-11-02"]["index_history_days"] == 3


def test_missing_catalog_activity_does_not_certify_a_rest_day():
    source = accounting_source()
    accounting = learning_view._accounting_series(source, [{"activity_ref": "omitted", "local_date": "2026-11-02"}])
    assert accounting["2026-11-02"]["load_status"] == "PARTIAL"
    assert accounting["2026-11-02"]["load_activity_count"] == 1
    assert accounting["2026-11-02"]["load_unknown_minutes"] is None
    assert all(q is None for q in accounting["2026-11-02"]["recorded_q_by_component"].values())
    # It remains the already recorded canonical index, not an invented zero.
    assert accounting["2026-11-02"]["index_7_40_by_component"]["Z1"] == 1.534


def test_missing_provider_record_cannot_certify_complete_load_from_old_ledger():
    source = accounting_source()
    add_recording(source)
    accounting = learning_view._accounting_series(source, [{"activity_ref": "act-known",
        "local_date": "2026-10-31", "quality_status": "provider_missing"}])
    assert accounting["2026-10-31"]["load_status"] == "PARTIAL"
    assert accounting["2026-10-31"]["load_unknown_minutes"] is None


@pytest.mark.parametrize("projection_unavailable", [False, True])
def test_private_view_keeps_existing_7_40_separate_from_learner_eligibility(monkeypatch, projection_unavailable):
    repo = Repository()
    source = accounting_source()
    add_recording(source, day="2026-11-02", unknown=90.)
    repo.active_planning_calendar = lambda *args: {"generation_id": "g", "revision": 1,
        "activities": [], "snapshot_payload": {"load_history": source}}
    original = report()
    original["recovery_policy"]["series"][0]["actual_q"] = None
    original["recovery_policy"]["series"][0]["actual_q_by_component"] = dict.fromkeys(COMPONENTS)
    monkeypatch.setattr(learning_view.ReadSession, "planning", lambda _: repo)
    monkeypatch.setattr(learning_view, "ManagementStore", lambda _: SimpleNamespace(profile=lambda alias: {
        "configured": True, "profile": configured(discipline="5000 m")}))
    def project(*args):
        if projection_unavailable:
            raise learning_view.ComponentLoadRefreshRequired("Missing reconstructible Q")
        return args[-1]
    monkeypatch.setattr(learning_view, "project_snapshot", project)
    monkeypatch.setattr(learning_view.learning_service, "context", lambda *args, **kwargs: deepcopy(original))
    result = learning_view.view(repo, "ath-test", now=NOW)
    scored = next(row for row in result["series"] if row["day"] == "2026-11-02")
    assert scored["actual_q"] is None and scored["learning_load_eligible"] is False
    assert scored["index_7_40_by_component"]["Z1"] == 1.534
    assert scored["index_7_40_by_component"]["STR"] == pytest.approx(1.173)
    assert scored["recorded_q_by_component"]["Z1"] == 2.
    assert scored["load_status"] == "PARTIAL" and scored["load_activity_count"] == 1
    assert scored["stress"] == 55. and scored["stress_basis"] == "FROZEN_BLOCK"
    assert result["source_date"] == source["period_end"]
    assert bool(result["limitations"]) is projection_unavailable
    if projection_unavailable:
        assert "последната записана обработка" in scored["load_reason"]
        assert "текущата проекция" in scored["load_reason"]
        assert all(row["index_load_status"] == "PARTIAL" for row in result["series"])


def test_current_canonical_date_is_visible_without_inventing_completed_daily_learning(monkeypatch):
    repo = Repository()
    source = accounting_source()
    for day in ("2026-11-03", "2026-11-04"):
        source["daily"].extend({**row, "date": day, "status_7_40": 4.567}
            for row in list(source["daily"]) if row["date"] == "2026-11-02")
        source["strength"]["daily"].append({**source["strength"]["daily"][-1], "date": day, "status_7_40": 2.345})
    repo.active_planning_calendar = lambda *args: {"generation_id": "g", "revision": 1,
        "activities": [], "snapshot_payload": {"load_history": source}}
    original = report()
    original["recovery_policy"]["series"].append({"day": "2026-11-03", "actual_q": 99.,
        "stress": 99., "actual_q_by_component": dict.fromkeys(COMPONENTS, 99.)})
    monkeypatch.setattr(learning_view.ReadSession, "planning", lambda _: repo)
    monkeypatch.setattr(learning_view, "ManagementStore", lambda _: SimpleNamespace(profile=lambda alias: {
        "configured": True, "profile": configured(discipline="5000 m")}))
    monkeypatch.setattr(learning_view, "project_snapshot", lambda *args: args[-1])
    monkeypatch.setattr(learning_view.learning_service, "context", lambda *args, **kwargs: deepcopy(original))
    result = learning_view.view(repo, "ath-test", now=NOW)
    assert [row["day"] for row in result["series"]] == ["2026-10-30", "2026-10-31", "2026-11-01", "2026-11-02", "2026-11-03"]
    current = result["series"][-1]
    assert current["index_7_40_by_component"] == {**dict.fromkeys(COMPONENTS[:-1], 4.567), "STR": 2.345}
    assert current["actual_q"] is None and current["stress"] is None
    assert current["learning_load_eligible"] is False
    assert all(q is None for q in current["actual_q_by_component"].values())
