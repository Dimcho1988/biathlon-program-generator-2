from copy import deepcopy
from datetime import date, timedelta
import math

import pytest

from biathlon import recovery_capacity as c, recovery_v2 as r, speed_duration, speed_equivalence, unified_load
from tests.test_unified_zone_load import BOUNDS, profile, streams

DAY = date(2026, 11, 26)


def history(q, u):
    rows = [{"date": (DAY-timedelta(days=i)).isoformat(), "zone": "Z5",
             "effective_load": 11., "direct_load": 5., "depletion_fraction": 0.}
            for i in range(1, 41)]
    return rows+[{"date": DAY.isoformat(), "zone": "Z5", "effective_load": q+7,
                  "direct_load": q, "depletion_fraction": u}]


@pytest.mark.parametrize("q,u,days,amplitude", [(20, 2/3, 3.6, 200/3), (30, 1, 6.6, 100), (40, 4/3, 8.8, 100)])
def test_approved_examples_keep_full_q_above_exhaustion(q, u, days, amplitude):
    result = r.simulate(history(q,u), target=DAY)
    today = result["daily"][-1]
    assert today["impulse"] == pytest.approx(amplitude)
    assert today["baseline_daily_min"] == 5
    assert today["isolated_days_to_90"] == pytest.approx(days)
    assert today["exhaustion_multiplier"] == pytest.approx(c.multiplier(u))
    assert today["recovery_basis"] == "CAPACITY_Q"


def test_continuity_and_accumulated_fatigue_are_not_capped_or_replaced():
    rows = history(30,1)
    next_day = DAY+timedelta(days=1)
    before = r.simulate(rows, target=next_day)["current"][4]["residual_fatigue"]
    rows.append({"date": next_day.isoformat(), "zone": "Z5", "effective_load": 30,
                 "direct_load": 30, "depletion_fraction": 1})
    result = r.simulate(rows, target=next_day)
    assert result["current"][4]["residual_fatigue"] == pytest.approx(before+100)
    assert result["current"][4]["readiness_percent"] == 0
    assert c.multiplier(.5-1e-8) == .8
    assert c.multiplier(.5+1e-8) == pytest.approx(.8)
    assert c.multiplier(2) == 1.1


def test_cache_distinguishes_same_e_and_q_with_different_intensity():
    cache = {}
    for fraction in (.4, .8, 1., .4):
        rows = history(20, fraction)
        full = r.simulate(rows, target=DAY)
        for _ in range(2):
            fast = r.simulate(rows, target=DAY, include_details=False, calculation_cache=cache)
            assert fast["current"] == full["current"]


def test_inverse_table_matches_curve_without_per_segment_inversion():
    curve = speed_duration.Curve((10., 120., 600., 1800., 7200., 21600.), (10., 8., 6.5, 5.8, 4., 3.))
    parameters = speed_equivalence.prepare(profile())
    capacity = c.prepare(curve, dict.fromkeys(c.ZONES,30.), parameters)
    for j in range(1001):
        t = math.exp(math.log(10)+math.log(2160)*j/1000)
        speed = curve.speed(t)*3.6
        capacity["bounds"]["Z3"] = [speed*.9, speed*1.1]
        fraction, source = c.segment(capacity, {"zone":"Z3", "relative_position":.5}, 1.)
        assert source == "CURVE_ESTIMATE"
        assert fraction == pytest.approx(60/t, rel=.001)



def test_daily_projection_ignores_spillover_and_preserves_estimate_provenance():
    source={"daily":[{"date":str(DAY),"zone":"Z5","effective_load":60.}],"activities":[
        {"date":str(DAY),"zones":[{"zone":"Z5","raw_time_min":20.,"equivalent_time_min":25.}],
         "load_source":{"capacity_context":{"minutes":{"Z5":30.}},
                        "recovery_capacity":{"fractions":{"Z5":2/3}}}},
        {"date":str(DAY),"zones":[{"zone":"Z5","raw_time_min":5.,"equivalent_time_min":8.,"planning_estimated_q":3.}],
         "load_source":{"capacity_context":{"minutes":{"Z5":30.}},
                        "recovery_capacity":{"fractions":{"Z5":.1}}}}]}
    saved=deepcopy(source)
    row=c.daily_rows(source)[0]
    assert row["direct_load"] == 33
    assert row["depletion_fraction"] == pytest.approx(2/3+.1+.1)
    assert row["capacity_estimated"] is True
    assert source == saved


def test_zero_or_absent_q_history_uses_initial_base_without_mixing_legacy_e():
    cfg=r.defaults()
    cfg['Z5']['initial_daily_min']=5.
    today={"date":str(DAY),"zone":"Z5","effective_load":99.,"direct_load":30.,"depletion_fraction":1.}
    for prior in ([], [{"date":str(DAY-timedelta(days=1)),"zone":"Z5","effective_load":1000.}],
                  [{"date":str(DAY-timedelta(days=1)),"zone":"Z5","effective_load":0.,"direct_load":0.,"depletion_fraction":0.}]):
        result=r.simulate(prior+[today],cfg,target=DAY)['daily'][-1]
        assert result['baseline_daily_min'] == 5.
        assert result['isolated_days_to_90'] == pytest.approx(6.6)


def test_frozen_reservation_load_keeps_capacity_and_copy_is_independent():
    original=c.Load({'Z5':40.},direct={'Z5':30.},depletion={'Z5':1.})
    copy=c.Load.thaw(original.frozen())
    assert copy == original and copy.direct == original.direct and copy.depletion == original.depletion
    copy.depletion['Z5']=.3
    assert original.depletion['Z5'] == 1.
