from copy import deepcopy
import pytest
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


def test_small_known_zero_is_local_support_but_missing_or_major_redistribution_is_not():
    e, r = episode(), recent()
    e["dose"]["Z5"].update(baseline_q=0., baseline_minutes=0.)
    r["components"]["Z5"].update(weekly_q=10., weekly_minutes=5.)
    assert service.local_evidence([e], r, [], TODAY) == [e]
    r["components"]["Z5"].update(weekly_q=200., weekly_minutes=100.)
    assert service.local_evidence([e], r, [], TODAY) == []
    r["components"]["Z5"]["weekly_q"] = None
    assert service.local_evidence([e], r, [], TODAY) == []
    r["components"]["Z5"].update(weekly_q=0., weekly_minutes=None)
    assert service.local_evidence([e], r, [], TODAY) == []


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


@pytest.mark.parametrize("component,expiry_offset",[("Z3",-1),("Z1",11)])
def test_service_persists_expired_or_now_ineligible_episode_marker_across_neutral_refreshes(monkeypatch,component,expiry_offset):
    """Exercise the real service memory writeback, not a test-only fallback."""
    from apps.api import learning_methods
    from apps.api.management_store import ManagementStore
    from tests.api.test_component_load_learning import projected_fixture
    from tests.api.test_load_progression import configured
    from tests.api.test_recovery_policy import fixture
    repo,_,_,_=projected_fixture()
    data=fixture(returned=None,follow_days=25)
    source=data['source']
    # The recovery dose is lower than loading but locally comparable to base.
    for a in source['activities']:
        if a['date']>str(data['today']-timedelta(days=25)):
            for z in a['zones']:
                z.update(equivalent_time_min=10.,raw_time_min=10.)
    envelope={'generation_id':'pinned','revision':1,'activities':[], 'snapshot_payload':{'load_history':source}}
    monkeypatch.setattr(service,'build_history',lambda **kwargs:deepcopy(data['history']))
    memory={}
    monkeypatch.setattr(ManagementStore,'learning_memory',lambda *args:deepcopy(memory))
    monkeypatch.setattr(learning_methods,'assess_methods',lambda **kwargs:{'preferences':[],'memory':{},'summary':{}})
    profile=configured(individual_learning={'mode':'CONTROL','max_volume_step_percent':20.,'max_intensity_step':.02,'exploration_enabled':True})
    phase={'phases':[{'kind':'GENERAL_PREPARATION','start_date':'2026-01-01','end_date':'2026-12-31'}]}
    first=service.context(repo,'athlete',profile,source,data['rows'],data['today'],envelope=envelope,entries=[],periodization=phase)
    memory.update(first['memory'])
    # A completed prior decision is an immutable consumed-exposure marker.
    episode=first['memory']['episodes'][-1]
    closed={'source_episode_id':episode['id'],'component':component,'mode':'CONTROL','config':profile['individual_learning'],'effective_from':str(data['today']-timedelta(days=2)),'expires_on':str(data['today']+timedelta(days=expiry_offset)),'baseline_actual_weekly_q':140.,'step':-.2}
    memory['decision']=deepcopy(closed)
    for _ in range(2):
        report=service.context(repo,'athlete',profile,source,data['rows'],data['today'],envelope=envelope,entries=[],periodization=phase)
        assert report.get('decision') is None
        assert report['memory']['decision']==closed
        memory.clear()
        memory.update(report['memory'])
