"""Frozen plan metadata may reference only an equal local dosing value."""
from copy import deepcopy
import json

import pytest

from apps.api.training_plan_engine import _compact_duplicate_dosing_cards, _compact_goal_cycles, compact_plan_metadata


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


@pytest.mark.parametrize("lookahead", [False, True])
@pytest.mark.parametrize("locked,changed", [(False, None), (True, None), (False, "guidance"), (False, "type")])
def test_combined_parent_references_preserve_every_frozen_value_and_both_json_aliases(lookahead, locked, changed):
    parent = {"id": "PARENT", "guidance": {"execution": "Complete source instructions"}, "ratio": 1}
    block_card = deepcopy(parent)
    if changed == "guidance":
        block_card["guidance"]["execution"] = "Different frozen instructions"
    elif changed == "type":
        block_card["ratio"] = True
    session = {"method_library": {"id": "COMBO", "components": [deepcopy(parent)]},
               "blocks": [{"kind": "WORK", "duration_min": 3., "parent_method_card": block_card}]}
    day = {"locked": locked, "sessions": [session], "session": deepcopy(session)}
    plan = {"days": [] if lookahead else [day], "lookahead_days": [day] if lookahead else []}
    before = deepcopy(plan)
    compact_plan_metadata(plan)
    if locked or changed:
        assert plan == before
    else:
        restored = deepcopy(plan)
        for candidate in [day["session"], *day["sessions"]]:
            assert candidate["blocks"][0]["parent_method_card_ref"] == "method_library.components[0]"
            assert "parent_method_card" not in candidate["blocks"][0]
        target_day = (restored["lookahead_days"] if lookahead else restored["days"])[0]
        for candidate in [target_day["session"], *target_day["sessions"]]:
            candidate["blocks"][0].pop("parent_method_card_ref")
            candidate["blocks"][0]["parent_method_card"] = deepcopy(candidate["method_library"]["components"][0])
        assert json.dumps(restored, sort_keys=True) == json.dumps(before, sort_keys=True)
        again = deepcopy(plan)
        compact_plan_metadata(plan)
        assert plan == again
    assert parent == {"id": "PARENT", "guidance": {"execution": "Complete source instructions"}, "ratio": 1}


def test_cached_plan_publishes_below_four_mib_without_rewriting_approved_evidence(monkeypatch):
    from tests.api.test_management_final_read_phase import PublicationHTTP
    from biathlon.constants import COMPONENTS

    transport = PublicationHTTP(shared_state="READY")
    parent = {"id": "PARENT", "guidance": {"execution": "Preserved guidance. " * 1000}}
    session = {"method_id": "COMBO", "title": "Combined fixture", "sport": "Run", "zone": "Z3",
        "purpose": "MAINTENANCE", "total_minutes": 30., "is_key_session": False,
        "canonical_effective_load": dict.fromkeys(COMPONENTS, 0.),
        "direct_equivalent_minutes": dict.fromkeys(COMPONENTS, 0.),
        "method_library": {"id": "COMBO", "components": [parent]},
        "blocks": [{"kind": "WORK", "duration_min": 1., "parent_method_card": deepcopy(parent)} for _ in range(30)]}
    day = transport.raw_plan["days"][1]
    day.update(status="TRAINING", session=session, sessions=[session])
    transport.raw_plan["lookahead_days"] = [deepcopy(day)]
    approved = deepcopy(transport.raw_plan)
    size = lambda value: len(json.dumps(value, ensure_ascii=False, allow_nan=False).encode())
    assert size(approved) < 4*1024*1024 < 2*size(approved)
    response = transport.run(monkeypatch)
    assert response.status_code == 200
    payload = transport.record["payload"]
    assert payload["plan"] == approved
    assert transport.raw_plan == approved  # The durable READY result is not mutated.
    assert size(payload) < 4*1024*1024
    for saved_day in [payload["proposal"]["days"][1], *payload["proposal"]["lookahead_days"]]:
        for candidate in [saved_day["session"], *saved_day["sessions"]]:
            assert candidate["method_library"]["components"][0] == parent
            assert all(block.get("parent_method_card_ref") == "method_library.components[0]" for block in candidate["blocks"])
