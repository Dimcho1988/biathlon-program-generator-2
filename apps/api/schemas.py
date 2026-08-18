"""Versioned HTTP schemas, kept separate from the physiology model."""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class HealthResponse(StrictModel):
    status: Literal["ok"]


class OAuthAuthorizationResponse(StrictModel):
    authorization_url: str


class OAuthConnectionStatusResponse(StrictModel):
    connected: bool
    scopes: list[str]


class SessionExchangeRequest(StrictModel):
    ticket: str


class SessionExchangeResponse(StrictModel):
    athlete_alias: str


class AthleteSettingsInput(StrictModel):
    hr_zone_bounds_bpm: tuple[int, int, int, int, int, int]
    timezone: str


class AthleteSettingsResponse(StrictModel):
    configured: bool
    hr_zone_bounds_bpm: tuple[int, int, int, int, int, int] | None = None
    timezone: str | None = None


class AthletePlanningProfileInput(StrictModel):
    schema_version: Literal["planning-profile-v1"]
    season_start: date
    season_end: date
    annual_target_hours: float
    sessions_per_week: int
    rest_days: tuple[int, ...]
    double_session_days: tuple[int, ...]
    long_session_day: int
    intensity_days: tuple[int, ...]
    strength_days: tuple[int, ...]
    max_key_sessions_per_week: int
    mesocycle_anchor_date: date
    mesocycle_length_weeks: int
    camp_default_accent_limit: int
    double_threshold_enabled: bool
    double_threshold_day: int
    double_threshold_components: tuple[Literal["Z3", "Z4"], ...]


class AthletePlanningProfileResponse(StrictModel):
    configured: bool
    profile: AthletePlanningProfileInput | None = None


class ModelMetadata(StrictModel):
    algorithm_version: str
    effective_hr_version: str
    effective_hr_source: str
    parameter_version: int


class DataQuality(StrictModel):
    history_reliability: float
    latest_activity_quality_score: float | None
    warnings: list[str]


class ZoneTrainingStatus(StrictModel):
    zone: Literal["Z1", "Z2", "Z3", "Z4", "Z5"]
    raw_time_min: float
    equivalent_time_min: float
    tref_min: float
    status_7_40: float
    recovery_readiness_percent: float
    recovery_days_to_full: float


class TrainingStatusResponse(StrictModel):
    schema_version: Literal["training-status-v1"]
    as_of: str
    athlete_id: str
    model: ModelMetadata
    data_quality: DataQuality
    zones: list[ZoneTrainingStatus]


class LoadHistoryQuality(StrictModel):
    processed_activities: int
    limited_activities: int
    excluded_activities: int
    no_activity_days: int
    warnings: list[str]


class ZoneLoadSummary(StrictModel):
    zone: Literal["Z1", "Z2", "Z3", "Z4", "Z5"]
    e7_daily: float
    e40_daily: float
    status_7_40: float
    tref_min: float
    history_reliability: float


class DailyZoneLoad(StrictModel):
    date: str
    zone: Literal["Z1", "Z2", "Z3", "Z4", "Z5"]
    effective_load: float
    e7_daily: float
    e40_daily: float
    status_7_40: float


class ActivityZoneLoad(StrictModel):
    zone: Literal["Z1", "Z2", "Z3", "Z4", "Z5"]
    raw_time_min: float
    equivalent_time_min: float
    effective_load: float
    mean_effective_hr_bpm: float | None
    average_minute_value_percent: float | None


class LoadHistoryActivity(StrictModel):
    activity_ref: str
    date: str
    sport: str
    duration_min: float | None
    quality_status: Literal["valid", "limited"]
    hr_coverage_percent: float
    zones: list[ActivityZoneLoad]


class LoadHistoryResponse(StrictModel):
    schema_version: Literal["load-history-v1"]
    athlete_id: str
    period_start: str
    period_end: str
    quality: LoadHistoryQuality
    zones: list[ZoneLoadSummary]
    daily: list[DailyZoneLoad]
    activities: list[LoadHistoryActivity]


class CompletedWorkMetadata(StrictModel):
    aggregation_version: Literal["completed-work-snapshot-aggregation-v1"]
    source_schema_version: Literal["load-history-v1"]
    sport_grouping: Literal["provider-label-exact"]


class CompletedWorkQuality(StrictModel):
    modeled_activities: int
    limited_activities: int
    missing_duration_activities: int


class CompletedWorkTotals(StrictModel):
    activity_duration_min: float
    zoned_hr_time_min: float


class CompletedWorkZone(StrictModel):
    zone: Literal["Z1", "Z2", "Z3", "Z4", "Z5"]
    raw_time_min: float
    equivalent_time_min: float
    effective_load: float


class CompletedWorkSport(StrictModel):
    sport: str
    activities_count: int
    activity_duration_min: float
    zoned_hr_time_min: float


class CompletedWorkResponse(StrictModel):
    schema_version: Literal["completed-work-v1"]
    athlete_id: str
    period_start: str
    period_end: str
    model: CompletedWorkMetadata
    quality: CompletedWorkQuality
    totals: CompletedWorkTotals
    zones: list[CompletedWorkZone]
    sports: list[CompletedWorkSport]


class VolumeHistoryMetadata(StrictModel):
    aggregation_version: Literal["volume-history-calendar-week-v1"]
    source_schema_version: Literal["load-history-v1"]
    calendar_week_start: Literal["monday"]
    activity_duration_handling: Literal["known-values-only"]


class VolumeHistoryQuality(StrictModel):
    modeled_activities: int
    limited_activities: int
    missing_duration_activities: int


class WeeklyVolume(StrictModel):
    week_start: str
    week_end: str
    observed_days: int
    activities_count: int
    limited_activities: int
    missing_duration_activities: int
    activity_duration_min: float
    zoned_hr_time_min: float


class VolumeHistoryResponse(StrictModel):
    schema_version: Literal["volume-history-v1"]
    athlete_id: str
    period_start: str
    period_end: str
    model: VolumeHistoryMetadata
    quality: VolumeHistoryQuality
    weekly: list[WeeklyVolume]


class RecoveryModelMetadata(StrictModel):
    algorithm_version: str
    parameter_version: str
    parameter_fingerprint: str
    practical_full_recovery_percent: float


class RecoveryZoneSettings(StrictModel):
    zone: Literal["Z1", "Z2", "Z3", "Z4", "Z5"]
    tref_min: float
    sensitivity: float
    tau_days: float
    fatigue_cap: float


class RecoveryZoneCurrent(StrictModel):
    zone: Literal["Z1", "Z2", "Z3", "Z4", "Z5"]
    readiness_percent: float
    residual_fatigue: float
    days_to_practical_recovery: float


class DailyRecovery(StrictModel):
    date: str
    zone: Literal["Z1", "Z2", "Z3", "Z4", "Z5"]
    readiness_before_percent: float
    readiness_after_percent: float
    residual_fatigue_after: float
    impulse: float
    effective_load: float
    tref_min: float


class RecoveryHistoryResponse(StrictModel):
    schema_version: Literal["recovery-history-v1"]
    athlete_id: str
    period_start: str
    period_end: str
    basis: Literal["load-only"]
    wellness_freshness: Literal["fresh", "stale", "unknown"]
    wellness_coverage_percent: float
    model: RecoveryModelMetadata
    settings: list[RecoveryZoneSettings]
    current: list[RecoveryZoneCurrent]
    daily: list[DailyRecovery]


class AthleteSnapshot(StrictModel):
    """Persisted aggregate envelope; no raw streams or provider identifiers."""

    schema_version: Literal["athlete-snapshot-v1"]
    training_status: TrainingStatusResponse
    load_history: LoadHistoryResponse
    recovery_history: RecoveryHistoryResponse | None = None
