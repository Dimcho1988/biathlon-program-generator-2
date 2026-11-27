"""A pending dashboard must be readable while a long planning task runs."""
from threading import Event
from types import SimpleNamespace

import pytest

from apps.api import background_planning


@pytest.mark.parametrize("slow_task", ["queued", "automatic"])
def test_dashboard_is_prepared_before_long_planning(slow_task):
    stopping, entered, release, ready = Event(), Event(), Event(), Event()

    def slow():
        entered.set()
        assert release.wait(2)
        return True

    repository = SimpleNamespace(
        process_next_planning_job=slow if slow_task == "queued" else lambda: False,
        refresh_due_training_plans=slow if slow_task == "automatic" else lambda: False,
        process_next_prepared_dashboard=ready.set,
    )
    worker = background_planning.start(repository, stopping)
    try:
        assert entered.wait(1)
        assert ready.is_set(), "Dashboard is blocked behind a long planning task"
    finally:
        stopping.set()
        release.set()
        worker.join(2)
    assert not worker.is_alive()


def test_failed_dashboard_does_not_block_planning(caplog):
    stopping, planned = Event(), Event()

    def dashboard():
        raise RuntimeError("private failure detail")

    def plan():
        planned.set()
        stopping.set()
        return True

    worker = background_planning.start(SimpleNamespace(
        process_next_planning_job=plan,
        process_next_prepared_dashboard=dashboard,
    ), stopping)
    worker.join(2)
    assert not worker.is_alive() and planned.is_set()
    assert "dashboard_tick_deferred" in caplog.text
    assert "private failure detail" not in caplog.text


def test_pending_dashboards_do_not_starve_a_busy_planning_queue(monkeypatch):
    stopping = Event()
    calls = []
    clock = [0.]
    monkeypatch.setattr(background_planning, "monotonic", lambda: clock[0])

    def dashboard():
        calls.append("dashboard")
        return True

    def plan():
        calls.append("plan")
        clock[0] += 16.
        if calls.count("plan") == 2:
            stopping.set()
        return True

    worker = background_planning.start(SimpleNamespace(
        process_next_planning_job=plan,
        process_next_prepared_dashboard=dashboard,
    ), stopping)
    worker.join(2)
    assert not worker.is_alive()
    assert calls == ["dashboard", "plan", "dashboard", "plan"]
