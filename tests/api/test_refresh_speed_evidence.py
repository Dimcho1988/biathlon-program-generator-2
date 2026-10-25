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
    def view(*args,**kw):
        inputs=kw['_inputs']
        seen.append((inputs['today'],len([r for r in inputs['history'] if r['index']])))
        return original(*args,**kw)
    monkeypatch.setattr(model_service,'speed_view',view)
    client=TwoDays()
    refresh(repo,environ=ENV,client=client,period_end=date(2026,8,15),athlete_settings=settings)
    assert seen==[(date(2026,8,13),0),(date(2026,8,14),1),(date(2026,8,15),2)]
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
