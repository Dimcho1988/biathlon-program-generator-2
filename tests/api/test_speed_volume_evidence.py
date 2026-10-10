"""Volume used to locate expert time anchors must retain its measurement basis."""
from copy import deepcopy
from datetime import date, timedelta

import pytest

from apps.api.model_service import _speed_volume_evidence

TODAY = date(2026, 9, 29)
ZONES = [f"Z{i}" for i in range(1, 6)]


def activity(ref="a", days=-1, **patch):
    return {"activity_ref": ref, "date": (TODAY+timedelta(days=days)).isoformat(), "sport": "Run",
            "duration_min": 40., "quality_status": "valid", "hr_coverage_percent": 100.,
            "zones": [{"zone": z, "equivalent_time_min": 10. if z=="Z1" else 0.} for z in ZONES], **patch}


def source(*rows, days=40, **quality):
    return {"period_start": (TODAY-timedelta(days=days)).isoformat(), "period_end": TODAY.isoformat(),
            "activities": list(rows), "quality": {"limited_activities": 0, "excluded_activities": 0, **quality}}


def catalog(ref="a", days=-1, **patch):
    return {"activity_ref": ref, "local_date": (TODAY+timedelta(days=days)).isoformat(),
            "sport": "Run", "quality_status": "valid", "moving_time_s": 2400., "elapsed_time_s": 7200., **patch}


def test_complete_zone_rows_preserve_measured_zero_and_do_not_overwrite_canonical_duration():
    history=source(activity())
    rows=[catalog(moving_time_s=3000., elapsed_time_s=9000.)]
    original=deepcopy((history,rows))
    result=_speed_volume_evidence(history,rows,TODAY)
    assert result["basis"]=="HR_MEASURED"
    assert result["zone_weekly_q"]=={"Z1":1.75,"Z2":0.,"Z3":0.,"Z4":0.,"Z5":0.}
    assert result["total_weekly_minutes"]==7.
    assert result["duration_sources"]=={"a":"CANONICAL_DURATION"}
    assert (history,rows)==original


@pytest.mark.parametrize("invalid", [None, -1., float("nan"), True])
def test_invalid_zone_values_are_unknown_even_when_global_quality_says_valid(invalid):
    row=activity()
    row["zones"][1]["equivalent_time_min"]=invalid
    result=_speed_volume_evidence(source(row),[catalog()],TODAY)
    assert result["basis"]=="HR_PARTIAL"
    assert result["zone_weekly_q"]["Z2"] is None
    assert result["zone_weekly_q"]["Z1"]==1.75
    assert result["total_weekly_minutes"]==7.


def test_missing_and_duplicate_zone_records_do_not_become_zero_exposure():
    row=activity()
    row["zones"]=[r for r in row["zones"] if r["zone"]!="Z3"]
    row["zones"].append(dict(row["zones"][1]))
    result=_speed_volume_evidence(source(row),[],TODAY)
    assert result["basis"]=="HR_PARTIAL"
    assert result["zone_weekly_q"]["Z2"] is None
    assert result["zone_weekly_q"]["Z3"] is None


def test_catalog_hr_free_activity_adds_actual_time_without_fabricating_zones():
    rows=[catalog(),catalog("no-hr",days=-2,quality_status="excluded",moving_time_s=3600)]
    result=_speed_volume_evidence(source(activity(),excluded_activities=1),rows,TODAY)
    assert result["basis"]=="HR_PARTIAL"
    assert result["total_weekly_minutes"]==17.5
    assert result["zone_weekly_q"]["Z1"]==1.75
    assert all(result["zone_weekly_q"][z] is None for z in ZONES[1:])
    assert result["duration_sources"]["no-hr"]=="CATALOG_MOVING_TIME"
    assert result["unexplained_quality_counts"]["excluded"]==0


def test_entirely_hr_free_history_uses_recorded_duration_and_no_zero_zone_vector():
    rows=[catalog("no-hr",quality_status="excluded",canonical_summary=None,moving_time_s=None,
                  recording_time_s=2700)]
    result=_speed_volume_evidence(source(excluded_activities=1),rows,TODAY)
    assert result["basis"]=="TOTAL_DURATION"
    assert result["total_weekly_minutes"]==pytest.approx(45*7/40)
    assert all(v is None for v in result["zone_weekly_q"].values())
    assert result["duration_sources"]["no-hr"]=="CATALOG_RECORDING_TIME"


def test_missing_snapshot_duration_can_be_recovered_from_same_catalog_identity_once():
    row=activity(duration_min=None)
    result=_speed_volume_evidence(source(row),[catalog(canonical_summary={"duration_min":35.})],TODAY)
    assert result["total_weekly_minutes"]==pytest.approx(35*7/40)
    assert result["missing_duration_refs"]==[]
    assert result["duration_sources"]=={"a":"CATALOG_CANONICAL_DURATION"}


def test_old_and_today_quality_flags_do_not_contaminate_completed_40_day_window():
    history=source(activity(),activity("old",days=-45,quality_status="limited"),days=50,
                   limited_activities=1,excluded_activities=1)
    rows=[catalog(),catalog("old",days=-45,quality_status="limited"),
          catalog("today",days=0,quality_status="excluded")]
    result=_speed_volume_evidence(history,rows,TODAY)
    assert result["basis"]=="HR_MEASURED"
    assert result["total_weekly_minutes"]==7.
    assert result["unexplained_quality_counts"]=={"limited":0,"excluded":0}


def test_unlocated_excluded_activity_does_not_authorize_an_incomplete_total():
    result=_speed_volume_evidence(source(activity(),excluded_activities=1),[catalog()],TODAY)
    assert result["basis"]=="HR_PARTIAL"
    assert result["total_weekly_minutes"] is None
    assert result["unexplained_quality_counts"]["excluded"]==1


def test_strength_is_not_counted_as_aerobic_time_or_missing_aerobic_zones():
    history=source(activity(),activity("strength",sport="WeightTraining",duration_min=90,zones=[]))
    result=_speed_volume_evidence(history,[catalog(),catalog("strength",sport="WeightTraining",moving_time_s=5400)],TODAY)
    assert result["basis"]=="HR_MEASURED"
    assert result["total_weekly_minutes"]==7.
    assert result["duration_sources"]=={"a":"CANONICAL_DURATION"}


def test_missing_days_are_not_created_from_an_unbounded_catalog():
    result=_speed_volume_evidence({},[catalog()],TODAY)
    assert result["basis"]=="UNAVAILABLE"
    assert result["total_weekly_minutes"] is None
    assert all(v is None for v in result["zone_weekly_q"].values())


def test_elapsed_only_duration_remains_explicit_and_never_overwrites_known_canonical_time():
    result=_speed_volume_evidence(source(activity(duration_min=None)),
                                  [catalog(moving_time_s=None,recording_time_s=None,elapsed_time_s=3600)],TODAY)
    assert result["total_weekly_minutes"]==10.5
    assert result["duration_sources"]=={"a":"CATALOG_ELAPSED_TIME"}
    assert result["warnings"]==["ELAPSED_ONLY_DURATION_ESTIMATE"]


def test_existing_valid_hr_admission_is_respected_and_zero_coverage_is_partial():
    result=_speed_volume_evidence(source(activity(hr_coverage_percent=99.2)),[catalog()],TODAY)
    assert result["basis"]=="HR_MEASURED"
    result=_speed_volume_evidence(source(activity(hr_coverage_percent=0)),[catalog()],TODAY)
    assert result["basis"]=="HR_PARTIAL"


def test_no_activity_identity_does_not_double_count_the_same_possible_workout():
    row=activity()
    row.pop("activity_ref")
    result=_speed_volume_evidence(source(row),[catalog()],TODAY)
    assert result["total_weekly_minutes"] is None
