"""Planning skips recovery diagnostics without changing prescriptions or math."""
from copy import deepcopy
from datetime import timedelta

import pytest

from apps.api import training_plan_engine as engine
from biathlon import recovery_v2
from tests.api.test_load_progression import configured, observed
from tests.api.test_training_plan_engine import NOW, TODAY, Repository, profile, reference_speed


@pytest.mark.parametrize("scenario", ["empty", "rest", "sparse", "varied", "long_tail", "future"])
def test_current_only_matches_every_current_field_and_preserves_inputs(scenario):
    configs = recovery_v2.defaults()
    rows = []
    if scenario != "empty":
        ages = [0, 2, 4, 39, 40, 55] if scenario == "sparse" else range(55, -1, -1)
        for i, zone in enumerate(recovery_v2.ZONES):
            configs[zone].update(shape=1 + i, duration_coefficient=.7 + i / 3)
            for age in ages:
                dose = 0. if scenario == "rest" else (age * 17 + i * 11) % 73 / 7
                if scenario == "long_tail" and age == 0:
                    dose = 4000.
                rows.append({"date": (TODAY - timedelta(days=age)).isoformat(),
                             "zone": zone, "effective_load": dose})
        if scenario == "future":
            rows.append({"date": (TODAY + timedelta(days=3)).isoformat(),
                         "zone": "Z5", "effective_load": 9999.})
    original = deepcopy((rows, configs))
    full = recovery_v2.simulate(rows, configs, target=TODAY)
    compact = recovery_v2.simulate(rows, configs, target=TODAY, include_details=False)
    assert compact == {**full, "daily": [], "forecast": []}
    assert (rows, configs) == original


@pytest.mark.parametrize("rows", [
    [{"date": TODAY.isoformat(), "zone": "OTHER", "effective_load": 1.}],
    [{"date": TODAY.isoformat(), "zone": "Z1", "effective_load": float("nan")}],
    [{"date": TODAY.isoformat(), "zone": "Z1", "effective_load": -1.}],
    [{"date": TODAY.isoformat(), "zone": "Z1", "effective_load": 1.}] * 2,
])
def test_current_only_retains_scientific_input_validation(rows):
    with pytest.raises(ValueError) as original:
        recovery_v2.simulate(rows, target=TODAY)
    with pytest.raises(ValueError) as compact:
        recovery_v2.simulate(rows, target=TODAY, include_details=False)
    assert str(original.value) == str(compact.value)


def _planning_inputs(mode):
    if mode == "legacy":
        return Repository(), profile(), None
    repo, _, _ = observed()
    body = configured()
    locked = None
    if mode == "controls":
        body.pop("load_progression")
    elif mode == "estimated":
        from tests.api.test_planning_history_estimate import incomplete
        repo, _ = incomplete()
    elif mode == "double":
        from tests.api.test_management_schedule import body as scheduling_body, high_capacity_history
        repo = high_capacity_history()
        body = scheduling_body(sessions_per_week=8, double_threshold_days=[TODAY.weekday()],
                               double_threshold_components=["Z3"], accent_mode="MANUAL", accents=["Z3"])
    elif mode == "race":
        race = (TODAY + timedelta(days=3)).isoformat()
        repo.events[0].update(start_date=race, end_date=race)
        body["horizon_mode"] = "AUTO_CALENDAR"
    elif mode == "strength":
        body.update(strength_enabled=True)
    elif mode == "locked":
        seed = engine.generate_plan(repo, "athlete", body, start_date=TODAY, now=NOW)
        locked = seed["days"][0]
        assert locked["sessions"], "The fixture must exercise a preserved session"
    return repo, body, locked


@pytest.mark.parametrize("mode", ["legacy", "controls", "progression", "estimated", "double", "race", "strength", "locked"])
def test_complete_weekly_and_outlook_payloads_equal_full_recovery(monkeypatch, mode):
    monkeypatch.setattr(engine.model_service, "speed_view", reference_speed)
    repo, body, locked = _planning_inputs(mode)
    before = deepcopy((repo.envelope, body, locked))
    result = engine.generate_plan(repo, "athlete", body, start_date=TODAY, now=NOW, locked_day=locked)
    original = recovery_v2.simulate

    def full_recovery(*args, **kwargs):
        kwargs.pop("include_details", None)
        return original(*args, **kwargs)

    monkeypatch.setattr(recovery_v2, "simulate", full_recovery)
    reference = engine.generate_plan(repo, "athlete", body, start_date=TODAY, now=NOW, locked_day=locked)
    # Includes numerical readiness, day choices, horizons, budget checks,
    # long-term outlook, metadata and the complete scientific fingerprints.
    assert result == reference
    assert (repo.envelope, body, locked) == before
