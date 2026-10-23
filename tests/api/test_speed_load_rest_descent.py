from copy import deepcopy

import pytest

from apps.api import speed_load, speed_segments
from apps.api.activity_shadow_pipeline import activity_shadow_configuration_fingerprint
from apps.api.shadow_storage import encode_shadow_payload, decode_shadow_payload
from vflat_b65.load import complete_load_speed
from vflat_b65.sports import speed_model_versions
from tests.api.test_speed_load import Repository, SETTINGS, TODAY
from tests.test_speed_load_samples import sample


def payload(source, sport="NordicSki", factor=1.):
    completed, summary = complete_load_speed(source, sport=sport, terrain_factor=factor)
    elapsed = 0.
    for row in completed:
        elapsed += row["dt_s"]
        row.update(elapsed_s=elapsed, grade_smoothed_pct=row["grade_raw_pct"])
    model, config = speed_model_versions(sport)
    return {"speed_test_series":completed,"speed_load_summary":summary,
            "vflat_model_version":model,"vflat_config_version":config}


def test_load_and_test_selection_are_separate_and_rest_survives_without_a_mapping():
    shadow = payload([sample(grade=-5.,vflat=4.),sample(raw=0.,vflat=None,grade=None,exclusion="VFLAT_SAMPLE_EXCLUDED")], "Run")
    original = deepcopy(shadow)
    strict = speed_segments.sample_intervals(shadow)
    load = speed_segments.sample_intervals(shadow,for_load=True,sport="Run")
    assert strict[0][3] == "DOWNHILL"
    assert [r[3] for r in load] == ["REST","REST"]
    result = speed_load._activity_record({"activity_ref":"run","sport":"Run","local_date":TODAY.isoformat(),"duration_min":2/60},
        {"activity_ref":"run","shadow_payload":shadow},None,"NO_PRIOR_SPORT_INDEX")
    assert result["rest_minutes"] == pytest.approx(2/60)
    assert result["unaccounted_minutes"] == 0
    assert result["classified_minutes"] == 0
    assert all(z["equivalent_minutes"] == 0 for z in result["zones"])
    assert shadow == original


def test_ski_balanced_descent_enters_q_but_never_speed_tests_and_roundtrips_storage():
    shadow = payload([sample(raw=16.,vflat=17.) for _ in range(180)] + [sample(raw=16.,grade=-5.) for _ in range(60)])
    restored = decode_shadow_payload(encode_shadow_payload(shadow))
    assert restored == shadow
    mapping, _ = speed_load.zone_mapping({"GENERAL":{"index":5.,"count":1,"seconds":600}}, SETTINGS.zone_bounds_bpm,200,"NordicSki")
    record = speed_load._activity_record({"activity_ref":"ski","sport":"NordicSki","local_date":TODAY.isoformat(),"duration_min":5},
        {"activity_ref":"ski","shadow_payload":restored},mapping,None)
    assert record["classified_minutes"] == pytest.approx(4.)
    assert record["estimated_descent_minutes"] == 1.
    assert record["unaccounted_minutes"] == pytest.approx(1.)  # Declared but unrecorded time is not rest.
    assert record["zones"][1]["minutes"] == pytest.approx(1.)  # Residual 13 km/h -> Z2.
    assert record["zones"][1]["equivalent_minutes"] == pytest.approx(.7)
    test = speed_segments.measure(speed_segments.sample_intervals(restored),0,240)
    assert test["excluded_seconds"]["downhill"] == 60
    assert test["measured_duration_s"] == 180


def test_load_sample_version_invalidates_session_runs_but_not_calibration_history(monkeypatch):
    from apps.api import activity_shadow_pipeline as pipeline
    args = dict(zone_bounds_bpm=SETTINGS.zone_bounds_bpm,explicit_hrmax_bpm=200,sport="NordicSki")
    calibration = activity_shadow_configuration_fingerprint(**args)
    session = activity_shadow_configuration_fingerprint(**args,activity_detail={"type":"NordicSki"})
    monkeypatch.setattr(pipeline,"SPEED_LOAD_SAMPLES_VERSION","future-load-version")
    assert activity_shadow_configuration_fingerprint(**args) == calibration
    assert activity_shadow_configuration_fingerprint(**args,activity_detail={"type":"NordicSki"}) != session


def test_running_downhill_flows_to_q_e_and_7_40_without_mutating_hr_observations(monkeypatch):
    from datetime import timedelta
    from apps.api.component_load_context import context_from_speed_view
    monkeypatch.setattr(speed_load,"read_contexts",lambda repository,alias,sports:
        {sport:context_from_speed_view({"status":"UNAVAILABLE","sport":sport}) for sport in sports})
    repo = Repository()
    repo.add("prior","Run",TODAY-timedelta(days=1),150,15)
    repo.add("now","Run",TODAY,150,15)
    repo.shadows["now"]["shadow_payload"] = payload([sample(raw=15.,vflat=15.,grade=-5.) for _ in range(540)]
        + [sample(raw=0.,vflat=None,grade=None,exclusion="VFLAT_SAMPLE_EXCLUDED") for _ in range(60)],"Run")
    original = deepcopy(repo.__dict__)
    view = speed_load.history_view(repo,"authorized-athlete",today=TODAY,period_start=TODAY,period_end=TODAY)
    assert view["classified_minutes"] == pytest.approx(9.)
    assert view["rest_minutes"] == pytest.approx(1.)
    assert view["accounted_percent"] == pytest.approx(100.)
    assert view["status"] == "AVAILABLE"
    assert view["zones"][2]["equivalent_minutes"] == pytest.approx(6.3)
    assert view["zones"][2]["effective_load"] > 0
    assert view["zones"][2]["ratio_7_40"] is not None
    assert repo.__dict__ == original
