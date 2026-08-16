"""Read-only Intervals-to-canonical orchestration for the single-profile pilot.

Provider credentials stop at :func:`refresh`; downstream code receives an
``AthleteContext`` and the privacy-minimized ``RealHistoryDataset`` only.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
import os
from typing import Any, Mapping

from biathlon.constants import fresh_parameters
from biathlon.effective_hr import EFFECTIVE_HR_SOURCE

from .cloud import AthleteContext, SnapshotRepository, normalize_wellness
from .schemas import (
    ActivityZoneLoad,
    AthleteSnapshot,
    DailyZoneLoad,
    DataQuality,
    DailyRecovery,
    LoadHistoryActivity,
    LoadHistoryQuality,
    LoadHistoryResponse,
    ModelMetadata,
    RecoveryHistoryResponse,
    RecoveryModelMetadata,
    RecoveryZoneCurrent,
    RecoveryZoneSettings,
    TrainingStatusResponse,
    ZoneLoadSummary,
    ZoneTrainingStatus,
)

ZONES = ("Z1", "Z2", "Z3", "Z4", "Z5")
PERCENTAGE_TOLERANCE = 1e-6


class ConfigurationError(RuntimeError):
    """A safe startup/refresh configuration failure."""


class ProviderFailure(RuntimeError):
    """A sanitized provider failure with no response payload or identity."""


@dataclass(frozen=True)
class RefreshResult:
    snapshot: TrainingStatusResponse
    processed_activities: int
    wellness_coverage: float


def context_from_environment(
    environ: Mapping[str, str] | None = None,
    *,
    provider_athlete_id: str | None = None,
) -> AthleteContext:
    env = environ or os.environ
    required = ("ONFLOWS_ATHLETE_ALIAS", "ONFLOWS_HR_ZONE_BOUNDS")
    if any(not env.get(key, "").strip() for key in required):
        raise ConfigurationError("Pilot athlete configuration is incomplete")
    resolved_provider_id = (
        provider_athlete_id or env.get("INTERVALS_ATHLETE_ID", "")
    ).strip()
    if not resolved_provider_id:
        raise ConfigurationError("Pilot athlete configuration is incomplete")
    try:
        bounds = tuple(int(item.strip()) for item in env["ONFLOWS_HR_ZONE_BOUNDS"].split(","))
    except (TypeError, ValueError) as exc:
        raise ConfigurationError("Pilot HR zone configuration is invalid") from exc
    try:
        return AthleteContext(
            public_alias=env["ONFLOWS_ATHLETE_ALIAS"].strip(),
            provider_athlete_id=resolved_provider_id,
            zone_bounds_bpm=bounds,
            timezone=env.get("ONFLOWS_ATHLETE_TIMEZONE", "").strip(),
            intra_zone_version=env.get("ONFLOWS_INTRAZONE_VERSION", "").strip(),
            tref_version=env.get("ONFLOWS_TREF_VERSION", "").strip(),
            recovery_parameter_version=env.get("ONFLOWS_RECOVERY_VERSION", "").strip(),
        ).validate()
    except ValueError as exc:
        raise ConfigurationError("Pilot athlete configuration is invalid") from exc


def _finite(value: Any, name: str) -> float:
    import math
    rendered = float(value)
    if not math.isfinite(rendered):
        raise ValueError(f"Non-finite canonical result: {name}")
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


def dataset_to_training_status(dataset: Any, context: AthleteContext, wellness: Mapping[str, Any]) -> TrainingStatusResponse:
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
        warnings.append(f"Wellness is {wellness.get('freshness', 'unknown')} and is not integrated into readiness.")
    if float(wellness.get("coverage", 0.0)) < 1.0:
        warnings.append("Wellness coverage is incomplete; readiness remains load-only.")
    quality = _bounded_percentage(
        latest["hr_coverage_percent"], "latest HR coverage"
    ) / 100.0
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

    return LoadHistoryResponse(
        schema_version="load-history-v1",
        athlete_id=context.public_alias,
        period_start=dataset.period_start,
        period_end=dataset.period_end,
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
    )


def dataset_to_recovery_history(
    dataset: Any,
    context: AthleteContext,
    wellness: Mapping[str, Any],
    parameters: Mapping[str, Any],
) -> RecoveryHistoryResponse:
    """Expose the already-computed canonical load-recovery history."""

    recovery = parameters["recovery"]
    history = dataset.readiness_history.loc[
        dataset.readiness_history["component"].isin(ZONES)
    ].sort_values(["date", "component"], kind="stable")
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
    )


def training_status_from_persisted(
    payload: Mapping[str, Any],
) -> TrainingStatusResponse:
    """Read the new aggregate envelope while accepting the deployed v1 snapshot."""

    if payload.get("schema_version") == "training-status-v1":
        return TrainingStatusResponse.model_validate(payload)
    return AthleteSnapshot.model_validate(payload).training_status


def load_history_from_persisted(payload: Mapping[str, Any]) -> LoadHistoryResponse:
    return AthleteSnapshot.model_validate(payload).load_history


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
            provider_athlete_id: str | None = None) -> RefreshResult:
    """Retrieve once, normalize once, calculate canonical results, then replace atomically."""
    env = environ or os.environ
    context = context_from_environment(
        env, provider_athlete_id=provider_athlete_id
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
    try:
        # Validate the configured profile and retrieve settings at the provider
        # boundary; payloads are deliberately discarded and never persisted.
        provider.get_athlete_result()
        provider.get_sport_settings_result()
        wellness_payload = provider.get_wellness_result(start.isoformat(), end.isoformat()).payload
        wellness_rows = wellness_payload if isinstance(wellness_payload, list) else []
        latest_wellness = max((r for r in wellness_rows if isinstance(r, Mapping)), key=lambda r: str(r.get("id") or r.get("date") or ""), default={})
        wellness = normalize_wellness(latest_wellness, now=now)
        parameters = fresh_parameters()
        dataset = load_real_history(provider, profile_identifier=context.provider_athlete_id,
                                    session_salt=salt, parameters=parameters,
                                    period_end=end, days=days, loaded_at_utc=now,
                                    configuration=configuration_with_hr_boundaries(context.zone_bounds_bpm))
        snapshot = dataset_to_training_status(dataset, context, wellness)
        load_history = dataset_to_load_history(dataset, context)
        recovery_history = dataset_to_recovery_history(
            dataset, context, wellness, parameters
        )
    except IntervalsAPIError as exc:
        raise ProviderFailure("Intervals provider request failed") from exc
    except (TypeError, ValueError) as exc:
        raise ProviderFailure("Real-data analysis could not be completed safely") from exc
    persisted = AthleteSnapshot(
        schema_version="athlete-snapshot-v1",
        training_status=snapshot,
        load_history=load_history,
        recovery_history=recovery_history,
    )
    repository.replace(context.public_alias, persisted.model_dump(mode="json"))
    return RefreshResult(snapshot, dataset.processed_activities, float(wellness["coverage"]))
