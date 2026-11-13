"""Explicit intentions only: no copied scientific doses or caller-owned provenance."""
from datetime import date
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from .personal_method_schemas import PersonalMethod


class ProposalModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, str_strip_whitespace=True)


class PersonalMethodProposal(ProposalModel):
    action: Literal["PERSONAL_METHOD"]
    method: PersonalMethod


class RemovePersonalMethod(ProposalModel):
    action: Literal["REMOVE_PERSONAL_METHOD"]
    method_id: str = Field(min_length=1, max_length=100)


class StructureProposal(ProposalModel):
    action: Literal["FIX_STRUCTURE", "REPEAT_STRUCTURE"]
    source_start: date
    source_end: date
    target_start: date
    name: str | None = Field(default=None, min_length=1, max_length=120)
    source_revisions: list[Annotated[int, Field(strict=True, ge=1)]] | None = Field(default=None, min_length=1, max_length=14)
    expected_plan_revision: int | None = Field(default=None, strict=True, ge=0)


class SessionMethodProposal(ProposalModel):
    action: Literal["SESSION_METHOD"]
    target_date: date
    session_index: int = Field(strict=True, ge=1, le=3)
    method_ids: list[Annotated[str, Field(min_length=1, max_length=180)]] = Field(min_length=1, max_length=2)
    sport: Literal["Run", "NordicSki", "RollerSki"] | None = None
    name: str | None = Field(default=None, min_length=1, max_length=120)
    expected_plan_revision: int | None = Field(default=None, strict=True, ge=0)


class RemoveStructure(ProposalModel):
    action: Literal["REMOVE_STRUCTURE"]
    directive_id: str = Field(min_length=1, max_length=80)


Proposal = Annotated[PersonalMethodProposal | RemovePersonalMethod | StructureProposal
    | SessionMethodProposal | RemoveStructure, Field(discriminator="action")]
PROPOSALS = TypeAdapter(Proposal)


class ProposalPreviewRequest(ProposalModel):
    proposal: Proposal


class ProposalApplyRequest(ProposalPreviewRequest):
    expected_revision: int = Field(strict=True, ge=1)
    operation_id: UUID
