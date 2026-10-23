from copy import deepcopy
import json
import math
import pytest
from biathlon import speed_equivalence as model
from biathlon.speed_zones import v10_anchor
from biathlon.speed_duration import Curve, REFERENCE_TIMES, REFERENCE_SPEEDS


def profile():
    edges = [10.,13.,15.,18.,20.,24.]
    return {"status":"AVAILABLE", "zones":[{"zone":f"Z{i+1}", "low_kmh":edges[i], "high_kmh":edges[i+1]}
            for i in range(5)], "v10":{"status":"AVAILABLE", "duration_s":10., "speed_kmh":24., "evidence":"MEASURED"}}


def test_known_rest_has_zero_q_and_distinct_accounting_in_single_and_aggregate_reports():
    parameters = model.prepare(profile())
    active = model.integrate(parameters, [{"seconds":60,"speed_kmh":15.}])
    mixed = model.integrate(parameters, [{"seconds":60,"speed_kmh":15.},
        {"seconds":30,"speed_kmh":4.,"exclusion_reason":"REST"}],recorded_seconds=100)
    assert mixed["zones"] == active["zones"]
    assert mixed["passive_rest_minutes"] == .5
    assert mixed["accounted_minutes"] == 1.5
    assert mixed["uncovered_minutes"] == pytest.approx(10/60)
    assert mixed["excluded_minutes"] == 0
    total = model.aggregate(parameters, [mixed,mixed])
    assert total["passive_rest_minutes"] == 1.
    assert total["accounted_minutes"] == 3.
    assert total["uncovered_minutes"] == pytest.approx(20/60)


def test_rest_integration_version_does_not_invalidate_approved_zone_parameters(monkeypatch):
    parameters = model.prepare(profile())
    monkeypatch.setattr(model,"INTEGRATION_VERSION","future-accounting-version")
    assert model.prepare(profile())["parameters_version"] == parameters["parameters_version"]
    result = model.integrate(parameters,[],recorded_seconds=0)
    assert result["integration_version"] == "future-accounting-version"
    # Plan passive rest is outside its recorded work subtotal; aggregation
    # must not inflate coverage when combining it with execution accounting.
    active = model.integrate(parameters,[{"seconds":60,"speed_kmh":15.}],recorded_seconds=120)
    planned = {**active,"passive_rest_minutes":5.}
    total = model.aggregate(parameters,[planned])
    assert total["accounted_minutes"] == 1.
    assert total["status"] == "PARTIAL"


def test_all_control_examples_and_exact_proportional_case():
    p = model.prepare(profile())
    assert model.equivalent_seconds(p, 3600, 13, 15, zone="Z2") / 60 == pytest.approx(52)
    assert model.equivalent_seconds(p, 3600, 18, 20, zone="Z4") / 60 == pytest.approx(45)
    assert model.equivalent_seconds(p, 1800, 19, 20, zone="Z4") / 60 == pytest.approx(26.25)
    assert p["zones"][3]["upper_weight"] == pytest.approx(4/3)
    for speed,expected in [(20,1.),(22,1.4),(24,1.8)]:
        v = model.evaluate(p,speed,zone="Z5")
        assert v["weight"] == pytest.approx(expected)
        assert model.equivalent_seconds(p,10,speed,20,zone="Z5") == pytest.approx(10*expected)
    baseline = model.prepare(profile(),settings={"version":"c1-control","origin":"EXPERT_DEFINED","coefficients":dict.fromkeys(model.ZONES,1.)})
    for row in baseline["zones"]:
        for f in (0.,.1,.25,.5,.9,1.):
            v=row["low_kmh"]+f*(row["high_kmh"]-row["low_kmh"])
            result=model.evaluate(baseline,v,zone=row["zone"])
            assert result["weight"] == pytest.approx(v/row["low_kmh"],abs=1e-14)
            assert result["coefficient"] == pytest.approx(result["baseline_coefficient"])


def test_monotone_k_and_reference_endpoints_for_all_zones():
    p=model.prepare(profile())
    for row in p["zones"]:
        values=[model.evaluate(p,row["low_kmh"]+(row["high_kmh"]-row["low_kmh"])*i/100,zone=row["zone"])["coefficient"] for i in range(101)]
        assert values == sorted(values)
        assert values[0 if row["zone"] == "Z5" else -1] == pytest.approx(1.)


def test_boundaries_count_once_and_above_v10_is_not_clamped():
    p=model.prepare(profile())
    assert [model.evaluate(p,v)["zone"] for v in [13,15,18,20,24]] == ["Z2","Z3","Z4","Z5","Z5"]
    result=model.integrate(p,[{"seconds":10,"speed_kmh":v} for v in [13,15,18,20,24,25]])
    assert sum(r["real_seconds"] for r in result["zones"]) == 60
    assert result["classified_minutes"] == pytest.approx(50/60)
    assert result["outside_range_minutes"] == pytest.approx(10/60)
    assert result["zones"][-1]["equivalent_minutes"] == pytest.approx(28/60)
    assert "ABOVE_V10_UPDATE_ANCHOR" in result["warnings"]


@pytest.mark.parametrize("bad",[None,0.,-1.,math.nan,math.inf,True])
def test_missing_invalid_bounds_are_visible_without_made_up_replacement(bad):
    source=profile();source["zones"][0]["low_kmh"]=bad
    p=model.prepare(source)
    assert p["zones"][0]["status"] == "UNAVAILABLE"
    out=model.integrate(p,[{"seconds":60,"speed_kmh":11.}])
    assert out["classified_minutes"] == 0
    assert out["recorded_minutes"] == 1
    assert out["zones"][0]["equivalent_seconds"] is None
    assert out["fallback_indicator"] == "CANONICAL_HR_Q_RETAINED"
    json.dumps(p,allow_nan=False)


def test_tight_ranges_report_sensitivity_and_degenerate_ranges_reject():
    source=profile();source["zones"][3]["high_kmh"]=18.001;source["zones"][4]["low_kmh"]=18.001
    p=model.prepare(source)
    assert "NARROW_RANGE_SENSITIVITY_Z4" in p["warnings"]
    assert p["zones"][3]["slope_per_kmh"] > 200
    source["zones"][3]["high_kmh"]=18.+1e-13;source["zones"][4]["low_kmh"]=18.+1e-13
    assert model.prepare(source)["zones"][3]["reason"] == "NUMERICALLY_DEGENERATE_RANGE"
    source=profile();source["zones"][2]["low_kmh"]=15.5
    assert model.evaluate(model.prepare(source),16.)["reason"] == "INVALID_ZONE_BOUNDARIES"


def test_v10_requires_ten_second_evidence_and_never_uses_10point8_or_gps_peak():
    curve=Curve(REFERENCE_TIMES,REFERENCE_SPEEDS)
    value=v10_anchor(curve,[])
    assert value["status"] == "UNAVAILABLE" and value["speed_kmh"] is None
    assert value["reason"] == "TEN_SECONDS_OUTSIDE_CURVE_DOMAIN"
    test={"duration_s":10.,"speed_kmh":24.,"source":"MANUAL","maximal":True,"start_type":"FLYING"}
    assert v10_anchor(curve,[test])["start_type"] == "FLYING"
    assert v10_anchor(curve,[{**test,"source":"VFLAT"}])["status"] == "UNAVAILABLE"
    assert v10_anchor(curve,[{**test,"duration_s":10.8}])["status"] == "UNAVAILABLE"
    extended=Curve((10.,100.),(8.,6.))
    extrapolated=v10_anchor(extended,[{"duration_s":100.,"speed_kmh":21.6,"maximal":True}])
    assert extrapolated["extrapolated"] is True and extrapolated["evidence"] == "EXTRAPOLATED"
    candidate=profile();candidate["v10"]=extrapolated
    assert "V10_EXTRAPOLATED_NOT_MEASURED" in model.prepare(candidate)["warnings"]
    source=profile();source["v10"]={"status":"UNAVAILABLE","speed_kmh":None}
    assert model.evaluate(model.prepare(source),21)["reason"] == "V10_REQUIRED"


def test_versioned_same_reference_normalization_and_no_cross_zone_time_sum():
    source=profile()
    with pytest.raises(ValueError,match="version"):
        model.prepare(source,tref_minutes={"Z4":20})
    p=model.prepare(source,tref_minutes={"Z4":20},tref_version="speed-normalizer-test-v1",tref_reference_speeds={"Z4":20})
    result=model.integrate(p,[{"seconds":1800.,"speed_kmh":19.}])
    assert result["zones"][3]["equivalent_minutes"] == pytest.approx(26.25)
    assert result["zones"][3]["normalized_q"] == pytest.approx(26.25/20)
    assert "equivalent_minutes" not in result
    assert model.integrate(model.prepare(source),[{"seconds":60,"speed_kmh":19}])["zones"][3]["normalized_q"] is None
    with pytest.raises(ValueError,match="reference speed"):
        model.prepare(source,tref_minutes={"Z4":20},tref_version="test",tref_reference_speeds={"Z4":19})
    changed=deepcopy(source);changed["v10"]["speed_kmh"]=25
    assert model.prepare(changed)["parameters_version"] != model.prepare(source)["parameters_version"]
    with pytest.raises(ValueError,match="different"):
        model.aggregate(model.prepare(source),[model.integrate(model.prepare(changed),[])])


def test_explicit_exclusions_and_other_sports_preserve_real_time():
    p=model.prepare(profile())
    result=model.integrate(p,[{"seconds":60,"speed_kmh":19,"exclusion_reason":"DOWNHILL"}],recorded_seconds=120)
    assert result["uncovered_minutes"] == 2 and result["excluded_minutes"] == 1
    assert result["zones"][3]["equivalent_minutes"] is None
    for sport in ["Ride","NordicSki","RollerSki","WeightTraining"]:
        assert model.evaluate(model.prepare(profile(),sport=sport),19)["reason"] == "RUN_ONLY"
