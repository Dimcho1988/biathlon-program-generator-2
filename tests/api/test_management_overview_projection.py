"""The opt-in overview contract preserves features while omitting evidence JSON."""
from copy import deepcopy
from datetime import timedelta

import httpx
import pytest
from fastapi.testclient import TestClient

from apps.api import dependencies, main, management_lifecycle as lifecycle, management_service as service
from apps.api.management_overview import empty_overview, validate_overview
from apps.api.management_plan_store import PlanStore
from apps.api.oauth_store import PersistentStoreFailure
from tests.api.test_management_api import HEADERS
from tests.api.test_outlook_calendar_reuse import PlanningHTTP, fixed_clocks, read_page
from tests.api.test_training_plan_engine import NOW, TODAY


def projection():
    return {"revision": 9, "record_revision": 9, "stored_fingerprint": "old",
            "active_schema_version": "active-plan-v2", "plan_type": "object", "proposal_type": "object",
            "selected_schema_version": "planning-draft-v1", "selected_status": "DRAFT",
            "start_date": TODAY.isoformat(), "end_date": (TODAY+timedelta(days=6)).isoformat(),
            "status": "REVIEW_REQUIRED", "has_proposal": True, "engine_version": "v49",
            "days": [{"date": TODAY.isoformat(), "status": "TRAINING", "sessions": [
                {"total_minutes": 6., "blocks": [{"zone": "Z5", "duration_min": 5.},
                                                 {"zone": "NMS", "duration_min": 1.}]}]}],
            "history_comparison": [{"start_date": (TODAY-timedelta(days=7)).isoformat(),
                                    "end_date": (TODAY-timedelta(days=1)).isoformat(),
                                    "actual_minutes": None, "covered_days": 6}],
            "outcomes": [{"date": (TODAY-timedelta(days=1)).isoformat(),
                          "planned_minutes": 50., "actual_minutes": None}]}


class OverviewHTTP(PlanningHTTP):
    def __init__(self):
        super().__init__()
        self.overview = projection()

    def handle(self, request):
        if request.url.path == "/rest/v1/rpc/read_onflows_management_overview":
            self.requests.append(request)
            assert request.method == "GET" and request.url.params["p_alias"] == "ath-test"
            return httpx.Response(200, content=b"null") if self.overview is None else httpx.Response(200, json=deepcopy(self.overview))
        return super().handle(request)


@pytest.fixture(autouse=True)
def fixed_lifecycle_clock(monkeypatch, fixed_clocks):
    monkeypatch.setattr(lifecycle, "datetime", service.datetime)


def request_overview(monkeypatch, harness, query="view=overview&projection=overview-v1"):
    monkeypatch.setenv("ONFLOWS_SERVICE_TOKEN", "service-secret")
    monkeypatch.setattr(dependencies, "repository", lambda: harness.repo)
    return TestClient(main.app).get(f"/api/v2/athlete/management/view?{query}", headers=HEADERS)


def test_opt_in_overview_uses_sixteen_reads_and_preserves_all_other_resources(monkeypatch):
    ordinary = PlanningHTTP()
    expected = read_page(monkeypatch, ordinary)
    compact = OverviewHTTP()
    response = request_overview(monkeypatch, compact)
    assert response.status_code == 200, response.text
    result = response.json()
    assert len(ordinary.requests) == 17 and len(compact.requests) == 16
    assert result["schema_version"] == "management-overview-v1" and "active" not in result
    assert {k: v for k, v in result["profile"].items() if k != "history"} == {
        k: v for k, v in expected["profile"].items() if k != "history"}
    assert result["profile"]["history"] is None
    for key in ("outlook", "sync", "drafts"):
        assert result[key] == expected[key]
    assert "stored_fingerprint" not in result["overview_active"]
    assert result["overview_active"]["outcomes"][0]["actual_minutes"] is None
    assert not any("/onflows_management_plan_revisions" in str(r.url)
                   and "changes:payload" in str(r.url) for r in compact.requests)


def test_default_overview_and_week_contracts_remain_full_even_with_week_opt_in(monkeypatch):
    ordinary = OverviewHTTP()
    response = request_overview(monkeypatch, ordinary, "view=overview")
    assert response.status_code == 200 and "active" in response.json()
    assert "overview_active" not in response.json()
    assert ordinary.counts()["/rpc/read_onflows_management_overview"] == 0
    for query in ("view=week", "view=week&projection=overview-v1"):
        weekly = OverviewHTTP()
        response = request_overview(monkeypatch, weekly, query)
        assert response.status_code == 200 and "active" in response.json()
        assert "schema_version" not in response.json()
        assert weekly.counts()["/rpc/read_onflows_management_overview"] == 0


def test_compact_current_uses_the_same_full_freshness_check_at_the_same_plan_revision(monkeypatch):
    harness = OverviewHTTP()
    harness.overview["stored_fingerprint"] = lifecycle._context(harness.repo, "ath-test", NOW)[3]
    response = request_overview(monkeypatch, harness)
    assert response.status_code == 200, response.text
    assert response.json()["overview_active"]["stale"] is False
    harness.saved_profile["discipline"] = "3000 m"
    changed = request_overview(monkeypatch, harness)
    assert changed.status_code == 200
    assert changed.json()["overview_active"]["revision"] == 9
    assert changed.json()["overview_active"]["stale"] is True


def test_no_active_plan_is_explicit_without_checking_nonexistent_plan_inputs(monkeypatch):
    harness = OverviewHTTP()
    harness.overview = None
    monkeypatch.setattr(lifecycle, "_context", lambda *args: pytest.fail("No active plan to check"))
    assert lifecycle.current_overview(harness.repo, "ath-test", now=NOW) == empty_overview()


@pytest.mark.parametrize("key,value", [
    ("record_revision", 8), ("revision", True), ("plan_type", "array"), ("proposal_type", "array"),
    ("has_proposal", False), ("proposal_type", None), ("active_schema_version", "unknown"), ("engine_version", None),
    ("days", None), ("outcomes", None), ("history_comparison", None),
])
def test_invalid_projection_is_a_store_failure_not_a_false_empty_plan(key, value):
    record = projection()
    record[key] = value
    with pytest.raises(PersistentStoreFailure):
        validate_overview(record)


@pytest.mark.parametrize("change", ["date", "negative", "boolean", "nonfinite", "missing_outcome", "missing_blocks"])
def test_projection_keeps_strict_date_and_time_validation(change):
    record = projection()
    if change == "date": record["days"][0]["date"] = "2026-02-30"
    elif change == "missing_outcome": record["outcomes"][0].pop("actual_minutes")
    elif change == "missing_blocks": record["days"][0]["sessions"][0]["blocks"] = None
    else:
        record["days"][0]["sessions"][0]["blocks"][0]["duration_min"] = {
            "negative": -1, "boolean": True, "nonfinite": float("nan")}[change]
    with pytest.raises(PersistentStoreFailure):
        validate_overview(record)


def test_invalid_compact_record_preserves_http_failure_instead_of_displaying_empty_data(monkeypatch):
    harness = OverviewHTTP()
    harness.overview["record_revision"] = None
    response = request_overview(monkeypatch, harness)
    assert response.status_code == 503
    assert "overview_active" not in response.json()


def test_readonly_rpc_alias_is_encoded_without_changing_the_query():
    calls = []
    class Repository:
        def _request(self, method, path):
            calls.append((method, path))
            return None
        def _json(self, value):
            return value
    assert PlanStore(Repository()).current_overview("one&two/three?") is None
    assert calls == [("GET", "/rpc/read_onflows_management_overview?p_alias=one%26two%2Fthree%3F")]
