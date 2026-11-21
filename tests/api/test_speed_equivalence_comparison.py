from copy import deepcopy
from uuid import UUID

import pytest

from apps.api import model_service, speed_load
from apps.api.component_load_context import context_from_speed_view
from biathlon import speed_equivalence
from tests.api.test_manual_speed_tests import Repository, ACTOR, body, fixed_time
from tests.api.test_speed_load import Repository as SpeedRepository, TODAY, SETTINGS
from tests.test_speed_equivalence import profile


def test_manual_ten_second_mean_is_separate_boundary_and_does_not_change_curve():
    repo=Repository()
    main=model_service.save_manual_test(repo,"ath-test",body(),ACTOR)
    before=model_service.speed_view(repo,"ath-test","Run",duration_s=180.)
    sprint_id=UUID("44444444-4444-4444-8444-444444444444")
    boundary=model_service.save_manual_test(repo,"ath-test",body(test_id=sprint_id,duration_s=10.,distance_m=None,speed_kmh=32.,start_type="FLYING"),ACTOR)
    after=model_service.speed_view(repo,"ath-test","Run",duration_s=180.)
    assert {k:after["prediction"][k] for k in ("duration_s","speed_kmh","distance_m")} == {k:before["prediction"][k] for k in ("duration_s","speed_kmh","distance_m")}
    assert after["active_test_keys"] == [main["entry_key"]]
    assert after["boundary_test_keys"] == [boundary["entry_key"]]
    assert after["speed_zones"]["v10"]["speed_kmh"] == 32.
    assert after["speed_zones"]["v10"]["evidence"] == "MEASURED"
    assert after["speed_zones"]["v10"]["start_type"] == "FLYING"
    assert [{k:p[k] for k in ("duration_s","speed_kmh","distance_m")} for p in after["points"]] == [{k:p[k] for k in ("duration_s","speed_kmh","distance_m")} for p in before["points"]]
    assert context_from_speed_view(after)["fingerprint"] != context_from_speed_view(before)["fingerprint"]
    assert context_from_speed_view(after)["minutes"] == context_from_speed_view(before)["minutes"]
    from apps.api.speed_zone_history import _mapping
    from datetime import date
    new_mapping=_mapping(after,repo.athlete_settings("ath-test"),date(2026,9,19),"Run")[0]
    old_mapping=_mapping(before,repo.athlete_settings("ath-test"),date(2026,9,19),"Run")[0]
    assert new_mapping["bounds_kmh"][:-1] == old_mapping["bounds_kmh"][:-1]
    assert new_mapping["bounds_kmh"][-1] == 32.
    assert repo.writes[-1]["p_payload"]["boundary_only"] is True
    capacity=model_service.speed_view(repo,"ath-test","Run",_capacity_only=True)
    params=context_from_speed_view(capacity)["comparison_parameters"]
    assert params["zones"][-1]["high_kmh"] == 32.
    assert params["zones"][-1]["tref_minutes"] is None
    assert params["parameters_version"] == context_from_speed_view(after)["comparison_parameters"]["parameters_version"]
    # Existing longer curve-anchor edits remain valid with boundary evidence stored.
    model_service.save_manual_test(repo,"ath-test",body(duration_s=190.,expected_revision=1),ACTOR)
    assert model_service.speed_view(repo,"ath-test","Run")["active_test_count"] == 1


def test_actual_comparison_uses_individual_profile_without_mutating_legacy_ledger(monkeypatch):
    repo=SpeedRepository()
    repo.add("first","Run",TODAY,150,19.)
    baseline=speed_load._activity_record(repo.activities[0],repo.shadows["first"],None,"NO_PRIOR_SPORT_INDEX")
    params=speed_equivalence.prepare(profile())
    context=context_from_speed_view({"status":"UNAVAILABLE","sport":"Run"})
    context["comparison_parameters"]=params
    monkeypatch.setattr(speed_load,"read_contexts",lambda *a,**kw:{"Run":context})
    output=speed_load.history_view(repo,"authorized-athlete",today=TODAY)
    comparison=output["speed_equivalence_comparison"]
    assert output["classified_minutes"] == 0  # Legacy causal TI history still unavailable.
    assert comparison["classified_minutes"] == pytest.approx(10.)
    assert comparison["zones"][3]["equivalent_minutes"] == pytest.approx(8.75)
    assert comparison["zones"][3]["baseline_equivalent_minutes"] == pytest.approx(9.5)
    assert comparison["zones"][3]["normalized_q"] is None
    actual=output["activities"][0]
    assert actual["zones"] == baseline["zones"]
    assert "SPEED_TREF_NOT_MIGRATED" in comparison["warnings"]
    assert output["load_role"] == "INDEPENDENT_SPEED_VIEW_OF_SHARED_KERNEL"


def test_smoothed_short_sprint_is_unresolved_not_fabricated_equivalent_zero():
    params=speed_equivalence.prepare(profile())
    activity={"activity_ref":"sprint","sport":"Run","local_date":"2026-10-01","duration_min":10/60}
    row={"activity_ref":"sprint","shadow_payload":{
        "vflat_model_version":speed_load.speed_model_versions("Run")[0],
        "vflat_config_version":speed_load.speed_model_versions("Run")[1],
        "speed_test_series":[{"elapsed_s":t,"dt_s":1,"vflat_b65_kmh":24.,"grade_smoothed_pct":0.} for t in range(1,11)]}}
    result=speed_load._activity_record(activity,row,None,None,params)["speed_equivalence_comparison"]
    assert result["recorded_minutes"] == pytest.approx(10/60)
    assert result["zones"][-1]["real_seconds"] == 10
    assert result["zones"][-1]["equivalent_seconds"] == pytest.approx(18.)
    assert result["coverage_percent"] == 100
    assert "VFLAT_21S_SMOOTHING_NOT_VALIDATED_FOR_10S_SPRINTS" in result["warnings"]
    # The same prescribed unsmoothed plan dose is scored exactly, no actual-stream warning.
    planned=speed_equivalence.integrate(params,[{"seconds":10,"speed_kmh":24}])
    assert planned["zones"][-1]["equivalent_seconds"] == pytest.approx(18.)


def test_comparison_parameter_change_invalidates_activity_cache_without_changing_legacy_map(monkeypatch):
    from apps.api.speed_load_cache import speed_activity_cache
    repo=SpeedRepository();repo.add("cached","Run",TODAY,150,19.)
    contexts={"Run":speed_equivalence.prepare(profile())}
    first=speed_load._activity_records(repo,"authorized-athlete",repo.activities,[],SETTINGS,"comparison-cache-test",comparison_by_sport=contexts)
    source=profile();source["zones"][3]["high_kmh"]=21.;source["zones"][4]["low_kmh"]=21.
    contexts={"Run":speed_equivalence.prepare(source)}
    second=speed_load._activity_records(repo,"authorized-athlete",repo.activities,[],SETTINGS,"comparison-cache-test",comparison_by_sport=contexts)
    assert first[0]["zones"] == second[0]["zones"]
    assert first[0]["speed_equivalence_comparison"]["parameters_version"] != second[0]["speed_equivalence_comparison"]["parameters_version"]
    assert first[0]["speed_equivalence_comparison"]["zones"][3]["equivalent_minutes"] != second[0]["speed_equivalence_comparison"]["zones"][3]["equivalent_minutes"]


def test_overlapping_samples_count_once_and_existing_ten_second_gap_limit_is_preserved():
    params=speed_equivalence.prepare(profile())
    activity={"activity_ref":"gaps","sport":"Run","local_date":"2026-10-01","duration_min":.75}
    row={"activity_ref":"gaps","shadow_payload":{
        "vflat_model_version":speed_load.speed_model_versions("Run")[0],
        "vflat_config_version":speed_load.speed_model_versions("Run")[1],
        "speed_test_series":[{"elapsed_s":t,"dt_s":dt,"vflat_b65_kmh":19.,"grade_smoothed_pct":0.}
                             for t,dt in [(10,10),(15,10),(45,30)]]}}
    result=speed_load._activity_record(activity,row,None,None,params)["speed_equivalence_comparison"]
    assert result["recorded_minutes"] == .75
    assert result["zones"][3]["real_seconds"] == 45  # Overlap [5,10] counted once.
    assert result["zones"][3]["covered_seconds"] == 15  # dt=10 allowed, dt=30 excluded.
    assert result["zones"][3]["equivalent_seconds"] == pytest.approx(13.125)
    assert result["excluded_minutes"] == .5
    assert "SAMPLE_GAP" in result["warnings"]
    gap=speed_load._comparison_segments([(0,10.000001,19.,None)],params)
    assert gap[0]["exclusion_reason"] == "SAMPLE_GAP"
