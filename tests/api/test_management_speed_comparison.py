from copy import deepcopy
from datetime import date, datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from apps.api import dependencies, main, management_speed_comparison as service
from biathlon import speed_equivalence
from tests.api.test_plan_speed_comparison import parameters, work

NOW = datetime(2026, 10, 22, 12, tzinfo=timezone.utc)
HEADERS = {"Authorization": "Bearer service-secret", "X-OnFlows-Athlete-Alias": "ath-test"}


class Repository:
    def __init__(self, *, first="2026-10-20", last="2026-10-26"):
        self.plan = {"start_date": first, "end_date": last, "fingerprint": "stored-fingerprint",
            "days": [{"date": first, "sessions": [{"sport": "Run", "blocks": [work()]}]}]}
        self.reads = []

    def athlete_settings(self, alias):
        assert alias == "ath-test"
        return SimpleNamespace(timezone="Europe/Sofia")

    def _request(self, method, path, **kwargs):
        assert method == "GET", "A comparison must never write a plan or history"
        assert "athlete_alias=eq.ath-test" in path
        self.reads.append(path)
        if "/onflows_management_entries?" in path:
            return [{"kind": "DRAFT", "entry_key": self.plan["start_date"], "revision": 3,
                     "payload": deepcopy(self.plan)}]
        if "/onflows_management_plan_state?" in path:
            return [{"revision": 7, "record": {"revision": 7, "operation": "ACTIVATE",
                     "payload": {"status": "ACTIVE", "plan": deepcopy(self.plan)}}}]
        raise AssertionError(path)

    def _json(self, value):
        return value


@pytest.fixture
def configured(monkeypatch):
    repo, params = Repository(), parameters()
    calls = []
    actual = speed_equivalence.integrate(params, [{"seconds": 1800, "speed_kmh": 19}])
    def report(repository, alias, sport, **kwargs):
        assert alias == "ath-test" and sport == "Run"
        calls.append(kwargs)
        # Legacy TI coverage can be absent while the independent V(t) metric is available.
        return {"status": "UNAVAILABLE", "source_generation_id": "generation", "source_revision": 4,
                "activities": [{"activity_ref": "run", "sport": "Run", "date": "2026-10-20",
                                "speed_equivalence_comparison": deepcopy(actual)}]}
    monkeypatch.setattr(service, "history_view", report)
    monkeypatch.setattr(service, "read_contexts", lambda repository, alias, sports:
                        {sport: {"comparison_parameters": params} for sport in sports})
    return repo, calls, params


def test_explicit_request_joins_actual_and_prescription_with_identical_current_parameters(configured):
    repo, calls, params = configured
    repo.plan["speed_equivalence_comparison"] = {"version": "saved-comparison", "sports": []}
    frozen = deepcopy(repo.plan)
    result = service.view(repo, "ath-test", kind="DRAFT", revision=3,
                          start_date=date(2026, 10, 20), now=NOW)
    assert calls == [{"today": NOW.date(), "period_start": date(2026, 10, 20), "period_end": NOW.date()}]
    sport = result["sports"][0]
    assert sport["parameters_version"] == params["parameters_version"]
    assert sport["actual"]["status"] == "AVAILABLE"
    planned = next(row for row in sport["planned"]["zones"] if row["zone"] == "Z4")
    actual = next(row for row in sport["actual"]["comparison"]["zones"] if row["zone"] == "Z4")
    assert planned["equivalent_minutes"] == actual["equivalent_minutes"] == pytest.approx(26.25)
    assert all(row["target_equivalent_minutes"] is None for row in sport["targets"])
    assert result["stored_plan_unchanged"] is True and repo.plan == frozen
    assert result["source_plan"]["revision"] == 3
    assert result["original_stored_comparison"] == frozen["speed_equivalence_comparison"]
    assert result["original_stored_comparison"] is not repo.plan["speed_equivalence_comparison"]
    assert len(repo.reads) == 1


def test_active_comparison_reads_saved_revision_without_lifecycle_recalculation(configured):
    repo, calls, _ = configured
    result = service.view(repo, "ath-test", kind="ACTIVE", revision=7, now=NOW)
    assert result["source_plan"]["kind"] == "ACTIVE"
    with pytest.raises(HTTPException) as caught:
        service.view(repo, "ath-test", kind="ACTIVE", revision=6, now=NOW)
    assert caught.value.status_code == 409
    assert len(calls) == 1


def test_future_plan_does_not_read_speed_streams(configured):
    _, calls, _ = configured
    repo = Repository(first="2026-10-24", last="2026-10-30")
    result = service.view(repo, "ath-test", kind="DRAFT", revision=3,
                          start_date=date(2026, 10, 24), now=NOW)
    assert calls == []
    assert result["actual_through"] is None
    assert result["sports"][0]["actual"]["status"] == "NO_RECORDED_ACTIVITIES"


def test_non_running_plan_does_not_request_unsupported_speed_history(configured, monkeypatch):
    repo, calls, _ = configured
    repo.plan["days"][0]["sessions"][0]["sport"] = "WeightTraining"
    monkeypatch.setattr(service, "read_contexts", lambda repository, alias, sports: {})
    result = service.view(repo, "ath-test", kind="DRAFT", revision=3,
                          start_date=date(2026, 10, 20), now=NOW)
    assert calls == []
    assert result["sports"][0]["planned"]["status"] == "UNAVAILABLE"
    assert all(row["reason"] == "RUN_ONLY" for row in result["sports"][0]["planned"]["zones"])
    assert result["sports"][0]["actual"]["reason"] == "RUN_ONLY"
    assert all(row["actual_equivalent_minutes"] is None for row in result["sports"][0]["targets"])


def test_service_rejects_unbounded_period_and_invalid_identity(configured):
    _, calls, _ = configured
    with pytest.raises(HTTPException) as caught:
        service.view(Repository(last="2027-10-26"), "ath-test", kind="DRAFT", revision=3,
                     start_date=date(2026, 10, 20), now=NOW)
    assert caught.value.status_code == 422 and not calls
    with pytest.raises(HTTPException) as caught:
        service.view(Repository(), "ath-test", kind="DRAFT", revision=3, now=NOW)
    assert caught.value.status_code == 422


def test_route_is_read_only_requires_service_and_athlete_and_validates_scope(configured, monkeypatch):
    repo, _, _ = configured
    monkeypatch.setenv("ONFLOWS_SERVICE_TOKEN", "service-secret")
    monkeypatch.setattr(dependencies, "repository", lambda: repo)
    client = TestClient(main.app)
    path = "/api/v2/athlete/management/speed-comparison?kind=DRAFT&revision=3&start_date=2026-10-20"
    assert client.get(path).status_code == 401
    assert client.get(path, headers={"Authorization": "Bearer service-secret"}).status_code == 401
    assert client.get(path.replace("revision=3", "revision=0"), headers=HEADERS).status_code == 422
    assert client.post(path, headers=HEADERS).status_code == 405
    result = client.get(path, headers=HEADERS)
    assert result.status_code == 200
    assert result.headers["Cache-Control"] == "no-store"
    assert result.json()["stored_plan_unchanged"] is True
