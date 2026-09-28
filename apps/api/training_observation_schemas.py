"""Optional training observations, independent of canonical load and stress."""
from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Zone = Literal["Z1", "Z2", "Z3", "Z4", "Z5"]


class ObservationModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class LactateRange(ObservationModel):
    low_mmol: float | None = Field(default=None, ge=0, le=40)
    high_mmol: float | None = Field(default=None, gt=0, le=40)

    @model_validator(mode="after")
    def ordered(self):
        if self.low_mmol is None and self.high_mmol is None:
            raise ValueError("Enter at least one lactate reference boundary")
        if self.low_mmol is not None and self.high_mmol is not None and self.low_mmol >= self.high_mmol:
            raise ValueError("The lactate reference boundaries must increase")
        return self


class LactateStage(ObservationModel):
    duration_min: float = Field(default=4, gt=0, le=60)
    hr_bpm: float = Field(gt=0, le=250)
    speed_kmh: float | None = Field(default=None, gt=0, le=150)
    lactate_mmol: float = Field(gt=0, le=40)


class LactateProfile(ObservationModel):
    sport: Literal["Run", "NordicSki", "RollerSki"]
    source: Literal["MANUAL", "TEST"] = "MANUAL"
    assessed_on: date
    device: str = Field(default="", max_length=80)
    protocol: str = Field(default="", max_length=250)
    note: str = Field(default="", max_length=500)
    zone_ranges: dict[Zone, LactateRange] = Field(default_factory=dict)
    stages: list[LactateStage] = Field(default_factory=list, max_length=30)

    @model_validator(mode="after")
    def supported(self):
        if self.source == "MANUAL" and not self.zone_ranges:
            raise ValueError("Enter individual zone references")
        if self.source == "TEST":
            if len(self.stages) < 3 or not self.protocol.strip():
                raise ValueError("A lactate test needs a protocol and at least three stages")
            if any(a.hr_bpm >= b.hr_bpm for a, b in zip(self.stages, self.stages[1:])):
                raise ValueError("Enter test stages in increasing heart-rate order")
        elif self.stages:
            raise ValueError("Test stages need the TEST source")
        return self


class NeuromuscularProfile(ObservationModel):
    enabled: bool = False
    mode: Literal["PROGRESSIVE_FINISH", "SHORT_SPRINT"] = "PROGRESSIVE_FINISH"
    repetitions: int = Field(default=4, ge=2, le=20)
    work_seconds: int = Field(default=10, ge=5, le=20)
    recovery_seconds: int = Field(default=120, ge=30, le=600)
    days: list[int] = Field(default_factory=lambda: [1, 4], min_length=1, max_length=7)

    @model_validator(mode="after")
    def structure(self):
        if len(set(self.days)) != len(self.days) or any(type(d) is not int or not 0 <= d <= 6 for d in self.days):
            raise ValueError("Choose distinct weekdays for accelerations")
        if self.mode == "SHORT_SPRINT" and self.work_seconds > 10:
            raise ValueError("This short sprint profile supports at most ten seconds per repetition")
        if self.recovery_seconds < 4 * self.work_seconds:
            raise ValueError("Neuromuscular repetitions require the configured full recovery, at least four times the work")
        return self


class LactateSample(ObservationModel):
    value_mmol: float = Field(gt=0, le=40)
    zone: Zone | None = None
    after: Literal["REPETITION", "BLOCK", "SESSION"] = "SESSION"
    repetition: int | None = Field(default=None, ge=1, le=200)
    delay_seconds: int | None = Field(default=None, ge=0, le=3600)
    planned_low_mmol: float | None = Field(default=None, ge=0, le=40)
    planned_high_mmol: float | None = Field(default=None, gt=0, le=40)
    comparison_confirmed: bool = False
    note: str = Field(default="", max_length=250)

    @model_validator(mode="after")
    def context(self):
        if (self.after == "REPETITION") != (self.repetition is not None):
            raise ValueError("A repetition sample needs its repetition number")
        low, high = self.planned_low_mmol, self.planned_high_mmol
        if low is not None and high is not None and low >= high:
            raise ValueError("Invalid planned lactate range")
        if self.comparison_confirmed and (low is None and high is None or self.delay_seconds is None):
            raise ValueError("Comparison needs a plan reference and the sampling delay")
        return self


class NeuromuscularReport(ObservationModel):
    repetitions: int = Field(ge=0, le=200)
    work_seconds: float = Field(ge=0, le=3600)
    peak_speed_kmh: float | None = Field(default=None, gt=0, le=150)
    planned_repetitions: int | None = Field(default=None, ge=0, le=200)
    planned_work_seconds: float | None = Field(default=None, ge=0, le=3600)
    note: str = Field(default="", max_length=250)

    @model_validator(mode="after")
    def completion(self):
        if (self.repetitions == 0) != (self.work_seconds == 0):
            raise ValueError("Repetitions and total sprint time must agree")
        if self.repetitions == 0 and self.peak_speed_kmh is not None:
            raise ValueError("No peak sprint speed without sprint work")
        return self
