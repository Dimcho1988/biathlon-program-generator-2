from apps.api.routes import management
from apps.api import dependencies
from copy import deepcopy
from datetime import date, datetime, timezone
from types import SimpleNamespace
from uuid import UUID

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError

from apps.api import main, model_service, management_service as service
from apps.api.management_schemas import ManagementProfile, ManagementGenerateRequest
from apps.api.oauth_store import PersistentStoreFailure


NOW = datetime(2026, 9, 21, 12, tzinfo=timezone.utc)
ACTOR = UUID("11111111-1111-4111-8111-111111111111")
PROFILE = {
    "schema_version": "management-profile-v1", "sport": "Run", "actual_sport": "Run",
    "discipline": "5000 m", "program_start": "2026-09-01", "program_end": "2027-06-01",
    "available_minutes": [45, 60, 0, 60, 45, 90, 0],
    "recent_weekly_hours": [5, 5, 4, 5],
}
HEADERS = {"Authorization": "Bearer service-secret", "X-OnFlows-Athlete-Alias": "ath-test",
           "X-OnFlows-Actor-Id": str(ACTOR)}


class Store:
    def __init__(self):
        self.saved = []
        self.current = {"configured": True, "profile": deepcopy(PROFILE), "revision": 2}

    def profile(self, alias):
        assert alias == "ath-test"
        return deepcopy(self.current)

    def save_profile(self, alias, payload, expected_revision, actor):
        assert alias == "ath-test" and actor == ACTOR
        self.saved.append(payload)
        return {"revision": expected_revision + 1}

    def drafts(self, alias):
        assert alias == "ath-test"
        return []

    def save_draft(self, alias, payload, actor, expected_profile_revision, **kwargs):
        assert alias == "ath-test" and actor == ACTOR
        self.saved.append({"payload": deepcopy(payload), "expected_profile_revision": expected_profile_revision, **kwargs})
        return {"saved": True, "entry_key": payload["start_date"], "revision": 1,
                "recorded_at": NOW.isoformat(), "payload": payload}


@pytest.fixture
def api(monkeypatch):
    monkeypatch.setenv("ONFLOWS_SERVICE_TOKEN", "service-secret")
    store = Store()
    repository = SimpleNamespace(athlete_settings=lambda alias: SimpleNamespace(timezone="Europe/Sofia"))
    monkeypatch.setattr(dependencies, "repository", lambda: repository)
    monkeypatch.setattr(model_service, "speed_view", lambda *args, **kwargs: {"status":"REFERENCE_ONLY"})
    monkeypatch.setattr(management, "ManagementStore", lambda repo: store)
    monkeypatch.setattr(service, "ManagementStore", lambda repo: store)
    return TestClient(main.app), store, repository


def test_management_requires_explicit_athlete_and_actor(api):
    client, store, _ = api
    assert client.get("/api/v2/athlete/management/profile").status_code == 401
    assert client.get("/api/v2/athlete/management/profile", headers={"Authorization": "Bearer service-secret"}).status_code == 401
    response = client.get("/api/v2/athlete/management/profile", headers=HEADERS)
    assert response.status_code == 200 and response.json()["revision"] == 2
    no_actor = {k: v for k, v in HEADERS.items() if k != "X-OnFlows-Actor-Id"}
    assert client.put("/api/v2/athlete/management/profile", json={"profile": PROFILE, "expected_revision": 2}, headers=no_actor).status_code == 401
    assert not store.saved
    assert client.put("/api/v2/athlete/management/profile", json={"profile": PROFILE, "expected_revision": 2}, headers=HEADERS).status_code == 200


def test_outlook_read_is_scoped_and_needs_neither_actor_nor_existing_draft(api):
    client, store, repository = api
    repository.active_analysis = lambda _: {}
    assert client.get("/api/v2/athlete/management/outlook").status_code == 401
    assert client.get("/api/v2/athlete/management/outlook", headers={"Authorization": "Bearer service-secret"}).status_code == 401
    response = client.get("/api/v2/athlete/management/outlook", headers={k:v for k,v in HEADERS.items() if k != "X-OnFlows-Actor-Id"})
    assert response.status_code == 200
    result = response.json()["outlook"]
    assert result["profile_revision"] == 2 and result["schema_version"] == "training-outlook-preview-v1"
    assert result["long_term"]["limited"] is True
    assert not store.saved
    store.current.update(configured=False, profile=None)
    assert client.get("/api/v2/athlete/management/outlook", headers=HEADERS).json() == {"configured": False, "outlook": None}


def test_profile_history_uses_the_same_saved_break_rule_as_the_planner(api, monkeypatch):
    from tests.api.test_training_plan_engine import Repository
    client, store, repository = api
    repository.active_analysis = lambda _: deepcopy(Repository().envelope)
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW.astimezone(tz)
    monkeypatch.setattr(management, "datetime", Clock)
    store.current["profile"]["planning_controls"] = {"history_gap_days": 7}
    restricted = client.get("/api/v2/athlete/management/profile", headers=HEADERS).json()["history"]["history_policy"]
    assert restricted["gap_threshold_days"] == 7 and restricted["usable"] is False
    store.current["profile"]["planning_controls"]["history_gap_days"] = 10
    ordinary = client.get("/api/v2/athlete/management/profile", headers=HEADERS).json()["history"]["history_policy"]
    assert ordinary["gap_threshold_days"] == 10 and ordinary["usable"] is True


def test_stale_daily_data_does_not_clip_coach_outlook_or_blend_microcycle_peaks(api):
    from datetime import timedelta
    from tests.api.test_training_plan_engine import Repository, TODAY
    from tests.api.test_planning_controls import controls
    _, store, repository = api
    source = deepcopy(Repository().envelope)
    repository.active_analysis = lambda _: source
    store.current['profile'].update(
        program_start=TODAY.isoformat(), reentry_days=0,
        available_minutes=[60, 60, 60, 60, 60, 90, 0],
        planning_controls=controls(accent_mode='MANUAL', accents=['Z3'],
                                   wave=[.96, 1.4, 1.5, .78], accent_index=1.1))
    # Replay the actual report: the latest analysis is yesterday and the coach
    # has a legacy 6.5-hour template plus a 1.5 wave, with no explicit time cap.
    result = service.outlook(repository, 'ath-test', now=NOW+timedelta(days=1))['outlook']
    assert result['long_term']['limited'] is True
    assert result['volume_context']['availability_mode'] == 'AUTO_HISTORY'
    assert result['volume_context']['available_weekly_minutes'] is None
    weeks = result['long_term']['weeks']
    assert weeks[0]['days'] == 6
    assert weeks[0]['end_date'] == (TODAY+timedelta(days=6)).isoformat()
    assert weeks[2]['components']['Z3']['target_index_7_40'] == pytest.approx(1.65)
    # The loading peak belongs to Z3; Z2 retains its maintenance target.
    assert weeks[2]['components']['Z2']['target_index_7_40'] == pytest.approx(1.)
    # The final unloading ceiling also applies to a stale read-only outlook;
    # it must not be refilled by the nominal 1.1 × .78 wave.
    assert weeks[3]['components']['Z3']['target_index_7_40'] < .858
    assert weeks[3]['components']['Z3']['target_weekly_effective'] <= .65*7*result['long_term']['baseline']['Z3']['c40']+.001
    assert weeks[3]['cycle']['accents'] == []  # No support permission from stale data.
    assert result['long_term']['readiness_forecast'] is False
    assert not store.saved

    # The identical intent must still respect the daily incomplete-data gate.
    engine = service.training_plan_engine
    rows = engine._daily_rows(source['snapshot_payload']['load_history'], TODAY)
    normalized = ManagementProfile.model_validate(store.current['profile']).model_dump(mode='json')
    goals, _, _ = engine._goals(normalized, TODAY+timedelta(days=14), 'GENERAL_PREPARATION',
                               False, {}, None, 2, 4, rows, TODAY, True, 1.)
    assert goals['Z3']['target_index'] == pytest.approx(1.)


@pytest.mark.parametrize("patch", [
    {"building_fraction": .7}, {"maintenance_fraction": .5}, {"reentry_fraction": .6},
    {"available_minutes": [0, 0, 0, -1, 0, 0, 0]},
    {"available_minutes": [0, 0]}, {"recent_weekly_hours": [4, 4, 4]},
    {"actual_sport": "NordicSki"}, {"program_end": "2028-01-01"},
    {"age_years": 15, "training_experience_years": 20}, {"hidden_multiplier": 2},
])
def test_profile_rejects_out_of_contract_doses_and_context(patch):
    with pytest.raises(ValidationError):
        ManagementProfile.model_validate({**PROFILE, **patch})


def test_nonfinite_values_are_not_valid_model_inputs():
    for key, value in (("race_duration_min", float("nan")),
                       ("recent_weekly_hours", [1, 2, 3, float("inf")])):
        with pytest.raises(ValidationError):
            ManagementProfile.model_validate({**PROFILE, key: value})


def request(**kwargs):
    return ManagementGenerateRequest(start_date="2026-09-22", expected_profile_revision=2, **kwargs)


def test_changed_inputs_discard_draft_without_a_write(api, monkeypatch):
    _, store, repository = api
    states = iter([{"generation_id": "old"}, {"generation_id": "new"}])
    monkeypatch.setattr(service, "input_state", lambda *_, **__: next(states))
    monkeypatch.setattr(service.training_plan_engine, "generate_plan", lambda *_, **kw: {
        "source": {"generation_id": "old"}, "start_date": kw["start_date"].isoformat(),
    })
    with pytest.raises(HTTPException) as error:
        service.generate(repository, "ath-test", request(), ACTOR, now=NOW)
    assert error.value.status_code == 409 and not store.saved


def test_saved_draft_freezes_profile_source_and_checks_generation(api, monkeypatch):
    _, store, repository = api
    state = {"generation_id": "generation-a", "analysis_revision": 7, "calendar": {"events": []}}
    original = deepcopy(state)
    monkeypatch.setattr(service, "input_state", lambda *_, **__: deepcopy(state))
    monkeypatch.setattr(service.training_plan_engine, "generate_plan", lambda *_, **kw: {
        "source": {"generation_id": "generation-a"}, "start_date": kw["start_date"].isoformat(),
        "status": "DRAFT", "days": [],
    })
    result = service.generate(repository, "ath-test", request(expected_draft_revision=3), ACTOR, now=NOW)
    saved = store.saved[0]
    assert state == original
    assert saved["check_generation"] is True and saved["expected_generation_id"] == "generation-a"
    assert saved["expected_revision"] == 3
    payload = result["payload"]
    assert payload["input_snapshot"]["management_profile"]["building_fraction"] == .5
    assert len(payload["input_fingerprint"]) == 64
    assert payload["review_required"] and not payload["automatically_published"]


def test_changed_profile_is_rejected_before_running_engine(api, monkeypatch):
    _, store, repository = api
    store.current["revision"] = 3
    monkeypatch.setattr(service.training_plan_engine, "generate_plan", lambda *a, **kw: pytest.fail("must not generate"))
    with pytest.raises(HTTPException) as error:
        service.generate(repository, "ath-test", request(), ACTOR, now=NOW)
    assert error.value.status_code == 409 and not store.saved


def test_generation_start_uses_athlete_local_day(api, monkeypatch):
    _, store, repository = api
    # UTC September21 22:00 is already September22 in Sofia.
    body = ManagementGenerateRequest(start_date="2026-09-21", expected_profile_revision=2)
    with pytest.raises(HTTPException) as error:
        service.generate(repository, "ath-test", body, ACTOR,
                         now=datetime(2026, 9, 21, 22, tzinfo=timezone.utc))
    assert error.value.status_code == 422 and not store.saved


def test_draft_history_marks_changed_inputs_without_rewriting_evidence(api, monkeypatch):
    _, store, repository = api
    state = {"generation_id": "generation-a", "analysis_revision": 1}
    fingerprint = service._hash({**state, "management_profile": ManagementProfile.model_validate(PROFILE).model_dump(mode="json"), "profile_revision": 2})
    rows = [{"revision": 1, "payload": {"input_fingerprint": fingerprint, "days": []}}]
    frozen = deepcopy(rows)
    monkeypatch.setattr(store, "drafts", lambda _: rows)
    monkeypatch.setattr(service, "input_state", lambda *_, **__: deepcopy(state))
    fresh = service.history(repository, "ath-test")["drafts"][0]
    assert fresh["stale"] is False and fresh["stale_reason"] is None
    state["analysis_revision"] = 2
    stale = service.history(repository, "ath-test")["drafts"][0]
    assert stale["stale"] is True and stale["stale_reason"]
    assert rows == frozen


def test_draft_history_retains_evidence_if_freshness_cannot_be_checked(api, monkeypatch):
    _, store, repository = api
    monkeypatch.setattr(store, "drafts", lambda _: [{"revision": 1, "payload": {"days": []}}])

    def unavailable(*_, **__):
        raise PersistentStoreFailure("temporary service failure")

    monkeypatch.setattr(service, "input_state", unavailable)
    row = service.history(repository, "ath-test")["drafts"][0]
    assert row["stale"] is None and row["stale_reason"]
    assert row["payload"] == {"days": []}


def test_draft_start_must_fit_program(api):
    _, store, repository = api
    store.current["profile"]["program_end"] = "2026-09-21"
    with pytest.raises(HTTPException) as error:
        service.generate(repository, "ath-test", request(), ACTOR, now=NOW)
    assert error.value.status_code == 422 and not store.saved


def test_dated_history_requests_latest_version_for_that_start_date(api, monkeypatch):
    client, _, repository = api
    calls = []

    def filtered(repo, alias, *, start_date=None):
        calls.append((repo, alias, start_date))
        return {"drafts": []}

    monkeypatch.setattr(service, "history", filtered)
    result = client.get("/api/v2/athlete/management/drafts?start_date=2026-09-22", headers=HEADERS)
    assert result.status_code == 200
    assert calls == [(repository, "ath-test", date(2026, 9, 22))]
    assert client.get("/api/v2/athlete/management/drafts?start_date=2026-02-30", headers=HEADERS).status_code == 422


def test_race_duration_preview_is_scoped_and_does_not_save_profile(api, monkeypatch):
    from apps.api import race_duration
    client, store, repository = api
    calls=[]
    def preview(repo,alias,p):
        calls.append((repo,alias,p))
        return {"source":"MANUAL","duration_min":12}
    monkeypatch.setattr(race_duration,"preview",preview)
    url="/api/v2/athlete/management/race-duration"
    body={"sport":"NordicSki","discipline":"7.5 km sprint","race_duration_min":12}
    assert client.post(url,json=body).status_code==401
    assert client.post(url,json={**body,"sport":"RollerSki"},headers=HEADERS).status_code==422
    result=client.post(url,json=body,headers=HEADERS)
    assert result.status_code==200 and result.json()["duration_min"]==12
    assert calls==[(repository,"ath-test",body)]
    assert not store.saved
