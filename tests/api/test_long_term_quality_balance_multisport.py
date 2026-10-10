"""Quality reservations retain the selected sport's method and capacity."""
from copy import deepcopy
from datetime import date

import pytest

from apps.api import training_plan_engine as engine
from biathlon import hr_speed, speed_duration
from biathlon.constants import COMPONENTS
from biathlon.sport_heart_rate import local_settings, reference_offset
from tests.api.test_long_term_quality_balance import balanced_fixture
from tests.api.test_long_term_scheduling import assert_segment_contract
from tests.api.test_readiness_adaptive_plan_v2 import assert_rolling_budgets


def sport_curve(repo, sport, today):
    """Two measured anchors and recent HR indices, independently per means."""
    multiplier = 20. / 22. if sport == "NordicSki" else 1.
    tests = [{"duration_s": duration, "speed_kmh": speed * multiplier,
              "maximal": True, "test_mode": "STRICT"}
             for duration, speed in ((600., 22.), (7200., 15.))]
    curve = speed_duration.calibrated(tests)
    durations = ((18000., 10800., 3600., 1800.) if sport == "NordicSki"
                 else (12600., 9000., 2700., 1200.))
    settings = local_settings(repo.settings, sport)
    offset = reference_offset(sport)
    indices = {f"Z{i + 1}": {
        "index": (100 * (settings.zone_bounds_bpm[i + 1] + offset) / (settings.hrmax_bpm + offset))
                 / (curve.speed(duration) * 3.6), "count": 3}
        for i, duration in enumerate(durations)}
    predictor = hr_speed.Predictor(curve, settings.zone_bounds_bpm, settings.hrmax_bpm,
                                   indices, normalization_offset_bpm=offset)
    return {"status": "CALIBRATED", "sport": sport,
            "model_version": speed_duration.VERSION,
            "hr_model": predictor.summary(),
            "source_generation_id": repo.envelope["generation_id"],
            "source_revision": repo.envelope["revision"],
            "active_test_keys": ["short", "long"],
            "tests": [{"entry_key": key, "payload": test}
                      for key, test in zip(("short", "long"), tests)],
            "index_window": {"last_activity_date": today.isoformat()},
            "index_summary": indices, "zone_corrections": {}}


def test_quality_balance_keeps_first_sport_method_and_curve_when_catalog_ids_repeat(monkeypatch):
    repo, body, start, now = balanced_fixture(monkeypatch)
    source = repo.envelope["snapshot_payload"]["load_history"]
    for activities in (source["activities"], repo.envelope["activities"]):
        activities.extend({**deepcopy(activity), "sport": "NordicSki",
                           "activity_ref": "ski-" + activity["activity_ref"]}
                          for activity in list(activities))
    body.update(sport="NordicSki", actual_sport="NordicSki")
    # Each shared catalogue ID is considered for skiing first, then running.
    # The later Run candidate must not overwrite the selected ski closure.
    body["planning_controls"].update(training_sports=["NordicSki", "Run"],
                                     capacity_policy="MODEL_WITH_PRIOR")
    speeds = {sport: sport_curve(repo, sport, now.date())
              for sport in body["planning_controls"]["training_sports"]}
    monkeypatch.setattr(engine.model_service, "speed_view",
                        lambda repository, alias, sport: deepcopy(speeds[sport]))

    builds, dose_checks = [], []
    original_blocks = engine._blocks
    original_usage = engine._dose_usage

    def observed_blocks(method, work, evidence, settings):
        parts = original_blocks(method, work, evidence, settings)
        # Observe genuine construction, including balancing's minimum and
        # bisection trials; do not alter either the capacity or returned dose.
        builds.append((deepcopy(method), evidence["capacity_minutes"],
                       evidence.get("target_speed_kmh"), work, parts))
        return parts

    monkeypatch.setattr(engine, "_blocks", observed_blocks)

    def observed_usage(parts, evidence, zone):
        dose_checks.append((parts, evidence["capacity_minutes"], zone))
        return original_usage(parts, evidence, zone)

    monkeypatch.setattr(engine, "_dose_usage", observed_usage)
    plan = engine.generate_plan(repo, "athlete", body, start_date=start, now=now)
    selected = [session for day in plan["days"] for session in day["sessions"]
                if session["sport"] == "NordicSki" and session["zone"] == "Z3"
                and any(limit["code"] == "LONG_TERM_QUALITY_PREPARATION"
                        for limit in session["dose_evidence"]["limits"])]
    assert selected, "The first sport must undergo real quality balancing."
    built = {id(parts): (method, minutes) for method, minutes, _, _, parts in builds}
    for parts, minutes, zone in dose_checks:
        if id(parts) in built:
            method, own_minutes = built[id(parts)]
            assert minutes == own_minutes, (method["actual_sport"], method["id"], zone,
                                            "dose checked with another sport's capacity", minutes, own_minutes)
    identifiers = {session["method_id"] for session in selected}
    for identifier in identifiers:
        calls = [build for build in builds if build[0]["id"] == identifier]
        assert {method.get("actual_sport") for method, *_ in calls} == {"NordicSki", "Run"}
        own_capacities = {}
        for method, minutes, target_speed, _, _ in calls:
            sport = method["actual_sport"]
            capacity = engine.capacity_for(method, repo.settings, speeds[sport],
                engine._capacity_context(speeds[sport], repo.settings), now.date(), use_model_prior=True)
            assert capacity is not None
            own_capacities[sport] = capacity["capacity_minutes"]
            assert minutes == capacity["capacity_minutes"], (identifier, sport, minutes, capacity)
            assert target_speed == capacity["target_speed_kmh"]
        assert own_capacities["NordicSki"] > own_capacities["Run"] * 1.25

    rows = engine._daily_rows(source, now.date())
    for day in plan["days"]:
        when = date.fromisoformat(day["date"])
        for session in day["sessions"]:
            evidence = session["dose_evidence"]
            matching = [build for build in builds if build[4] is session["blocks"]]
            assert matching, "Final whole blocks must come from an observed genuine build."
            method, minutes, _, work, parts = matching[-1]
            assert method["actual_sport"] == session["sport"]
            assert minutes == evidence["capacity_minutes"]
            assert evidence["structure_work_budget_minutes"] == pytest.approx(work, abs=.001)
            assert evidence["primary_work_budget_minutes"] <= work + .001
            assert session["total_minutes"] == pytest.approx(sum(b["duration_min"] for b in parts), abs=.001)
            assert session["main_work_minutes"] == pytest.approx(
                sum(b["duration_min"] for b in parts if b["kind"] == "WORK"), abs=.001)
            primary = sum(b["duration_min"] for b in parts
                          if b["kind"] == "WORK" and b["zone"] == session["zone"])
            assert evidence["applied_fraction"] == pytest.approx(primary / minutes, abs=.001)
            if session["zone"] != "STR":
                assert engine._dose_usage(parts, evidence, session["zone"]) <= evidence["max_dose_fraction"] + .001
                if evidence["min_dose_fraction"] is not None:
                    assert engine._minimum_dose_usage(parts, evidence, session["zone"],
                        primary_only=bool(session.get("mixed_component") or method.get("developmental_variant"))) >= evidence["min_dose_fraction"] - .001
                else:
                    assert session["purpose"] == "RECOVERY"
                    assert work >= evidence["minimum_primary_work_minutes"]
            if method["structure"] in {"MODEL_INTERVALS", "METABOLIC_INTERVALS"}:
                profile = evidence["effort_profile"]
                repetitions = [b for b in parts if b["kind"] == "WORK" and b["zone"] == session["zone"]]
                rests = [b for b in parts if b["kind"] == "RECOVERY"]
                assert profile["min_repetitions"] <= len(repetitions) <= profile["max_repetitions"]
                assert all(b["duration_s"] == profile["work_seconds"] for b in repetitions)
                assert len(rests) == len(repetitions) - 1
                assert all(b["duration_s"] == profile["recovery_seconds"] for b in rests)
            direct, effective, _ = engine._canonical_load(parts, repo.settings, rows, when,
                zone_tmax_minutes=evidence["zone_tmax_minutes"])
            for zone in COMPONENTS:
                assert session["direct_equivalent_minutes"][zone] == pytest.approx(direct[zone], abs=.001)
                assert session["canonical_effective_load"][zone] == pytest.approx(effective[zone], abs=.001)
            rows = engine._add_forecast_session(rows, when, effective)
    assert_segment_contract(plan)
    assert_rolling_budgets(plan)
