from copy import deepcopy

import numpy as np
import pandas as pd
import pytest

from apps.api.shadow_models.vflat_b65 import run_vflat_b65_shadow
from vflat_b65 import MODEL_VERSION as SKI_VERSION, apply_vflat_b65
from vflat_b65.running import GRADE_TABLE, MODEL_VERSION, apply_running_grade, running_grade_multiplier
from vflat_b65.treadmill import MODEL_VERSION as TREADMILL_VERSION
from vflat_b65.sports import is_running


def frame(grade=0., speed=10., count=90):
    return pd.DataFrame({"timestamp":pd.date_range("2026-09-01",periods=count,freq="s",tz="UTC"),
        "grade_pct":grade,"speed_mps":speed/3.6,"speed_raw_mps":speed/3.6,
        "accel_mps2":0.,"block":0,"turn_flag":False},index=range(count))


def test_table_knots_interpolation_units_and_no_extrapolation():
    grades,factors=zip(*GRADE_TABLE)
    np.testing.assert_array_equal(running_grade_multiplier(grades),factors)
    assert running_grade_multiplier(2.)==pytest.approx(1.06)
    assert running_grade_multiplier(-3.)==pytest.approx(.925)
    assert np.isnan(running_grade_multiplier([-33.,31.,np.nan])).all()


@pytest.mark.parametrize("sport",["Run","TrailRun","VirtualRun","TrackRun","RoadRun","CrossCountryRun","TreadmillRun","trail running"])
def test_running_sports_use_the_table_without_ski_dynamics(sport):
    data=frame(10.)
    data["accel_mps2"]=np.linspace(-2.,2.,len(data))
    original=deepcopy(data)
    result=run_vflat_b65_shadow(data,activity_detail={"type":sport,"distance":1000,"total_elevation_gain":20})
    version=TREADMILL_VERSION if sport in {"VirtualRun","TreadmillRun"} else MODEL_VERSION
    assert result["model_version"]==version
    assert result["terrain_correction"]["applied"] is False
    assert all(r["vflat_b65_kmh"]==pytest.approx(15.) for r in result["timeseries"])
    assert all(r["vflat_model_version"]==version for r in result["timeseries"])
    pd.testing.assert_frame_equal(original,data)


@pytest.mark.parametrize("sport",["NordicSki","RollerSki","Hike","Walk",None,"CrossCountrySkiing","Workout"])
def test_other_sports_retain_identical_existing_b65_result(sport):
    data=frame(6.)
    detail={"type":sport,"name":"Morning run","distance":1000,"total_elevation_gain":20}
    assert not is_running(sport)
    expected=apply_vflat_b65(data,activity_detail=detail)
    result=run_vflat_b65_shadow(data,activity_detail=detail)
    assert result["model_version"]==SKI_VERSION
    np.testing.assert_array_equal([r["vflat_b65_kmh"] for r in result["timeseries"]],expected.vflat_b65_kmh)


def test_flat_identity_masks_and_block_smoothing_do_not_manufacture_measurements():
    data=frame()
    data.loc[45:,"block"]=1
    data.loc[45:,"speed_mps"]=20/3.6
    data.loc[5,"grade_pct"]=31.
    data.loc[15,"speed_mps"]=np.nan
    data.loc[25,"turn_flag"]=True
    data.loc[30,"speed_mps"]=4/3.6
    data.loc[35,"block"]=-1
    result=apply_running_grade(data)
    assert result.loc[44,"vflat_b65_kmh"]==pytest.approx(10.)
    assert result.loc[45,"vflat_b65_kmh"]==pytest.approx(20.)
    assert not result.loc[[5,15,25,30,35],"valid"].any()
    assert result.loc[[5,15,35],"vflat_b65_kmh"].isna().all()
