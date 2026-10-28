"""Direct prescription and physiological forecasts must not spend two quotas."""
from copy import deepcopy
from datetime import date, timedelta
import random

import pandas as pd
import pytest

from apps.api import training_plan_engine as engine
from biathlon import planning_allocation as allocation, planning_continuity, planning_envelope
from biathlon.component_load import calculate_component_load, effective_upper_bound
from biathlon.constants import COMPONENTS, DEFAULT_PARAMETERS
from biathlon.physiology import compute_load_statistics

DAY = date(2026, 10, 27)


def history(days=50, value=10.):
    return [{"date": (DAY-timedelta(days=i)).isoformat(), "zone": z, "effective_load": value}
            for i in range(days, 0, -1) for z in COMPONENTS]


def segment(rows, *, q=100., e=10., source=None, days=()):
    windows = {DAY+timedelta(days=i): {z: {"target": e, "target_weekly_q": q}
                                    for z in COMPONENTS} for i in range(7)}
    # STR's direct exposure has its own target, numerically equal to E.
    for goals in windows.values():
        goals["STR"]["target"] = q
    return allocation.segment_headroom(windows, history(), rows, source or {"activities": []}, days,
                                       DAY, segment_keys={d: 1 for d in windows})


def budgets(rows, objective):
    goals = {z: {"target": 10., "basis": "TEST", "target_index": 1.} for z in COMPONENTS}
    return engine._prescription_budgets(rows, DAY, engine._budgets(rows, DAY, targets=goals), objective)


def test_received_e_above_cycle_reference_does_not_consume_direct_q():
    rows = engine._add_forecast_session(history(), DAY, dict.fromkeys(COMPONENTS, 40.))
    objective = segment(rows)
    assert objective["components"]["Z3"]["remaining_effective"] == 0.
    assert objective["components"]["Z3"]["remaining"] == 100.
    limits, q, _ = budgets(rows, objective)
    assert q["Z3"] == pytest.approx(110.)
    assert limits["Z3"]["deficit_effective"] > 0
    assert limits["Z3"]["effective_limit_basis"] == "PROSPECTIVE_CANONICAL_7_40"
    allowance = planning_continuity.limits(objective["components"], limits, q,
        {"q": {}, "e": {}, "references": {}}, zone="Z3")
    direct = dict.fromkeys(COMPONENTS, 0.); direct["Z3"] = 10.
    effective = calculate_component_load(direct)["effective"]
    assert planning_continuity.fits(direct, effective, allowance, objective["components"])


def test_maintenance_index_one_is_not_the_prospective_maximum():
    rows = history()
    objective = segment(rows, q=100., e=70.)
    limits, q, _ = budgets(rows, objective)
    # Old rolling remainder is zero at an E target of 70, although 7/40=1.
    assert sum(r["effective_load"] for r in rows if r["zone"] == "STR"
               and r["date"] >= (DAY-timedelta(days=6)).isoformat()) == 60.
    assert limits["STR"]["desired_index_7_40"] == 1.
    assert limits["STR"]["index_7_40_limit"] == 2.
    assert q["STR"] == pytest.approx(110.)
    assert limits["STR"]["deficit_effective"] > 10.


def test_q_tolerance_is_local_and_actual_overshoot_never_refills_it():
    row = {"basis": "DIRECT_Q", "target": 100., "actual": 35., "planned": 70.}
    assert allocation.assess_component(row)["within_tolerance"]
    assert allocation.prescription_headroom(row) == pytest.approx(5.)
    assert allocation.prescription_headroom({**row, "actual": 120.}) == 0.
    assert allocation.prescription_headroom({**row, "actual": None}) is None
    assert allocation.prescription_headroom({**row, "basis": "CANONICAL_E"}) == 0.
    assert allocation.gap_score([("Z3", {**row, "planned": 65.})]) > allocation.gap_score([("Z3", row)])


def test_preparation_and_taper_do_not_add_another_band_to_110_percent():
    rows = history()
    objective = segment(rows)
    limits, q, _ = budgets(rows, objective)
    reserve = {"q": dict.fromkeys(COMPONENTS, 100.), "e": dict.fromkeys(COMPONENTS, 500.),
               "references": {"Z3": {}}}
    for allowance in (
        planning_continuity.limits(objective["components"], limits, q, reserve, zone="Z3", preparation=True),
        planning_continuity.taper_limits(objective["components"], limits, q, zone="Z1", light=True),
    ):
        assert allowance["q"]["Z1"] == pytest.approx(110.)
        assert allowance["e"]["Z1"] == limits["Z1"]["deficit_effective"]


def test_unknown_actual_q_and_legacy_e_keep_their_own_boundaries():
    rows = history()
    source = {"activities": [{"date": DAY.isoformat(), "zones": []}]}
    objective = segment(rows, source=source)
    _, q, _ = budgets(rows, objective)
    assert q["Z3"] == 0. and objective["components"]["Z3"]["actual_q"] is None
    objective = segment(rows)
    objective["components"]["Z3"].update(basis="CANONICAL_E", remaining=8.,
                                           remaining_effective=8., target=10., actual=0., planned=2.)
    limits, q, _ = budgets(rows, objective)
    assert "Z3" not in q
    assert limits["Z3"]["deficit_effective"] <= 8.
    assert limits["Z3"]["effective_limit_basis"] == "LEGACY_E_TARGET"


@pytest.mark.parametrize("count", [0, 4, 10, 20, 50, 65])
def test_envelope_matches_canonical_recorded_index_including_candidate_day(count):
    rng = random.Random(712+count)
    rows = history(count)
    for row in rows:
        row["effective_load"] *= rng.uniform(.3, 2.)
    before = deepcopy(rows)
    maximum = dict.fromkeys(COMPONENTS, 10000.)
    limits = planning_envelope.headroom(rows, DAY, maximum)
    assert rows == before
    projected = engine._add_forecast_session(rows, DAY, {z: limits[z]["deficit_effective"] for z in COMPONENTS})
    frame = pd.DataFrame(projected).pivot(index="date", columns="zone", values="effective_load")
    frame.index = pd.to_datetime(frame.index)
    frame.columns = [f"e_{z}" for z in frame.columns]
    statistics = compute_load_statistics(frame, DEFAULT_PARAMETERS, DAY)
    for z in COMPONENTS:
        assert statistics.loc[z, "index_7_40"] <= 2.+1e-9
        if limits[z]["index_limit_binding"]:
            assert statistics.loc[z, "index_7_40"] >= 1.999


def test_future_load_cannot_raise_todays_allowed_envelope():
    rows = history()
    later = [{"date": (DAY+timedelta(days=2)).isoformat(), "zone": z, "effective_load": 10000.}
             for z in COMPONENTS]
    assert planning_envelope.headroom(rows, DAY, dict.fromkeys(COMPONENTS, 500.)) == (
        planning_envelope.headroom(rows+later, DAY, dict.fromkeys(COMPONENTS, 500.)))


def test_effective_search_bound_covers_different_doses_and_splits():
    rng = random.Random(431)
    for _ in range(30):
        q = {z: rng.uniform(0., 100.) for z in COMPONENTS}
        bound = effective_upper_bound(q)
        total = dict.fromkeys(COMPONENTS, 0.)
        split = rng.random()
        for fraction in (split, 1-split):
            load = calculate_component_load({z: fraction*v for z, v in q.items()},
                {z: rng.uniform(1., 200.) for z in COMPONENTS if z != "STR"})["effective"]
            for z in COMPONENTS:
                total[z] += load[z]
        assert all(total[z] <= bound[z]+1e-9 for z in COMPONENTS)
        assert bound["STR"] == q["STR"]


@pytest.mark.parametrize("target,expect_supplement", [(3., True), (40., False)])
def test_free_slots_do_not_reject_a_supplement_without_a_legal_standalone(monkeypatch, target, expect_supplement):
    from tests.api.test_load_progression import configured, observed
    from tests.api.test_management_v2 import reference_speed
    from tests.api.test_readiness_adaptive_plan_v2 import fixed_readiness
    from tests.api.test_training_plan_engine import TODAY, NOW
    fixed_readiness(monkeypatch, 100.)
    profile = configured(age_years=30, training_experience_years=10, available_minutes=[180]*7)
    profile["planning_controls"].update(accent_mode="MANUAL", accents=["Z4"], threshold_days=[0, 4],
        sessions_per_week=7, sessions_by_day=[1]*7, mixed_sessions_enabled=True)
    ids = {"ONFLOWS-CONTROLLED-Z4-V2", "ONFLOWS-CONTROLLED-Z4-V2-MIX-REPETITIONS-SHORT"}
    methods = [m for m in engine.resolved_methods(profile) if m["id"] in ids]
    assert len(methods) == 2
    monkeypatch.setattr(engine, "resolved_methods", lambda _: deepcopy(methods))
    monkeypatch.setattr(engine.model_service, "speed_view", reference_speed)
    monkeypatch.setattr(engine.combined_methods, "shortage", lambda *args, **kwargs: {
        "shortage":False, "sessions_by_component":{}, "available_sessions":7})
    original = engine._goals
    def goals(*args, **kwargs):
        values, accents, cycle = original(*args, **kwargs)
        for z in COMPONENTS:
            values[z].update(target_weekly_q=1000. if z == "Z1" else target if z == "Z4" else 0.)
        values["STR"]["target"] = 0.
        return values, accents, cycle
    monkeypatch.setattr(engine, "_goals", goals)
    repo, _, _ = observed()
    plan = engine.generate_plan(repo, "athlete", profile, start_date=TODAY, now=NOW)
    sessions = plan["days"][0]["sessions"]
    assert sessions and bool(sessions[0]["mixed_component"]) == expect_supplement
    if expect_supplement:
        assert sessions[0]["dose_evidence"]["selection"]["supporting_basis"] == "NO_ADMISSIBLE_STANDALONE_IN_THIS_SLOT"
    assert plan["allocation"]["segments"][0]["components"]["Z4"]["planned_q"] <= target*1.1+.005
