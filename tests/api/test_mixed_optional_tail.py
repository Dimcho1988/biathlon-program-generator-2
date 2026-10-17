"""Optional easy finishing work cannot change a complete mixed primary dose."""
from copy import deepcopy

import pytest

from apps.api import training_plan_engine as engine
from biathlon.training_methods import resolved_methods
from tests.api.test_management_schedule import body
from tests.api.test_management_v2 import interval
from tests.api.test_training_plan_engine import Repository, TODAY


def mixed_fixture(zone, *, ratio=59.5471667, variant="REPETITIONS"):
    profile = body()
    profile["interval_profiles"] = [
        interval(zone="Z4"),
        interval(zone="Z5", continuous_capacity_min=10,
                 work_seconds=30, recovery_seconds=60),
    ]
    method = next(method for method in resolved_methods(profile)
                  if method.get("mixed_component") and method["zone"] == zone
                  and method.get("mixed_variant") == variant)
    repo = Repository()
    evidence = engine.capacity_for(method, repo.settings, None, (None, [], []), TODAY)
    assert evidence is not None, "These catalog methods have explicit supported effort references."
    evidence.update(
        secondary_capacity={"capacity_minutes": 357.283, "target_hr_bpm": 115.,
                            "target_speed_kmh": None},
        easy_to_primary_ratio=ratio,
    )
    return method, evidence, repo


def primary_work(blocks, zone):
    return sum(block["duration_min"] for block in blocks
               if block["kind"] == "WORK" and block["zone"] == zone)


def easy_work(blocks):
    return sum(block["duration_min"] for block in blocks
               if block["kind"] == "WORK" and block["zone"] == "Z1")


def without_optional_tail(blocks):
    return [block for block in blocks
            if not (block["kind"] == "WORK" and block["zone"] == "Z1")]


@pytest.mark.parametrize("zone", ["Z2", "Z3", "Z4", "Z5"])
def test_absent_or_none_easy_cap_preserves_the_existing_mixed_ratio(zone):
    method, evidence, repo = mixed_fixture(zone)
    original_method, original_evidence = deepcopy(method), deepcopy(evidence)
    work = max(method["min_work_min"], 3.)
    default = engine._blocks(method, work, evidence, repo.settings)
    explicit_none = engine._blocks(method, work, {**evidence, "easy_work_cap_minutes": None}, repo.settings)
    assert default == explicit_none
    assert easy_work(default) == pytest.approx(
        primary_work(default, zone) * evidence["easy_to_primary_ratio"], abs=.00001)
    assert method == original_method and evidence == original_evidence


@pytest.mark.parametrize("zone", ["Z2", "Z3", "Z4", "Z5"])
def test_zero_easy_cap_preserves_whole_primary_work_and_every_mandatory_block(zone):
    method, evidence, repo = mixed_fixture(zone)
    work = max(method["min_work_min"], 3.)
    full = engine._blocks(method, work, evidence, repo.settings)
    core = engine._blocks(method, work, {**evidence, "easy_work_cap_minutes": 0.}, repo.settings)
    assert core == without_optional_tail(full)
    assert easy_work(core) == 0
    assert [(block["kind"], block["duration_min"]) for block in core[:2]] == [
        ("WARMUP", 15.), ("PREPARATION", 3.),
    ]
    assert (core[-1]["kind"], core[-1]["duration_min"]) == ("COOLDOWN", 5.)
    assert primary_work(core, zone) == primary_work(full, zone) > 0
    rests = [block for block in core if block["kind"] == "RECOVERY"]
    assert rests, "This fixture uses repeated work, so a complete core needs its full rests."
    if method["structure"] == "METABOLIC_INTERVALS":
        prescription = method["interval_profile"]
        repetitions = [block for block in core if block["kind"] == "WORK"]
        assert len(repetitions) >= prescription["min_repetitions"]
        assert all(block["duration_s"] == prescription["work_seconds"] for block in repetitions)
        assert all(block["duration_s"] == prescription["recovery_seconds"] for block in rests)
        assert len(rests) == len(repetitions) - 1

    # The same real fixture history supplies the canonical spill reference for
    # both versions. Removing a tail must never create history or free warmups.
    history = repo.envelope["snapshot_payload"]["load_history"]["daily"]
    before = deepcopy(history)
    full_q, full_e, technical = engine._canonical_load(full, repo.settings, history, TODAY)
    core_q, core_e, _ = engine._canonical_load(
        core, repo.settings, history, TODAY, technical_reference=technical)
    assert history == before
    assert core_q[zone] == full_q[zone] > 0
    assert 0 < core_q["Z1"] < full_q["Z1"]
    assert all(core_e[component] <= value + 1e-9 for component, value in full_e.items())
    assert sum(block["duration_s"] for block in core) == pytest.approx(
        60 * sum(block["duration_min"] for block in core))


@pytest.mark.parametrize("cap", [.375, 2., 1000.])
def test_positive_cap_only_trims_the_tail_from_realized_whole_repetitions(cap):
    method, evidence, repo = mixed_fixture("Z5")
    requested = 1.1  # Four complete 15-second repetitions realize one minute.
    original = deepcopy(evidence)
    full = engine._blocks(method, requested, evidence, repo.settings)
    capped = engine._blocks(method, requested, {**evidence, "easy_work_cap_minutes": cap}, repo.settings)
    actual = primary_work(capped, "Z5")
    assert actual == 1. and actual < requested
    assert without_optional_tail(capped) == without_optional_tail(full)
    assert easy_work(capped) == pytest.approx(min(actual * evidence["easy_to_primary_ratio"], cap), abs=.00001)
    assert easy_work(capped) <= easy_work(full)
    assert evidence == original


@pytest.mark.parametrize("readiness_factor", [.5, 1.])
def test_optional_tail_cannot_change_the_five_percent_primary_minimum(readiness_factor):
    method, evidence, repo = mixed_fixture("Z3", variant="STEADY")
    evidence.update(capacity_minutes=160., readiness_dose_factor=readiness_factor)
    assert method["minimum_fraction"] == .05
    required_primary = .05 * 160. * readiness_factor
    minima = [engine._minimum_work(method, {**evidence, "easy_work_cap_minutes": cap}, repo.settings)
              for cap in (None, 0., 2., 1000.)]
    assert minima == [required_primary] * len(minima)
    for cap in (None, 0., 2., 1000.):
        shaped = {**evidence, "easy_work_cap_minutes": cap}
        complete = engine._blocks(method, required_primary, shaped, repo.settings)
        smaller = engine._blocks(method, required_primary - .5, shaped, repo.settings)
        assert engine._minimum_dose_usage(complete, shaped, "Z3", primary_only=True) == pytest.approx(.05 * readiness_factor)
        assert engine._minimum_dose_usage(smaller, shaped, "Z3", primary_only=True) < .05 * readiness_factor
