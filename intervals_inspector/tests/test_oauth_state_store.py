from __future__ import annotations

from intervals_inspector.oauth_state_store import PendingStateStore


def test_pending_state_is_consumed_exactly_once() -> None:
    now = [1_000.0]
    store = PendingStateStore(clock=lambda: now[0])
    state = "opaque-signed-state"

    store.register(state)

    assert store.is_pending(state)
    assert store.consume(state)
    assert not store.is_pending(state)
    assert not store.consume(state)


def test_store_accepts_ttl_boundary_and_rejects_one_second_later() -> None:
    now = [1_000.0]
    store = PendingStateStore(ttl_seconds=600, clock=lambda: now[0])
    boundary_state = "boundary-state"
    expired_state = "expired-state"
    store.register(boundary_state)
    store.register(expired_state)

    now[0] = 1_600.0
    assert store.consume(boundary_state)

    now[0] = 1_601.0
    assert not store.consume(expired_state)
    assert not store.is_pending(expired_state)


def test_tampered_state_does_not_consume_registered_state() -> None:
    store = PendingStateStore(clock=lambda: 1_000.0)
    state = "signed-state.original"
    store.register(state)

    assert not store.consume("signed-state.tampered")
    assert store.is_pending(state)


def test_store_is_bounded_and_evicts_oldest_pending_state() -> None:
    now = [1_000.0]
    store = PendingStateStore(max_entries=2, clock=lambda: now[0])
    store.register("state-one")
    now[0] += 1
    store.register("state-two")
    now[0] += 1
    store.register("state-three")

    assert not store.is_pending("state-one")
    assert store.is_pending("state-two")
    assert store.is_pending("state-three")
