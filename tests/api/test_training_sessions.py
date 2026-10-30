"""Recorded intervals occupy occasions, without changing scientific history."""
from copy import deepcopy

import pytest

from apps.api.training_sessions import group_recordings, role_is_key, with_confirmed_methods


def activity(ref, minutes=10, day="2026-10-19", **extra):
    return {"activity_ref": ref, "date": day, "sport": "Run", "duration_min": minutes,
            "zones": [], **extra}


def catalog(a, start, elapsed=None):
    return {"activity_ref": a["activity_ref"], "local_date": a["date"],
            "start_local": f'{a["date"]}T{start}',
            "elapsed_time_s": a["duration_min"]*60 if elapsed is None else elapsed}


@pytest.mark.parametrize("next_start,expected", [("12:59:59", 1), ("13:00:00", 1), ("13:00:01", 2)])
def test_two_hour_boundary_is_from_end_not_start(next_start, expected):
    aa = [activity("first", 180), activity("second")]
    cc = [catalog(aa[0], "08:00:00"), catalog(aa[1], next_start)]
    assert len(group_recordings(aa, cc, "Europe/Sofia")) == expected


def test_seven_interval_records_group_once_and_preserve_all_inputs():
    starts = ["11:02:00", "11:44:00", "11:49:00", "11:55:00", "12:00:00", "12:06:00", "12:11:00"]
    minutes = [10+56/60, 4, 31/60, 3+58/60, .5, 3.9, 28/60]
    aa = [activity(str(i), m) for i, m in enumerate(minutes)]
    cc = [catalog(a, start) for a, start in zip(aa, starts)]
    before = deepcopy((aa, cc))
    groups = group_recordings(aa[::-1], cc[::-1], "Europe/Sofia")
    assert len(groups) == 1
    assert groups[0].activities == tuple(aa)
    assert (aa, cc) == before


def test_two_occasions_and_combined_sports_remain_distinct_after_long_gap():
    aa = [activity("warmup"), activity("strength", sport="WeightTraining"), activity("afternoon")]
    cc = [catalog(a, s) for a, s in zip(aa, ["08:00:00", "08:20:00", "16:00:00"])]
    groups = group_recordings(aa, cc, "Europe/Sofia")
    assert [len(g.activities) for g in groups] == [2, 1]


def test_elapsed_time_including_pauses_defines_end():
    aa = [activity("first"), activity("second")]
    cc = [catalog(aa[0], "08:00:00", 3600), catalog(aa[1], "11:00:00")]
    assert len(group_recordings(aa, cc, "Europe/Sofia")) == 1


@pytest.mark.parametrize("invalid", [None, "2026-10-19", "invalid"])
def test_missing_or_invalid_clock_never_invents_a_shared_session(invalid):
    aa = [activity("a"), activity("b")]
    cc = [catalog(a, "08:00:00") for a in aa]
    cc[1]["start_local"] = invalid
    assert len(group_recordings(aa, cc, "Europe/Sofia")) == 2


def test_duplicate_metadata_and_mismatched_dates_do_not_group():
    aa = [activity("a"), activity("b")]
    cc = [catalog(a, "08:00:00") for a in aa]
    assert len(group_recordings(aa, cc + [cc[1]], "Europe/Sofia")) == 2
    cc[1]["local_date"] = "2026-10-18"
    assert len(group_recordings(aa, cc, "Europe/Sofia")) == 2


def test_calendar_day_boundary_is_preserved():
    aa = [activity("a"), activity("b", day="2026-10-20")]
    cc = [catalog(aa[0], "23:45:00"), catalog(aa[1], "00:10:00")]
    assert len(group_recordings(aa, cc, "Europe/Sofia")) == 2


def test_key_session_uses_combined_zone_exposure_without_changing_loads():
    aa = [activity(str(i), zones=[{"zone": "Z4", "raw_time_min": 3., "effective_load": i+1}]) for i in range(3)]
    cc = [catalog(a, f"08:{i*15:02}:00") for i, a in enumerate(aa)]
    before = deepcopy(aa)
    groups = group_recordings(aa, cc, "Europe/Sofia")
    assert len(groups) == 1 and groups[0].is_key
    assert aa == before


def test_utc_metadata_avoids_ambiguous_local_clock():
    aa = [activity("a", day="2026-10-25"), activity("b", day="2026-10-25")]
    cc = [catalog(a, "03:15:00") for a in aa]
    assert len(group_recordings(aa, cc, "Europe/Sofia")) == 2
    cc[0]["start_at_utc"] = "2026-10-25T00:15:00Z"
    cc[1]["start_at_utc"] = "2026-10-25T01:15:00+00:00"
    assert len(group_recordings(aa, cc, "Europe/Sofia")) == 1


def test_overlapping_and_long_chains_use_latest_end():
    aa = [activity("long", 180), activity("overlap", 10), activity("later", 120)]
    cc = [catalog(a, s) for a, s in zip(aa, ["08:00:00", "08:30:00", "12:50:00"])]
    assert len(group_recordings(aa, cc, "Europe/Sofia")) == 1


@pytest.mark.parametrize("role", ["BUILDING", "MAINTENANCE", "SUPPORTING", "RECOVERY", "ACTIVATION"])
@pytest.mark.parametrize("zone", ["Z2", "Z3", "Z4", "Z5"])
def test_same_confirmed_dose_retains_its_planned_key_classification(role, zone):
    a = activity("confirmed", 30., zones=[{"zone":zone, "raw_time_min":20.}])
    sessions = group_recordings([a], [catalog(a, "08:00:00")], "Europe/Sofia")
    observations = [{"id":"confirmed", "success":True, "planned_minutes":30., "actual_minutes":30.,
                     "method":{"zone":zone, "resolved_role":role}}]
    actual = with_confirmed_methods(sessions, observations)[0]
    assert actual.is_key == role_is_key(zone, role)
    assert actual.key_evidence["basis"] == "CONFIRMED_METHOD_ROLE"
    assert sessions[0].confirmed_methods == {}  # Scientific history stays immutable.


@pytest.mark.parametrize("changes", [{"success":False}, {"actual_minutes":45.}, {"speed_ratio":1.2},
    {"method":{"zone":"Z4", "purpose":"MAINTENANCE"}}])
def test_unknown_or_exceeded_dose_cannot_claim_a_reduced_role(changes):
    a = activity("uncertain", 30., zones=[{"zone":"Z4", "raw_time_min":12.}])
    observation = {"id":"uncertain", "success":True, "planned_minutes":30., "actual_minutes":30.,
                   "method":{"zone":"Z4", "resolved_role":"MAINTENANCE"}, **changes}
    actual = with_confirmed_methods(group_recordings([a], [], "UTC"), [observation])[0]
    assert actual.is_key
    assert actual.key_evidence["basis"] == "UNLABELLED_EXPOSURE_FALLBACK"


def test_confirmed_easy_part_does_not_hide_unlabelled_intense_intervals():
    aa = [activity("easy", 20., zones=[{"zone":"Z3", "raw_time_min":10.}]),
          activity("intervals", 10., zones=[{"zone":"Z5", "raw_time_min":6.}])]
    cc = [catalog(a, s) for a,s in zip(aa, ["08:00:00", "08:30:00"])]
    groups = with_confirmed_methods(group_recordings(aa, cc, "UTC"), [{"id":"easy", "success":True,
        "planned_minutes":20., "actual_minutes":20., "method":{"zone":"Z3", "resolved_role":"SUPPORTING"}}])
    assert len(groups) == 1 and groups[0].is_key
    assert groups[0].key_evidence["basis"] == "MIXED_CONFIRMED_AND_UNLABELLED"
