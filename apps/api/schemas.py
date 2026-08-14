"""Versioned HTTP schemas, kept separate from the physiology model."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class HealthResponse(StrictModel):
    status: Literal["ok"]


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
