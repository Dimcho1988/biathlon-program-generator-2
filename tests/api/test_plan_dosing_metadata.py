"""Frozen plan metadata may reference only an equal local dosing value."""
from copy import deepcopy

import pytest

from apps.api.training_plan_engine import _compact_duplicate_dosing_cards, _compact_goal_cycles


@pytest.mark.parametrize("kind,locked,equal", [
    ("COACH_PROFILE", False, True),
    ("COACH_PROFILE", False, False),
    ("COACH_PROFILE", True, True),
    ("SOURCE_RECIPE", False, True),
])
def test_local_dosing_reference_preserves_frozen_values_and_guidance(kind, locked, equal):
    dosing = {"version": "dosing-v1", "capacity_ratio": 2.5}
    card = {"id": "METHOD", "registry_kind": kind, "dosing": deepcopy(dosing),
            "guidance": {"execution": "Complete instructions"},
            "original": {"protocol": "Original protocol"}, "adaptation": "Declared adaptation"}
    evidence = {"dosing": deepcopy(dosing) if equal else {**dosing, "capacity_ratio": 2.}}
    session = {"method_library": card, "dose_evidence": evidence}
    day = {"sessions": [session], "session": session, "locked": locked}
    before = deepcopy(day)
    _compact_duplicate_dosing_cards([day])
    if kind == "COACH_PROFILE" and not locked and equal:
        assert card["dosing_ref"] == "dose_evidence.dosing"
        assert "dosing" not in card
        restored = {k: v for k, v in card.items() if k != "dosing_ref"}
        restored["dosing"] = evidence["dosing"]
        assert restored == before["session"]["method_library"]
        assert evidence == before["session"]["dose_evidence"]
        assert day["session"] is day["sessions"][0]
        first = deepcopy(day)
        _compact_duplicate_dosing_cards([day])
        assert day == first
    else:
        assert day == before


@pytest.mark.parametrize("locked,equal", [(False, True), (True, True), (False, False)])
def test_goal_cycle_reference_is_lossless_and_never_mutates_shared_target(locked, equal):
    cycle = {"kind": "BUILD", "accents": ["Z3"], "wave_factor": 1.2}
    goal = {"target": 140., "cycle": deepcopy(cycle) if equal else {**cycle, "kind": "RECOVERY"}}
    original = deepcopy(goal)
    day = {"cycle": deepcopy(cycle), "locked": locked,
           "load_budget": {"components": {"Z3": {"goal_evidence": goal}}}}
    before = deepcopy(day)
    _compact_goal_cycles([day])
    assert goal == original
    if not locked and equal:
        compact = day["load_budget"]["components"]["Z3"]["goal_evidence"]
        assert compact["cycle_ref"] == "day.cycle"
        restored = {k: v for k, v in compact.items() if k != "cycle_ref"}
        restored["cycle"] = day["cycle"]
        assert restored == original
        again = deepcopy(day)
        _compact_goal_cycles([day])
        assert day == again
    else:
        assert day == before
