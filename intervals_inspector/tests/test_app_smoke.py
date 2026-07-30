from __future__ import annotations

from pathlib import Path
import time
from urllib.parse import parse_qs, urlparse

import pytest
from streamlit.testing.v1 import AppTest

from intervals_inspector import app as inspector_app
from intervals_inspector.oauth import OAuthGrant
from intervals_inspector.oauth_state_store import (
    clear_pending_states_for_tests,
    is_pending_state,
)
from intervals_inspector.security import create_signed_state


APP_PATH = Path(__file__).resolve().parents[1] / "app.py"
SETTING_NAMES = (
    "INTERVALS_CLIENT_ID",
    "INTERVALS_CLIENT_SECRET",
    "INTERVALS_REDIRECT_URI",
    "OAUTH_STATE_SECRET",
    "INSPECTOR_ACCESS_PASSWORD",
)
FAKE_SECRETS = {
    "INTERVALS_CLIENT_ID": "test-client",
    "INTERVALS_CLIENT_SECRET": "test-client-secret-not-real",
    "INTERVALS_REDIRECT_URI": "https://pilot.example/",
    "OAUTH_STATE_SECRET": "test-state-secret-not-real",
    "INSPECTOR_ACCESS_PASSWORD": "test-password-not-real",
}


@pytest.fixture(autouse=True)
def _isolated_pending_state_store():
    clear_pending_states_for_tests()
    yield
    clear_pending_states_for_tests()


def _new_app(
    secrets: dict[str, str] | None = None,
) -> AppTest:
    app = AppTest.from_file(str(APP_PATH), default_timeout=10)
    app.secrets.update(FAKE_SECRETS if secrets is None else secrets)
    return app


def test_streamlit_smoke_reports_missing_configuration(monkeypatch):
    for name in SETTING_NAMES:
        monkeypatch.setenv(name, f"ignored-environment-{name}")

    app = _new_app({}).run()

    assert not app.exception
    assert app.title[0].value == (
        "onFlows — Intervals.icu Data Inspector (TEST ONLY)"
    )
    rendered_codes = {item.value for item in app.code}
    assert set(SETTING_NAMES) <= rendered_codes


def test_blank_streamlit_secrets_are_reported_missing():
    app = _new_app(
        {
            name: "618" if name == "INTERVALS_CLIENT_ID" else ""
            for name in SETTING_NAMES
        }
    ).run()

    rendered_codes = {item.value for item in app.code}
    assert set(SETTING_NAMES) - {"INTERVALS_CLIENT_ID"} <= rendered_codes
    assert "INTERVALS_CLIENT_ID" not in rendered_codes


def test_streamlit_smoke_shows_password_gate_without_network():
    app = _new_app().run()

    assert not app.exception
    assert app.text_input[0].label == "Парола"
    assert app.button[0].label == "Вход"


def test_unicode_access_password_is_supported():
    secrets = dict(FAKE_SECRETS)
    secrets["INSPECTOR_ACCESS_PASSWORD"] = "сигурна-парола-🔒"
    app = _new_app(secrets).run()

    app.text_input[0].set_value("сигурна-парола-🔒")
    app.button[0].click()
    app.run()

    assert not app.exception
    assert (
        app.session_state[inspector_app.SESSION_AUTHENTICATED]
        is True
    )


def test_pending_state_survives_two_real_apptest_sessions():
    issuing_app = _new_app()
    issuing_app.session_state["_inspector_authenticated"] = True
    issuing_app.run()

    assert not issuing_app.exception
    link_buttons = issuing_app.get("link_button")
    assert len(link_buttons) == 1
    query = parse_qs(urlparse(link_buttons[0].url).query)
    state = query["state"][0]

    callback_app = _new_app()
    callback_app.query_params["error"] = "access_denied"
    callback_app.query_params["state"] = state
    callback_app.run()

    assert not callback_app.exception
    assert any(
        "OAuth достъпът беше отказан" in item.value
        for item in callback_app.warning
    )
    assert all(
        "state-ът е невалиден" not in item.value
        for item in callback_app.error
    )


def test_connected_profile_name_and_athlete_id_render_as_literal_text():
    untrusted_name = "![pixel](https://attacker.invalid/tracker)"
    app = _new_app()
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
    assert any(
        item.value == "Intervals athlete ID: test-athlete"
        for item in app.text
    )
    assert all(
        untrusted_name not in item.value
        for item in app.markdown
    )


def test_password_reentry_preserves_token_received_before_login():
    app = _new_app()
    app.session_state["_intervals_access_token"] = "session-token-not-real"
    app.session_state["_intervals_athlete_id"] = "test-athlete"
    app.session_state["_intervals_athlete_name"] = "Test Athlete"
    app.session_state["_intervals_granted_scopes"] = [
        "ACTIVITY:READ",
        "WELLNESS:READ",
        "SETTINGS:READ",
        "CALENDAR:READ",
    ]

    app.run()

    assert not app.exception
    assert app.text_input[0].label == "Парола"
    assert (
        app.session_state["_intervals_access_token"]
        == "session-token-not-real"
    )

    app.text_input[0].set_value("test-password-not-real")
    app.button[0].click()
    app.run()

    assert not app.exception
    assert (
        app.session_state["_intervals_access_token"]
        == "session-token-not-real"
    )
    assert any(item.value == "OAuth статус: свързан." for item in app.success)


def test_access_tokens_are_isolated_between_apptest_sessions():
    first = _new_app()
    first.session_state[inspector_app.SESSION_AUTHENTICATED] = True
    first.session_state[inspector_app.SESSION_TOKEN] = "isolated-session-A"
    first.session_state[inspector_app.SESSION_ATHLETE_ID] = "athlete-A"
    first.session_state[inspector_app.SESSION_ATHLETE_NAME] = "Athlete A"
    first.session_state[inspector_app.SESSION_SCOPES] = list(
        inspector_app.READ_ONLY_SCOPES
    )
    first.run()

    second = _new_app()
    second.session_state[inspector_app.SESSION_AUTHENTICATED] = True
    second.run()

    assert not first.exception
    assert not second.exception
    assert (
        first.session_state[inspector_app.SESSION_TOKEN]
        == "isolated-session-A"
    )
    assert "isolated-session-A" not in repr(second.session_state)
    assert len(second.get("link_button")) == 1


def test_connected_report_renders_bounded_periods_and_summary_table():
    app = _new_app()
    app.session_state[inspector_app.SESSION_AUTHENTICATED] = True
    app.session_state[inspector_app.SESSION_TOKEN] = "test-token-not-real"
    app.session_state[inspector_app.SESSION_ATHLETE_ID] = "test-athlete"
    app.session_state[inspector_app.SESSION_ATHLETE_NAME] = "Test Athlete"
    app.session_state[inspector_app.SESSION_SCOPES] = list(
        inspector_app.READ_ONLY_SCOPES
    )
    app.session_state[inspector_app.SESSION_REPORT] = {
        "period_days": 7,
        "counts": {
            "activities": 0,
            "wellness": 0,
            "calendar": 0,
            "planned_workouts": 0,
        },
        "coverage": {
            "profile": [],
            "sport_settings": [],
            "activities": [],
            "wellness": [],
            "calendar": [],
            "planned_workouts": [],
        },
        "streams": [],
        "endpoint_checks": [
            {
                "category": "Профил",
                "endpoint": "/api/v1/athlete/{athlete_id}",
                "http_status": 200,
                "available": True,
                "record_count": 1,
                "field_names": ["timezone"],
                "safe_error": "",
            }
        ],
    }
    app.session_state[inspector_app.SESSION_ACTIVITY_CHOICES] = []

    app.run()

    assert not app.exception
    assert tuple(app.radio[0].options) == (
        "7 дни",
        "14 дни",
        "30 дни",
    )
    assert len(app.dataframe) >= 1
    assert any(
        tab.label == "API проверки" for tab in app.tabs
    )


def test_disconnect_clears_only_current_session_and_callback_query(
    monkeypatch,
):
    session = {
        inspector_app.SESSION_TOKEN: "session-token-not-real",
        inspector_app.SESSION_ATHLETE_ID: "test-athlete",
        inspector_app.SESSION_AUTHENTICATED: True,
    }
    query = {"code": "must-be-cleared", "state": "must-be-cleared"}
    reruns: list[bool] = []
    monkeypatch.setattr(
        inspector_app.st, "session_state", session, raising=False
    )
    monkeypatch.setattr(
        inspector_app.st, "query_params", query, raising=False
    )
    monkeypatch.setattr(
        inspector_app.st, "rerun", lambda: reruns.append(True)
    )

    inspector_app._disconnect()

    assert session == {}
    assert query == {}
    assert reruns == [True]


def test_changing_selected_activity_discards_previous_activity_report(
    monkeypatch,
):
    previous_report = {
        "coverage": {"activity_detail": [{"json_path": "id"}]}
    }
    session = {
        inspector_app.SESSION_ACTIVITY_REPORT_ID: "activity-A",
        inspector_app.SESSION_ACTIVITY_REPORT: previous_report,
    }
    monkeypatch.setattr(
        inspector_app.st, "session_state", session, raising=False
    )

    assert (
        inspector_app._activity_report_for_selection("activity-A")
        is previous_report
    )
    assert (
        inspector_app._activity_report_for_selection("activity-B")
        is None
    )
    assert inspector_app.SESSION_ACTIVITY_REPORT not in session
    assert inspector_app.SESSION_ACTIVITY_REPORT_ID not in session


def _callback_config() -> inspector_app.InspectorConfig:
    return inspector_app.InspectorConfig(
        client_id="test-client",
        client_secret="test-client-secret-not-real",
        redirect_uri="https://pilot.example/",
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

    inspector_app._process_callback(config)

    assert calls == []
    assert query == {}
    assert inspector_app.SESSION_TOKEN not in session


def test_issued_state_exchanges_once_in_fresh_session_and_cannot_replay(
    monkeypatch,
):
    config = _callback_config()
    issuing_session: dict[str, object] = {}
    monkeypatch.setattr(
        inspector_app.st, "session_state", issuing_session, raising=False
    )
    state = inspector_app._oauth_state(config)
    assert issuing_session == {}

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

    inspector_app._process_callback(config)

    assert len(calls) == 1
    assert calls[0]["redact_values"] == (state,)
    assert query == {}
    assert session[inspector_app.SESSION_TOKEN] == "session-token-not-real"
    assert session[inspector_app.SESSION_ATHLETE_ID] == "test-athlete"
    assert inspector_app.SESSION_AUTHENTICATED not in session
    rendered_session = repr(session)
    assert "one-time-code" not in rendered_session
    assert config.client_secret not in rendered_session
    assert state not in rendered_session

    replay_query = {"code": "second-code", "state": state}
    replay_session = _patch_callback_streamlit(monkeypatch, replay_query)
    inspector_app._process_callback(config)

    assert len(calls) == 1
    assert replay_query == {}
    assert inspector_app.SESSION_TOKEN not in replay_session


def test_denied_callback_consumes_issued_state(monkeypatch):
    config = _callback_config()
    state = inspector_app._oauth_state(config)
    query = {"error": "access_denied", "state": state}
    session = _patch_callback_streamlit(monkeypatch, query)

    inspector_app._process_callback(config)

    assert query == {}
    assert not is_pending_state(state)
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
    state = inspector_app._oauth_state(config)
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

    inspector_app._process_callback(config)

    assert query == {}
    assert inspector_app.SESSION_TOKEN not in session
    assert "overprivileged-token" not in repr(session)


def test_tampered_state_never_exchanges_and_does_not_consume_original(
    monkeypatch,
):
    config = _callback_config()
    state = inspector_app._oauth_state(config)
    payload, signature = state.split(".")
    replacement = "A" if signature[-1] != "A" else "B"
    tampered = f"{payload}.{signature[:-1]}{replacement}"
    query = {"code": "one-time-code", "state": tampered}
    session = _patch_callback_streamlit(monkeypatch, query)
    calls = []

    monkeypatch.setattr(
        inspector_app,
        "exchange_authorization_code",
        lambda **kwargs: calls.append(kwargs),
    )

    inspector_app._process_callback(config)

    assert calls == []
    assert query == {}
    assert inspector_app.SESSION_TOKEN not in session
    assert is_pending_state(state)


def test_expired_signed_state_never_exchanges(monkeypatch):
    config = _callback_config()
    state = create_signed_state(
        config.state_secret,
        redirect_uri=config.redirect_uri,
        now=int(time.time()) - inspector_app.STATE_MAX_AGE_SECONDS - 1,
    )
    inspector_app.register_pending_state(state)
    query = {"code": "one-time-code", "state": state}
    session = _patch_callback_streamlit(monkeypatch, query)
    calls = []

    monkeypatch.setattr(
        inspector_app,
        "exchange_authorization_code",
        lambda **kwargs: calls.append(kwargs),
    )

    inspector_app._process_callback(config)

    assert calls == []
    assert query == {}
    assert inspector_app.SESSION_TOKEN not in session


def test_failed_exchange_consumes_state_and_is_not_retried(monkeypatch):
    config = _callback_config()
    state = inspector_app._oauth_state(config)
    query = {"code": "one-time-code", "state": state}
    first_session = _patch_callback_streamlit(monkeypatch, query)
    calls = []

    def failed_exchange(**kwargs):
        calls.append(kwargs)
        raise inspector_app.OAuthExchangeError("sanitized failure")

    monkeypatch.setattr(
        inspector_app, "exchange_authorization_code", failed_exchange
    )

    inspector_app._process_callback(config)

    replay_query = {"code": "one-time-code", "state": state}
    replay_session = _patch_callback_streamlit(
        monkeypatch, replay_query
    )
    inspector_app._process_callback(config)

    assert len(calls) == 1
    assert query == {}
    assert replay_query == {}
    assert inspector_app.SESSION_TOKEN not in first_session
    assert inspector_app.SESSION_TOKEN not in replay_session


def test_exchange_failure_notice_is_sanitized_and_actionable(monkeypatch):
    config = _callback_config()
    state = inspector_app._oauth_state(config)
    query = {"code": "one-time-code", "state": state}
    session = _patch_callback_streamlit(monkeypatch, query)

    def failed_exchange(**kwargs):
        raise inspector_app.OAuthExchangeError(
            "Intervals.icu отказа OAuth token заявката "
            "(HTTP 400; error=invalid_grant; "
            "error_description=Authorization code expired)."
        )

    monkeypatch.setattr(
        inspector_app, "exchange_authorization_code", failed_exchange
    )

    inspector_app._process_callback(config)

    notice = session[inspector_app.SESSION_NOTICE]
    assert notice["level"] == "error"
    assert "HTTP 400" in notice["message"]
    assert "error=invalid_grant" in notice["message"]
    assert "error_description=Authorization code expired" in notice["message"]
    rendered = repr(session)
    assert "one-time-code" not in rendered
    assert state not in rendered
    assert config.client_secret not in rendered


def test_main_processes_callback_before_password_gate() -> None:
    source = Path(inspector_app.__file__).read_text(encoding="utf-8")
    main_source = source[source.index("def main()") :]

    assert main_source.index("_process_callback(config)") < main_source.index(
        "_password_gate(config)"
    )
