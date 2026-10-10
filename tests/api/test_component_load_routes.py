from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from apps.api import component_load_context, dependencies, management_service, model_service
from apps.api.component_load_projection import ComponentLoadRefreshRequired
from apps.api.main import app
from apps.api.routes import management
from tests.api.test_component_load_context import curve_view
from tests.api.test_component_load_projection import source


HEADERS = {
    "Authorization": "Bearer test-service-token",
    "X-OnFlows-Athlete-Alias": "athlete-test",
    "X-OnFlows-Actor-Id": "11111111-1111-4111-8111-111111111111",
}


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("ONFLOWS_SERVICE_TOKEN", "test-service-token")
    return TestClient(app)


@pytest.mark.parametrize("route", ["load-history", "recovery-history", "training-status", "dashboard-view"])
@pytest.mark.parametrize("problem", ["missing_hr_mapping", "missing_activity_q"])
def test_dashboard_returns_actionable_conflict_for_capacity_or_history(client, monkeypatch, route, problem):
    history = source()
    view, _ = curve_view()
    if problem == "missing_hr_mapping":
        view["hr_model"] = None
    else:
        history["activities"] = []
    snapshot = {"load_history": history}
    repository = SimpleNamespace(
        latest=lambda alias: snapshot,
        active_analysis=lambda alias: {"snapshot_payload": snapshot},
        _request=lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(dependencies, "repository", lambda: repository)
    monkeypatch.setattr(model_service, "speed_view", lambda *args, **kwargs: view)
    response = client.get(f"/api/v2/real/{route}", headers=HEADERS)
    assert response.status_code == 409
    message = response.json()["detail"]
    if problem == "missing_hr_mapping":
        assert "HRmax" in message and "пулсовите граници" in message
        assert "скорост–време" in message
    else:
        assert "Обновете реалните данни от Intervals.icu" in message


@pytest.mark.parametrize("route,method,body", [
    ("outlook", "get", None),
    ("view?view=overview", "get", None),
    ("generate", "post", {"start_date": "2026-10-10", "expected_profile_revision": 1}),
    ("active", "get", None),
    ("action", "post", {"action": "REFRESH", "expected_revision": 1}),
])
@pytest.mark.parametrize("problem", ["missing_hr_mapping", "missing_activity_q"])
def test_management_returns_actionable_conflict_without_expert_replacement(client, monkeypatch, route, method, body, problem):
    monkeypatch.setattr(dependencies, "repository", lambda: SimpleNamespace())
    view, _ = curve_view()
    view["hr_model"] = None
    def fail(*args, **kwargs):
        if problem == "missing_activity_q":
            raise ComponentLoadRefreshRequired("Stored activity Q is incomplete")
        return component_load_context.context_from_speed_view(view)
    monkeypatch.setattr(management_service, "outlook", fail)
    monkeypatch.setattr(management_service, "generate", fail)
    monkeypatch.setattr(management_service, "profile_view", lambda *args, **kwargs: {})
    monkeypatch.setattr(management.management_lifecycle, "current", lambda *args, **kwargs: {})
    monkeypatch.setattr(management.management_lifecycle, "action", fail)
    if route == "active":
        monkeypatch.setattr(management.management_lifecycle, "current", fail)
    kwargs = {"headers": HEADERS}
    if body is not None:
        kwargs["json"] = body
    response = getattr(client, method)(f"/api/v2/athlete/management/{route}", **kwargs)
    assert response.status_code == 409
    message = response.json()["detail"]
    assert ("HRmax" if problem == "missing_hr_mapping" else "Intervals.icu") in message
