from copy import deepcopy

import pytest

from apps.api.component_load_context import context_from_speed_view, ZONES
from apps.api.component_load_projection import ComponentLoadRefreshRequired, project_history, project_snapshot
from biathlon.component_load import VERSION


def context():
    result = context_from_speed_view({"sport": "Run"})
    result["minutes"] = {zone: 100. for zone in ZONES}
    result["fingerprint"] = "uniform-100-test"
    return {"Run": result}


def source(work=(60.,), days=("2026-10-08", "2026-10-09")):
    return {"period_start": days[0], "period_end": days[-1],
        "daily": [{"date": day, "zone": z, "effective_load": sum(work) if day == days[-1] and z in {"Z1", "Z2"} else 0.,
                   "e7_daily": 999., "e40_daily": 999., "status_7_40": 9., "tref_used_min": 999.}
                  for day in days for z in ZONES],
        "activities": [{"activity_ref": str(i), "date": days[-1], "sport": "Run", "duration_min": q,
                        "zones": [{"zone": z, "equivalent_time_min": q if z == "Z2" else 0.,
                                   "raw_time_min": q if z == "Z2" else 0., "effective_load": q if z in {"Z1", "Z2"} else 0.} for z in ZONES]}
                       for i, q in enumerate(work)],
        "zones": [{"zone": z, "e7_daily": 999., "e40_daily": 999., "status_7_40": 9., "tref_min": 999.,
                   "history_reliability": .05} for z in ZONES],
        "strength": {"daily": [{"date": day, "effective_load": 10., "tref_used_min": 120.} for day in days],
                     "summary": {"effective_load": 20.}}}


def value(history, zone, day="2026-10-09"):
    return next(row["effective_load"] for row in history["daily"] if row["zone"] == zone and row["date"] == day)


def test_old_cascade_rebases_from_direct_q_and_preserves_source_and_strength():
    original = source()
    unchanged = deepcopy(original)
    result = project_history(original, context())
    assert original == unchanged
    assert value(result, "Z1") == 6
    assert value(result, "Z2") == 60
    assert value(result, "Z3") == 12
    assert value(result, "Z4") == 0
    assert result["strength"] == original["strength"]
    for old, new in zip(original["activities"][0]["zones"], result["activities"][0]["zones"]):
        assert new["equivalent_time_min"] == old["equivalent_time_min"]
        assert new["raw_time_min"] == old["raw_time_min"]
    z3 = next(row for row in result["zones"] if row["zone"] == "Z3")
    assert z3["e7_daily"] == 6
    assert result["component_load_model"]["version"] == VERSION
    assert result["component_load_model"]["scope"] == "ACTIVITY"


def test_two_short_sessions_do_not_trigger_a_fictitious_daily_continuous_threshold():
    result = project_history(source((30., 30.)), context())
    assert value(result, "Z2") == 60
    assert value(result, "Z3") == 0
    assert value(result, "Z1") == 0


@pytest.mark.parametrize("damage", ["missing_activity", "duplicate_activity", "missing_activity_e", "wrong_zone_ledger"])
def test_partial_activity_history_cannot_silently_replace_the_daily_ledger(damage):
    broken = source((30., 30.))
    if damage == "missing_activity":
        broken["activities"].pop()
    elif damage == "duplicate_activity":
        broken["activities"].append(deepcopy(broken["activities"][0]))
    elif damage == "missing_activity_e":
        broken["activities"][0]["zones"][0].pop("effective_load")
    else:
        # Matching overall totals cannot hide a mismatch in one component.
        for row in broken["daily"]:
            if row["date"] == "2026-10-09" and row["zone"] in {"Z1", "Z2"}:
                row["effective_load"] += 1 if row["zone"] == "Z1" else -1
    unchanged = deepcopy(broken)
    with pytest.raises(ComponentLoadRefreshRequired):
        project_history(broken, context())
    assert broken == unchanged


def test_ledger_is_validated_before_idempotent_projection_can_be_reused():
    projected = project_history(source(), context())
    next(row for row in projected["daily"] if row["date"] == "2026-10-09" and row["zone"] == "Z2")["effective_load"] += 1
    with pytest.raises(ComponentLoadRefreshRequired, match="daily ledger"):
        project_history(projected, context())


def test_tiny_float_aggregation_error_does_not_reject_complete_activity_history():
    complete = source((30., 30.))
    next(row for row in complete["daily"] if row["date"] == "2026-10-09" and row["zone"] == "Z2")["effective_load"] += 1e-8
    assert value(project_history(complete, context()), "Z2") == 60


def test_projection_is_idempotent_and_capacity_change_recomputes():
    first = project_history(source(), context())
    assert project_history(first, context()) is first
    updated = context()
    updated["Run"]["minutes"]["Z2"] = 50
    updated["Run"]["fingerprint"] = "z2-50"
    second = project_history(first, updated)
    assert value(second, "Z3") == 18
    assert second["component_load_model"]["fingerprint"] != first["component_load_model"]["fingerprint"]


def test_calendar_gaps_are_never_converted_into_rest_days():
    result = project_history(source(days=("2026-10-01", "2026-10-09")), context())
    assert {row["date"] for row in result["daily"]} == {"2026-10-01", "2026-10-09"}
    z3 = next(row for row in result["daily"] if row["date"] == "2026-10-09" and row["zone"] == "Z3")
    assert z3["e7_daily"] == 12  # Only one known day in the seven-day window.


@pytest.mark.parametrize("damage", ["missing_activities", "missing_q", "missing_day_zone"])
def test_unreconstructible_nonzero_legacy_load_explicitly_requires_refresh(damage):
    broken = source()
    if damage == "missing_activities": broken["activities"] = []
    elif damage == "missing_q": broken["activities"][0]["zones"][0].pop("equivalent_time_min")
    else: broken["daily"].pop()
    with pytest.raises(ComponentLoadRefreshRequired):
        project_history(broken, context())


def test_snapshot_status_uses_the_same_rebased_7_40_and_tref():
    snapshot = {"load_history": source(), "training_status": {"zones": [{"zone": "Z3", "status_7_40": 9., "tref_min": 999.}]}}
    result = project_snapshot(None, "athlete", snapshot, contexts_by_sport=context())
    summary = next(row for row in result["load_history"]["zones"] if row["zone"] == "Z3")
    assert result["training_status"]["zones"][0]["status_7_40"] == summary["status_7_40"]
    assert result["training_status"]["zones"][0]["tref_min"] == summary["tref_min"]


@pytest.mark.parametrize("snapshot", [None, {}, {"training_status": {"athlete_id": "athlete"}}])
def test_legacy_recovery_without_load_history_explicitly_requires_refresh(snapshot):
    from apps.api.component_load_projection import project_legacy_recovery
    with pytest.raises(ComponentLoadRefreshRequired, match="complete load history"):
        project_legacy_recovery(snapshot)
