"""Date-effective HR changes across raw analysis and downstream calibration."""
from dataclasses import replace
from datetime import date, timedelta
from types import SimpleNamespace

import pytest

from apps.api.cloud import AthleteModelSettings, InMemorySnapshotRepository
from apps.api.hr_settings_history import at, effective_date, history_days
from apps.api.schemas import AthleteSettingsInput
from apps.api.model_service import historical_speed_inputs
from apps.api.real_service import refresh
from biathlon.hr_zones import bounds_from_hrmax
from tests.api.test_real_cloud_pipeline import Client, MixedStreamClient, ENV

PERCENTAGES = (45,72,79,89,94,100)


def calibration(maximum, start):
    return {'from':start, 'hrmax_bpm':maximum,
            'zone_bounds_bpm':list(bounds_from_hrmax(maximum,PERCENTAGES)),
            'hr_zone_source':'AUTOMATIC_HRMAX', 'hr_zone_percentages':list(PERCENTAGES)}


def settings():
    return AthleteModelSettings(bounds_from_hrmax(200,PERCENTAGES),'Europe/Sofia',200,
        'AUTOMATIC_HRMAX',PERCENTAGES,
        hr_history=(calibration(190,'0001-01-01'),calibration(200,'2026-08-15')),
        hr_settings_revision='test-revision',hr_recompute_from='2026-08-15').validate()


def test_boundary_has_old_before_new_on_date_and_keeps_original():
    s=settings()
    assert at(s,date(2026,8,14)).hrmax_bpm==190
    assert at(s,date(2026,8,15)).hrmax_bpm==200
    assert at(s,date(2026,8,16)).zone_bounds_bpm==s.zone_bounds_bpm
    assert s.hrmax_bpm==200 and len(s.hr_history)==2


def test_defaults_and_date_validation():
    today=date(2026,11,14)
    assert effective_date(AthleteSettingsInput(timezone='UTC'),settings(),today=today)=='2026-10-06'
    assert effective_date(AthleteSettingsInput(timezone='UTC',hrmax_apply_mode='LAST_90_DAYS'),settings(),today=today)=='2026-08-17'
    assert effective_date(AthleteSettingsInput(timezone='UTC',hrmax_apply_mode='FROM_TODAY'),settings(),today=today)==today.isoformat()
    for end, first in ((today,date(2025,11,14)),(date(2024,2,29),date(2023,2,28)),(date(2025,2,28),date(2024,2,28))):
        body=AthleteSettingsInput(timezone='UTC',hrmax_apply_mode='FROM_DATE',hrmax_effective_from=first)
        assert effective_date(body,settings(),today=end)==first.isoformat()
        with pytest.raises(ValueError):
            effective_date(body.model_copy(update={"hrmax_effective_from": first-timedelta(days=1)}),settings(),today=end)
    for day in (today+timedelta(days=1),date(2025,11,13),None):
        with pytest.raises(ValueError):
            effective_date(AthleteSettingsInput(timezone='UTC',hrmax_apply_mode='FROM_DATE',hrmax_effective_from=day),settings(),today=today)
    assert effective_date(AthleteSettingsInput(timezone='UTC',hrmax_apply_mode='FROM_DATE',hrmax_effective_from=today),settings(),today=today)==today.isoformat()


def test_rebuild_has_40_day_context_and_bounded_older_range():
    s=replace(settings(),hr_recompute_from='2026-08-17')
    assert history_days(s,date(2026,11,14),90)==130
    assert history_days(replace(s,hr_recompute_from='2025-11-14'),date(2026,11,14),90)==406
    assert history_days(replace(s,hr_recompute_from='2023-02-28'),date(2024,2,29),90)==407


def test_historical_model_uses_activity_date_not_previous_evidence_date():
    inputs={'settings':settings(),'today':date(2026,8,20)}
    current=historical_speed_inputs(inputs,date(2026,8,15))
    assert current['settings'].hrmax_bpm==200
    assert current['today']==date(2026,8,14)
    assert historical_speed_inputs(inputs,date(2026,8,14))['settings'].hrmax_bpm==190


def test_raw_analysis_and_zone_ledger_use_same_date_specific_configuration(monkeypatch):
    from apps.api import activity_shadow_pipeline
    calls=[]
    original=activity_shadow_pipeline.compute_activity_shadow
    def capture(**kwargs):
        calls.append((kwargs['detail']['start_date_local'][:10],kwargs['explicit_hrmax_bpm']))
        return original(**kwargs)
    monkeypatch.setattr(activity_shadow_pipeline,'compute_activity_shadow',capture)
    class TwoDays(MixedStreamClient):
        def get_streams_result(self,activity_id):
            return Client.get_streams_result(self,activity_id)
    repo=InMemorySnapshotRepository()
    refresh(repo,environ=ENV,client=TwoDays(),period_end=date(2026,8,15),athlete_settings=settings())
    source=repo.latest('pilot')['load_history']
    assert calls==[('2026-08-14',190),('2026-08-15',200)]
    assert source['hr_settings_revision']=='test-revision'
    # The same 145 bpm is Z2 with HRmax 190 and Z2 with HRmax 200,
    # but relative intrazone contribution changes. Raw measured minutes stay equal.
    old,new=sorted(source['activities'],key=lambda a:a['date'])
    assert sum(z['raw_time_min'] for z in old['zones'])==sum(z['raw_time_min'] for z in new['zones'])
    assert old['zones']!=new['zones']
    assert source['hr_calibration_history'][0]['hrmax_bpm']==190
    # Ordinary subsequent syncs preserve the timeline without expanding the import again.
    calls.clear()
    refresh(repo,environ=ENV,client=TwoDays(),period_end=date(2026,8,15),athlete_settings=settings())
    assert repo.latest('pilot')['load_history']['period_start']=='2026-07-06'


def test_obsolete_analysis_cannot_be_relabelled_as_current():
    from apps.api.actual_load_projection import reconstruct_history
    from apps.api.component_load_projection import ComponentLoadRefreshRequired
    repo=SimpleNamespace(athlete_settings=lambda _:settings(),activity_speed_history_samples=lambda *a: {})
    with pytest.raises(ComponentLoadRefreshRequired,match='full recording refresh'):
        reconstruct_history(repo,'pilot',{'hr_settings_revision':'old-revision'})


def test_manual_calibration_is_not_undone_by_observed_peak(monkeypatch):
    from apps.api import component_load_context
    from intervals_inspector.intervals_client import IntervalsResponse
    class Peak(Client):
        def get_streams_result(self, key):
            return IntervalsResponse(200,[{'type':'time','data':list(range(61))},
                                         {'type':'heartrate','data':[210.]*61}])
    repo=InMemorySnapshotRepository()
    repo.athlete_settings=lambda _:settings()
    def unexpected(*args,**kwargs):
        raise AssertionError('Manual calibration must not be overwritten by automatic peak detection')
    repo.increase_athlete_hrmax=unexpected
    monkeypatch.setattr(component_load_context,'read_contexts',lambda repository,alias,sports:{s:component_load_context.context_from_speed_view({'sport':s}) for s in sports})
    refresh(repo,environ=ENV,client=Peak(),period_end=date(2026,8,15),athlete_settings=settings())
    assert repo.latest('pilot')['load_history']['hrmax_bpm']==200


def test_reconstructed_load_uses_each_activity_calibration(monkeypatch):
    from apps.api import actual_load_projection, model_service
    from biathlon import speed_equivalence
    from tests.api.test_unified_load_integration import streams, profile
    s=settings()
    days=('2026-08-14','2026-08-15')
    rows={day:{'activity_ref':day,'shadow_payload':streams(150.,16.,seconds=60)} for day in days}
    repo=SimpleNamespace(athlete_settings=lambda _:s,activity_speed_history_samples=lambda a,k:rows)
    inputs={'settings':s,'today':date(2026,8,20),'entries':[],
        'calendar':{'activities':[{'activity_ref':day,'sport':'Run','local_date':day,
            'latest_shadow_run_key':day,'elapsed_time_s':60} for day in days]}}
    maxima,bounds=[],[]
    def view(repository,alias,sport,*,_inputs,_capacity_only):
        maxima.append(_inputs['settings'].hrmax_bpm)
        return {'sport':sport,'speed_zones':profile(sport),'status':'UNAVAILABLE'}
    original=actual_load_projection.unified_load.integrate
    def integrate(payload,**kwargs):
        bounds.append(tuple(kwargs['bounds']))
        return original(payload,**kwargs)
    monkeypatch.setattr(model_service,'speed_view',view)
    monkeypatch.setattr(actual_load_projection.unified_load,'integrate',integrate)
    source={'period_start':days[0],'period_end':days[1],'hr_settings_revision':'test-revision',
        'activities':[{'activity_ref':day,'sport':'Run','date':day,'duration_min':1.,
            'quality_status':'limited','zones':[{'zone':z,'effective_load':0.} for z in speed_equivalence.ZONES]} for day in days]}
    result=actual_load_projection.reconstruct_history(repo,'history-calibration-test',source,speed_inputs=inputs)
    assert maxima==[190,200]
    assert bounds==[tuple(bounds_from_hrmax(m,PERCENTAGES)) for m in maxima]
    assert result['load_selection']['reconstructed_activities']==2

def test_year_correction_imports_the_boundary_and_full_reference_period(monkeypatch):
    from apps.api import activity_shadow_pipeline
    from intervals_inspector.intervals_client import IntervalsResponse

    first, end = date(2023,2,28), date(2024,2,29)
    observed = {}
    class BoundaryClient(Client):
        def get_activities_result(self, oldest, newest):
            assert (oldest, newest) == ((first-timedelta(days=40)).isoformat(), end.isoformat())
            return IntervalsResponse(200, [{"id": day.isoformat(), "start_date_local": day.isoformat()+"T08:00:00"}
                for day in (first-timedelta(days=1), first, end)])
        def get_activity_result(self, key, **kwargs):
            original = super().get_activity_result(key, **kwargs)
            return IntervalsResponse(200, {**original.payload, "start_date_local": key+"T08:00:00"})
    original = activity_shadow_pipeline.compute_activity_shadow
    def capture(**kwargs):
        observed[kwargs["detail"]["id"]] = kwargs["explicit_hrmax_bpm"]
        return original(**kwargs)
    monkeypatch.setattr(activity_shadow_pipeline, "compute_activity_shadow", capture)
    current = replace(settings(), hr_recompute_from=first.isoformat(),
        hr_history=(calibration(190,"0001-01-01"),calibration(200,first.isoformat())))
    repo = InMemorySnapshotRepository()
    refresh(repo, environ=ENV, client=BoundaryClient(), period_end=end, athlete_settings=current)
    assert observed == {"2023-02-27":190, "2023-02-28":200, "2024-02-29":200}
    source = repo.latest("pilot")["load_history"]
    assert len({row["date"] for row in source["daily"]}) == 407
