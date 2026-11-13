"""Private learning observations; trusted web transport enforces recovery access."""
from datetime import date
from typing import Annotated
from fastapi import APIRouter, Header, HTTPException, Query, Response
from .. import dependencies, prepared_learning
from ..oauth_store import PersistentStoreFailure

router = APIRouter()


@router.get("/api/v2/athlete/learning")
def learning_history(response: Response, days: int = Query(default=30,ge=14,le=366), end: date | None = Query(default=None),
    authorization: Annotated[str | None, Header()] = None,
    athlete_alias: Annotated[str | None, Header(alias="X-OnFlows-Athlete-Alias")] = None):
    alias = dependencies.model_alias(authorization, athlete_alias)
    if days not in {14,30,60,90,366}:
        raise HTTPException(422, "Изберете поддържан период на графиката.")
    response.headers["Cache-Control"] = "no-store"
    try:
        return prepared_learning.read_view(dependencies.repository(), alias, days=days, **({"end":end} if end is not None else {}))
    except PersistentStoreFailure as exc:
        raise HTTPException(503, "Learning observations are temporarily unavailable") from exc
