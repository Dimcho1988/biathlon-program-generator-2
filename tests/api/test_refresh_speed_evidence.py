"""Regression: changing Vflat config must not discard newly computed TI."""
from copy import deepcopy
from datetime import date, timedelta

import pytest

from apps.api import model_service
from apps.api.refresh_speed_evidence import RefreshSpeedEvidence
from tests.api.test_speed_history_window import Repository, NOW


@pytest.mark.parametrize('manual_test', [False, True])
def test_new_config_indices_lower_boundaries_without_changing_manual_test(manual_test):
    repo = Repository()
    repo.add('one', -1, sport='NordicSki')
    if manual_test:
        repo.test(sport='NordicSki', source='MANUAL', vflat_version=None, vflat_config_version=None)
    inputs = model_service.speed_view_inputs(repo, 'ath-test')
    inputs['today'] = NOW.date()  # Both sides use the fixture's calibration day.
    old = model_service.speed_view(repo, 'ath-test', 'NordicSki', _inputs=inputs)
    saved = deepcopy(inputs)
    # The active generation carries the previous transform's comparison key.
    inputs['history'][0]['index']['comparison_key'] = 'previous-vflat-config'
    fresh = deepcopy(repo.summaries['one'])
    for band in [*fresh['trainability_index']['zones'], fresh['trainability_index']['general']]:
        band['index'] *= 1.2/1.1
    evidence = RefreshSpeedEvidence(inputs, NOW.date()-timedelta(days=2), NOW.date())
    evidence.record(inputs['calendar']['activities'][0], fresh)
    view = model_service.speed_view(repo, 'ath-test', 'NordicSki', _inputs=evidence.at(NOW.date()))
    assert all(z['boundary_source']=='PAIRED_INDEX' for z in view['speed_zones']['zones'][:4])
    assert view['speed_zones']['bounds_kmh'][1:5] == pytest.approx(
        [v*1.1/1.2 for v in old['speed_zones']['bounds_kmh'][1:5]])
    if manual_test:
        assert view['dosing_model']['weights']=={'index':.3,'tests':.7}
        point = next(p for p in view['points'] if p['duration_s']==600.)
        assert point['speed_kmh']==pytest.approx(22.)
    assert repo.saved==[] and inputs['entries']==saved['entries']


def test_window_replaces_removed_records_and_excludes_same_day_and_future_ti():
    repo = Repository()
    repo.add('removed', -1, sport='NordicSki')
    repo.add('older', -4, sport='NordicSki')
    inputs = model_service.speed_view_inputs(repo, 'ath-test')
    evidence = RefreshSpeedEvidence(inputs, NOW.date()-timedelta(days=2), NOW.date())
    metadata = {'activity_ref':'fresh','sport':'NordicSki','local_date':NOW.date().isoformat()}
    fresh = repo.summaries['removed']
    evidence.record(metadata, fresh)
    previous = evidence.at(NOW.date()-timedelta(days=1))
    assert [r['activity_ref'] for r in previous['history']]==['older']
    current = evidence.at(NOW.date())
    assert {r['activity_ref'] for r in current['history']}=={'older','fresh'}
    evidence.record(metadata)  # A failed/absent shadow cannot revive old TI.
    assert next(r for r in evidence.at(NOW.date())['history'] if r['activity_ref']=='fresh')['index'] is None
    assert [r['activity_ref'] for r in inputs['history']]==['older','removed']


def test_refresh_raw_calendar_estimate_requires_every_listed_activity_and_keeps_rest_dates():
    from apps.api.initial_threshold_view import build
    from apps.api.refresh_speed_evidence import expected_activity_dates
    start, end = date(2026,10,1), date(2026,10,8)
    expected = expected_activity_dates([{'id':'one','start_date_local':'2026-10-01T08:00:00'},
                                        {'id':'missing','start_date_local':'2026-10-04T08:00:00'}], start, end)
    inputs = {'calendar': {'activities': []}, 'history': [], 'today': end}
    evidence = RefreshSpeedEvidence(inputs, start, end, expected_activity_dates=expected)
    evidence.record({'activity_ref':'one','sport':'Run','local_date':'2026-10-01',
        'moving_time_s':3000.,'recording_time_s':3600.,'distance_m':10000.})
    before = evidence.at(end)
    assert '2026-10-04' not in {r['date'] for r in before['calendar']['snapshot_payload']['load_history']['daily']}
    assert build(before['calendar'], {'training_experience_years':4}, end, 'Run')['status'] == 'UNAVAILABLE'
    evidence.record({'activity_ref':'missing','sport':'WeightTraining','local_date':'2026-10-04','recording_time_s':1800.})
    after = evidence.at(end)
    estimate = build(after['calendar'], {'training_experience_years':4}, end, 'Run')
    assert estimate['status'] == 'AVAILABLE' and estimate['window']['days'] == 7
    assert estimate['inputs'] == {'H':1.5, 'v':12., 'S':4.}
    assert all(set(r) == {'date','zone'} for r in after['calendar']['snapshot_payload']['load_history']['daily'])
    assert expected_activity_dates([{'id':'unknown-date'}], start, end) is None


def test_refresh_preserves_complete_prior_calendar_without_importing_old_refreshed_rows():
    from tests.api.test_initial_threshold_view import calendar, TODAY
    source = calendar()
    old = deepcopy(source)
    evidence = RefreshSpeedEvidence({'calendar':source,'history':[],'today':TODAY}, TODAY, TODAY,
                                    expected_activity_dates={})
    previous = evidence.at(TODAY)
    from apps.api.initial_threshold_view import build
    assert build(previous['calendar'], {'training_experience_years':4}, TODAY, 'Run')['status'] == 'AVAILABLE'
    assert source == old


def test_refresh_without_hr_activates_raw_model_after_seven_completed_days(monkeypatch):
    from apps.api.cloud import InMemorySnapshotRepository, AthleteModelSettings
    from apps.api.real_service import refresh
    from intervals_inspector.intervals_client import IntervalsResponse
    from tests.api.test_real_cloud_pipeline import Client, ENV
    class RawWeek(Client):
        def get_activities_result(self, oldest, newest):
            return IntervalsResponse(200,[{'id':str(d),'start_date_local':f'2026-08-{d:02d}T08:00:00'} for d in range(8,16)])
        def get_activity_result(self,key,*,include_intervals=False):
            return IntervalsResponse(200,{'id':key,'start_date_local':f'2026-08-{int(key):02d}T08:00:00',
                'type':'Run','moving_time':600,'elapsed_time':600,'icu_recording_time':600,'distance':2000.,'recording_stops':[]})
        def get_streams_result(self,key):
            return IntervalsResponse(200,[{'type':'time','data':list(range(601))},
                {'type':'velocity_smooth','data':[10/3]*601},{'type':'gradient','data':[4.]*601}])
    repo = InMemorySnapshotRepository()
    settings = AthleteModelSettings((90,144,158,178,188,200),'UTC',200)
    repo.athlete_settings = lambda _:settings
    repo._request = lambda *args,**kwargs:None
    frozen = {'alias':'pilot','settings':settings,'today':date(2026,8,15),
        'calendar':{'activities':[]},'history':[],'entries':[],
        'initial_profile':{'training_experience_years':4.,'revision':1}}
    monkeypatch.setattr(model_service,'speed_view_inputs',lambda *args:frozen)
    refresh(repo,environ=ENV,client=RawWeek(),period_end=date(2026,8,15),athlete_settings=settings)
    source = repo.latest('pilot')['load_history']
    last = next(a for a in source['activities'] if a['date'] == '2026-08-15')
    assert last['load_source']['initial_heuristic'] is True
    assert last['load_source']['speed_role'] == 'RAW_MOVING_SPEED'
    assert last['load_source']['source_seconds']['SPEED'] == pytest.approx(600.)
    assert last['load_source']['unknown_seconds'] == pytest.approx(0.)
    assert source['component_load_model']['contexts_by_sport']['Run']['calibration_source'] == 'INITIAL_HEURISTIC'
    catalog = repo.activity_detail('pilot', last['activity_ref'])
    assert catalog['canonical_summary']['intensity_provenance'] == {
        'speed_role':'RAW_MOVING_SPEED','initial_heuristic':True,'calibration_source':'INITIAL_HEURISTIC'}


def test_refresh_uses_new_prior_day_ti_and_exports_all_completed_observations(monkeypatch):
    from apps.api.cloud import InMemorySnapshotRepository, AthleteModelSettings
    from apps.api.real_service import refresh
    from intervals_inspector.intervals_client import IntervalsResponse
    from tests.api.test_real_cloud_pipeline import Client, ENV
    class TwoDays(Client):
        reads = 0
        def get_activities_result(self, oldest, newest):
            return IntervalsResponse(200, [{'id':str(d),'start_date_local':f'2026-08-{d}T08:00:00'} for d in [14,15]])
        def get_activity_result(self, key, *, include_intervals=False):
            return IntervalsResponse(200, {'id':key,'start_date_local':f'2026-08-{key}T08:00:00',
                'type':'Run','moving_time':720,'elapsed_time':720,'icu_recording_time':720,'recording_stops':[]})
        def get_streams_result(self, key):
            self.reads+=1
            return IntervalsResponse(200,[{'type':'time','data':list(range(721))},
                {'type':'heartrate','data':[150.]*721},{'type':'velocity_smooth','data':[4.]*721},
                {'type':'gradient','data':[0.]*721}])
    repo = InMemorySnapshotRepository()
    settings=AthleteModelSettings((100,126,146,163,178,196),'UTC',196)
    repo.athlete_settings=lambda _:settings
    repo._request=lambda *a,**kw:None  # Exercise the persisted refresh branch.
    frozen={'alias':'pilot','settings':settings,'today':date(2026,8,15),'calendar':{'activities':[]},'history':[],'entries':[]}
    monkeypatch.setattr(model_service,'speed_view_inputs',lambda *a:frozen)
    original=model_service.speed_view
    seen=[]
    historical_windows=[]
    def view(*args,**kw):
        inputs=kw['_inputs']
        seen.append((inputs['today'],len([r for r in inputs['history'] if r['index']])))
        if inputs.get('historical_calibration'):
            historical_windows.append((inputs['today'], inputs['test_end'], inputs['evaluation_day']))
        return original(*args,**kw)
    monkeypatch.setattr(model_service,'speed_view',view)
    client=TwoDays()
    refresh(repo,environ=ENV,client=client,period_end=date(2026,8,15),athlete_settings=settings)
    assert seen==[(date(2026,8,13),0),(date(2026,8,14),1),(date(2026,8,15),2)]
    assert historical_windows == [(date(2026,8,13),date(2026,8,15),date(2026,8,15)),
                                  (date(2026,8,14),date(2026,8,15),date(2026,8,15))]
    assert client.reads==2
    current=repo.latest('pilot')['load_history']['component_load_model']['contexts_by_sport']['Run']
    assert current['zone_profile']['zones'][2]['boundary_source']=='PAIRED_INDEX'
    assert current['source_revision'] is None
    assert frozen['history']==[] and frozen['calendar']['activities']==[]
    def unexpected_recomputation(**kwargs):
        raise AssertionError('Compatible stored shadows should supply the refreshed TI too')
    monkeypatch.setattr('apps.api.activity_shadow_pipeline.compute_activity_shadow', unexpected_recomputation)
    seen.clear()
    refresh(repo,environ=ENV,client=TwoDays(),period_end=date(2026,8,15),athlete_settings=settings)
    assert seen==[(date(2026,8,13),0),(date(2026,8,14),1),(date(2026,8,15),2)]
    repeated=repo.latest('pilot')['load_history']['component_load_model']['contexts_by_sport']['Run']
    assert repeated['zone_profile']['bounds_kmh']==current['zone_profile']['bounds_kmh']
