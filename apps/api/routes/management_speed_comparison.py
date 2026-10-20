"""Athlete-scoped, explicitly requested plan/execution comparison."""
from datetime import date
from typing import Annotated, Literal

from fastapi import APIRouter, Header, HTTPException, Query, Response

from .. import dependencies
from ..component_load_context import ComponentCapacityUnavailable
from ..management_projection import public_management
from ..oauth_store import PersistentStoreFailure

router = APIRouter()


@router.get("/api/v2/athlete/management/speed-comparison")
def speed_comparison(
    response: Response,
    kind: Literal["DRAFT", "ACTIVE"],
    revision: Annotated[int, Query(ge=1, le=9007199254740991)],
    start_date: date | None = None,
    authorization: Annotated[str | None, Header()] = None,
    athlete_alias: Annotated[str | None, Header(alias="X-OnFlows-Athlete-Alias")] = None,
):
    # End-user plan + recovery grants are checked by the same-origin web proxy;
    # the API boundary accepts only its server token and explicit athlete alias.
    alias = dependencies.model_alias(authorization, athlete_alias)
    response.headers["Cache-Control"] = "no-store"
    try:
        # Keep optional speed/history processing outside the API import path,
        # as with the existing speed report routes.
        from .. import management_speed_comparison
        return public_management(management_speed_comparison.view(dependencies.repository(), alias,
            kind=kind, revision=revision, start_date=start_date))
    except ComponentCapacityUnavailable as exc:
        raise HTTPException(409, exc.user_message) from exc
    except PersistentStoreFailure as exc:
        raise HTTPException(503, "Speed plan comparison is temporarily unavailable") from exc
