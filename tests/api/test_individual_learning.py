from copy import deepcopy
from datetime import date, timedelta
import json
import math

import pytest

from biathlon import individual_learning as learner
from biathlon.constants import COMPONENTS


TODAY = date(2026, 9, 27)
CONFIG = {"mode": "CONTROL", "exploration_enabled": False, "max_volume_step_percent": 5., "max_intensity_step": .02}
CURRENT = {"latest_day": TODAY.isoformat(), "coverage": 60, "families": 3, "history_usable": True,
           "recovered": True, "stress_score": 45, "illness_hold": False, "lab_review": False, "execution_caution": False}


def episodes(n=16, slope=2., *, correlated=False, scope="GLOBAL", intensity=False):
    result = []
    for i in range(n):
        x = [-2., -1., 1., 2.][i % 4]
        end = TODAY-timedelta(days=14*(n-i))
        dose = {z: 0. for z in COMPONENTS}
        effort = {z: 0. for z in COMPONENTS if z != "STR"}
        (effort if intensity else dose)["Z1"] = x*(learner.INTENSITY_SCALE if intensity else learner.VOLUME_SCALE)
        if correlated:
            dose["Z2"] = dose["Z1"]
        result.append({"id": str(i), "start": (end-timedelta(days=13)).isoformat(), "end": end.isoformat(),
                       "observed_on": end.isoformat(), "quality": 1., "confounded": False,
                       "dose_change": dose, "intensity_change": effort,
                       "response": {"burden_delta": 0., "recovery_days": 2, "recovered": True},
                       "outcomes": [{"scope": scope, "change_percent": slope*x, "weight": 1., "source": "TEST"}]})
    return result


def decide(es, *, current=None, config=None, retained=None, phase="GENERAL_PREPARATION", taper=False, allowed=("Z1",), scales=None):
    return learner.decide(models=learner.fit(es, TODAY), episodes=es, current=current or CURRENT, today=TODAY,
                          config=config or CONFIG, allowed_components=allowed, phase=phase, taper=taper,
                          retained=retained, intensity_scale=scales)


def test_bayesian_update_is_symmetric_positive_covariance_and_replayable():
    es = episodes()
    a, b = learner.fit(es, TODAY), learner.fit(list(reversed(es)), TODAY)
    assert a == b
    model = a["GLOBAL"]["posterior"]
    assert model["mean"][1] > 1.5
    assert model["covariance"][1][1] < 1
    import numpy as np
    covariance = np.array(model["covariance"])
    assert np.allclose(covariance, covariance.T)
    assert min(np.linalg.eigvalsh(covariance)) > 0


def test_independent_later_validation_and_positive_bounded_action():
    report = decide(episodes())
    assert report["validation"]["status"] == "PASSED"
    assert report["validation"]["evaluated"] == 12
    assert report["components"]["Z1"]["volume_factor"] == 1.05
    assert report["components"]["Z1"]["confidence"] == "MEDIUM"
    assert report["predictions"]["performance"]["low"] > 0
    assert all(report["components"][z]["volume_factor"] == 1 for z in COMPONENTS if z != "Z1")


def test_less_load_can_have_positive_observed_effect():
    report = decide(episodes(slope=-2.))
    assert report["components"]["Z1"]["volume_factor"] == .95
    assert report["components"]["Z1"]["action"] == "DECREASE"


def test_shadow_never_applies_its_proposal():
    report = decide(episodes(), config={**CONFIG, "mode": "SHADOW"})
    assert report["components"]["Z1"]["proposed_volume_factor"] == 1.05
    assert report["components"]["Z1"]["volume_factor"] == 1.
    assert report["status"] == "SHADOW"


@pytest.mark.parametrize("changes", [{"illness_hold": True}, {"lab_review": True}, {"execution_caution": True},
                                    {"stress_score": 80}, {"coverage": 25}, {"families": 1},
                                    {"latest_day": (TODAY-timedelta(days=5)).isoformat()}, {"history_usable": False}])
def test_current_missingness_or_caution_stops_increases(changes):
    report = decide(episodes(), current={**CURRENT, **changes})
    assert all(c["volume_factor"] == 1 and c["intensity_delta"] == 0 for c in report["components"].values())


@pytest.mark.parametrize("phase,taper", [("RE_ENTRY", False), ("TRANSITION", False), ("COMPETITION", False),
                                       ("PRECOMPETITION", False), ("GENERAL_PREPARATION", True)])
def test_periodization_stops_new_experiments(phase, taper):
    assert decide(episodes(), phase=phase, taper=taper)["components"]["Z1"]["volume_factor"] == 1


def test_correlated_accents_do_not_manufacture_individual_confidence():
    report = decide(episodes(correlated=True), allowed=("Z1", "Z2"))
    assert all(c["volume_factor"] == 1 for c in report["components"].values())


def test_cold_start_no_missing_feedback_reward_and_small_labeled_probe():
    config = {**CONFIG, "exploration_enabled": True}
    assert decide([], config=config)["status"] == "COLLECTING" or decide([], config=config)["components"]["Z1"]["volume_factor"] == 1
    report = decide(episodes(2), config=config)
    assert report["status"] == "EXPERIMENT"
    assert report["components"]["Z1"]["volume_factor"] == 1.025
    assert report["components"]["Z1"]["confidence"] == "LOW"
    es = episodes(2)
    for e in es:
        e["outcomes"] = []
    assert decide(es, config=config)["components"]["Z1"]["volume_factor"] == 1


def test_no_return_means_no_exploratory_increase():
    es = episodes(2)
    assert decide(es, current={**CURRENT, "recovered": False}, config={**CONFIG, "exploration_enabled": True})["components"]["Z1"]["volume_factor"] == 1


def test_retained_proposal_not_compounded_and_caution_cancels_it():
    config = {**CONFIG, "exploration_enabled": True}
    first = decide(episodes(2), config=config)
    memory = {"decision": first["decision"]}
    second = decide(episodes(2), config=config, retained=memory)
    assert second["components"] == first["components"]
    assert second["expires_on"] == first["expires_on"]
    assert second["decision_id"] == first["decision_id"]
    cancelled = decide(episodes(2), config=config, retained=memory, current={**CURRENT, "illness_hold": True})
    assert cancelled["status"] == "CAUTION"
    held = decide(episodes(2), config=config, retained={"cooldown_mode": "CONTROL", "cooldown_until": first["expires_on"]})
    assert held["components"]["Z1"]["volume_factor"] == 1


def test_future_outcomes_and_first_observed_dates_never_enter_fit():
    es = episodes()
    expected = learner.fit(es, TODAY)
    future = deepcopy(es[-1])
    future.update(id="future", end=(TODAY+timedelta(days=1)).isoformat())
    late = deepcopy(es[-1])
    late.update(id="late", observed_on=(TODAY+timedelta(days=1)).isoformat())
    assert learner.fit([*es, future, late], TODAY) == expected


def test_intensity_is_separate_from_volume_and_requires_conversion():
    es = episodes(intensity=True)
    neutral = decide(es)
    assert neutral["components"]["Z1"]["intensity_delta"] == 0
    report = decide(es, scales={"Z1": 1.})
    assert report["components"]["Z1"]["intensity_delta"] == .02
    assert report["components"]["Z1"]["volume_factor"] == 1


def test_failed_out_of_sample_validation_disables_exploration():
    es = episodes(16)
    for e in es[8:]:
        e["outcomes"][0]["change_percent"] *= -1
    model = learner.fit(es, TODAY)["GLOBAL"]
    assert model["validation"]["status"] == "FAILED"
    report = decide(es, config={**CONFIG, "exploration_enabled": True})
    assert report["components"]["Z1"]["volume_factor"] == 1


def test_conflicting_independent_outcomes_are_not_cherry_picked():
    es = episodes()
    for e in es:
        e["outcomes"].append({"scope": "GLOBAL", "change_percent": -e["outcomes"][0]["change_percent"], "weight": 1})
    assert learner.fit(es, TODAY)["GLOBAL"]["posterior"]["count"] == 0


def test_fit_cache_is_not_mutable_between_athletes_and_report_is_json_finite():
    es = episodes()
    first = learner.fit(es, TODAY)
    first["GLOBAL"]["posterior"]["mean"][1] = 999
    assert learner.fit(es, TODAY)["GLOBAL"]["posterior"]["mean"][1] < 3
    json.dumps(decide(es), allow_nan=False)


def test_candidate_budget_and_unchanged_action_remain_available():
    report = decide(episodes(), allowed=tuple(COMPONENTS), scales={z: 1 for z in ("Z1", "Z2", "Z3")})
    assert report["candidate_count"] <= 23
    assert decide(episodes(), allowed=())["components"]["Z1"]["volume_factor"] == 1


def test_late_historical_import_is_training_only_not_prospective_validation():
    es = episodes()
    for e in es:
        e["observed_on"] = TODAY.isoformat()
    models = learner.fit(es, TODAY)
    assert models["GLOBAL"]["posterior"]["count"] == 16
    assert models["GLOBAL"]["validation"]["status"] == "WARMUP"
    assert models["GLOBAL"]["validation"]["evaluated"] == 0
    assert decide(es)["components"]["Z1"]["volume_factor"] == 1


def test_new_validation_failure_cancels_retained_learned_change():
    es = episodes()
    prior = decide(es)
    models = learner.fit(es, TODAY)
    models["GLOBAL"]["validation"]["status"] = "FAILED"
    report = learner.decide(models=models, episodes=es, current=CURRENT, today=TODAY,
        config=CONFIG, allowed_components=["Z1"], phase="GENERAL_PREPARATION", retained={"decision": prior["decision"]})
    assert report["status"] == "CAUTION"
    assert report["components"]["Z1"]["volume_factor"] == 1


@pytest.mark.parametrize("outcome,baseline", [("burden", 12.), ("recovery", 8.)])
def test_updated_response_baseline_cancels_retained_increase(outcome, baseline):
    es = episodes()
    prior = decide(es)
    assert prior["components"]["Z1"]["volume_factor"] > 1
    models = learner.fit(es, TODAY)
    models[outcome]["posterior"]["mean"][0] = baseline
    report = learner.decide(models=models, episodes=es, current=CURRENT, today=TODAY,
        config=CONFIG, allowed_components=["Z1"], phase="GENERAL_PREPARATION", retained={"decision": prior["decision"]})
    assert report["status"] == "CAUTION"
    assert report["components"]["Z1"]["volume_factor"] == 1


def test_rotating_accent_or_new_config_does_not_start_second_experiment():
    config = {**CONFIG, "exploration_enabled": True}
    es = episodes(2)
    prior = decide(es, config=config)
    for allowed, cfg in [(("Z2",), config), (("Z1",), {**config, "max_volume_step_percent": 3.})]:
        report = decide(es, config=cfg, allowed=allowed, retained={"decision": prior["decision"]})
        assert all(c["volume_factor"] == 1 for c in report["components"].values())


def test_delayed_recovery_penalizes_previously_positive_dose_association():
    es = episodes()
    assert decide(es)["components"]["Z1"]["volume_factor"] > 1
    for e in es:
        e["response"]["recovery_days"] = 4+2*e["dose_change"]["Z1"]/learner.VOLUME_SCALE
        e["response"]["burden_delta"] = 20*e["dose_change"]["Z1"]/learner.VOLUME_SCALE
    assert decide(es)["components"]["Z1"]["volume_factor"] == 1
