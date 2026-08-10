"""Session-safe application dataset built from normalized Intervals activities.

This module is deliberately independent of Streamlit.  It coordinates the
existing read-only API client boundary, quality/normalization/canonical bridge,
the existing onFlows shadow physiology, and the existing main recovery/7-40
functions.  It does not contain physiological formulas and never retains raw
streams, OAuth values, provider identifiers, names, or GPS data.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
import hashlib
import hmac
import json
import math
import re
from typing import Any

import pandas as pd

from biathlon import __version__ as MAIN_MODEL_VERSION
from biathlon.constants import AEROBIC_COMPONENTS, COMPONENTS
from biathlon.physiology import (
    compute_load_statistics,
    compute_readiness_history,
    current_readiness,
    rolling_load_statistics,
)
from intervals_inspector.intervals_client import IntervalsResponse
from intervals_inspector.effective_hr import EFFECTIVE_HR_ADAPTER_VERSION
from intervals_inspector.onflows_intrazone_load import (
    ALGORITHM_VERSION as EQUIVALENT_TIME_ALGORITHM_VERSION,
)
from intervals_inspector.onflows_zone_profile import (
    INTRA_ZONE_EQUIVALENCE_VERSION,
)
from intervals_inspector.pipeline import process_activity_payloads
from intervals_inspector.shadow_model import (
    HISTORY_WINDOW_DAYS,
    SHADOW_MODEL_VERSION,
    TREF_BOUNDS_PROFILE_VERSION,
    ShadowModelConfiguration,
    calculate_shadow_result,
    default_shadow_configuration,
    profile_from_configuration,
)
from intervals_inspector.stream_normalizer import (
    ALGORITHM_VERSION as NORMALIZATION_VERSION,
)


REAL_DATA_SOURCE = "intervals"
DEMO_DATA_SOURCE = "demo"
DATA_SOURCE_VALUES = (DEMO_DATA_SOURCE, REAL_DATA_SOURCE)
REAL_HISTORY_SCHEMA_VERSION = "onflows-real-history-dataset-v3-equivalent-time"
RECOVERY_MODEL_VERSION = (
    f"main-load-recovery-v{MAIN_MODEL_VERSION}-equivalent-time-fixed-tref"
)
DEFAULT_HISTORY_DAYS = 90
MIN_HISTORY_DAYS = 41
MAX_HISTORY_DAYS = 180
_SAFE_ACTIVITY_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")


@dataclass(frozen=True)
class RealHistoryDataset:
    """Aggregate-only dataset shared by the load and recovery UI adapters."""

    schema_version: str
    cache_key: str
    source: str
    period_start: str
    period_end: str
    loaded_at_utc: str
    processed_activities: int
    limited_activities: int
    excluded_activities: int
    no_activity_days: int
    normalization_version: str
    equivalent_time_algorithm_version: str
    equivalence_version: str
    effective_hr_adapter_version: str
    zone_profile_fingerprint: str
    configuration_fingerprint: str
    model_version: str
    tref_bounds_profile_version: str
    profile_level: str
    recovery_model_version: str
    parameter_fingerprint: str
    activities: pd.DataFrame
    activity_zones: pd.DataFrame
    daily_zones: pd.DataFrame
    daily_loads: pd.DataFrame
    load_stats: pd.DataFrame
    rolling_load: pd.DataFrame
    readiness_history: pd.DataFrame
    load_readiness: pd.DataFrame
    warnings: tuple[str, ...]

    @property
    def modeled_activity_count(self) -> int:
        return self.processed_activities - self.excluded_activities


def validate_data_source(value: Any) -> str:
    """Validate an explicit source; never silently fall back to demo data."""

    rendered = str(value)
    if rendered not in DATA_SOURCE_VALUES:
        raise ValueError("unknown data source")
    return rendered


def _finite_non_negative(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0.0
    rendered = float(value)
    return rendered if math.isfinite(rendered) and rendered >= 0.0 else 0.0


def _parameter_payload(parameters: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "short_window_days": int(parameters["short_window_days"]),
        "long_window_days": int(parameters["long_window_days"]),
        "base_window_days": int(parameters["base_window_days"]),
        "practical_full_recovery": float(parameters["practical_full_recovery"]),
        "base_loads": {
            component: float(parameters["base_loads"][component])
            for component in COMPONENTS
        },
        "recovery": {
            component: {
                key: float(parameters["recovery"][component][key])
                for key in ("sensitivity", "tau_days", "fmax")
            }
            for component in COMPONENTS
        },
    }


def recovery_parameter_fingerprint(parameters: Mapping[str, Any]) -> str:
    payload = json.dumps(
        _parameter_payload(parameters),
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def build_history_cache_key(
    *,
    profile_identifier: str,
    session_salt: str,
    period_start: date,
    period_end: date,
    configuration: ShadowModelConfiguration,
    parameter_fingerprint: str,
) -> str:
    """Build a cache key that accounts for profile and model versions safely."""

    if not profile_identifier or not session_salt:
        raise ValueError("profile identifier and session salt are required")
    profile_digest = hmac.new(
        session_salt.encode("utf-8"),
        profile_identifier.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    profile = profile_from_configuration(configuration)
    payload = {
        "dataset_schema_version": REAL_HISTORY_SCHEMA_VERSION,
        "profile_digest": profile_digest,
        "period_start": period_start.isoformat(),
        "period_end": period_end.isoformat(),
        "normalization_version": NORMALIZATION_VERSION,
        "equivalent_time_algorithm_version": (
            EQUIVALENT_TIME_ALGORITHM_VERSION
        ),
        "equivalence_version": INTRA_ZONE_EQUIVALENCE_VERSION,
        "effective_hr_adapter_version": EFFECTIVE_HR_ADAPTER_VERSION,
        "zone_profile_fingerprint": profile.fingerprint,
        "configuration_fingerprint": configuration.fingerprint,
        "model_version": configuration.physiology_profile_version,
        "tref_bounds_profile_version": configuration.tref_bounds_profile_version,
        "recovery_model_version": RECOVERY_MODEL_VERSION,
        "parameter_fingerprint": parameter_fingerprint,
    }
    rendered = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(rendered.encode("utf-8")).hexdigest()


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


def _activity_id(value: Mapping[str, Any]) -> str | None:
    rendered = str(value.get("id") or "").strip()
    return rendered if _SAFE_ACTIVITY_ID.fullmatch(rendered) else None


def _empty_intrazone_analysis(
    configuration: ShadowModelConfiguration,
) -> dict[str, Any]:
    return {
        "hr_coverage_percent": 0.0,
        "zones": [
            {
                "zone": zone.zone,
                "real_seconds": 0.0,
                "equivalent_seconds": 0.0,
                "mean_effective_hr_bpm": None,
                "average_minute_value_percent": None,
            }
            for zone in configuration.zones
        ],
    }


def _model_rows(result: Mapping[str, Any] | None) -> list[Mapping[str, Any]]:
    if not isinstance(result, Mapping):
        return []
    rows = result.get("rows")
    if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes, bytearray)):
        return []
    return [row for row in rows if isinstance(row, Mapping)]


def _safe_sport(metadata: Mapping[str, Any]) -> str:
    value = metadata.get("sport")
    rendered = re.sub(
        r"[^\w /+.-]", "", str(value or "Активност"), flags=re.UNICODE
    ).strip()
    return rendered[:40] or "Активност"


def _activity_columns() -> list[str]:
    return [
        "activity_ref",
        "date",
        "sport",
        "duration_min",
        "quality_status",
        "status_reason",
        "hr_coverage_percent",
        "normalization_version",
        "model_version",
        "tref_bounds_profile_version",
    ]


def _zone_columns(*, daily: bool = False) -> list[str]:
    prefix = ["date", "zone"] if daily else ["activity_ref", "date", "zone"]
    return [
        *prefix,
        "T_z",
        "T_eq_z",
        "mean_effective_hr_bpm",
        "average_minute_value_percent",
        "direct_ratio",
        "cascade",
        "spillover",
        "E_z",
        "tref_raw",
        "tref_effective",
        "tref_history_value",
        "tref_source",
        "tref_history_days",
        "quality_status",
    ]


def _validated_period(days: int, period_end: date) -> tuple[date, date]:
    if isinstance(days, bool) or not MIN_HISTORY_DAYS <= int(days) <= MAX_HISTORY_DAYS:
        raise ValueError(
            f"history days must be between {MIN_HISTORY_DAYS} and {MAX_HISTORY_DAYS}"
        )
    return period_end - timedelta(days=int(days) - 1), period_end


def load_real_history(
    client: Any,
    *,
    profile_identifier: str,
    session_salt: str,
    parameters: Mapping[str, Any],
    period_end: date | None = None,
    days: int = DEFAULT_HISTORY_DAYS,
    configuration: ShadowModelConfiguration | None = None,
    loaded_at_utc: datetime | None = None,
) -> RealHistoryDataset:
    """Load and process one bounded real history in chronological order."""

    selected_configuration = configuration or default_shadow_configuration()
    profile = profile_from_configuration(selected_configuration)
    end = period_end or date.today()
    start, end = _validated_period(days, end)
    parameter_fingerprint = recovery_parameter_fingerprint(parameters)
    cache_key = build_history_cache_key(
        profile_identifier=profile_identifier,
        session_salt=session_salt,
        period_start=start,
        period_end=end,
        configuration=selected_configuration,
        parameter_fingerprint=parameter_fingerprint,
    )

    listed_payload = _response_payload(
        client.get_activities_result(start.isoformat(), end.isoformat())
    )
    listed = (
        listed_payload
        if isinstance(listed_payload, Sequence)
        and not isinstance(listed_payload, (str, bytes, bytearray))
        else []
    )
    grouped: dict[date, list[str]] = defaultdict(list)
    invalid_list_entries = 0
    for item in listed:
        if not isinstance(item, Mapping):
            invalid_list_entries += 1
            continue
        activity_day = _activity_date(item)
        provider_id = _activity_id(item)
        if activity_day is None or provider_id is None or not start <= activity_day <= end:
            invalid_list_entries += 1
            continue
        grouped[activity_day].append(provider_id)

    activity_records: list[dict[str, Any]] = []
    activity_zone_records: list[dict[str, Any]] = []
    daily_zone_records: list[dict[str, Any]] = []
    daily_load_records: list[dict[str, Any]] = []
    # These histories contain downstream E, so H40 is exactly 7 × E40 and
    # matches the denominator used by the 7/40 model. The public T_eq columns
    # remain the one direct dose from which E is derived.
    prior_baseline_effective_load: list[dict[str, Any]] = []
    prior_experimental_effective_load: list[dict[str, Any]] = []
    warnings: list[str] = []
    processed = 0
    limited = 0
    excluded = 0
    no_activity_days = 0
    activity_ordinal = 0

    for current_day in pd.date_range(start, end, freq="D").date:
        day_provider_ids = sorted(set(grouped.get(current_day, [])))
        if not day_provider_ids:
            no_activity_days += 1
        day_totals = {
            zone.zone: {
                "T_z": 0.0,
                "T_eq_z": 0.0,
                "effective_hr_bpm_minutes": 0.0,
                "cascade": 0.0,
                "spillover": 0.0,
                "E_z": 0.0,
            }
            for zone in selected_configuration.zones
        }
        day_baseline_effective_load = {
            zone.zone: 0.0 for zone in selected_configuration.zones
        }
        day_reference_rows: list[Mapping[str, Any]] | None = None
        day_statuses: list[str] = []

        for provider_id in day_provider_ids:
            activity_ordinal += 1
            activity_ref = f"activity-{activity_ordinal:03d}"
            try:
                detail = _response_payload(
                    client.get_activity_result(provider_id, include_intervals=False)
                )
                streams = _response_payload(client.get_streams_result(provider_id))
                if not isinstance(detail, Mapping):
                    raise TypeError("activity detail is not a mapping")
                if _activity_date(detail) != current_day:
                    raise ValueError("activity date does not match the history day")
                summary = process_activity_payloads(
                    detail,
                    streams,
                    include_1hz_preview=False,
                    profile=profile,
                    experimental_configuration=selected_configuration,
                    prior_baseline_effective_load=(
                        prior_baseline_effective_load
                    ),
                    prior_experimental_effective_load=(
                        prior_experimental_effective_load
                    ),
                )
            except Exception:
                processed += 1
                excluded += 1
                day_statuses.append("excluded")
                activity_records.append(
                    {
                        "activity_ref": activity_ref,
                        "date": pd.Timestamp(current_day),
                        "sport": "Активност",
                        "duration_min": None,
                        "quality_status": "excluded",
                        "status_reason": "API или обработката не завърши безопасно.",
                        "hr_coverage_percent": 0.0,
                        "normalization_version": NORMALIZATION_VERSION,
                        "model_version": SHADOW_MODEL_VERSION,
                        "tref_bounds_profile_version": TREF_BOUNDS_PROFILE_VERSION,
                    }
                )
                continue

            processed += 1
            metadata = summary.get("activity_metadata", {})
            model_status = summary.get("model_status", {})
            status = str(model_status.get("status") or "not_run")
            if status == "not_run":
                status = "excluded"
                excluded += 1
            elif status == "limited":
                limited += 1
            elif status != "valid":
                status = "excluded"
                excluded += 1
            day_statuses.append(status)
            onflows = summary.get("onflows_load_analysis", {})
            coverage = _finite_non_negative(
                onflows.get("hr_coverage_percent")
                if isinstance(onflows, Mapping)
                else 0.0
            )
            duration_seconds = (
                metadata.get("moving_time_sec")
                or metadata.get("recording_time_sec")
                or metadata.get("elapsed_time_sec")
                if isinstance(metadata, Mapping)
                else None
            )
            activity_records.append(
                {
                    "activity_ref": activity_ref,
                    "date": pd.Timestamp(current_day),
                    "sport": _safe_sport(metadata if isinstance(metadata, Mapping) else {}),
                    "duration_min": (
                        _finite_non_negative(duration_seconds) / 60.0
                        if duration_seconds is not None
                        else None
                    ),
                    "quality_status": status,
                    "status_reason": str(model_status.get("reason") or ""),
                    "hr_coverage_percent": coverage,
                    "normalization_version": NORMALIZATION_VERSION,
                    "model_version": selected_configuration.physiology_profile_version,
                    "tref_bounds_profile_version": (
                        selected_configuration.tref_bounds_profile_version
                    ),
                }
            )
            comparison = summary.get("shadow_model_comparison")
            baseline_rows = _model_rows(
                comparison.get("baseline") if isinstance(comparison, Mapping) else None
            )
            rows = _model_rows(
                comparison.get("experimental")
                if isinstance(comparison, Mapping)
                else None
            )
            if status == "excluded" or not rows:
                continue
            for baseline_row in baseline_rows:
                baseline_zone = str(baseline_row.get("zone") or "")
                if baseline_zone in day_baseline_effective_load:
                    day_baseline_effective_load[
                        baseline_zone
                    ] += _finite_non_negative(
                        baseline_row.get("E_z")
                    )
            if day_reference_rows is None:
                day_reference_rows = rows
            for row in rows:
                zone = str(row.get("zone") or "")
                if zone not in day_totals:
                    continue
                rendered = {
                    "activity_ref": activity_ref,
                    "date": pd.Timestamp(current_day),
                    "zone": zone,
                    "T_z": _finite_non_negative(row.get("T_z")),
                    "T_eq_z": _finite_non_negative(row.get("T_eq_z")),
                    "mean_effective_hr_bpm": (
                        _finite_non_negative(row.get("mean_effective_hr_bpm"))
                        if row.get("mean_effective_hr_bpm") is not None
                        else None
                    ),
                    "average_minute_value_percent": (
                        _finite_non_negative(
                            row.get("average_minute_value_percent")
                        )
                        if row.get("average_minute_value_percent") is not None
                        else None
                    ),
                    "direct_ratio": _finite_non_negative(
                        row.get("direct_ratio")
                    ),
                    "cascade": _finite_non_negative(row.get("cascade")),
                    "spillover": _finite_non_negative(row.get("spillover_received")),
                    "E_z": _finite_non_negative(row.get("E_z")),
                    "tref_raw": _finite_non_negative(row.get("tref_raw")),
                    "tref_effective": _finite_non_negative(
                        row.get("tref_effective")
                    ),
                    "tref_history_value": (
                        _finite_non_negative(row.get("tref_history_value"))
                        if row.get("tref_history_value") is not None
                        else None
                    ),
                    "tref_source": str(
                        row.get("tref_source")
                        or "initial expert setting"
                    ),
                    "tref_history_days": int(
                        _finite_non_negative(row.get("tref_history_days"))
                    ),
                    "quality_status": status,
                }
                activity_zone_records.append(rendered)
                for field in (
                    "T_z",
                    "T_eq_z",
                    "cascade",
                    "spillover",
                    "E_z",
                ):
                    day_totals[zone][field] += float(rendered[field])
                if rendered["mean_effective_hr_bpm"] is not None:
                    day_totals[zone][
                        "effective_hr_bpm_minutes"
                    ] += float(rendered["mean_effective_hr_bpm"]) * float(
                        rendered["T_z"]
                    )

        if day_reference_rows is None:
            day_reference_rows = _model_rows(
                calculate_shadow_result(
                    _empty_intrazone_analysis(selected_configuration),
                    selected_configuration,
                    prior_daily_effective_load=(
                        prior_experimental_effective_load
                    ),
                    activity_date=current_day,
                )
            )
        reference_by_zone = {
            str(row.get("zone")): row for row in day_reference_rows
        }
        if not day_statuses:
            day_status = "no_activity"
        elif "limited" in day_statuses or (
            "valid" in day_statuses and "excluded" in day_statuses
        ):
            day_status = "limited"
        elif "valid" in day_statuses:
            day_status = "valid"
        else:
            day_status = "excluded"

        baseline_history_row: dict[str, Any] = {
            "date": current_day.isoformat()
        }
        experimental_history_row: dict[str, Any] = {
            "date": current_day.isoformat()
        }
        load_row: dict[str, Any] = {"date": pd.Timestamp(current_day)}
        for zone in AEROBIC_COMPONENTS:
            totals = day_totals[zone]
            reference = reference_by_zone.get(zone, {})
            mean_effective_hr_bpm = (
                totals["effective_hr_bpm_minutes"] / totals["T_z"]
                if totals["T_z"] > 0.0
                else None
            )
            average_minute_value_percent = (
                100.0 * totals["T_eq_z"] / totals["T_z"]
                if totals["T_z"] > 0.0
                else None
            )
            baseline_history_row[zone] = day_baseline_effective_load[zone]
            experimental_history_row[zone] = totals["E_z"]
            # ``q_*`` is a deprecated main-core input name. Its single value
            # is exactly T_eq; no legacy Q calculation is retained.
            load_row[f"q_{zone}"] = totals["T_eq_z"]
            load_row[f"e_{zone}"] = totals["E_z"]
            load_row[f"tref_used_{zone}"] = _finite_non_negative(
                reference.get("tref_effective")
            )
            daily_zone_records.append(
                {
                    "date": pd.Timestamp(current_day),
                    "zone": zone,
                    "T_z": totals["T_z"],
                    "T_eq_z": totals["T_eq_z"],
                    "mean_effective_hr_bpm": mean_effective_hr_bpm,
                    "average_minute_value_percent": (
                        average_minute_value_percent
                    ),
                    "cascade": totals["cascade"],
                    "spillover": totals["spillover"],
                    "E_z": totals["E_z"],
                    "direct_ratio": (
                        totals["T_eq_z"]
                        / max(_finite_non_negative(reference.get("tref_effective")), 1e-12)
                    ),
                    "tref_raw": _finite_non_negative(reference.get("tref_raw")),
                    "tref_effective": _finite_non_negative(
                        reference.get("tref_effective")
                    ),
                    "tref_history_value": (
                        _finite_non_negative(reference.get("tref_history_value"))
                        if reference.get("tref_history_value") is not None
                        else None
                    ),
                    "tref_source": str(
                        reference.get("tref_source")
                        or "initial expert setting"
                    ),
                    "tref_history_days": int(
                        _finite_non_negative(reference.get("tref_history_days"))
                    ),
                    "quality_status": day_status,
                }
            )
        daily_load_records.append(load_row)
        prior_baseline_effective_load.append(baseline_history_row)
        prior_experimental_effective_load.append(experimental_history_row)

    activities_frame = pd.DataFrame(activity_records, columns=_activity_columns())
    activity_zones_frame = pd.DataFrame(
        activity_zone_records, columns=_zone_columns()
    )
    daily_zones_frame = pd.DataFrame(
        daily_zone_records, columns=_zone_columns(daily=True)
    )
    daily_loads = pd.DataFrame(daily_load_records).set_index("date").sort_index()
    # The unchanged main functions currently require every main component.
    # Add STR only to this transient adapter input, then remove it from every
    # published result: HR history provides no real strength observation.
    main_model_input = daily_loads.copy()
    main_model_input["q_STR"] = 0.0
    main_model_input["e_STR"] = 0.0
    main_model_input["tref_used_STR"] = 7.0 * float(
        parameters["base_loads"]["STR"]
    )
    load_stats = compute_load_statistics(
        main_model_input, dict(parameters), as_of=end
    ).loc[list(AEROBIC_COMPONENTS)]
    latest_tref = (
        daily_zones_frame.loc[
            daily_zones_frame["date"] == pd.Timestamp(end)
        ]
        .set_index("zone")
    )
    load_stats["Tref"] = latest_tref["tref_effective"].reindex(
        load_stats.index
    )
    load_stats["reliability"] = (
        latest_tref["tref_history_days"].reindex(load_stats.index).astype(float)
        / float(HISTORY_WINDOW_DAYS)
    ).clip(upper=1.0)
    rolling_load = rolling_load_statistics(
        main_model_input, dict(parameters)
    )
    rolling_load = rolling_load.loc[
        rolling_load["component"].isin(AEROBIC_COMPONENTS)
    ].reset_index(drop=True)
    rolling_tref = daily_zones_frame[
        ["date", "zone", "tref_effective"]
    ].rename(columns={"zone": "component", "tref_effective": "fixed_Tref"})
    rolling_load = rolling_load.merge(
        rolling_tref,
        on=["date", "component"],
        how="left",
        validate="one_to_one",
    )
    rolling_load["Tref"] = rolling_load.pop("fixed_Tref")
    readiness_history = compute_readiness_history(
        main_model_input,
        dict(parameters),
        use_supplied_tref=True,
    )
    readiness_history = readiness_history.loc[
        readiness_history["component"].isin(AEROBIC_COMPONENTS)
    ].reset_index(drop=True)
    load_readiness = current_readiness(
        readiness_history,
        dict(parameters),
        target_date=end,
    ).loc[list(AEROBIC_COMPONENTS)]

    if invalid_list_entries:
        warnings.append(
            f"Пропуснати невалидни записи от списъка с активности: {invalid_list_entries}."
        )
    if excluded:
        warnings.append(
            f"Активности без използваем моделeн HR резултат: {excluded}."
        )
    if limited:
        warnings.append(f"Активности с ограничено HR покритие: {limited}.")
    if int(days) < 2 * HISTORY_WINDOW_DAYS:
        warnings.append(
            "Периодът е кратък за стабилно 40-дневно загряване на всички исторически дни."
        )

    loaded_at = loaded_at_utc or datetime.now(timezone.utc)
    if loaded_at.tzinfo is None:
        loaded_at = loaded_at.replace(tzinfo=timezone.utc)
    return RealHistoryDataset(
        schema_version=REAL_HISTORY_SCHEMA_VERSION,
        cache_key=cache_key,
        source=REAL_DATA_SOURCE,
        period_start=start.isoformat(),
        period_end=end.isoformat(),
        loaded_at_utc=loaded_at.astimezone(timezone.utc).isoformat(),
        processed_activities=processed,
        limited_activities=limited,
        excluded_activities=excluded,
        no_activity_days=no_activity_days,
        normalization_version=NORMALIZATION_VERSION,
        equivalent_time_algorithm_version=(
            EQUIVALENT_TIME_ALGORITHM_VERSION
        ),
        equivalence_version=INTRA_ZONE_EQUIVALENCE_VERSION,
        effective_hr_adapter_version=EFFECTIVE_HR_ADAPTER_VERSION,
        zone_profile_fingerprint=profile.fingerprint,
        configuration_fingerprint=selected_configuration.fingerprint,
        model_version=selected_configuration.physiology_profile_version,
        tref_bounds_profile_version=(
            selected_configuration.tref_bounds_profile_version
        ),
        profile_level=selected_configuration.profile_level,
        recovery_model_version=RECOVERY_MODEL_VERSION,
        parameter_fingerprint=parameter_fingerprint,
        activities=activities_frame,
        activity_zones=activity_zones_frame,
        daily_zones=daily_zones_frame,
        daily_loads=daily_loads,
        load_stats=load_stats,
        rolling_load=rolling_load,
        readiness_history=readiness_history,
        load_readiness=load_readiness,
        warnings=tuple(warnings),
    )


def resolve_real_dataset(
    value: Any,
    *,
    expected_cache_key: str,
) -> RealHistoryDataset:
    """Validate cached session data without falling back to demo analysis."""

    if not isinstance(value, RealHistoryDataset):
        raise ValueError("real history is not loaded")
    if value.source != REAL_DATA_SOURCE:
        raise ValueError("cached dataset has the wrong source")
    if not hmac.compare_digest(value.cache_key, expected_cache_key):
        raise ValueError("cached real history is stale")
    return value


def build_real_load_view(dataset: RealHistoryDataset) -> dict[str, Any]:
    if dataset.source != REAL_DATA_SOURCE:
        raise ValueError("load view requires a real Intervals dataset")
    return {
        "load_stats": dataset.load_stats,
        "rolling_load": dataset.rolling_load,
        "activities": dataset.activities,
        "activity_zones": dataset.activity_zones,
        "daily_zones": dataset.daily_zones,
        "load_readiness": dataset.load_readiness,
    }


def build_real_recovery_view(dataset: RealHistoryDataset) -> dict[str, Any]:
    if dataset.source != REAL_DATA_SOURCE:
        raise ValueError("recovery view requires a real Intervals dataset")
    return {
        "readiness_history": dataset.readiness_history,
        "load_readiness": dataset.load_readiness,
        "daily_zones": dataset.daily_zones,
    }


__all__ = [
    "DATA_SOURCE_VALUES",
    "DEFAULT_HISTORY_DAYS",
    "DEMO_DATA_SOURCE",
    "REAL_DATA_SOURCE",
    "RealHistoryDataset",
    "build_history_cache_key",
    "build_real_load_view",
    "build_real_recovery_view",
    "load_real_history",
    "recovery_parameter_fingerprint",
    "resolve_real_dataset",
    "validate_data_source",
]
