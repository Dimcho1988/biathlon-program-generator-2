import math
import random
import pytest
from biathlon.hr_speed import Predictor,TMAX_RANGES_S,volume_duration_centers
from biathlon.speed_duration import calibrated,adjusted

BOUNDS=[80,137,148,160,170,180]

def make(indices=None,bounds=BOUNDS):
    return Predictor(calibrated([{'duration_s':1200,'speed_kmh':22}]),bounds,bounds[-1],indices or {})

def test_guard_midpoints_existing_zone_weight_and_shared_z5_boundary():
    p=make()
    assert p.times==(12600,8100,3300,1200)
    assert p.duration(155)==pytest.approx(3300/.85)
    assert p.duration(170)==1200
    assert p.duration(180)==pytest.approx(1200/1.3)
    assert p.summary()['zones'][-1]['source']=='Z4_SHARED_BOUNDARY'

def test_in_range_index_is_retained_and_out_of_range_uses_exact_midpoint():
    p=make();c=p.curve
    index=100*160/180/(c.speed(2700)*3.6)
    p=make({'Z3':{'index':index,'count':9},'Z2':{'index':1000,'count':7}})
    assert p.times[2]==pytest.approx(2700)
    assert p.anchors[2]['source']=='INDEX'
    assert p.anchors[1]['duration_s']==8100
    assert p.anchors[1]['reason']=='INDEX_OUTSIDE_DURATION_BOUNDS'

def test_conflicting_anchors_fall_back_together_to_preserve_order():
    c=make().curve
    indices={z:{'index':100*BOUNDS[i+1]/180/(c.speed(t)*3.6)} for i,(z,t) in enumerate([('Z1',7300),('Z2',10000),('Z3',3000),('Z4',1000)])}
    p=make(indices)
    assert p.times==(12600,8100,3300,1200)
    assert all(a['reason']=='CONFLICTING_ZONE_ANCHORS' for a in p.anchors)
    assert p.summary()['conflicting_zones']==[['Z1','Z2']]
    assert p.anchors[0]['candidate_duration_s']==pytest.approx(7300)
    assert all(a['candidate_reason']=='ACCEPTED' for a in p.anchors)

def test_diagnostics_distinguish_curve_domain_from_zone_limits_without_extrapolation():
    c=make().curve
    for speed,reason,t in [(c.speed(c.times[-1])*3.6/2,'SPEED_BELOW_CURVE',None),
                           (c.speed(c.times[0])*3.6*2,'SPEED_ABOVE_CURVE',None),
                           (c.speed(900)*3.6,'DURATION_BELOW_MIN',900),
                           (c.speed(6000)*3.6,'DURATION_ABOVE_MAX',6000),
                           (c.speed(2700)*3.6,'ACCEPTED',2700)]:
        a=make({'Z3':{'index':100*160/180/speed,'count':8}}).anchors[2]
        assert a['candidate_speed_kmh']==pytest.approx(speed)
        assert a['candidate_reason']==reason
        assert a['candidate_duration_s']==(pytest.approx(t) if t else None)
        assert a['duration_s']==pytest.approx(2700 if reason=='ACCEPTED' else 3300)
    assert make().anchors[2]['candidate_reason']=='NO_VALID_INDEX'

def test_randomized_profiles_are_continuous_monotone_and_bidirectionally_consistent():
    rng=random.Random(123)
    for _ in range(30):
        edges=sorted(rng.sample(range(110,180),4))
        bounds=[70,*edges,190]
        c=make(bounds=bounds).curve
        indices={z:{'index':100*bounds[i+1]/190/(c.speed(rng.uniform(*limits))*3.6)} for i,(z,limits) in enumerate(TMAX_RANGES_S.items())}
        p=make(indices,bounds)
        hrs=[p.min_hr+(p.max_hr-p.min_hr)*i/150 for i in range(151)]
        speeds=[p.speed_for_hr(h) for h in hrs]
        assert all(a<b for a,b in zip(speeds,speeds[1:]))
        for h,v in zip(hrs[::15],speeds[::15]):assert p.hr_for_speed(v)==pytest.approx(h,abs=1e-6)
        for h in bounds[1:-1]:
            if p.min_hr<h<p.max_hr:assert p.duration(h-1e-7)==pytest.approx(p.duration(h+1e-7),rel=1e-6)
    with pytest.raises(ValueError):p.speed_for_hr(p.min_hr-1)
    with pytest.raises(ValueError):p.hr_for_speed(p.speed_range[1]*1.01)

def test_volume_position_has_no_index_or_hr_prediction_dependency_and_preserves_test():
    assert volume_duration_centers(BOUNDS)[:4]==[12600,8100,3300,1200]
    tests=[{'duration_s':1200,'speed_kmh':22}];c=calibrated(tests)
    def forbidden(_):raise AssertionError('Volume must not use estimated HR')
    tuned,f=adjusted(c,tests,forbidden,[],[-.1,.1,.03,-.1,.1],duration_centers=volume_duration_centers(BOUNDS))
    assert tuned.speed(1200)*3.6==pytest.approx(22)
    assert 0<=f<=1
    assert all(math.isfinite(tuned.speed(t)) for t in tuned.times)
