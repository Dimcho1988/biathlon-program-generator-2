from copy import deepcopy

import pytest

from vflat_b65.load import complete_load_speed


def sample(raw=16., vflat=17., grade=0., dt=1., exclusion=None):
    return {"speed_raw_kmh": raw, "vflat_b65_kmh": vflat, "grade_raw_pct": grade,
            "dt_s": dt, "exclusion_reason": exclusion}


@pytest.mark.parametrize("factor", [1., .95, .87])
@pytest.mark.parametrize("target", [16., 20.])
def test_45_minutes_known_and_15_descent_preserve_vflat_and_balance_identical_time(factor, target):
    source = [sample(raw=target, vflat=17.*factor) for _ in range(45*60)]
    source += [sample(raw=target, vflat=35., grade=-5.) for _ in range(15*60)]
    before = deepcopy(source)
    rows, info = complete_load_speed(source, sport="NordicSki", terrain_factor=factor)
    assert source == before
    assert [r["vflat_b65_kmh"] for r in rows] == [r["vflat_b65_kmh"] for r in source]
    assert all(r["load_speed_kmh"] == 17.*factor for r in rows[:2700])
    assert info["descent_speed_kmh"] == pytest.approx((target*60-17*45)/15*factor)
    assert sum(r["dt_s"]*r["load_speed_kmh"] for r in rows)/3600 == pytest.approx(target*factor)
    assert info["balance_duration_s"] == 3600
    assert info["estimated_descent_duration_s"] == 900


@pytest.mark.parametrize("known,expected,status", [(20.,4.,"ACTIVE"),(64/3,0.,"REST"),(19.,7.,"ACTIVE")])
def test_low_residual_is_rest_but_retains_numeric_speed_for_balance(known, expected, status):
    rows, info = complete_load_speed([sample(vflat=known) for _ in range(3)] + [sample(grade=-4)], sport="NordicSki")
    assert info["balance_status"] == "BALANCED"
    assert rows[-1]["load_speed_kmh"] == pytest.approx(expected)
    assert rows[-1]["load_status"] == status
    assert rows[-1]["load_source"] == "DESCENT_BALANCE"
    assert sum(r["load_speed_kmh"] for r in rows)/4 == pytest.approx(16.)


def test_negative_residual_is_unavailable_not_zero_or_a_change_to_known_speeds():
    rows, info = complete_load_speed([sample(vflat=24.) for _ in range(3)] + [sample(grade=-4)], sport="NordicSki")
    assert info["balance_status"] == "NEGATIVE_RESIDUAL"
    assert rows[-1]["load_speed_kmh"] is None
    assert rows[-1]["load_status"] == "UNAVAILABLE"
    assert rows[-1]["load_reason"] == "NEGATIVE_DESCENT_RESIDUAL"
    assert info["estimated_descent_duration_s"] == 0
    assert all(r["load_speed_kmh"] == 24 for r in rows[:3])


def test_raw_rest_grade_boundary_quality_and_zero_duration_have_distinct_roles():
    source = [sample(raw=4.9, grade=None, vflat=None, exclusion="VFLAT_SAMPLE_EXCLUDED"),
              sample(raw=5., vflat=5., grade=-3.), sample(raw=16., grade=-3.01),
              sample(grade=-5., exclusion="TURN"), sample(grade=-5., raw=None),
              sample(grade=-5., dt=0.), sample(grade=-5., dt=30.)]
    rows, info = complete_load_speed(source, sport="RollerSki")
    assert rows[0]["load_status"] == "UNAVAILABLE" and rows[0]["load_speed_kmh"] is None
    assert rows[1]["load_status"] == "ACTIVE" and rows[1]["load_source"] == "VFLAT"
    assert rows[2]["load_source"] == "DESCENT_BALANCE"
    assert all(r["load_status"] == "UNAVAILABLE" for r in rows[3:])
    assert info["balance_duration_s"] == 2
    assert info["descent_duration_s"] == 1
    assert info["excluded_interval_s"] == 3


def test_fractional_intervals_weight_the_residual_and_running_keeps_measured_downhill():
    source = [sample(raw=16.,vflat=18.,dt=.5),sample(raw=16.,grade=-5.,dt=1.)]
    ski, info = complete_load_speed(source,sport="CrossCountrySkiing")
    assert info["descent_speed_kmh"] == 15.
    assert sum(r["load_speed_kmh"]*r["dt_s"] for r in ski)/1.5 == 16.
    run, info = complete_load_speed(source+[sample(grade=-33.,exclusion="VFLAT_SAMPLE_EXCLUDED")],sport="TrailRun")
    assert run[1]["load_speed_kmh"] == source[1]["vflat_b65_kmh"]
    assert info["balance_status"] == "NOT_NEEDED"
    assert run[-1]["load_status"] == "UNAVAILABLE"
    unchanged, info = complete_load_speed(source,sport="Ride")
    assert unchanged == source and info is None
