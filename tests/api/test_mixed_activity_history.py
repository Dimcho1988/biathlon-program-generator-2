"""Mixed sports share Q provenance without weakening missing-data gates."""
from copy import deepcopy
from datetime import date, timedelta
from types import SimpleNamespace

import pytest

from apps.api import actual_load_projection, model_service, planning_history_estimate, recovery_load_projection
from biathlon import load_evidence, load_progression, planning_history, speed_equivalence, unified_load
from tests.api.test_load_progression import configured
from tests.api.test_recovery_indicator_fallback import Repository, TODAY, source
from tests.test_unified_zone_load import BOUNDS, streams


def add_recording(history, sport, pulse=150., unknown=0., *, strength=False):
    ledger = unified_load.integrate(streams(pulse, None, seconds=60), bounds=BOUNDS,
                                    sport=sport, duration_s=60+unknown)
    activity = {"activity_ref": sport, "date": (TODAY-timedelta(days=1)).isoformat(),
                "sport": sport, "duration_min": (60+unknown)/60, "hr_coverage_percent": 100.,
                "quality_status": "limited" if ledger["unknown_seconds"] else "valid",
                "load_source": {k:v for k,v in ledger.items() if k != "zones"},
                "zones": [{**z, "effective_load": z["equivalent_time_min"]} for z in ledger["zones"]]}
    if strength:
        activity.update(sport="WeightTraining", strength_time_min=1., load_source=None, quality_status="valid")
    history["activities"].append(activity)
    history["quality"]["limited_activities"] = sum(a["quality_status"] == "limited" for a in history["activities"])
    for row in history["daily"]:
        if row["date"] == activity["date"]:
            row["effective_load"] += next(z["effective_load"] for z in activity["zones"] if z["zone"] == row["zone"])
    return activity


@pytest.mark.parametrize("sport", ["Hike", "Swim", "StairStepper", "Workout"])
def test_hr_only_sports_share_recorded_history_with_speed_only_run(sport):
    history = source(0.)  # Run has no HR, but a complete speed record.
    activity = add_recording(history, sport)
    before = deepcopy(history)
    coverage = planning_history.recovery_coverage(history, TODAY)
    prepared, evidence = planning_history_estimate.prepare(history, history["activities"], configured(), TODAY)
    assert coverage["readiness_available"] and coverage["complete"]
    assert coverage["coverage_percent"] == 100 and evidence["supported"] and not evidence["estimated"]
    assert planning_history.recorded_zone_minutes(activity, unified=True, require_complete=True)
    rows = history["daily"] + [{**r,"zone":"STR"} for r in history["strength"]["daily"]]
    observed = load_progression.observed_window(history, rows, TODAY-timedelta(days=40), TODAY)
    assert all(observed["components"][z]["reference_supported"] for z in speed_equivalence.ZONES)
    assert prepared["activities"] == history["activities"] and history == before


@pytest.mark.parametrize("pulse,unknown", [(150.,60.), (None,0.)])
def test_hr_only_sport_known_unknown_duration_uses_same_estimate_in_recovery_and_plan(pulse, unknown):
    history = source(0.)
    add_recording(history, "Hike", pulse, unknown)
    before = deepcopy(history)
    repo = Repository(history)
    prepared, quality = recovery_load_projection.prepare(repo, "test", history, TODAY,
        planning_history.recovery_coverage(history, TODAY))
    planned, evidence = planning_history_estimate.prepare(history, repo.calendar, configured(), TODAY)
    assert quality["readiness_available"] and quality["readiness_basis"] == "ESTIMATED_LOAD"
    assert evidence["supported"] and evidence["estimated_minutes"] == 1.
    assert prepared["daily"] == planned["daily"] and prepared["activities"] == planned["activities"]
    assert history == before


@pytest.mark.parametrize("damage,reason", [
    ("version","Q_REANALYSIS_REQUIRED"), ("ledger","INVALID_RECORDING_LEDGER"),
    ("missing_ledger","Q_REANALYSIS_REQUIRED"), ("provider","PROVIDER_RECORD_MISSING"),
    ("missing_q","ACTIVITY_Q_INCOMPLETE"), ("malformed_ledger","INVALID_RECORDING_LEDGER"),
])
def test_hr_label_cannot_certify_incompatible_or_corrupt_recording(damage,reason):
    history = source(0.)
    activity = add_recording(history, "Hike")
    if damage == "version": activity["load_source"]["equivalence_version"] = "incompatible-old-q"
    elif damage == "ledger": activity["load_source"]["recorded_seconds"] += 10
    elif damage == "missing_ledger": activity.pop("load_source")
    elif damage == "missing_q": activity["zones"][0]["equivalent_time_min"] = None
    elif damage == "malformed_ledger": activity["load_source"] = "invalid"
    else: activity["quality_status"] = "provider_missing"
    coverage = planning_history.recovery_coverage(history, TODAY)
    _, evidence = planning_history_estimate.prepare(history, history["activities"], configured(), TODAY)
    assert not coverage["readiness_available"] and not evidence["supported"]
    assert coverage["issues"][-1]["reason"] == evidence["issues"][-1]["reason"] == reason
    if damage == "missing_q":
        # Observed component exposure does not depend on a derived Q value.
        assert planning_history.recorded_zone_minutes(activity, unified=True, require_complete=True)
    else:
        assert planning_history.recorded_zone_minutes(activity, unified=True, require_complete=True) is None


def test_old_q_is_rebuilt_from_pinned_hr_not_relabelled_or_double_counted(monkeypatch):
    history = source(0.)
    hike = add_recording(history, "Hike")
    hike.pop("load_source")
    for z in hike["zones"]: z["equivalent_time_min"] = 999.
    before = deepcopy(history)
    settings = SimpleNamespace(zone_bounds_bpm=BOUNDS, hrmax_bpm=200, leading_indicator="SPEED")
    pinned = streams(150., 100., seconds=60)  # Unsupported speed must not enter Q.
    repo = SimpleNamespace(athlete_settings=lambda _:settings,
        activity_speed_history_samples=lambda alias, keys:{"pinned": {"activity_ref":"Hike","shadow_payload":pinned}})
    inputs = {"alias":"test", "today":TODAY, "entries":[], "calendar":{"activities":[
        {"activity_ref":"Hike", "sport":"Hike", "local_date":hike["date"],
         "elapsed_time_s":60., "latest_shadow_run_key":"pinned"}]}}
    monkeypatch.setattr(model_service, "speed_view", lambda *a,**k: pytest.fail("No speed model for Hike"))
    rebuilt = actual_load_projection.reconstruct_history(repo,"test",history,speed_inputs=inputs)
    actual = next(a for a in rebuilt["activities"] if a["sport"] == "Hike")
    expected = unified_load.integrate(pinned, bounds=BOUNDS,sport="Hike",duration_s=60.,leading_indicator="SPEED")
    assert [z["equivalent_time_min"] for z in actual["zones"]] == [z["equivalent_time_min"] for z in expected["zones"]]
    assert actual["load_source"]["source_seconds"] == {"HEART_RATE":60.,"SPEED":0.}
    assert actual["load_source"]["equivalence_version"] == speed_equivalence.VERSION
    assert planning_history.recovery_coverage(rebuilt,TODAY)["complete"]
    assert actual_load_projection.reconstruct_history(repo,"test",rebuilt,speed_inputs=inputs) == rebuilt
    assert history == before


def test_dated_old_unreconstructible_q_does_not_block_recent_window():
    history = source(0.)
    history["period_start"] = (TODAY-timedelta(days=60)).isoformat()
    old = add_recording(history,"Hike")
    old.update(date=(TODAY-timedelta(days=45)).isoformat(),load_source=None)
    _, evidence = planning_history_estimate.prepare(history,history["activities"],configured(),TODAY,
        required_coverage_start=TODAY-timedelta(days=40))
    assert evidence["supported"] and evidence["older_unresolved_activity_count"] == 1
    assert planning_history.recovery_coverage(history,TODAY)["complete"]
    old.pop("date")
    _, evidence = planning_history_estimate.prepare(history,history["activities"],configured(),TODAY,
        required_coverage_start=TODAY-timedelta(days=40))
    assert not evidence["supported"] and evidence["issues"][0]["reason"] == "ACTIVITY_DATE_MISSING"


def test_pinned_samples_without_total_duration_do_not_certify_a_complete_recording(monkeypatch):
    history = source(0.)
    hike = add_recording(history, "Hike")
    hike.pop("load_source")
    hike["duration_min"] = None
    settings = SimpleNamespace(zone_bounds_bpm=BOUNDS, hrmax_bpm=200, leading_indicator="HEART_RATE")
    repo = SimpleNamespace(athlete_settings=lambda _:settings,
        activity_speed_history_samples=lambda alias, keys:{"pinned": {
            "activity_ref":"Hike", "shadow_payload":streams(150.,None,seconds=60)}})
    inputs = {"alias":"test", "today":TODAY, "entries":[], "calendar":{"activities":[
        {"activity_ref":"Hike", "sport":"Hike", "local_date":hike["date"], "latest_shadow_run_key":"pinned"}]}}
    rebuilt = actual_load_projection.reconstruct_history(repo,"test",history,speed_inputs=inputs)
    actual = next(a for a in rebuilt["activities"] if a["sport"] == "Hike")
    assert "load_source" not in actual
    assert not planning_history.recovery_coverage(rebuilt,TODAY)["complete"]
    _, evidence = planning_history_estimate.prepare(rebuilt,inputs["calendar"]["activities"],configured(),TODAY)
    assert not evidence["supported"]
    assert evidence["issues"][-1]["reason"] == "RECORDING_DURATION_MISSING"


def test_strength_uses_own_exposure_and_provider_gap_remains_unknown():
    history = source(0.)
    strength = add_recording(history,"WeightTraining",strength=True)
    assert load_evidence.assess(strength,unified=True)["basis"] == "STRENGTH"
    assert planning_history.recovery_coverage(history,TODAY)["complete"]
    _, evidence = planning_history_estimate.prepare(history,history["activities"],configured(),TODAY)
    assert evidence["supported"] and not evidence["estimated"]
    strength["quality_status"] = "provider_missing"
    assert not planning_history.recovery_coverage(history,TODAY)["complete"]
    _, evidence = planning_history_estimate.prepare(history,history["activities"],configured(),TODAY)
    assert not evidence["supported"]
