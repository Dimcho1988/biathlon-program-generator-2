"""An original HR protocol reaches planner blocks without a speed estimate."""
from copy import deepcopy
from datetime import date
from types import SimpleNamespace

import pytest

from biathlon import method_library, method_dosing
from biathlon.equivalence import equivalence_slope
from biathlon.physiology import linear_equivalence_coefficient
from apps.api import training_plan_engine as engine


SETTINGS = SimpleNamespace(hrmax_bpm=200, zone_bounds_bpm=(100, 150, 170, 180, 190, 200))


def source():
    return {
        "id": "SOURCE-HR4X4", "title": "Source HR 4×4", "sports": ["Run"],
        "periods": ["GENERAL_PREPARATION"],
        "source": {"doi": "10.1249/mss.0b013e3180304570"},
        "adaptation": "Whole fewer repetitions are an explicit volume adaptation; intensity and rest stay prescribed.",
        "prescription": {
            "anchor": {"type": "HRMAX"}, "hrmax_fraction": [.9, .95],
            "work": {"unit": "seconds", "range": [240, 240]},
            "structure": "INTERVALS", "repetitions": [4, 4],
            "allow_fewer_repetitions": True,
            "standard_variant": {"repetitions": 4, "sets": 1,
                                 "work": {"unit": "seconds", "range": [240, 240]}},
            "recovery": {"seconds": [180, 180], "type": "ACTIVE", "hrmax_fraction": [.7, .7]},
        },
    }


def adapter(monkeypatch):
    profile = {"sport": "Run", "building_fraction": .65,
               "planning_controls": {"capacity_policy": "MODEL_WITH_PRIOR"}}
    speed = {"status": "UNAVAILABLE", "sport": "Run",
             "sport_hr_policy": {"sport_hrmax_bpm": 200,
                                 "sport_zone_bounds_bpm": list(SETTINGS.zone_bounds_bpm)}}
    monkeypatch.setattr(method_library, "entries", lambda: [deepcopy(source())])
    methods = method_library.executable_methods(profile, speed, phase="GENERAL_PREPARATION")
    assert len(methods) == 1
    method = {**methods[0], "actual_sport": "Run", "warmup_min": 0, "cooldown_min": 0}
    context = engine._capacity_context(speed, SETTINGS)
    capacity = engine.capacity_for(method, SETTINGS, speed, context, date(2026, 10, 20), use_model_prior=True)
    assert capacity is not None
    return method, capacity, profile


def test_hr_source_runs_without_curve_and_keeps_full_original_rest(monkeypatch):
    method, evidence, profile = adapter(monkeypatch)
    assert evidence["target_speed_kmh"] is None
    assert evidence["capacity_reference"] == "SOURCE_STANDARD_WORK_NOT_PHYSIOLOGICAL_TMAX"
    fraction = engine._nominal_fraction(method, "BUILDING", profile, "GENERAL_PREPARATION", evidence, {})
    work_minutes = evidence["capacity_minutes"] * fraction
    assert work_minutes == pytest.approx(16)
    blocks = engine._blocks(method, work_minutes, evidence, SETTINGS)
    work = [b for b in blocks if b["kind"] == "WORK"]
    rests = [b for b in blocks if b["kind"] == "RECOVERY"]
    assert len(work) == 4
    assert len(rests) == 3
    assert all(b["duration_min"] == 4 and b["target_hr_bpm"] == 185 for b in work)
    assert all(b["hr_reference_range_bpm"] == [180, 190] for b in work)
    assert all(b["duration_min"] == 3 and b["target_hr_bpm"] == 140 for b in rests)
    assert all(b["target_speed_kmh"] is None for b in blocks)
    direct, _, _ = engine._canonical_load(blocks, SETTINGS, [], date(2026, 10, 20))
    expected_work = 16 * linear_equivalence_coefficient(185, 180, 190, equivalence_slope("Z4"))
    expected_rest = 9 * linear_equivalence_coefficient(140, 100, 150, equivalence_slope("Z1"))
    assert direct["Z4"] == pytest.approx(expected_work)
    assert direct["Z1"] == pytest.approx(expected_rest)
    assert all(direct[z] == 0 for z in ("Z2", "Z3", "Z5", "STR"))


def test_hr_maintenance_uses_selected_standard_once_then_whole_repetitions(monkeypatch):
    method, evidence, profile = adapter(monkeypatch)
    fraction = engine._nominal_fraction(method, "MAINTENANCE", profile, "GENERAL_PREPARATION", evidence, {})
    requested = evidence["capacity_minutes"] * fraction
    assert requested == pytest.approx(16 * .6)
    blocks = engine._blocks(method, requested, evidence, SETTINGS)
    assert len([b for b in blocks if b["kind"] == "WORK"]) == 2
    assert sum(b["duration_min"] for b in blocks if b["kind"] == "WORK") == 8
    assert sum(b["duration_min"] for b in blocks if b["kind"] == "RECOVERY") == 3


def test_hr_building_recovery_reduces_work_once_and_preserves_intensity(monkeypatch):
    method, evidence, profile = adapter(monkeypatch)
    fraction = engine._nominal_fraction(method, "BUILDING", profile, "GENERAL_PREPARATION", evidence, {})
    factor = method_dosing.recovery_factor({"allowed": True, "observed_dose_factor": .8}, "BUILDING")
    assert factor == .9
    requested = evidence["capacity_minutes"] * fraction * factor
    assert requested == pytest.approx(14.4)
    blocks = engine._blocks(method, requested, evidence, SETTINGS)
    work = [b for b in blocks if b["kind"] == "WORK"]
    rests = [b for b in blocks if b["kind"] == "RECOVERY"]
    assert len(work) == 3
    assert all(b["duration_min"] == 4 and b["target_hr_bpm"] == 185 for b in work)
    assert len(rests) == 2 and all(b["duration_min"] == 3 for b in rests)


def test_original_beginner_protocol_keeps_published_preparation_and_no_partial_repeat(monkeypatch):
    row = next(r for r in method_library.entries() if r["id"] == "HR-RUN-1X4-TJONNA2013")
    monkeypatch.setattr(method_library, "entries", lambda: [deepcopy(row)])
    profile = {"sport": "Run", "load_progression": {"training_level": "LOW"},
               "planning_controls": {"capacity_policy": "MODEL_WITH_PRIOR"}}
    speed = {"status": "UNAVAILABLE", "sport": "Run",
             "sport_hr_policy": {"sport_hrmax_bpm": 200,
                                 "sport_zone_bounds_bpm": list(SETTINGS.zone_bounds_bpm)}}
    method, = method_library.executable_methods(profile, speed, phase="GENERAL_PREPARATION")
    method["actual_sport"] = "Run"
    evidence = engine.capacity_for(method, SETTINGS, speed, engine._capacity_context(speed, SETTINGS),
                                   date(2026, 10, 20), use_model_prior=True)
    assert method["warmup_min"] == 10
    assert method["cooldown_min"] == 5
    blocks = engine._blocks(method, 4, evidence, SETTINGS)
    assert [b["kind"] for b in blocks] == ["WARMUP", "WORK", "COOLDOWN"]
    assert [b["duration_min"] for b in blocks] == [10, 4, 5]
    assert blocks[0]["target_hr_bpm"] == 140
    assert blocks[1]["target_hr_bpm"] == 180
    assert engine._blocks(method, 4 * .6, evidence, SETTINGS) == []
