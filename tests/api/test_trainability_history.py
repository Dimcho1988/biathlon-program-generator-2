import base64
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from fastapi.testclient import TestClient

from apps.api import main
from apps.api.oauth_store import PersistentStoreFailure, SupabasePilotRepository
from apps.api.trainability import compute_trainability


KEY = "a" * 64
REF = "shadow-" + "b" * 32
HEADERS = {"Authorization": "Bearer service-secret", "X-OnFlows-Athlete-Alias": "ath-test"}
PERIOD = "?period_start=2026-08-01&period_end=2026-08-31"


class PinnedRepository:
    def active_activity_calendar(self, alias, start, end):
        assert alias == "ath-test"
        return {"generation_id": "pinned-generation", "revision": 3, "activities": [
            {"activity_ref": REF, "name": "Training", "sport": "RollerSki",
             "start_at_utc": "2026-08-10T10:00:00Z", "local_date": "2026-08-10",
             "latest_shadow_run_key": KEY},
        ]}

    def trainability_summaries(self, alias, keys):
        assert alias == "ath-test" and keys == (KEY,)
        return {KEY: {"activity_ref": REF, "trainability_index": None}}


def test_history_reads_pinned_keys_and_marks_old_results_for_refresh(monkeypatch):
    monkeypatch.setenv("ONFLOWS_SERVICE_TOKEN", "service-secret")
    monkeypatch.setattr(main, "_repository", lambda: PinnedRepository())
    with TestClient(main.app) as client:
        assert client.get("/api/v2/real/trainability" + PERIOD).status_code == 401
        response = client.get("/api/v2/real/trainability" + PERIOD, headers=HEADERS)
        assert response.status_code == 200
        body = response.json()
        assert body["generation_id"] == "pinned-generation"
        assert body["activities"][0]["unavailable_reason"] == "REFRESH_REQUIRED"
        assert client.get("/api/v2/real/trainability?period_start=2026-01-01&period_end=2026-08-31", headers=HEADERS).status_code == 422


def test_history_fails_closed_if_pinned_summary_belongs_to_other_activity(monkeypatch):
    class Mismatched(PinnedRepository):
        def trainability_summaries(self, alias, keys):
            return {KEY: {"activity_ref": "another-activity", "trainability_index": {}}}
    monkeypatch.setenv("ONFLOWS_SERVICE_TOKEN", "service-secret")
    monkeypatch.setattr(main, "_repository", lambda: Mismatched())
    with TestClient(main.app) as client:
        response = client.get("/api/v2/real/trainability" + PERIOD, headers=HEADERS)
    assert response.status_code == 503
    assert "another-activity" not in response.text


@pytest.mark.parametrize("legacy", [False, True])
def test_history_serves_only_the_current_normalized_model(monkeypatch, legacy):
    index = compute_trainability(
        [{"hrmod_final_bpm": 154, "dt_s": 420}],
        [{"vflat_b65_kmh": 20, "dt_s": 420, "grade_raw_pct": 0}],
        zone_bounds_bpm=[50,137,147,158,170,178], hrmax_bpm=178,
        activity_duration_s=420, comparison_key="current", source_versions={},
    )
    if legacy:
        index = {"schema_version": "trainability-index-v1", "model_version": "trainability_rank_v1", "general": {"index": 7.7}}

    class WithIndex(PinnedRepository):
        def trainability_summaries(self, alias, keys):
            assert keys == (KEY,)
            return {KEY: {"activity_ref": REF, "trainability_index": index}}

    monkeypatch.setenv("ONFLOWS_SERVICE_TOKEN", "service-secret")
    monkeypatch.setattr(main, "_repository", lambda: WithIndex())
    with TestClient(main.app) as client:
        response = client.get("/api/v2/real/trainability" + PERIOD, headers=HEADERS)
    assert response.status_code == 200
    row = response.json()["activities"][0]
    assert row["unavailable_reason"] == ("REFRESH_REQUIRED" if legacy else None)
    assert row["index"] == (None if legacy else index)


def test_summary_store_batches_exact_keys_and_only_projects_small_index():
    keys = tuple(f"{i:064x}" for i in range(51))
    calls = []

    class Client:
        def request(self, method, url, **kwargs):
            query = parse_qs(urlparse(url).query)
            calls.append(query)
            assert method == "GET"
            assert query["athlete_alias"] == ["eq.ath-test"]
            assert query["select"] == ["run_key,activity_ref,trainability_index:result_payload->trainability_index"]
            selected = query["run_key"][0][4:-1].split(",")
            assert len(selected) <= 50
            return httpx.Response(200, json=[{"run_key": key, "activity_ref": REF, "trainability_index": None} for key in selected])

    repository = SupabasePilotRepository(
        supabase_url="https://project.supabase.co", secret_key="sb_secret_server-key",
        encryption_key=base64.urlsafe_b64encode(bytes(range(32))).decode(), client=Client(),
    )
    assert set(repository.trainability_summaries("ath-test", keys)) == set(keys)
    assert len(calls) == 2
    with pytest.raises(PersistentStoreFailure):
        repository.trainability_summaries("ath-test", ("invalid-query-key",))
