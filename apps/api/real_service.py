"""Read-only Intervals-to-canonical orchestration for the single-profile pilot.

Provider credentials stop at :func:`refresh`; downstream code receives an
``AthleteContext`` and the privacy-minimized ``RealHistoryDataset`` only.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import logging
import os
from typing import Any, Mapping

import pandas as pd

from biathlon.constants import fresh_parameters
from biathlon.effective_hr import EFFECTIVE_HR_SOURCE
from biathlon.physiology import compute_readiness_history, current_readiness

from .cloud import (
    CANONICAL_TREF_PROFILE_VERSION,
    AthleteContext,
    AthleteModelSettings,
    SnapshotRepository,
    daily_wellness_summaries,
    normalize_wellness,
    summarize_wellness_coverage,
    wellness_rows_from_payload,
)
from .activity_catalog import extract_activity_metadata, provider_activity_key
from .schemas import (
    ActivityZoneLoad,
    AthleteSnapshot,
    CompletedWorkMetadata,
    CompletedWorkQuality,
    CompletedWorkResponse,
    CompletedWorkSport,
    CompletedWorkTotals,
    CompletedWorkZone,
    DailyStrengthLoad,
    DailyStrengthRecovery,
    DailyZoneLoad,
    DataQuality,
    DailyRecovery,
    LoadHistoryActivity,
    LoadHistoryQuality,
    LoadHistoryResponse,
    ModelMetadata,
    RecoveryHistoryResponse,
    RecoveryModelMetadata,
    RecoveryStrengthCurrent,
    RecoveryStrengthHistory,
    RecoveryStrengthSettings,
    RecoveryZoneCurrent,
    RecoveryZoneSettings,
    TrainingStatusResponse,
    StrengthLoadHistory,
    StrengthLoadModel,
    StrengthLoadSummary,
    VolumeHistoryMetadata,
    VolumeHistoryQuality,
    VolumeHistoryResponse,
    WellnessCoverageDiagnostics,
    WeeklyVolume,
    ZoneLoadSummary,
    ZoneTrainingStatus,
)

ZONES = ("Z1", "Z2", "Z3", "Z4", "Z5")
PERCENTAGE_TOLERANCE = 1e-6
logger = logging.getLogger(__name__)


class ConfigurationError(RuntimeError):
    """A safe startup/refresh configuration failure."""


class RecoverySourceRefreshRequired(ConfigurationError):
    """Recovery cannot be reproduced until a new full analysis is stored."""


class ProviderFailure(RuntimeError):
    """A sanitized provider failure with no response payload or identity."""


@dataclass(frozen=True)
class RefreshResult:
    snapshot: TrainingStatusResponse
    processed_activities: int
    wellness_coverage: float
    wellness_records_received: int
    wellness_days_stored: int


@dataclass(frozen=True)
class WellnessRefreshResult:
    records_received: int
    days_stored: int


def refresh_wellness_calendar(
    repository: SnapshotRepository,
    *,
    access_token: str,
    provider_athlete_id: str,
    athlete_alias: str,
    environ: Mapping[str, str] | None = None,
    client: Any | None = None,
    period_end: date | None = None,
    now: datetime | None = None,
) -> WellnessRefreshResult:
    """Refresh only display-safe wellness aggregates, never activity models."""
    env = environ or os.environ
    token = access_token.strip() if isinstance(access_token, str) else ""
    provider_id = (
        provider_athlete_id.strip()
        if isinstance(provider_athlete_id, str)
        else ""
    )
    alias = athlete_alias.strip() if isinstance(athlete_alias, str) else ""
    if not token or not provider_id or not alias:
        raise ConfigurationError("Provider credentials or athlete alias are unavailable")
    try:
        days = int(env.get("ONFLOWS_HISTORY_DAYS", "90"))
        if not 41 <= days <= 90:
            raise ValueError
    except ValueError as exc:
        raise ConfigurationError("History period must be between 41 and 90 days") from exc
    existing = repository.latest(alias)
    if not isinstance(existing, Mapping):
        raise ConfigurationError("Training snapshot requires a full real-data refresh")

    from intervals_inspector.intervals_client import IntervalsAPIError, IntervalsClient

    provider = client or IntervalsClient(token, provider_id)
    end = period_end or date.today()
    start = end.fromordinal(end.toordinal() - days + 1)
    try:
        payload = provider.get_wellness_result(
            start.isoformat(), end.isoformat()
        ).payload
        rows = wellness_rows_from_payload(payload)
        diagnostics = summarize_wellness_coverage(
            rows, period_start=start, period_end=end, now=now
        )
        calendar = daily_wellness_summaries(
            rows, period_start=start, period_end=end, now=now
        )
    except IntervalsAPIError as exc:
        logger.warning(
            "wellness_refresh_failed provider_status=%s",
            exc.status_code if exc.status_code is not None else "network",
        )
        raise ProviderFailure("Intervals wellness request failed") from exc
    except (TypeError, ValueError) as exc:
        logger.warning("wellness_refresh_failed error_type=%s", type(exc).__name__)
        raise ProviderFailure("Wellness data could not be normalized safely") from exc

    # The provider request can overlap a full canonical refresh. Re-read the
    # current snapshot immediately before patching so a wellness-only request
    # cannot restore the stale pre-refresh envelope and erase recovery_history.
    current = repository.latest(alias)
    if not isinstance(current, Mapping):
        raise ConfigurationError("Training snapshot requires a full real-data refresh")
    updated = dict(current)
    updated["wellness_calendar"] = calendar
    recovery = updated.get("recovery_history")
    if isinstance(recovery, Mapping):
        recovery_update = dict(recovery)
        try:
            recovery_start = date.fromisoformat(str(recovery["period_start"]))
            recovery_end = date.fromisoformat(str(recovery["period_end"]))
        except (KeyError, TypeError, ValueError):
            # Preserve an older recovery payload rather than attaching
            # diagnostics for an incompatible calendar period.
            pass
        else:
            recovery_rows = [
                row for row in rows
                if recovery_start.isoformat()
                <= str(row.get("id") or row.get("date") or "")[:10]
                <= recovery_end.isoformat()
            ]
            recovery_latest = max(
                recovery_rows,
                key=lambda row: str(row.get("id") or row.get("date") or ""),
                default={},
            )
            recovery_wellness = normalize_wellness(recovery_latest, now=now)
            recovery_update["wellness_freshness"] = recovery_wellness["freshness"]
            recovery_update["wellness_coverage_percent"] = round(
                100.0 * float(recovery_wellness["coverage"]), 6
            )
            recovery_update["wellness_diagnostics"] = summarize_wellness_coverage(
                recovery_rows,
                period_start=recovery_start,
                period_end=recovery_end,
                now=now,
            )
        updated["recovery_history"] = recovery_update
    repository.replace(alias, updated)
    return WellnessRefreshResult(
        records_received=int(diagnostics["records_received"]),
        days_stored=len(calendar),
    )


def context_from_environment(
    environ: Mapping[str, str] | None = None,
    *,
    provider_athlete_id: str | None = None,
    athlete_alias: str | None = None,
    athlete_settings: AthleteModelSettings | None = None,
) -> AthleteContext:
    env = environ or os.environ
    from intervals_inspector.shadow_model import TREF_PROFILE_VERSION

    configured_tref_version = env.get("ONFLOWS_TREF_VERSION", "").strip()
    if configured_tref_version != TREF_PROFILE_VERSION:
        raise ConfigurationError("Configured Tref profile version is unsupported")
    resolved_provider_id = (
        provider_athlete_id or env.get("INTERVALS_ATHLETE_ID", "")
    ).strip()
    if not resolved_provider_id:
        raise ConfigurationError("Pilot athlete configuration is incomplete")
    configured_alias = env.get("ONFLOWS_ATHLETE_ALIAS", "").strip()
    resolved_alias = athlete_alias or configured_alias
    if not resolved_alias:
        raise ConfigurationError("Pilot athlete configuration is incomplete")
    if athlete_settings is not None:
        try:
            settings = athlete_settings.validate()
        except ValueError as exc:
            raise ConfigurationError("Athlete physiological configuration is invalid") from exc
        bounds = settings.zone_bounds_bpm
        athlete_timezone = settings.timezone
        explicit_hrmax = settings.hrmax_bpm
    else:
        if resolved_alias != configured_alias:
            raise ConfigurationError(
                "Athlete-specific physiological configuration is required"
            )
        try:
            bounds = tuple(int(item.strip()) for item in env["ONFLOWS_HR_ZONE_BOUNDS"].split(","))
        except (KeyError, TypeError, ValueError) as exc:
            raise ConfigurationError("Pilot HR zone configuration is invalid") from exc
        athlete_timezone = env.get("ONFLOWS_ATHLETE_TIMEZONE", "").strip()
        raw_hrmax = env.get("ONFLOWS_HRMAX_BPM", "").strip()
        try:
            explicit_hrmax = int(raw_hrmax) if raw_hrmax else None
        except ValueError as exc:
            raise ConfigurationError("Pilot explicit HRmax is invalid") from exc
    try:
        return AthleteContext(
            public_alias=resolved_alias,
            provider_athlete_id=resolved_provider_id,
            zone_bounds_bpm=bounds,
            timezone=athlete_timezone,
            intra_zone_version=env.get("ONFLOWS_INTRAZONE_VERSION", "").strip(),
            tref_version=configured_tref_version,
            recovery_parameter_version=env.get("ONFLOWS_RECOVERY_VERSION", "").strip(),
            hrmax_bpm=explicit_hrmax,
        ).validate()
    except ValueError as exc:
        raise ConfigurationError("Pilot athlete configuration is invalid") from exc


def _finite(value: Any, name: str) -> float:
    import math
    rendered = float(value)
    if not math.isfinite(rendered):
        raise ValueError(f"Non-finite canonical result: {name}")
    return rendered


def _positive_finite(value: Any, name: str) -> float:
    rendered = _finite(value, name)
    if rendered <= 0.0:
        raise ValueError(f"Non-positive canonical result: {name}")
    return rendered


def _optional_finite(value: Any, name: str) -> float | None:
    import math

    if value is None:
        return None
    try:
        rendered = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Invalid optional canonical result: {name}") from exc
    return rendered if math.isfinite(rendered) else None


def _bounded_percentage(value: Any, name: str) -> float:
    """Normalize harmless floating-point drift at the HTTP contract boundary."""

    rendered = _finite(value, name)
    if rendered < -PERCENTAGE_TOLERANCE or rendered > 100.0 + PERCENTAGE_TOLERANCE:
        raise ValueError(f"Canonical percentage is outside 0–100: {name}")
    return min(100.0, max(0.0, rendered))


def _calendar_date(value: Any, name: str) -> str:
    rendered = str(value)[:10]
    try:
        date.fromisoformat(rendered)
    except ValueError as exc:
        raise ValueError(f"Invalid canonical date: {name}") from exc
    return rendered


def dataset_to_training_status(
    dataset: Any,
    context: AthleteContext,
    wellness: Mapping[str, Any],
    wellness_diagnostics: Mapping[str, Any],
) -> TrainingStatusResponse:
    """Pure aggregate adapter; it never invokes Intervals or reads credentials."""
    eligible = dataset.activities.loc[dataset.activities["quality_status"].isin(("valid", "limited"))]
    if eligible.empty:
        raise ValueError("No valid activity is available for training status")
    latest = eligible.sort_values(["date", "activity_ref"], kind="stable").iloc[-1]
    rows = dataset.activity_zones.loc[dataset.activity_zones["activity_ref"] == latest["activity_ref"]].set_index("zone")
    if set(rows.index) != set(ZONES):
        raise ValueError("Latest activity has incomplete canonical zones")
    reliability = min(_finite(dataset.load_stats.loc[z, "reliability"], f"reliability[{z}]") for z in ZONES)
    warnings = list(dataset.warnings)
    if wellness.get("freshness") != "fresh":
        warnings.append(
            f"Wellness is {wellness.get('freshness', 'unknown')} and is not integrated into readiness."
        )
    if int(wellness_diagnostics.get("records_received", 0)) == 0:
        warnings.append(
            "No dated wellness records were received for the stored period; readiness remains load-only."
        )
    else:
        warnings.append(
            "Wellness field coverage is diagnostic-only; readiness remains load-only in this model version."
        )
    quality = (
        None
        if _finite(latest.get("strength_time_min", 0.0), "latest strength time") > 0.0
        else _bounded_percentage(
            latest["hr_coverage_percent"], "latest HR coverage"
        )
        / 100.0
    )
    return TrainingStatusResponse(
        schema_version="training-status-v1", as_of=dataset.period_end,
        athlete_id=context.public_alias,
        model=ModelMetadata(algorithm_version=dataset.model_version,
                            effective_hr_version=dataset.effective_hr_adapter_version,
                            effective_hr_source=EFFECTIVE_HR_SOURCE,
                            parameter_version=1),
        data_quality=DataQuality(history_reliability=reliability,
                                 latest_activity_quality_score=quality,
                                 warnings=warnings),
        zones=[ZoneTrainingStatus(
            zone=z, raw_time_min=_finite(rows.loc[z, "T_z"], f"T_z[{z}]"),
            equivalent_time_min=_finite(rows.loc[z, "T_eq_z"], f"T_eq_z[{z}]"),
            tref_min=_finite(dataset.load_stats.loc[z, "Tref"], f"Tref[{z}]"),
            status_7_40=_finite(dataset.load_stats.loc[z, "index_7_40"], f"7/40[{z}]"),
            recovery_readiness_percent=_finite(dataset.load_readiness.loc[z, "readiness"], f"readiness[{z}]"),
            recovery_days_to_full=_finite(dataset.load_readiness.loc[z, "days_to_full"], f"days[{z}]"),
        ) for z in ZONES],
    )


def dataset_to_load_history(
    dataset: Any,
    context: AthleteContext,
) -> LoadHistoryResponse:
    """Publish precomputed aggregates only; raw provider streams remain transient."""

    tref_profile_version = getattr(dataset, "tref_bounds_profile_version", None)
    if not isinstance(tref_profile_version, str) or not tref_profile_version.strip():
        raise ValueError("Canonical Tref profile provenance is incomplete")
    tref_profile_version = tref_profile_version.strip()

    zone_summaries = [
        ZoneLoadSummary(
            zone=zone,
            e7_daily=_finite(dataset.load_stats.loc[zone, "E7_daily"], f"E7[{zone}]"),
            e40_daily=_finite(dataset.load_stats.loc[zone, "E40_daily"], f"E40[{zone}]"),
            status_7_40=_finite(
                dataset.load_stats.loc[zone, "index_7_40"], f"7/40[{zone}]"
            ),
            tref_min=_finite(dataset.load_stats.loc[zone, "Tref"], f"Tref[{zone}]"),
            history_reliability=_finite(
                dataset.load_stats.loc[zone, "reliability"],
                f"reliability[{zone}]",
            ),
        )
        for zone in ZONES
    ]

    daily_rows = dataset.rolling_load.loc[
        dataset.rolling_load["component"].isin(ZONES)
    ].sort_values(["date", "component"], kind="stable")
    daily = [
        DailyZoneLoad(
            date=_calendar_date(row.date, "daily load"),
            zone=str(row.component),
            effective_load=_finite(row.effective, f"effective[{row.component}]"),
            e7_daily=_finite(row.E7_daily, f"E7[{row.component}]"),
            e40_daily=_finite(row.E40_daily, f"E40[{row.component}]"),
            status_7_40=_finite(row.index_7_40, f"7/40[{row.component}]"),
            tref_used_min=_positive_finite(
                row.Tref, f"Tref used[{row.component}]"
            ),
        )
        for row in daily_rows.itertuples(index=False)
    ]

    modeled = dataset.activities.loc[
        dataset.activities["quality_status"].isin(("valid", "limited"))
    ].sort_values(["date", "activity_ref"], ascending=[False, False], kind="stable")
    activities: list[LoadHistoryActivity] = []
    for activity in modeled.itertuples(index=False):
        zone_rows = (
            dataset.activity_zones.loc[
                dataset.activity_zones["activity_ref"] == activity.activity_ref
            ]
            .set_index("zone")
            .reindex(ZONES)
        )
        if zone_rows[list(("T_z", "T_eq_z", "E_z"))].isna().any().any():
            raise ValueError("Modeled activity has incomplete canonical zones")
        activities.append(
            LoadHistoryActivity(
                activity_ref=str(activity.activity_ref),
                date=_calendar_date(activity.date, "activity"),
                sport=str(activity.sport),
                duration_min=_optional_finite(activity.duration_min, "activity duration"),
                strength_time_min=_finite(
                    activity.strength_time_min, "activity strength time"
                ),
                quality_status=str(activity.quality_status),
                hr_coverage_percent=_bounded_percentage(
                    activity.hr_coverage_percent, "activity HR coverage"
                ),
                zones=[
                    ActivityZoneLoad(
                        zone=zone,
                        raw_time_min=_finite(zone_rows.loc[zone, "T_z"], f"T_z[{zone}]"),
                        equivalent_time_min=_finite(
                            zone_rows.loc[zone, "T_eq_z"], f"T_eq_z[{zone}]"
                        ),
                        effective_load=_finite(
                            zone_rows.loc[zone, "E_z"], f"E_z[{zone}]"
                        ),
                        mean_effective_hr_bpm=_optional_finite(
                            zone_rows.loc[zone, "mean_effective_hr_bpm"],
                            f"mean HR[{zone}]",
                        ),
                        average_minute_value_percent=_optional_finite(
                            zone_rows.loc[zone, "average_minute_value_percent"],
                            f"minute value[{zone}]",
                        ),
                    )
                    for zone in ZONES
                ],
            )
        )

    strength_rows = dataset.rolling_load.loc[
        dataset.rolling_load["component"] == "STR"
    ].sort_values("date", kind="stable")
    strength_daily = [
        DailyStrengthLoad(
            date=_calendar_date(row.date, "daily strength load"),
            real_time_min=_finite(
                dataset.daily_loads.loc[pd.Timestamp(row.date), "q_STR"]
                / float(dataset.strength_duration_coefficient),
                "daily strength real time",
            ),
            equivalent_time_min=_finite(
                dataset.daily_loads.loc[pd.Timestamp(row.date), "q_STR"],
                "daily strength equivalent time",
            ),
            effective_load=_finite(row.effective, "daily strength effective"),
            e7_daily=_finite(row.E7_daily, "daily strength E7"),
            e40_daily=_finite(row.E40_daily, "daily strength E40"),
            status_7_40=_finite(row.index_7_40, "daily strength 7/40"),
            tref_used_min=_positive_finite(
                row.Tref, "daily strength Tref used"
            ),
        )
        for row in strength_rows.itertuples(index=False)
    ]
    period_end = pd.Timestamp(dataset.period_end)
    strength_real = (
        dataset.daily_loads["q_STR"]
        / float(dataset.strength_duration_coefficient)
    )
    strength = StrengthLoadHistory(
        model=StrengthLoadModel(
            classification_version=str(dataset.strength_classification_version),
            source="intervals-activity-type-duration",
            duration_basis="recording-time-first",
            equivalent_time_coefficient=_finite(
                dataset.strength_duration_coefficient,
                "strength duration coefficient",
            ),
            aerobic_hr_counted=False,
        ),
        summary=StrengthLoadSummary(
            recorded_activities=int(
                (
                    dataset.activities["strength_time_min"].astype(float) > 0.0
                ).sum()
            ),
            real_time_7d_min=_finite(
                strength_real.loc[
                    (strength_real.index >= period_end - pd.Timedelta(days=6))
                    & (strength_real.index <= period_end)
                ].sum(),
                "strength real time 7d",
            ),
            real_time_40d_min=_finite(
                strength_real.loc[
                    (strength_real.index >= period_end - pd.Timedelta(days=39))
                    & (strength_real.index <= period_end)
                ].sum(),
                "strength real time 40d",
            ),
            e7_daily=_finite(dataset.load_stats.loc["STR", "E7_daily"], "strength E7"),
            e40_daily=_finite(dataset.load_stats.loc["STR", "E40_daily"], "strength E40"),
            status_7_40=_finite(dataset.load_stats.loc["STR", "index_7_40"], "strength 7/40"),
            tref_min=_finite(dataset.load_stats.loc["STR", "Tref"], "strength Tref"),
            history_reliability=_finite(
                dataset.load_stats.loc["STR", "reliability"],
                "strength reliability",
            ),
        ),
        daily=strength_daily,
    )

    return LoadHistoryResponse(
        schema_version="load-history-v2",
        athlete_id=context.public_alias,
        period_start=dataset.period_start,
        period_end=dataset.period_end,
        tref_bounds_profile_version=tref_profile_version,
        quality=LoadHistoryQuality(
            processed_activities=int(dataset.processed_activities),
            limited_activities=int(dataset.limited_activities),
            excluded_activities=int(dataset.excluded_activities),
            no_activity_days=int(dataset.no_activity_days),
            warnings=list(dataset.warnings),
        ),
        zones=zone_summaries,
        daily=daily,
        activities=activities,
        strength=strength,
    )


def dataset_to_recovery_history(
    dataset: Any,
    context: AthleteContext,
    wellness: Mapping[str, Any],
    wellness_diagnostics: Mapping[str, Any],
    parameters: Mapping[str, Any],
) -> RecoveryHistoryResponse:
    """Expose the already-computed canonical load-recovery history."""

    recovery = parameters["recovery"]
    history = dataset.readiness_history.loc[
        dataset.readiness_history["component"].isin(ZONES)
    ].sort_values(["date", "component"], kind="stable")
    strength_history = dataset.readiness_history.loc[
        dataset.readiness_history["component"] == "STR"
    ].sort_values("date", kind="stable")
    strength = RecoveryStrengthHistory(
        settings=RecoveryStrengthSettings(
            tref_min=_finite(dataset.load_stats.loc["STR", "Tref"], "Tref[STR]"),
            sensitivity=_finite(recovery["STR"]["sensitivity"], "sensitivity[STR]"),
            tau_days=_finite(recovery["STR"]["tau_days"], "tau[STR]"),
            fatigue_cap=_finite(recovery["STR"]["fmax"], "fmax[STR]"),
        ),
        current=RecoveryStrengthCurrent(
            readiness_percent=_bounded_percentage(
                dataset.load_readiness.loc["STR", "readiness"],
                "readiness[STR]",
            ),
            residual_fatigue=_finite(
                dataset.load_readiness.loc["STR", "fatigue"], "fatigue[STR]"
            ),
            days_to_practical_recovery=_finite(
                dataset.load_readiness.loc["STR", "days_to_full"], "days[STR]"
            ),
        ),
        daily=[
            DailyStrengthRecovery(
                date=_calendar_date(row.date, "daily strength recovery"),
                readiness_before_percent=_bounded_percentage(
                    row.readiness_before, "strength readiness before"
                ),
                readiness_after_percent=_bounded_percentage(
                    row.readiness_after, "strength readiness after"
                ),
                residual_fatigue_after=_finite(
                    row.fatigue_after, "strength fatigue after"
                ),
                impulse=_finite(row.impulse, "strength impulse"),
                effective_load=_finite(row.effective, "strength effective"),
                tref_min=_finite(row.Tref, "strength Tref"),
            )
            for row in strength_history.itertuples(index=False)
        ],
    )
    return RecoveryHistoryResponse(
        schema_version="recovery-history-v1",
        athlete_id=context.public_alias,
        period_start=dataset.period_start,
        period_end=dataset.period_end,
        basis="load-only",
        wellness_freshness=str(wellness.get("freshness", "unknown")),
        wellness_coverage_percent=_bounded_percentage(
            100.0 * _finite(wellness.get("coverage", 0.0), "wellness coverage"),
            "wellness coverage",
        ),
        wellness_diagnostics=WellnessCoverageDiagnostics.model_validate(
            wellness_diagnostics
        ),
        model=RecoveryModelMetadata(
            algorithm_version=str(dataset.recovery_model_version),
            parameter_version=context.recovery_parameter_version,
            parameter_fingerprint=str(dataset.parameter_fingerprint),
            practical_full_recovery_percent=_bounded_percentage(
                parameters["practical_full_recovery"],
                "practical full recovery",
            ),
        ),
        settings=[
            RecoveryZoneSettings(
                zone=zone,
                tref_min=_finite(dataset.load_stats.loc[zone, "Tref"], f"Tref[{zone}]"),
                sensitivity=_finite(recovery[zone]["sensitivity"], f"sensitivity[{zone}]"),
                tau_days=_finite(recovery[zone]["tau_days"], f"tau[{zone}]"),
                fatigue_cap=_finite(recovery[zone]["fmax"], f"fmax[{zone}]"),
            )
            for zone in ZONES
        ],
        current=[
            RecoveryZoneCurrent(
                zone=zone,
                readiness_percent=_bounded_percentage(
                    dataset.load_readiness.loc[zone, "readiness"],
                    f"readiness[{zone}]",
                ),
                residual_fatigue=_finite(
                    dataset.load_readiness.loc[zone, "fatigue"],
                    f"fatigue[{zone}]",
                ),
                days_to_practical_recovery=_finite(
                    dataset.load_readiness.loc[zone, "days_to_full"],
                    f"days[{zone}]",
                ),
            )
            for zone in ZONES
        ],
        daily=[
            DailyRecovery(
                date=_calendar_date(row.date, "daily recovery"),
                zone=str(row.component),
                readiness_before_percent=_bounded_percentage(
                    row.readiness_before, f"readiness before[{row.component}]"
                ),
                readiness_after_percent=_bounded_percentage(
                    row.readiness_after, f"readiness after[{row.component}]"
                ),
                residual_fatigue_after=_finite(
                    row.fatigue_after, f"fatigue after[{row.component}]"
                ),
                impulse=_finite(row.impulse, f"impulse[{row.component}]"),
                effective_load=_finite(row.effective, f"effective[{row.component}]"),
                tref_min=_finite(row.Tref, f"Tref[{row.component}]"),
            )
            for row in history.itertuples(index=False)
        ],
        strength=strength,
    )


def recovery_source_supports_restore(payload: Mapping[str, Any]) -> bool:
    """Return whether a snapshot pins every daily Tref needed for restore."""

    try:
        history = AthleteSnapshot.model_validate(payload).load_history
    except (TypeError, ValueError):
        return False
    return (
        history.schema_version == "load-history-v2"
        and history.tref_bounds_profile_version
        == CANONICAL_TREF_PROFILE_VERSION
    )


def restore_recovery_history_from_snapshot(
    repository: SnapshotRepository,
    *,
    athlete_alias: str,
    provider_athlete_id: str,
    athlete_settings: AthleteModelSettings | None,
    environ: Mapping[str, str] | None = None,
) -> RecoveryHistoryResponse:
    """Rebuild load-only recovery from the persisted canonical load history.

    Recovery is a deterministic projection of canonical daily effective load.
    Restoring it therefore must not depend on another slow provider import or
    modify the already-persisted training/load analysis.
    """

    env = environ or os.environ
    alias = athlete_alias.strip() if isinstance(athlete_alias, str) else ""
    provider_id = (
        provider_athlete_id.strip()
        if isinstance(provider_athlete_id, str)
        else ""
    )
    if not alias or not provider_id:
        raise ConfigurationError("Provider profile or athlete alias is unavailable")
    payload = repository.latest(alias)
    if not isinstance(payload, Mapping):
        raise RecoverySourceRefreshRequired(
            "Recovery source requires a full real-data refresh"
        )
    if not recovery_source_supports_restore(payload):
        raise RecoverySourceRefreshRequired(
            "Recovery source requires a full real-data refresh"
        )
    try:
        snapshot = AthleteSnapshot.model_validate(payload)
        history = snapshot.load_history
        start = date.fromisoformat(history.period_start)
        end = date.fromisoformat(history.period_end)
    except (TypeError, ValueError) as exc:
        raise RecoverySourceRefreshRequired(
            "Recovery source requires a full real-data refresh"
        ) from exc

    context = context_from_environment(
        env,
        provider_athlete_id=provider_id,
        athlete_alias=alias,
        athlete_settings=athlete_settings,
    )
    parameters = fresh_parameters()
    # Keep the API import surface credential-free and lightweight.  The real
    # data package is loaded only for this explicit recovery operation.
    from intervals_inspector.real_data_source import (
        RECOVERY_MODEL_VERSION,
        recovery_parameter_fingerprint,
    )

    dates = pd.date_range(start, end, freq="D")
    daily_loads = pd.DataFrame(
        0.0,
        index=dates,
        columns=[f"e_{component}" for component in (*ZONES, "STR")],
    )
    for row in history.daily:
        day = pd.Timestamp(row.date).normalize()
        if day in daily_loads.index:
            daily_loads.loc[day, f"e_{row.zone}"] = float(row.effective_load)
            if row.tref_used_min is None:
                raise RecoverySourceRefreshRequired(
                    "Recovery source requires a full real-data refresh"
                )
            daily_loads.loc[day, f"tref_used_{row.zone}"] = float(
                row.tref_used_min
            )
    if history.strength is not None:
        for row in history.strength.daily:
            day = pd.Timestamp(row.date).normalize()
            if day in daily_loads.index:
                daily_loads.loc[day, "e_STR"] = float(row.effective_load)
                if row.tref_used_min is None:
                    raise RecoverySourceRefreshRequired(
                        "Recovery source requires a full real-data refresh"
                    )
                daily_loads.loc[day, "tref_used_STR"] = float(
                    row.tref_used_min
                )

    required_tref_columns = {
        f"tref_used_{component}" for component in (*ZONES, "STR")
    }
    if not required_tref_columns.issubset(daily_loads.columns) or daily_loads[
        sorted(required_tref_columns)
    ].isna().any().any():
        raise RecoverySourceRefreshRequired(
            "Recovery source requires a full real-data refresh"
        )

    readiness_history = compute_readiness_history(
        daily_loads,
        parameters,
        use_supplied_tref=True,
    )
    readiness = current_readiness(readiness_history, parameters, target_date=end)
    zone_settings = {row.zone: row for row in history.zones}
    recovery_parameters = parameters["recovery"]
    zone_daily = readiness_history.loc[
        readiness_history["component"].isin(ZONES)
    ].sort_values(["date", "component"], kind="stable")
    strength_daily = readiness_history.loc[
        readiness_history["component"] == "STR"
    ].sort_values("date", kind="stable")

    strength: RecoveryStrengthHistory | None = None
    if history.strength is not None:
        strength = RecoveryStrengthHistory(
            settings=RecoveryStrengthSettings(
                tref_min=_finite(history.strength.summary.tref_min, "Tref[STR]"),
                sensitivity=_finite(
                    recovery_parameters["STR"]["sensitivity"], "sensitivity[STR]"
                ),
                tau_days=_finite(
                    recovery_parameters["STR"]["tau_days"], "tau[STR]"
                ),
                fatigue_cap=_finite(
                    recovery_parameters["STR"]["fmax"], "fmax[STR]"
                ),
            ),
            current=RecoveryStrengthCurrent(
                readiness_percent=_bounded_percentage(
                    readiness.loc["STR", "readiness"], "readiness[STR]"
                ),
                residual_fatigue=_finite(
                    readiness.loc["STR", "fatigue"], "fatigue[STR]"
                ),
                days_to_practical_recovery=_finite(
                    readiness.loc["STR", "days_to_full"], "days[STR]"
                ),
            ),
            daily=[
                DailyStrengthRecovery(
                    date=_calendar_date(row.date, "daily strength recovery"),
                    readiness_before_percent=_bounded_percentage(
                        row.readiness_before, "strength readiness before"
                    ),
                    readiness_after_percent=_bounded_percentage(
                        row.readiness_after, "strength readiness after"
                    ),
                    residual_fatigue_after=_finite(
                        row.fatigue_after, "strength fatigue after"
                    ),
                    impulse=_finite(row.impulse, "strength impulse"),
                    effective_load=_finite(row.effective, "strength effective"),
                    tref_min=_finite(row.Tref, "strength Tref"),
                )
                for row in strength_daily.itertuples(index=False)
            ],
        )

    restored = RecoveryHistoryResponse(
        schema_version="recovery-history-v1",
        athlete_id=context.public_alias,
        period_start=history.period_start,
        period_end=history.period_end,
        basis="load-only",
        wellness_freshness="unknown",
        wellness_coverage_percent=0.0,
        wellness_diagnostics=None,
        model=RecoveryModelMetadata(
            algorithm_version=RECOVERY_MODEL_VERSION,
            parameter_version=context.recovery_parameter_version,
            parameter_fingerprint=recovery_parameter_fingerprint(parameters),
            practical_full_recovery_percent=_bounded_percentage(
                parameters["practical_full_recovery"],
                "practical full recovery",
            ),
        ),
        settings=[
            RecoveryZoneSettings(
                zone=zone,
                tref_min=_finite(zone_settings[zone].tref_min, f"Tref[{zone}]"),
                sensitivity=_finite(
                    recovery_parameters[zone]["sensitivity"],
                    f"sensitivity[{zone}]",
                ),
                tau_days=_finite(
                    recovery_parameters[zone]["tau_days"], f"tau[{zone}]"
                ),
                fatigue_cap=_finite(
                    recovery_parameters[zone]["fmax"], f"fmax[{zone}]"
                ),
            )
            for zone in ZONES
        ],
        current=[
            RecoveryZoneCurrent(
                zone=zone,
                readiness_percent=_bounded_percentage(
                    readiness.loc[zone, "readiness"], f"readiness[{zone}]"
                ),
                residual_fatigue=_finite(
                    readiness.loc[zone, "fatigue"], f"fatigue[{zone}]"
                ),
                days_to_practical_recovery=_finite(
                    readiness.loc[zone, "days_to_full"], f"days[{zone}]"
                ),
            )
            for zone in ZONES
        ],
        daily=[
            DailyRecovery(
                date=_calendar_date(row.date, "daily recovery"),
                zone=str(row.component),
                readiness_before_percent=_bounded_percentage(
                    row.readiness_before, f"readiness before[{row.component}]"
                ),
                readiness_after_percent=_bounded_percentage(
                    row.readiness_after, f"readiness after[{row.component}]"
                ),
                residual_fatigue_after=_finite(
                    row.fatigue_after, f"fatigue after[{row.component}]"
                ),
                impulse=_finite(row.impulse, f"impulse[{row.component}]"),
                effective_load=_finite(
                    row.effective, f"effective[{row.component}]"
                ),
                tref_min=_finite(row.Tref, f"Tref[{row.component}]"),
            )
            for row in zone_daily.itertuples(index=False)
        ],
        strength=strength,
    )
    current_payload = repository.latest(alias)
    if not isinstance(current_payload, Mapping):
        raise ConfigurationError("Training snapshot requires a full real-data refresh")
    patched = dict(current_payload)
    patched["recovery_history"] = restored.model_dump(mode="json")
    repository.replace(alias, patched)
    return restored


def training_status_from_persisted(
    payload: Mapping[str, Any],
) -> TrainingStatusResponse:
    """Read the new aggregate envelope while accepting the deployed v1 snapshot."""

    if payload.get("schema_version") == "training-status-v1":
        return TrainingStatusResponse.model_validate(payload)
    return AthleteSnapshot.model_validate(payload).training_status


def load_history_from_persisted(payload: Mapping[str, Any]) -> LoadHistoryResponse:
    return AthleteSnapshot.model_validate(payload).load_history


def completed_work_from_load_history(
    history: LoadHistoryResponse,
    period_start: date | None = None,
    period_end: date | None = None,
) -> CompletedWorkResponse:
    """Aggregate an explicit period from an already-persisted load snapshot.

    This is a technical reporting adapter. It does not fetch provider data and
    does not alter any physiological value stored in the load history.
    """

    available_start = date.fromisoformat(history.period_start)
    available_end = date.fromisoformat(history.period_end)
    start = period_start or available_start
    end = period_end or available_end
    if start < available_start or end > available_end or start > end:
        raise ValueError("Completed-work period is outside stored history")

    activities = [
        activity
        for activity in history.activities
        if start <= date.fromisoformat(activity.date) <= end
    ]
    zone_totals = {
        zone: {"raw": 0.0, "equivalent": 0.0, "effective": 0.0}
        for zone in ZONES
    }
    sport_totals: dict[str, dict[str, float | int]] = {}
    duration_total = 0.0
    missing_duration = 0
    for activity in activities:
        if activity.duration_min is None:
            missing_duration += 1
            duration = 0.0
        else:
            duration = activity.duration_min
            duration_total += duration
        sport = sport_totals.setdefault(
            activity.sport,
            {"activities": 0, "duration": 0.0, "zoned": 0.0},
        )
        sport["activities"] += 1
        sport["duration"] += duration
        for row in activity.zones:
            target = zone_totals[row.zone]
            target["raw"] += row.raw_time_min
            target["equivalent"] += row.equivalent_time_min
            target["effective"] += row.effective_load
            sport["zoned"] += row.raw_time_min

    zoned_total = sum(float(row["raw"]) for row in zone_totals.values())
    return CompletedWorkResponse(
        schema_version="completed-work-v1",
        athlete_id=history.athlete_id,
        period_start=start.isoformat(),
        period_end=end.isoformat(),
        model=CompletedWorkMetadata(
            aggregation_version="completed-work-snapshot-aggregation-v1",
            source_schema_version=history.schema_version,
            sport_grouping="provider-label-exact",
        ),
        quality=CompletedWorkQuality(
            modeled_activities=len(activities),
            limited_activities=sum(
                activity.quality_status == "limited" for activity in activities
            ),
            missing_duration_activities=missing_duration,
        ),
        totals=CompletedWorkTotals(
            activity_duration_min=_finite(duration_total, "completed duration"),
            zoned_hr_time_min=_finite(zoned_total, "completed zoned HR time"),
        ),
        zones=[
            CompletedWorkZone(
                zone=zone,
                raw_time_min=_finite(zone_totals[zone]["raw"], f"completed raw[{zone}]"),
                equivalent_time_min=_finite(
                    zone_totals[zone]["equivalent"],
                    f"completed equivalent[{zone}]",
                ),
                effective_load=_finite(
                    zone_totals[zone]["effective"],
                    f"completed effective[{zone}]",
                ),
            )
            for zone in ZONES
        ],
        sports=[
            CompletedWorkSport(
                sport=sport,
                activities_count=int(values["activities"]),
                activity_duration_min=_finite(
                    values["duration"], f"sport duration[{sport}]"
                ),
                zoned_hr_time_min=_finite(
                    values["zoned"], f"sport zoned HR time[{sport}]"
                ),
            )
            for sport, values in sorted(
                sport_totals.items(), key=lambda item: (item[0].casefold(), item[0])
            )
        ],
    )


def completed_work_from_persisted(
    payload: Mapping[str, Any],
    period_start: date | None = None,
    period_end: date | None = None,
) -> CompletedWorkResponse:
    return completed_work_from_load_history(
        load_history_from_persisted(payload), period_start, period_end
    )


def volume_history_from_load_history(
    history: LoadHistoryResponse,
) -> VolumeHistoryResponse:
    """Build calendar-week volume series from the persisted activity aggregates.

    The two output series retain their distinct source semantics. Provider
    duration uses only present values, while HR-zoned time is the exact sum of
    stored Z1–Z5 ``raw_time_min``. No effective-load values are combined.
    """

    period_start = date.fromisoformat(history.period_start)
    period_end = date.fromisoformat(history.period_end)
    first_week = period_start - timedelta(days=period_start.weekday())
    last_week = period_end - timedelta(days=period_end.weekday())
    buckets: dict[date, dict[str, float | int]] = {}
    week_start = first_week
    while week_start <= last_week:
        buckets[week_start] = {
            "activities": 0,
            "limited": 0,
            "missing_duration": 0,
            "duration": 0.0,
            "zoned": 0.0,
        }
        week_start += timedelta(days=7)

    for activity in history.activities:
        activity_date = date.fromisoformat(activity.date)
        if not period_start <= activity_date <= period_end:
            raise ValueError("Load-history activity is outside its stored period")
        activity_week = activity_date - timedelta(days=activity_date.weekday())
        bucket = buckets[activity_week]
        bucket["activities"] += 1
        bucket["limited"] += activity.quality_status == "limited"
        if activity.duration_min is None:
            bucket["missing_duration"] += 1
        else:
            bucket["duration"] += activity.duration_min
        bucket["zoned"] += sum(row.raw_time_min for row in activity.zones)

    weekly = []
    for week_start, values in buckets.items():
        week_end = week_start + timedelta(days=6)
        observed_start = max(week_start, period_start)
        observed_end = min(week_end, period_end)
        weekly.append(
            WeeklyVolume(
                week_start=week_start.isoformat(),
                week_end=week_end.isoformat(),
                observed_days=(observed_end - observed_start).days + 1,
                activities_count=int(values["activities"]),
                limited_activities=int(values["limited"]),
                missing_duration_activities=int(values["missing_duration"]),
                activity_duration_min=_finite(
                    values["duration"], f"weekly duration[{week_start}]"
                ),
                zoned_hr_time_min=_finite(
                    values["zoned"], f"weekly zoned HR time[{week_start}]"
                ),
            )
        )

    return VolumeHistoryResponse(
        schema_version="volume-history-v1",
        athlete_id=history.athlete_id,
        period_start=history.period_start,
        period_end=history.period_end,
        model=VolumeHistoryMetadata(
            aggregation_version="volume-history-calendar-week-v1",
            source_schema_version=history.schema_version,
            calendar_week_start="monday",
            activity_duration_handling="known-values-only",
        ),
        quality=VolumeHistoryQuality(
            modeled_activities=len(history.activities),
            limited_activities=sum(
                activity.quality_status == "limited" for activity in history.activities
            ),
            missing_duration_activities=sum(
                activity.duration_min is None for activity in history.activities
            ),
        ),
        weekly=weekly,
    )


def volume_history_from_persisted(
    payload: Mapping[str, Any],
) -> VolumeHistoryResponse:
    return volume_history_from_load_history(load_history_from_persisted(payload))


def recovery_history_from_persisted(
    payload: Mapping[str, Any],
) -> RecoveryHistoryResponse:
    history = AthleteSnapshot.model_validate(payload).recovery_history
    if history is None:
        raise ValueError("Recovery history requires a new real-data refresh")
    return history


def refresh(repository: SnapshotRepository, *, environ: Mapping[str, str] | None = None,
            period_end: date | None = None, client: Any | None = None,
            now: datetime | None = None, access_token: str | None = None,
            provider_athlete_id: str | None = None,
            athlete_alias: str | None = None,
            athlete_settings: AthleteModelSettings | None = None) -> RefreshResult:
    """Retrieve once, normalize once, calculate canonical results, then replace atomically."""
    env = environ or os.environ
    context = context_from_environment(
        env,
        provider_athlete_id=provider_athlete_id,
        athlete_alias=athlete_alias,
        athlete_settings=athlete_settings,
    )
    token = (access_token or env.get("INTERVALS_ACCESS_TOKEN", "")).strip()
    salt = env.get("ONFLOWS_SNAPSHOT_SALT", "").strip()
    if not token or not salt:
        raise ConfigurationError("Provider credentials or snapshot salt are unavailable")
    try:
        days = int(env.get("ONFLOWS_HISTORY_DAYS", "90"))
        if not 41 <= days <= 90:
            raise ValueError
    except ValueError as exc:
        raise ConfigurationError("History period must be between 41 and 90 days") from exc
    # Lazy imports keep FastAPI startup free from Streamlit and provider runtime dependencies.
    from intervals_inspector.intervals_client import IntervalsAPIError, IntervalsClient
    from intervals_inspector.real_data_source import load_real_history
    from intervals_inspector.shadow_model import configuration_with_hr_boundaries
    provider = client or IntervalsClient(token, context.provider_athlete_id)
    end = period_end or date.today(); start = end.fromordinal(end.toordinal() - days + 1)
    stage = "athlete"
    try:
        # Validate the configured profile and retrieve settings at the provider
        # boundary; payloads are deliberately discarded and never persisted.
        provider.get_athlete_result()
        stage = "sport_settings"
        provider.get_sport_settings_result()
        stage = "wellness"
        wellness_payload = provider.get_wellness_result(start.isoformat(), end.isoformat()).payload
        wellness_rows = wellness_rows_from_payload(wellness_payload)
        latest_wellness = max((r for r in wellness_rows if isinstance(r, Mapping)), key=lambda r: str(r.get("id") or r.get("date") or ""), default={})
        wellness = normalize_wellness(latest_wellness, now=now)
        wellness_diagnostics = summarize_wellness_coverage(
            wellness_rows,
            period_start=start,
            period_end=end,
            now=now,
        )
        wellness_calendar = daily_wellness_summaries(
            wellness_rows,
            period_start=start,
            period_end=end,
            now=now,
        )
        parameters = fresh_parameters()
        stage = "history"
        from .activity_shadow_pipeline import (
            activity_shadow_configuration_fingerprint,
            build_immutable_activity_input,
            compute_activity_shadow,
        )
        catalog_metadata: dict[str, dict[str, Any]] = {}
        catalog_provider_keys: dict[str, str] = {}
        shadow_runs: dict[str, str] = {}
        scientific_input_hashes: dict[str, str] = {}
        identity_secret = env.get("ONFLOWS_ACTIVITY_ID_SECRET", "").strip() or salt

        def resolve_activity_ref(provider_activity_id: str) -> str:
            key = provider_activity_key(
                provider_athlete_id=context.provider_athlete_id,
                provider_activity_id=provider_activity_id,
                secret=identity_secret,
            )
            activity_ref = repository.resolve_activity_ref(
                context.public_alias, key
            )
            catalog_provider_keys[activity_ref] = key
            return activity_ref

        def collect_activity_metadata(
            activity_ref: str, detail: Mapping[str, Any]
        ) -> None:
            catalog_metadata[activity_ref] = extract_activity_metadata(
                activity_ref, detail
            )

        def process_activity_shadow(
            activity_ref: str,
            detail: Mapping[str, Any],
            normalized: Any,
        ) -> Mapping[str, Any]:
            immutable_input = build_immutable_activity_input(detail, normalized)
            scientific_input_hashes[activity_ref] = str(
                immutable_input["input_hash"]
            )
            configuration_fingerprint = activity_shadow_configuration_fingerprint(
                context.zone_bounds_bpm, context.hrmax_bpm
            )
            if repository.latest_activity_input_hash(
                context.public_alias, activity_ref
            ) == immutable_input["input_hash"]:
                existing_run = repository.latest_activity_shadow_run_metadata(
                    context.public_alias, activity_ref
                )
                if (
                    existing_run is not None
                    and existing_run.get("configuration_fingerprint")
                    == configuration_fingerprint
                ):
                    existing_run_key = str(existing_run["run_key"])
                    shadow_runs[activity_ref] = existing_run_key
                    return {
                        "status": "unchanged",
                        "run_key": existing_run_key,
                        "experimental": True,
                        "affects_canonical_load": False,
                    }
            immutable_input, derived = compute_activity_shadow(
                detail=detail,
                normalized=normalized,
                zone_bounds_bpm=context.zone_bounds_bpm,
                explicit_hrmax_bpm=context.hrmax_bpm,
            )
            run_key = repository.publish_activity_shadow(
                athlete_alias=context.public_alias,
                activity_ref=activity_ref,
                input_payload=immutable_input,
                derived_payload=derived,
            )
            shadow_runs[activity_ref] = run_key
            return {
                "status": "published",
                "run_key": run_key,
                "experimental": True,
                "affects_canonical_load": False,
            }

        dataset = load_real_history(provider, profile_identifier=context.provider_athlete_id,
                                    session_salt=salt, parameters=parameters,
                                    period_end=end, days=days, loaded_at_utc=now,
                                    configuration=configuration_with_hr_boundaries(context.zone_bounds_bpm),
                                    activity_shadow_processor=process_activity_shadow,
                                    activity_ref_resolver=resolve_activity_ref,
                                    activity_metadata_collector=collect_activity_metadata)
        catalog_rows: list[Mapping[str, Any]] = []
        for activity in dataset.activities.itertuples(index=False):
            metadata = catalog_metadata.get(str(activity.activity_ref))
            if metadata is None:
                continue
            activity_day = pd.Timestamp(activity.date).normalize()
            if activity_day not in dataset.daily_loads.index:
                raise ValueError("Canonical activity Tref provenance is incomplete")
            daily_load_row = dataset.daily_loads.loc[activity_day]
            tref_used_min = {
                component: _positive_finite(
                    daily_load_row[f"tref_used_{component}"],
                    f"activity Tref used[{component}]",
                )
                for component in (*ZONES, "STR")
            }
            tref_profile_version = dataset.tref_bounds_profile_version
            if (
                not isinstance(tref_profile_version, str)
                or not tref_profile_version.strip()
            ):
                raise ValueError("Canonical activity Tref profile is incomplete")
            tref_profile_version = tref_profile_version.strip()
            catalog_provider_key = catalog_provider_keys.get(
                str(activity.activity_ref)
            )
            if catalog_provider_key is None:
                raise ValueError("Canonical activity provider identity is incomplete")
            zone_rows = dataset.activity_zones.loc[
                dataset.activity_zones["activity_ref"] == activity.activity_ref
            ]
            zones = [
                {
                    "zone": str(zone.zone),
                    "raw_time_s": float(zone.T_z) * 60.0,
                    "equivalent_time_s": float(zone.T_eq_z) * 60.0,
                    "effective_load": float(zone.E_z),
                }
                for zone in zone_rows.itertuples(index=False)
            ]
            canonical_load = sum(float(zone["effective_load"]) for zone in zones)
            if float(activity.strength_time_min or 0.0) > 0.0:
                canonical_load = float(activity.strength_time_min)
            canonical_summary = {
                "schema_version": "activity-canonical-summary-v2",
                "model_version": str(activity.model_version),
                "normalization_version": str(activity.normalization_version),
                "tref_bounds_profile_version": tref_profile_version,
                "tref_used_min": tref_used_min,
                "duration_min": (
                    float(activity.duration_min)
                    if pd.notna(activity.duration_min)
                    else None
                ),
                "strength_time_min": float(activity.strength_time_min or 0.0),
                "zones": zones,
            }
            source_scientific_input_hash = scientific_input_hashes.get(
                str(activity.activity_ref)
            )
            if source_scientific_input_hash is None:
                strength_input = {
                    "schema_version": "activity-strength-input-v1",
                    "activity_type": metadata.get("activity_type"),
                    "activity_sub_type": metadata.get("activity_sub_type"),
                    "duration_min": canonical_summary["duration_min"],
                    "classification_version": str(
                        dataset.strength_classification_version
                    ),
                }
                source_scientific_input_hash = hashlib.sha256(
                    json.dumps(
                        strength_input,
                        sort_keys=True,
                        separators=(",", ":"),
                        allow_nan=False,
                    ).encode("utf-8")
                ).hexdigest()
            scientific_input_hash = hashlib.sha256(
                json.dumps(
                    {
                        "schema_version": "canonical-scientific-input-v2",
                        "source_input_hash": source_scientific_input_hash,
                        "tref_bounds_profile_version": tref_profile_version,
                        "tref_used_min": tref_used_min,
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                    allow_nan=False,
                ).encode("utf-8")
            ).hexdigest()
            canonical_run_key = repository.publish_canonical_activity_result(
                athlete_alias=context.public_alias,
                activity_ref=str(activity.activity_ref),
                scientific_input_hash=scientific_input_hash,
                result_payload=canonical_summary,
            )
            catalog_row = {
                    **metadata,
                    "provider_activity_key": catalog_provider_key,
                    "sport": str(activity.sport),
                    "quality_status": str(activity.quality_status),
                    "quality_reason": str(activity.status_reason or "")[:500] or None,
                    "hr_coverage_percent": _bounded_percentage(
                        activity.hr_coverage_percent,
                        "catalog activity HR coverage",
                    ),
                    "canonical_training_load": canonical_load,
                    "canonical_summary": canonical_summary,
                    "latest_canonical_run_key": canonical_run_key,
                }
            if str(activity.activity_ref) in shadow_runs:
                catalog_row["latest_shadow_run_key"] = shadow_runs[
                    str(activity.activity_ref)
                ]
            catalog_rows.append(catalog_row)
        repository.upsert_activity_catalog(context.public_alias, catalog_rows)
        logger.warning(
            "real_refresh_history_result processed=%d limited=%d excluded=%d",
            dataset.processed_activities,
            dataset.limited_activities,
            dataset.excluded_activities,
        )
        stage = "training_status"
        snapshot = dataset_to_training_status(
            dataset, context, wellness, wellness_diagnostics
        )
        stage = "load_history"
        load_history = dataset_to_load_history(dataset, context)
        stage = "recovery_history"
        recovery_history = dataset_to_recovery_history(
            dataset, context, wellness, wellness_diagnostics, parameters
        )
    except IntervalsAPIError as exc:
        logger.warning(
            "real_refresh_failed stage=%s provider_status=%s",
            stage,
            exc.status_code if exc.status_code is not None else "network",
        )
        raise ProviderFailure("Intervals provider request failed") from exc
    except (TypeError, ValueError) as exc:
        logger.warning(
            "real_refresh_failed stage=%s error_type=%s",
            stage,
            type(exc).__name__,
        )
        raise ProviderFailure("Real-data analysis could not be completed safely") from exc
    persisted = AthleteSnapshot(
        schema_version="athlete-snapshot-v1",
        training_status=snapshot,
        load_history=load_history,
        recovery_history=recovery_history,
        wellness_calendar=wellness_calendar,
    )
    repository.replace(context.public_alias, persisted.model_dump(mode="json"))
    return RefreshResult(
        snapshot=snapshot,
        processed_activities=dataset.processed_activities,
        wellness_coverage=float(wellness["coverage"]),
        wellness_records_received=int(wellness_diagnostics["records_received"]),
        wellness_days_stored=len(wellness_calendar),
    )
