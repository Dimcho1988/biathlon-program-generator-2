from copy import deepcopy
from datetime import date
from types import SimpleNamespace
import pytest

from apps.api.activity_shadow_pipeline import compute_activity_shadow, activity_shadow_configuration_fingerprint
from apps.api.trainability import compute_trainability
from apps.api.training_plan_engine import _blocks, _canonical_load
from biathlon.training_methods import METHODS
from intervals_inspector.stream_normalizer import NormalizerInput, normalize_stream_intervals
from intervals_inspector.shadow_model import default_shadow_configuration, configuration_for_sport
from vflat_b65.cycling import equivalent_speed

BOUNDS=(100,120,140,160,180,200)


def test_cycling_pipeline_uses_own_speed_and_shifted_index_without_mutating_observations():
    metrics={"heartrate":[143.]*941,"velocity_smooth":[20/3.6]*941,"gradient":[5.]*941,"cadence":[80.]*941}
    normalized=normalize_stream_intervals(NormalizerInput(offsets=list(range(941)),metrics=metrics))
    original=deepcopy(normalized)
    detail={"type":"Ride","start_date":"2026-09-30T08:00:00Z","moving_time":940}
    immutable,derived=compute_activity_shadow(detail=detail,normalized=normalized,zone_bounds_bpm=BOUNDS,explicit_hrmax_bpm=200)
    index=derived["trainability_index"];z=index["zones"][2]
    assert derived["vflat_model_version"]=="vflat_cycling_mechanical_v1"
    assert index["hr_reference_offset_bpm"]==7
    assert z["valid"] and z["mean_hr_bpm"]==150 and z["mean_hr_raw_bpm"]==143
    assert z["index"]==pytest.approx(75/float(equivalent_speed(20/3.6,5)))
    assert all(p["hr_raw_bpm"]==143 for p in immutable["samples"])
    assert immutable["samples"][0]["cadence_rpm"]==80
    assert normalized==original
    assert activity_shadow_configuration_fingerprint(BOUNDS,200,sport="Ride")!=activity_shadow_configuration_fingerprint(BOUNDS,200,sport="NordicSki")


def test_index_equivalent_reference_and_cycle_raw_hr_give_identical_zone_and_ti():
    speed=[{"elapsed_s":t,"dt_s":1.,"vflat_b65_kmh":25.,"grade_raw_pct":0.} for t in range(1,601)]
    def compute(hr,offset):
        rows=[{"elapsed_s":t,"hr_raw_bpm":hr} for t in range(622)]
        return compute_trainability(rows,speed,zone_bounds_bpm=BOUNDS,hrmax_bpm=200,activity_duration_s=620,
            comparison_key="test",source_versions={},hr_reference_offset_bpm=offset)
    run=compute(150,0);bike=compute(143,7)
    for a,b in zip(run["zones"],bike["zones"]):
        assert (a["index"],a["hr_seconds"],a["mean_hr_bpm"])==(b["index"],b["hr_seconds"],b["mean_hr_bpm"])
    assert bike["general"]["index"]==run["general"]["index"]


def test_cycling_zone_configuration_preserves_coefficients_and_reference():
    config=default_shadow_configuration()
    original=deepcopy(config)
    local=configuration_for_sport(config,"Ride")
    assert config==original and configuration_for_sport(config,"Run") is config
    for a,b in zip(config.zones,local.zones):
        assert b.hr_low==a.hr_low-7
        assert b.hr_high==a.hr_high-7


def test_method_blocks_have_lower_cycle_hr_and_same_equivalent_load():
    settings=SimpleNamespace(zone_bounds_bpm=BOUNDS,hrmax_bpm=200)
    method=next(m for m in METHODS if m["zone"]=="Z1" and m["structure"]=="CONTINUOUS" and m["warmup_min"])
    evidence={"target_hr_bpm":113.,"target_speed_kmh":None}
    running=_blocks({**method,"actual_sport":"Run"},20,evidence,settings)
    cycling=_blocks({**method,"actual_sport":"Ride"},20,{**evidence,"target_hr_bpm":106.},settings)
    for a,b in zip(running,cycling):
        if a["target_hr_bpm"] is not None:assert b["target_hr_bpm"]==a["target_hr_bpm"]-7
    r=_canonical_load(running,settings,[],date(2026,10,1));c=_canonical_load(cycling,settings,[],date(2026,10,1))
    assert c==r
