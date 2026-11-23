"""Nutrition answers must survive a large saved training prescription."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json

import pytest

from apps.api import assistant_nutrition as nutrition, assistant_service as service
from apps.api.assistant_provider import (
    AssistantProviderError, DisabledAssistantProvider, OpenAIAssistantProvider,
    OpenAIProviderConfig, ScriptedAssistantProvider,
)
from tests.api.test_assistant_service import ALIAS, ACTOR, DAY, body, query, repo


def energy():
    return {"query_kind": "energy", "availability": "AVAILABLE", "revision": 7,
            "data": {"days": [{"day": DAY,
                "targets": {"kcal": 2800, "carbohydrate_g": 400, "protein_g": 120, "fat_g": 80},
                "nutrition_basis": {"provisional": True, "history": [{"day": "2026-11-10", "kcal": 2750}],
                    "food_preferences": {"allergies": "ядки", "preferences": "без месо"},
                    "notes": ["Предварителна цел."]},
                "activities": [{"timeline": list(range(1000))}]}],
                "race_preparation": [{"status": "PENDING", "days": []}], "menu_guidance": "Saved guidance"}}


def training():
    return {"query_kind": "day", "availability": "AVAILABLE", "date": DAY,
            "plan_revision": 4, "plan_status": "ACTIVE", "prescription_active": True,
            "sessions": [{"session_id": DAY + ":1", "title": "Интервали", "sport_label_bg": "Ролкови ски",
                "total_minutes": 60, "dose_evidence": {"explanation": "Long scientific evidence " * 5000},
                "blocks": [{"kind": "REPEAT_GROUP", "repeat_count": 5, "sequence": [
                    {"kind": "WORK", "zone": "Z4", "duration_min": 2, "instructions": "Coaching " * 1000},
                    {"kind": "REST", "zone": "Z1", "duration_min": 3, "passive_rest": False}]}]},
                {"session_id": DAY + ":2", "title": "Леко движение", "total_minutes": 30, "blocks": []}]}


def test_projection_retains_all_targets_preferences_provenance_and_training_durations():
    source, day = energy(), training()
    originals = deepcopy((source, day))
    result, summary = nutrition.compact_context(source), nutrition.compact_context(day)
    assert result["data"]["days"][0]["targets"] == source["data"]["days"][0]["targets"]
    assert result["data"]["days"][0]["nutrition_basis"] == source["data"]["days"][0]["nutrition_basis"]
    assert result["data"]["race_preparation"] == source["data"]["race_preparation"]
    assert result["revision"] == 7 and summary["plan_revision"] == 4
    assert len(summary["sessions"]) == 2 and summary["prescription_active"] is True
    group = summary["sessions"][0]["blocks"][0]
    assert group["repeat_count"] == 5
    assert [b["duration_min"] for b in group["sequence"]] == [2, 3]
    assert group["sequence"][1]["passive_rest"] is False
    assert (source, day) == originals
    contract = {"query_kind": "inputs", "payload": {"notes": "allergies"}}
    assert nutrition.compact_context(contract) is contract
    section = {**source, "section": "days.0.activities"}
    assert nutrition.compact_context(section) is section


@pytest.mark.parametrize("message", [
    "Прочети хранителната цел и предложи конкретно меню с грамажи.",
    "Не те питам за натоварването а за храненето",
    "Храненето а не програмата", "Исках хранителен режим за утре",
    "Какво да ям днес?",
    "Направи меню за утре.",
    "Храненето, а не програмата. Повтори менюто за 2026-11-23 кратко.",
])
def test_nutrition_turn_uses_compact_results_and_has_no_training_nudge(repo, message):
    repo.read_result = lambda q: energy() if q["kind"] == "energy" else training()
    provider = ScriptedAssistantProvider([
        {"text": "", "query": query("energy", date=DAY)}, {"text": "", "query": query("day", date=DAY)},
        {"text": "Хранителна цел: 2800 kcal. Примерно меню с порции."}])
    result = service.turn(repo, ALIAS, ACTOR, body(message), provider)
    context = provider.calls[-1]["context"]
    assert context["answer_focus"] == "nutrition" and context["read_only_turn"]
    assert "dose_evidence" not in context["results"][1]["sessions"][0]
    assert "food_preferences" in context["results"][0]["data"]["days"][0]["nutrition_basis"]
    assert "Хранителна цел" in result["message"] and "умора" not in result["message"]
    assert len(repo.reads) == 2 and not repo.writes


@pytest.mark.parametrize("failure", ["size", "timeout", "read_budget"])
def test_nutrition_failure_returns_targets_never_training_instructions(repo, failure):
    repo.read_result = lambda q: energy() if q["kind"] == "energy" else training()
    class Provider:
        calls = 0
        def complete(self, *args):
            self.calls += 1
            if self.calls > 2 and failure in {"size", "timeout"}:
                raise AssistantProviderError("input_too_large" if failure == "size" else "timeout")
            return {"query": query("energy" if self.calls == 1 else "day", date=DAY)}
    result = service.turn(repo, ALIAS, ACTOR, body("Храненето а не програмата"), Provider())
    text = result["message"]
    assert "2800 kcal" in text and "400 g" in text and "120 g" in text and "80 g" in text
    assert "Предварителна цел" in text and "Конкретното меню не е изготвено" in text
    assert "Интервали" not in text and "Седмична програма" not in text
    assert not repo.writes
    if failure == "timeout":
        assert result["error_code"] == "timeout"


def test_pending_energy_and_available_plan_do_not_turn_food_request_into_training(repo):
    repo.read_result = lambda q: {"query_kind": "energy", "availability": "PENDING"} if q["kind"] == "energy" else training()
    provider = ScriptedAssistantProvider([{"text": "", "query": query("energy")}, {"text": "", "query": query("day")},
                                         {"text": "", "query": query("day")}, {"text": "", "query": query("day")}])
    result = service.turn(repo, ALIAS, ACTOR, body("Препоръчай меню за утре"), provider)
    assert "хранителна цел" in result["message"] and "Интервали" not in result["message"]


def test_disabled_provider_reads_nutrition_instead_of_today_training(repo):
    repo.read_result = energy()
    result = service.turn(repo, ALIAS, ACTOR, body("Какво да ям днес?"), DisabledAssistantProvider())
    assert repo.reads[0]["kind"] == "energy" and "2800 kcal" in result["message"]


def test_nutrition_question_ends_an_unrelated_pending_write(repo):
    conversation = repo.seed({"input_request": {"kind": "DAILY", "request": "Запиши умората",
        "answers": [], "expires_at": (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat()}})
    provider = ScriptedAssistantProvider([{"text": "Примерно меню"}])
    service.turn(repo, ALIAS, ACTOR, body("Храненето а не програмата", conversation), provider)
    assert provider.calls[0]["context"]["active_input_request"] is None and not repo.writes


def test_training_question_keeps_full_prescription(repo):
    repo.read_result = training()
    provider = ScriptedAssistantProvider([{"text": "", "query": query("day")}, {"text": "Описание на тренировката"}])
    service.turn(repo, ALIAS, ACTOR, body("Как да изпълня тренировката?"), provider)
    assert provider.calls[-1]["context"]["results"][0] == repo.read_result


def test_nutrition_payload_fits_budget_with_large_training_evidence_without_network():
    provider = OpenAIAssistantProvider(OpenAIProviderConfig(api_key="unused-test-key"))
    contexts = [energy(), training()]
    metadata = {"results": contexts, "completed_queries": [query("energy", date=DAY), query("day", date=DAY)],
                "read_only_turn": True, "answer_focus": "nutrition"}
    messages = [{"role": "user", "content": "Нямам алергии. Искам меню за утре."}]
    with pytest.raises(AssistantProviderError, match="input_too_large"):
        provider._payload(messages, metadata, {"read": ["energy", "day"]})
    metadata["results"] = [nutrition.compact_context(c) for c in contexts]
    payload = provider._payload(messages, metadata, {"read": ["energy", "day"]})
    assert len(json.dumps(payload, ensure_ascii=False).encode()) < 64 * 1024
    assert "Текущото искане е за хранене" in payload["instructions"]
    schema = payload["tools"][0]["parameters"]["properties"]
    assert schema["query"]["properties"]["kind"]["enum"] == ["energy", "day"]
    assert schema["query"]["properties"]["input_kind"] == {"type": "null"}
    assert schema["command"] == {"type": "null"}
    assert "MANAGEMENT_PROFILE" not in payload["instructions"]


def test_menu_timeout_is_bounded_and_does_not_expand_unrelated_calls():
    provider = OpenAIAssistantProvider(OpenAIProviderConfig(api_key="unused-test-key"))
    assert provider.request_timeout({}) == 20
    assert provider.request_timeout({"answer_focus": "nutrition", "results": []}) == 20
    assert provider.request_timeout({"answer_focus": "nutrition", "results": [energy()]}) == 40
    assert provider.request_timeout({"results": [training()]}) == 20


def test_explicit_observation_write_with_food_mention_keeps_its_contract(repo):
    provider = ScriptedAssistantProvider([{"text": "Уточни самооценката."}])
    service.turn(repo, ALIAS, ACTOR, body("Искам да запиша умората си след хранене."), provider)
    context = provider.calls[0]["context"]
    assert context["active_input_request"]["kind"] == "DAILY"
    assert context["answer_focus"] is None


def test_longer_menu_timeout_respects_total_turn_deadline(repo, monkeypatch):
    repo.read_result = energy()
    clock = iter([0, 50, 51])
    monkeypatch.setattr(service, "monotonic", lambda: next(clock))
    class Provider:
        calls = 0
        def request_timeout(self, context):
            return 40 if context["results"] else 20
        def complete(self, *args):
            self.calls += 1
            assert self.calls == 1, "No 40s call may start with only 40s left in the turn"
            return {"query": query("energy", date=DAY)}
    provider = Provider()
    result = service.turn(repo, ALIAS, ACTOR, body("Направи меню за днес"), provider)
    assert provider.calls == 1 and "2800 kcal" in result["message"]
    assert "Конкретното меню не е изготвено" in result["message"]


def test_unknown_nutrition_form_is_recovered_without_reading_or_writing_it(repo):
    repo.read_result = energy()
    provider = ScriptedAssistantProvider([
        {"text": "", "query": query("energy", date=DAY)},
        {"text": "", "query": query("inputs", input_kind="NUTRITION")},
        {"text": "Примерно меню според прочетената цел."}])
    result = service.turn(repo, ALIAS, ACTOR, body("Повтори менюто за днес"), provider)
    assert [q["kind"] for q in repo.reads] == ["energy"]
    assert provider.calls[-1]["context"]["query_feedback"]["status"] == "UNSUPPORTED_NUTRITION_READ"
    assert "Примерно меню" in result["message"] and not repo.writes


def test_nutrition_tool_schema_cannot_grant_plan_access():
    provider = OpenAIAssistantProvider(OpenAIProviderConfig(api_key="unused-test-key"))
    payload = provider._payload([{"role": "user", "content": "Искам меню"}],
        {"answer_focus": "nutrition", "read_only_turn": True}, {"read": ["energy", "inputs"], "write": []})
    assert payload["tools"][0]["parameters"]["properties"]["query"]["properties"]["kind"]["enum"] == ["energy"]
