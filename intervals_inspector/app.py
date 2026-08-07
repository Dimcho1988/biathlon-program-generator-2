"""Isolated, read-only Streamlit inspector for Intervals.icu OAuth data."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
import hmac
from pathlib import Path
import re
import sys
from typing import Any

import streamlit as st

# Streamlit Cloud adds the main file's directory to ``sys.path``. Because the
# main file lives inside the package, add the repository root when this file is
# executed directly so the same absolute imports work locally and in Cloud.
if __package__ in (None, ""):
    repository_root = str(Path(__file__).resolve().parent.parent)
    if repository_root not in sys.path:
        sys.path.insert(0, repository_root)

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
from intervals_inspector.intervals_hr_adapter import (
    adapt_intervals_hr_zones,
    build_intervals_zone_analysis,
)
from intervals_inspector.inventory import (
    build_field_coverage,
    export_inventory_csv,
    export_inventory_json,
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
    PendingConsentEvidence,
    consume_pending_state_with_consent,
    register_pending_state,
)
from intervals_inspector.onflows_intrazone_load import (
    calculate_onflows_intrazone_load,
)
from intervals_inspector.onflows_zone_profile import (
    MANUAL_PROFILE_SOURCE,
    OnFlowsZoneProfile,
    build_onflows_zone_profile,
    default_onflows_zone_profile,
    profile_edit_rows,
    profile_from_safe_dict,
    safe_profile_dict,
)
from intervals_inspector.model_registry import (
    explanation_text,
    validate_registry_items,
)
from intervals_inspector.shadow_model import (
    EDITABLE_FIELDS,
    FIELD_RANGES,
    FIELD_UNITS,
    READ_ONLY_FIELDS,
    ShadowModelConfiguration,
    build_model_registry,
    calculate_shadow_comparison,
    configuration_from_profile,
    configuration_from_safe_dict,
    configuration_to_safe_dict,
    configuration_with_overrides,
    default_shadow_configuration,
    export_shadow_diagnostics_json,
    profile_from_configuration,
    reset_shadow_configuration,
)
from intervals_inspector.public_pages import (
    ABOUT_URL_PATH,
    PRIVACY_POLICY_VERSION,
    PRIVACY_URL_PATH,
)
from intervals_inspector.stream_quality import (
    analyze_stream_quality,
    export_stream_quality_json,
)
from intervals_inspector.stream_normalizer import (
    build_normalizer_input,
    build_normalizer_summary,
    materialize_1hz,
    normalize_stream_intervals,
)


CONFIG_NAMES = (
    "INTERVALS_CLIENT_ID",
    "INTERVALS_CLIENT_SECRET",
    "INTERVALS_REDIRECT_URI",
    "OAUTH_STATE_SECRET",
    "INSPECTOR_ACCESS_PASSWORD",
)
CALLBACK_QUERY_KEYS = ("code", "state", "error", "error_description")
INSPECTION_PERIOD_OPTIONS = (7, 14, 30, 60, 90)
MAX_SUPPORTING_DATA_PERIOD_DAYS = 30
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
SESSION_NORMALIZER_REPORT = "_intervals_normalizer_report"
SESSION_NORMALIZER_REPORT_ID = "_intervals_normalizer_report_id"
SESSION_ONFLOWS_PROFILE = "_onflows_zone_profile"
SESSION_ONFLOWS_PROFILE_FINGERPRINT = "_onflows_zone_profile_fingerprint"
ONFLOWS_PROFILE_EDITOR_KEY = "_onflows_zone_profile_editor"
SESSION_SHADOW_CONFIGURATION = "_shadow_model_configuration"
SHADOW_SETTING_KEY_PREFIX = "_shadow_setting_"
SESSION_AUTHENTICATED = "_inspector_authenticated"
SESSION_CONSENT = "_pilot_consent_evidence"
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
        "icu_recording_time",
        "recording_stops",
        "distance",
        "icu_training_load",
        "icu_average_watts",
        "icu_weighted_avg_watts",
        "icu_hr_zones",
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
        consent_evidence = consume_pending_state_with_consent(
            callback_state
        )
        if (
            consent_evidence is None
            or not consent_evidence.is_complete
            or not hmac.compare_digest(
                consent_evidence.policy_version,
                PRIVACY_POLICY_VERSION,
            )
        ):
            raise OAuthCallbackError(
                "OAuth state не е издаден след необходимите потвърждения "
                "или вече е използван."
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
        st.session_state[SESSION_CONSENT] = {
            "policy_version": consent_evidence.policy_version,
            "confirmed_at_utc": datetime.fromtimestamp(
                consent_evidence.confirmed_at,
                tz=timezone.utc,
            ).isoformat(),
            "privacy_and_general_consent": True,
            "wellness_health_explicit_consent": True,
            "adult_confirmed": True,
        }
        st.session_state.pop(SESSION_REPORT, None)
        st.session_state.pop(SESSION_ACTIVITY_CHOICES, None)
        st.session_state.pop(SESSION_ACTIVITY_REPORT, None)
        st.session_state.pop(SESSION_ACTIVITY_REPORT_ID, None)
        st.session_state.pop(SESSION_NORMALIZER_REPORT, None)
        st.session_state.pop(SESSION_NORMALIZER_REPORT_ID, None)
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


def _oauth_state(
    config: InspectorConfig,
    *,
    consent: PendingConsentEvidence,
) -> str:
    state = create_signed_state(
        config.state_secret,
        redirect_uri=config.redirect_uri,
    )
    register_pending_state(state, consent=consent)
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
        raise ValueError("inspection period must not exceed 90 days")
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
    activity_oldest = newest - timedelta(days=period_days - 1)
    activity_oldest_iso = activity_oldest.isoformat()
    supporting_period_days = min(
        period_days, MAX_SUPPORTING_DATA_PERIOD_DAYS
    )
    supporting_oldest = newest - timedelta(days=supporting_period_days - 1)
    supporting_oldest_iso = supporting_oldest.isoformat()
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
            activity_oldest_iso, newest_iso
        ),
        standard_fields=STANDARD_FIELDS["activities"],
        empty=[],
    )
    wellness_payload, wellness_coverage = _inspect_source(
        endpoint_checks,
        category="Wellness",
        endpoint=SOURCE_ENDPOINTS["wellness"],
        operation=lambda: client.get_wellness_result(
            supporting_oldest_iso, newest_iso
        ),
        standard_fields=STANDARD_FIELDS["wellness"],
        empty=[],
    )
    calendar_payload, calendar_coverage = _inspect_source(
        endpoint_checks,
        category="Календар",
        endpoint=SOURCE_ENDPOINTS["calendar"],
        operation=lambda: client.get_events_result(
            supporting_oldest_iso, newest_iso
        ),
        standard_fields=STANDARD_FIELDS["calendar"],
        empty=[],
    )
    planned_payload, planned_coverage = _inspect_source(
        endpoint_checks,
        category="Планирани тренировки (WORKOUT)",
        endpoint=SOURCE_ENDPOINTS["planned_workouts"],
        operation=lambda: client.get_events_result(
            supporting_oldest_iso, newest_iso, category="WORKOUT"
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
    stream_quality = analyze_stream_quality(detail_payload, streams_payload)
    timing = dict(stream_quality.get("timing", {}))
    time_point_count = int(timing.get("point_count", 0))
    frequency = timing.get("estimated_frequency_hz")
    stream_summary = [
        {
            "stream_name": str(row.get("stream_name", "")),
            "value_type": None,
            "unit": None,
            "activity_count": 1,
            "total_points": int(row.get("point_count", 0)),
            "estimated_frequency_hz": (
                frequency
                if int(row.get("point_count", 0)) == time_point_count
                else None
            ),
        }
        for row in stream_quality.get("streams", [])
        if str(row.get("stream_name", ""))
    ]

    # Raw detail and stream payloads, including any latlng coordinates, are
    # discarded immediately after the deidentified aggregates are built.
    del detail_payload, streams_payload
    return {
        "coverage": {"activity_detail": detail_coverage},
        "streams": stream_summary,
        "stream_quality": stream_quality,
        "endpoint_checks": endpoint_checks,
    }


def _run_activity_normalizer(
    activity_id: str,
    *,
    include_1hz_preview: bool,
    onflows_profile: OnFlowsZoneProfile | None = None,
    shadow_configuration: ShadowModelConfiguration | None = None,
) -> dict[str, Any]:
    """Run one transient normalization and retain aggregate diagnostics only."""

    profile = onflows_profile or default_onflows_zone_profile()
    model_configuration = (
        shadow_configuration
        or configuration_from_profile(profile)
    )
    athlete_id = str(st.session_state[SESSION_ATHLETE_ID])
    token = str(st.session_state[SESSION_TOKEN])
    client = IntervalsClient(access_token=token, athlete_id=athlete_id)
    detail_response = client.get_activity_result(
        activity_id, include_intervals=False
    )
    streams_response = client.get_streams_result(activity_id)
    if not isinstance(detail_response, IntervalsResponse) or not isinstance(
        streams_response, IntervalsResponse
    ):
        raise TypeError("unexpected Intervals response envelope")

    detail_payload = detail_response.payload
    streams_payload = streams_response.payload
    adapted_zones, zone_adapter_reason = adapt_intervals_hr_zones(
        detail_payload
    )
    normalizer_input = build_normalizer_input(
        detail_payload,
        streams_payload,
    )
    # The full payloads, including any location stream, go out of scope before
    # the core normalizer is invoked. Only the privacy-minimized input remains.
    del detail_payload, streams_payload, detail_response, streams_response

    interval_result = normalize_stream_intervals(normalizer_input)
    one_hz_result = (
        materialize_1hz(interval_result) if include_1hz_preview else None
    )
    summary = build_normalizer_summary(interval_result, one_hz_result)
    summary["zone_analysis"] = build_intervals_zone_analysis(
        interval_result,
        adapted_zones,
        unavailable_reason=zone_adapter_reason,
    )
    summary["onflows_zone_profile"] = safe_profile_dict(profile)
    summary["onflows_load_analysis"] = calculate_onflows_intrazone_load(
        interval_result,
        profile,
    )
    summary["shadow_model_comparison"] = calculate_shadow_comparison(
        interval_result,
        experimental_configuration=model_configuration,
    )
    # Interval objects and optional 1 Hz samples are intentionally transient.
    del (
        normalizer_input,
        interval_result,
        one_hz_result,
        adapted_zones,
        profile,
        model_configuration,
    )
    return summary


def _store_onflows_profile(profile: OnFlowsZoneProfile) -> None:
    st.session_state[SESSION_ONFLOWS_PROFILE] = safe_profile_dict(profile)
    st.session_state[SESSION_ONFLOWS_PROFILE_FINGERPRINT] = (
        profile.fingerprint
    )


def _session_onflows_profile() -> OnFlowsZoneProfile:
    raw_profile = st.session_state.get(SESSION_ONFLOWS_PROFILE)
    try:
        profile = profile_from_safe_dict(raw_profile)
    except ValueError:
        profile = default_onflows_zone_profile()
    _store_onflows_profile(profile)
    return profile


def _store_shadow_configuration(
    configuration: ShadowModelConfiguration,
) -> None:
    """Keep only aggregate-safe, session-local experimental settings."""

    st.session_state[SESSION_SHADOW_CONFIGURATION] = (
        configuration_to_safe_dict(configuration)
    )


def _session_shadow_configuration() -> ShadowModelConfiguration:
    raw = st.session_state.get(SESSION_SHADOW_CONFIGURATION)
    try:
        configuration = configuration_from_safe_dict(raw)
    except ValueError:
        configuration = default_shadow_configuration()
    _store_shadow_configuration(configuration)
    return configuration


def _profile_editor_records(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [dict(row) for row in value if isinstance(row, Mapping)]
    converter = getattr(value, "to_dict", None)
    if callable(converter):
        records = converter(orient="records")
        if isinstance(records, list):
            return [
                dict(row) for row in records if isinstance(row, Mapping)
            ]
    return []


def _onflows_analysis_is_stale(
    analysis: Any,
    profile: OnFlowsZoneProfile | None,
) -> bool:
    if not isinstance(analysis, Mapping):
        return False
    return (
        profile is None
        or analysis.get("profile_fingerprint") != profile.fingerprint
    )


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
        st.session_state.pop(SESSION_NORMALIZER_REPORT, None)
        st.session_state.pop(SESSION_NORMALIZER_REPORT_ID, None)
        return None
    report = st.session_state.get(SESSION_ACTIVITY_REPORT)
    return report if isinstance(report, Mapping) else None


def _normalizer_report_for_selection(
    selected_activity_id: str,
) -> Mapping[str, Any] | None:
    if (
        st.session_state.get(SESSION_NORMALIZER_REPORT_ID)
        != selected_activity_id
    ):
        st.session_state.pop(SESSION_NORMALIZER_REPORT, None)
        st.session_state.pop(SESSION_NORMALIZER_REPORT_ID, None)
        return None
    report = st.session_state.get(SESSION_NORMALIZER_REPORT)
    return report if isinstance(report, Mapping) else None


def _render_table(rows: list[dict[str, Any]], empty_message: str) -> None:
    if not rows:
        st.info(empty_message)
        return
    st.dataframe(rows, width="stretch", hide_index=True)


def _render_registry_help(
    definition: Mapping[str, Any], *, key: str
) -> None:
    """Render a click/keyboard accessible explanation, not a hover tooltip."""

    with st.popover(
        "?",
        key=key,
    ):
        st.markdown(explanation_text(definition))


def _render_model_help_strip(
    registry: Mapping[str, Mapping[str, Any]],
    item_ids: Sequence[str],
    *,
    key_prefix: str,
) -> None:
    validate_registry_items(item_ids, registry)
    help_columns = st.columns(4)
    for index, item_id in enumerate(item_ids):
        definition = registry[item_id]
        with help_columns[index % len(help_columns)]:
            label_columns = st.columns([0.8, 0.2])
            label_columns[0].text(str(definition["short_name"]))
            with label_columns[1]:
                _render_registry_help(
                    definition,
                    key=f"{key_prefix}_{item_id.replace('.', '_')}",
                )


def _clear_shadow_setting_widgets() -> None:
    for key in list(st.session_state):
        if str(key).startswith(SHADOW_SETTING_KEY_PREFIX):
            st.session_state.pop(key, None)


def _render_shadow_settings_panel(
    selected_activity_id: str,
    normalizer_summary: Mapping[str, Any] | None,
) -> tuple[ShadowModelConfiguration, Mapping[str, Any] | None]:
    """Render validated session-only settings and recalculate on submit."""

    baseline = default_shadow_configuration()
    configuration = _session_shadow_configuration()
    registry = build_model_registry(configuration, baseline=baseline)
    parameter_ids = [
        item_id for item_id in registry if item_id.startswith("parameter.")
    ]
    validate_registry_items(parameter_ids, registry)

    with st.expander("Моделни настройки", expanded=False):
        st.caption(
            "Промените са временни, само в паметта на този TEST shadow "
            "изглед. Те не се записват, не променят main и не участват в "
            "реални тренировъчни планове. Само tester/administrator може "
            "да редактира разрешените полета в настоящия пилот."
        )
        if configuration.is_experimental:
            st.warning(
                "ЕКСПЕРИМЕНТАЛНА КОНФИГУРАЦИЯ: показаните текущи "
                "стойности се различават от началните."
            )
        else:
            st.info("Използват се началните shadow моделни стойности.")

        submitted_values: dict[str, float] = {}
        with st.form("_shadow_model_settings_form", clear_on_submit=False):
            zone_tabs = st.tabs([zone.zone for zone in configuration.zones])
            for zone_index, (zone, initial_zone) in enumerate(
                zip(configuration.zones, baseline.zones)
            ):
                with zone_tabs[zone_index]:
                    headings = st.columns([1.45, 0.8, 1.1, 1.35, 0.35])
                    headings[0].markdown("**Параметър**")
                    headings[1].markdown("**Начална**")
                    headings[2].markdown("**Текуща**")
                    headings[3].markdown("**Единица · източник · версия**")
                    headings[4].markdown("**?**")
                    for field in EDITABLE_FIELDS:
                        item_id = f"parameter.{zone.zone}.{field}"
                        definition = registry[item_id]
                        columns = st.columns([1.45, 0.8, 1.1, 1.35, 0.35])
                        columns[0].text(str(definition["short_name"]))
                        percent = field.startswith("spill_")
                        scale = 100.0 if percent else 1.0
                        initial_value = float(getattr(initial_zone, field)) * scale
                        current_value = float(getattr(zone, field)) * scale
                        minimum, maximum = FIELD_RANGES[field]
                        columns[1].text(f"{initial_value:.3f}".rstrip("0").rstrip("."))
                        with columns[2]:
                            rendered = st.number_input(
                                str(definition["full_name"]),
                                min_value=float(minimum * scale),
                                max_value=float(maximum * scale),
                                value=float(current_value),
                                step=(
                                    1.0
                                    if percent
                                    else 0.05
                                    if field in {"power", "bounds_factor"}
                                    else 1.0
                                ),
                                key=(
                                    f"{SHADOW_SETTING_KEY_PREFIX}"
                                    f"{zone.zone}_{field}"
                                ),
                                label_visibility="collapsed",
                            )
                        submitted_values[item_id] = float(rendered) / scale
                        columns[3].caption(
                            f"{FIELD_UNITS[field]} · {definition['value_source']} · "
                            f"{definition['version']}"
                        )
                        with columns[4]:
                            _render_registry_help(
                                definition,
                                key=f"_shadow_help_{zone.zone}_{field}",
                            )

                    for field in READ_ONLY_FIELDS:
                        item_id = f"parameter.{zone.zone}.{field}"
                        definition = registry[item_id]
                        columns = st.columns([1.45, 0.8, 1.1, 1.35, 0.35])
                        columns[0].text(str(definition["short_name"]))
                        columns[1].text(str(definition["initial_value"]))
                        columns[2].text(str(definition["current_value"]))
                        columns[3].caption(
                            f"{FIELD_UNITS[field]} · {definition['value_source']} · "
                            f"{definition['version']} · само за четене"
                        )
                        with columns[4]:
                            _render_registry_help(
                                definition,
                                key=f"_shadow_help_{zone.zone}_{field}",
                            )

            apply_settings = st.form_submit_button(
                "Приложи временно и преизчисли",
                type="primary",
                width="stretch",
            )

        reset_columns = st.columns(2)
        reset_requested = reset_columns[0].button(
            "Върни началните стойности",
            key="_reset_shadow_model_settings",
            width="stretch",
        )
        legacy_reset_requested = reset_columns[1].button(
            "Възстанови стандартния onFlows профил",
            key="_restore_default_onflows_profile",
            width="stretch",
        )

        candidate: ShadowModelConfiguration | None = None
        if apply_settings:
            try:
                candidate = configuration_with_overrides(
                    submitted_values,
                    baseline=baseline,
                )
            except ValueError as exc:
                st.error(f"Невалидна експериментална стойност: {exc}")
        elif reset_requested or legacy_reset_requested:
            candidate = reset_shadow_configuration()
            _clear_shadow_setting_widgets()

        if candidate is not None:
            _store_shadow_configuration(candidate)
            configuration = candidate
            if selected_activity_id:
                try:
                    with st.spinner(
                        "Преизчисляване на baseline и experimental shadow резултатите…"
                    ):
                        normalizer_summary = _run_activity_normalizer(
                            selected_activity_id,
                            include_1hz_preview=False,
                            onflows_profile=profile_from_configuration(candidate),
                            shadow_configuration=candidate,
                        )
                    st.session_state[SESSION_NORMALIZER_REPORT] = normalizer_summary
                    st.session_state[SESSION_NORMALIZER_REPORT_ID] = selected_activity_id
                    st.success("Shadow резултатите са преизчислени само в паметта.")
                except IntervalsAPIError as exc:
                    st.warning(str(exc))
                except Exception:
                    st.error("Локална грешка при shadow преизчислението.")
            else:
                st.info(
                    "Настройките са приложени. Изберете активност, за да се "
                    "изчислят реалните baseline и experimental резултати."
                )
            if reset_requested or legacy_reset_requested:
                st.rerun()

    return configuration, normalizer_summary


def _render_shadow_comparison(
    comparison: Mapping[str, Any] | None,
) -> None:
    st.subheader("Baseline ↔ experimental shadow сравнение")
    if not isinstance(comparison, Mapping):
        st.info(
            "Стартирайте interval-aware normalizer, за да се запазят "
            "едновременно началният и експерименталният резултат."
        )
        return

    experimental_configuration = comparison.get("experimental_configuration")
    try:
        configuration = configuration_from_safe_dict(experimental_configuration)
    except ValueError:
        st.error("Shadow сравнението съдържа невалидна моделна конфигурация.")
        return
    registry = comparison.get("registry")
    if not isinstance(registry, Mapping):
        registry = build_model_registry(configuration)
    result_ids = (
        "result.t",
        "result.q",
        "result.cascade",
        "result.spillover",
        "result.e",
        "result.tref_raw",
        "result.tref_effective",
        "result.hr_coverage",
    )
    validate_registry_items(result_ids, registry)

    if configuration.is_experimental:
        st.warning(
            "ЕКСПЕРИМЕНТАЛНА КОНФИГУРАЦИЯ — сравнението е диагностично "
            "и не влияе на планове или на основния демонстратор."
        )
    else:
        st.info("Baseline и experimental конфигурацията са идентични.")

    _render_model_help_strip(
        registry,
        result_ids,
        key_prefix="_shadow_result_help",
    )

    rows = []
    for row in comparison.get("comparison_rows", []):
        if not isinstance(row, Mapping):
            continue
        rendered: dict[str, Any] = {"зона": row.get("zone")}
        for field, label in (
            ("T_z", "T_z"),
            ("Q_z", "Q_z"),
            ("cascade", "cascade"),
            ("spillover_received", "spillover"),
            ("E_z", "E_z"),
            ("tref_raw", "tref_raw"),
            ("tref_effective", "tref_effective"),
        ):
            rendered[f"начален {label}"] = row.get(f"baseline_{field}")
            rendered[f"експериментален {label}"] = row.get(
                f"experimental_{field}"
            )
            rendered[f"Δ {label}"] = row.get(f"delta_{field}")
        rows.append(rendered)
    _render_table(rows, "Няма изчислени shadow резултати по зони.")

    experimental_result = comparison.get("experimental")
    if isinstance(experimental_result, Mapping):
        for index, warning in enumerate(experimental_result.get("warnings", [])):
            if not isinstance(warning, Mapping):
                continue
            warning_id = str(warning.get("id") or "")
            definition = registry.get(warning_id)
            if not isinstance(definition, Mapping):
                continue
            columns = st.columns([0.94, 0.06])
            columns[0].warning(str(warning.get("message") or definition["description"]))
            with columns[1]:
                _render_registry_help(
                    definition,
                    key=f"_shadow_warning_help_{index}_{warning_id.replace('.', '_')}",
                )

    st.download_button(
        "Изтегли безопасно shadow моделно сравнение JSON",
        data=export_shadow_diagnostics_json(comparison),
        file_name="onflows_shadow_model_comparison.json",
        mime="application/json",
        width="stretch",
    )


def _render_report(
    report: Mapping[str, Any],
    activity_report: Mapping[str, Any] | None = None,
) -> None:
    counts = report.get("counts", {})
    shadow_configuration = _session_shadow_configuration()
    onflows_profile = profile_from_configuration(shadow_configuration)
    (
        coverage,
        streams,
        mapping_report,
        model_readiness,
        endpoint_checks,
    ) = _combined_diagnostics(report, activity_report)
    stream_quality = (
        activity_report.get("stream_quality")
        if isinstance(activity_report, Mapping)
        else None
    )
    selected_activity_id = str(
        st.session_state.get(SESSION_ACTIVITY_REPORT_ID) or ""
    )
    normalizer_summary = (
        _normalizer_report_for_selection(selected_activity_id)
        if selected_activity_id and isinstance(activity_report, Mapping)
        else None
    )

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
            "Качество на реалните streams",
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
        st.caption(
            "Стойностна диагностика само за изрично избраната активност. "
            "Показват се агрегати; няма ID, token, абсолютни timestamps, "
            "GPS координати или сурови точки."
        )
        if isinstance(stream_quality, Mapping):
            stream_lengths = stream_quality.get("stream_lengths", {})
            timing = stream_quality.get("timing", {})
            dt_distribution = (
                timing.get("dt_distribution", {})
                if isinstance(timing, Mapping)
                else {}
            )
            recording_stops = stream_quality.get("recording_stops", {})
            recording_segments = stream_quality.get(
                "recording_segments", {}
            )
            speed = stream_quality.get("speed", {})

            st.subheader("Streams и числово покритие")
            _render_table(
                list(stream_quality.get("streams", [])),
                "Няма безопасни числови stream агрегати.",
            )
            _render_table(
                [
                    {
                        "еднаква дължина": stream_lengths.get("all_equal"),
                        "различни дължини": stream_lengths.get(
                            "distinct_point_counts"
                        ),
                        "min точки": stream_lengths.get("min_point_count"),
                        "max точки": stream_lengths.get("max_point_count"),
                        "референтни точки": stream_lengths.get(
                            "reference_point_count"
                        ),
                        "изключени location streams": stream_quality.get(
                            "location_stream_excluded_count", 0
                        ),
                    }
                ],
                "Няма информация за дължините на streams.",
            )

            st.subheader("Времева решетка и dt")
            st.caption(
                "dt е разликата между два съседни offsets в оригиналния "
                "time stream: right_offset_sec − left_offset_sec. "
                "Bucket процентите са спрямо всички числови съседни dt; "
                "percentiles използват само положителните dt."
            )
            _render_table(
                [
                    {
                        "точки": timing.get("point_count"),
                        "валидни offsets": timing.get("valid_offset_count"),
                        "dt интервали": timing.get("dt_interval_count"),
                        "median dt (s)": timing.get("median_dt_sec"),
                        "mode dt (s)": timing.get("mode_dt_sec"),
                        "min dt (s)": timing.get("min_dt_sec"),
                        "max dt (s)": timing.get("max_dt_sec"),
                        "точно 1 s (%)": timing.get(
                            "exactly_1s_interval_percent"
                        ),
                        "повторени offsets": timing.get(
                            "repeated_offset_count"
                        ),
                        "non-monotonic offsets": timing.get(
                            "non_monotonic_offset_count"
                        ),
                        "gaps > 1.5 s": timing.get("gap_count_over_1_5s"),
                        "stream duration (s)": timing.get(
                            "stream_duration_sec"
                        ),
                    }
                ],
                "Няма time stream за времева диагностика.",
            )
            _render_table(
                list(dt_distribution.get("buckets", []))
                if isinstance(dt_distribution, Mapping)
                else [],
                "Няма dt buckets.",
            )
            percentiles = (
                dt_distribution.get("percentiles_sec", {})
                if isinstance(dt_distribution, Mapping)
                else {}
            )
            _render_table(
                [dict(percentiles)] if isinstance(percentiles, Mapping) else [],
                "Няма dt percentiles.",
            )

            st.subheader("Recording stops и gaps")
            _render_table(
                [dict(recording_stops)]
                if isinstance(recording_stops, Mapping)
                else [],
                "Няма recording-stop диагностика.",
            )

            st.subheader("Непрекъснати recording сегменти")
            if isinstance(recording_segments, Mapping):
                segment_summary = {
                    key: value
                    for key, value in recording_segments.items()
                    if key != "dt_distribution"
                }
                _render_table(
                    [segment_summary],
                    "Няма recording сегменти.",
                )
                segment_distribution = recording_segments.get(
                    "dt_distribution", {}
                )
                _render_table(
                    list(segment_distribution.get("buckets", []))
                    if isinstance(segment_distribution, Mapping)
                    else [],
                    "Няма вътрешни segment dt buckets.",
                )

            st.subheader("Speed и състояние на крайните точки")
            if isinstance(speed, Mapping):
                speed_summary = {
                    key: value
                    for key, value in speed.items()
                    if key != "dt_buckets"
                }
                _render_table(
                    [speed_summary],
                    "Няма speed stream.",
                )
                _render_table(
                    list(speed.get("dt_buckets", [])),
                    "Няма speed агрегати по dt bucket.",
                )

            st.subheader("Duration reconciliation")
            reconciliation = stream_quality.get(
                "duration_reconciliation", {}
            )
            _render_table(
                [dict(reconciliation)]
                if isinstance(reconciliation, Mapping)
                else [],
                "Няма достатъчно durations за reconciliation.",
            )

            st.subheader("HR и допълнително покритие")
            heart_rate = stream_quality.get("heart_rate", {})
            metric_coverage = stream_quality.get("metric_coverage", {})
            _render_table(
                [dict(heart_rate)]
                if isinstance(heart_rate, Mapping)
                else [],
                "Няма HR stream.",
            )
            _render_table(
                [
                    {"metric": metric, **dict(values)}
                    for metric, values in metric_coverage.items()
                    if isinstance(values, Mapping)
                ]
                if isinstance(metric_coverage, Mapping)
                else [],
                "Няма cadence, altitude или power streams.",
            )

            st.subheader("Диагностични предупреждения")
            for warning in stream_quality.get("warnings", []):
                if isinstance(warning, Mapping) and warning.get("message"):
                    st.warning(str(warning["message"]))

            st.subheader("Консервативна 1 Hz нормализация")
            st.caption(
                "Основният режим създава лека interval-aware структура. "
                "Временен 1 Hz изглед се създава само от втория изричен "
                "бутон, само в паметта, и точките никога не се показват, "
                "изтеглят или записват. Processing time е само техническа "
                "диагностика."
            )
            if selected_activity_id:
                if st.button(
                    "Стартирай interval-aware normalizer",
                    key="_run_interval_aware_normalizer",
                    width="stretch",
                ):
                    try:
                        with st.spinner(
                            "Консервативна interval-aware нормализация…"
                        ):
                            normalizer_summary = _run_activity_normalizer(
                                selected_activity_id,
                                include_1hz_preview=False,
                                onflows_profile=onflows_profile,
                                shadow_configuration=shadow_configuration,
                            )
                        st.session_state[SESSION_NORMALIZER_REPORT] = (
                            normalizer_summary
                        )
                        st.session_state[SESSION_NORMALIZER_REPORT_ID] = (
                            selected_activity_id
                        )
                    except IntervalsAPIError as exc:
                        st.warning(str(exc))
                    except Exception:
                        st.error(
                            "Локална грешка при normalizer диагностиката."
                        )
                if st.button(
                    "Създай временен 1 Hz preview и агрегати",
                    key="_run_materialized_1hz_preview",
                    width="stretch",
                ):
                    try:
                        with st.spinner(
                            "Interval-aware нормализация и временен 1 Hz "
                            "preview…"
                        ):
                            normalizer_summary = _run_activity_normalizer(
                                selected_activity_id,
                                include_1hz_preview=True,
                                onflows_profile=onflows_profile,
                                shadow_configuration=shadow_configuration,
                            )
                        st.session_state[SESSION_NORMALIZER_REPORT] = (
                            normalizer_summary
                        )
                        st.session_state[SESSION_NORMALIZER_REPORT_ID] = (
                            selected_activity_id
                        )
                    except IntervalsAPIError as exc:
                        st.warning(str(exc))
                    except Exception:
                        st.error(
                            "Локална грешка при временния 1 Hz preview."
                        )

            if isinstance(normalizer_summary, Mapping):
                summary_fields = (
                    "algorithm_version",
                    "path",
                    "fast_path_used",
                    "input_point_count",
                    "valid_offset_point_count",
                    "unique_valid_point_count",
                    "interval_count",
                    "normalized_second_count_estimate",
                    "original_second_count",
                    "interpolated_short_second_count",
                    "interpolated_extended_second_count",
                    "points_with_missing_metrics_estimate",
                    "recording_segment_count",
                    "active_duration_sec",
                    "duplicate_offset_count",
                    "invalid_offset_count",
                    "original_point_percent",
                    "interpolated_point_percent",
                    "processing_time_ms",
                    "approximate_interval_result_size_bytes",
                )
                _render_table(
                    [
                        {
                            key: normalizer_summary.get(key)
                            for key in summary_fields
                        }
                    ],
                    "Няма interval-aware normalizer summary.",
                )
                classifications = normalizer_summary.get(
                    "classifications", {}
                )
                _render_table(
                    [
                        {"classification": name, **dict(values)}
                        for name, values in classifications.items()
                        if isinstance(values, Mapping)
                    ]
                    if isinstance(classifications, Mapping)
                    else [],
                    "Няма класифицирани интервали.",
                )
                invalid_values = normalizer_summary.get(
                    "invalid_values_by_metric", {}
                )
                _render_table(
                    [
                        {
                            "metric": metric,
                            "invalid_value_count": count,
                        }
                        for metric, count in invalid_values.items()
                    ]
                    if isinstance(invalid_values, Mapping)
                    else [],
                    "Няма структурно невалидни stream стойности.",
                )
                reconciliation = normalizer_summary.get(
                    "reconciliation", {}
                )
                _render_table(
                    [dict(reconciliation)]
                    if isinstance(reconciliation, Mapping)
                    else [],
                    "Няма normalizer duration reconciliation.",
                )
                materialization = normalizer_summary.get(
                    "materialize_1hz", {}
                )
                _render_table(
                    [dict(materialization)]
                    if isinstance(materialization, Mapping)
                    else [],
                    "Не е поискан временен 1 Hz preview.",
                )
                for warning in normalizer_summary.get("warnings", []):
                    if isinstance(warning, Mapping) and warning.get("message"):
                        st.warning(str(warning["message"]))
            else:
                st.info(
                    "Normalizer-ът още не е стартиран за избраната "
                    "активност."
                )

            st.subheader("onFlows вътрешнозоново претегляне")
            st.caption(
                "Този слой използва собствен регулируем onFlows профил и "
                "аналитично интегрира k = W / W_low директно върху "
                "interval-aware резултата. Не използва icu_hr_zones и не "
                "изисква временен 1 Hz preview."
            )
            shadow_configuration, normalizer_summary = (
                _render_shadow_settings_panel(
                    selected_activity_id,
                    normalizer_summary
                    if isinstance(normalizer_summary, Mapping)
                    else None,
                )
            )
            current_onflows_profile = profile_from_configuration(
                shadow_configuration
            )

            onflows_load_analysis = (
                normalizer_summary.get("onflows_load_analysis")
                if isinstance(normalizer_summary, Mapping)
                else None
            )
            analysis_is_stale = _onflows_analysis_is_stale(
                onflows_load_analysis,
                current_onflows_profile,
            )
            if analysis_is_stale:
                st.warning(
                    "Показаният onFlows резултат е изчислен с предишен "
                    "профил. Използвайте бутона за преизчисление."
                )

            if isinstance(onflows_load_analysis, Mapping):
                legacy_registry = build_model_registry(
                    shadow_configuration,
                    baseline=default_shadow_configuration(),
                )
                _render_model_help_strip(
                    legacy_registry,
                    (
                        "parameter.Z1.weight_low",
                        "parameter.Z1.weight_high",
                        "parameter.Z1.power",
                        "result.t",
                        "result.q",
                        "result.average_k",
                        "result.zone_share",
                        "result.active_duration",
                        "result.classified_hr",
                        "result.unclassified_hr",
                        "result.hr_coverage",
                        "result.excluded_duration",
                        "model.profile_fingerprint",
                    ),
                    key_prefix="_onflows_table_help",
                )
                _render_table(
                    [
                        {
                            "зона": row.get("zone"),
                            "HR диапазон": (
                                f"{row.get('hr_low')}–{row.get('hr_high')}"
                            ),
                            "W_low": row.get("weight_low"),
                            "W_high": row.get("weight_high"),
                            "p": row.get("power"),
                            "реално време T_z (s)": row.get(
                                "real_seconds"
                            ),
                            "претеглено време Q_z (s)": row.get(
                                "weighted_seconds"
                            ),
                            "среден k_z": row.get("average_k"),
                            "% от класифицираното T": row.get(
                                "percent_of_classified_hr_time"
                            ),
                        }
                        for row in onflows_load_analysis.get("zones", [])
                        if isinstance(row, Mapping)
                    ],
                    "Няма onFlows вътрешнозонови агрегати.",
                )
                _render_table(
                    [
                        {
                            "активно време (s)": onflows_load_analysis.get(
                                "active_duration_sec"
                            ),
                            "класифицирано HR време (s)": (
                                onflows_load_analysis.get("classified_hr_sec")
                            ),
                            "неопределено HR време (s)": (
                                onflows_load_analysis.get(
                                    "unclassified_hr_sec"
                                )
                            ),
                            "HR coverage (%)": onflows_load_analysis.get(
                                "hr_coverage_percent"
                            ),
                            "изключено време (s)": onflows_load_analysis.get(
                                "excluded_duration_sec"
                            ),
                            "общо реално T (s)": onflows_load_analysis.get(
                                "total_real_sec"
                            ),
                            "диагностичен сбор Q (s)": (
                                onflows_load_analysis.get(
                                    "total_weighted_sec"
                                )
                            ),
                            "среден коефициент": onflows_load_analysis.get(
                                "overall_average_k"
                            ),
                            "profile version": onflows_load_analysis.get(
                                "profile_schema_version"
                            ),
                            "profile fingerprint": onflows_load_analysis.get(
                                "profile_fingerprint"
                            ),
                        }
                    ],
                    "Няма onFlows summary.",
                )
            else:
                st.info(
                    "Стартирайте interval-aware normalizer или "
                    "преизчислението с текущия профил."
                )
            shadow_comparison = (
                normalizer_summary.get("shadow_model_comparison")
                if isinstance(normalizer_summary, Mapping)
                else None
            )
            _render_shadow_comparison(
                shadow_comparison
                if isinstance(shadow_comparison, Mapping)
                else None
            )

            st.subheader("Експериментално време по HR зони")
            st.caption(
                "Изчислението използва директно надеждните активни "
                "interval-aware интервали и линейна промяна на HR между "
                "валидни крайни стойности. Не се създава 1 Hz поток. "
                "Intervals времената са само сравнителна референция, не "
                "ground truth и не участват в onFlows изчислението."
            )
            zone_analysis = (
                normalizer_summary.get("zone_analysis")
                if isinstance(normalizer_summary, Mapping)
                else None
            )
            if isinstance(zone_analysis, Mapping):
                reason_messages = {
                    "hr_stream_unavailable": "Липсва HR stream.",
                    "icu_hr_zones_missing": (
                        "Activity detail не съдържа icu_hr_zones."
                    ),
                    "icu_hr_zones_invalid_structure": (
                        "icu_hr_zones не е поддържаният масив от граници."
                    ),
                    "icu_hr_zones_invalid_boundaries": (
                        "icu_hr_zones съдържа невалидни HR граници."
                    ),
                    "icu_hr_zones_not_strictly_increasing": (
                        "HR границите не са строго нарастващи."
                    ),
                }
                if not zone_analysis.get("available"):
                    reason = str(zone_analysis.get("reason") or "")
                    st.warning(
                        reason_messages.get(
                            reason,
                            "HR zone диагностиката не е налична.",
                        )
                    )

                zone_rows: list[dict[str, Any]] = []
                for row in zone_analysis.get("zones", []):
                    if not isinstance(row, Mapping):
                        continue
                    left_bracket = (
                        "[" if row.get("lower_inclusive") else "("
                    )
                    right_bracket = (
                        "]" if row.get("upper_inclusive") else ")"
                    )
                    zone_rows.append(
                        {
                            "зона": row.get("zone"),
                            "HR граници": (
                                f"{left_bracket}{row.get('lower_bpm')}, "
                                f"{row.get('upper_bpm')}{right_bracket}"
                            ),
                            "onFlows interval-aware време (s)": row.get(
                                "seconds"
                            ),
                            "% от класифицираното време": row.get(
                                "percent_of_classified_hr_time"
                            ),
                            "Intervals време (s)": row.get(
                                "intervals_reference_sec"
                            ),
                            "разлика (s)": row.get("difference_sec"),
                        }
                    )
                _render_table(
                    zone_rows,
                    "Няма валидни HR зони за сравнение.",
                )
                _render_table(
                    [
                        {
                            "активно време (s)": zone_analysis.get(
                                "active_duration_sec"
                            ),
                            "класифицирано HR време (s)": zone_analysis.get(
                                "classified_hr_sec"
                            ),
                            "неопределено HR време (s)": zone_analysis.get(
                                "unclassified_hr_sec"
                            ),
                            "HR coverage (%)": zone_analysis.get(
                                "hr_coverage_percent"
                            ),
                            "изключено stops/pauses/gaps време (s)": (
                                zone_analysis.get("excluded_duration_sec")
                            ),
                        }
                    ],
                    "Няма HR zone агрегати.",
                )
                excluded_by_classification = zone_analysis.get(
                    "excluded_duration_by_classification", {}
                )
                _render_table(
                    [
                        {
                            "изключена класификация": classification,
                            "време (s)": seconds,
                        }
                        for classification, seconds in (
                            excluded_by_classification.items()
                            if isinstance(
                                excluded_by_classification, Mapping
                            )
                            else []
                        )
                    ],
                    "Няма изключено време от stops, pauses или gaps.",
                )
            else:
                st.info(
                    "Стартирайте interval-aware normalizer, за да се "
                    "изчисли експерименталното време по HR зони."
                )
            st.download_button(
                "Изтегли безопасна диагностика JSON",
                data=export_stream_quality_json(
                    stream_quality,
                    normalizer_summary
                    if isinstance(normalizer_summary, Mapping)
                    else None,
                ),
                file_name="intervals_stream_quality.json",
                mime="application/json",
                width="stretch",
            )
        else:
            st.info(
                "Изберете активност и натиснете „Провери избраната "
                "активност“, за да се изчисли диагностиката."
            )

    with tabs[5]:
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

    with tabs[6]:
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

    with tabs[7]:
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


def _render_inspector() -> None:
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
        st.page_link(
            "views/privacy-policy.py",
            label="Privacy Policy / Политика за поверителност",
            icon="🔐",
        )
        st.warning(
            "Пилотът засега е предназначен само за пълнолетни "
            "потребители (18+)."
        )
        privacy_consent = st.checkbox(
            "Прочетох Privacy Policy и давам доброволното си съгласие "
            "за описаното обработване на профилни, спортни, activity, "
            "settings и calendar данни.",
            value=False,
            key="_privacy_and_general_consent",
        )
        health_consent = st.checkbox(
            "Давам отделно и изрично съгласие за обработване на wellness "
            "и свързани със здравето данни, включително пулс, HRV, сън, "
            "умора и други налични health-related полета.",
            value=False,
            key="_wellness_health_explicit_consent",
        )
        adult_confirmed = st.checkbox(
            "Потвърждавам, че съм навършил/а 18 години.",
            value=False,
            key="_adult_confirmed",
        )
        all_confirmed = (
            privacy_consent and health_consent and adult_confirmed
        )
        if not all_confirmed:
            st.button(
                "Свържи Intervals.icu",
                type="primary",
                width="stretch",
                disabled=True,
            )
            st.caption(
                "И трите потвърждения са задължителни преди започване "
                "на OAuth връзката."
            )
            return

        consent_evidence = PendingConsentEvidence(
            policy_version=PRIVACY_POLICY_VERSION,
            confirmed_at=datetime.now(timezone.utc).timestamp(),
            privacy_and_general_consent=True,
            wellness_health_explicit_consent=True,
            adult_confirmed=True,
        )
        state = _oauth_state(config, consent=consent_evidence)
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
        "Период за списъка с активности",
        options=INSPECTION_PERIOD_OPTIONS,
        index=0,
        horizontal=True,
        format_func=lambda value: f"{value} дни",
    )
    st.caption(
        "Само списъкът с активности се разширява до избрания период. "
        "Останалите периодични API проверки остават ограничени до максимум "
        "30 дни, а detail и streams се зареждат on-demand само за избраната "
        "активност."
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
            st.session_state.pop(SESSION_NORMALIZER_REPORT, None)
            st.session_state.pop(SESSION_NORMALIZER_REPORT_ID, None)

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
                    st.session_state.pop(SESSION_NORMALIZER_REPORT, None)
                    st.session_state.pop(SESSION_NORMALIZER_REPORT_ID, None)
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


def main() -> None:
    st.set_page_config(
        page_title="onFlows Pilot",
        page_icon="🔎",
        layout="wide",
    )
    inspector_page = st.Page(
        _render_inspector,
        title="Intervals Data Inspector",
        icon="🔎",
        default=True,
    )
    about_page = st.Page(
        "views/about.py",
        title="About onFlows",
        icon="ℹ️",
        url_path=ABOUT_URL_PATH,
    )
    privacy_page = st.Page(
        "views/privacy-policy.py",
        title="Privacy Policy",
        icon="🔐",
        url_path=PRIVACY_URL_PATH,
    )
    st.navigation(
        [inspector_page, about_page, privacy_page],
        position="top",
    ).run()


if __name__ == "__main__":
    main()
