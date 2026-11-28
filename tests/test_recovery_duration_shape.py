from copy import deepcopy
from datetime import date, timedelta
import math

import pytest

from biathlon import recovery_v2 as r

DAY = date(2026, 11, 26)


@pytest.mark.parametrize("days,shape", [(0,1), (1,1), (2,1), (4.5,3), (7,5), (15,5)])
def test_duration_shape_boundaries(days, shape):
    assert r.duration_shape(days) == shape


@pytest.mark.parametrize("days", [1., 2., 2.01, 4.5, 7., 7.63, 15.])
def test_dynamic_shape_preserves_dose_and_deadline_and_accelerates_early_recovery(days):
    cfg = {**r.defaults()["Z5"], "shape": 1.}
    old = r.Impulse(DAY, 100., math.log(10)/days, 1., days)
    new = r.impulse(DAY, days*20, 20, cfg)
    assert old.amplitude == new.amplitude
    assert old.nominal_days == new.nominal_days
    assert r.days_to_ready([new], DAY) == pytest.approx(days)
    assert new.residual(days) == pytest.approx(old.residual(days))
    assert new.residual(days/4) <= old.residual(days/4) + 1e-12
    if days <= 2:
        assert new.residual(.5) == pytest.approx(old.residual(.5))


def test_original_dose_shape_is_frozen_and_future_load_cannot_change_it():
    rows = [{"date": DAY.isoformat(), "zone": "Z5", "effective_load": 16.}]
    now = r.simulate(rows, target=DAY)
    later = r.simulate(rows + [{"date": (DAY+timedelta(days=2)).isoformat(),
                               "zone": "Z5", "effective_load": 100.}],
                       target=DAY)
    assert later == now
    next_day = r.simulate(rows, target=DAY+timedelta(days=1))
    assert next_day["daily"][0]["shape_used"] == now["daily"][0]["shape_used"] == 5.


def test_dynamic_curve_matches_cached_planning_and_detailed_dashboard():
    rows = [{"date": (DAY-timedelta(days=i)).isoformat(), "zone": "Z5", "effective_load": 0.} for i in range(1, 41)]
    rows.append({"date": DAY.isoformat(), "zone": "Z5", "effective_load": 16.})
    configs = r.defaults()
    before = deepcopy((rows, configs))
    cache = {}
    for age in (0, 1, 2):
        target = DAY + timedelta(days=age)
        detailed = r.simulate(rows, configs, target=target)
        for _ in range(2):
            planning = r.simulate(rows, configs, target=target, include_details=False, calculation_cache=cache)
            assert planning["current"] == detailed["current"]
    assert (rows, configs) == before


def test_dynamic_policy_replaces_manual_shape_without_mutating_it():
    cfg = {**r.defaults()["Z5"], "shape": 9., "duration_coefficient": .5}
    before = dict(cfg)
    i = r.impulse(DAY, 180, 20, cfg)
    assert i.nominal_days == 4.5 and i.shape == 3.
    assert cfg == before
    assert r.impulse(DAY, 180, 20, {**cfg, "shape": 1.}) == i
