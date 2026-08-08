"""Reusable real-activity orchestration for the onFlows shadow models.

The module intentionally has no Streamlit dependency.  API loading, quality
validation, normalization, the canonical adapter, physiology, and presentation
remain separate layers.  Raw Intervals payloads and normalized points are kept
only for the duration of a call; returned values are aggregate diagnostics.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
import math
import re
from typing import Any, Protocol

from intervals_inspector.intervals_client import IntervalsResponse
from intervals_inspector.intervals_hr_adapter import (
    adapt_intervals_hr_zones,
    build_intervals_zone_analysis,
)
from intervals_inspector.onflows_intrazone_load import (
    calculate_onflows_intrazone_load,
)
from intervals_inspector.onflows_zone_profile import (
    OnFlowsZoneProfile,
    default_onflows_zone_profile,
    safe_profile_dict,
)
from intervals_inspector.shadow_model import (
    HISTORY_WINDOW_DAYS,
    LOW_HR_COVERAGE_PERCENT,
    ShadowModelConfiguration,
    calculate_shadow_comparison,
    configuration_from_profile,
)
from intervals_inspector.stream_normalizer import (
    ALGORITHM_VERSION as NORMALIZATION_VERSION,
    IntervalAwareResult,
    build_normalizer_input,
    build_normalizer_summary,
    materialize_1hz,
    normalize_stream_intervals,
)
from intervals_inspector.stream_quality import analyze_stream_quality


CANONICAL_INPUT_VERSION = "canonical-real-activity-v1"
MAX_HISTORY_ACTIVITIES = 200
_SAFE_ACTIVITY_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")


class ActivityClient(Protocol):
    """Small read-only client boundary used by this orchestration service."""

    def get_activities_result(self, oldest: str, newest: str) -> IntervalsResponse: ...

    def get_activity_result(
        self, activity_id: str, *, include_intervals: bool = False
    ) -> IntervalsResponse: ...

    def get_streams_result(self, activity_id: str) -> IntervalsResponse: ...


@dataclass(frozen=True, slots=True)
class CanonicalModelInput:
    """Canonical boundary consumed by the physiological model layer."""

    normalized_activity: IntervalAwareResult
    activity_date: date | None
    prior_baseline_effective: tuple[Mapping[str, Any], ...] = ()
    prior_experimental_effective: tuple[Mapping[str, Any], ...] = ()
    schema_version: str = CANONICAL_INPUT_VERSION
    normalization_version: str = NORMALIZATION_VERSION


def build_canonical_model_input(
    normalized_activity: IntervalAwareResult,
    *,
    activity_date: date | None = None,
    prior_baseline_effective: Sequence[Mapping[str, Any]] = (),
    prior_experimental_effective: Sequence[Mapping[str, Any]] = (),
) -> CanonicalModelInput:
    """Adapt one normalized activity without copying or re-normalizing it."""

    if not isinstance(normalized_activity, IntervalAwareResult):
        raise TypeError("normalized_activity must be an IntervalAwareResult")
    return CanonicalModelInput(
        normalized_activity=normalized_activity,
        activity_date=activity_date,
        prior_baseline_effective=tuple(prior_baseline_effective),
        prior_experimental_effective=tuple(prior_experimental_effective),
    )


def _response_payload(value: Any) -> Any:
    if not isinstance(value, IntervalsResponse):
        raise TypeError("unexpected Intervals response envelope")
    return value.payload


def _activity_date(value: Mapping[str, Any]) -> date | None:
    raw = value.get("start_date_local") or value.get("start_date")
    if not isinstance(raw, str):
        return None
    try:
        return date.fromisoformat(raw.strip()[:10])
    except ValueError:
        return None


def _safe_number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    rendered = float(value)
    return rendered if math.isfinite(rendered) and rendered >= 0.0 else None


def _safe_activity_metadata(detail: Mapping[str, Any]) -> dict[str, Any]:
    activity_day = _activity_date(detail)
    sport = detail.get("type") or detail.get("sub_type") or detail.get("sport")
    safe_sport = (
        re.sub(r"[^\w /+.-]", "", str(sport), flags=re.UNICODE).strip()[:40]
        if sport is not None
        else None
    )
    return {
        "date": activity_day.isoformat() if activity_day else None,
        "sport": safe_sport or None,
        "elapsed_time_sec": _safe_number(detail.get("elapsed_time")),
        "moving_time_sec": _safe_number(detail.get("moving_time")),
        "recording_time_sec": _safe_number(detail.get("icu_recording_time")),
    }


def _normalise_activity(
    detail_payload: Mapping[str, Any],
    streams_payload: Any,
    *,
    include_1hz_preview: bool,
) -> tuple[IntervalAwareResult, dict[str, Any]]:
    normalizer_input = build_normalizer_input(detail_payload, streams_payload)
    interval_result = normalize_stream_intervals(normalizer_input)
    one_hz_result = materialize_1hz(interval_result) if include_1hz_preview else None
    summary = build_normalizer_summary(interval_result, one_hz_result)
    return interval_result, summary


def run_physiological_models(
    canonical_input: CanonicalModelInput,
    *,
    profile: OnFlowsZoneProfile,
    experimental_configuration: ShadowModelConfiguration,
) -> tuple[dict[str, Any], dict[str, Any] | None, dict[str, str]]:
    """Run the unchanged bridge models against exactly one canonical input."""

    analysis = calculate_onflows_intrazone_load(
        canonical_input.normalized_activity,
        profile,
    )
    coverage = float(analysis.get("hr_coverage_percent") or 0.0)
    classified = float(analysis.get("classified_hr_sec") or 0.0)
    if not analysis.get("available") or classified <= 0.0:
        return (
            analysis,
            None,
            {
                "status": "not_run",
                "reason": "Липсва надежден HR поток за физиологично изчисление.",
            },
        )

    comparison = calculate_shadow_comparison(
        canonical_input.normalized_activity,
        experimental_configuration=experimental_configuration,
        prior_baseline_effective=canonical_input.prior_baseline_effective,
        prior_experimental_effective=canonical_input.prior_experimental_effective,
        activity_date=canonical_input.activity_date,
    )
    if coverage < LOW_HR_COVERAGE_PERCENT:
        status = {
            "status": "limited",
            "reason": (
                f"HR покритието е {coverage:.1f}% и резултатът е ограничен; "
                f"прагът за надеждно покритие е {LOW_HR_COVERAGE_PERCENT:.0f}%."
            ),
        }
    else:
        status = {
            "status": "valid",
            "reason": "HR покритието позволява диагностично shadow изчисление.",
        }
    return analysis, comparison, status


def process_activity_payloads(
    detail_payload: Mapping[str, Any],
    streams_payload: Any,
    *,
    include_1hz_preview: bool = False,
    profile: OnFlowsZoneProfile | None = None,
    experimental_configuration: ShadowModelConfiguration | None = None,
    prior_baseline_effective: Sequence[Mapping[str, Any]] = (),
    prior_experimental_effective: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """Validate, normalize, adapt, and model one already-loaded activity."""

    selected_profile = profile or default_onflows_zone_profile()
    configuration = experimental_configuration or configuration_from_profile(
        selected_profile
    )
    stream_quality = analyze_stream_quality(detail_payload, streams_payload)
    adapted_zones, zone_adapter_reason = adapt_intervals_hr_zones(detail_payload)
    interval_result, summary = _normalise_activity(
        detail_payload,
        streams_payload,
        include_1hz_preview=include_1hz_preview,
    )
    canonical_input = build_canonical_model_input(
        interval_result,
        activity_date=_activity_date(detail_payload),
        prior_baseline_effective=prior_baseline_effective,
        prior_experimental_effective=prior_experimental_effective,
    )
    onflows_analysis, comparison, model_status = run_physiological_models(
        canonical_input,
        profile=selected_profile,
        experimental_configuration=configuration,
    )

    summary["activity_metadata"] = _safe_activity_metadata(detail_payload)
    summary["stream_quality"] = stream_quality
    summary["zone_analysis"] = build_intervals_zone_analysis(
        interval_result,
        adapted_zones,
        unavailable_reason=zone_adapter_reason,
    )
    summary["onflows_zone_profile"] = safe_profile_dict(selected_profile)
    summary["onflows_load_analysis"] = onflows_analysis
    summary["shadow_model_comparison"] = comparison
    summary["model_status"] = model_status
    summary["canonical_model_input"] = {
        "schema_version": canonical_input.schema_version,
        "normalization_version": canonical_input.normalization_version,
        "activity_date": (
            canonical_input.activity_date.isoformat()
            if canonical_input.activity_date
            else None
        ),
        "normalized_once": True,
        "shared_by_baseline_and_experimental": True,
    }
    return summary


def _history_rows_from_result(result: Mapping[str, Any] | None) -> dict[str, float] | None:
    if not isinstance(result, Mapping):
        return None
    rows = result.get("rows")
    if not isinstance(rows, Sequence):
        return None
    values: dict[str, float] = {}
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        zone = str(row.get("zone") or "")
        value = row.get("E_z")
        if zone and isinstance(value, (int, float)) and not isinstance(value, bool):
            values[zone] = max(0.0, float(value))
    return values or None


def _load_history(
    client: ActivityClient,
    *,
    selected_date: date | None,
    profile: OnFlowsZoneProfile,
    experimental_configuration: ShadowModelConfiguration,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    baseline_history: list[dict[str, Any]] = []
    experimental_history: list[dict[str, Any]] = []
    diagnostics: dict[str, Any] = {
        "window_days": HISTORY_WINDOW_DAYS,
        "available_days": 0,
        "available_activities": 0,
        "skipped_activities": 0,
        "period_start": None,
        "period_end": None,
        "current_day_excluded": True,
        "limited_by_available_data": True,
        "warnings": [],
    }
    if selected_date is None:
        diagnostics["warnings"].append(
            "Липсва безопасно разпознаваема дата; Tref използва видимия моделeн fallback."
        )
        return baseline_history, experimental_history, diagnostics

    oldest = selected_date - timedelta(days=HISTORY_WINDOW_DAYS)
    newest = selected_date - timedelta(days=1)
    activities_payload = _response_payload(
        client.get_activities_result(oldest.isoformat(), newest.isoformat())
    )
    activities = (
        activities_payload
        if isinstance(activities_payload, Sequence)
        and not isinstance(activities_payload, (str, bytes, bytearray))
        else []
    )
    grouped: dict[date, list[str]] = defaultdict(list)
    for activity in activities:
        if not isinstance(activity, Mapping):
            continue
        activity_day = _activity_date(activity)
        activity_id = str(activity.get("id") or "").strip()
        if (
            activity_day is None
            or activity_day < oldest
            or activity_day >= selected_date
            or not _SAFE_ACTIVITY_ID.fullmatch(activity_id)
        ):
            continue
        grouped[activity_day].append(activity_id)

    processed_activities = 0
    skipped_activities = 0
    for activity_day in sorted(grouped):
        baseline_day: dict[str, float] = defaultdict(float)
        experimental_day: dict[str, float] = defaultdict(float)
        day_has_result = False
        for activity_id in sorted(set(grouped[activity_day])):
            if processed_activities + skipped_activities >= MAX_HISTORY_ACTIVITIES:
                skipped_activities += 1
                continue
            try:
                detail = _response_payload(
                    client.get_activity_result(activity_id, include_intervals=False)
                )
                streams = _response_payload(client.get_streams_result(activity_id))
                if not isinstance(detail, Mapping):
                    raise TypeError("activity detail is not a mapping")
                interval_result, _summary = _normalise_activity(
                    detail,
                    streams,
                    include_1hz_preview=False,
                )
                canonical = build_canonical_model_input(
                    interval_result,
                    activity_date=activity_day,
                    prior_baseline_effective=baseline_history,
                    prior_experimental_effective=experimental_history,
                )
                _analysis, comparison, status = run_physiological_models(
                    canonical,
                    profile=profile,
                    experimental_configuration=experimental_configuration,
                )
                if comparison is None or status["status"] == "not_run":
                    skipped_activities += 1
                    continue
                baseline_values = _history_rows_from_result(comparison.get("baseline"))
                experimental_values = _history_rows_from_result(
                    comparison.get("experimental")
                )
                if not baseline_values or not experimental_values:
                    skipped_activities += 1
                    continue
                for zone, value in baseline_values.items():
                    baseline_day[zone] += value
                for zone, value in experimental_values.items():
                    experimental_day[zone] += value
                processed_activities += 1
                day_has_result = True
            except Exception:
                # Fail closed: no provider payload, identifier, or exception text
                # is retained in diagnostics.
                skipped_activities += 1
        if day_has_result:
            baseline_history.append(
                {"date": activity_day.isoformat(), **dict(baseline_day)}
            )
            experimental_history.append(
                {"date": activity_day.isoformat(), **dict(experimental_day)}
            )

    diagnostics.update(
        {
            "available_days": len(baseline_history),
            "available_activities": processed_activities,
            "skipped_activities": skipped_activities,
            "period_start": (
                baseline_history[0]["date"] if baseline_history else None
            ),
            "period_end": (
                baseline_history[-1]["date"] if baseline_history else None
            ),
            "limited_by_available_data": len(baseline_history) < HISTORY_WINDOW_DAYS,
        }
    )
    if skipped_activities:
        diagnostics["warnings"].append(
            f"Пропуснати исторически активности без използваем HR резултат: {skipped_activities}."
        )
    if len(baseline_history) < HISTORY_WINDOW_DAYS:
        diagnostics["warnings"].append(
            f"Използвани са {len(baseline_history)}/{HISTORY_WINDOW_DAYS} реално налични предходни дни."
        )
    return baseline_history, experimental_history, diagnostics


def run_activity_pipeline(
    client: ActivityClient,
    activity_id: str,
    *,
    include_1hz_preview: bool = False,
    profile: OnFlowsZoneProfile | None = None,
    experimental_configuration: ShadowModelConfiguration | None = None,
    load_history: bool = True,
) -> dict[str, Any]:
    """Load and process a selected activity through the complete read-only path."""

    if not _SAFE_ACTIVITY_ID.fullmatch(str(activity_id)):
        raise ValueError("invalid activity identifier")
    selected_profile = profile or default_onflows_zone_profile()
    configuration = experimental_configuration or configuration_from_profile(
        selected_profile
    )
    detail_payload = _response_payload(
        client.get_activity_result(activity_id, include_intervals=False)
    )
    streams_payload = _response_payload(client.get_streams_result(activity_id))
    if not isinstance(detail_payload, Mapping):
        raise TypeError("activity detail is not a mapping")

    if load_history:
        baseline_history, experimental_history, history = _load_history(
            client,
            selected_date=_activity_date(detail_payload),
            profile=selected_profile,
            experimental_configuration=configuration,
        )
    else:
        baseline_history, experimental_history = [], []
        history = {
            "window_days": HISTORY_WINDOW_DAYS,
            "available_days": 0,
            "available_activities": 0,
            "skipped_activities": 0,
            "period_start": None,
            "period_end": None,
            "current_day_excluded": True,
            "limited_by_available_data": True,
            "warnings": ["Историческото зареждане е изключено за тази проверка."],
        }

    summary = process_activity_payloads(
        detail_payload,
        streams_payload,
        include_1hz_preview=include_1hz_preview,
        profile=selected_profile,
        experimental_configuration=configuration,
        prior_baseline_effective=baseline_history,
        prior_experimental_effective=experimental_history,
    )
    summary["history"] = history
    return summary


__all__ = [
    "CANONICAL_INPUT_VERSION",
    "CanonicalModelInput",
    "build_canonical_model_input",
    "process_activity_payloads",
    "run_activity_pipeline",
    "run_physiological_models",
]
