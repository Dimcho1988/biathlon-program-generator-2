"""Explicit, revisioned inputs for the coach-reviewed training management pilot."""
from __future__ import annotations

from datetime import date, timedelta
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class IntervalDoseProfile(BaseModel):
    """A coach-resolved effort anchor, never inferred from peak HR."""
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    goal: Literal["AEROBIC_POWER", "THRESHOLD"] = "AEROBIC_POWER"
    zone: Literal["Z4", "Z5"]
    sport: Literal["Run", "NordicSki", "RollerSki"]
    continuous_capacity_min: float = Field(gt=0, le=60)
    assessed_on: date
    effort: str = Field(min_length=8, max_length=250)
    work_seconds: int = Field(ge=15, le=360)
    recovery_seconds: int = Field(ge=15, le=600)
    min_repetitions: int = Field(ge=2, le=20)
    max_repetitions: int = Field(ge=2, le=20)
    total_capacity_ratio: float = Field(gt=0, le=3)
    reserve_repetitions: int = Field(ge=1, le=4)
    target_speed_kmh: float | None = Field(default=None, gt=0, le=80)
    speed_basis: Literal["ACTUAL", "FLAT_EQUIVALENT"] = "ACTUAL"

    @model_validator(mode="after")
    def coherent(self):
        if self.goal == "THRESHOLD" and self.zone != "Z4":
            raise ValueError("A threshold profile must use Z4, never Z5")
        if self.min_repetitions > self.max_repetitions:
            raise ValueError("Invalid repetition range")
        if self.work_seconds >= self.continuous_capacity_min * 60:
            raise ValueError("One repetition must stay below continuous capacity")
        if self.min_repetitions * self.work_seconds > self.continuous_capacity_min * 60 * self.total_capacity_ratio:
            raise ValueError("The minimum method dose exceeds the total capacity budget")
        if not self.effort.strip():
            raise ValueError("Describe the effort associated with this capacity")
        return self


class CycleDirective(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    start_date: date
    end_date: date
    name: str = Field(default="Мезоцикъл", min_length=1, max_length=80)
    kind: Literal["BUILD", "MAINTAIN", "STRESS", "RECOVERY"] = "BUILD"
    accents: list[Literal["Z1", "Z2", "Z3", "Z4", "Z5", "STR"]] = Field(min_length=1, max_length=6)
    target_index: float = Field(ge=.5, le=2)
    volume_factor: float = Field(default=1, ge=.5, le=1.5)
    recovery_days: int = Field(default=7, ge=7, le=14)

    @model_validator(mode="after")
    def coherent(self):
        if not 0 <= (self.end_date-self.start_date).days < (7 if self.kind == "STRESS" else 42):
            raise ValueError("A stress microcycle lasts up to 7 days; other directives up to 6 weeks")
        if not self.name.strip():
            raise ValueError("Name the cycle directive")
        if len(set(self.accents)) != len(self.accents):
            raise ValueError("Duplicate accents")
        if self.kind == "STRESS" and self.target_index <= 1:
            raise ValueError("Enter an explicit stress target above 1")
        if self.kind == "RECOVERY" and (self.target_index > 1 or self.volume_factor > 1):
            raise ValueError("Recovery must not increase the target or volume")
        return self


class PlanningControls(BaseModel):
    """One set of executable controls; ratios are coach goals, not safety limits."""
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    sessions_per_week: int = Field(default=7, ge=1, le=21)
    sessions_by_day: tuple[int, int, int, int, int, int, int] | None = None
    threshold_days: list[int] = Field(default_factory=list, max_length=7)
    threshold_method: Literal["AUTO", "CONTINUOUS", "INTERVALS"] = "AUTO"
    double_threshold_days: list[int] = Field(default_factory=list, max_length=3)
    double_threshold_components: list[Literal["Z3", "Z4"]] = Field(default_factory=lambda: ["Z3"], min_length=1, max_length=2)
    intensity_days: list[int] = Field(default_factory=list, max_length=7)
    strength_days: list[int] = Field(default_factory=list, max_length=7)
    long_session_day: int | None = Field(default=None, ge=0, le=6)
    max_strength_sessions: int = Field(default=2, ge=0, le=3)
    training_sports: list[Literal["Run", "NordicSki", "RollerSki"]] = Field(default_factory=list, max_length=3)
    weekly_target_hours: float | None = Field(default=None, gt=0, le=42)
    history_gap_days: int = Field(default=10, ge=5, le=14)
    automatic_intervals: bool = True
    capacity_policy: Literal["OBSERVED_ONLY", "MODEL_WITH_PRIOR"] = "MODEL_WITH_PRIOR"
    mesocycle_anchor: date | None = None
    wave: list[float] = Field(default_factory=lambda: [.96, 1.04, 1.10, .78], min_length=2, max_length=6)
    accent_mode: Literal["AUTO", "MANUAL", "HYBRID"] = "AUTO"
    accent_limit: int = Field(default=2, ge=1, le=6)
    accents: list[Literal["Z1", "Z2", "Z3", "Z4", "Z5", "STR"]] = Field(default_factory=list, max_length=6)
    accent_index: float = Field(default=1.1, ge=.5, le=2)
    maintenance_index: float = Field(default=1, ge=.5, le=1.2)
    cycles: list[CycleDirective] = Field(default_factory=list, max_length=52)

    @model_validator(mode="after")
    def coherent(self):
        for days in (self.intensity_days, self.strength_days, self.threshold_days, self.double_threshold_days):
            if len(set(days)) != len(days) or any(type(d) is not int or not 0 <= d <= 6 for d in days):
                raise ValueError("Choose distinct weekdays")
        if self.sessions_by_day is not None and any(not 0 <= v <= 3 for v in self.sessions_by_day):
            raise ValueError("Choose zero to three sessions per day")
        if len(set(self.double_threshold_components)) != len(self.double_threshold_components):
            raise ValueError("Duplicate double-threshold components")
        if self.sessions_per_week < 2*len(self.double_threshold_days) or (self.sessions_by_day is not None and any(self.sessions_by_day[d] < 2 for d in self.double_threshold_days)):
            raise ValueError("Double threshold requires two daily and weekly session slots")
        if any(not .5 <= v <= 1.5 for v in self.wave) or self.wave[-1] >= 1:
            raise ValueError("The final week must unload; wave values must be .5 to 1.5")
        if len(set(self.training_sports)) != len(self.training_sports) or len(set(self.accents)) != len(self.accents):
            raise ValueError("Duplicate sports or accents")
        if len(self.accents) > self.accent_limit or (self.accent_mode != "AUTO" and not self.accents):
            raise ValueError("Choose accents within the configured limit")
        ordered = sorted(self.cycles, key=lambda c: c.start_date)
        from datetime import timedelta
        if any(a.end_date + timedelta(days=a.recovery_days if a.kind == "STRESS" else 0) >= b.start_date for a,b in zip(ordered, ordered[1:])):
            raise ValueError("Cycle directives and mandatory post-stress recovery must not overlap")
        return self


class LoadProgression(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    enabled: bool = True
    low_volume_annual_percent: float = Field(default=30., ge=0, le=30)
    upper_volume_annual_percent: float = Field(default=10., ge=0, le=30)
    ceiling_ratio: float = Field(default=1.3, gt=1, le=1.3)
    precompetition_factor: float = Field(default=.15, ge=0, le=1)
    competition_factor: float = Field(default=.05, ge=0, le=1)
    max_dose_fraction: float = Field(default=.8, ge=.5, le=.8)
    feedback_enabled: bool = True
    training_level: Literal["AUTO", "LOW", "MEDIUM", "HIGH"] = "AUTO"
    component_reference_positions: dict[Literal["Z1", "Z2", "Z3", "Z4", "Z5"], float] = Field(default_factory=dict)
    reference_revision: int = Field(default=0, ge=0, le=10000)

    @model_validator(mode="after")
    def declining_rate(self):
        if any(not 0 <= p <= 1 for p in self.component_reference_positions.values()):
            raise ValueError("Component reference positions must be between zero and one")
        if self.upper_volume_annual_percent > self.low_volume_annual_percent:
            raise ValueError("The growth rate must decrease with volume")
        if self.competition_factor > self.precompetition_factor:
            raise ValueError("Competition growth cannot exceed precompetition growth")
        return self


class RaceDurationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    discipline: str = Field(min_length=1, max_length=100)
    sport: Literal["Run", "NordicSki"]
    race_duration_min: float | None = Field(default=None, gt=0, le=1440)


class ManagementProfile(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    schema_version: Literal["management-profile-v1"] = "management-profile-v1"
    sport: Literal["Run", "NordicSki"]
    actual_sport: Literal["Run", "NordicSki", "RollerSki"]
    discipline: str = Field(min_length=1, max_length=100)
    age_years: int | None = Field(default=None, ge=10, le=100)
    training_experience_years: float | None = Field(default=None, ge=0, le=85)
    race_duration_min: float | None = Field(default=None, gt=0, le=1440)
    program_start: date
    program_end: date
    horizon_mode: Literal["AUTO_CALENDAR", "MANUAL"] = "AUTO_CALENDAR"
    availability_mode: Literal["AUTO_HISTORY", "MANUAL"] | None = None
    training_days: list[int] | None = None
    available_minutes: tuple[float, float, float, float, float, float, float]
    recent_weekly_hours: tuple[float, float, float, float] | None = None
    reentry_days: int | None = Field(default=None, ge=0, le=21)
    taper_days: int = Field(default=7, ge=0, le=21)
    max_key_sessions_per_week: int = Field(default=2, ge=0, le=8)
    building_fraction: float = Field(default=.5, ge=.5, le=.6)
    maintenance_fraction: float = Field(default=.3, ge=.3, le=.4)
    reentry_fraction: float = Field(default=.4, ge=.4, le=.5)
    recovery_session_cap_min: float = Field(default=30, ge=5, le=45)
    allow_expert_fallback: bool = True
    # New optional fields preserve stored v1 profiles. The activation freezes
    # these rules; editing them subsequently requires a fresh approval.
    adaptation_mode: Literal["AUTO", "REVIEW"] = "AUTO"
    auto_import_enabled: bool = True
    progression_percent: float = Field(default=5, ge=0, le=10)
    load_progression: LoadProgression | None = None
    component_targets_weekly: dict[Literal["Z1", "Z2", "Z3", "Z4", "Z5", "STR"], float] = Field(default_factory=dict)
    interval_profiles: list[IntervalDoseProfile] = Field(default_factory=list, max_length=2)
    strength_enabled: bool = False
    strength_circuits: int = Field(default=2, ge=2, le=3)
    transition_days: int = Field(default=0, ge=0, le=28)
    planning_controls: PlanningControls | None = None

    @model_validator(mode="after")
    def coherent(self):
        if not 0 <= (self.program_end - self.program_start).days <= 365:
            raise ValueError("Choose a planning period of 1 to 366 days")
        if any(not 0 <= value <= 360 for value in self.available_minutes):
            raise ValueError("Daily availability must be 0 to 360 minutes")
        if self.recent_weekly_hours is not None and any(
            not 0 <= value <= 80 for value in self.recent_weekly_hours
        ):
            raise ValueError("Provide four weekly volumes between 0 and 80 hours")
        if self.sport == "Run" and self.actual_sport != "Run":
            raise ValueError("Running preparation requires the running means in this pilot")
        if self.age_years is not None and self.training_experience_years is not None:
            if self.training_experience_years > self.age_years:
                raise ValueError("Training experience cannot exceed age")
        if not self.discipline.strip() or self.discipline != self.discipline.strip():
            raise ValueError("Enter a discipline without surrounding whitespace")
        if self.training_days is not None and (len(set(self.training_days)) != len(self.training_days) or any(type(d) is not int or not 0 <= d <= 6 for d in self.training_days)):
            raise ValueError("Choose distinct training weekdays")
        if self.planning_controls:
            c = self.planning_controls
            if c.training_sports and self.actual_sport not in c.training_sports:
                raise ValueError("The primary means must be included")
            if self.sport == "Run" and any(s != "Run" for s in c.training_sports):
                raise ValueError("Choose running means for a running programme")
            from biathlon.planning_history import availability
            resolved_available = availability(self.model_dump(mode="json"))
            if any(resolved_available[d] == 0 for d in [*c.intensity_days, *c.strength_days, *c.threshold_days, *c.double_threshold_days, *([] if c.long_session_day is None else [c.long_session_day])]):
                raise ValueError("A preferred session day cannot be a rest day")
            if c.double_threshold_days and ((self.age_years or 0) < 18 or (self.training_experience_years or 0) < 1):
                raise ValueError("Double threshold requires known adult age and at least one training year")
            if self.max_key_sessions_per_week < 2*len(c.double_threshold_days):
                raise ValueError("Each double-threshold day counts as two key sessions")
            control_end = self.program_end if self.horizon_mode == "MANUAL" else self.program_start + timedelta(days=365)
            if any(x.start_date < self.program_start or x.end_date > control_end for x in c.cycles):
                raise ValueError("Cycle directives must lie within the programme")
            if any(x.kind == "STRESS" and (control_end-x.end_date).days < x.recovery_days for x in c.cycles):
                raise ValueError("Keep the entire post-stress unloading period within the programme")
        if any(not 0 <= value <= 3000 for value in self.component_targets_weekly.values()):
            raise ValueError("Component goals must be finite weekly equivalent minutes, 0 to 3000")
        if len({p.zone for p in self.interval_profiles}) != len(self.interval_profiles):
            raise ValueError("Configure at most one complete interval profile per zone")
        if any(p.sport != self.actual_sport for p in self.interval_profiles):
            raise ValueError("Interval capacity must belong to the actual means")
        if self.interval_profiles and (self.age_years is None or self.age_years < 18 or
                                      self.training_experience_years is None or self.training_experience_years < 1):
            raise ValueError("These interval profiles require an adult with at least one year of training; youth/novice profiles require separate rules")
        return self


class ManagementProfileWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")
    profile: ManagementProfile
    expected_revision: int = Field(ge=0, strict=True)


class ManagementGenerateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    start_date: date
    expected_profile_revision: int = Field(ge=1, strict=True)
    expected_draft_revision: int = Field(default=0, ge=0, strict=True)


class ManagementActivateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    start_date: date
    draft_revision: int = Field(ge=1, strict=True)
    expected_revision: int = Field(ge=0, strict=True)


class ManagementPlanAction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["PAUSE", "RESUME", "REFRESH", "APPROVE"]
    expected_revision: int = Field(ge=1, strict=True)


class ManagementDayAction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    date: date
    action: Literal["SKIP", "REST", "CLEAR"]
    expected_revision: int = Field(ge=1, strict=True)
    note: str = Field(default="", max_length=250)
