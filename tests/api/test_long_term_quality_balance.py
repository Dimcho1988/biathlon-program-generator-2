"""A complete mixed dose must not consume the preparation for other qualities."""
from copy import deepcopy
from collections import Counter
from datetime import date, timedelta

from apps.api import training_plan_engine as engine
from biathlon import adaptive_methods
from biathlon.constants import COMPONENTS
from tests.api.test_load_progression import configured, observed
from tests.api.test_long_term_scheduling import prescribed_q, assert_segment_contract
from tests.api.test_management_v2 import interval
from tests.api.test_readiness_adaptive_plan_v2 import fixed_readiness
from tests.api.test_training_plan_engine import NOW, TODAY, reference_speed


def balanced_fixture(monkeypatch):
    # Move the synthetic observed calendar to Thursday. The Friday draft is
    # tomorrow, so no unknown intervening load is silently bypassed.
    repo, source, _ = observed()
    # This scheduling fixture needs the original component E exposure. Under
    # the adjacent-zone model these subthreshold direct vectors reproduce it
    # exactly; the old fixture kept high daily E but replaced every Q with 8.
    recorded = {(row["date"], row["zone"]): row["effective_load"] for row in source["daily"]}
    for activities in (source["activities"], repo.envelope["activities"]):
        for activity in activities:
            for row in activity["zones"]:
                row["equivalent_time_min"] = row["raw_time_min"] = recorded[activity["date"], row["zone"]]
            activity["duration_min"] = sum(row["raw_time_min"] for row in activity["zones"])
    for rows in (source["daily"], source["activities"], source["strength"]["daily"]):
        for row in rows:
            row["date"] = (date.fromisoformat(row["date"]) + timedelta(days=3)).isoformat()
    for activity in repo.envelope["activities"]:
        activity["date"] = (date.fromisoformat(activity["date"]) + timedelta(days=3)).isoformat()
        activity["local_date"] = activity["date"]
    for key in ("period_start", "period_end"):
        source[key] = (date.fromisoformat(source[key]) + timedelta(days=3)).isoformat()

    fixed_readiness(monkeypatch, 80.)
    body = configured(age_years=30, training_experience_years=10, strength_enabled=True,
        race_duration_min=4, available_minutes=[360]*6+[0], building_fraction=.65,
        interval_profiles=[interval("Z4", continuous_capacity_min=20.),
                           interval("Z5", continuous_capacity_min=20.)])
    body["planning_controls"].update(
        mesocycle_anchor=(TODAY-timedelta(days=13)).isoformat(), accent_mode="AUTO",
        accents=[], automatic_focus_count=3, sessions_per_week=13,
        sessions_by_day=[2,2,2,2,2,1,0], threshold_days=[1,4], strength_days=[1,4],
        threshold_method="INTERVALS", mixed_sessions_enabled=True, history_gap_days=10)
    cutoff = TODAY + timedelta(days=8)
    # Coach intent is known directly in Q. Preserve genuine canonical E
    # goals, capacities, complete structures and all scheduling constraints.
    building = {"Z1":379.32, "Z2":128.41, "Z3":75.72, "Z4":28.72, "Z5":5.65}
    recovery = {"Z1":189.85, "Z2":69.30, "Z3":38.38, "Z4":15.50, "Z5":4.57}
    prescribed_q(monkeypatch, lambda day: building if day < cutoff else recovery)
    return repo, body, TODAY+timedelta(days=4), NOW+timedelta(days=3)


def assert_complete_counterfactual_fits(repo, body, plan, now):
    """Prove that the requested coverage has a real, legal whole-dose bundle.

    Main Z3 and strength share the preferred Friday/Tuesday, with mixed Z2,
    Z4 and Z5 cores on subsequent available days. Their optional easy completion
    uses surplus only. Canonical E is recalculated from causal rows after
    every dose; no warm-up or cascade is counted as quality.
    """
    methods = {method["id"]:method for method in engine.resolved_methods(body)}
    speed = reference_speed(repo, "athlete", "Run")
    context = engine._capacity_context(speed, repo.settings)
    rows = engine._daily_rows(repo.envelope["snapshot_payload"]["load_history"], now.date())
    identifiers = ("END-THR-TIME-01-FLEX", "STR-CIRCUIT-RUN-01",
                   "END-CROSS-TRAIN-01-Z2-MIX-STEADY", "END-VO2-TREF-01-Z4-MIX-REPETITIONS-SHORT",
                   "END-VO2-TREF-01-Z5-MIX-REPETITIONS-SHORT")
    segments = plan["allocation"]["segments"]
    assert len(identifiers)*len(segments) <= body["planning_controls"]["sessions_per_week"]
    assert len(segments) <= body["max_key_sessions_per_week"]
    assert len(segments) <= body["planning_controls"]["max_strength_sessions"]
    starts = [date.fromisoformat(segment["window_start"]) for segment in segments]
    assert all((later-earlier).days >= 2 for earlier, later in zip(starts, starts[1:]))
    for segment in segments:
        first = date.fromisoformat(segment["window_start"])
        last = date.fromisoformat(segment["window_end"])
        placements = (first, first, first+timedelta(days=1), last, last)
        assert all(count <= body["planning_controls"]["sessions_by_day"][day.weekday()]
                   for day, count in Counter(placements).items())
        direct_total = dict.fromkeys(COMPONENTS, 0.)
        effective_total = dict.fromkeys(COMPONENTS, 0.)
        for identifier, day in zip(identifiers, placements):
            method = deepcopy(methods[identifier])
            capacity = engine.capacity_for(method, repo.settings, speed, context, now.date())
            assert capacity is not None
            readiness = adaptive_methods.readiness_policy(method, body, dict.fromkeys(COMPONENTS, 80.))
            capacity["readiness_dose_factor"] = readiness["dose_factor"]
            if method.get("mixed_component"):
                secondary = engine.capacity_for({**method,"zone":"Z1","position":.35,"structure":"CONTINUOUS"},
                    repo.settings, speed, context, now.date())
                assert secondary is not None
                capacity.update(secondary_capacity=secondary, mixed_component=True,
                    easy_work_cap_minutes=0.,
                    easy_to_primary_ratio=max(2., secondary["capacity_minutes"]*.25/max(1., capacity["capacity_minutes"]*.15)))
            purpose = method["purpose"]
            if segment["days"] == 3 and purpose == "BUILDING":
                purpose = "MAINTENANCE"
            nominal = engine._nominal_fraction(method, purpose, body, "GENERAL_PREPARATION", capacity, body["planning_controls"])
            minimum = engine._minimum_work(method, capacity, repo.settings)
            assert minimum is not None and minimum <= capacity["capacity_minutes"]*nominal*.8+.001
            blocks = engine._blocks(method, minimum, capacity, repo.settings)
            # This counterfactual can also be checked against the old engine,
            # whose block builder always appended the optional easy tail.
            # Retain every work repetition, active rest and preparation block.
            blocks = [block for block in blocks if block["label"] != "Довършване на леката аеробна работа"]
            assert blocks and sum(block["duration_min"] for block in blocks) <= body["available_minutes"][day.weekday()]
            if method["zone"] != "STR":
                lower = method.get("minimum_fraction", .25)*.8
                assert engine._minimum_dose_usage(blocks, capacity, method["zone"], primary_only=bool(method.get("mixed_component"))) >= lower-1e-9
            ceiling = engine._dose_ceiling(method, purpose, body, "GENERAL_PREPARATION",
                capacity, body["planning_controls"])
            assert engine._dose_fits(blocks, capacity, method["zone"], ceiling*.8)
            direct, effective, _ = engine._canonical_load(blocks, repo.settings, rows, day,
                zone_tmax_minutes=capacity["zone_tmax_minutes"])
            rows = engine._add_forecast_session(rows, day, effective)
            for zone in COMPONENTS:
                direct_total[zone] += direct[zone]
                effective_total[zone] += effective[zone]
        for zone, objective in segment["components"].items():
            assert direct_total[zone] <= objective["target"]+.005
            assert effective_total[zone] <= objective["target_effective"]+.005, (segment["window_start"], zone, effective_total[zone], objective["target_effective"])


def test_long_mixed_tails_and_easy_work_cannot_starve_executable_long_term_qualities(monkeypatch):
    repo, body, start, now = balanced_fixture(monkeypatch)
    plan = engine.generate_plan(repo, "athlete", body, start_date=start, now=now)
    assert plan["allocation"] is not None
    assert [segment["days"] for segment in plan["allocation"]["segments"]] == [4,3]
    assert_complete_counterfactual_fits(repo, body, plan, now)
    protection_bundles = 0
    for segment in plan["allocation"]["segments"]:
        days = [day for day in plan["days"] if segment["window_start"] <= day["date"] <= segment["window_end"]]
        for zone in ("Z2", "Z4", "STR"):
            work = sum(block["duration_min"] for day in days for session in day["sessions"]
                       for block in session["blocks"] if block["kind"] == "WORK" and block["zone"] == zone)
            assert work > 0, f"{zone} has executable whole-dose headroom in {segment['window_start']}; optional Z1 must leave its preparation."
        for day in days:
            for session in day["sessions"]:
                assert session["dose_evidence"]["readiness_dose_factor"] == .8
                for limit in session["dose_evidence"]["limits"]:
                    if limit["code"] != "LONG_TERM_QUALITY_PREPARATION":
                        continue
                    protection_bundles += 1
                    methods = limit["protected_methods"]
                    assert methods and {method["component"] for method in methods} == set(limit["protected_components"])
                    placements = [(method["date"], method["slot"]) for method in methods]
                    assert len(placements) == len(set(placements)), "A protection bundle cannot assign two methods to one slot."
                    for protected in methods:
                        reserved_day = date.fromisoformat(protected["date"])
                        assert segment["window_start"] <= protected["date"] <= segment["window_end"]
                        slots = body["planning_controls"]["sessions_by_day"][reserved_day.weekday()]
                        assert slots > 0 and body["available_minutes"][reserved_day.weekday()] > 0
                        assert 1 <= protected["slot"] <= slots
    assert protection_bundles > 0
    assert plan["summary"]["key_sessions"] <= body["max_key_sessions_per_week"] == 2
    assert not next(day for day in plan["days"] if date.fromisoformat(day["date"]).weekday() == 6)["sessions"]
    assert_segment_contract(plan)
