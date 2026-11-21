from copy import deepcopy
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException

from apps.api import assistant_app_actions as actions, assistant_commands as commands
from apps.api.oauth_store import _PlanningSettingsBundle
from tests.api.test_assistant_app_actions import Repository as AppRepository
from tests.api.test_assistant_commands import ALIAS, ACTOR, command
from tests.api.test_cloud_boundaries import planning_profile


class Repository(AppRepository):
    def __init__(self):
        super().__init__()
        self.settings['planning_profile'] = planning_profile().to_payload()
        self.settings['mesocycle_accent_preferences'] = dict(schema_version='mesocycle-accent-preferences-v1',
            accent_mode='HYBRID',accent_limit=3,manual_components=['Z2'])

    def athlete_planning_profile(self, alias):
        return _PlanningSettingsBundle([self.settings]).athlete_planning_profile()

    def active_analysis(self, alias):
        return None

    def _request(self, method, path, **kwargs):
        result = super()._request(method, path, **kwargs)
        if path == '/rpc/save_onflows_assistant_app_operation' and kwargs['json']['p_kind'] == 'SYNC_DATA':
            result.update(revision=0,queued=True,refresh_state='QUEUED',
                sync_job={'job_id':str(uuid4()),'status':'QUEUED','deduplicated':False})
            self.receipts[kwargs['json']['p_operation_id']] = deepcopy(result)
        return result


def test_legacy_profile_partial_edit_preserves_full_form_and_uses_domain_validation():
    repo = Repository(); repo.permissions.update(is_owner=False,can_view_recovery=False)
    result = commands.execute(repo,ALIAS,ACTOR,uuid4(),command('PLANNING_PROFILE',{'annual_target_hours':650},2,'planning-profile-v1'))
    assert result['saved']
    stored = repo.commits[0]['p_payload']
    assert stored == {**planning_profile().to_payload(),'annual_target_hours':650}
    with pytest.raises(HTTPException) as exc:
        commands.execute(repo,ALIAS,ACTOR,uuid4(),command('PLANNING_PROFILE',{'sessions_per_week':15},2,'planning-profile-v1'))
    assert exc.value.status_code == 422 and len(repo.commits) == 1


def test_legacy_accent_edit_uses_canonical_order_and_rejects_duplicate_components():
    repo = Repository(); repo.permissions.update(is_owner=False,can_view_recovery=False)
    commands.execute(repo,ALIAS,ACTOR,uuid4(),command('MESOCYCLE_PREFERENCES',{'manual_components':['STR','Z3']},2,'mesocycle-accent-preferences-v1'))
    assert repo.commits[0]['p_payload']['manual_components'] == ['Z3','STR']
    with pytest.raises(HTTPException) as exc:
        commands.execute(repo,ALIAS,ACTOR,uuid4(),command('MESOCYCLE_PREFERENCES',{'manual_components':['Z2','Z2']},2,'mesocycle-accent-preferences-v1'))
    assert exc.value.status_code == 422 and len(repo.commits) == 1


@pytest.mark.parametrize('kind,key,patch',[
    ('PLANNING_PROFILE','planning-profile-v1',{'annual_target_hours':650}),
    ('MESOCYCLE_PREFERENCES','mesocycle-accent-preferences-v1',{'accent_limit':4}),
])
def test_legacy_read_is_coherent_and_stale_revision_never_writes(kind,key,patch):
    repo = Repository()
    value = actions.read_input(repo,ALIAS,kind)
    assert value['revision'] == 2 and value['entry_key'] == key
    with pytest.raises(HTTPException) as exc:
        commands.execute(repo,ALIAS,ACTOR,uuid4(),command(kind,patch,1,key))
    assert exc.value.status_code == 409 and not repo.commits


def test_legacy_accent_requires_a_planning_profile():
    repo = Repository(); repo.settings['planning_profile'] = None
    with pytest.raises(HTTPException) as exc:
        commands.execute(repo,ALIAS,ACTOR,uuid4(),command('MESOCYCLE_PREFERENCES',{'accent_limit':4},2,'mesocycle-accent-preferences-v1'))
    assert exc.value.status_code == 409 and not repo.commits


@pytest.mark.parametrize('recovery,plan,edit,allowed',[
    (True,False,False,True),(False,True,True,True),(False,True,False,False),
])
def test_sync_matches_visible_app_permissions_without_owner_requirement(recovery,plan,edit,allowed):
    repo = Repository(); repo.permissions.update(is_owner=False,can_view_recovery=recovery,can_view_plan=plan,can_edit_plan=edit)
    change = command('SYNC_DATA',{'scope':'FULL'},0,'intervals-sync')
    if not allowed:
        with pytest.raises(HTTPException) as exc:
            commands.execute(repo,ALIAS,ACTOR,uuid4(),change)
        assert exc.value.status_code == 403 and not repo.commits
    else:
        result = commands.execute(repo,ALIAS,ACTOR,uuid4(),change)
        assert result['queued'] and result['refresh_state'] == 'QUEUED'
        assert repo.commits[0]['p_payload']['job_kind'] == 'FULL_SYNC'


def test_sync_uses_canonical_fallback_and_replay_never_queues_second_job():
    repo = Repository(); operation = uuid4()
    change = command('SYNC_DATA',{'scope':'RECOVERY'},0,'intervals-sync')
    result = commands.execute(repo,ALIAS,ACTOR,operation,change)
    assert repo.commits[0]['p_payload']['request_payload']['scope'] == 'FULL'
    assert commands.execute(repo,ALIAS,ACTOR,operation,change) == result
    assert len(repo.commits) == 1


def test_sync_rejects_invalid_scope_and_disconnected_athlete_before_commit():
    repo = Repository()
    with pytest.raises(HTTPException) as exc:
        commands.execute(repo,ALIAS,ACTOR,uuid4(),command('SYNC_DATA',{'scope':'ALL_USERS'},0,'intervals-sync'))
    assert exc.value.status_code == 422
    repo.connection = lambda alias: SimpleNamespace(status='REVOKED')
    with pytest.raises(HTTPException) as exc:
        commands.execute(repo,ALIAS,ACTOR,uuid4(),command('SYNC_DATA',{'scope':'FULL'},0,'intervals-sync'))
    assert exc.value.status_code == 409 and not repo.commits
