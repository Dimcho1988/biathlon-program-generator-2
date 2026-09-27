"""The learned policy stays inside the existing prescription and publication gates."""
from copy import deepcopy
from datetime import timedelta

import pytest
from pydantic import ValidationError

from apps.api import training_plan_engine as engine, management_service
from apps.api.management_schemas import ManagementProfile, IndividualLearning
from apps.api.management_store import ManagementStore
from biathlon import load_progression as policy
from tests.api.test_load_progression import configured, observed
from tests.api.test_training_plan_engine import TODAY, NOW, reference_speed
from tests.api.test_management_store import Repository as StoreRepository, ACTOR


def setup_context(mode="CONTROL", factor=1.05, intensity=.02):
    profile = configured(individual_learning=IndividualLearning(mode=mode).model_dump())
    repo, source, rows = observed()
    context = policy.context(profile, source, rows, TODAY)
    context["individual_learning"] = {
        "version": "test", "mode": mode, "as_of": TODAY.isoformat(),
        "effective_from": TODAY.isoformat(), "expires_on": (TODAY+timedelta(days=13)).isoformat(),
        "components": {"Z3": {"volume_factor": factor, "intensity_delta": intensity,
                                "reason": "Observed tolerance", "confidence": .9, "action": "TRIAL"}},
        "method_preferences": [], "memory": {"version": "test", "episodes": []},
    }
    return profile, repo, source, rows, context


def goals(profile, rows, context, day=TODAY, period="GENERAL_PREPARATION", **kwargs):
    return engine._goals(profile, day, period, kwargs.pop("taper", False), {}, None, 0, 4,
                         rows, TODAY, kwargs.pop("limited", False), 1., context, **kwargs)[0]


def test_old_profiles_default_to_shadow_and_validate_bounded_controls():
    body = ManagementProfile.model_validate(configured(discipline="5000 m"))
    assert body.individual_learning.mode == "SHADOW"
    assert body.individual_learning.max_volume_step_percent == 5
    for values in ({"mode": "AUTO"}, {"max_volume_step_percent": 11}, {"max_intensity_step": .051},
                   {"max_volume_step_percent": float("nan")}):
        with pytest.raises(ValidationError):
            IndividualLearning(**values)


def test_control_volume_changes_direct_q_once_without_expanding_separate_e_gate():
    profile, _, _, rows, context = setup_context()
    baseline_context = {k: v for k, v in context.items() if k != "individual_learning"}
    before, after = goals(profile, rows, baseline_context), goals(profile, rows, context)
    assert after["Z3"]["target_weekly_q"] == pytest.approx(before["Z3"]["target_weekly_q"]*1.05)
    assert after["Z3"]["target"] == before["Z3"]["target"]
    assert after["Z2"]["target_weekly_q"] == before["Z2"]["target_weekly_q"]
    assert after["Z3"]["progression"]["individual_learning"]["applied_volume_factor"] == pytest.approx(1.05)
    assert goals(profile, rows, context) == after


@pytest.mark.parametrize("mode", ["OFF", "SHADOW"])
def test_off_and_shadow_cannot_change_goals_or_intensity(mode):
    profile, _, _, rows, context = setup_context(mode)
    baseline = goals(profile, rows, {k: v for k, v in context.items() if k != "individual_learning"})
    assert goals(profile, rows, context) == baseline
    method = {"zone": "Z3", "position": .75, "structure": "CONTINUOUS"}
    assert engine._learned_method(method, profile, context, TODAY, "GENERAL_PREPARATION", {"kind": "BUILD"}) is None
    assert method["position"] == .75


@pytest.mark.parametrize("condition", ["manual", "explicit", "missing", "zero", "expired", "future", "disabled", "limited"])
def test_control_never_overrides_manual_or_unreliable_context(condition):
    profile, _, _, _, context = setup_context()
    state = {"kind": "BUILD"}
    if condition == "manual": profile["component_targets_weekly"] = {"Z3": 100}
    if condition == "explicit": state["explicit"] = True
    if condition == "missing": context["components"]["Z3"]["recent_observed_q"] = None
    if condition == "zero": context["components"]["Z3"]["recent_observed_q"] = 0
    if condition == "expired": context["individual_learning"]["expires_on"] = (TODAY-timedelta(days=1)).isoformat()
    if condition == "future": context["individual_learning"]["effective_from"] = (TODAY+timedelta(days=1)).isoformat()
    if condition == "disabled": profile["load_progression"]["feedback_enabled"] = False
    result = policy.individual_adjustment(context, profile, "Z3", TODAY, "GENERAL_PREPARATION", state,
                                          limited=condition == "limited")
    assert not result["eligible"] and result["volume_factor"] == 1 and result["intensity_delta"] == 0


@pytest.mark.parametrize("period,kind,taper", [("RE_ENTRY", "RE_ENTRY", False), ("TRANSITION", "BUILD", False),
    ("PRECOMPETITION", "BUILD", False), ("COMPETITION", "BUILD", False),
    ("GENERAL_PREPARATION", "STRESS", False), ("GENERAL_PREPARATION", "RECOVERY", False),
    ("GENERAL_PREPARATION", "BUILD", True)])
def test_loading_experiment_cannot_expand_protected_phases(period, kind, taper):
    profile, _, _, _, context = setup_context()
    result = policy.individual_adjustment(context, profile, "Z3", TODAY, period, {"kind": kind}, taper=taper)
    assert result["volume_factor"] == 1 and result["intensity_delta"] == 0
    context["individual_learning"]["components"]["Z3"].update(volume_factor=.95, intensity_delta=-.02)
    result = policy.individual_adjustment(context, profile, "Z3", TODAY, period, {"kind": kind}, taper=taper)
    assert result["volume_factor"] == .95 and result["intensity_delta"] == -.02


def test_strength_uses_its_own_effective_budget_without_an_aerobic_q_capacity():
    profile, _, _, rows, context = setup_context(factor=.95)
    context["components"]["STR"]["recent_observed_q"] = 30
    context["individual_learning"]["components"]["STR"] = {"volume_factor": .95, "intensity_delta": 0}
    baseline = goals(profile, rows, {k: v for k, v in context.items() if k != "individual_learning"})
    result = goals(profile, rows, context)
    assert "target_weekly_q" not in result["STR"]
    assert result["STR"]["target"] == pytest.approx(baseline["STR"]["target"]*.95)


def test_intensity_is_bounded_before_recomputing_hr_and_capacity():
    profile, repo, _, _, context = setup_context(intensity=.3)
    method = {"zone": "Z3", "position": .75, "structure": "CONTINUOUS"}
    before = engine.capacity_for(method, repo.settings, None, (None, [], []), TODAY)
    evidence = engine._learned_method(method, profile, context, TODAY, "GENERAL_PREPARATION", {"kind": "BUILD"})
    after = engine.capacity_for(method, repo.settings, None, (None, [], []), TODAY)
    assert method["position"] == .77 and evidence["capacity_recalculated"]
    assert after["target_hr_bpm"] > before["target_hr_bpm"]
    assert after["capacity_minutes"] < before["capacity_minutes"]
    assert after["target_speed_kmh"] is None  # Never invent speed from a learning delta.


@pytest.mark.parametrize("structure", ["MODEL_INTERVALS", "METABOLIC_INTERVALS", "THREE_PROGRESSIVE_BLOCKS", "ALTERNATING"])
def test_composite_and_effort_led_intensity_profiles_remain_intact(structure):
    profile, _, _, _, context = setup_context()
    method = {"zone": "Z3", "position": .75, "structure": structure}
    original = deepcopy(method)
    assert engine._learned_method(method, profile, context, TODAY, "GENERAL_PREPARATION", {"kind": "BUILD"}) is None
    assert method == original


@pytest.mark.parametrize("mismatch", [None, "version", "sport", "purpose", "dose"])
def test_method_preference_is_small_and_only_applies_to_confirmed_comparable_dose(mismatch):
    from apps.api.learning_methods import method_descriptor
    profile, _, _, _, context = setup_context(intensity=0)
    method = {"id": "test-method", "zone": "Z3", "position": .75, "structure": "CONTINUOUS", "purpose": "BUILDING"}
    descriptor = method_descriptor(method, "Run")
    preference = {"method_id": method["id"], "component": "Z3", "sport": "Run", "purpose": "BUILDING",
                  "version": descriptor["version"], "duration_min": 40, "duration_max": 60,
                  "score_delta": 1., "reason": "Repeated confirmed completion", "confidence": .9}
    if mismatch == "version": preference["version"] = "obsolete"
    if mismatch == "sport": preference["sport"] = "NordicSki"
    if mismatch == "purpose": preference["purpose"] = "MAINTENANCE"
    if mismatch == "dose": preference["duration_max"] = 49
    context["individual_learning"]["method_preferences"] = [preference]
    result = engine._learned_preference(method, "Run", profile, context, TODAY, "GENERAL_PREPARATION",
                                        {"kind": "BUILD"}, purpose="BUILDING", duration=50)
    if mismatch:
        assert result is None
    else:
        assert result["applied_score_delta"] == .25


def test_newest_complete_memory_preserves_archive_without_resurrecting_invalidated_ids():
    class MemoryRepository(StoreRepository):
        def _request(self, method, path, **kwargs):
            self.requests.append((method, path, kwargs))
            if path.startswith("/onflows_management_plan_revisions?"):
                return [{"recorded_at": "2026-09-25T12:00:00+00:00", "proposal_memory": None,
                         "plan_memory": {"version": "v1", "episodes": [{"id": "old", "value": 1}, {"id": "invalid"}]}}]
            return [{"recorded_at": "2026-09-26T12:00:00+00:00", "memory": {
                "version": "v1", "episodes": [{"id": "old", "value": 1}, {"id": "recent", "value": 2}],
                "decision_history": [{"id": "decision"}]}}]
    repo = MemoryRepository()
    result = ManagementStore(repo).learning_memory("ath&a=1")
    assert [e["id"] for e in result["episodes"]] == ["old", "recent"]
    assert result["decision_history"] == [{"id": "decision"}]
    assert all("athlete_alias=eq.ath%26a%3D1" in path and "limit=1" in path for _, path, _ in repo.requests)


def test_memory_looks_past_disabled_reports_and_orders_by_analysis_not_later_pause():
    class MemoryRepository(StoreRepository):
        def _request(self, method, path, **kwargs):
            if path.startswith("/onflows_management_plan_revisions?"):
                assert "payload->proposal->parameters->individual_learning->>memory.not.is.null" in path
                return [{"recorded_at": "2026-09-27T12:00:00+00:00", "plan_generated_at": "2026-09-20T12:00:00+00:00",
                         "plan_memory": {"version": "v1", "episodes": [{"id": "older-plan"}]}}]
            assert "payload->parameters->individual_learning->>memory=not.is.null" in path
            return [{"recorded_at": "2026-09-26T12:00:00+00:00", "generated_at": "2026-09-26T11:59:00+00:00",
                     "memory": {"version": "v1", "episodes": [{"id": "newer-evidence"}]}}]
    assert ManagementStore(MemoryRepository()).learning_memory("athlete")["episodes"] == [{"id": "newer-evidence"}]


def test_draft_save_pins_exact_compact_response_identity_including_empty_history():
    repo = StoreRepository()
    ManagementStore(repo).save_draft("athlete", {"start_date": TODAY.isoformat()}, ACTOR, 1, expected_responses=[])
    assert repo.requests[0][2]["json"]["p_expected_responses"] == []


def test_input_fingerprint_contains_compact_latest_response_identity(monkeypatch):
    from types import SimpleNamespace
    repo = SimpleNamespace(
        athlete_settings=lambda _: SimpleNamespace(timezone="UTC", zone_bounds_bpm=[100, 125, 145, 160, 175, 190], hrmax_bpm=190),
        active_analysis=lambda _: {"generation_id": "current", "snapshot_payload": {}},
        athlete_planning_calendar=lambda _: None, athlete_planning_profile=lambda _: None,
        athlete_mesocycle_accent_preferences=lambda _: None)
    monkeypatch.setattr(management_service, "ModelStore", lambda _: SimpleNamespace(entries=lambda _: []))
    responses = [
        {"kind": "LAB", "entry_key": "lab", "revision": 3, "payload": {"sensitive": "not frozen"}},
        {"kind": "DAILY", "entry_key": TODAY.isoformat(), "revision": 2, "payload": {}},
    ]
    monkeypatch.setattr(engine, "ResponseStore", lambda _: SimpleNamespace(entries=lambda _: responses))
    state = management_service.input_state(repo, "athlete", evaluated_at=NOW, include_response=True)
    assert state["response_revisions"] == [
        {"kind": "DAILY", "entry_key": TODAY.isoformat(), "revision": 2},
        {"kind": "LAB", "entry_key": "lab", "revision": 3},
    ]
    before = state["response_fingerprint"]
    responses[0]["revision"] = 4
    assert management_service.input_state(repo, "athlete", evaluated_at=NOW, include_response=True)["response_fingerprint"] != before
    assert "individual_learning" in state["rule_versions"]
    assert management_service.input_state(repo, "athlete", evaluated_at=NOW)["response_revisions"] is None


def test_control_replaces_legacy_learning_and_disabled_feedback_reads_no_learner(monkeypatch):
    from apps.api import learning_service
    profile, repo, source, rows, context = setup_context()
    calls = []
    monkeypatch.setattr(learning_service, "context", lambda *args, **kwargs: calls.append(kwargs) or context["individual_learning"])
    monkeypatch.setattr(engine.load_adaptation, "assess", lambda *args, **kwargs: pytest.fail("Legacy learner stacked"))
    result = engine.progression_context(repo, "athlete", profile, source, rows, TODAY, envelope=repo.envelope)
    assert "adjustments" not in result["adaptation"]
    assert result["individual_learning"] == context["individual_learning"]
    assert calls[0]["envelope"] == repo.envelope
    profile["load_progression"]["feedback_enabled"] = False
    result = engine.progression_context(repo, "athlete", profile, source, rows, TODAY)
    assert result["adaptation"] is None and "individual_learning" not in result
    assert len(calls) == 1


def test_shadow_plan_preserves_every_session_and_readiness(monkeypatch):
    from apps.api import learning_service
    profile, repo, _, _, context = setup_context("SHADOW")
    monkeypatch.setattr(engine.model_service, "speed_view", reference_speed)
    monkeypatch.setattr(learning_service, "context", lambda *args, **kwargs: context["individual_learning"])
    shadow = engine.generate_plan(repo, "athlete", profile, start_date=TODAY, now=NOW)
    profile["individual_learning"]["mode"] = "OFF"
    context["individual_learning"]["mode"] = "OFF"
    off = engine.generate_plan(repo, "athlete", profile, start_date=TODAY, now=NOW)
    assert shadow["days"] == off["days"]
    assert shadow["summary"] == off["summary"]


def test_only_control_lab_review_blocks_activation(monkeypatch):
    from apps.api import learning_service
    profile, repo, _, _, context = setup_context()
    context["individual_learning"]["current"] = {"lab_review": True}
    monkeypatch.setattr(engine.model_service, "speed_view", reference_speed)
    monkeypatch.setattr(learning_service, "context", lambda *args, **kwargs: context["individual_learning"])
    result = engine.generate_plan(repo, "athlete", profile, start_date=TODAY, now=NOW)
    assert result["activation_eligible"] is False
    assert "LEARNING_REVIEW" in {w["code"] for w in result["warnings"]}
    profile["individual_learning"]["mode"] = "SHADOW"
    context["individual_learning"]["mode"] = "SHADOW"
    result = engine.generate_plan(repo, "athlete", profile, start_date=TODAY, now=NOW)
    assert "LEARNING_REVIEW" not in {w["code"] for w in result["warnings"]}


def test_full_year_plan_has_one_bounded_replay_archive_and_stays_below_one_mebibyte(monkeypatch):
    import json
    from apps.api import learning_service, learning_methods
    from tests.api.test_learning_evidence import fixture, episode
    from tests.api.test_learning_methods import observation, A, B

    # Start with actual evidence shapes and all outcome scopes. The byte
    # window, not a tiny hand-picked example, must bound a long-used account.
    template = episode(fixture())
    all_episodes = []
    for i in range(78):
        item = deepcopy(template)
        end = TODAY-timedelta(days=14*(78-i))
        item.update(id=f"auto14:{end.isoformat()}", start=(end-timedelta(days=13)).isoformat(),
                    end=end.isoformat(), observed_on=end.isoformat())
        item["outcomes"] = [{**deepcopy(template["outcomes"][0]), "scope": scope}
                            for scope in ["GLOBAL", *engine.COMPONENTS]]
        all_episodes.append(item)
    bounded_episodes = learning_service.bounded_episodes(all_episodes)
    pairs = [observation(i+1, method=A if i % 2 else B, age=1+i % 600, success=bool(i % 2)) for i in range(1000)]
    methods = learning_methods.assess_methods(entries=[p[0] for p in pairs],
        source={"activities": [p[1] for p in pairs]}, today=TODAY)["memory"]
    assert 0 < len(bounded_episodes) < len(all_episodes)
    assert 0 < len(methods["observations"]) < len(pairs)
    assert len(json.dumps(methods).encode()) <= learning_methods.MAX_MEMORY_BYTES

    profile, repo, _, _, context = setup_context("SHADOW")
    profile.update(program_end=(TODAY+timedelta(days=335)).isoformat(), horizon_mode="MANUAL",
                   available_minutes=[360]*7, strength_enabled=True)
    profile["planning_controls"].update(sessions_per_week=21, sessions_by_day=[3]*7)
    report = context["individual_learning"]
    report["memory"].update(episodes=bounded_episodes, methods=methods)
    report.update(current={"stress_score": 55, "lab_review": False}, source={"context_key": "private"},
                  exclusions=[{"id": "excluded", "reason": "private"}], model_summary={"private": True})
    original_report = deepcopy(report)
    monkeypatch.setattr(engine.model_service, "speed_view", reference_speed)
    monkeypatch.setattr(learning_service, "context", lambda *args, **kwargs: report)
    result = engine.generate_plan(repo, "athlete", profile, start_date=TODAY, now=NOW)
    assert report == original_report  # Runtime evidence is not stripped in place.
    assert result["parameters"]["individual_learning"]["memory"] == report["memory"]
    copies = [result["parameters"]["load_progression"]["individual_learning"],
              result["long_term"]["individual_learning"], result["long_term"]["progression"]["individual_learning"]]
    assert all(not ({"memory", "current", "source", "exclusions", "model_summary"} & set(copy)) for copy in copies)
    encoded = json.dumps(result).encode()  # Larger ASCII/space form bounds the ordinary compact UTF-8 RPC body too.
    assert encoded.count(b'"episodes":') == 1
    assert encoded.count(b'"observations": [') == 1
    assert len(result["long_term"]["weeks"]) == 48
    assert len(encoded) < 1024*1024
