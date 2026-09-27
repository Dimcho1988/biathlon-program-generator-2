from copy import deepcopy
from datetime import date, timedelta
import json

from apps.api import learning_service as service
from biathlon.constants import COMPONENTS

TODAY = date(2026, 9, 27)


def episode(i=0):
    return {"id": str(i), "end": (TODAY-timedelta(days=14*(80-i))).isoformat(),
            "dose": {z: {"baseline_q": 200., "baseline_minutes": 250.} for z in COMPONENTS},
            "outcomes": [{"source": "TI", "scope": "GLOBAL", "comparison_key": "Run:stable:GLOBAL", "before_value": 10.}]}


def recent(q=210.):
    return {"components": {z: {"weekly_q": q, "weekly_minutes": q/.8} for z in COMPONENTS}}


def test_support_rejects_four_times_volume_even_same_relative_training_change():
    e = episode()
    assert service.local_evidence([e], recent(), [], TODAY) == [e]
    assert service.local_evidence([e], recent(800), [], TODAY) == []


def test_support_rejects_new_exposure_and_large_effort_change():
    r = recent()
    r["components"]["Z4"]["weekly_q"] = 0
    assert service.local_evidence([episode()], r, [], TODAY) == []
    r = recent()
    r["components"]["Z2"]["weekly_minutes"] *= 2
    assert service.local_evidence([episode()], r, [], TODAY) == []


def test_changed_fitness_level_prevents_false_precision():
    ti = [{"sport": "Run", "local_date": (TODAY-timedelta(days=1)).isoformat(),
           "index": {"admission": {"status": "ACCEPTED"}, "comparison_key": "stable", "general": {"valid": True, "index": 7.}}}]
    assert service.local_evidence([episode()], recent(), ti, TODAY) == []
    ti[0]["index"]["general"]["index"] = 9.5
    assert service.local_evidence([episode()], recent(), ti, TODAY)


def test_memory_byte_budget_is_deterministic_and_fits_same_replay_data():
    es = [episode(i) for i in range(78)]
    for e in es:
        e["outcomes"] *= 40
    a = service.bounded_episodes(es)
    assert a == service.bounded_episodes(list(reversed(es)))
    assert a == service.bounded_episodes(deepcopy(a))
    assert a[-1]["id"] == "77"
    assert len(a) < 78
    assert len(json.dumps(a, ensure_ascii=False, separators=(",", ":")).encode()) <= service.EPISODE_MEMORY_BYTES


def test_oversized_episode_cannot_break_persistence():
    e = episode()
    e["oversized"] = "я" * service.EPISODE_MEMORY_BYTES
    assert service.bounded_episodes([e]) == []
