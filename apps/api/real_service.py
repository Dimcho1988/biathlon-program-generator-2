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
from .schemas import DataQuality, ModelMetadata, TrainingStatusResponse, ZoneTrainingStatus

ZONES = ("Z1", "Z2", "Z3", "Z4", "Z5")


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
    quality = _finite(latest["hr_coverage_percent"], "latest HR coverage") / 100.0
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
        dataset = load_real_history(provider, profile_identifier=context.provider_athlete_id,
                                    session_salt=salt, parameters=fresh_parameters(),
                                    period_end=end, days=days, loaded_at_utc=now,
                                    configuration=configuration_with_hr_boundaries(context.zone_bounds_bpm))
        snapshot = dataset_to_training_status(dataset, context, wellness)
    except IntervalsAPIError as exc:
        raise ProviderFailure("Intervals provider request failed") from exc
    except (TypeError, ValueError) as exc:
        raise ProviderFailure("Real-data analysis could not be completed safely") from exc
    repository.replace(context.public_alias, snapshot.model_dump(mode="json"))
    return RefreshResult(snapshot, dataset.processed_activities, float(wellness["coverage"]))
