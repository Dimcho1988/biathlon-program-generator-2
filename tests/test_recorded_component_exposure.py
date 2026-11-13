from copy import deepcopy

import pytest

from biathlon.planning_history import component_exposure_dates, recorded_zone_minutes


def activity(*, source="SPEED", status="limited", unknown=30.):
    return {"date":"2026-10-27", "sport":"Run", "quality_status":status,
        "hr_coverage_percent":0. if source == "SPEED" else 100.,
        "zones":[{"zone":z, "raw_time_min":v} for z,v in
                 zip(("Z1","Z2","Z3","Z4","Z5"),(10.,5.,3.,8.,2.))],
        "load_source":{"recorded_seconds":1800.+unknown, "active_seconds":1680.,
                       "rest_seconds":120., "unknown_seconds":unknown,
                       "source_seconds":{source:1800.}}}


def test_hr_and_speed_give_the_same_component_exposure_without_promoting_unknown_time():
    hr, speed = activity(source="HEART_RATE", status="valid"), activity()
    def dates(a):
        return component_exposure_dates({"load_selection":{}, "activities":[a]}, "2026-10-01", "2026-10-29")
    assert dates(hr) == dates(speed) == {( "Run",z):{"2026-10-27"} for z in ("Z1","Z2","Z3","Z4","Z5")}
    assert recorded_zone_minutes(speed)["Z4"] == 8.
    assert recorded_zone_minutes(speed, require_complete=True) is None
    assert recorded_zone_minutes(activity(unknown=0.), require_complete=True)["Z4"] == 8.


@pytest.mark.parametrize("field,value", [("recorded_seconds",1.), ("active_seconds",0.), ("unknown_seconds",-1.)])
def test_invalid_ledger_cannot_fall_back_to_a_valid_hr_label(field,value):
    a=activity(status="valid")
    a["load_source"][field]=value
    assert recorded_zone_minutes(a) is None


def test_duration_estimates_do_not_create_component_exposure():
    a=activity()
    before=deepcopy(a)
    a["planning_estimated"]=True
    for row in a["zones"]:
        row["planning_measured_time_min"]=row["raw_time_min"]
        row["raw_time_min"]+=10.
    assert recorded_zone_minutes(a) == recorded_zone_minutes(before)
    a["zones"][-1].pop("planning_measured_time_min")
    assert recorded_zone_minutes(a) is None
    assert recorded_zone_minutes({"planning_estimated":True,"quality_status":"valid","zones":[]}) is None


def test_known_exposure_survives_partial_recording_but_missing_records_do_not():
    assert recorded_zone_minutes(activity(unknown=3600.))["Z4"] == 8.
    assert recorded_zone_minutes(activity(status="provider_missing")) is None
    assert recorded_zone_minutes(activity(status="excluded"))["Z4"] == 8.


def test_exposure_is_dated_and_separate_for_each_sport_and_strength():
    run=activity()
    ski={**deepcopy(run),"sport":"NordicSki","date":"2026-10-26"}
    strength={"sport":"WeightTraining","date":"2026-10-28","quality_status":"valid"}
    result=component_exposure_dates({"load_selection":{"version":"unified"},"activities":[run,ski,strength]}, "2026-10-27","2026-10-28")
    assert ("NordicSki","Z4") not in result
    assert result[("Run","Z4")] == {"2026-10-27"}
    assert result[("WeightTraining","STR")] == {"2026-10-28"}
