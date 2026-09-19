"""Versioned editable scientific inputs and Recovery v2 response contract."""
from datetime import date
from uuid import UUID
from typing import Literal, Annotated
from pydantic import BaseModel, ConfigDict, Field, model_validator
from biathlon.recovery_v2 import ZONES, defaults

Zone = Literal["Z1","Z2","Z3","Z4","Z5","STR"]
Nonnegative = Annotated[float, Field(ge=0, allow_inf_nan=False)]

class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

class ZoneConfig(Strict):
    duration_coefficient: float = Field(default=1,ge=.1,le=5)
    shape: float = Field(default=1,ge=1,le=10)
    sensitivity: float = Field(default=1,ge=.05,le=3)
    # Retain the persisted key; v2.2 uses it as a permanent daily base addition.
    initial_daily_min: float = Field(default=20,ge=.1,le=600)

class RecoveryConfigInput(Strict):
    expected_revision: int = Field(default=0,ge=0,strict=True)
    zones: dict[Zone,ZoneConfig]

    @model_validator(mode="after")
    def complete(self):
        if set(self.zones)!=set(ZONES):
            raise ValueError("All five zones and strength are required")
        return self

class SpeedTestInput(Strict):
    activity_ref: str = Field(pattern=r"^act_[a-f0-9]{32}$")
    start_s: int = Field(ge=0,le=172800,strict=True)
    duration_s: int = Field(ge=11,le=43516,strict=True)
    test_mode: Literal["STRICT","EXPLORATORY"] = "STRICT"
    maximal: bool = Field(strict=True)
    exploratory_confirmed: bool = Field(default=False,strict=True)
    comparable: Literal[True]
    enabled: bool = True
    use_for_cs: bool = False
    conditions: str = Field(min_length=3,max_length=400)
    expected_revision: int = Field(default=0,ge=0,strict=True)
    expected_source_run_key: str | None = Field(default=None,pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def attest_test_mode(self):
        if self.test_mode == "STRICT" and not self.maximal:
            raise ValueError("A strict test requires a maximal continuous effort")
        if self.test_mode == "EXPLORATORY":
            if not self.exploratory_confirmed:
                raise ValueError("Confirm exploratory calibration")
            if self.maximal or self.use_for_cs:
                raise ValueError("Exploratory records are not continuous maximal or critical-speed tests")
        return self

class ManualSpeedTestInput(Strict):
    test_id: UUID
    name: str = Field(min_length=1,max_length=120)
    day: date
    sport: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_]{0,39}$")
    duration_s: float = Field(ge=10.8,le=43516,strict=True)
    distance_m: float | None = Field(default=None,gt=0,le=1000000,strict=True)
    speed_kmh: float | None = Field(default=None,gt=0,le=150,strict=True)
    maximal: Literal[True]
    comparable: Literal[True]
    flat_terrain: Literal[True]
    enabled: bool = Field(default=True,strict=True)
    use_for_cs: bool = Field(default=False,strict=True)
    conditions: str = Field(min_length=3,max_length=400)
    expected_revision: int = Field(default=0,ge=0,strict=True)

    @model_validator(mode="after")
    def measurements(self):
        self.name=self.name.strip()
        self.conditions=self.conditions.strip()
        if not self.name or len(self.conditions)<3:
            raise ValueError("Describe the manual test and its conditions")
        if (self.distance_m is None)==(self.speed_kmh is None):
            raise ValueError("Enter either distance or speed")
        speed=self.speed_kmh if self.speed_kmh is not None else self.distance_m/self.duration_s*3.6
        if not 0 < speed <= 150:
            raise ValueError("Manual test speed must be between 0 and 150 km/h")
        if self.use_for_cs and not 120 <= self.duration_s <= 1200:
            raise ValueError("Critical speed tests must last 2 to 20 minutes")
        return self

class Baseline(Strict):
    baseline_daily_min: float = Field(gt=0)
    baseline_raw_daily_min: Nonnegative | None
    history_days: int = Field(ge=0,le=40)
    baseline_source: Literal["NO_HISTORY","NO_ZONE_LOAD","SHORT_HISTORY","SPARSE_ZONE_HISTORY","PERSONAL"]

class RecoveryCurrentV2(Baseline):
    zone: Zone
    readiness_percent: float = Field(ge=0,le=100)
    residual_fatigue: Nonnegative
    days_to_practical_recovery: Nonnegative

class RecoveryDailyV2(Baseline):
    date: str
    zone: Zone
    readiness_before_percent: float = Field(ge=0,le=100)
    readiness_after_percent: float = Field(ge=0,le=100)
    residual_fatigue_after: Nonnegative
    impulse: Nonnegative
    effective_load: Nonnegative
    isolated_days_to_90: Nonnegative
    residual_fatigue_now: Nonnegative | None = None

class RecoveryForecastV2(Strict):
    zone: Zone
    days: Nonnegative
    readiness_percent: float = Field(ge=0,le=100)

class RecoveryMetadataV2(Strict):
    algorithm_version: Literal["recovery-daily-e-biexponential-v2.2"]
    parameter_version: str
    parameter_fingerprint: str
    practical_full_recovery_percent: Literal[90]

class RecoveryHistoryV2(Strict):
    schema_version: Literal["recovery-history-v2"] = "recovery-history-v2"
    athlete_id: str
    period_start: str
    period_end: str
    as_of: str
    basis: Literal["load-only"] = "load-only"
    time_resolution: Literal["calendar-day"] = "calendar-day"
    ready_threshold_percent: Literal[90] = 90
    model: RecoveryMetadataV2
    config_revision: int
    settings: dict[Zone,ZoneConfig]
    current: list[RecoveryCurrentV2]
    daily: list[RecoveryDailyV2]
    forecast: list[RecoveryForecastV2]
    source_as_of: str
    source_stale: bool
    warnings: list[str]
    wellness_diagnostics: dict | None = None

def initial_settings():
    return RecoveryConfigInput(zones=defaults()).model_dump(mode="json")["zones"]
