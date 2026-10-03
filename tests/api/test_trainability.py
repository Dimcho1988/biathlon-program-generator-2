"""Synthetic time-aligned inputs: no private athlete samples in the repository."""
from copy import deepcopy
import math
import pytest
from apps.api.trainability import MIN_SECONDS_BY_BAND, compute_trainability, signal_quality

BOUNDS = [80,137,148,160,170,180]

def samples(seconds=600, hr=150, speed=20, grade=0):
    hrs=[{'elapsed_s':t,'hr_raw_bpm':hr,'hrmod_final_bpm':100} for t in range(math.ceil(seconds)+22)]
    speeds=[{'elapsed_s':min(t+1,seconds),'dt_s':min(1,seconds-t),
             'vflat_b65_kmh':speed,'grade_raw_pct':grade} for t in range(math.ceil(seconds))]
    return hrs,speeds

def calculate(hrs,speeds,**kwargs):
    return compute_trainability(hrs,speeds,zone_bounds_bpm=BOUNDS,hrmax_bpm=kwargs.get('hrmax',180),
        activity_duration_s=kwargs.get('duration',3600),comparison_key='synthetic',source_versions={})

def named(result,name):
    return result['general'] if name=='GENERAL' else result['zones'][int(name[1])-1]

def test_raw_hr_is_shifted_forward_twenty_seconds_and_never_rank_sorted():
    hrs,speeds=samples(1200)
    for row in hrs:
        # Speed 30 belongs to low HR, speed 10 to high HR: sorting would reverse these.
        row['hr_raw_bpm']=120 if row['elapsed_s']<620 else 155
    for row in speeds:row['vflat_b65_kmh']=30 if row['elapsed_s']<=600 else 10
    result=calculate(hrs,speeds)
    assert result['hr_source']=='raw' and result['lag_seconds']==20
    assert result['zones'][0]['mean_vflat_kmh']==30
    assert result['zones'][2]['mean_vflat_kmh']==10
    assert result['zones'][2]['index']==pytest.approx(100*155/180/10)
    assert result['signal_quality']['status']=='PASSED_SCREEN'

@pytest.mark.parametrize('name,hr',[('Z1',120),('Z2',140),('Z3',150),('Z4',165),('Z5',175),('GENERAL',150)])
@pytest.mark.parametrize('offset,valid',[(-.001,False),(0,True),(.001,True)])
def test_paired_cumulative_time_minima_are_not_rounded(name,hr,offset,valid):
    result=calculate(*samples(MIN_SECONDS_BY_BAND[name]+offset,hr))
    band=named(result,name)
    assert band['hr_seconds']==pytest.approx(MIN_SECONDS_BY_BAND[name]+offset)
    assert band['hr_seconds']==band['speed_seconds']
    assert band['valid'] is valid
    if not valid:assert band['invalid_reason']=='PAIRED_TIME_BELOW_MINIMUM'

@pytest.mark.parametrize('duration,valid',[(419.999,False),(420,True)])
def test_activity_minimum(duration,valid):
    assert calculate(*samples(),duration=duration)['general']['valid'] is valid

def test_downhill_excludes_both_members_of_pair_and_keeps_exact_minus_three():
    hrs,speeds=samples(840,grade=-3)
    for row in speeds[:420]:row.update(grade_raw_pct=-3.001,vflat_b65_kmh=100)
    result=calculate(hrs,speeds)
    assert result['hr_seconds']==result['eligible_speed_seconds']==420
    assert result['downhill_excluded_seconds']==420
    assert result['general']['mean_vflat_kmh']==20

def test_lag_uses_seconds_with_irregular_sampling_and_has_no_end_extrapolation():
    hrs=[{'elapsed_s':t,'hr_raw_bpm':100+t/10} for t in range(0,701,4)]
    speeds=[{'elapsed_s':t,'dt_s':2,'vflat_b65_kmh':20,'grade_raw_pct':0} for t in range(2,701,2)]
    r=calculate(hrs,speeds)
    assert r['hr_seconds']==680  # midpoint 679 + 20 is in range; 681 + 20 is not.
    weighted=sum(z['mean_hr_bpm']*z['hr_seconds'] for z in r['zones'] if z['hr_seconds'])/680
    assert weighted==pytest.approx(136.)  # HR at midpoint average 340 + lag 20.

@pytest.mark.parametrize('gap_kind',['missing','long_gap'])
def test_missing_hr_or_long_gap_does_not_bridge_the_lag_window(gap_kind):
    hrs,speeds=samples()
    if gap_kind=='missing':hrs[300]['hr_raw_bpm']=None
    else:hrs=[r for r in hrs if not 290<=r['elapsed_s']<=310]
    r=calculate(hrs,speeds)
    assert r['hr_seconds']<600-20
    assert r['unavailable_speed_seconds']>20
    assert r['general']['valid']

def test_signal_screen_excludes_entire_workout_without_modifying_raw_channels():
    hrs,speeds=samples()
    for t in [100,200,300]:hrs[t]['hr_raw_bpm']=180
    before=deepcopy((hrs,speeds))
    r=calculate(hrs,speeds)
    assert signal_quality(hrs)['rapid_change_episodes']==3
    assert r['signal_quality']['status']=='EXCLUDED'
    assert all(not b['valid'] and b['index'] is None and b['invalid_reason']=='HR_SIGNAL_SUSPECT' for b in [*r['zones'],r['general']])
    assert (hrs,speeds)==before

def test_hrmax_is_normalization_not_signal_validity_cutoff():
    r=calculate(*samples(hr=185))
    assert r['hr_seconds']==600 and r['signal_quality']['status']=='PASSED_SCREEN'
    assert not r['zones'][-1]['valid']  # Outside configured zones, not discarded as a bad sensor.
    r=calculate(*samples(),hrmax=None)
    assert all(b['index'] is None for b in [*r['zones'],r['general']])

def test_excluded_speed_never_steals_another_zones_hr_and_hrmod_flags_do_not_exclude_raw_hr():
    hrs,speeds=samples(840)
    for h in hrs:h['exclusion_reason']='WAVE_NOT_CORRECTED'
    for s in speeds[:420]:s['exclusion_reason']='VFLAT_SAMPLE_EXCLUDED'
    r=calculate(hrs,speeds)
    assert r['general']['hr_seconds']==420
    assert r['general']['index']==pytest.approx(100*150/180/20)
