"""Dated, athlete-owned execution preferences; historical doses are forbidden."""
from datetime import date, timedelta
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StructureModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TrainingStructureSession(StructureModel):
    slot: int = Field(default=1, ge=1, le=3)
    method_ids: list[Annotated[str, Field(min_length=1, max_length=180)]] = Field(min_length=1, max_length=2)
    sport: Literal["Run", "NordicSki", "RollerSki"]

    @model_validator(mode="after")
    def distinct_methods(self):
        if len(set(self.method_ids)) != len(self.method_ids):
            raise ValueError("The parts of a combined method must be distinct")
        return self


class TrainingStructureDay(StructureModel):
    offset: int = Field(ge=0, le=13)
    sessions: list[TrainingStructureSession] = Field(default_factory=list, max_length=3)


class TrainingStructureSourceDay(StructureModel):
    date: date
    plan_revision: int = Field(ge=1)


class TrainingStructureSource(StructureModel):
    start_date: date
    end_date: date
    plan_revisions: list[Annotated[int, Field(ge=1)]] = Field(min_length=1, max_length=14)
    days: list[TrainingStructureSourceDay] = Field(default_factory=list, max_length=14)

    @model_validator(mode="after")
    def bounded(self):
        if not 0 <= (self.end_date-self.start_date).days < 14:
            raise ValueError("Source must contain at most 14 consecutive dates")
        if len(set(self.plan_revisions)) != len(self.plan_revisions):
            raise ValueError("Source revisions must be distinct")
        if self.days and ([d.date for d in self.days] != [self.start_date+timedelta(days=n)
                for n in range((self.end_date-self.start_date).days+1)]
                or any(d.plan_revision not in self.plan_revisions for d in self.days)):
            raise ValueError("Source provenance must cover each date with a declared revision")
        return self


class TrainingStructureDirective(StructureModel):
    id: str = Field(min_length=1, max_length=80)
    name: str = Field(min_length=1, max_length=120)
    mode: Literal["FIX", "REPEAT", "SESSION"]
    start_date: date
    days: list[TrainingStructureDay] = Field(min_length=1, max_length=14)
    source: TrainingStructureSource | None = None

    @model_validator(mode="after")
    def coherent(self):
        if [d.offset for d in self.days] != list(range(len(self.days))):
            raise ValueError("Structure dates must be consecutive and ordered")
        if self.mode == "SESSION":
            if len(self.days) != 1 or not self.days[0].sessions:
                raise ValueError("Session preferences must identify one date and at least one session")
            slots = [s.slot for s in self.days[0].sessions]
            if slots != sorted(set(slots)):
                raise ValueError("Session preferences must have distinct, ordered slots")
        else:
            if len(self.days) not in {7, 14} or self.source is None:
                raise ValueError("A fixed or repeated block needs 7 or 14 sourced dates")
            if (self.source.end_date-self.source.start_date).days+1 != len(self.days):
                raise ValueError("Source and target blocks must have the same length")
            if any([s.slot for s in d.sessions] != list(range(1, len(d.sessions)+1)) for d in self.days):
                raise ValueError("Block sessions must preserve their contiguous order")
        return self
