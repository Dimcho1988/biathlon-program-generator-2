from copy import deepcopy
import math
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from vflat_b65.cycling import equivalent_speed, apply_cycling_grade, CyclingConfig
from vflat_b65.sports import speed_model_versions
from biathlon.sport_heart_rate import local_settings, reference_offset
from biathlon.hr_speed import Predictor
from biathlon import speed_duration, dosing_curve, preliminary_capacity

BOUNDS=(100,120,140,160,180,200)


def test_cycling_flat_identity_and_independent_power_conservation():
    speeds=np.array([5,10,20,30,50])/3.6
    np.testing.assert_allclose(equivalent_speed(speeds,0),speeds*3.6,atol=1e-10)
    # Direct independent balance in SI units: 20 km/h on +5%.
    v=20/3.6;angle=math.atan(.05)
    power=(80*9.81*(math.sin(angle)+.005*math.cos(angle))+.5*1.225*.32*v*v)*v
    flat=float(equivalent_speed(v,5))/3.6
    assert .5*1.225*.32*flat**3+80*9.81*.005*flat==pytest.approx(power,rel=1e-10)
    assert flat*3.6>20
    assert math.isnan(float(equivalent_speed(5/3.6,-3)))  # Braking/coasting, negative driving power.
    assert speed_model_versions("Ride")!=speed_model_versions("Run")!=speed_model_versions("NordicSki")
    assert reference_offset("VirtualRide")==7 and reference_offset("EBikeRide")==0


def test_invalid_coasting_and_post_descent_samples_stay_excluded_after_smoothing():
    frame=pd.DataFrame({"grade_pct":np.zeros(120),"speed_mps":np.full(120,8.),"block":0,"turn_flag":False,"cadence_rpm":80.})
    frame.loc[20:29,"cadence_rpm"]=0
    frame.loc[50:59,"grade_pct"]=-4
    frame.loc[100,"speed_mps"]=np.nan
    before=frame.copy(deep=True)
    result=apply_cycling_grade(frame)
    assert result.loc[20:29,"vflat_b65_kmh"].isna().all()
    assert result.loc[50:79,"vflat_b65_kmh"].isna().all()
    assert math.isnan(result.loc[100,"vflat_b65_kmh"])
    assert result.loc[85,"vflat_b65_kmh"]==pytest.approx(28.8)
    pd.testing.assert_frame_equal(frame,before)
    with pytest.raises(ValueError):CyclingConfig(total_mass_kg=-1)


@pytest.mark.parametrize("blended",[False,True])
def test_cycling_hr_targets_shift_seven_without_changing_capacity_or_blend(blended):
    settings=SimpleNamespace(zone_bounds_bpm=BOUNDS,hrmax_bpm=200,timezone="UTC")
    original=deepcopy(settings)
    local=local_settings(settings,"Ride")
    assert local.zone_bounds_bpm==tuple(b-7 for b in BOUNDS)
    assert settings==original and local_settings(local,"Ride") is local
    tests=[{"duration_s":180,"speed_kmh":40},{"duration_s":720,"speed_kmh":32}]
    curve=speed_duration.calibrated(tests)
    indices={f"Z{i+1}":{"index":100*BOUNDS[i+1]/200/(curve.speed(t)*3.6),"count":3,"seconds":2400}
             for i,t in enumerate((15000,9900,3150,1500))}
    if blended:
        prior=preliminary_capacity.build(BOUNDS,200,indices)
        curve=dosing_curve.Curve(prior["curve"],curve)
    cls=dosing_curve.Predictor if blended else Predictor
    base=cls(curve,BOUNDS,200,indices)
    bike=cls(curve,local.zone_bounds_bpm,local.hrmax_bpm,indices,normalization_offset_bpm=7)
    assert base.times==pytest.approx(bike.times)
    for hr in (130,150,170,190):
        assert bike.duration(hr-7)==pytest.approx(base.duration(hr))
        assert bike.speed_for_hr(hr-7)==pytest.approx(base.speed_for_hr(hr))
        assert bike.hr_for_speed(base.speed_for_hr(hr))==pytest.approx(hr-7)
