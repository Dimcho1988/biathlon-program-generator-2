"""Athlete-owned method proposals; scientific coefficients are not editable here."""
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


PositiveWork = Annotated[float, Field(strict=True, gt=0, le=100000)]
RepeatCount = Annotated[int, Field(strict=True, ge=1, le=100)]
RecoverySeconds = Annotated[float, Field(strict=True, ge=0, le=86400)]


class PersonalMethod(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, str_strip_whitespace=True)

    id: str = Field(pattern=r"^PERSONAL-[A-Za-z0-9][A-Za-z0-9_.-]{0,89}$")
    name: str = Field(min_length=1, max_length=160)
    base_method_id: str = Field(min_length=1, max_length=120, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
    work_unit: Literal["seconds", "meters"] | None = None
    work_range: tuple[PositiveWork, PositiveWork] | None = None
    repetitions: tuple[RepeatCount, RepeatCount] | None = None
    recovery_seconds: tuple[RecoverySeconds, RecoverySeconds] | None = None
    notes: str | None = Field(default=None, max_length=1000)

    @model_validator(mode="after")
    def ordered_ranges(self):
        for field in ("work_range", "repetitions", "recovery_seconds"):
            value = getattr(self, field)
            if value is not None and value[0] > value[1]:
                raise ValueError(f"{field} minimum must not exceed maximum")
        return self
