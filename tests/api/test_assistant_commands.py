"""Conversation writes preserve unrelated inputs and use the canonical service."""
from copy import deepcopy
from datetime import datetime, timezone
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException
from apps.api import assistant_commands as commands
from apps.api.body_observations import WeightReport
from apps.api.response_monitoring import DailyReport
from apps.api import response_service

ALIAS = 'ath-assistant'
ACTOR = UUID('11111111-1111-4111-8111-111111111111')
DAY = datetime.now(timezone.utc).date().isoformat()


class Repository:
    def __init__(self):
        self.rows = {}
        self.receipts = {}
        self.commits = []
        self.permissions = dict(is_owner=True,can_view_plan=True,can_view_recovery=True,can_edit_plan=True)

    def athlete_settings(self, alias):
        assert alias == ALIAS
        return SimpleNamespace(timezone='UTC')

    def activity_detail(self, alias, reference):
        assert alias == ALIAS
        return {'local_date':DAY,'sport':'Run'} if reference == 'act_'+'a'*32 else None

    def _json(self, response):
        return response

    def _request(self, method, path, **kwargs):
        if method == 'GET':
            query = parse_qs(urlsplit(path).query)
            if 'entry_key' not in query:
                return []
            kind = query['kind'][0][3:]
            key = query['entry_key'][0][3:]
            row = self.rows.get((kind,key))
            return [deepcopy(row)] if row else []
        body = kwargs['json']
        if path == '/rpc/onflows_assistant_access':
            return deepcopy(self.permissions)
        if path == '/rpc/onflows_assistant_operation_status':
            if not (self.permissions['can_view_plan'] or self.permissions['can_view_recovery']):
                return {'forbidden':True}
            return deepcopy(self.receipts.get(body['p_operation_id'], {'status':'NOT_FOUND'}))
        if path == '/rpc/save_onflows_assistant_operation':
            old = self.receipts.get(body['p_operation_id'])
            if old:
                assert old['request_hash'] == body['p_request_hash']
                return deepcopy(old)
            kind = body['p_kind']
            key = body['p_key']
            current = self.rows.get((kind,key),{}).get('revision',0)
            if current != body['p_expected_revision']:
                result = {'status':'CONFLICT','conflict':True,'revision':current}
            else:
                self.rows[(kind,key)] = {'revision':current+1,'payload':deepcopy(body['p_payload'])}
                self.commits.append(deepcopy(body))
                result = {'status':'SAVED','saved':True,'revision':current+1}
            result.update(operation_id=body['p_operation_id'],request_hash=body['p_request_hash'])
            self.receipts[body['p_operation_id']] = deepcopy(result)
            return result
        if path == '/rpc/save_onflows_response_entry':
            self.commits.append(deepcopy(body))
            return {'saved':True,'revision':body['p_expected_revision']+1}
        raise AssertionError(path)


def command(kind,patch,revision=0,key=DAY):
    return dict(kind=kind,entry_key=key,expected_revision=revision,patch=patch)


def test_daily_partial_patch_preserves_flags_note_and_unknown_fields():
    repo = Repository()
    repo.rows[('DAILY',DAY)] = {'revision':3,'payload':dict(day=DAY,sleep_quality=2,
        fatigue=1,pain_or_illness=True,note='Старо наблюдение',future_field={'x':1})}
    result = commands.execute(repo,ALIAS,ACTOR,uuid4(),command('DAILY',{'fatigue':4},3))
    assert result['saved'] and result['revision'] == 4
    payload = repo.commits[0]['p_payload']
    assert payload['sleep_quality'] == 2 and payload['fatigue'] == 4
    assert payload['pain_or_illness'] and payload['note'] == 'Старо наблюдение'
    assert payload['future_field'] == {'x':1} and payload['input_method'] == 'CHAT'


def test_weight_session_patch_preserves_morning_before_other_sessions():
    repo = Repository()
    repo.rows[('WEIGHT',DAY)] = {'revision':1,'payload':dict(day=DAY,morning_kg=72.4,
        morning_standardized=True,sessions=[dict(session=1,before_kg=72.8,fluid_l=.5),
                                           dict(session=2,before_kg=73.)],note='Измерено')}
    commands.execute(repo,ALIAS,ACTOR,uuid4(),command('WEIGHT',{'sessions':[{'session':1,'after_kg':72.1}]},1))
    payload = repo.commits[0]['p_payload']
    assert payload['morning_kg'] == 72.4 and payload['morning_standardized']
    assert payload['sessions'][0]['before_kg'] == 72.8 and payload['sessions'][0]['after_kg'] == 72.1
    assert payload['sessions'][0]['fluid_l'] == .5 and payload['sessions'][1]['before_kg'] == 73.
    assert payload['sessions'][0]['comparable'] is False


def test_explicit_null_clears_only_that_field():
    repo = Repository()
    repo.rows[('DAILY',DAY)] = {'revision':1,'payload':dict(day=DAY,sleep_quality=2,fatigue=3,note='Пази')}
    commands.execute(repo,ALIAS,ACTOR,uuid4(),command('DAILY',{'sleep_quality':None},1))
    payload = repo.commits[0]['p_payload']
    assert payload['sleep_quality'] is None and payload['fatigue'] == 3 and payload['note'] == 'Пази'


def test_same_operation_replays_even_after_input_changed():
    repo = Repository()
    operation = uuid4()
    change = command('DAILY',{'fatigue':4})
    first = commands.execute(repo,ALIAS,ACTOR,operation,change)
    repo.rows[('DAILY',DAY)] = {'revision':7,'payload':dict(day=DAY,fatigue=2)}
    again = commands.execute(repo,ALIAS,ACTOR,operation,change)
    assert first == again and len(repo.commits) == 1


def test_reused_operation_with_different_command_is_rejected():
    repo = Repository(); operation = uuid4()
    commands.execute(repo,ALIAS,ACTOR,operation,command('DAILY',{'fatigue':4}))
    with pytest.raises(HTTPException) as raised:
        commands.execute(repo,ALIAS,ACTOR,operation,command('DAILY',{'fatigue':2}))
    assert raised.value.status_code == 409 and len(repo.commits) == 1


def test_stale_revision_never_merges_over_concurrent_edit():
    repo = Repository()
    repo.rows[('DAILY',DAY)] = {'revision':2,'payload':dict(day=DAY,fatigue=2)}
    with pytest.raises(HTTPException) as raised:
        commands.execute(repo,ALIAS,ACTOR,uuid4(),command('DAILY',{'fatigue':4},1))
    assert raised.value.status_code == 409 and not repo.commits


@pytest.mark.parametrize('permissions',[
    dict(is_owner=False,can_view_plan=True,can_view_recovery=False,can_edit_plan=True),
    dict(is_owner=False,can_view_plan=True,can_view_recovery=True,can_edit_plan=True),
    dict(is_owner=False,can_view_plan=True,can_view_recovery=True,can_edit_plan=False),
])
def test_daily_owner_only_even_for_editing_coach(permissions):
    repo=Repository();repo.permissions=permissions
    with pytest.raises(HTTPException) as raised:
        commands.execute(repo,ALIAS,ACTOR,uuid4(),command('DAILY',{'fatigue':4}))
    assert raised.value.status_code == 403 and not repo.commits


def test_revoked_access_blocks_receipt_replay():
    repo=Repository();operation=uuid4();change=command('DAILY',{'fatigue':4})
    commands.execute(repo,ALIAS,ACTOR,operation,change)
    repo.permissions=dict.fromkeys(repo.permissions,False)
    with pytest.raises(HTTPException) as raised:
        commands.execute(repo,ALIAS,ACTOR,operation,change)
    assert raised.value.status_code == 403


@pytest.mark.parametrize('kind,patch',[
    ('DAILY',{'note':'Само бележка'}),('DAILY',{'fatigue':5.0}),('DAILY',{'fatigue':True}),
    ('DAILY',{'posterior':2}),('WEIGHT',{'morning_kg':0}),
    ('WEIGHT',{'sessions':[{'session':1,'after_kg':70.},{'session':1,'after_kg':71.}]}),
])
def test_invalid_patch_has_no_write(kind,patch):
    repo=Repository()
    with pytest.raises(HTTPException) as raised:
        commands.execute(repo,ALIAS,ACTOR,uuid4(),command(kind,patch))
    assert raised.value.status_code == 422 and not repo.commits


@pytest.mark.parametrize('kind,model,patch',[
    ('DAILY',DailyReport,{'fatigue':4,'sleep_hours':8.}),
    ('WEIGHT',WeightReport,{'morning_kg':72.4}),
])
def test_form_and_chat_share_canonical_payload_except_provenance(kind,model,patch):
    chat=Repository();form=Repository()
    commands.execute(chat,ALIAS,ACTOR,uuid4(),command(kind,patch))
    response_service.save_report(form,ALIAS,kind,model(day=DAY,**patch),ACTOR)
    a=chat.commits[0]['p_payload'];b=form.commits[0]['p_payload']
    a.pop('input_method',None);b.pop('input_method',None)
    assert a==b


def test_session_patch_keeps_timing_duration_and_lactate_context():
    repo=Repository();ref='act_'+'a'*32
    repo.rows[('SESSION',ref)]={'revision':1,'payload':dict(activity_ref=ref,rpe=4,
        duration_minutes=60.,timing='DELAYED',lactate_device='Lactate meter',
        lactate_samples=[{'value_mmol':2.4,'zone':'Z2'}],note='След тренировка')}
    commands.execute(repo,ALIAS,ACTOR,uuid4(),command('SESSION',{'feel':2},1,key=ref))
    payload=repo.commits[0]['p_payload']
    assert payload['rpe']==4 and payload['feel']==2 and payload['duration_minutes']==60.
    assert payload['timing']=='DELAYED' and payload['lactate_samples'][0]['value_mmol']==2.4
    assert payload['input_method']=='CHAT' and payload['method_confirmed'] is False


def test_session_rpe_without_timing_asks_instead_of_inventing_context():
    repo=Repository();ref='act_'+'a'*32
    with pytest.raises(HTTPException) as raised:
        commands.execute(repo,ALIAS,ACTOR,uuid4(),command('SESSION',{'rpe':6,'duration_minutes':60.},key=ref))
    assert raised.value.status_code==422 and not repo.commits


def test_manual_maximal_test_uses_canonical_speed_and_stable_operation_identity():
    repo=Repository();operation=uuid4()
    patch=dict(name='3 минути',day=DAY,sport='Run',duration_s=180.,distance_m=1000.,
               maximal=True,flat_terrain=True,comparable=True,conditions='Писта, сухо')
    result=commands.execute(repo,ALIAS,ACTOR,operation,command('MANUAL_SPEED_TEST',patch,key=''))
    assert result['entry_key']=='manual_'+operation.hex
    payload=repo.commits[0]['p_payload']
    assert payload['speed_kmh']==20. and payload['duration_s']==180.
    assert payload['source']=='MANUAL' and payload['measurement_input']=='DISTANCE'


def test_routine_repetition_cannot_become_maximal_test_without_attestation():
    repo=Repository()
    patch=dict(name='3 минути',day=DAY,sport='Run',duration_s=180.,distance_m=1000.,conditions='Писта, сухо')
    with pytest.raises(HTTPException) as raised:
        commands.execute(repo,ALIAS,ACTOR,uuid4(),command('MANUAL_SPEED_TEST',patch,key=''))
    assert raised.value.status_code==422 and not repo.commits
