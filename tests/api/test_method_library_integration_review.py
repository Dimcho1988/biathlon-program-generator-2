"""Cross-module safeguards for source recipes entering the real weekly planner."""
from copy import deepcopy
from datetime import timedelta
import json
from types import SimpleNamespace

import pytest

from apps.api import training_plan_engine as engine
from biathlon import dosing_curve, method_library
from tests.test_dosing_curve import BOUNDS, example
from tests.api.test_training_plan_engine import TODAY, NOW
from tests.api.test_load_progression import configured, observed
from tests.api.test_readiness_adaptive_plan_v2 import fixed_readiness, assert_rolling_budgets


SETTINGS = SimpleNamespace(zone_bounds_bpm=BOUNDS, hrmax_bpm=205, timezone="Europe/Sofia")


def source(identifier):
    return next(r for r in method_library.entries() if r["id"] == identifier)


def athlete(**changes):
    return {"sport": "Run", "actual_sport": "Run", "discipline": "5000 m",
            "planning_controls": {"capacity_policy": "MODEL_WITH_PRIOR"}, **changes}


def executable(identifier, monkeypatch, *, phase=None):
    row = source(identifier)
    phase = phase or row["periods"][0]
    profile = athlete(discipline=f"{row['disciplines'][0]} m") if row.get("disciplines") else athlete()
    monkeypatch.setattr(method_library, "entries", lambda: [deepcopy(row)])
    view = example()
    methods = method_library.executable_methods(profile, view, phase=phase)
    assert methods, method_library.resolve(row, profile, view, phase=phase)
    method = methods[0]
    evidence = engine.capacity_for(method, SETTINGS, view,
                                  engine._capacity_context(view, SETTINGS), TODAY, use_model_prior=True)
    assert evidence
    return method, evidence, view


def test_race_distance_uses_the_same_blended_curve_for_distance_and_speed():
    view = example()
    before = deepcopy(view)
    row = source("AO17")
    resolved = method_library.resolve(row, athlete(), view, phase=row["periods"][0])
    assert resolved["status"] == "READY", resolved
    anchor = resolved["anchor"]
    curve = dosing_curve.from_view(view)
    assert anchor["duration_seconds"] * anchor["speed_kmh"] / 3.6 == pytest.approx(5000)
    assert anchor["duration_seconds"] == pytest.approx(curve.inverse(5000, distance=True))
    assert resolved["target_speed_kmh"] == pytest.approx(anchor["speed_kmh"] * 1.025)
    assert view == before


def test_ski_discipline_uses_rollerski_curve_and_never_calendar_fitness_uplift():
    view = example()
    view["sport"] = "RollerSki"
    profile = athlete(sport="NordicSki", actual_sport="RollerSki", discipline="10 km")
    row = source("OF-XC-SHORT")
    early = method_library.resolve(row, profile, view, phase="GENERAL_PREPARATION")
    special = method_library.resolve(row, profile, view, phase="SPECIAL_PREPARATION")
    assert early["status"] == special["status"] == "READY"
    assert early["anchor"]["distance_m"] == 10000
    assert early["actual_sport"] == "RollerSki"
    assert early["anchor"]["speed_kmh"] == special["anchor"]["speed_kmh"]
    assert early["work_duration_seconds"] < special["work_duration_seconds"]
    assert early["target_speed_kmh"] > special["target_speed_kmh"]
    assert not early["curve_improvement_assumed"] and not special["curve_improvement_assumed"]
    assert method_library.resolve(row, profile, {**view, "sport": "NordicSki"},
                                  phase="GENERAL_PREPARATION")["status"] == "UNAVAILABLE"


def test_fixed_source_repeat_count_reduces_once_for_recovery(monkeypatch):
    method, evidence, _ = executable("R01", monkeypatch)
    evidence["readiness_dose_factor"] = .5
    fraction = engine._nominal_fraction(method, "BUILDING", {"building_fraction": .65},
                                        "GENERAL_PREPARATION", evidence, None)
    work = evidence["capacity_minutes"] * fraction * .5
    blocks = engine._blocks(method, work, evidence, SETTINGS)
    repeats = [b for b in blocks if b["kind"] == "WORK"]
    assert len(repeats) == 2
    assert sum(b["target_distance_m"] for b in repeats) == 2000
    assert evidence["method_library"]["resolution"]["source_repetitions"] == [4, 4]
    assert engine._dose_usage(blocks, evidence, method["zone"]) == pytest.approx(.5)


def test_continuous_source_uses_tmax_denominator_not_its_own_recipe_ceiling(monkeypatch):
    method, evidence, _ = executable("AO21", monkeypatch, phase="SPECIAL_PREPARATION")
    blocks = engine._blocks(method, 15, evidence, SETTINGS)
    assert blocks
    assert engine._dose_usage(blocks, evidence, method["zone"]) == pytest.approx(
        15 / evidence["capacity_minutes"])
    assert engine._dose_fits(blocks, evidence, method["zone"], .65)


def test_no_source_repeat_count_does_not_manufacture_a_hundred_repeat_budget():
    row = source("R07")
    resolved = method_library.resolve(row, athlete(), example(), phase=row["periods"][0])
    assert resolved["status"] == "READY", resolved
    assert resolved["maximum_work_seconds"] <= resolved["continuous_capacity_seconds"] + 1e-6
    assert resolved["maximum_work_seconds"] < 100 * resolved["cycle_work_seconds"]


def test_active_recovery_keeps_its_source_speed_anchor(monkeypatch):
    method, evidence, view = executable("R10", monkeypatch)
    blocks = engine._blocks(method, 12.5, evidence, SETTINGS)
    rests = [b for b in blocks if b["kind"] == "RECOVERY"]
    expected = .6 * dosing_curve.from_view(view).speed(300) * 3.6
    assert len(rests) == 4
    assert all(b["target_speed_kmh"] == pytest.approx(expected, abs=.001) for b in rests)


def test_alternating_recovery_is_not_counted_as_primary_work(monkeypatch):
    method, evidence, _ = executable("R04", monkeypatch)
    cycle = evidence["method_library"]["resolution"]["cycle_work_seconds"] / 60
    blocks = engine._blocks(method, 2 * cycle, evidence, SETTINGS)
    work = [b for b in blocks if b["kind"] == "WORK"]
    rest = [b for b in blocks if b["kind"] == "RECOVERY"]
    assert len(work) == 2 and sum(b["target_distance_m"] for b in work) == 2000
    assert len(rest) == 1 and rest[0]["target_distance_m"] == 200


def test_source_method_is_selected_and_published_as_real_weekly_blocks(monkeypatch):
    method, _, view = executable("R10", monkeypatch)
    view.update(source_generation_id="generation-one", source_revision=1,
                index_window={"last_activity_date": TODAY.isoformat()})
    zone = method["zone"]
    profile = configured(age_years=25, training_experience_years=5, discipline="5000 m",
                         building_fraction=.65, available_minutes=[180] * 7,
                         horizon_mode="MANUAL", program_end=(TODAY + timedelta(days=6)).isoformat(),
                         component_targets_weekly={"Z1": 100., zone: 40.})
    profile["planning_controls"].update(accents=[zone], sessions_per_week=1,
                                        sessions_by_day=[0, 1, 0, 0, 0, 0, 0], threshold_days=[1])
    fixed_readiness(monkeypatch, 100.)
    monkeypatch.setattr(engine, "resolved_methods", lambda _: [])
    monkeypatch.setattr(engine.model_service, "speed_view", lambda *args, **kwargs: deepcopy(view))
    repo, _, _ = observed()
    repo.settings = SETTINGS
    plan = engine.generate_plan(repo, "athlete", profile, start_date=TODAY, now=NOW)
    sessions = [s for day in plan["days"] for s in day["sessions"]]
    assert sessions, [r for d in plan["days"] for r in d.get("rejected_alternatives", [])]
    assert sessions[0]["method_library"]["id"] == "R10"
    work = [b for b in sessions[0]["blocks"] if b["kind"] == "WORK"]
    assert work and all(b["duration_min"] == 2.5 for b in work)
    assert all(b["target_speed_kmh"] > 0 for b in work)
    assert_rolling_budgets(plan)


def test_catalog_content_change_invalidates_frozen_management_inputs(monkeypatch, tmp_path):
    from apps.api import management_service

    repository = SimpleNamespace(
        athlete_settings=lambda _: None, active_analysis=lambda _: None,
        athlete_planning_calendar=lambda _: None, athlete_planning_profile=lambda _: None,
        athlete_mesocycle_accent_preferences=lambda _: None,
    )
    monkeypatch.setattr(management_service, "ModelStore", lambda _: SimpleNamespace(entries=lambda _: []))
    path = tmp_path / "catalog.json"
    data = json.loads(method_library.CATALOG_PATH.read_text())
    path.write_text(json.dumps(data))
    monkeypatch.setattr(method_library, "CATALOG_PATH", path)
    first = management_service.input_state(repository, "athlete", evaluated_at=NOW)
    assert first["rule_versions"]["method_library"]["method_count"] == len(data["methods"])
    data["methods"][0]["guidance"]["execution"] += " Уточнение за изпълнението."
    path.write_text(json.dumps(data))
    second = management_service.input_state(repository, "athlete", evaluated_at=NOW)
    assert first["rule_versions"]["engine"] == second["rule_versions"]["engine"]
    assert first["rule_versions"]["method_library"]["version"] == second["rule_versions"]["method_library"]["version"]
    assert management_service._hash(first) != management_service._hash(second)
