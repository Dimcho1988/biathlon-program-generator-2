"""Explicit, revisioned inputs for the coach-reviewed training management pilot."""
from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


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
    available_minutes: tuple[float, float, float, float, float, float, float]
    recent_weekly_hours: tuple[float, float, float, float] | None = None
    reentry_days: int | None = Field(default=None, ge=0, le=21)
    taper_days: int = Field(default=7, ge=0, le=21)
    max_key_sessions_per_week: int = Field(default=2, ge=0, le=3)
    building_fraction: float = Field(default=.5, ge=.5, le=.6)
    maintenance_fraction: float = Field(default=.3, ge=.3, le=.4)
    reentry_fraction: float = Field(default=.4, ge=.4, le=.5)
    recovery_session_cap_min: float = Field(default=30, ge=5, le=45)
    allow_expert_fallback: bool = True

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
