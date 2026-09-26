"""Coverage and race specificity without changing anchored cycle or dose gates."""
from datetime import timedelta

import pytest

from apps.api import training_plan_engine as engine
from biathlon import mesocycle_focus
from tests.api.test_mesocycle_focus import configured, state
from tests.api.test_training_plan_engine import NOW, TODAY, Repository, reference_speed


def at(offset):
    return (TODAY + timedelta(days=offset)).isoformat()


def calendar(*intervals, taper=(), unavailable=()):
    return {
        "phases": [
            {"kind": kind, "start_date": at(left), "end_date": at(right)}
            for kind, left, right in intervals
        ],
        "taper_windows": [
            {"start_date": at(left), "end_date": at(right)} for left, right in taper
        ],
        "calendar_context": [
            {"event_type": "UNAVAILABLE", "start_date": at(left), "end_date": at(right)}
            for left, right in unavailable
        ],
    }


def profile(end=139):
    return configured(program_start=at(0), program_end=at(end))


@pytest.mark.parametrize("count,strength", [(1, True), (2, True), (1, False), (2, False)])
def test_reduced_focus_count_covers_every_enabled_component_before_repeating(count, strength):
    p = profile(200)
    p["strength_enabled"] = strength
    p["planning_controls"]["automatic_focus_count"] = count
    enabled = ["Z1", "Z3", *(["STR"] if strength else []), "Z2", "Z4", "Z5"]
    count_cycles = (len(enabled) + count - 1) // count
    choices = [state(p, 28 * n)["accents"] for n in range(count_cycles)]
    assert all(0 < len(choice) <= count for choice in choices)
    assert [zone for choice in choices for zone in choice] == enabled
    assert state(p, 28 * count_cycles)["accents"] == choices[0]


def test_general_two_blocks_cover_all_components_without_inventing_a_strength_replacement():
    p = profile()
    p["strength_enabled"] = False
    first, second = state(p, 0), state(p, 28)
    assert first["accents"] == ["Z1", "Z3"]
    assert second["accents"] == ["Z2", "Z4", "Z5"]
    assert set(first["accents"] + second["accents"]) == set(mesocycle_focus.COMPONENTS) - {"STR"}


def test_short_general_period_reports_missing_template_coverage_instead_of_claiming_all_components():
    p = profile(83)
    phases = calendar(("GENERAL_PREPARATION", 0, 27), ("SPECIAL_PREPARATION", 28, 55),
                      ("PRECOMPETITION", 56, 83))
    first = state(p, 0, periodization=phases)
    assert first["focus_stage"] == "GENERAL_COVERAGE"
    assert set(first["focus_coverage_missing"]) == {"Z2", "Z4", "Z5"}
    extended = calendar(("GENERAL_PREPARATION", 0, 55), ("SPECIAL_PREPARATION", 56, 83))
    assert state(p, 0, periodization=extended)["focus_coverage_missing"] == []


@pytest.mark.parametrize("minutes,band", [(240, ["Z1", "Z2", "Z3"]), (120, ["Z3", "Z2", "Z4"]),
                                         (22, ["Z4", "Z3", "Z5"]), (4, ["Z5", "Z4", "Z3"])])
def test_final_special_and_precompetition_keep_race_priority_and_unique_neighbor_roles(minutes, band):
    p = profile()
    p["race_duration_min"] = minutes
    phases = calendar(("GENERAL_PREPARATION", 0, 27), ("SPECIAL_PREPARATION", 28, 83),
                      ("PRECOMPETITION", 84, 111), ("COMPETITION", 112, 139))
    early = state(p, 28, "SPECIAL_PREPARATION", periodization=phases)
    late = state(p, 56, "SPECIAL_PREPARATION", periodization=phases)
    pre = state(p, 84, "PRECOMPETITION", periodization=phases)
    assert late["accents"] == pre["accents"] == band
    assert late["race_band"] == pre["race_band"] == band
    assert early["focus_stage"] == "SPECIAL_FOUNDATION"
    assert late["focus_stage"] == "SPECIAL_RACE_BAND"
    assert pre["focus_stage"] == "PRECOMPETITION_RACE_BAND"
    assert (early["phase_mesocycle_number"], early["phase_mesocycle_count"]) == (1, 2)
    assert (late["phase_mesocycle_number"], late["phase_mesocycle_count"]) == (2, 2)
    assert late["component_indices"] == dict(zip(band, [1.6, 1.5, 1.2]))
    assert state(p, 112, "COMPETITION", periodization=phases)["accents"] == band[:1]


def test_general_ownership_survives_phase_boundary_and_short_special_uses_its_only_loading_block():
    p = profile(83)
    phases = calendar(("GENERAL_PREPARATION", 0, 19), ("SPECIAL_PREPARATION", 20, 31),
                      ("PRECOMPETITION", 32, 83))
    before = state(p, 0, periodization=phases)
    crossing = state(p, 20, "SPECIAL_PREPARATION", periodization=phases)
    final = state(p, 28, "SPECIAL_PREPARATION", periodization=phases)
    assert before["accents"] == crossing["accents"]
    assert crossing["focus_period"] == "GENERAL_PREPARATION"
    assert final["accents"] == ["Z4", "Z3", "Z5"]
    assert (final["phase_mesocycle_number"], final["phase_mesocycle_count"]) == (1, 1)
    assert state(p, 32, "PRECOMPETITION", periodization=phases)["mesocycle_accents"] == final["mesocycle_accents"]


def test_first_partial_special_after_entry_is_counted_once_and_not_restarted():
    p = profile(83)
    phases = calendar(("RE_ENTRY", 0, 4), ("SPECIAL_PREPARATION", 5, 55), ("PRECOMPETITION", 56, 83))
    first = state(p, 5, "SPECIAL_PREPARATION", periodization=phases)
    same = state(p, 18, "SPECIAL_PREPARATION", periodization=phases)
    second = state(p, 28, "SPECIAL_PREPARATION", periodization=phases)
    assert (first["phase_mesocycle_number"], first["phase_mesocycle_count"]) == (1, 2)
    assert first["mesocycle_accents"] == same["mesocycle_accents"] == ["Z4", "Z3", "STR"]
    assert (second["phase_mesocycle_number"], second["phase_mesocycle_count"]) == (2, 2)
    assert second["mesocycle_accents"] == ["Z4", "Z3", "Z5"]


def test_first_preparation_after_entry_owns_fragment_across_another_phase_change():
    p = profile(83)
    phases = calendar(("RE_ENTRY", 0, 4), ("GENERAL_PREPARATION", 5, 9),
                      ("SPECIAL_PREPARATION", 10, 55), ("PRECOMPETITION", 56, 83))
    first = state(p, 5, "GENERAL_PREPARATION", periodization=phases)
    crossing = state(p, 15, "SPECIAL_PREPARATION", periodization=phases)
    next_block = state(p, 28, "SPECIAL_PREPARATION", periodization=phases)
    assert crossing["focus_period"] == first["focus_period"] == "GENERAL_PREPARATION"
    assert crossing["mesocycle_accents"] == first["mesocycle_accents"]
    assert (first["phase_mesocycle_number"], first["phase_mesocycle_count"]) == (1, 1)
    assert (next_block["phase_mesocycle_number"], next_block["phase_mesocycle_count"]) == (1, 1)
    assert next_block["mesocycle_accents"] == ["Z4", "Z3", "Z5"]


@pytest.mark.parametrize("blocked_by", ["taper", "unavailable", "directive", "stress_recovery"])
def test_final_unusable_block_does_not_take_the_race_band_from_previous_loading_block(blocked_by):
    p = profile(139)
    kwargs = {blocked_by: [(84, 111)]} if blocked_by in {"taper", "unavailable"} else {}
    phases = calendar(("GENERAL_PREPARATION", 0, 27), ("SPECIAL_PREPARATION", 28, 111),
                      ("PRECOMPETITION", 112, 139), **kwargs)
    if blocked_by in {"directive", "stress_recovery"}:
        p["planning_controls"]["cycles"] = [{
            "name": "Треньорски блок", "kind": "RECOVERY" if blocked_by == "directive" else "STRESS",
            "start_date": at(84), "end_date": at(111 if blocked_by == "directive" else 90),
            "accents": ["Z2"], "target_index": .8 if blocked_by == "directive" else 1.3,
            "volume_factor": .8 if blocked_by == "directive" else 1.1,
            "recovery_days": 14,
        }]
    early = state(p, 28, "SPECIAL_PREPARATION", periodization=phases)
    last_loading = state(p, 56, "SPECIAL_PREPARATION", periodization=phases)
    assert early["accents"] == ["Z4", "Z3", "STR"]
    assert last_loading["accents"] == ["Z4", "Z3", "Z5"]
    assert last_loading["phase_mesocycle_count"] == 2
    if blocked_by in {"taper", "unavailable"}:
        tail = state(p, 84, "SPECIAL_PREPARATION", periodization=phases)
        assert tail["mesocycle_accents"] == last_loading["mesocycle_accents"]
        assert tail["phase_mesocycle_number"] is None


def test_complete_loading_week_is_preferred_over_one_day_at_end_of_special_phase():
    p = profile(111)
    phases = calendar(("GENERAL_PREPARATION", 0, 27), ("SPECIAL_PREPARATION", 28, 56),
                      ("PRECOMPETITION", 57, 111))
    full = state(p, 28, "SPECIAL_PREPARATION", periodization=phases)
    short = state(p, 56, "SPECIAL_PREPARATION", periodization=phases)
    assert full["accents"] == ["Z4", "Z3", "Z5"]
    assert full["focus_stage"] == short["focus_stage"] == "SPECIAL_RACE_BAND"
    assert short["mesocycle_accents"] == full["mesocycle_accents"]


def test_special_period_with_no_loading_opportunity_does_not_claim_a_developing_race_block():
    p = profile(55)
    phases = calendar(("SPECIAL_PREPARATION", 0, 55), taper=[(0, 55)])
    for offset in (0, 28):
        s = state(p, offset, "SPECIAL_PREPARATION", periodization=phases)
        assert s["focus_stage"] == "SPECIAL_FOUNDATION"
        assert s["phase_mesocycle_count"] == 0
        assert s["phase_mesocycle_number"] is None


def test_separate_special_phases_each_have_their_own_final_race_block():
    p = profile(258)
    phases = calendar(("GENERAL_PREPARATION", 0, 27), ("SPECIAL_PREPARATION", 28, 83),
                      ("PRECOMPETITION", 84, 111), ("COMPETITION", 112, 118),
                      ("TRANSITION", 119, 139), ("GENERAL_PREPARATION", 140, 167),
                      ("SPECIAL_PREPARATION", 168, 223), ("PRECOMPETITION", 224, 251),
                      ("COMPETITION", 252, 258))
    for start in (28, 168):
        early = state(p, start, "SPECIAL_PREPARATION", periodization=phases)
        late = state(p, start + 28, "SPECIAL_PREPARATION", periodization=phases)
        assert early["mesocycle_accents"] == ["Z4", "Z3", "STR"]
        assert late["mesocycle_accents"] == ["Z4", "Z3", "Z5"]
        assert (early["phase_mesocycle_number"], early["phase_mesocycle_count"]) == (1, 2)
        assert (late["phase_mesocycle_number"], late["phase_mesocycle_count"]) == (2, 2)


def test_missing_race_duration_does_not_claim_an_established_race_band():
    p = profile()
    p["race_duration_min"] = None
    s = state(p, 0, "PRECOMPETITION")
    assert s["race_component"] is None
    assert s["race_band"] == []


def test_manual_and_hybrid_priorities_survive_new_precompetition_band():
    p = profile()
    p["planning_controls"].update(accent_mode="MANUAL", accents=["STR"])
    assert state(p, 0, "PRECOMPETITION")["accents"] == ["STR"]
    p["planning_controls"].update(accent_mode="HYBRID", accents=["Z2"], accent_limit=2)
    assert state(p, 0, "PRECOMPETITION")["accents"] == ["Z2", "Z4"]


def test_competition_start_inside_precompetition_anchor_reduces_to_race_component():
    p = profile(111)
    phases = calendar(("GENERAL_PREPARATION", 0, 27), ("SPECIAL_PREPARATION", 28, 55),
                      ("PRECOMPETITION", 56, 90), ("COMPETITION", 91, 111))
    pre = state(p, 84, "PRECOMPETITION", periodization=phases)
    race = state(p, 91, "COMPETITION", periodization=phases)
    assert pre["accents"] == ["Z4", "Z3", "Z5"]
    assert race["accents"] == ["Z4"]
    assert race["focus_stage"] == race["focus_period"] == "COMPETITION"
    assert race["mesocycle_start"] == pre["mesocycle_start"]


def test_precompetition_neighbor_can_receive_real_session_without_bypassing_capacity_or_race_gates(monkeypatch):
    """Exercise the actual catalog, Recovery, allocation and session construction.

    The athlete has recent Z3/Z5 exposure, but the reference-only speed model
    cannot establish Z5 capacity. A race-band label must admit Z3 to dosing,
    while neither that label nor the missing Z5 capacity can override a gate.
    """
    monkeypatch.setattr(engine.model_service, "speed_view", reference_speed)
    results = {}
    for period, duration in (("PRECOMPETITION", 22), ("COMPETITION", 22), ("PRECOMPETITION", None)):
        p = profile(55)
        p.update(race_duration_min=duration, age_years=23, training_experience_years=10)
        p["planning_controls"]["sessions_per_week"] = 7
        repo = Repository()
        repo.events = []
        monkeypatch.setattr(engine, "build_periodization", lambda *args, phase=period, **kwargs: calendar((phase, 0, 55)))
        results[(period, duration)] = engine.generate_plan(repo, "athlete", p, start_date=TODAY, now=NOW)

    preparation = results[("PRECOMPETITION", 22)]
    neighboring_sessions = [(day, session) for day in preparation["days"] for session in day["sessions"]
                            if session["zone"] == "Z3" and session["purpose"] == "BUILDING"]
    assert neighboring_sessions
    for day, session in neighboring_sessions:
        assert day["cycle"]["race_component"] == "Z4"
        assert "Z3" in day["cycle"]["mesocycle_accents"]
        for zone, effective in session["canonical_effective_load"].items():
            if effective > 0:
                assert day["readiness_before"][zone] >= 90
                assert effective <= day["load_budget"]["components"][zone]["deficit_effective"] + .002

    neighbor_ids = {"END-THR-TIME-01-FLEX", "ONFLOWS-CONTROLLED-Z5-V2"}
    rejected = [item for day in preparation["days"] for item in day["rejected_alternatives"]]
    assert not any(item["code"] == "RACE_COMPONENT_PRIORITY" and item["method_id"] in neighbor_ids for item in rejected)
    assert any(item["method_id"] == "ONFLOWS-CONTROLLED-Z5-V2" and item["code"] == "CAPACITY_UNAVAILABLE" for item in rejected)
    assert not any(session["zone"] == "Z5" for day in preparation["days"] for session in day["sessions"])

    for key in (("COMPETITION", 22), ("PRECOMPETITION", None)):
        plan = results[key]
        rejected_ids = {item["method_id"] for day in plan["days"] for item in day["rejected_alternatives"]
                        if item["code"] == "RACE_COMPONENT_PRIORITY"}
        assert neighbor_ids <= rejected_ids
        assert not any(session["zone"] in {"Z3", "Z5"} and session["purpose"] == "BUILDING"
                       for day in plan["days"] for session in day["sessions"])
