"""Shared HTTP authentication and repository construction."""

import os
import re
from urllib.parse import urlsplit
from fastapi import HTTPException
from .cloud import service_token_valid
from .oauth_store import PersistentStoreConfigurationError, SupabasePilotRepository
from .http_runtime import store_client

ATHLETE_ALIAS_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{2,63}$")


def model_alias(authorization, athlete_alias):
    authorize(authorization)
    alias = validated_alias(athlete_alias, fallback=False)
    if not alias:
        raise HTTPException(401, "Athlete session is required")
    return alias


def web_base_url() -> str:
    destination = os.environ.get("ONFLOWS_WEB_BASE_URL", "").strip().rstrip("/")
    parsed = urlsplit(destination)
    if (
        parsed.scheme != "https"
        or not parsed.netloc
        or parsed.path not in ("", "/")
        or parsed.query
        or parsed.fragment
    ):
        raise HTTPException(status_code=503, detail="Web destination is not configured")
    return destination


def authorize(authorization: str | None) -> None:
    expected = os.environ.get("ONFLOWS_SERVICE_TOKEN", "")
    provided = authorization[7:] if authorization and authorization.startswith("Bearer ") else None
    if not service_token_valid(provided, expected):
        raise HTTPException(
            status_code=401,
            detail="Unauthorized",
            headers={"WWW-Authenticate": "Bearer"},
        )


def repository() -> SupabasePilotRepository:
    try:
        return SupabasePilotRepository.from_environment(client=store_client())
    except PersistentStoreConfigurationError as exc:
        raise HTTPException(
            status_code=503,
            detail="Persistent server storage is not configured",
        ) from exc


def pilot_alias() -> str:
    alias = os.environ.get("ONFLOWS_ATHLETE_ALIAS", "").strip()
    if not alias:
        raise HTTPException(
            status_code=503, detail="Pilot athlete configuration is incomplete"
        )
    return alias


def validated_alias(alias: str | None, *, fallback: bool = True) -> str | None:
    candidate = alias.strip() if alias else (pilot_alias() if fallback else None)
    if candidate is not None and not ATHLETE_ALIAS_PATTERN.fullmatch(candidate):
        raise HTTPException(status_code=400, detail="Athlete session is invalid")
    return candidate
