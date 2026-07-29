from __future__ import annotations

from pathlib import Path

from streamlit.testing.v1 import AppTest

from intervals_inspector import app as inspector_app
from intervals_inspector.oauth import OAuthGrant
from intervals_inspector.security import create_signed_state


APP_PATH = Path(__file__).resolve().parents[1] / "app.py"
SETTING_NAMES = (
    "INTERVALS_CLIENT_ID",
    "INTERVALS_CLIENT_SECRET",
    "INTERVALS_REDIRECT_URI",
    "OAUTH_STATE_SECRET",
    "INSPECTOR_ACCESS_PASSWORD",
)


def test_streamlit_smoke_reports_missing_configuration(monkeypatch):
    for name in SETTING_NAMES:
        monkeypatch.delenv(name, raising=False)

    app = AppTest.from_file(str(APP_PATH), default_timeout=10).run()

    assert not app.exception
    assert app.title[0].value == (
        "onFlows — Intervals.icu Data Inspector (TEST ONLY)"
    )
    rendered_codes = {item.value for item in app.code}
    assert set(SETTING_NAMES) <= rendered_codes


def test_streamlit_smoke_shows_password_gate_without_network(monkeypatch):
    fake_values = {
        "INTERVALS_CLIENT_ID": "test-client",
        "INTERVALS_CLIENT_SECRET": "test-client-secret-not-real",
        "INTERVALS_REDIRECT_URI": "http://localhost:8501/",
        "OAUTH_STATE_SECRET": "test-state-secret-not-real",
        "INSPECTOR_ACCESS_PASSWORD": "test-password-not-real",
    }
    for name, value in fake_values.items():
        monkeypatch.setenv(name, value)

    app = AppTest.from_file(str(APP_PATH), default_timeout=10).run()

    assert not app.exception
    assert app.text_input[0].label == "Парола"
    assert app.button[0].label == "Вход"


def test_connected_profile_name_is_rendered_as_literal_text(monkeypatch):
    fake_values = {
        "INTERVALS_CLIENT_ID": "test-client",
        "INTERVALS_CLIENT_SECRET": "test-client-secret-not-real",
        "INTERVALS_REDIRECT_URI": "http://localhost:8501/",
        "OAUTH_STATE_SECRET": "test-state-secret-not-real",
        "INSPECTOR_ACCESS_PASSWORD": "test-password-not-real",
    }
    for name, value in fake_values.items():
        monkeypatch.setenv(name, value)

    untrusted_name = "![pixel](https://attacker.invalid/tracker)"
    app = AppTest.from_file(str(APP_PATH), default_timeout=10)
    app.session_state["_inspector_authenticated"] = True
    app.session_state["_intervals_access_token"] = "test-token-not-real"
    app.session_state["_intervals_athlete_id"] = "test-athlete"
    app.session_state["_intervals_athlete_name"] = untrusted_name
    app.session_state["_intervals_granted_scopes"] = [
        "ACTIVITY:READ",
        "WELLNESS:READ",
        "SETTINGS:READ",
        "CALENDAR:READ",
    ]

    app.run()

    assert not app.exception
    assert any(
        item.value == f"Свързан профил: {untrusted_name}"
        for item in app.text
    )
    assert all(
        untrusted_name not in item.value
        for item in app.markdown
    )


def test_pending_state_registry_is_one_time(monkeypatch):
    now = 1_000.0
    monkeypatch.setattr(inspector_app.time, "time", lambda: now)
    inspector_app._pending_state_registry.clear()

    inspector_app._register_pending_state("state-digest", now)

    assert inspector_app._pending_state_is_issued("state-digest")
    assert inspector_app._consume_pending_state("state-digest")
    assert not inspector_app._pending_state_is_issued("state-digest")
    assert not inspector_app._consume_pending_state("state-digest")


def _callback_config() -> inspector_app.InspectorConfig:
    return inspector_app.InspectorConfig(
        client_id="test-client",
        client_secret="test-client-secret-not-real",
        redirect_uri="http://localhost:8501/",
        state_secret="test-state-secret-not-real",
        access_password="test-password-not-real",
    )


def _patch_callback_streamlit(
    monkeypatch,
    query: dict[str, str],
) -> dict[str, object]:
    session: dict[str, object] = {}
    monkeypatch.setattr(
        inspector_app.st, "query_params", query, raising=False
    )
    monkeypatch.setattr(
        inspector_app.st, "session_state", session, raising=False
    )
    monkeypatch.setattr(inspector_app.st, "rerun", lambda: None)
    return session


def test_unissued_callback_state_never_exchanges_and_is_cleared(monkeypatch):
    config = _callback_config()
    state = create_signed_state(
        config.state_secret,
        redirect_uri=config.redirect_uri,
    )
    query = {"code": "one-time-code", "state": state}
    session = _patch_callback_streamlit(monkeypatch, query)
    calls = []

    def exchange(**kwargs):
        calls.append(kwargs)
        raise AssertionError("exchange must not run")

    monkeypatch.setattr(
        inspector_app, "exchange_authorization_code", exchange
    )
    inspector_app._pending_state_registry.clear()

    inspector_app._process_callback(config)

    assert calls == []
    assert query == {}
    assert inspector_app.SESSION_TOKEN not in session


def test_issued_state_exchanges_once_in_fresh_session_and_cannot_replay(
    monkeypatch,
):
    config = _callback_config()
    state = create_signed_state(
        config.state_secret,
        redirect_uri=config.redirect_uri,
    )
    digest = inspector_app._state_digest(state)
    query = {"code": "one-time-code", "state": state}
    session = _patch_callback_streamlit(monkeypatch, query)
    calls = []

    def exchange(**kwargs):
        calls.append(kwargs)
        return OAuthGrant(
            access_token="session-token-not-real",
            athlete_id="test-athlete",
            athlete_name="Test Athlete",
            scopes=tuple(inspector_app.READ_ONLY_SCOPES),
        )

    monkeypatch.setattr(
        inspector_app, "exchange_authorization_code", exchange
    )
    inspector_app._pending_state_registry.clear()
    inspector_app._register_pending_state(digest, inspector_app.time.time())

    inspector_app._process_callback(config)

    assert len(calls) == 1
    assert query == {}
    assert session[inspector_app.SESSION_TOKEN] == "session-token-not-real"
    assert session[inspector_app.SESSION_ATHLETE_ID] == "test-athlete"
    rendered_session = repr(session)
    assert "one-time-code" not in rendered_session
    assert config.client_secret not in rendered_session
    assert state not in rendered_session

    query.update({"code": "second-code", "state": state})
    inspector_app._process_callback(config)

    assert len(calls) == 1
    assert query == {}


def test_denied_callback_consumes_issued_state(monkeypatch):
    config = _callback_config()
    state = create_signed_state(
        config.state_secret,
        redirect_uri=config.redirect_uri,
    )
    digest = inspector_app._state_digest(state)
    query = {"error": "access_denied", "state": state}
    session = _patch_callback_streamlit(monkeypatch, query)
    inspector_app._pending_state_registry.clear()
    inspector_app._register_pending_state(digest, inspector_app.time.time())

    inspector_app._process_callback(config)

    assert query == {}
    assert not inspector_app._pending_state_is_issued(digest)
    assert inspector_app.SESSION_TOKEN not in session
    assert session[inspector_app.SESSION_NOTICE]["level"] == "warning"


def test_documented_denial_without_state_is_clear_and_never_exchanges(
    monkeypatch,
):
    config = _callback_config()
    query = {"error": "access_denied"}
    session = _patch_callback_streamlit(monkeypatch, query)
    calls = []

    def exchange(**kwargs):
        calls.append(kwargs)
        raise AssertionError("exchange must not run for a denial")

    monkeypatch.setattr(
        inspector_app, "exchange_authorization_code", exchange
    )

    inspector_app._process_callback(config)

    assert calls == []
    assert query == {}
    assert inspector_app.SESSION_TOKEN not in session
    assert session[inspector_app.SESSION_NOTICE] == {
        "level": "warning",
        "message": "OAuth достъпът беше отказан в Intervals.icu.",
    }


def test_app_rejects_write_scope_before_storing_token(monkeypatch):
    config = _callback_config()
    state = create_signed_state(
        config.state_secret,
        redirect_uri=config.redirect_uri,
    )
    digest = inspector_app._state_digest(state)
    query = {"code": "one-time-code", "state": state}
    session = _patch_callback_streamlit(monkeypatch, query)

    def exchange(**kwargs):
        return OAuthGrant(
            access_token="overprivileged-token",
            athlete_id="test-athlete",
            athlete_name="Test Athlete",
            scopes=("ACTIVITY:READ", "CALENDAR:WRITE"),
        )

    monkeypatch.setattr(
        inspector_app, "exchange_authorization_code", exchange
    )
    inspector_app._pending_state_registry.clear()
    inspector_app._register_pending_state(digest, inspector_app.time.time())

    inspector_app._process_callback(config)

    assert query == {}
    assert inspector_app.SESSION_TOKEN not in session
    assert "overprivileged-token" not in repr(session)
