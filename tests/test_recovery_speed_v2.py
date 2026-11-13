from datetime import date,timedelta
import math
import pytest
from biathlon import recovery_v2 as r
from biathlon import speed_duration as s

TODAY=date(2026,9,12)

def config(**kwargs):
    return {**r.defaults()["Z2"],"sensitivity":1.,**kwargs}

@pytest.mark.parametrize("shape",[1,2,5,10])
def test_60_over_20_is_three_days_to_absolute_90(shape):
    i=r.impulse(TODAY,60,20,config(shape=shape))
    assert i.residual(3)==pytest.approx(10,rel=1e-12)
    assert r.days_to_ready([i],TODAY)==pytest.approx(3)
    if shape==1:
        assert 100-i.residual(1)==pytest.approx(53.584111664)
    # Recovery gain is greatest initially and gradually slows.
    gains=[i.residual(t)-i.residual(t+.1) for t in (0,.5,1,2)]
    assert all(a>b for a,b in zip(gains,gains[1:]))

def test_duration_and_shape_are_independent_and_fatigue_is_retained():
    a=r.impulse(TODAY,60,20,config())
    b=r.impulse(TODAY,60,20,config(shape=5))
    assert a.residual(.3)!=pytest.approx(b.residual(.3))
    assert a.residual(3)==pytest.approx(b.residual(3))
    old=r.impulse(TODAY-timedelta(days=1),60,20,config())
    assert r.days_to_ready([old,a],TODAY)>3
    doubled=r.impulse(TODAY,60,20,config(duration_coefficient=2))
    assert r.days_to_ready([doubled],TODAY)==pytest.approx(6)

def test_tiny_load_does_not_reset_readiness_but_still_accumulates():
    i=r.impulse(TODAY,.5,20,config())
    assert i.amplitude==2.5
    assert r.days_to_ready([i],TODAY)==0
    assert r.days_to_ready([i]*5,TODAY)>0
    assert r.impulse(TODAY,0,20,config()) is None

def test_baseline_calendar_rest_days_and_zero_history():
    assert r.baseline([],20)==(20,None,0,"NO_HISTORY")
    assert r.baseline([0]*40,20)==(20,0,40,"NO_ZONE_LOAD")
    assert r.baseline([140]+[0]*6,20)==(40,20,7,"PERSONAL")
    assert r.baseline([10],20)==(30,10,1,"SHORT_HISTORY")
    assert r.baseline([1e-9]+[0]*39,20)[0]==pytest.approx(20)
    assert r.baseline([1]+[0]*39,20)[3]=="SPARSE_ZONE_HISTORY"
    assert r.baseline([1000]*40,20)[0]==1020  # no Tref bounds

def test_permanent_base_prevents_sparse_zone_tail_and_remains_editable():
    rows=[{"date":(TODAY-timedelta(days=n)).isoformat(),"zone":"Z5","effective_load":.4}
          for n in range(1,41)]
    rows.append({"date":TODAY.isoformat(),"zone":"Z5","effective_load":10})
    result=r.simulate(rows,target=TODAY)
    dose=result["daily"][-1]
    assert dose["baseline_raw_daily_min"]==pytest.approx(.4)
    assert dose["baseline_daily_min"]==pytest.approx(2.4)
    assert dose["isolated_days_to_90"]==pytest.approx(10/2.4)
    current=next(row for row in result["current"] if row["zone"]=="Z5")
    assert current["days_to_practical_recovery"]>dose["isolated_days_to_90"]
    cfg=r.defaults();cfg["Z5"]["initial_daily_min"]=4
    changed=r.simulate(rows,cfg,target=TODAY)["daily"][-1]
    assert changed["baseline_daily_min"]==pytest.approx(4.4)
    assert changed["isolated_days_to_90"]==pytest.approx(10/4.4)
    assert changed["effective_load"]==dose["effective_load"]

@pytest.mark.parametrize("covered_days",[0,40])
def test_base_addition_never_creates_load_or_initial_fatigue(covered_days):
    rows=[{"date":(TODAY-timedelta(days=n)).isoformat(),"zone":z,"effective_load":0}
          for z in r.ZONES for n in range(covered_days)]
    result=r.simulate(rows,target=TODAY)
    for current in result["current"]:
        assert current["baseline_daily_min"]==r.INITIAL_DAILY[current["zone"]]
        assert current["residual_fatigue"]==0
        assert current["readiness_percent"]==100
        assert current["days_to_practical_recovery"]==0
    assert all(row["impulse"]==0 and row["effective_load"]==0 for row in result["daily"])

def test_future_doses_cannot_change_past_recovery():
    rows=[{"date":(TODAY-timedelta(days=n)).isoformat(),"zone":"Z2","effective_load":20} for n in range(41)]
    first=r.simulate(rows,target=TODAY)
    rows += [{"date":(TODAY+timedelta(days=1)).isoformat(),"zone":"Z2","effective_load":900}]
    assert r.simulate(rows,target=TODAY)==first
    final=[x for x in first["daily"] if x["date"]==TODAY.isoformat()][0]
    assert final["history_days"]==40
    assert final["baseline_daily_min"]==40

def test_missing_calendar_days_are_not_invented_rest():
    rows=[{"date":(TODAY-timedelta(days=n)).isoformat(),"zone":"Z2","effective_load":20} for n in (4,2,0)]
    result=r.simulate(rows,target=TODAY)
    assert result["daily"][-1]["history_days"]==2
    with pytest.raises(ValueError): r.simulate(rows+[rows[0]],target=TODAY)


def test_shared_forecast_covers_two_days_and_matches_each_zone_deadline():
    rows = [{"date": TODAY.isoformat(), "zone": z, "effective_load": dose}
            for z, dose in zip(r.ZONES, (0, .5, 20, 60, 80, 5))]
    result = r.simulate(rows, target=TODAY)
    for current in result["current"]:
        points = [p for p in result["forecast"] if p["zone"] == current["zone"]]
        assert points[0]["readiness_percent"] == current["readiness_percent"]
        assert points[-1]["days"] >= 2
        assert all(any(p["days"] == hour / 24 for p in points) for hour in range(49))
        deadline = current["days_to_practical_recovery"]
        at_deadline = next(p for p in points if p["days"] == deadline)
        if deadline:
            assert at_deadline["readiness_percent"] == pytest.approx(90)
        else:
            assert at_deadline["readiness_percent"] >= 90
        assert all(a["readiness_percent"] <= b["readiness_percent"] for a, b in zip(points, points[1:]))


def test_residual_attribution_preserves_old_doses_and_sums_to_current_fatigue():
    rows = [{"date": (TODAY-timedelta(days=age)).isoformat(), "zone": z, "effective_load": dose}
            for z in r.ZONES for age, dose in ((21, 60), (20, 40), (2, 5), (0, 0))]
    result = r.simulate(rows, target=TODAY)
    for current in result["current"]:
        daily = [row for row in result["daily"] if row["zone"] == current["zone"]]
        assert math.fsum(row["residual_fatigue_now"] for row in daily) == pytest.approx(current["residual_fatigue"])
        assert daily[-1]["residual_fatigue_now"] == 0
        assert all(row["residual_fatigue_now"] >= 0 for row in daily)

def observations():
    return [{"duration_s":120,"speed_kmh":25.92,"use_for_cs":True},
            {"duration_s":720,"speed_kmh":21.6,"use_for_cs":True},
            {"duration_s":1200,"speed_kmh":20.52,"use_for_cs":True}]

def check_curve(curve):
    times=[math.exp(curve._x[0]+(curve._x[-1]-curve._x[0])*i/1000) for i in range(1001)]
    speeds=[curve.speed(t) for t in times]
    distances=[curve.distance(t) for t in times]
    assert all(a>b for a,b in zip(speeds,speeds[1:]))
    assert all(a<b for a,b in zip(distances,distances[1:]))
    for t in times[::100]:
        assert curve.inverse(curve.speed(t))==pytest.approx(t)
        assert curve.inverse(curve.distance(t),distance=True)==pytest.approx(t)
    for t in curve.times[1:-1]:
        left=curve.log_slope(t*(1-1e-11))
        right=curve.log_slope(t*(1+1e-11))
        assert -1<left<0 and -1<right<0
        assert left==pytest.approx(right,rel=2e-4,abs=1e-7)

def test_reference_one_and_multiple_tests_are_invertible_and_smooth():
    for count in (0,1,3):
        chosen=observations()[:count]
        c=s.calibrated(chosen)
        check_curve(c)
        for t in chosen: assert c.speed(t["duration_s"])*3.6==pytest.approx(t["speed_kmh"])
    reference=s.calibrated([])
    anchored=s.calibrated([{ "duration_s":1020,"speed_kmh":reference.speed(1020)*3.6*.85}])
    assert anchored.speed(3600)==pytest.approx(reference.speed(3600)*.85)

def test_conflicts_extrapolation_and_nonfinite_are_rejected():
    with pytest.raises(ValueError): s.calibrated([{"duration_s":120,"speed_kmh":20},{"duration_s":720,"speed_kmh":21}])
    with pytest.raises(ValueError): s.calibrated(observations()+[observations()[0]])
    with pytest.raises(ValueError): s.calibrated([]).speed(2)
    with pytest.raises(ValueError): s.calibrated([]).inverse(float("nan"))

def test_extreme_adjacent_log_slopes_remain_smooth_and_bounded():
    c=s.Curve((1.,math.e,math.e**2),(10.,10*math.exp(-.001),10*math.exp(-1)))
    check_curve(c)

def test_zone_correction_smooth_transition_and_anchor_preservation():
    assert s.zone_adjustment(150,[140,160],[.08,-.04])==pytest.approx(.02)
    assert s.volume_correction("Z2",None)==0
    assert s.volume_correction("Z2",180)==pytest.approx(0)
    assert s.volume_correction("Z2",9999)==.1
    c,f=s.adjusted(s.calibrated(observations()),observations(),lambda v:6*v,[100,120,140,160,180],[.1,.08,-.04,.1,-.1])
    check_curve(c)
    assert 0<=f<=1
    for t in observations(): assert c.speed(t["duration_s"])*3.6==pytest.approx(t["speed_kmh"])

def test_cs_uses_real_eligible_tests_with_residuals():
    assert s.critical_speed(observations()[:1])["status"]=="INSUFFICIENT_TESTS"
    assert s.critical_speed(observations()[:2])["status"]=="PRELIMINARY_TWO_TESTS"
    result=s.critical_speed(observations())
    assert result["status"]=="FITTED" and result["distance_rmse_m"]>0
    assert 0<result["speed_kmh"]<min(t["speed_kmh"] for t in observations())
    # Even an incorrectly marked exploratory entry must not become a CS anchor.
    exploratory=[{**t,"test_mode":"EXPLORATORY","use_for_cs":True} for t in observations()]
    assert s.critical_speed(exploratory)["count"]==0
    assert s.critical_speed(observations()+exploratory)==result


@pytest.mark.parametrize("shape",[1,5,10])
def test_near_ready_doses_have_finite_decay_and_continuous_residuals(shape):
    # A tiny change around A=10 must not introduce a nearly permanent tail.
    for boundary in (10.,20.):
        impulses=[r.impulse(TODAY,a/100*20,20,config(shape=shape))
                  for a in (boundary-1e-7,boundary,boundary+1e-7)]
        future=[i.residual(.5) for i in impulses]
        assert max(future)-min(future)<1e-5
    near=r.impulse(TODAY,2.00000002,20,config(shape=shape))
    assert near.residual(near.nominal_days)<=near.amplitude*.5+1e-10
    assert r.days_to_ready([near],TODAY)<1e-5
    row=r.simulate([{"date":TODAY.isoformat(),"zone":"Z2","effective_load":3}],
                  {**r.defaults(),"Z2":config(shape=shape)},target=TODAY)["daily"][0]
    assert row["isolated_days_to_90"]==pytest.approx(r.days_to_ready([
        r.impulse(TODAY,3,20,config(shape=shape))],TODAY))
