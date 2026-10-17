"""Plan access must not reveal private learning observations or laboratory keys."""
from copy import deepcopy
import json

import pytest
from fastapi.testclient import TestClient

from apps.api import main, dependencies, management_service, management_lifecycle
from apps.api.routes import management
from apps.api.management_projection import public_management
from biathlon.constants import COMPONENTS


def private_payload():
    component = {"volume_factor": 1., "proposed_volume_factor": 1.025, "intensity_delta": 0.,
                 "proposed_intensity_delta": 0., "reason": "Ограничена пробна промяна.",
                 "confidence": "LOW", "action": "HOLD", "evidence_count": 3,
                 "observations": [{"lab": "SECRET_COMPONENT"}]}
    report = {"version": "learning-v1", "mode": "SHADOW", "status": "SHADOW", "as_of": "2026-09-27",
              "effective_from": "2026-09-27", "expires_on": "2026-10-10",
              "summary": "Ограничена пробна промяна.", "confidence": "LOW", "evidence_count": 3,
              "components": {zone: deepcopy(component) for zone in COMPONENTS},
              "validation": {"status": "WARMUP", "evaluated": 3, "model_mae": None, "baseline_mae": None,
                             "sample": "SECRET_VALIDATION"}, "limitations": ["Наблюдавана връзка."],
              "memory": {"episodes": [{"id": "SECRET_EPISODE", "stress": 99}]},
              "current": {"weight": 60, "lab_review": "SECRET_LAB"},
              "source": {"context_key": "SECRET_SOURCE"}, "exclusions": [{"reason": "SECRET_ILLNESS"}],
              "model_summary": {"posterior": "SECRET_POSTERIOR"},
              "predictions": {"weight": "SECRET_PREDICTION"}, "future_private_extension": "SECRET_FUTURE"}
    plan = {"parameters": {"individual_learning": deepcopy(report),
                           "load_progression": {"individual_learning": deepcopy(report)}},
            "long_term": {"individual_learning": deepcopy(report),
                          "progression": {"individual_learning": deepcopy(report)}},
            "input_snapshot": {"response_revisions": [{"kind": "LAB", "entry_key": "SECRET_DATE"}],
                               "response_fingerprint": "opaque-hash", "management_profile": {
                                   "individual_learning": {"mode": "SHADOW", "exploration_enabled": True,
                                       "max_volume_step_percent": 5, "max_intensity_step": .02}}},
            "persistence": {"response_revisions": [{"entry_key": "SECRET_KEY"}]},
            "source": {"generation_id": "public-generation"}, "memory": {"unrelated": "preserved"}}
    return {"active": {"payload": {"plan": deepcopy(plan), "proposal": deepcopy(plan)}},
            "drafts": [{"payload": deepcopy(plan)}], "individual_learning": deepcopy(report)}


def test_projection_removes_all_private_duplicates_without_mutating_server_evidence():
    source = private_payload()
    before = deepcopy(source)
    projected = public_management(source)
    assert "SECRET" not in json.dumps(projected)
    assert source == before and "SECRET" in json.dumps(source)
    report = projected["individual_learning"]
    assert report["components"]["Z3"]["proposed_volume_factor"] == 1.025
    assert report["validation"] == {"status": "WARMUP", "evaluated": 3, "model_mae": None, "baseline_mae": None}
    plan = projected["drafts"][0]["payload"]
    assert plan["source"] == {"generation_id": "public-generation"}
    assert plan["memory"] == {"unrelated": "preserved"}  # Not a generic recursive key scrubber.
    assert plan["input_snapshot"]["management_profile"]["individual_learning"]["max_volume_step_percent"] == 5
    assert plan["input_snapshot"]["response_fingerprint"] == "opaque-hash"


def test_malformed_learning_extensions_fail_closed_and_rule_version_strings_survive():
    assert public_management({"individual_learning": [{"memory": "SECRET"}]}) == {"individual_learning": None}
    assert public_management({"rule_versions": {"individual_learning": "v1"}}) == {"rule_versions": {"individual_learning": "v1"}}


@pytest.mark.parametrize("method,path,body", [
    ("GET", "/profile", None), ("GET", "/view", None), ("GET", "/view?view=overview", None),
    ("GET", "/outlook", None), ("GET", "/drafts", None), ("GET", "/active", None),
    ("POST", "/generate", {"start_date": "2026-09-27", "expected_profile_revision": 1}),
    ("POST", "/activate", {"start_date": "2026-09-27", "draft_revision": 1, "expected_revision": 0}),
    ("POST", "/action", {"action": "REFRESH", "expected_revision": 1}),
    ("POST", "/action", {"action": "APPROVE", "expected_revision": 1}),
    ("POST", "/day", {"date": "2026-09-27", "action": "REST", "expected_revision": 1}),
])
def test_every_management_http_transport_projects_private_learning(monkeypatch, method, path, body):
    payload = private_payload()
    monkeypatch.setattr(dependencies, "model_alias", lambda *args: "athlete")
    monkeypatch.setattr(dependencies, "repository", lambda: object())
    monkeypatch.setattr(management, "ReadSession", lambda value: value)
    monkeypatch.setattr(management, "public_sync_state", lambda value: None)
    for module, names in ((management_service, ["profile_view", "history", "outlook", "generate"]),
                          (management_lifecycle, ["current", "activate", "refresh", "action"])):
        for name in names:
            monkeypatch.setattr(module, name, lambda *args, **kwargs: deepcopy(payload))
    # The page's optional sync accessor is independent of the plan projection.
    class Repo:
        def sync_state(self, alias): return None
    monkeypatch.setattr(dependencies, "repository", lambda: Repo())
    response = TestClient(main.app).request(method, "/api/v2/athlete/management"+path, json=body,
        headers={"X-OnFlows-Actor-Id": "11111111-1111-4111-8111-111111111111"})
    assert response.status_code == 200, response.text
    assert "SECRET" not in response.text
    assert "SECRET" in json.dumps(payload)
