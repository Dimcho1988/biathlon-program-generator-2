from __future__ import annotations

import pytest

from intervals_inspector.security import (
    StateExpiredError,
    StateValidationError,
    create_signed_state,
    verify_signed_state,
)


REDIRECT_URI = "http://localhost:8501/"
SECRET = "test-state-secret-with-enough-entropy"


def test_signed_state_round_trip_binds_nonce_time_and_redirect() -> None:
    state = create_signed_state(
        SECRET,
        redirect_uri=REDIRECT_URI,
        now=1_000,
        nonce="fixed-nonce",
    )

    verified = verify_signed_state(
        state,
        SECRET,
        expected_redirect_uri=REDIRECT_URI,
        max_age_seconds=600,
        now=1_599,
    )

    assert verified.nonce == "fixed-nonce"
    assert verified.issued_at == 1_000
    assert verified.redirect_uri == REDIRECT_URI


def test_tampered_state_is_rejected() -> None:
    state = create_signed_state(
        SECRET, redirect_uri=REDIRECT_URI, now=1_000
    )
    payload, signature = state.split(".")
    tampered = f"{payload[:-1]}A.{signature}"

    with pytest.raises(StateValidationError):
        verify_signed_state(
            tampered,
            SECRET,
            expected_redirect_uri=REDIRECT_URI,
            now=1_001,
        )


def test_expired_state_is_rejected() -> None:
    state = create_signed_state(
        SECRET, redirect_uri=REDIRECT_URI, now=1_000
    )

    with pytest.raises(StateExpiredError):
        verify_signed_state(
            state,
            SECRET,
            expected_redirect_uri=REDIRECT_URI,
            max_age_seconds=600,
            now=1_601,
        )


def test_state_cannot_be_replayed_with_another_redirect_uri() -> None:
    state = create_signed_state(
        SECRET, redirect_uri=REDIRECT_URI, now=1_000
    )

    with pytest.raises(StateValidationError):
        verify_signed_state(
            state,
            SECRET,
            expected_redirect_uri="https://example.invalid/callback",
            now=1_001,
        )


@pytest.mark.parametrize("state", ["é.invalid", "x" * 4097])
def test_non_ascii_or_oversized_state_is_safely_rejected(state: str) -> None:
    with pytest.raises(StateValidationError):
        verify_signed_state(
            state,
            SECRET,
            expected_redirect_uri=REDIRECT_URI,
            now=1_001,
        )
