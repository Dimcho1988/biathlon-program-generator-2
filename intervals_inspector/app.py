"""Isolated, read-only Streamlit inspector for Intervals.icu OAuth data."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
import hmac
import re
from typing import Any

import streamlit as st

from intervals_inspector.data_adapter import (
    build_mapping_report,
    build_model_readiness,
    validate_shadow_period,
)
from intervals_inspector.intervals_client import (
    IntervalsAPIError,
    IntervalsClient,
    IntervalsResponse,
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
from intervals_inspector.oauth_state_store import (
    consume_pending_state,
    register_pending_state,
)


CONFIG_NAMES = (
    "INTERVALS_CLIENT_ID",
    "INTERVALS_CLIENT_SECRET",
    "INTERVALS_REDIRECT_URI",
    "OAUTH_STATE_SECRET",
    "INSPECTOR_ACCESS_PASSWORD",
)
CALLBACK_QUERY_KEYS = ("code", "state", "error", "error_description")
INSPECTION_PERIOD_OPTIONS = (7, 14, 30)
SHADOW_PERIOD_OPTIONS = (30, 60, 90)
MAX_ACTIVITY_CHOICES = 200
STATE_MAX_AGE_SECONDS = 10 * 60
_SAFE_ACTIVITY_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")

SESSION_TOKEN = "_intervals_access_token"
SESSION_ATHLETE_ID = "_intervals_athlete_id"
SESSION_ATHLETE_NAME = "_intervals_athlete_name"
SESSION_SCOPES = "_intervals_granted_scopes"
SESSION_REPORT = "_intervals_inventory_report"
SESSION_ACTIVITY_CHOICES = "_intervals_activity_choices"
SESSION_ACTIVITY_REPORT = "_intervals_activity_report"
SESSION_ACTIVITY_REPORT_ID = "_intervals_activity_report_id"
SESSION_AUTHENTICATED = "_inspector_authenticated"
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
    "activity_detail": {
        "id",
        "name",
        "type",
        "sub_type",
        "start_date",
        "start_date_local",
        "moving_time",
        "elapsed_time",
        "distance",
        "icu_training_load",
        "icu_average_watts",
        "icu_weighted_avg_watts",
        "icu_hr_zone_times",
        "icu_zone_times",
        "pace_zone_times",
        "stream_types",
        "total_elevation_gain",
        "perceived_exertion",
        "icu_rpe",
        "feel",
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
    "planned_workouts": {
        "id",
        "category",
        "start_date_local",
        "end_date_local",
        "type",
        "name",
        "moving_time",
        "distance",
        "icu_training_load",
        "workout_doc",
        "load_target",
        "time_target",
        "distance_target",
        "training_availability",
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
    "planned_workouts": "/api/v1/athlete/{athlete_id}/events",
    "activity_detail": "/api/v1/activity/{activity_id}",
    "streams": "/api/v1/activity/{activity_id}/streams.json",
}


def _configuration_value(name: str) -> str | None:
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
        if not consume_pending_state(callback_state):
            raise OAuthCallbackError(
                "OAuth state не е издаден или вече е използван."
            )
        callback = parse_callback(query)

        grant = exchange_authorization_code(
            client_id=config.client_id,
            client_secret=config.client_secret,
            code=callback.code,
            redact_values=(callback.state,),
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
        st.session_state.pop(SESSION_ACTIVITY_CHOICES, None)
        st.session_state.pop(SESSION_ACTIVITY_REPORT, None)
        st.session_state.pop(SESSION_ACTIVITY_REPORT_ID, None)
        _remember_notice("success", "Intervals.icu профилът е свързан.")
    except OAuthAccessDenied:
        _remember_notice(
            "warning", "OAuth достъпът беше отказан в Intervals.icu."
        )
    except (OAuthCallbackError, StateValidationError):
        _remember_notice(
            "error",
            "OAuth state-ът е невалиден, изтекъл или вече използван. "
            "Стартирайте ново свързване.",
        )
    except OAuthExchangeError as exc:
        _remember_notice(
            "error",
            "Валидният OAuth callback беше приет, но authorization code-ът "
            "не можа да бъде обменен. "
            f"{exc} Стартирайте ново свързване.",
        )
    finally:
        # Authorization codes are short-lived credentials. Remove every
        # callback value before any UI rerun, successful or not.
        _clear_callback_query()

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
        if hmac.compare_digest(
            attempted_password.encode("utf-8"),
            config.access_password.encode("utf-8"),
        ):
            st.session_state[SESSION_AUTHENTICATED] = True
            st.rerun()
        st.error("Невалидна парола.")
    return False


def _oauth_state(config: InspectorConfig) -> str:
    state = create_signed_state(
        config.state_secret,
        redirect_uri=config.redirect_uri,
    )
    register_pending_state(state)
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
    endpoint_checks: list[dict[str, Any]],
    *,
    category: str,
    endpoint: str,
    operation: Callable[[], IntervalsResponse],
    standard_fields: set[str] | None,
    empty: Any,
) -> tuple[Any, list[dict[str, Any]]]:
    try:
        response = operation()
        if not isinstance(response, IntervalsResponse):
            raise TypeError("unexpected Intervals response envelope")
        records = _normalise_records(response.payload)
        coverage = build_field_coverage(
            records,
            endpoint,
            standard_fields,
        )
    except IntervalsAPIError as exc:
        endpoint_checks.append(
            {
                "category": category,
                "endpoint": endpoint,
                "http_status": exc.status_code,
                "available": False,
                "record_count": 0,
                "field_names": [],
                "safe_error": str(exc),
            }
        )
    except Exception:
        endpoint_checks.append(
            {
                "category": category,
                "endpoint": endpoint,
                "http_status": None,
                "available": False,
                "record_count": 0,
                "field_names": [],
                "safe_error": "Локална грешка при обработката на отговора.",
            }
        )
    else:
        endpoint_checks.append(
            {
                "category": category,
                "endpoint": endpoint,
                "http_status": response.status_code,
                "available": True,
                "record_count": len(records),
                "field_names": [
                    str(row["json_path"]) for row in coverage
                ],
                "safe_error": "",
            }
        )
        return response.payload, coverage
    return empty, []


def _validate_inspection_period(period_days: int) -> int:
    validated = validate_shadow_period(period_days)
    if validated > max(INSPECTION_PERIOD_OPTIONS):
        raise ValueError("inspection period must not exceed 30 days")
    return validated


def _activity_choices(
    activities: Sequence[Mapping[str, Any]],
) -> list[dict[str, str]]:
    choices: list[dict[str, str]] = []
    seen_ids: set[str] = set()
    label_counts: dict[str, int] = {}
    for activity in activities:
        if len(choices) >= MAX_ACTIVITY_CHOICES:
            break
        activity_id = str(activity.get("id", "")).strip()
        if (
            not _SAFE_ACTIVITY_ID.fullmatch(activity_id)
            or activity_id in seen_ids
        ):
            continue
        seen_ids.add(activity_id)

        raw_date = str(
            activity.get("start_date_local")
            or activity.get("start_date")
            or ""
        )
        rendered_date = (
            raw_date[:10]
            if re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw_date[:10])
            else "Без дата"
        )
        rendered_time = (
            raw_date[11:16]
            if len(raw_date) >= 16
            and re.fullmatch(r"\d{2}:\d{2}", raw_date[11:16])
            else ""
        )
        raw_sport = str(
            activity.get("type") or activity.get("sub_type") or "Активност"
        )
        rendered_sport = re.sub(
            r"[^\w /+.-]", "", raw_sport, flags=re.UNICODE
        ).strip()[:40] or "Активност"
        base_label = (
            f"{rendered_date} {rendered_time} · {rendered_sport}"
            if rendered_time
            else f"{rendered_date} · {rendered_sport}"
        )
        label_counts[base_label] = label_counts.get(base_label, 0) + 1
        occurrence = label_counts[base_label]
        label = (
            base_label
            if occurrence == 1
            else f"{base_label} · #{occurrence}"
        )
        choices.append(
            {
                "activity_id": activity_id,
                "label": label,
            }
        )
    return choices


def _run_inspection(
    period_days: int,
) -> tuple[dict[str, Any], list[dict[str, str]]]:
    period_days = _validate_inspection_period(period_days)
    athlete_id = str(st.session_state[SESSION_ATHLETE_ID])
    token = str(st.session_state[SESSION_TOKEN])
    client = IntervalsClient(access_token=token, athlete_id=athlete_id)

    newest = date.today()
    oldest = newest - timedelta(days=period_days - 1)
    oldest_iso = oldest.isoformat()
    newest_iso = newest.isoformat()
    endpoint_checks: list[dict[str, Any]] = []

    profile_payload, profile_coverage = _inspect_source(
        endpoint_checks,
        category="Профил",
        endpoint=SOURCE_ENDPOINTS["profile"],
        operation=client.get_athlete_result,
        standard_fields=STANDARD_FIELDS["profile"],
        empty={},
    )
    settings_payload, settings_coverage = _inspect_source(
        endpoint_checks,
        category="Спортни настройки и зони",
        endpoint=SOURCE_ENDPOINTS["sport_settings"],
        operation=client.get_sport_settings_result,
        standard_fields=STANDARD_FIELDS["sport_settings"],
        empty=[],
    )
    activities_payload, activities_coverage = _inspect_source(
        endpoint_checks,
        category="Списък активности",
        endpoint=SOURCE_ENDPOINTS["activities"],
        operation=lambda: client.get_activities_result(
            oldest_iso, newest_iso
        ),
        standard_fields=STANDARD_FIELDS["activities"],
        empty=[],
    )
    wellness_payload, wellness_coverage = _inspect_source(
        endpoint_checks,
        category="Wellness",
        endpoint=SOURCE_ENDPOINTS["wellness"],
        operation=lambda: client.get_wellness_result(
            oldest_iso, newest_iso
        ),
        standard_fields=STANDARD_FIELDS["wellness"],
        empty=[],
    )
    calendar_payload, calendar_coverage = _inspect_source(
        endpoint_checks,
        category="Календар",
        endpoint=SOURCE_ENDPOINTS["calendar"],
        operation=lambda: client.get_events_result(
            oldest_iso, newest_iso
        ),
        standard_fields=STANDARD_FIELDS["calendar"],
        empty=[],
    )
    planned_payload, planned_coverage = _inspect_source(
        endpoint_checks,
        category="Планирани тренировки (WORKOUT)",
        endpoint=SOURCE_ENDPOINTS["planned_workouts"],
        operation=lambda: client.get_events_result(
            oldest_iso, newest_iso, category="WORKOUT"
        ),
        standard_fields=STANDARD_FIELDS["planned_workouts"],
        empty=[],
    )

    records = {
        "profile": _normalise_records(profile_payload),
        "sport_settings": _normalise_records(settings_payload),
        "activities": _normalise_records(activities_payload),
        "wellness": _normalise_records(wellness_payload),
        "calendar": _normalise_records(calendar_payload),
        "planned_workouts": _normalise_records(planned_payload),
    }

    coverage = {
        "profile": profile_coverage,
        "sport_settings": settings_coverage,
        "activities": activities_coverage,
        "wellness": wellness_coverage,
        "calendar": calendar_coverage,
        "planned_workouts": planned_coverage,
    }
    mapping_report = build_mapping_report(coverage)
    model_readiness = build_model_readiness(mapping_report)

    # Raw API responses go out of scope here. Session state receives metadata
    # only. The bounded activity choices contain only ID/date/sport and stay in
    # this user's session so detail and streams remain strictly on demand.
    return (
        {
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "period_days": period_days,
            "counts": {
                group: len(group_records)
                for group, group_records in records.items()
            },
            "coverage": coverage,
            "streams": [],
            "endpoint_checks": endpoint_checks,
            "mapping_report": mapping_report,
            "model_readiness": model_readiness,
        },
        _activity_choices(records["activities"]),
    )


def _run_activity_inspection(activity_id: str) -> dict[str, Any]:
    athlete_id = str(st.session_state[SESSION_ATHLETE_ID])
    token = str(st.session_state[SESSION_TOKEN])
    client = IntervalsClient(access_token=token, athlete_id=athlete_id)
    endpoint_checks: list[dict[str, Any]] = []

    detail_payload, detail_coverage = _inspect_source(
        endpoint_checks,
        category="Детайли на избрана активност",
        endpoint=SOURCE_ENDPOINTS["activity_detail"],
        operation=lambda: client.get_activity_result(
            activity_id, include_intervals=False
        ),
        standard_fields=STANDARD_FIELDS["activity_detail"],
        empty={},
    )
    streams_payload, _stream_coverage = _inspect_source(
        endpoint_checks,
        category="Streams на избрана активност",
        endpoint=SOURCE_ENDPOINTS["streams"],
        operation=lambda: client.get_streams_result(activity_id),
        standard_fields=None,
        empty=[],
    )
    stream_summary = summarize_streams(
        [{"streams": streams_payload}],
        max_activities=1,
    )

    # detail_payload is deliberately discarded after its field inventory is
    # built. No activity values or stream samples enter session state.
    del detail_payload, streams_payload
    return {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "coverage": {"activity_detail": detail_coverage},
        "streams": stream_summary,
        "endpoint_checks": endpoint_checks,
    }


def _combined_diagnostics(
    report: Mapping[str, Any],
    activity_report: Mapping[str, Any] | None,
) -> tuple[
    dict[str, list[dict[str, Any]]],
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, str]],
    list[dict[str, Any]],
]:
    coverage = {
        str(group): list(rows)
        for group, rows in dict(report.get("coverage", {})).items()
    }
    endpoint_checks = list(report.get("endpoint_checks", []))
    streams = list(report.get("streams", []))
    if isinstance(activity_report, Mapping):
        for group, rows in dict(
            activity_report.get("coverage", {})
        ).items():
            coverage[str(group)] = list(rows)
        endpoint_checks.extend(activity_report.get("endpoint_checks", []))
        streams = list(activity_report.get("streams", []))

    mapping_coverage = dict(coverage)
    mapping_coverage["activities"] = [
        *coverage.get("activities", []),
        *coverage.get("activity_detail", []),
    ]
    mapping_coverage["calendar"] = [
        *coverage.get("calendar", []),
        *coverage.get("planned_workouts", []),
    ]
    mapping_report = build_mapping_report(mapping_coverage, streams)
    model_readiness = build_model_readiness(mapping_report)
    return (
        coverage,
        streams,
        mapping_report,
        model_readiness,
        endpoint_checks,
    )


def _activity_report_for_selection(
    selected_activity_id: str,
) -> Mapping[str, Any] | None:
    if (
        st.session_state.get(SESSION_ACTIVITY_REPORT_ID)
        != selected_activity_id
    ):
        st.session_state.pop(SESSION_ACTIVITY_REPORT, None)
        st.session_state.pop(SESSION_ACTIVITY_REPORT_ID, None)
        return None
    report = st.session_state.get(SESSION_ACTIVITY_REPORT)
    return report if isinstance(report, Mapping) else None


def _render_table(rows: list[dict[str, Any]], empty_message: str) -> None:
    if not rows:
        st.info(empty_message)
        return
    st.dataframe(rows, width="stretch", hide_index=True)


def _render_report(
    report: Mapping[str, Any],
    activity_report: Mapping[str, Any] | None = None,
) -> None:
    counts = report.get("counts", {})
    (
        coverage,
        streams,
        mapping_report,
        model_readiness,
        endpoint_checks,
    ) = _combined_diagnostics(report, activity_report)

    for check in endpoint_checks:
        if not check.get("available") and check.get("safe_error"):
            st.warning(
                f"{check.get('category', 'API')}: "
                f"{check.get('safe_error')}"
            )

    tabs = st.tabs(
        [
            "API проверки",
            "Активности",
            "Wellness",
            "Streams",
            "Картографиране към onFlows",
            "Настройки и календар",
            "Обезличен отчет",
        ]
    )
    with tabs[0]:
        summary_rows = [
            {
                "endpoint/категория": str(check.get("category", "")),
                "endpoint": str(check.get("endpoint", "")),
                "HTTP статус": check.get("http_status"),
                "достъпно": (
                    "да" if check.get("available") else "не"
                ),
                "брой записи": int(check.get("record_count", 0)),
                "налични полета": ", ".join(
                    str(item) for item in check.get("field_names", [])
                ),
                "безопасна грешка": str(check.get("safe_error", "")),
            }
            for check in endpoint_checks
        ]
        _render_table(
            summary_rows,
            "Все още няма изпълнени API проверки.",
        )

    with tabs[1]:
        st.metric("Получени записи", int(counts.get("activities", 0)))
        _render_table(
            list(coverage.get("activities", [])),
            "Няма налични полета за активности в избрания период.",
        )
        st.subheader("Детайли на избраната активност")
        _render_table(
            list(coverage.get("activity_detail", [])),
            "Изберете активност, за да проверите detail endpoint-а.",
        )

    with tabs[2]:
        st.metric("Получени записи", int(counts.get("wellness", 0)))
        _render_table(
            list(coverage.get("wellness", [])),
            "Няма налични wellness полета в избрания период.",
        )

    with tabs[3]:
        st.caption(
            "Streams се зареждат само за изрично избраната активност. "
            "Точки, GPS координати и други стойности не се показват."
        )
        st.metric(
            "Проверени активности",
            1 if streams else 0,
        )
        _render_table(streams, "Не са открити достъпни streams.")

    with tabs[4]:
        st.subheader("Схемна готовност и оставащи model inputs")
        _render_table(
            model_readiness,
            "Няма достатъчно данни за оценка на моделите.",
        )
        st.subheader("Intervals → вътрешен onFlows формат")
        _render_table(
            mapping_report,
            "Няма налична карта на полетата.",
        )
        shadow_period = st.select_slider(
            "Предвиден прозорец за бъдещ shadow run",
            options=SHADOW_PERIOD_OPTIONS,
            value=90,
            format_func=lambda value: f"{value} дни",
        )
        validate_shadow_period(int(shadow_period))
        st.caption(
            "Този избор описва ограничения до 90 дни за следващия етап. "
            "На тази страница не се изпълняват модели и не се променят планове."
        )

    with tabs[5]:
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
        st.subheader("Планирани тренировки (WORKOUT)")
        st.metric(
            "Получени планирани тренировки",
            int(counts.get("planned_workouts", 0)),
        )
        _render_table(
            list(coverage.get("planned_workouts", [])),
            "Няма планирани тренировки в избрания период.",
        )

    with tabs[6]:
        all_coverage = [
            row
            for group_rows in coverage.values()
            for row in group_rows
        ]
        st.caption(
            "Отчетите съдържат само структура и покритие. В тях няма token, "
            "authorization code, идентификатор/име на спортиста, реални "
            "примерни стойности, GPS координати/стойности, маршрути или бележки."
        )
        json_report = export_inventory_json(
            all_coverage,
            streams,
            endpoint_checks,
            mapping_report,
            model_readiness,
        )
        csv_report = export_inventory_csv(
            all_coverage,
            streams,
            endpoint_checks,
            mapping_report,
            model_readiness,
        )
        st.download_button(
            "Изтегли обезличен JSON",
            data=json_report,
            file_name="intervals_field_inventory.json",
            mime="application/json",
            width="stretch",
        )
        st.download_button(
            "Изтегли обезличен CSV",
            data=csv_report,
            file_name="intervals_field_inventory.csv",
            mime="text/csv",
            width="stretch",
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
            width="stretch",
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
    athlete_id = str(st.session_state[SESSION_ATHLETE_ID])
    st.text(f"Свързан профил: {athlete_name}")
    st.text(f"Intervals athlete ID: {athlete_id}")

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
        "Период за API инспекция",
        options=INSPECTION_PERIOD_OPTIONS,
        index=0,
        horizontal=True,
        format_func=lambda value: f"{value} дни",
    )
    if st.button(
        "Провери наличните данни",
        type="primary",
        disabled=bool(missing_scopes),
        width="stretch",
    ):
        with st.spinner("Проверка на read-only API данните…"):
            report, activity_choices = _run_inspection(period_days)
            st.session_state[SESSION_REPORT] = report
            st.session_state[SESSION_ACTIVITY_CHOICES] = activity_choices
            st.session_state.pop(SESSION_ACTIVITY_REPORT, None)
            st.session_state.pop(SESSION_ACTIVITY_REPORT_ID, None)

    report = st.session_state.get(SESSION_REPORT)
    if isinstance(report, Mapping):
        activity_report: Mapping[str, Any] | None = None
        activity_choices = st.session_state.get(
            SESSION_ACTIVITY_CHOICES, []
        )
        if isinstance(activity_choices, list) and activity_choices:
            selected_index = st.selectbox(
                "Избрана активност за detail и streams",
                options=range(len(activity_choices)),
                format_func=lambda index: activity_choices[index]["label"],
            )
            selected = activity_choices[int(selected_index)]
            selected_activity_id = str(selected["activity_id"])
            activity_report = _activity_report_for_selection(
                selected_activity_id
            )
            if st.button(
                "Провери избраната активност",
                type="secondary",
                width="stretch",
            ):
                with st.spinner(
                    "Проверка на детайлите и наличните streams…"
                ):
                    st.session_state[SESSION_ACTIVITY_REPORT] = (
                        _run_activity_inspection(selected_activity_id)
                    )
                    st.session_state[SESSION_ACTIVITY_REPORT_ID] = (
                        selected_activity_id
                    )
                    activity_report = st.session_state[
                        SESSION_ACTIVITY_REPORT
                    ]
        elif int(report.get("counts", {}).get("activities", 0)) == 0:
            st.info(
                "Няма активности в периода за отделна detail/streams проверка."
            )

        _render_report(
            report,
            activity_report
            if isinstance(activity_report, Mapping)
            else None,
        )

    st.divider()
    if st.button(
        "Прекрати връзката",
        type="secondary",
        width="stretch",
    ):
        _disconnect()


if __name__ == "__main__":
    main()
