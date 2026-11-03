"""Private learning observations; trusted web transport enforces recovery access."""
from typing import Annotated
from fastapi import APIRouter, Header, HTTPException, Query, Response
from .. import dependencies, learning_view
from ..oauth_store import PersistentStoreFailure

router = APIRouter()


@router.get("/api/v2/athlete/learning")
def learning_history(response: Response, days: int = Query(default=90, ge=7, le=90),
    authorization: Annotated[str | None, Header()] = None,
    athlete_alias: Annotated[str | None, Header(alias="X-OnFlows-Athlete-Alias")] = None):
    alias = dependencies.model_alias(authorization, athlete_alias)
    response.headers["Cache-Control"] = "no-store"
    try:
        return learning_view.view(dependencies.repository(), alias, days=days)
    except PersistentStoreFailure as exc:
        raise HTTPException(503, "Learning observations are temporarily unavailable") from exc
