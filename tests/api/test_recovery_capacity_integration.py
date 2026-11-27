from datetime import date
from types import SimpleNamespace
import pytest
from biathlon import recovery_capacity as c, recovery_v2 as r, speed_duration, speed_equivalence, unified_load
from tests.test_unified_zone_load import BOUNDS, profile, streams
DAY = date(2026, 11, 26)


@pytest.mark.parametrize("indicator", ["SPEED", "HEART_RATE"])
def test_actual_and_planned_segments_use_same_exhaustion_without_changing_q(indicator):
    from apps.api.training_plan_engine import _canonical_load, _add_forecast_session, _day_candidate_loads
    parameters = speed_equivalence.prepare(profile())
    curve = speed_duration.Curve((10., 600., 3600., 21600.), (12., 7., 4., 2.))
    capacity = c.prepare(curve, dict.fromkeys(c.ZONES,30.), parameters)
    shadow = streams(155., 16., seconds=60)
    old = unified_load.integrate(shadow, bounds=BOUNDS, sport="Run", parameters=parameters, leading_indicator=indicator)
    parameters["recovery_capacity"] = capacity
    actual = unified_load.integrate(shadow, bounds=BOUNDS, sport="Run", parameters=parameters, leading_indicator=indicator)
    assert old["zones"] == actual["zones"]
    z = next(row["zone"] for row in actual["zones"] if row["raw_time_min"])
    settings = SimpleNamespace(zone_bounds_bpm=BOUNDS, hrmax_bpm=200, leading_indicator=indicator,
        load_contexts_by_sport={"Run":{"comparison_parameters":parameters}})
    block = {"zone":z,"kind":"WORK","duration_min":1.,"target_hr_bpm":155.,"target_speed_kmh":16.,"load_sport":"Run"}
    direct, effective, _ = _canonical_load([block],settings,[],DAY)
    assert effective.depletion[z] == pytest.approx(actual["recovery_capacity"]["fractions"][z])
    copied = c.Load(effective)
    rows = _add_forecast_session([],DAY,copied)
    rows = _add_forecast_session(rows,DAY,effective)
    result = next(row for row in rows if row["zone"] == z)
    assert result["direct_load"] == pytest.approx(2*direct[z])
    assert result["depletion_fraction"] == pytest.approx(2*effective.depletion[z])
    loader = _day_candidate_loads(settings,[],DAY,dict.fromkeys(r.ZONES,10.))
    _, cached, _ = loader([block],capacity={"zone_tmax_minutes":dict.fromkeys(c.ZONES,30.)})
    assert cached.depletion == effective.depletion
    cached.depletion[z] = 99
    assert loader([block],capacity={"zone_tmax_minutes":dict.fromkeys(c.ZONES,30.)})[1].depletion[z] != 99

