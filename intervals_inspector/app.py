"""Isolated, read-only Streamlit inspector for Intervals.icu OAuth data."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
import hashlib
import hmac
import os
import threading
import time
from typing import Any

import streamlit as st

from intervals_inspector.intervals_client import (
    IntervalsAPIError,
    IntervalsClient,
)
from intervals_inspector.inventory import (
    build_field_coverage,
    export_inventory_csv,
    export_inventory_json,
    summarize_streams,
)
from intervals_inspector.oauth import (
    READ_ONLY_SCOPES,
    OAuthAccessDenied,
    OAuthCallbackError,
    OAuthExchangeError,
    StateValidationError,
    build_authorization_url,
    create_signed_state,
    exchange_authorization_code,
    parse_callback,
    verify_signed_state,
)


CONFIG_NAMES = (
    "INTERVALS_CLIENT_ID",
    "INTERVALS_CLIENT_SECRET",
    "INTERVALS_REDIRECT_URI",
    "OAUTH_STATE_SECRET",
    "INSPECTOR_ACCESS_PASSWORD",
)
CALLBACK_QUERY_KEYS = ("code", "state", "error", "error_description")
MAX_STREAM_ACTIVITIES = 3
STATE_MAX_AGE_SECONDS = 10 * 60
STATE_REFRESH_SECONDS = 8 * 60

SESSION_TOKEN = "_intervals_access_token"
SESSION_ATHLETE_ID = "_intervals_athlete_id"
SESSION_ATHLETE_NAME = "_intervals_athlete_name"
SESSION_SCOPES = "_intervals_granted_scopes"
SESSION_REPORT = "_intervals_inventory_report"
SESSION_AUTHENTICATED = "_inspector_authenticated"
SESSION_PENDING_STATE = "_oauth_pending_state"
SESSION_PENDING_STATE_DIGEST = "_oauth_pending_state_digest"
SESSION_PENDING_STATE_CREATED = "_oauth_pending_state_created"
SESSION_CONSUMED_STATE_DIGESTS = "_oauth_consumed_state_digests"
SESSION_NOTICE = "_inspector_notice"


@dataclass(frozen=True, repr=False)
class InspectorConfig:
    client_id: str
    client_secret: str = field(repr=False)
    redirect_uri: str
    state_secret: str = field(repr=False)
    access_password: str = field(repr=False)


STANDARD_FIELDS: dict[str, set[str]] = {
    "profile": {
        "id",
        "name",
        "timezone",
        "locale",
        "sportSettings",
        "custom_items",
        "created",
        "updated",
    },
    "sport_settings": {
        "id",
        "athlete_id",
        "types",
        "sport",
        "ftp",
        "indoor_ftp",
        "w_prime",
        "p_max",
        "power_zones",
        "lthr",
        "max_hr",
        "resting_hr",
        "hr_zones",
        "threshold_pace",
        "pace_units",
        "warmup_time",
        "cooldown_time",
        "display",
        "custom_items",
    },
    "activities": {
        "id",
        "name",
        "type",
        "start_date",
        "start_date_local",
        "moving_time",
        "elapsed_time",
        "distance",
        "icu_training_load",
        "calories",
        "average_heartrate",
        "max_heartrate",
        "average_watts",
        "weighted_average_watts",
        "normalized_power",
        "average_speed",
        "max_speed",
        "average_cadence",
        "trainer",
        "commute",
        "manual",
        "file_type",
        "stream_types",
        "source",
        "created",
        "updated",
    },
    "wellness": {
        "id",
        "updated",
        "weight",
        "restingHR",
        "hrv",
        "sleepSecs",
        "sleepQuality",
        "soreness",
        "fatigue",
        "stress",
        "mood",
        "motivation",
        "spO2",
        "vo2max",
        "steps",
        "respiration",
        "menstrualPhase",
        "menstrualPhasePredicted",
        "kcalConsumed",
        "fitness",
        "fatigue",
        "form",
    },
    "calendar": {
        "id",
        "category",
        "start_date_local",
        "end_date_local",
        "type",
        "name",
        "icu_training_load",
        "duration",
        "distance",
        "workout_doc",
        "external_id",
        "tags",
        "color",
        "calendar_id",
        "created",
        "updated",
    },
}

SOURCE_ENDPOINTS = {
    "profile": "/api/v1/athlete/{athlete_id}",
    "sport_settings": "/api/v1/athlete/{athlete_id}/sport-settings",
    "activities": "/api/v1/athlete/{athlete_id}/activities",
    "wellness": "/api/v1/athlete/{athlete_id}/wellness",
    "calendar": "/api/v1/athlete/{athlete_id}/events",
    "streams": "/api/v1/activity/{activity_id}/streams.json",
}


def _configuration_value(name: str) -> str | None:
    environment_value = os.environ.get(name)
    if environment_value is not None and environment_value.strip():
        return environment_value

    try:
        secret_value = st.secrets[name]
    except Exception:
        return None
    if secret_value is None:
        return None
    rendered = str(secret_value)
    return rendered if rendered.strip() else None


def _load_configuration() -> tuple[InspectorConfig | None, list[str]]:
    values = {name: _configuration_value(name) for name in CONFIG_NAMES}
    missing = [name for name in CONFIG_NAMES if values[name] is None]
    if missing:
        return None, missing
    return (
        InspectorConfig(
            client_id=values["INTERVALS_CLIENT_ID"] or "",
            client_secret=values["INTERVALS_CLIENT_SECRET"] or "",
            redirect_uri=values["INTERVALS_REDIRECT_URI"] or "",
            state_secret=values["OAUTH_STATE_SECRET"] or "",
            access_password=values["INSPECTOR_ACCESS_PASSWORD"] or "",
        ),
        [],
    )


def _single_query_value(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, Sequence) and not isinstance(
        value, (str, bytes, bytearray)
    ):
        if not value:
            return None
        value = value[0]
    rendered = str(value)
    return rendered if rendered else None


def _callback_query() -> dict[str, str]:
    callback: dict[str, str] = {}
    for key in CALLBACK_QUERY_KEYS:
        value = _single_query_value(st.query_params.get(key))
        if value is not None:
            callback[key] = value
    return callback


def _clear_callback_query() -> None:
    st.query_params.clear()


def _remember_notice(level: str, message: str) -> None:
    st.session_state[SESSION_NOTICE] = {"level": level, "message": message}


def _render_notice() -> None:
    notice = st.session_state.pop(SESSION_NOTICE, None)
    if not isinstance(notice, Mapping):
        return
    message = str(notice.get("message", ""))
    level = str(notice.get("level", "info"))
    renderer = {
        "success": st.success,
        "warning": st.warning,
        "error": st.error,
    }.get(level, st.info)
    renderer(message)


def _state_digest(state: str) -> str:
    return hashlib.sha256(state.encode("utf-8")).hexdigest()


@st.cache_resource(show_spinner=False)
def _pending_state_registry() -> dict[str, Any]:
    """Cross-session, metadata-only one-time state registry.

    Streamlit can create a fresh session after an external OAuth round trip.
    The registry stores only SHA-256 digests and issue times—never a token,
    athlete data, state secret, authorization code, or raw state value.
    """

    return {"lock": threading.Lock(), "issued": {}}


def _register_pending_state(digest: str, issued_at: float) -> None:
    registry = _pending_state_registry()
    now = time.time()
    with registry["lock"]:
        issued = registry["issued"]
        expired = [
            candidate
            for candidate, created in issued.items()
            if now - float(created) > STATE_MAX_AGE_SECONDS
        ]
        for candidate in expired:
            issued.pop(candidate, None)
        issued[digest] = issued_at


def _consume_pending_state(digest: str) -> bool:
    registry = _pending_state_registry()
    now = time.time()
    with registry["lock"]:
        issued_at = registry["issued"].pop(digest, None)
    if issued_at is None:
        return False
    age = now - float(issued_at)
    return 0 <= age <= STATE_MAX_AGE_SECONDS


def _pending_state_is_issued(digest: str) -> bool:
    registry = _pending_state_registry()
    now = time.time()
    with registry["lock"]:
        issued_at = registry["issued"].get(digest)
        if issued_at is None:
            return False
        if not 0 <= now - float(issued_at) <= STATE_MAX_AGE_SECONDS:
            registry["issued"].pop(digest, None)
            return False
        return True


def _process_callback(config: InspectorConfig) -> None:
    query = _callback_query()
    if not query:
        return

    # Intervals.icu documents a declined authorization as
    # ``?error=access_denied`` without a state parameter. It carries no code
    # or credential, so handle that exact shape without attempting exchange.
    if (
        query.get("error") == "access_denied"
        and "code" not in query
        and "state" not in query
    ):
        _remember_notice(
            "warning", "OAuth достъпът беше отказан в Intervals.icu."
        )
        _clear_callback_query()
        st.session_state.pop(SESSION_PENDING_STATE, None)
        st.session_state.pop(SESSION_PENDING_STATE_DIGEST, None)
        st.session_state.pop(SESSION_PENDING_STATE_CREATED, None)
        st.rerun()
        return

    try:
        # All success callbacks and provider errors that include state are
        # validated and consumed before their parameters are interpreted.
        # This makes those callbacks one-time and rejects unissued flows.
        callback_state = _single_query_value(query.get("state"))
        if callback_state is None:
            raise OAuthCallbackError("OAuth callback-ът няма state.")
        verify_signed_state(
            callback_state,
            config.state_secret,
            expected_redirect_uri=config.redirect_uri,
            max_age_seconds=STATE_MAX_AGE_SECONDS,
        )
        state_digest = _state_digest(callback_state)
        consumed = list(
            st.session_state.get(SESSION_CONSUMED_STATE_DIGESTS, [])
        )
        if state_digest in consumed:
            raise OAuthCallbackError("OAuth state вече е използван.")

        expected_digest = st.session_state.get(SESSION_PENDING_STATE_DIGEST)
        if expected_digest is not None and not hmac.compare_digest(
            str(expected_digest), state_digest
        ):
            raise OAuthCallbackError("OAuth state не съвпада с текущата сесия.")
        if not _consume_pending_state(state_digest):
            raise OAuthCallbackError(
                "OAuth state не е издаден или вече е използван."
            )
        callback = parse_callback(query)

        grant = exchange_authorization_code(
            client_id=config.client_id,
            client_secret=config.client_secret,
            code=callback.code,
        )
        granted_scopes = {
            str(scope).upper() for scope in grant.scopes
        }
        if any(
            scope not in READ_ONLY_SCOPES for scope in granted_scopes
        ):
            raise OAuthExchangeError(
                "OAuth grant-ът съдържа непозволени права."
            )
        st.session_state[SESSION_TOKEN] = grant.access_token
        st.session_state[SESSION_ATHLETE_ID] = grant.athlete_id
        st.session_state[SESSION_ATHLETE_NAME] = grant.athlete_name
        st.session_state[SESSION_SCOPES] = [
            scope for scope in READ_ONLY_SCOPES if scope in granted_scopes
        ]
        st.session_state.pop(SESSION_REPORT, None)
        consumed.append(state_digest)
        st.session_state[SESSION_CONSUMED_STATE_DIGESTS] = consumed[-5:]
        st.session_state.pop(SESSION_PENDING_STATE_DIGEST, None)
        st.session_state.pop(SESSION_PENDING_STATE_CREATED, None)
        st.session_state.pop(SESSION_PENDING_STATE, None)
        _remember_notice("success", "Intervals.icu профилът е свързан.")
    except OAuthAccessDenied:
        _remember_notice(
            "warning", "OAuth достъпът беше отказан в Intervals.icu."
        )
    except (OAuthCallbackError, OAuthExchangeError, StateValidationError):
        _remember_notice(
            "error",
            "OAuth callback-ът не можа да бъде потвърден. Стартирайте ново "
            "свързване.",
        )
    finally:
        # Authorization codes are short-lived credentials. Remove every
        # callback value before any UI rerun, successful or not.
        _clear_callback_query()
        st.session_state.pop(SESSION_PENDING_STATE, None)
        st.session_state.pop(SESSION_PENDING_STATE_DIGEST, None)
        st.session_state.pop(SESSION_PENDING_STATE_CREATED, None)

    st.rerun()


def _password_gate(config: InspectorConfig) -> bool:
    if st.session_state.get(SESSION_AUTHENTICATED) is True:
        return True

    st.subheader("Достъп до тестовия инспектор")
    with st.form("inspector_access_form", clear_on_submit=True):
        attempted_password = st.text_input(
            "Парола", type="password", autocomplete="current-password"
        )
        submitted = st.form_submit_button("Вход", type="primary")

    if submitted:
        if hmac.compare_digest(attempted_password, config.access_password):
            st.session_state[SESSION_AUTHENTICATED] = True
            st.rerun()
        st.error("Невалидна парола.")
    return False


def _oauth_state(config: InspectorConfig) -> str:
    now = time.time()
    created = float(
        st.session_state.get(SESSION_PENDING_STATE_CREATED, 0.0) or 0.0
    )
    stored_state = st.session_state.get(SESSION_PENDING_STATE)
    if (
        isinstance(stored_state, str)
        and now - created < STATE_REFRESH_SECONDS
        and _pending_state_is_issued(_state_digest(stored_state))
    ):
        return stored_state

    state = create_signed_state(
        config.state_secret,
        redirect_uri=config.redirect_uri,
    )
    st.session_state[SESSION_PENDING_STATE] = state
    st.session_state[SESSION_PENDING_STATE_DIGEST] = _state_digest(state)
    st.session_state[SESSION_PENDING_STATE_CREATED] = now
    _register_pending_state(_state_digest(state), now)
    return state


def _normalise_records(payload: Any) -> list[Mapping[str, Any]]:
    if isinstance(payload, Mapping):
        return [payload]
    if isinstance(payload, Sequence) and not isinstance(
        payload, (str, bytes, bytearray)
    ):
        return [item for item in payload if isinstance(item, Mapping)]
    return []


def _inspect_source(
    errors: dict[str, str],
    label: str,
    operation: Callable[[], Any],
    *,
    empty: Any,
) -> Any:
    try:
        return operation()
    except IntervalsAPIError as exc:
        errors[label] = str(exc)
    except Exception:
        errors[label] = "Локална грешка при обработката на отговора."
    return empty


def _run_inspection(period_days: int) -> dict[str, Any]:
    athlete_id = str(st.session_state[SESSION_ATHLETE_ID])
    token = str(st.session_state[SESSION_TOKEN])
    client = IntervalsClient(access_token=token, athlete_id=athlete_id)

    newest = date.today()
    oldest = newest - timedelta(days=period_days - 1)
    oldest_iso = oldest.isoformat()
    newest_iso = newest.isoformat()
    errors: dict[str, str] = {}

    profile_payload = _inspect_source(
        errors, "Профил", client.get_athlete, empty={}
    )
    settings_payload = _inspect_source(
        errors,
        "Спортни настройки",
        client.get_sport_settings,
        empty=[],
    )
    activities_payload = _inspect_source(
        errors,
        "Активности",
        lambda: client.get_activities(oldest_iso, newest_iso),
        empty=[],
    )
    wellness_payload = _inspect_source(
        errors,
        "Wellness",
        lambda: client.get_wellness(oldest_iso, newest_iso),
        empty=[],
    )
    calendar_payload = _inspect_source(
        errors,
        "Календар",
        lambda: client.get_events(oldest_iso, newest_iso),
        empty=[],
    )

    records = {
        "profile": _normalise_records(profile_payload),
        "sport_settings": _normalise_records(settings_payload),
        "activities": _normalise_records(activities_payload),
        "wellness": _normalise_records(wellness_payload),
        "calendar": _normalise_records(calendar_payload),
    }

    stream_payloads: list[dict[str, Any]] = []
    for activity in records["activities"]:
        if len(stream_payloads) >= MAX_STREAM_ACTIVITIES:
            break
        activity_id = activity.get("id")
        if activity_id in (None, ""):
            continue
        streams = _inspect_source(
            errors,
            "Streams",
            lambda activity_id=str(activity_id): client.get_streams(
                activity_id
            ),
            empty=None,
        )
        if streams is not None:
            stream_payloads.append({"streams": streams})

    coverage = {
        group: build_field_coverage(
            group_records,
            SOURCE_ENDPOINTS[group],
            STANDARD_FIELDS[group],
        )
        for group, group_records in records.items()
    }
    stream_summary = summarize_streams(
        stream_payloads, max_activities=MAX_STREAM_ACTIVITIES
    )

    # Raw API responses go out of scope here. Session state receives metadata
    # only, never athlete values or stream samples.
    return {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "period_days": period_days,
        "counts": {
            group: len(group_records)
            for group, group_records in records.items()
        },
        "coverage": coverage,
        "streams": stream_summary,
        "stream_activities_checked": len(stream_payloads),
        "errors": errors,
    }


def _render_table(rows: list[dict[str, Any]], empty_message: str) -> None:
    if not rows:
        st.info(empty_message)
        return
    st.dataframe(rows, use_container_width=True, hide_index=True)


def _render_report(report: Mapping[str, Any]) -> None:
    counts = report.get("counts", {})
    coverage = report.get("coverage", {})
    errors = report.get("errors", {})
    streams = list(report.get("streams", []))

    if isinstance(errors, Mapping):
        for source, message in errors.items():
            st.warning(f"{source}: {message}")

    tabs = st.tabs(
        [
            "Активности",
            "Wellness",
            "Streams",
            "Настройки и календар",
            "Обезличен отчет",
        ]
    )
    with tabs[0]:
        st.metric("Получени записи", int(counts.get("activities", 0)))
        _render_table(
            list(coverage.get("activities", [])),
            "Няма налични полета за активности в избрания период.",
        )

    with tabs[1]:
        st.metric("Получени записи", int(counts.get("wellness", 0)))
        _render_table(
            list(coverage.get("wellness", [])),
            "Няма налични wellness полета в избрания период.",
        )

    with tabs[2]:
        st.caption(
            "Streams са проверени за най-много "
            f"{MAX_STREAM_ACTIVITIES} активности. Точки и GPS стойности не "
            "се показват."
        )
        st.metric(
            "Проверени активности",
            int(report.get("stream_activities_checked", 0)),
        )
        _render_table(streams, "Не са открити достъпни streams.")

    with tabs[3]:
        st.subheader("Профилна структура")
        _render_table(
            list(coverage.get("profile", [])),
            "Не е получена профилна структура.",
        )
        st.subheader("Спортни настройки")
        _render_table(
            list(coverage.get("sport_settings", [])),
            "Не са получени спортни настройки.",
        )
        st.subheader("Календар")
        st.metric("Получени записи", int(counts.get("calendar", 0)))
        _render_table(
            list(coverage.get("calendar", [])),
            "Няма календарни полета в избрания период.",
        )

    with tabs[4]:
        all_coverage = [
            row
            for group_rows in coverage.values()
            for row in group_rows
        ]
        st.caption(
            "Отчетите съдържат само структура и покритие. В тях няма token, "
            "authorization code, идентификатор/име на спортиста, реални "
            "примерни стойности, GPS, маршрути или бележки."
        )
        json_report = export_inventory_json(all_coverage, streams)
        csv_report = export_inventory_csv(all_coverage, streams)
        st.download_button(
            "Изтегли обезличен JSON",
            data=json_report,
            file_name="intervals_field_inventory.json",
            mime="application/json",
            use_container_width=True,
        )
        st.download_button(
            "Изтегли обезличен CSV",
            data=csv_report,
            file_name="intervals_field_inventory.csv",
            mime="text/csv",
            use_container_width=True,
        )


def _disconnect() -> None:
    _clear_callback_query()
    st.session_state.clear()
    st.rerun()


def main() -> None:
    st.set_page_config(
        page_title="onFlows — Intervals.icu Data Inspector",
        page_icon="🔎",
        layout="wide",
    )
    st.title("onFlows — Intervals.icu Data Inspector (TEST ONLY)")
    st.warning(
        "Изследователски read-only инструмент. Данните не се записват в "
        "Supabase и не се използват от основното приложение."
    )

    config, missing = _load_configuration()
    if config is None:
        st.error("Конфигурацията не е готова.")
        st.write("Липсващи имена на настройки:")
        for name in missing:
            st.code(name)
        if _callback_query():
            _clear_callback_query()
            st.warning(
                "OAuth callback параметрите бяха премахнати, защото "
                "конфигурацията е непълна."
            )
        return

    st.success("Конфигурация: готова (5/5 настройки).")
    # A callback is accepted only for a signed, one-time registry entry that
    # was issued after the original password gate. Exchange immediately so
    # Intervals.icu's short-lived code never waits for a second login.
    _process_callback(config)
    _render_notice()

    if not _password_gate(config):
        return

    connected = bool(
        st.session_state.get(SESSION_TOKEN)
        and st.session_state.get(SESSION_ATHLETE_ID)
    )
    if not connected:
        st.info("OAuth статус: няма свързан Intervals.icu профил.")
        state = _oauth_state(config)
        authorization_url = build_authorization_url(
            client_id=config.client_id,
            redirect_uri=config.redirect_uri,
            state=state,
        )
        st.link_button(
            "Свържи Intervals.icu",
            authorization_url,
            type="primary",
            use_container_width=True,
        )
        st.caption(
            "Заявяват се само ACTIVITY:READ, WELLNESS:READ, SETTINGS:READ "
            "и CALENDAR:READ."
        )
        return

    st.success("OAuth статус: свързан.")
    athlete_name = str(
        st.session_state.get(SESSION_ATHLETE_NAME) or "Intervals.icu профил"
    )
    st.text(f"Свързан профил: {athlete_name}")

    granted = {
        str(scope).upper()
        for scope in st.session_state.get(SESSION_SCOPES, [])
    }
    missing_scopes = [
        scope for scope in READ_ONLY_SCOPES if scope not in granted
    ]
    if missing_scopes:
        st.error(
            "Липсват задължителни read-only права: "
            + ", ".join(missing_scopes)
            + ". Прекратете връзката и направете нов grant."
        )

    period_days = st.radio(
        "Период",
        options=(30, 60, 90),
        index=0,
        horizontal=True,
        format_func=lambda value: f"{value} дни",
    )
    if st.button(
        "Провери наличните данни",
        type="primary",
        disabled=bool(missing_scopes),
        use_container_width=True,
    ):
        with st.spinner("Проверка на read-only API данните…"):
            st.session_state[SESSION_REPORT] = _run_inspection(period_days)

    report = st.session_state.get(SESSION_REPORT)
    if isinstance(report, Mapping):
        _render_report(report)

    st.divider()
    if st.button(
        "Прекрати връзката",
        type="secondary",
        use_container_width=True,
    ):
        _disconnect()


if __name__ == "__main__":
    main()
