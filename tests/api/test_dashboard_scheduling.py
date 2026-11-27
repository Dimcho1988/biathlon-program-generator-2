"""Pending read models get a bounded turn before long planning tasks."""
from threading import Event
from types import SimpleNamespace

import pytest

from apps.api import background_planning


@pytest.mark.parametrize("read_kind", ["dashboard", "learning", "view"])
@pytest.mark.parametrize("slow_task", ["queued", "automatic"])
def test_read_model_is_prepared_before_long_planning(slow_task, read_kind):
    stopping, entered, release, ready = Event(), Event(), Event(), Event()

    def slow():
        entered.set()
        assert release.wait(2)
        return True

    repository = SimpleNamespace(
        process_next_planning_job=slow if slow_task == "queued" else lambda: False,
        refresh_due_training_plans=slow if slow_task == "automatic" else lambda: False,
        **{f"process_next_prepared_{read_kind}": ready.set},
    )
    worker = background_planning.start(repository, stopping)
    try:
        assert entered.wait(1)
        assert ready.is_set(), "Read model is blocked behind a long planning task"
    finally:
        stopping.set()
        release.set()
        worker.join(2)
    assert not worker.is_alive()


@pytest.mark.parametrize("read_kind", ["dashboard", "learning", "view"])
def test_failed_read_model_does_not_block_planning(caplog, read_kind):
    stopping, planned = Event(), Event()

    def dashboard():
        raise RuntimeError("private failure detail")

    def plan():
        planned.set()
        stopping.set()
        return True

    worker = background_planning.start(SimpleNamespace(
        process_next_planning_job=plan,
        **{f"process_next_prepared_{read_kind}": dashboard},
    ), stopping)
    worker.join(2)
    assert not worker.is_alive() and planned.is_set()
    assert "tick_deferred" in caplog.text
    assert "private failure detail" not in caplog.text


def test_pending_read_models_do_not_starve_a_busy_planning_queue(monkeypatch):
    monkeypatch.setattr(background_planning, "PREPARATION_JOBS", 3)
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
        process_next_prepared_learning=lambda: (calls.append("learning"), True)[1],
        process_next_prepared_view=lambda: (calls.append("view"), True)[1],
    ), stopping)
    worker.join(2)
    assert not worker.is_alive()
    assert calls == ["dashboard", "learning", "view", "plan"] * 2


@pytest.mark.parametrize("busy", [True, False])
def test_page_polling_is_throttled_only_while_planning_is_busy(monkeypatch, busy):
    stopping = Event()
    calls = []
    monkeypatch.setattr(background_planning, "monotonic", lambda: 0.)

    def page():
        calls.append("page")
        return True

    def plan():
        calls.append("plan")
        if calls.count("plan") == 3:
            stopping.set()
        return busy

    worker = background_planning.start(SimpleNamespace(
        process_next_planning_job=plan,
        refresh_due_training_plans=lambda: False,
        process_next_prepared_view=page,
    ), stopping)
    worker.join(2)
    assert not worker.is_alive()
    assert calls.count("plan") == 3
    assert calls.count("page") == background_planning.PREPARATION_JOBS * (1 if busy else 3)


@pytest.mark.parametrize("automatic", [False, True])
def test_all_pending_athletes_and_pages_finish_before_long_plan(automatic):
    stopping, entered, release = Event(), Event(), Event()
    pending = {"dashboard": ["first", "second"], "learning": ["first", "second"],
               "view": ["speed", "trainability", "response", "active", "overview"]}
    prepared = []

    def prepare(kind):
        if not pending[kind]:
            return False
        prepared.append((kind, pending[kind].pop(0)))
        return True

    def plan():
        entered.set()
        assert release.wait(2)
        return True

    repo = SimpleNamespace(process_next_planning_job=(lambda: False) if automatic else plan,
        refresh_due_training_plans=plan,
        **{f"process_next_prepared_{kind}": lambda kind=kind: prepare(kind) for kind in pending})
    worker = background_planning.start(repo, stopping)
    try:
        assert entered.wait(1)
        assert all(not values for values in pending.values()), "Analyses still wait behind the plan"
        assert len(prepared) == 9
    finally:
        stopping.set()
        release.set()
        worker.join(2)
    assert not worker.is_alive()


def test_preparation_time_budget_leaves_a_turn_for_planning(monkeypatch):
    clock = [0.]
    monkeypatch.setattr(background_planning, "monotonic", lambda: clock[0])
    stopping = Event()
    prepared = []

    def prepare():
        prepared.append(clock[0])
        clock[0] += 50.
        return True

    def plan():
        stopping.set()
        return True

    worker = background_planning.start(SimpleNamespace(process_next_planning_job=plan,
        process_next_prepared_view=prepare), stopping)
    worker.join(2)
    assert not worker.is_alive()
    assert prepared == [0., 50., 100.]


def test_failed_queue_is_not_retried_inside_batch_but_other_pages_drain():
    calls = []
    pages = [1, 2, 3]
    def broken():
        calls.append("broken")
        raise RuntimeError("offline")
    def page():
        if not pages:
            return False
        pages.pop()
        return True
    assert background_planning.prepare_pending(SimpleNamespace(
        process_next_prepared_learning=broken, process_next_prepared_view=page), Event()) == 3
    assert calls == ["broken"]
