"""Alternative schedules retain exact histories, doses and input isolation."""
from copy import deepcopy
from datetime import timedelta
from unittest.mock import Mock

import pytest

from apps.api import training_plan_engine as engine
from apps.api.component_load_projection import ComponentLoadRefreshRequired
from apps.api.read_session import ReadSession
from tests.api.test_training_plan_engine import NOW, TODAY, Repository, profile, reference_speed


@pytest.fixture(autouse=True)
def fixed_speed(monkeypatch):
    monkeypatch.setattr(engine.model_service, "speed_view", reference_speed)


def inputs():
    repository = Repository()
    phase = ReadSession(repository)
    return repository, phase, profile(), phase.athlete_settings("athlete"), deepcopy(repository.envelope)


def test_scheduling_choices_reuse_history_without_exposing_retained_maps(monkeypatch):
    repository, phase, body, settings, envelope = inputs()
    original = deepcopy((body, settings, envelope, repository.envelope))
    expected = engine.planning_source_details(repository, "athlete", body, settings, envelope, TODAY)
    calculate = Mock(wraps=engine._planning_source_details)
    monkeypatch.setattr(engine, "_planning_source_details", calculate)
    first = engine.planning_source_details(phase, "athlete", body, settings, envelope, TODAY)
    first.source["daily"][0]["effective_load"] = -123.
    for policy in ({"optional_easy_fraction": 0.}, {"component_gap_first": True}):
        result = engine.planning_source_details(phase, "athlete", {**body, "_allocation_policy": policy},
                                               settings, envelope, TODAY)
        assert result == expected
    assert calculate.call_count == 1
    assert (body, settings, envelope, repository.envelope) == original


@pytest.mark.parametrize("change", ["profile", "settings", "analysis", "speed", "day", "phase"])
def test_history_reuse_observes_every_non_scheduling_input(monkeypatch, change):
    repository, phase, body, settings, envelope = inputs()
    views = {"Run": reference_speed(phase, "athlete", "Run")}
    engine.planning_source_details(phase, "athlete", body, settings, envelope, TODAY, speed_by_sport=views)
    day = TODAY
    if change == "profile":
        body["training_experience_years"] = 12.
    elif change == "settings":
        settings.hrmax_bpm += 1
    elif change == "analysis":
        envelope["revision"] += 1
    elif change == "speed":
        views["Run"]["source_revision"] += 1
    elif change == "day":
        day += timedelta(days=1)
    else:
        phase = ReadSession(repository)
    expected = engine.planning_source_details(repository, "athlete", body, settings, envelope, day,
                                               speed_by_sport=views)
    calculate = Mock(wraps=engine._planning_source_details)
    monkeypatch.setattr(engine, "_planning_source_details", calculate)
    assert engine.planning_source_details(phase, "athlete", body, settings, envelope, day,
                                          speed_by_sport=views) == expected
    assert calculate.call_count == 1


def test_alternative_schedules_share_only_exact_recovery_arithmetic():
    repository, phase, body, _, _ = inputs()
    original = deepcopy((repository.envelope, body))
    cache = {}
    for policy in ({}, {"optional_easy_fraction": 0., "component_gap_first": True}):
        candidate = {**body, "_allocation_policy": policy}
        expected = engine.generate_plan(ReadSession(repository), "athlete", candidate,
                                        start_date=TODAY+timedelta(days=1), now=NOW)
        actual = engine.generate_plan(phase, "athlete", candidate,
            start_date=TODAY+timedelta(days=1), now=NOW, _recovery_calculation_cache=cache)
        assert actual == expected
        assert (repository.envelope, body) == original
    assert all(len(values) <= 4096 for values in cache.values())


def test_changed_invalid_ledger_cannot_reuse_a_previously_valid_projection():
    _, phase, body, settings, envelope = inputs()
    engine.planning_source_details(phase, "athlete", body, settings, envelope, TODAY)
    envelope["snapshot_payload"]["load_history"]["daily"][0]["effective_load"] += 1.
    with pytest.raises(ComponentLoadRefreshRequired):
        engine.planning_source_details(phase, "athlete", body, settings, envelope, TODAY)


def test_technical_references_match_canonical_windows_and_are_bounded(monkeypatch):
    repository = Repository()
    rows = repository.envelope["snapshot_payload"]["load_history"]["daily"]
    settings = repository.settings
    calculate = Mock(wraps=engine._causal_tref)
    monkeypatch.setattr(engine, "_causal_tref", calculate)
    reference = engine._technical_reference_reuse()
    revised = deepcopy(rows)
    revised[-20]["effective_load"] += 45.
    cases = [(rows, TODAY), (rows, TODAY+timedelta(days=1)),
             (revised, TODAY), (rows[:-10], TODAY), ([], TODAY)]
    for history, day in cases:
        expected = engine._canonical_load([], settings, history, day)[2]
        assert reference(history, day) == expected
        reference(history, day)["Z1"] = -1.
        assert reference(history, day) == expected
    # Shifting the window never merges uncovered days with explicit zeroes.
    for offset in range(300):
        one = [{"date": TODAY.isoformat(), "zone": "Z1", "effective_load": offset/10.}]
        assert reference(one, TODAY+timedelta(days=1)) == engine._canonical_load(
            [], settings, one, TODAY+timedelta(days=1))[2]
    calculate.reset_mock()
    reference(one, TODAY+timedelta(days=1))
    assert calculate.call_count == 0
    reference([{**one[0], "effective_load": 0.}], TODAY+timedelta(days=1))
    assert calculate.call_count == len(engine.COMPONENTS)
