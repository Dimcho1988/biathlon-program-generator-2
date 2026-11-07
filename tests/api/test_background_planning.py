from copy import deepcopy
from datetime import date
from threading import Event
from types import SimpleNamespace

import httpx
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from apps.api import dependencies, main, management_service, planning_jobs, background_planning
from apps.api.management_store import ManagementStore
from apps.api.oauth_store import SupabasePilotRepository, PersistentStoreFailure, PlanStorageCapacityExceeded

ACTOR = '11111111-1111-4111-8111-111111111111'
JOB = '22222222-2222-4222-8222-222222222222'
BODY = {'start_date':'2026-11-02','expected_profile_revision':1,'expected_draft_revision':0}
STATE = {'job_id':JOB,'status':'QUEUED','stage':'QUEUED','start_date':BODY['start_date'],
         'result_revision':None,'error_code':None,'attempts':0}
HEADERS = {'Authorization':'Bearer test-background','X-OnFlows-Athlete-Alias':'ath-job','X-OnFlows-Actor-Id':ACTOR}
URL = '/api/v2/athlete/management/generation-jobs'


def test_http_accepts_durably_without_running_the_generator(monkeypatch):
    calls=[]
    def rpc(operation,**arguments):
        calls.append((operation,arguments)); return deepcopy(STATE)
    monkeypatch.setenv('ONFLOWS_SERVICE_TOKEN','test-background')
    monkeypatch.setattr(dependencies,'repository',lambda:SimpleNamespace(planning_job=rpc))
    monkeypatch.setattr(management_service,'build_draft',lambda *a,**k:pytest.fail('HTTP must not calculate'))
    client=TestClient(main.app)
    response=client.post(URL,headers=HEADERS,json=BODY)
    assert response.status_code==202
    assert response.headers['cache-control']=='no-store' and response.headers['retry-after']=='5'
    assert response.json()['job']['job_id']==JOB
    assert calls==[('ENQUEUE',{'alias':'ath-job','actor':ACTOR,'request':BODY})]
    no_actor={k:v for k,v in HEADERS.items() if k!='X-OnFlows-Actor-Id'}
    assert client.post(URL,headers=no_actor,json=BODY).status_code==401
    assert client.post(URL,json=BODY).status_code==401
    assert len(calls)==1


def test_progress_is_scoped_read_only_and_filters_worker_fields(monkeypatch):
    calls=[]
    def rpc(operation,**arguments):
        calls.append((operation,arguments));return {'job':{**STATE,'actor_id':'private','request':{'private':True}}}
    monkeypatch.setenv('ONFLOWS_SERVICE_TOKEN','test-background')
    monkeypatch.setattr(dependencies,'repository',lambda:SimpleNamespace(planning_job=rpc))
    response=TestClient(main.app).get(URL+'?job_id='+JOB,headers=HEADERS)
    assert response.status_code==200 and 'private' not in response.text
    assert calls==[('STATUS',{'alias':'ath-job','actor':ACTOR,'job':JOB})]
    assert TestClient(main.app).get(URL+'?job_id=not-a-uuid',headers=HEADERS).status_code==422
    assert len(calls)==1


def test_enqueue_conflict_and_unknown_job_do_not_calculate():
    from apps.api.management_schemas import ManagementGenerateRequest
    with pytest.raises(HTTPException) as error:
        planning_jobs.enqueue(SimpleNamespace(planning_job=lambda *a,**k:{'conflict':True}),
            'ath-job',ManagementGenerateRequest(**BODY),ACTOR)
    assert error.value.status_code==409
    with pytest.raises(HTTPException) as error:
        planning_jobs.status(SimpleNamespace(planning_job=lambda *a,**k:{'job':None}),'ath-job',ACTOR,JOB)
    assert error.value.status_code==404


class WorkerRepository:
    def __init__(self):self.calls=[];self.lost=False
    def planning_job(self,operation,**arguments):
        self.calls.append((operation,deepcopy(arguments)))
        if operation=='CLAIM':return {'job_id':JOB,'athlete_alias':'ath-job','actor_id':ACTOR,'request':BODY}
        if operation=='RENEW':return {'state':'LOST'} if self.lost else {'status':'RUNNING'}
        if operation=='COMPLETE':return {**STATE,'status':'SUCCEEDED'}
        return {'status':'FAILED'}


def test_worker_reuses_the_exact_generator_payload_and_only_publishes_through_fencing(monkeypatch):
    repository=WorkerRepository();payload={'start_date':BODY['start_date'],'q':{'Z4':3.125},'blocks':[{'seconds':180}]}
    def build(repo,alias,start,revision,*,on_progress):
        assert repo is repository and alias=='ath-job' and start==date(2026,11,2) and revision==1
        on_progress('BASELINE');on_progress('SEARCH');on_progress('SEARCH')
        return deepcopy(payload)
    monkeypatch.setattr(management_service,'build_draft',build)
    assert planning_jobs.process_next(repository)
    assert [op for op,_ in repository.calls]==['CLAIM','RENEW','RENEW','RENEW','COMPLETE']
    assert [args['stage'] for op,args in repository.calls if op=='RENEW']==['BASELINE','SEARCH','SAVING']
    assert repository.calls[-1][1]['payload']==payload
    assert len({args['token'] for _,args in repository.calls})==1


@pytest.mark.parametrize('failure,code',[(HTTPException(409,'changed'),'INPUT_CHANGED'),
    (HTTPException(422,'invalid'),'INVALID_INPUT'),(PersistentStoreFailure('network'),'TEMPORARILY_UNAVAILABLE'),
    (HTTPException(503,'capacity busy'),'TEMPORARILY_UNAVAILABLE'),
    (HTTPException(504,'upstream timeout'),'TEMPORARILY_UNAVAILABLE'),
    (HTTPException(429,'rate limited'),'TEMPORARILY_UNAVAILABLE'),
    (PlanStorageCapacityExceeded('size limit'),'COMPUTATION_FAILED'),
    (RuntimeError('private-value'),'COMPUTATION_FAILED')])
def test_failed_calculation_is_not_published_and_uses_a_safe_retry_class(monkeypatch,failure,code,caplog):
    repository=WorkerRepository()
    def build(*a,**kw):raise failure
    monkeypatch.setattr(management_service,'build_draft',build)
    assert planning_jobs.process_next(repository)
    assert [op for op,_ in repository.calls]==['CLAIM','FAIL']
    assert repository.calls[-1][1]['stage']==code
    assert 'private-value' not in caplog.text


def test_lost_job_lease_never_reaches_publication(monkeypatch):
    repository=WorkerRepository();repository.lost=True
    def build(*a,on_progress,**k):on_progress('BASELINE');pytest.fail('Lost owner continued calculation')
    monkeypatch.setattr(management_service,'build_draft',build)
    planning_jobs.process_next(repository)
    assert not any(op=='COMPLETE' for op,_ in repository.calls)


def test_shutdown_drains_the_inflight_job_without_accepting_another():
    stopping,entered,release=Event(),Event(),Event();calls=[]
    def process():
        calls.append('job');entered.set();assert release.wait(2);return True
    thread=background_planning.start(SimpleNamespace(process_next_planning_job=process,
        refresh_due_training_plans=lambda:calls.append('automatic')),stopping)
    try:
        assert entered.wait(1) and thread is not None and not thread.daemon
        stopping.set();assert thread.is_alive()
    finally:
        release.set();thread.join(2)
    assert not thread.is_alive() and calls==['job']


def test_metadata_and_exact_revision_reads_are_small_and_athlete_scoped():
    calls=[]
    def request(method,path):calls.append((method,path));return [{'revision':7}]
    repo=SimpleNamespace(_request=request,_json=lambda v:v)
    assert ManagementStore(repo).draft_metadata('ath&other=1',date(2026,11,2))=={'revision':7}
    assert 'select=revision' in calls[0][1] and 'ath%26other%3D1' in calls[0][1] and 'limit=1' in calls[0][1]


def test_rpc_adapter_does_not_retry_an_uncertain_queue_write():
    calls=[]
    def handler(request):calls.append(request);raise httpx.ReadTimeout('uncertain')
    repo=SupabasePilotRepository(supabase_url='https://offline.invalid',secret_key='offline',
        encryption_key='AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=',client=httpx.Client(transport=httpx.MockTransport(handler)))
    with pytest.raises(PersistentStoreFailure):repo.planning_job('ENQUEUE',alias='ath-job',actor=ACTOR,request=BODY)
    assert len(calls)==1
