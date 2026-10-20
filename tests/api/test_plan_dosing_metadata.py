"""Frozen plan metadata may reference only an equal local dosing value."""
from copy import deepcopy

import pytest

from apps.api.training_plan_engine import _compact_duplicate_dosing_cards


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
