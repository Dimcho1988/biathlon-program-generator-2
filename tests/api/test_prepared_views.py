from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock

from fastapi import HTTPException
from fastapi.testclient import TestClient
import pytest

from apps.api import dependencies, main, prepared_views, management_service
from apps.api.activity_derivations import reuse
from apps.api.oauth_store import PersistentStoreFailure

HEADERS = {"Authorization":"Bearer prepared-test", "X-OnFlows-Athlete-Alias":"ath-prepared"}


@pytest.mark.parametrize("kind,url,parameters", [
    ("speed","/api/v2/athlete/models/speed?sport=Run",{"sport":"Run"}),
    ("speed-load","/api/v2/athlete/models/speed-load",{}),
    ("response","/api/v2/athlete/response",{}),
    ("trainability","/api/v2/real/trainability?period_start=2026-11-01&period_end=2026-11-05",
        {"period_start":"2026-11-01","period_end":"2026-11-05"}),
    ("management-view","/api/v2/athlete/management/view?view=overview&projection=overview-v1",
        {"view":"overview","projection":"overview-v1"}),
    ("management-active","/api/v2/athlete/management/active",{}),
    ("management-state","/api/v2/athlete/management/state",{}),
    ("management-methods","/api/v2/athlete/management/methods",{}),
    ("management-speed-comparison","/api/v2/athlete/management/speed-comparison?kind=ACTIVE&revision=3",
        {"kind":"ACTIVE","revision":3}),
])
def test_http_is_one_prepared_read_with_no_scientific_inputs(monkeypatch,kind,url,parameters):
    calls=[]
    def operation(op,**kw):
        calls.append((op,kw))
        return {"ready":True,"athlete_alias":"ath-prepared","kind":kind,"payload":{"unchanged":12.345}}
    repo=SimpleNamespace(prepared_view=operation)
    monkeypatch.setenv("ONFLOWS_SERVICE_TOKEN","prepared-test")
    monkeypatch.setattr(dependencies,"repository",lambda:repo)
    response=TestClient(main.app).get(url,headers=HEADERS)
    assert response.status_code==200 and response.json()=={"unchanged":12.345}
    assert len(calls)==1 and calls[0][0]=="READ"
    assert calls[0][1]["parameters"]==parameters
    assert calls[0][1]["alias"]=="ath-prepared" and calls[0][1]["kind"]==kind
    assert len(calls[0][1]["model_key"])==64


@pytest.mark.parametrize("state",[None,{"ready":False},{"ready":True,"athlete_alias":"other","kind":"speed","payload":{}},
    {"ready":True,"athlete_alias":"ath-prepared","kind":"response","payload":{}}])
def test_cold_or_wrong_scope_only_reports_preparation(monkeypatch,state):
    rpc=Mock(return_value=state)
    repo=SimpleNamespace(prepared_view=rpc,active_analysis=Mock(side_effect=AssertionError("HTTP model work")))
    monkeypatch.setenv("ONFLOWS_SERVICE_TOKEN","prepared-test")
    monkeypatch.setattr(dependencies,"repository",lambda:repo)
    response=TestClient(main.app).get("/api/v2/athlete/models/speed",headers=HEADERS)
    assert response.status_code==503 and response.headers["x-onflows-preparation"]=="pending"
    assert rpc.call_count==1 and rpc.call_args.args==("READ",)
    repo.active_analysis.assert_not_called()


def test_authentication_and_explicit_athlete_precede_prepared_storage(monkeypatch):
    repository=Mock()
    monkeypatch.setenv("ONFLOWS_SERVICE_TOKEN","prepared-test")
    monkeypatch.setattr(dependencies,"repository",repository)
    client=TestClient(main.app)
    assert client.get("/api/v2/athlete/models/speed").status_code==401
    assert client.get("/api/v2/athlete/models/speed",headers={"Authorization":"Bearer prepared-test"}).status_code==401
    repository.assert_not_called()


def test_store_failure_never_falls_through_to_calculation():
    repo=SimpleNamespace(prepared_view=Mock(side_effect=PersistentStoreFailure("private")))
    with pytest.raises(HTTPException) as error: prepared_views.read(repo,"ath-prepared","speed")
    assert error.value.status_code==503 and "private" not in error.value.detail


def test_worker_uses_original_view_and_resets_its_private_context(monkeypatch):
    repository=SimpleNamespace(athlete_settings=lambda alias:{"athlete":alias})
    def original(athlete_alias=None,authorization=None):
        dependencies.authorize(authorization)
        return {"value":dependencies.repository().athlete_settings(athlete_alias)}
    prepared_views._load_registry()
    monkeypatch.setitem(prepared_views._registry,"speed",original)
    expected={"value":{"athlete":"ath-prepared"}}
    assert prepared_views.calculate(repository,"ath-prepared","speed",{})==expected
    assert prepared_views.worker_repository() is None
    with pytest.raises(HTTPException): dependencies.authorize(None)


def test_worker_context_does_not_leak_after_failed_computation(monkeypatch):
    prepared_views._load_registry()
    def original(athlete_alias=None,authorization=None): raise RuntimeError("failed calculation")
    monkeypatch.setitem(prepared_views._registry,"speed",original)
    with pytest.raises(RuntimeError): prepared_views.calculate(SimpleNamespace(),"ath-prepared","speed",{})
    assert prepared_views.worker_repository() is None


def test_publication_only_uses_the_owned_claim(monkeypatch):
    calls=[]
    def operation(op,**kw):
        calls.append((op,kw))
        if op=="CLAIM": return {"athlete_alias":"ath-prepared","kind":"speed","parameters":{},"revision":4,"day":"2026-11-06"}
        if op=="STORE": return {"stored":True}
        return {"state":"RUNNING"}
    repo=SimpleNamespace(prepared_view=operation)
    calculate=Mock(return_value={"values":[12.345]})
    monkeypatch.setattr(prepared_views,"calculate",calculate)
    assert prepared_views.process_next(repo)
    assert [op for op,_ in calls]==["CLAIM","STORE"]
    assert calls[0][1]["token"]==calls[1][1]["token"]
    assert calls[1][1]["revision"]==4 and calls[1][1]["payload"]=={"values":[12.345]}


def test_small_activity_results_reuse_only_matching_persisted_keys():
    existing={"key-a":{"q":1.125}}
    writer=Mock()
    repo=SimpleNamespace(read_activity_derivations=Mock(return_value=deepcopy(existing)),write_activity_derivations=writer)
    calculate=Mock(return_value={"key-b":{"q":2.25}})
    result=reuse(repo,"ath-prepared","SPEED_Q",["key-a","key-b"],calculate)
    assert result=={"key-a":{"q":1.125},"key-b":{"q":2.25}}
    calculate.assert_called_once_with(["key-b"])
    writer.assert_called_once_with("ath-prepared","SPEED_Q",{"key-b":{"q":2.25}})


def test_manual_refresh_accepts_without_running_the_generator(monkeypatch):
    enqueue=Mock(return_value={"queued":True})
    monkeypatch.setenv("ONFLOWS_SERVICE_TOKEN","prepared-test")
    monkeypatch.setattr(dependencies,"repository",lambda:SimpleNamespace(queue_plan_refresh=enqueue))
    monkeypatch.setattr(management_service,"build_draft",Mock(side_effect=AssertionError("HTTP calculation")))
    actor="11111111-1111-4111-8111-111111111111"
    result=TestClient(main.app).post("/api/v2/athlete/management/action",
        headers={**HEADERS,"X-OnFlows-Actor-Id":actor},json={"action":"REFRESH","expected_revision":3})
    assert result.status_code==202 and result.json()["background_update"]["status"]=="QUEUED"
    enqueue.assert_called_once_with("ath-prepared",actor,3,False)
    management_service.build_draft.assert_not_called()
