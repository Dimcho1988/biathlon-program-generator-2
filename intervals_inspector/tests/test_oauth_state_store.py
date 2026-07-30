from __future__ import annotations

import pytest

from intervals_inspector.oauth_state_store import (
    PendingConsentEvidence,
    PendingStateStore,
)


def _complete_consent() -> PendingConsentEvidence:
    return PendingConsentEvidence(
        policy_version="1.0",
        confirmed_at=1_000.0,
        privacy_and_general_consent=True,
        wellness_health_explicit_consent=True,
        adult_confirmed=True,
    )


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


def test_consent_evidence_is_returned_once_with_consumed_state() -> None:
    store = PendingStateStore(clock=lambda: 1_000.0)
    consent = _complete_consent()

    store.register("consented-state", consent=consent)

    assert store.consume_with_consent("consented-state") == consent
    assert store.consume_with_consent("consented-state") is None


@pytest.mark.parametrize(
    "incomplete_consent",
    (
        PendingConsentEvidence(
            policy_version="",
            confirmed_at=1_000.0,
            privacy_and_general_consent=True,
            wellness_health_explicit_consent=True,
            adult_confirmed=True,
        ),
        PendingConsentEvidence(
            policy_version="1.0",
            confirmed_at=1_000.0,
            privacy_and_general_consent=False,
            wellness_health_explicit_consent=True,
            adult_confirmed=True,
        ),
        PendingConsentEvidence(
            policy_version="1.0",
            confirmed_at=1_000.0,
            privacy_and_general_consent=True,
            wellness_health_explicit_consent=False,
            adult_confirmed=True,
        ),
        PendingConsentEvidence(
            policy_version="1.0",
            confirmed_at=1_000.0,
            privacy_and_general_consent=True,
            wellness_health_explicit_consent=True,
            adult_confirmed=False,
        ),
    ),
)
def test_incomplete_consent_evidence_cannot_be_registered(
    incomplete_consent: PendingConsentEvidence,
) -> None:
    store = PendingStateStore(clock=lambda: 1_000.0)

    with pytest.raises(ValueError, match="consent evidence"):
        store.register("state", consent=incomplete_consent)

    assert not store.is_pending("state")
