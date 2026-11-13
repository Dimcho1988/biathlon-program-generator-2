"""Canonical proposal boundary: immutable sources, scoped inputs and revision fences."""
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from apps.api import assistant_commands, dependencies, main, training_proposals as service
from apps.api.management_schemas import ManagementProfile
from apps.api.oauth_store import PersistentStoreFailure

ALIAS = 'ath-proposal-test'
ACTOR = UUID('11111111-1111-4111-8111-111111111111')
HEADERS = {'Authorization': 'Bearer proposal-test-secret', 'X-OnFlows-Athlete-Alias': ALIAS,
           'X-OnFlows-Actor-Id': str(ACTOR)}
BASE = '/api/v2/athlete/management/training-proposals'
NOW = datetime(2026, 11, 13, 10, tzinfo=timezone.utc)
METHOD = 'END-THR-LONG-01'
OTHER = 'RUN-REC-EASY-01'


class Clock(datetime):
    @classmethod
    def now(cls, tz=None):
        return NOW.astimezone(tz) if tz else NOW.replace(tzinfo=None)


class Repository:
    def __init__(self):
        self.profile = ManagementProfile.model_validate({
            'sport': 'Run', 'actual_sport': 'Run', 'discipline': '5000 m',
            'program_start': '2026-10-01', 'program_end': '2027-02-28',
            'horizon_mode': 'MANUAL', 'available_minutes': [90] * 7,
            'age_years': 30, 'training_experience_years': 8,
            'strength_enabled': True,
        }).model_dump(mode='json')
        self.revision = 5
        self.active = {'revision': 12, 'start_date': '2026-11-13', 'end_date': '2026-11-19'}
        self.calendar = {'events': []}
        self.permissions = dict(is_owner=True, can_view_plan=True, can_edit_plan=True, can_view_recovery=False)
        self.calls, self.commits, self.receipts = [], [], {}
        self.source = source_days(7)
        self.zone = 'UTC'

    def athlete_settings(self, alias):
        assert alias == ALIAS
        return SimpleNamespace(timezone=self.zone)

    def athlete_planning_calendar(self, alias):
        assert alias == ALIAS
        return deepcopy(self.calendar)

    def _json(self, response):
        return response

    def _request(self, method, path, **kwargs):
        self.calls.append((method, path, deepcopy(kwargs)))
        body = kwargs.get('json', {})
        assert body.get('p_alias', ALIAS) == ALIAS
        if path == '/rpc/read_onflows_training_structure':
            return deepcopy(self.source)
        if path == '/rpc/onflows_assistant_access':
            return deepcopy(self.permissions)
        if path == '/rpc/onflows_assistant_operation_status':
            return deepcopy(self.receipts.get(body['p_operation_id'], {'status': 'NOT_FOUND'}))
        if path == '/rpc/save_onflows_training_proposal':
            assert body['p_kind'] == 'MANAGEMENT_PROFILE'
            assert body['p_key'] == 'management-profile-v1'
            assert body['p_day'] is None
            assert body['p_expected_revision'] == self.revision
            assert body['p_expected_plan_revision'] in (None, self.active['revision'] if self.active else 0)
            self.commits.append(deepcopy(body))
            self.revision += 1
            self.profile = deepcopy(body['p_payload'])
            receipt = {'status': 'SAVED', 'saved': True, 'revision': self.revision,
                       'operation_id': body['p_operation_id'], 'request_hash': body['p_request_hash']}
            self.receipts[body['p_operation_id']] = deepcopy(receipt)
            return receipt
        raise AssertionError((method, path))


def source_days(length, start='2026-10-26'):
    first = date.fromisoformat(start)
    days = []
    for offset in range(length):
        rest = offset % 7 == 6
        days.append({'date': (first + timedelta(days=offset)).isoformat(),
                     'revision': 8 + offset // 7, 'status': 'REST' if rest else 'TRAINING',
                     'sessions': [] if rest else [{
                         'method_id': METHOD, 'sport': 'Run', 'method_library': {'id': METHOD},
                         # A malformed/wide repository response still cannot smuggle dose into preferences.
                         'total_minutes': 900, 'target_speed_kmh': 30,
                         'dose_evidence': {'capacity': 9999}, 'blocks': [{'duration_min': 900}],
                     }]})
    return {'days': days, 'missing_dates': [], 'plan_revisions': sorted({d['revision'] for d in days})}


def block(length=7, **changes):
    return {'action': 'REPEAT_STRUCTURE', 'source_start': '2026-10-26',
            'source_end': (date(2026, 10, 26) + timedelta(days=length-1)).isoformat(),
            'target_start': '2026-11-16', **changes}


def session(**changes):
    return {'action': 'SESSION_METHOD', 'target_date': '2026-11-16',
            'session_index': 1, 'method_ids': [METHOD], **changes}


def personal(**changes):
    return {'action': 'PERSONAL_METHOD', 'method': {
        'id': 'PERSONAL-test', 'name': 'Личен праг', 'base_method_id': METHOD,
        'work_range': [1200., 1800.], **changes}}


@pytest.fixture
def repo(monkeypatch):
    repository = Repository()
    monkeypatch.setattr(service, 'datetime', Clock)
    monkeypatch.setattr(service.ManagementStore, 'profile', lambda self, alias: {
        'configured': True, 'profile': deepcopy(repository.profile), 'revision': repository.revision})
    monkeypatch.setattr(service.PlanStore, 'current_overview', lambda self, alias: deepcopy(repository.active))
    return repository


def canonical(repo, proposal):
    return service.preview(repo, ALIAS, proposal)['command']


def prepared(repo, proposal):
    command = canonical(repo, proposal)
    return service.prepare(repo, ALIAS, command['patch'], command['expected_revision'], ACTOR)


@pytest.mark.parametrize('length', [7, 14])
def test_preview_and_prepare_preserve_source_order_provenance_without_copying_dose(repo, length):
    repo.source = source_days(length)
    before = deepcopy(repo.profile)
    result = service.preview(repo, ALIAS, block(length))
    command = result['command']
    assert command['patch']['source_revisions'] == ([8] if length == 7 else [8, 9])
    assert command['patch']['expected_plan_revision'] == 12
    assert result['revision'] == result['profile_revision'] == 5
    assert result['preview']['dose_status'] == 'RECALCULATED_ON_PUBLICATION'
    value = service.prepare(repo, ALIAS, command['patch'], 5)
    directive = value['payload']['training_structure'][0]
    assert value['kind'] == 'MANAGEMENT_PROFILE' and value['training_proposal'] is True
    assert value['expected_plan_revision'] == 12
    assert directive['source']['days'] == [
        {'date': d['date'], 'plan_revision': d['revision']} for d in repo.source['days']]
    assert [d['offset'] for d in directive['days']] == list(range(length))
    assert directive['days'][6]['sessions'] == []
    assert directive['days'][0]['sessions'] == [{'slot': 1, 'method_ids': [METHOD], 'sport': 'Run'}]
    assert 'total_minutes' not in str(directive) and 'dose_evidence' not in str(directive)
    assert value['payload']['strength_enabled'] is True
    assert repo.profile == before and not repo.commits
    assert all(path == '/rpc/read_onflows_training_structure' for _, path, _ in repo.calls)
    assert repo.calls[-1][2]['json']['p_revisions'] == command['patch']['source_revisions']


def test_source_session_order_and_combined_components_are_preserved(repo):
    repo.source['days'][0]['sessions'] = [
        {'sport': 'Run', 'method_id': 'COMBINED-NOT-A-CATALOG-ID',
         'method_library': {'components': [{'id': METHOD}, {'id': OTHER}]}},
        {'sport': 'Run', 'method_id': OTHER}]
    result = prepared(repo, block())['payload']['training_structure'][0]
    assert result['days'][0]['sessions'] == [
        {'slot': 1, 'method_ids': [METHOD, OTHER], 'sport': 'Run'},
        {'slot': 2, 'method_ids': [OTHER], 'sport': 'Run'}]


@pytest.mark.parametrize('length', [0, 1, 6, 8, 13, 15, 28])
def test_source_block_must_be_exactly_seven_or_fourteen_dates(repo, length):
    with pytest.raises(HTTPException) as exc:
        service.preview(repo, ALIAS, block(length))
    assert exc.value.status_code == 422 and not repo.calls


def test_missing_source_dates_are_named_and_never_invented(repo):
    repo.source['missing_dates'] = ['2026-10-30']
    with pytest.raises(HTTPException) as exc:
        service.preview(repo, ALIAS, block())
    assert exc.value.status_code == 422 and '2026-10-30' in exc.value.detail
    assert not repo.commits


@pytest.mark.parametrize('change', [
    {'status': 'EXISTING_ACTIVITY', 'sessions': []}, {'status': 'TRAINING', 'sessions': []},
    {'sessions': None}, {'sessions': [None]},
    {'sessions': [{'sport': 'Run', 'method_id': 'NONEXISTENT'}]},
    {'sessions': [{'sport': 'Run'}]},
    {'sessions': [{'sport': 'Ride', 'method_id': METHOD}]},
])
def test_non_prescribable_source_days_reject_without_fabricating_rest_or_methods(repo, change):
    repo.source['days'][0].update(change)
    with pytest.raises(HTTPException) as exc:
        service.preview(repo, ALIAS, block())
    assert exc.value.status_code == 422 and not repo.commits


@pytest.mark.parametrize('change', [{'date': '2026-10-25'}, {'revision': True}, {'revision': 99}])
def test_wrong_date_or_unpinned_revision_is_a_storage_contract_failure(repo, change):
    repo.source['days'][0].update(change)
    with pytest.raises(PersistentStoreFailure):
        service.preview(repo, ALIAS, block(source_revisions=[8]))


def test_duplicate_source_pins_reject_before_history_read(repo):
    with pytest.raises(HTTPException) as exc:
        service.preview(repo, ALIAS, block(source_revisions=[8, 8]))
    assert exc.value.status_code == 422 and not repo.calls


@pytest.mark.parametrize('target', ['2026-11-12', '2026-09-30', '2027-03-01', '2027-02-25'])
def test_target_dates_cannot_rewrite_history_or_exceed_programme(repo, target):
    with pytest.raises(HTTPException) as exc:
        service.preview(repo, ALIAS, block(target_start=target))
    assert exc.value.status_code == 422 and not repo.commits


def test_calendar_effective_horizon_is_used_instead_of_stale_profile_end(repo):
    repo.profile['horizon_mode'] = 'AUTO_CALENDAR'
    repo.calendar['events'] = [{'event_type': 'MAIN_RACE', 'start_date': '2026-11-20', 'end_date': '2026-11-20'}]
    assert service.state(repo, ALIAS)['payload']['program_end'] == '2026-11-20'
    with pytest.raises(HTTPException) as exc:
        service.preview(repo, ALIAS, block(target_start='2026-11-16'))
    assert exc.value.status_code == 422


def test_target_today_uses_athlete_local_date(repo, monkeypatch):
    monkeypatch.setattr(Clock, 'now', classmethod(lambda cls, tz=None: datetime(2026, 11, 13, 23, 30, tzinfo=timezone.utc)))
    repo.zone = 'Europe/Sofia'
    assert service.state(repo, ALIAS)['payload']['today'] == '2026-11-14'
    with pytest.raises(HTTPException) as exc:
        service.preview(repo, ALIAS, session(target_date='2026-11-13'))
    assert exc.value.status_code == 422


@pytest.mark.parametrize('changed', ['profile', 'plan'])
def test_stale_preview_never_prepares_a_write(repo, changed):
    command = canonical(repo, block())
    if changed == 'profile':
        repo.revision += 1
    else:
        repo.active['revision'] += 1
    with pytest.raises(HTTPException) as exc:
        service.prepare(repo, ALIAS, command['patch'], command['expected_revision'])
    assert exc.value.status_code == 409 and not repo.commits


@pytest.mark.parametrize('proposal', [block(), session()])
def test_apply_requires_canonical_preview_pins(repo, proposal):
    with pytest.raises(HTTPException) as exc:
        service.prepare(repo, ALIAS, proposal, 5)
    assert exc.value.status_code in (409, 422) and not repo.commits


def test_personal_variant_normalizes_and_reports_adjustments_without_touching_global_catalog(repo):
    from biathlon.method_library import entries
    global_before = entries()
    proposal = personal(work_range=[90000., 90000.], repetitions=[99, 99], recovery_seconds=[600., 600.])
    result = service.preview(repo, ALIAS, proposal)
    method = result['command']['patch']['method']
    assert method['work_range'] == [2400, 2400] and method['repetitions'] == [1, 1]
    assert method['recovery_seconds'] == [0, 0]
    assert result['preview']['corrections']
    saved = service.prepare(repo, ALIAS, result['command']['patch'], 5)
    assert saved['payload']['method_library']['personal_methods'] == [method]
    assert repo.profile['method_library']['personal_methods'] == [] and not repo.commits
    assert entries() == global_before


def test_personal_upsert_preserves_other_variants_and_unrelated_settings(repo):
    repo.profile['method_library']['personal_methods'] = [personal(id='PERSONAL-other')['method'], personal()['method']]
    result = prepared(repo, personal(name='Променено име'))['payload']
    values = result['method_library']['personal_methods']
    assert len(values) == 2 and values[0] == repo.profile['method_library']['personal_methods'][0] | {'work_unit': 'seconds'}
    assert values[1]['name'] == 'Променено име'
    assert result['available_minutes'] == repo.profile['available_minutes']


def test_removing_personal_method_cleans_its_override_and_preserves_other_methods(repo):
    repo.profile['method_library']['personal_methods'] = [personal()['method'], personal(id='PERSONAL-other')['method']]
    repo.profile['method_library']['overrides']['PERSONAL-test'] = {'enabled': False}
    result = prepared(repo, {'action': 'REMOVE_PERSONAL_METHOD', 'method_id': 'PERSONAL-test'})['payload']
    assert [m['id'] for m in result['method_library']['personal_methods']] == ['PERSONAL-other']
    assert 'PERSONAL-test' not in result['method_library']['overrides']


def test_removing_referenced_personal_method_is_explicitly_rejected(repo):
    repo.profile['method_library']['personal_methods'] = [personal()['method']]
    repo.profile = prepared(repo, session(method_ids=['PERSONAL-test']))['payload']
    with pytest.raises(HTTPException) as exc:
        service.preview(repo, ALIAS, {'action': 'REMOVE_PERSONAL_METHOD', 'method_id': 'PERSONAL-test'})
    assert exc.value.status_code == 409


@pytest.mark.parametrize('proposal', [
    personal(base_method_id='UNPROVED-METHOD'), personal(work_unit='meters'),
    personal(total_capacity_ratio=10.), personal(speed_multiplier=[1., 2.]),
    personal(corrections=[]), block(days=[]), block(source={'plan_revisions': [1]}),
    block(dose=100), session(speed_kmh=20), session(method_ids=[METHOD, METHOD]),
    session(session_index=True), session(method_ids=['UNKNOWN']), session(sport='NordicSki'),
])
def test_unsupported_or_unvalidated_inputs_reject_without_writes(repo, proposal):
    with pytest.raises(HTTPException) as exc:
        service.preview(repo, ALIAS, proposal)
    assert exc.value.status_code == 422 and not repo.commits


def test_session_defaults_to_current_sport_and_uses_one_based_slot(repo):
    result = prepared(repo, session(session_index=2))
    directive = result['payload']['training_structure'][0]
    assert directive['mode'] == 'SESSION' and directive['source'] is None
    assert directive['days'] == [{'offset': 0, 'sessions': [{'slot': 2, 'method_ids': [METHOD], 'sport': 'Run'}]}]


def test_different_session_edits_on_same_day_preserve_existing_slot(repo):
    repo.profile = prepared(repo, session(session_index=1))['payload']
    result = prepared(repo, session(session_index=2, method_ids=[OTHER]))['payload']
    assert result['training_structure'][0]['days'][0]['sessions'] == [
        {'slot': 1, 'method_ids': [METHOD], 'sport': 'Run'},
        {'slot': 2, 'method_ids': [OTHER], 'sport': 'Run'}]


def test_session_edit_inside_fixed_block_retains_other_days_and_provenance(repo):
    repo.source = source_days(7, start='2026-11-16')
    repo.profile = prepared(repo, block(action='FIX_STRUCTURE', source_start='2026-11-16', source_end='2026-11-22'))['payload']
    before = deepcopy(repo.profile['training_structure'][0])
    result = prepared(repo, session(target_date='2026-11-17', method_ids=[OTHER]))['payload']['training_structure']
    assert len(result) == 1 and result[0]['id'] == before['id']
    assert result[0]['source'] == before['source']
    assert result[0]['days'][0] == before['days'][0]
    assert result[0]['days'][1]['sessions'][0]['method_ids'] == [OTHER]
    assert result[0]['days'][2:] == before['days'][2:]


def test_session_edit_cannot_invent_new_slot_in_fixed_block(repo):
    repo.source = source_days(7, start='2026-11-16')
    repo.profile = prepared(repo, block(action='FIX_STRUCTURE', source_start='2026-11-16', source_end='2026-11-22'))['payload']
    with pytest.raises(HTTPException) as exc:
        service.preview(repo, ALIAS, session(session_index=2))
    assert exc.value.status_code in (409, 422)


def test_overlapping_blocks_are_rejected_and_removal_restores_algorithm_choice(repo):
    repo.profile = prepared(repo, block())['payload']
    with pytest.raises(HTTPException) as exc:
        service.preview(repo, ALIAS, block(target_start='2026-11-18'))
    assert exc.value.status_code == 422
    identifier = repo.profile['training_structure'][0]['id']
    result = prepared(repo, {'action': 'REMOVE_STRUCTURE', 'directive_id': identifier})
    assert result['payload']['training_structure'] == []
    assert len(repo.profile['training_structure']) == 1


@pytest.mark.parametrize('proposal', [
    {'action': 'REMOVE_STRUCTURE', 'directive_id': 'missing'},
    {'action': 'REMOVE_PERSONAL_METHOD', 'method_id': 'PERSONAL-missing'},
])
def test_missing_removal_is_explicit(repo, proposal):
    with pytest.raises(HTTPException) as exc:
        service.preview(repo, ALIAS, proposal)
    assert exc.value.status_code == 404


@pytest.fixture
def api(repo, monkeypatch):
    monkeypatch.setenv('ONFLOWS_SERVICE_TOKEN', 'proposal-test-secret')
    monkeypatch.setattr(dependencies, 'repository', lambda: repo)
    return TestClient(main.app), repo


@pytest.mark.parametrize('headers', [
    {}, {'Authorization': 'Bearer wrong'},
    {key: value for key, value in HEADERS.items() if key != 'X-OnFlows-Athlete-Alias'},
    {key: value for key, value in HEADERS.items() if key != 'X-OnFlows-Actor-Id'},
])
def test_proposal_routes_require_service_alias_and_actor_before_private_reads(api, headers):
    client, repo = api
    for path, method in [(BASE, 'get'), (BASE + '/preview', 'post'), (BASE + '/apply', 'post')]:
        kwargs = {'headers': headers}
        if method == 'post':
            kwargs['json'] = {'proposal': personal()}
            if path.endswith('/apply'):
                kwargs['json'].update(expected_revision=5, operation_id=str(uuid4()))
        assert getattr(client, method)(path, **kwargs).status_code == 401
    assert not repo.calls


def test_read_only_coach_can_discuss_and_preview_but_cannot_apply(api):
    client, repo = api
    repo.permissions.update(is_owner=False, can_edit_plan=False)
    state = client.get(BASE, headers=HEADERS)
    assert state.status_code == 200
    assert state.json()['payload']['current_plan']['revision'] == 12
    preview = client.post(BASE + '/preview', headers=HEADERS, json={'proposal': block()})
    assert preview.status_code == 200
    command = preview.json()['command']
    applied = client.post(BASE + '/apply', headers=HEADERS, json={
        'proposal': command['patch'], 'expected_revision': command['expected_revision'], 'operation_id': str(uuid4())})
    assert applied.status_code == 403 and not repo.commits


def test_recovery_only_access_cannot_read_training_proposals(api):
    client, repo = api
    repo.permissions.update(is_owner=False, can_view_plan=False, can_edit_plan=False, can_view_recovery=True)
    assert client.get(BASE, headers=HEADERS).status_code == 403
    assert client.post(BASE + '/preview', headers=HEADERS, json={'proposal': block()}).status_code == 403
    assert not any(path == '/rpc/read_onflows_training_structure' for _, path, _ in repo.calls)


def test_form_apply_uses_atomic_profile_receipt_and_retry_does_not_repeat_change(api):
    client, repo = api
    preview = client.post(BASE + '/preview', headers=HEADERS, json={'proposal': block()})
    assert preview.status_code == 200, preview.text
    command = preview.json()['command']
    operation = str(uuid4())
    body = {'proposal': command['patch'], 'expected_revision': 5, 'operation_id': operation}
    first = client.post(BASE + '/apply', headers=HEADERS, json=body)
    assert first.status_code == 200, first.text
    assert first.json()['status'] == 'SAVED' and first.json()['refresh_queued'] is True
    assert first.json()['dose_status'] == 'RECALCULATED_ON_PUBLICATION'
    assert len(repo.commits) == 1 and repo.commits[0]['p_origin'] == 'FORM'
    assert repo.commits[0]['p_actor'] == str(ACTOR)
    repo.active['revision'] = 99  # An idempotent replay does not reinterpret the old source.
    second = client.post(BASE + '/apply', headers=HEADERS, json=body)
    assert second.status_code == 200 and second.json()['revision'] == first.json()['revision']
    assert second.json()['operation_id'] == operation and len(repo.commits) == 1
    body['proposal']['name'] = 'Different intent'
    assert client.post(BASE + '/apply', headers=HEADERS, json=body).status_code == 409
    assert len(repo.commits) == 1


def test_chat_and_form_share_canonical_validated_payload(api):
    client, repo = api
    command = canonical(repo, personal())
    first = assistant_commands.execute(repo, ALIAS, ACTOR, uuid4(), command)
    assert first['saved'] and repo.commits[0]['p_origin'] == 'CHAT'
    chat_payload = deepcopy(repo.commits[0]['p_payload'])
    repo.profile['method_library']['personal_methods'] = []
    repo.revision = 5
    response = client.post(BASE + '/apply', headers=HEADERS, json={
        'proposal': command['patch'], 'expected_revision': 5, 'operation_id': str(uuid4())})
    assert response.status_code == 200, response.text
    assert repo.commits[-1]['p_origin'] == 'FORM'
    assert repo.commits[-1]['p_payload'] == chat_payload


def test_editing_coach_with_no_recovery_right_can_apply_training_proposal(api):
    client, repo = api
    repo.permissions.update(is_owner=False, can_view_plan=True, can_edit_plan=True, can_view_recovery=False)
    response = client.post(BASE + '/apply', headers=HEADERS, json={
        'proposal': canonical(repo, session())['patch'], 'expected_revision': 5, 'operation_id': str(uuid4())})
    assert response.status_code == 200, response.text
    assert repo.commits


def test_access_revocation_blocks_retry_even_when_receipt_exists(api):
    client, repo = api
    command = canonical(repo, personal())
    body = {'proposal': command['patch'], 'expected_revision': 5, 'operation_id': str(uuid4())}
    assert client.post(BASE + '/apply', headers=HEADERS, json=body).status_code == 200
    repo.permissions.update(can_edit_plan=False)
    assert client.post(BASE + '/apply', headers=HEADERS, json=body).status_code == 403
    assert len(repo.commits) == 1


def test_wrong_command_key_cannot_retarget_profile(repo):
    command = canonical(repo, personal())
    command['entry_key'] = 'another-profile'
    with pytest.raises(HTTPException) as exc:
        assistant_commands.execute(repo, ALIAS, ACTOR, uuid4(), command)
    assert exc.value.status_code == 422 and not repo.commits


def test_raw_management_patch_cannot_forge_frozen_structure_provenance(repo):
    command = {'kind': 'MANAGEMENT_PROFILE', 'entry_key': 'management-profile-v1', 'expected_revision': 5,
               'patch': {'training_structure': []}}
    with pytest.raises(HTTPException) as exc:
        assistant_commands.execute(repo, ALIAS, ACTOR, uuid4(), command)
    assert exc.value.status_code == 422 and not repo.commits


def test_fix_started_week_retains_dates_and_changes_only_future_planning_intent(repo):
    repo.source = source_days(7, start='2026-11-09')
    value = prepared(repo, block(action='FIX_STRUCTURE', source_start='2026-11-09',
                                 source_end='2026-11-15', target_start='2026-11-09'))
    directive = value['payload']['training_structure'][0]
    assert directive['mode'] == 'FIX' and directive['start_date'] == '2026-11-09'
    assert directive['source']['start_date'] == directive['start_date']
    assert len(directive['days']) == 7
    assert not repo.commits  # This boundary only prepares a profile; no history writes.


def test_fix_wholly_past_week_rejects(repo):
    repo.source = source_days(7, start='2026-11-02')
    with pytest.raises(HTTPException) as exc:
        service.preview(repo, ALIAS, block(action='FIX_STRUCTURE', source_start='2026-11-02',
                                          source_end='2026-11-08', target_start='2026-11-02'))
    assert exc.value.status_code == 422 and not repo.commits


def test_fix_cannot_move_source_dates_into_different_week(repo):
    with pytest.raises(HTTPException) as exc:
        service.preview(repo, ALIAS, block(action='FIX_STRUCTURE'))
    assert exc.value.status_code == 422 and not repo.commits


@pytest.mark.parametrize('slots', [[2], [True], [1, 3], [2, 1], [1, 1]])
def test_partial_or_unordered_source_slots_are_not_silently_renumbered(repo, slots):
    repo.source['days'][0]['sessions'] = [
        {'slot': slot, 'sport': 'Run', 'method_id': METHOD} for slot in slots]
    with pytest.raises(HTTPException) as exc:
        service.preview(repo, ALIAS, block())
    assert exc.value.status_code == 422 and not repo.commits


@pytest.mark.parametrize('proposal', [block(source_revisions=[8]), session()])
def test_explicit_null_plan_revision_cannot_bypass_apply_fence(repo, proposal):
    with pytest.raises(HTTPException) as exc:
        service.prepare(repo, ALIAS, {**proposal, 'expected_plan_revision': None}, 5)
    assert exc.value.status_code == 422 and not repo.commits


def test_profile_size_guard_uses_utf8_and_fails_before_remote_write():
    from apps.api.management_store import ManagementStore, validate_profile_size
    validate_profile_size({'notes': 'я' * 1000})
    repository = SimpleNamespace(_request=lambda *a, **k: pytest.fail('Oversize profile reached storage'))
    with pytest.raises(HTTPException) as error:
        ManagementStore(repository).save_profile(ALIAS, {'notes': 'я' * 33000}, 1, ACTOR)
    assert error.value.status_code == 422


def test_atomic_profile_size_guard_runs_before_receipt_rpc():
    repository = SimpleNamespace(_request=lambda *a, **k: pytest.fail('Oversize profile reached receipt RPC'))
    with pytest.raises(HTTPException) as error:
        assistant_commands.apply_prepared(repository, ALIAS, ACTOR, uuid4(),
            {'kind': 'MANAGEMENT_PROFILE', 'payload': {'notes': 'я' * 33000}}, 'a' * 64)
    assert error.value.status_code == 422
