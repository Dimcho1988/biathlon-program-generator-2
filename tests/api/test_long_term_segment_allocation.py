"""Short drafts cannot borrow coverage from another long-term microcycle."""
from datetime import date, timedelta

import pytest

from biathlon import planning_allocation as policy
from biathlon.constants import COMPONENTS


START = date(2026, 10, 8)


def targets():
    windows = {
        START + timedelta(days=i): {
            z: {"target": 420. if i < 5 else 140.,
                "target_weekly_q": 140. if i < 5 else 70.}
            for z in COMPONENTS}
        for i in range(7)}
    keys = {day: "BUILD" if i < 5 else "RECOVERY"
            for i, day in enumerate(windows)}
    return windows, keys


def rows(day, load):
    return [{"date": day.isoformat(), "zone": z, "effective_load": load}
            for z in COMPONENTS]


def activity(day, q):
    return {"date": day.isoformat(),
            "zones": [{"zone": z, "equivalent_time_min": q} for z in COMPONENTS]}


def proposed(day, q):
    return {"date": day.isoformat(), "status": "TRAINING",
            "sessions": [{"direct_equivalent_minutes": {z: q for z in COMPONENTS},
                          "canonical_effective_load": {z: q for z in COMPONENTS},
                          "total_minutes": q, "dose_evidence": {}}],
            "rejected_alternatives": []}


def test_partial_long_term_segments_keep_exact_daily_q_and_e_sums():
    windows, keys = targets()
    outside = START - timedelta(days=1)
    actual = rows(outside, 300.) + rows(START, 50.)
    source = {"activities": [activity(outside, 300.), activity(START, 10.)]}
    days = [proposed(START + timedelta(days=4), 80.)]
    forecast = actual + rows(START + timedelta(days=4), 120.)

    build = policy.segment_headroom(windows, actual, forecast, source, days, START,
                                    segment_keys=keys)
    recovery = policy.segment_headroom(windows, actual, forecast, source, days,
                                       START + timedelta(days=5), segment_keys=keys)
    assert build["days"] == 5 and recovery["days"] == 2
    assert build["window_end"] == "2026-10-12"
    assert recovery["window_start"] == "2026-10-13"
    z1 = build["components"]["Z1"]
    assert z1["target_q"] == 100.  # 5 * 140 / 7
    assert z1["actual_q"] == 10. and z1["planned_q"] == 80.
    assert z1["remaining"] == 10.
    assert z1["target_effective"] == 300.  # independent 5 * 420 / 7
    assert z1["remaining_effective"] == 130.
    assert not z1["planned_exceeds_target"]
    assert recovery["components"]["Z1"]["remaining"] == 20.
    assert recovery["components"]["Z1"]["remaining_effective"] == 40.
    # Strength has its own exposure units; aerobic Q values do not set its dose.
    assert build["components"]["STR"]["target"] == 300.
    assert build["components"]["STR"]["actual"] == 50.


def test_combined_period_headroom_does_not_authorize_cross_segment_borrowing():
    windows, keys = targets()
    days = [proposed(START + timedelta(days=4), 105.)]
    combined = policy.objectives(windows, [], [], {"activities": []}, days)
    assert combined["Z1"]["remaining"] == 15.
    build = policy.segment_headroom(windows, [], [], {"activities": []}, days,
                                    START + timedelta(days=4), segment_keys=keys)
    z1 = build["components"]["Z1"]
    assert z1["target"] == 100. and z1["planned"] == 105.
    assert z1["remaining"] == 0.
    assert z1["planned_exceeds_target"]
    recovery = policy.segment_headroom(windows, [], [], {"activities": []}, days,
                                       START + timedelta(days=6), segment_keys=keys)
    assert recovery["components"]["Z1"]["remaining"] == 20.


def test_actual_overshoot_is_visible_and_does_not_create_a_negative_dose():
    windows, keys = targets()
    actual = rows(START, 320.)
    source = {"activities": [activity(START, 110.)]}
    active = policy.segment_headroom(windows, actual, actual, source, [], START,
                                     segment_keys=keys)["components"]["Z1"]
    assert active["remaining"] == 0. and active["remaining_effective"] == 0.
    assert active["actual_exceeds_target"]
    assert active["actual_effective_exceeds_target"]
    assert not active["planned_exceeds_target"]


def test_unknown_actual_q_cannot_be_authorized_by_remaining_e_headroom():
    windows, keys = targets()
    actual = rows(START, 20.)
    source = {"activities": [{"date": START.isoformat(), "zones": []}]}
    z1 = policy.segment_headroom(windows, actual, actual, source, [], START,
                                 segment_keys=keys)["components"]["Z1"]
    assert z1["basis"] == "DIRECT_Q" and z1["remaining"] is None
    assert z1["actual_q"] is None
    assert z1["remaining_effective"] == 280.


def test_legacy_manual_e_uses_the_same_segment_ceiling_without_fabricating_q():
    windows, keys = targets()
    for goal in windows.values():
        goal["Z1"]["target_weekly_q"] = None
    actual = rows(START, 50.)
    forecast = actual + rows(START + timedelta(days=1), 100.)
    z1 = policy.segment_headroom(windows, actual, forecast, {"activities": []}, [],
                                 START, segment_keys=keys)["components"]["Z1"]
    assert z1["basis"] == "CANONICAL_E" and z1["target_q"] is None
    assert z1["target"] == 300. and z1["remaining"] == 150.
    assert z1["remaining_effective"] == z1["remaining"]


def test_identity_follows_outlook_including_a_wave_change_inside_one_segment():
    windows = {START + timedelta(days=i): {
        z: {"target": 700., "target_weekly_q": 140. if i < 2 else 70.}
        for z in COMPONENTS} for i in range(4)}
    # The engine controls the authoritative outlook boundary, not the value
    # changing within that boundary (such as a phase change).
    keys = {day: "ONE_OUTLOOK_SEGMENT" for day in windows}
    segment = policy.segment_headroom(windows, [], [], {}, [], START + timedelta(days=3),
                                      segment_keys=keys)
    assert segment["days"] == 4
    assert segment["components"]["Z1"]["target_q"] == 60.
    assert segment["components"]["Z1"]["target_effective"] == 400.


def test_report_keeps_global_totals_but_exposes_unfilled_recovery_segment():
    windows, keys = targets()
    days = [proposed(START + timedelta(days=4), 120.)]
    report = policy.report(windows, [], rows(START + timedelta(days=4), 120.), days,
                           7, 7, 0., source={}, segment_keys=keys)
    assert report["version"] == policy.SEGMENT_VERSION
    assert report["status"] == "LONG_TERM_SEGMENT_OBJECTIVES_WITH_PHYSIOLOGICAL_GATES"
    assert report["components"]["Z1"]["remaining"] == 0.
    assert report["has_unallocated_load"]
    assert report["segments"][0]["components"]["Z1"]["planned_exceeds_target"] is True
    assert report["segments"][1]["components"]["Z1"]["remaining"] == 20.


def test_repeated_identity_after_a_gap_does_not_merge_allocation_windows():
    windows, _ = targets()
    windows = {START: windows[START], START + timedelta(days=2): windows[START + timedelta(days=2)]}
    keys = {day: "SAME" for day in windows}
    segments = policy.segment_objectives(windows, [], [], {}, [], segment_keys=keys)
    assert len(segments) == 2 and all(segment["days"] == 1 for segment in segments)
    with pytest.raises(ValueError, match="outside"):
        policy.segment_headroom(windows, [], [], {}, [], START + timedelta(days=1),
                                segment_keys=keys)
