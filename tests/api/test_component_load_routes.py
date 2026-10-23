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


@pytest.mark.parametrize("route", ["load-history", "completed-work", "recovery-history", "training-status", "dashboard-view"])
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


def test_completed_work_tracks_current_curve_capacity_without_refreshing_persisted_data(client, monkeypatch):
    from datetime import date

    from apps.api.cloud import InMemorySnapshotRepository
    from apps.api.real_service import refresh
    from biathlon import speed_duration
    from intervals_inspector.intervals_client import IntervalsResponse
    from tests.api.test_real_cloud_pipeline import Client, ENV

    class HourClient(Client):
        def get_activity_result(self, activity_id, *, include_intervals=False):
            response = super().get_activity_result(activity_id, include_intervals=include_intervals)
            response.payload.update(moving_time=3600, elapsed_time=3600, icu_recording_time=3600)
            return response

        def get_streams_result(self, activity_id):
            return IntervalsResponse(200, [
                {"type": "time", "data": list(range(3601))},
                {"type": "heartrate", "data": [145.] * 3601},
            ])

    repository = InMemorySnapshotRepository()
    refresh(repository, environ=ENV, client=HourClient(), period_end=date(2026, 8, 15))
    persisted = repository.latest("pilot")
    # Expose model-store capability, with the current curve supplied below.
    repository._request = lambda *args, **kwargs: None
    monkeypatch.setattr(dependencies, "repository", lambda: repository)
    view, _ = curve_view()
    curve = speed_duration.calibrated([row["payload"] for row in view["tests"]])
    z2_reference = next(row for row in view["hr_model"]["zones"] if row["zone"] == "Z2")
    monkeypatch.setattr(model_service, "speed_view", lambda *args, **kwargs: view)
    headers = {**HEADERS, "X-OnFlows-Athlete-Alias": "pilot"}
    actual_z3 = []
    for tmax_minutes in (100., 200.):
        z2_reference["speed_kmh"] = curve.speed(tmax_minutes * 60.) * 3.6
        load_response = client.get("/api/v2/real/load-history", headers=headers)
        report_response = client.get("/api/v2/real/completed-work", headers=headers,
            params={"period_start": "2026-08-15", "period_end": "2026-08-15"})
        assert load_response.status_code == report_response.status_code == 200
        load, report = load_response.json(), report_response.json()
        for report_zone in report["zones"]:
            expected = sum(row["effective_load"] for activity in load["activities"]
                           for row in activity["zones"] if row["zone"] == report_zone["zone"])
            assert report_zone["effective_load"] == pytest.approx(expected)
        actual_z3.append(next(row["effective_load"] for row in report["zones"] if row["zone"] == "Z3"))
    assert actual_z3 == pytest.approx([12., 0.])
    assert repository.latest("pilot") == persisted
