from copy import deepcopy
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException

from apps.api import assistant_commands as commands
from apps.api import assistant_app_actions as actions
from apps.api.model_schemas import initial_settings
from apps.api.oauth_store import _PlanningSettingsBundle
from tests.api.test_assistant_commands import Repository as BaseRepository, ALIAS, ACTOR, command


class Repository(BaseRepository):
    def __init__(self):
        super().__init__()
        self.settings = dict(settings_revision=2, hr_zone_bounds=[90,120,140,160,180,200], timezone='UTC',
            hrmax_bpm=200, hr_zone_source='MANUAL', hr_zone_percentages=None,
            model_preferences={'leading_indicator':'HEART_RATE','age_years':25,'hrmax_source':'MANUAL'})
        self.plan = {'revision':4, 'payload':{'status':'ACTIVE','plan':{'start_date':'2026-11-10','end_date':'2026-11-16'}}}
        self.rows[('RECOVERY','recovery-v2')] = {'kind':'RECOVERY','entry_key':'recovery-v2',
            'revision':3,'payload':{'schema_version':'recovery-config-v2','zones':initial_settings()}}

    def connection(self, alias):
        return SimpleNamespace(status='CONNECTED')

    def athlete_settings(self, alias):
        return _PlanningSettingsBundle([self.settings]).athlete_settings()

    def latest_model_entries(self, alias):
        return [deepcopy(row) for row in self.rows.values()]

    def _request(self, method, path, **kwargs):
        if path.startswith('/onflows_athlete_settings?select=settings_revision'):
            return [deepcopy(self.settings)]
        if path.startswith('/onflows_management_plan_state?'):
            return [{'revision':self.plan['revision'],'record':deepcopy(self.plan)}]
        if path == '/rpc/onflows_management_checkpoint':
            return {'models':[]}
        if path == '/rpc/save_onflows_assistant_app_operation':
            body=kwargs['json']; self.commits.append(deepcopy(body))
            result=dict(saved=True,status='SAVED',revision=body['p_expected_revision']+1,
                kind=body['p_kind'],entry_key=body['p_key'],recorded_at='2026-11-12T12:00:00+00:00',
                request_hash=body['p_request_hash'],operation_id=body['p_operation_id'])
            if body['p_payload'].get('rpc') == 'queue_onflows_plan_refresh':
                result.update(queued=True,refresh_state='QUEUED')
            self.receipts[body['p_operation_id']] = deepcopy(result)
            return result
        return super()._request(method,path,**kwargs)


def test_settings_partial_update_preserves_existing_timezone_and_zones():
    repo=Repository()
    result=commands.execute(repo,ALIAS,ACTOR,uuid4(),command('ATHLETE_SETTINGS',{'hrmax_bpm':195},2,'athlete-settings'))
    payload=repo.commits[0]['p_payload']
    assert result['saved'] and payload['hrmax_bpm']==195
    assert payload['timezone']=='UTC' and payload['hr_zone_bounds'][1:5]==[120,140,160,180]
    assert payload['model_preferences']['age_years']==25
    assert 'p_actor' not in payload


def test_settings_read_revision_and_values_come_from_same_snapshot():
    repo=Repository()
    repo.athlete_settings=lambda alias: (_ for _ in ()).throw(AssertionError('second read'))
    value=actions.read_input(repo,ALIAS,'ATHLETE_SETTINGS')
    assert value['revision']==2 and value['payload']['hrmax_bpm']==200


@pytest.mark.parametrize('kind,key,patch,revision',[
    ('ATHLETE_SETTINGS','athlete-settings',{'hrmax_bpm':195},1),
    ('RECOVERY_CONFIG','recovery-v2',{'zones':{'Z1':{'sensitivity':1.2}}},2),
    ('PLAN_ACTION','active-plan',{'action':'PAUSE'},3),
])
def test_stale_application_inputs_cannot_write(kind,key,patch,revision):
    repo=Repository()
    with pytest.raises(HTTPException) as exc:
        commands.execute(repo,ALIAS,ACTOR,uuid4(),command(kind,patch,revision,key))
    assert exc.value.status_code==409 and not repo.commits


def test_recovery_partial_zone_update_reuses_canonical_writer():
    repo=Repository()
    commands.execute(repo,ALIAS,ACTOR,uuid4(),command('RECOVERY_CONFIG',{'zones':{'Z1':{'sensitivity':1.2}}},3,'recovery-v2'))
    payload=repo.commits[0]['p_payload']
    assert payload['schema_version']=='recovery-config-v2'
    assert payload['zones']['Z1']['sensitivity']==1.2
    assert payload['zones']['Z2']==initial_settings()['Z2']


def test_plan_pause_runs_real_lifecycle_and_preserves_compact_publication_contract(monkeypatch):
    from apps.api import management_lifecycle
    repo=Repository()
    monkeypatch.setattr(management_lifecycle,'_context',lambda *a,**kw:(3,{},
        {'evaluation_date':'2026-11-12'},'fingerprint','approval'))
    result=commands.execute(repo,ALIAS,ACTOR,uuid4(),command('PLAN_ACTION',{'action':'PAUSE'},4,'active-plan'))
    assert result['saved'] and result['revision']==5
    envelope=repo.commits[0]['p_payload']
    assert envelope['rpc']=='save_onflows_management_plan_compact'
    assert envelope['arguments']['p_payload']['status']=='PAUSED'
    assert envelope['arguments']['p_operation']=='PAUSE'
    assert envelope['arguments']['p_actor']==str(ACTOR)
    assert envelope['arguments']['p_expected_revision']==4


def test_refresh_queues_existing_worker_and_replay_does_not_enqueue_again():
    repo=Repository(); operation=uuid4()
    change=command('PLAN_ACTION',{'action':'REFRESH'},4,'active-plan')
    first=commands.execute(repo,ALIAS,ACTOR,operation,change)
    again=commands.execute(repo,ALIAS,ACTOR,operation,change)
    assert first==again and first['refresh_state']=='QUEUED' and len(repo.commits)==1
    assert repo.commits[0]['p_payload']['rpc']=='queue_onflows_plan_refresh'


@pytest.mark.parametrize('kind,key,patch,revision',[
    ('ATHLETE_SETTINGS','athlete-settings',{'hrmax_bpm':195},2),
    ('RECOVERY_CONFIG','recovery-v2',{'zones':{'Z1':{'sensitivity':1.2}}},3),
    ('PLAN_ACTION','active-plan',{'action':'PAUSE'},4),
])
def test_read_only_actor_cannot_use_app_commands(kind,key,patch,revision):
    repo=Repository();repo.permissions['can_edit_plan']=False
    with pytest.raises(HTTPException) as exc:
        commands.execute(repo,ALIAS,ACTOR,uuid4(),command(kind,patch,revision,key))
    assert exc.value.status_code==403 and not repo.commits


def test_plan_only_editor_can_pause_and_edit_settings_but_not_recovery_config(monkeypatch):
    from apps.api import management_lifecycle
    repo=Repository();repo.permissions['can_view_recovery']=False;repo.permissions['is_owner']=False
    monkeypatch.setattr(management_lifecycle,'_context',lambda *a,**kw:(3,{},
        {'evaluation_date':'2026-11-12'},'fingerprint','approval'))
    commands.execute(repo,ALIAS,ACTOR,uuid4(),command('PLAN_ACTION',{'action':'PAUSE'},4,'active-plan'))
    with pytest.raises(HTTPException) as exc:
        commands.execute(repo,ALIAS,ACTOR,uuid4(),command('RECOVERY_CONFIG',{'zones':{'Z1':{'sensitivity':1.2}}},3,'recovery-v2'))
    assert exc.value.status_code==403 and len(repo.commits)==1


def test_model_cannot_supply_rpc_arguments_or_saved_plan_payload():
    repo=Repository()
    with pytest.raises(HTTPException) as exc:
        commands.execute(repo,ALIAS,ACTOR,uuid4(),command('PLAN_ACTION',{'action':'PAUSE','rpc':'anything'},4,'active-plan'))
    assert exc.value.status_code==422 and not repo.commits


def test_plan_only_editor_has_same_settings_write_as_form():
    repo=Repository();repo.permissions.update(is_owner=False,can_view_recovery=False)
    result=commands.execute(repo,ALIAS,ACTOR,uuid4(),command('ATHLETE_SETTINGS',{'hrmax_bpm':195},2,'athlete-settings'))
    assert result['saved'] and repo.commits[0]['p_payload']['hrmax_bpm']==195


def test_saved_segment_disable_keeps_frozen_measurement_and_uses_canonical_save():
    from hashlib import sha256
    repo=Repository();ref='act_'+'a'*32
    key=sha256(f'{ref}:0:180'.encode()).hexdigest()[:32]
    payload=dict(activity_ref=ref,start_s=0,duration_s=180,test_mode='STRICT',maximal=True,
        comparable=True,enabled=True,conditions='Measured on track',speed_kmh=20.,source_run_key='b'*64)
    repo.rows[('SPEED_TEST',key)]={'kind':'SPEED_TEST','entry_key':key,'revision':2,'payload':payload}
    result=commands.execute(repo,ALIAS,ACTOR,uuid4(),command('SPEED_TEST',{'enabled':False},2,key))
    assert result['saved']
    assert repo.commits[0]['p_payload']=={**payload,'enabled':False}


def test_new_segment_requires_explicit_maximal_and_comparable_test_context():
    repo=Repository()
    with pytest.raises(HTTPException) as exc:
        commands.execute(repo,ALIAS,ACTOR,uuid4(),command('SPEED_TEST',{
            'activity_ref':'act_'+'a'*32,'start_s':0,'duration_s':180,'conditions':'Track'},0,''))
    assert exc.value.status_code==422 and not repo.commits


def test_approval_input_summary_has_exact_sessions_without_private_evidence():
    repo=Repository()
    repo.plan['payload']['proposal']={'start_date':'2026-11-12','end_date':'2026-11-18',
        'input_snapshot':{'hidden':'secret'},'days':[{'date':'2026-11-13','status':'PLANNED','sessions':[
            {'title':'Прагова тренировка','zone':'Z3','total_minutes':60,'dose_evidence':{'readiness_percent':73},
             'blocks':[{'kind':'WORK','duration_min':10,'target_hr_bpm':[160,175],'internal':'hidden'}]}]}]}
    result=actions.read_input(repo,ALIAS,'PLAN_ACTION')
    summary=result['preview_summary']
    assert summary['revision']==4 and summary['proposal']['days'][0]['sessions'][0]['title']=='Прагова тренировка'
    assert 'secret' not in str(summary) and 'readiness' not in str(summary) and 'internal' not in str(summary)


def test_approve_with_no_proposal_is_actionable_conflict():
    repo=Repository()
    with pytest.raises(HTTPException) as exc:
        commands.execute(repo,ALIAS,ACTOR,uuid4(),command('PLAN_ACTION',{'action':'APPROVE'},4,'active-plan'))
    assert exc.value.status_code==409 and not repo.commits


def test_initial_generation_uses_canonical_worker_job_and_exact_profile_revision(monkeypatch):
    from datetime import datetime, timezone
    from apps.api import management_store
    repo=Repository(); day=datetime.now(timezone.utc).date().isoformat()
    monkeypatch.setattr(management_store.ManagementStore,'profile',lambda *args:{'configured':True,'revision':3,'profile':{}})
    monkeypatch.setattr(management_store.ManagementStore,'draft_metadata',lambda *args:{'revision':0})
    original=repo._request
    def request(method,path,**kwargs):
        result=original(method,path,**kwargs)
        if path=='/rpc/save_onflows_assistant_app_operation' and kwargs['json']['p_kind']=='PLAN_GENERATE':
            result.update(queued=True,refresh_state='QUEUED',job={'job_id':str(uuid4()),'status':'QUEUED','stage':'QUEUED','start_date':day})
            repo.receipts[kwargs['json']['p_operation_id']]=deepcopy(result)
        return result
    repo._request=request
    current=actions.read_input(repo,ALIAS,'PLAN_GENERATE',day)
    assert current['entry_key']==day and current['revision']==3
    assert current['payload']=={'start_date':day,'expected_profile_revision':3,'expected_draft_revision':0}
    operation=uuid4(); change=command('PLAN_GENERATE',{'start_date':day},3,day)
    saved=commands.execute(repo,ALIAS,ACTOR,operation,change)
    assert saved['job']['status']=='QUEUED' and saved['queued']
    assert repo.commits[0]['p_payload']=={'request':current['payload']}
    assert commands.execute(repo,ALIAS,ACTOR,operation,change)==saved and len(repo.commits)==1


def test_activation_input_can_target_exact_draft_outside_recent_groups(monkeypatch):
    from datetime import date
    from apps.api import management_store
    repo=Repository();calls=[]
    old={'kind':'DRAFT','entry_key':'2026-09-20','revision':7,'payload':{
        'start_date':'2026-09-20','end_date':'2026-09-26','days':[{'date':'2026-09-20','status':'REST'}]}}
    def targeted(self,alias,start,revision):
        calls.append((alias,start,revision))
        return [deepcopy(old)]
    monkeypatch.setattr(management_store.ManagementStore,'draft_revision',targeted)
    monkeypatch.setattr(management_store.ManagementStore,'drafts',lambda *a,**k:(_ for _ in ()).throw(AssertionError('unbounded/recent draft lookup')))
    result=actions.read_input(repo,ALIAS,'PLAN_ACTIVATE','active-plan',draft_date='2026-09-20',draft_revision=7)
    assert result['entry_key']=='active-plan' and result['revision']==4
    assert result['preview_summary']['drafts'][0]['draft_revision']==7
    assert result['preview_summary']['drafts'][0]['plan']['start_date']=='2026-09-20'
    assert calls==[(ALIAS,date(2026,9,20),7)]


def test_activation_date_without_revision_reads_latest_for_exact_date(monkeypatch):
    from datetime import date
    from apps.api import management_store
    repo=Repository();calls=[]
    def targeted(self,alias,**kwargs):
        calls.append(kwargs)
        return [{'entry_key':'2026-11-13','revision':9,'payload':{'start_date':'2026-11-13','end_date':'2026-11-19','days':[]}}]
    monkeypatch.setattr(management_store.ManagementStore,'drafts',targeted)
    result=actions.read_input(repo,ALIAS,'PLAN_ACTIVATE',draft_date='2026-11-13')
    assert calls==[{'start_date':date(2026,11,13),'limit':1}]
    assert result['preview_summary']['drafts'][0]['draft_revision']==9


def test_activation_unknown_exact_draft_is_not_replaced_with_recent_one(monkeypatch):
    from apps.api import management_store
    repo=Repository()
    monkeypatch.setattr(management_store.ManagementStore,'draft_revision',lambda *a,**k:[])
    with pytest.raises(HTTPException) as error:
        actions.read_input(repo,ALIAS,'PLAN_ACTIVATE',draft_date='2026-09-20',draft_revision=7)
    assert error.value.status_code==404
