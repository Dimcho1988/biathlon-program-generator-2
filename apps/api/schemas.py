"""Versioned HTTP schemas, kept separate from the physiology model."""

from __future__ import annotations

from datetime import date, timedelta
import math
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class HealthResponse(StrictModel):
    status: Literal["ok"]


class SyncJobRequest(StrictModel):
    scope: Literal["FULL", "WELLNESS", "RECOVERY"]


class SyncEnqueueResponse(StrictModel):
    schema_version: Literal["sync-enqueue-v1"]
    job_id: str
    scope: Literal["FULL", "WELLNESS", "RECOVERY"]
    state: Literal["QUEUED", "RUNNING"]
    coalesced: bool


class SyncStateResponse(StrictModel):
    schema_version: Literal["sync-state-v1"]
    job_id: str | None
    scope: Literal["FULL", "WELLNESS", "RECOVERY"] | None
    state: Literal[
        "IDLE",
        "QUEUED",
        "RUNNING",
        "RETRY_WAIT",
        "SUCCEEDED",
        "FAILED",
        "SUPERSEDED",
    ]
    stage: str | None
    progress_percent: float
    requested_at: str | None
    started_at: str | None
    finished_at: str | None
    retry_at: str | None
    failure_code: str | None
    active_generation_id: str | None
    active_revision: int
    analysis_as_of: str | None
    activated_at: str | None


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
    hrmax_bpm: int | None = None


class AthleteSettingsResponse(StrictModel):
    configured: bool
    hr_zone_bounds_bpm: tuple[int, int, int, int, int, int] | None = None
    timezone: str | None = None
    hrmax_bpm: int | None = None


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


class StressMesocyclePolicy(StrictModel):
    status: Literal["DESIGNED_NOT_ACTIVE"]
    automatic_enabled: Literal[False]
    manual_dose_required: Literal[True]
    selected_accents_only: Literal[True]
    mandatory_recovery: Literal[True]
    affects_canonical_result: Literal[False]


class PlanningMethodologyMetadata(StrictModel):
    schema_version: Literal["planning-methodology-v1"]
    methodology_id: Literal["onflows-canonical"]
    methodology_version: Literal["onflows-canonical-v1"]
    source_scope: Literal["BUILT_IN"]
    mesocycle_pattern: tuple[float, ...]
    supported_accent_modes: tuple[Literal["AUTO", "MANUAL", "HYBRID"], ...]
    accent_components: tuple[Literal["Z1", "Z2", "Z3", "Z4", "Z5", "STR"], ...]
    default_accent_limit: int
    maximum_accent_limit: int
    hybrid_rule: Literal["manual-first-auto-fill"]
    stress_mesocycle: StressMesocyclePolicy


class AthletePlanningProfileResponse(StrictModel):
    configured: bool
    profile: AthletePlanningProfileInput | None = None


class MesocycleAccentPreferencesInput(StrictModel):
    schema_version: Literal["mesocycle-accent-preferences-v1"]
    accent_mode: Literal["AUTO", "MANUAL", "HYBRID"]
    accent_limit: int
    manual_components: tuple[Literal["Z1", "Z2", "Z3", "Z4", "Z5", "STR"], ...]


class MesocycleAccentResolution(StrictModel):
    methodology_version: Literal["onflows-canonical-v1"]
    fixed_components: tuple[Literal["Z1", "Z2", "Z3", "Z4", "Z5", "STR"], ...]
    automatic_slots: int
    resolution_stage: Literal["PLAN_GENERATION"]


class MesocycleAccentPreferencesResponse(StrictModel):
    configured: bool
    preferences: MesocycleAccentPreferencesInput | None = None
    resolution: MesocycleAccentResolution | None = None


class PlanningCalendarEvent(StrictModel):
    event_id: str
    event_type: Literal[
        "MAIN_RACE", "CONTROL_RACE", "CAMP", "TEST", "UNAVAILABLE"
    ]
    name: str
    start_date: date
    end_date: date


class PlanningCalendarInput(StrictModel):
    schema_version: Literal["planning-calendar-v1"]
    events: tuple[PlanningCalendarEvent, ...]


class PlanningGenerationContext(StrictModel):
    schema_version: Literal["planning-context-v1"]
    as_of: date
    ready_for_generation: bool
    generator_status: Literal["NOT_ACTIVE"]
    missing_inputs: tuple[
        Literal[
            "PLANNING_PROFILE",
            "MESOCYCLE_ACCENTS",
            "FUTURE_MAIN_RACE",
            "TRAINING_SNAPSHOT",
        ],
        ...,
    ]
    next_main_race: PlanningCalendarEvent | None
    methodology_version: Literal["onflows-canonical-v1"]
    recovery_basis: Literal["LOAD_ONLY"]
    wellness_integration: Literal["DIAGNOSTIC_ONLY"]


class PlanningCalendarResponse(StrictModel):
    configured: bool
    calendar: PlanningCalendarInput | None = None
    context: PlanningGenerationContext


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
    tref_used_min: float | None = None


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
    strength_time_min: float = 0.0
    quality_status: Literal["valid", "limited"]
    hr_coverage_percent: float
    zones: list[ActivityZoneLoad]


class StrengthLoadModel(StrictModel):
    classification_version: str
    source: Literal["intervals-activity-type-duration"]
    duration_basis: Literal["recording-time-first"]
    equivalent_time_coefficient: float
    aerobic_hr_counted: Literal[False]


class StrengthLoadSummary(StrictModel):
    recorded_activities: int
    real_time_7d_min: float
    real_time_40d_min: float
    e7_daily: float
    e40_daily: float
    status_7_40: float
    tref_min: float
    history_reliability: float


class DailyStrengthLoad(StrictModel):
    date: str
    real_time_min: float
    equivalent_time_min: float
    effective_load: float
    e7_daily: float
    e40_daily: float
    status_7_40: float
    tref_used_min: float | None = None


class StrengthLoadHistory(StrictModel):
    model: StrengthLoadModel
    summary: StrengthLoadSummary
    daily: list[DailyStrengthLoad]


class LoadHistoryResponse(StrictModel):
    schema_version: Literal["load-history-v1", "load-history-v2"]
    athlete_id: str
    period_start: str
    period_end: str
    tref_bounds_profile_version: str | None = None
    quality: LoadHistoryQuality
    zones: list[ZoneLoadSummary]
    daily: list[DailyZoneLoad]
    activities: list[LoadHistoryActivity]
    strength: StrengthLoadHistory | None = None

    @model_validator(mode="after")
    def validate_v2_tref_provenance(self) -> "LoadHistoryResponse":
        if self.schema_version == "load-history-v1":
            return self
        if not self.tref_bounds_profile_version:
            raise ValueError("load-history-v2 requires a Tref bounds profile version")
        if any(
            row.tref_used_min is None
            or not math.isfinite(row.tref_used_min)
            or row.tref_used_min <= 0.0
            for row in self.daily
        ):
            raise ValueError("load-history-v2 requires daily aerobic Tref provenance")
        if self.strength is not None and any(
            row.tref_used_min is None
            or not math.isfinite(row.tref_used_min)
            or row.tref_used_min <= 0.0
            for row in self.strength.daily
        ):
            raise ValueError("load-history-v2 requires daily strength Tref provenance")
        try:
            start = date.fromisoformat(self.period_start)
            end = date.fromisoformat(self.period_end)
        except ValueError as exc:
            raise ValueError("load-history-v2 period is invalid") from exc
        if end < start:
            raise ValueError("load-history-v2 period is invalid")
        expected_dates = {
            (start + timedelta(days=offset)).isoformat()
            for offset in range((end - start).days + 1)
        }
        expected_zone_rows = {
            (day, zone)
            for day in expected_dates
            for zone in ("Z1", "Z2", "Z3", "Z4", "Z5")
        }
        actual_zone_rows = {(row.date, row.zone) for row in self.daily}
        if (
            len(self.daily) != len(expected_zone_rows)
            or actual_zone_rows != expected_zone_rows
        ):
            raise ValueError("load-history-v2 requires complete daily Tref provenance")
        if self.strength is None:
            raise ValueError("load-history-v2 requires strength Tref provenance")
        actual_strength_dates = {row.date for row in self.strength.daily}
        if (
            len(self.strength.daily) != len(expected_dates)
            or actual_strength_dates != expected_dates
        ):
            raise ValueError("load-history-v2 requires complete strength Tref provenance")
        return self


class ActivityZoneSummary(StrictModel):
    zone: Literal["Z1", "Z2", "Z3", "Z4", "Z5"]
    raw_time_s: float
    equivalent_time_s: float
    effective_load: float


class ActivityHrmodZoneSummary(StrictModel):
    zone: Literal["Z1", "Z2", "Z3", "Z4", "Z5"]
    final_time_s: float


class DailyWellnessMetric(StrictModel):
    value: bool | float
    unit: str


class DailyWellnessSummary(StrictModel):
    date: str
    metrics: dict[str, DailyWellnessMetric]


class ActivityCalendarItem(StrictModel):
    activity_ref: str
    start_at_utc: str
    start_local: str
    local_date: str
    local_time: str
    timezone: str | None
    utc_offset_minutes: int | None
    sport: str
    activity_type: str | None
    activity_sub_type: str | None
    name: str | None
    duration_min: float | None
    distance_m: float | None
    elevation_gain_m: float | None
    average_hr_bpm: float | None
    max_hr_bpm: float | None
    average_speed_mps: float | None
    max_speed_mps: float | None
    canonical_training_load: float | None
    quality_status: Literal["valid", "limited", "excluded", "provider_missing"]
    quality_reason: str | None
    hr_coverage_percent: float | None
    shadow_available: bool
    zones: list[ActivityZoneSummary]
    hrmod_zones: list[ActivityHrmodZoneSummary]
    zone_visualization_source: Literal["hrmod_final", "canonical_raw", "none"]


class ActivityWeekSummary(StrictModel):
    week_start: str
    week_end: str
    activities_count: int
    duration_min: float
    distance_m: float
    canonical_training_load: float
    zones: list[ActivityZoneSummary]


class ActivityWellnessStatus(StrictModel):
    state: Literal[
        "available",
        "refresh_required",
        "no_provider_records",
        "no_recognized_values",
        "outside_snapshot_period",
    ]
    records_received: int
    stored_days: int
    displayed_days: int
    latest_observed_date: str | None


class ActivityCalendarResponse(StrictModel):
    schema_version: Literal[
        "activity-calendar-index-v1", "activity-calendar-index-v2"
    ]
    generation_id: str | None = None
    revision: int | None = None
    analysis_as_of: str | None = None
    activated_at: str | None = None
    athlete_id: str
    period_start: str
    period_end: str
    activities: list[ActivityCalendarItem]
    weeks: list[ActivityWeekSummary]
    wellness_days: list[DailyWellnessSummary]
    wellness_status: ActivityWellnessStatus
    wellness_integration: Literal["DIAGNOSTIC_ONLY"]
    includes_timeseries: Literal[False]

    @model_validator(mode="after")
    def validate_generation_metadata(self) -> "ActivityCalendarResponse":
        if self.schema_version == "activity-calendar-index-v2":
            if self.revision is None or self.revision < 0:
                raise ValueError("activity calendar generation revision is invalid")
        return self


class ActivityDetailResponse(ActivityCalendarItem):
    schema_version: Literal["activity-detail-v1"]
    description: str | None
    moving_time_min: float | None
    elapsed_time_min: float | None
    recording_time_min: float | None
    intervals: list[dict[str, Any]]
    previous_activity_ref: str | None
    next_activity_ref: str | None


class ActivitySeriesPoint(StrictModel):
    timestamp: str | None
    elapsed_s: float | None
    hr_bpm: float | None
    speed_kmh: float | None
    altitude_m: float | None
    grade_pct: float | None
    quality_flags: list[str]


class ActivitySeriesResponse(StrictModel):
    schema_version: Literal["activity-series-v1"]
    activity_ref: str
    source_sample_count: int
    returned_sample_count: int
    downsample_step: int
    series: list[ActivitySeriesPoint]


class ActivityViewResponse(StrictModel):
    """One coherent activity page resolved from a single active generation."""

    schema_version: Literal["activity-view-v1"]
    generation_id: str | None
    revision: int
    analysis_as_of: str | None
    activated_at: str | None
    activity: ActivityDetailResponse
    series: ActivitySeriesResponse | None
    shadow: dict[str, Any] | None

    @model_validator(mode="after")
    def validate_generation_metadata(self) -> "ActivityViewResponse":
        if self.revision < 0:
            raise ValueError("activity view generation revision is invalid")
        if (self.generation_id is None) != (self.revision == 0):
            raise ValueError("activity view generation identity is inconsistent")
        if self.series is not None and (
            self.series.activity_ref != self.activity.activity_ref
        ):
            raise ValueError("activity view series reference is inconsistent")
        if self.activity.shadow_available != (self.shadow is not None):
            raise ValueError("activity view shadow availability is inconsistent")
        return self


class CompletedWorkMetadata(StrictModel):
    aggregation_version: Literal["completed-work-snapshot-aggregation-v1"]
    source_schema_version: Literal["load-history-v1", "load-history-v2"]
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
    source_schema_version: Literal["load-history-v1", "load-history-v2"]
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


class RecoveryStrengthSettings(StrictModel):
    tref_min: float
    sensitivity: float
    tau_days: float
    fatigue_cap: float


class RecoveryStrengthCurrent(StrictModel):
    readiness_percent: float
    residual_fatigue: float
    days_to_practical_recovery: float


class DailyStrengthRecovery(StrictModel):
    date: str
    readiness_before_percent: float
    readiness_after_percent: float
    residual_fatigue_after: float
    impulse: float
    effective_load: float
    tref_min: float


class RecoveryStrengthHistory(StrictModel):
    settings: RecoveryStrengthSettings
    current: RecoveryStrengthCurrent
    daily: list[DailyStrengthRecovery]


class WellnessFieldCoverage(StrictModel):
    field: Literal[
        "sleep_duration",
        "sleep_score",
        "sleep_quality",
        "resting_hr",
        "average_sleeping_hr",
        "hrv",
        "hrv_sdnn",
        "readiness",
        "respiration",
        "spo2",
        "fatigue",
        "stress",
        "mood",
        "motivation",
        "soreness",
        "injury",
    ]
    source_fields: list[str]
    present_days: int
    valid_days: int
    invalid_days: int
    coverage_percent: float


class WellnessCoverageDiagnostics(StrictModel):
    schema_version: Literal["wellness-coverage-v1"]
    period_start: str
    period_end: str
    calendar_days: int
    records_received: int
    days_with_any_recognized_data: int
    daily_presence_percent: float
    recognized_field_coverage_percent: float
    latest_observed_date: str | None
    freshness: Literal["fresh", "stale", "unknown"]
    fields: list[WellnessFieldCoverage]
    unresolved_canonical_inputs: list[
        Literal["soreness_legs", "soreness_upper", "pain", "illness"]
    ]
    model_status: Literal["diagnostic-only"]
    affects_recovery: Literal[False]


class RecoveryHistoryResponse(StrictModel):
    schema_version: Literal["recovery-history-v1"]
    athlete_id: str
    period_start: str
    period_end: str
    basis: Literal["load-only"]
    wellness_freshness: Literal["fresh", "stale", "unknown"]
    wellness_coverage_percent: float
    wellness_diagnostics: WellnessCoverageDiagnostics | None = None
    model: RecoveryModelMetadata
    settings: list[RecoveryZoneSettings]
    current: list[RecoveryZoneCurrent]
    daily: list[DailyRecovery]
    strength: RecoveryStrengthHistory | None = None


class AthleteSnapshot(StrictModel):
    """Persisted aggregate envelope; no raw streams or provider identifiers."""

    schema_version: Literal["athlete-snapshot-v1"]
    training_status: TrainingStatusResponse
    load_history: LoadHistoryResponse
    recovery_history: RecoveryHistoryResponse | None = None
    wellness_calendar: list[DailyWellnessSummary] = []


class DashboardViewResponse(StrictModel):
    schema_version: Literal["dashboard-view-v1"]
    generation_id: str | None
    revision: int
    analysis_as_of: str | None
    activated_at: str | None
    training_status: TrainingStatusResponse
    completed_work: CompletedWorkResponse
    load_history: LoadHistoryResponse
    recovery_history: RecoveryHistoryResponse | None
    volume_history: VolumeHistoryResponse
