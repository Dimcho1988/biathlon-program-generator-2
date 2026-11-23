"""OAuth for the staging ChatGPT client, scoped to one user-selected athlete."""
import base64
from datetime import datetime, timezone
from hashlib import sha256
import os
import re
import secrets
from urllib.parse import urlencode, parse_qsl
from uuid import UUID

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from .. import assistant_commands, dependencies

router = APIRouter()
ISSUER = "https://onflows-api-staging.onrender.com"
WEB = "https://onflows-web-staging.onrender.com"
RESOURCE = ISSUER + "/mcp"
METADATA = ISSUER + "/.well-known/oauth-protected-resource/mcp"
# Registered public ChatGPT client with the stable callback for RFC 9207 issuers.
CLIENT = "https://chatgpt.com/oauth/client.json"
CALLBACK = "https://chatgpt.com/connector_platform_oauth_redirect"
READ, WRITE = "plan.read", "calendar.write"
NO_STORE = {"Cache-Control": "no-store", "Pragma": "no-cache"}
CHALLENGE = re.compile(r"[A-Za-z0-9_-]{43}")
VERIFIER = re.compile(r"[A-Za-z0-9._~-]{43,128}")


def enabled():
    return os.getenv("ONFLOWS_WEB_BASE_URL", "").rstrip("/") == WEB


def require_enabled():
    if not enabled():
        raise HTTPException(404, "Integration unavailable")


def digest(value):
    return sha256(value.encode("ascii")).hexdigest()


def pkce(value):
    return base64.urlsafe_b64encode(sha256(value.encode("ascii")).digest()).decode().rstrip("=")


def read_grant(repo, token):
    if not token or not CHALLENGE.fullmatch(token):
        return None
    rows = repo._json(repo._request("GET", "/onflows_chatgpt_grants", params={
        "select": "actor,athlete_alias,scopes,onflows_user_athletes(onflows_profiles(display_name))", "access_hash": "eq." + digest(token),
        "access_until": "gt." + datetime.now(timezone.utc).isoformat(), "limit": "1",
    }))
    return rows[0] if len(rows) == 1 else None


@router.get("/.well-known/oauth-protected-resource/mcp")
@router.get("/.well-known/oauth-protected-resource")
def protected_metadata():
    require_enabled()
    return {"resource": RESOURCE, "authorization_servers": [ISSUER],
            "scopes_supported": [READ, WRITE], "bearer_methods_supported": ["header"]}


@router.get("/.well-known/oauth-authorization-server")
def authorization_metadata():
    require_enabled()
    return {"issuer": ISSUER, "authorization_endpoint": WEB + "/connect/chatgpt",
            "token_endpoint": ISSUER + "/oauth/chatgpt/token",
            "response_types_supported": ["code"], "grant_types_supported": ["authorization_code", "refresh_token"],
            "authorization_response_iss_parameter_supported": True, "client_id_metadata_document_supported": True,
            "token_endpoint_auth_methods_supported": ["none"], "code_challenge_methods_supported": ["S256"],
            "scopes_supported": [READ, WRITE]}


class Consent(BaseModel):
    actor: UUID
    athlete_alias: str = Field(max_length=64)
    params: dict[str, str]
    allow_write: bool = False
    approve: bool


def validate_authorization(params):
    if (params.get("client_id") != CLIENT or params.get("redirect_uri") != CALLBACK
            or params.get("resource") != RESOURCE or params.get("response_type") != "code"
            or params.get("code_challenge_method") != "S256"
            or not CHALLENGE.fullmatch(params.get("code_challenge", ""))
            or len(params.get("state", "")) > 2048):
        raise HTTPException(400, "Невалидна заявка за свързване с ChatGPT.")
    scopes = set(params.get("scope", READ).split())
    if READ not in scopes or scopes - {READ, WRITE}:
        raise HTTPException(400, "Неподдържани права.")
    return scopes


@router.post("/api/v2/integrations/chatgpt/consent")
def consent(body: Consent, authorization: str | None = Header(default=None)):
    require_enabled()
    dependencies.authorize(authorization)
    scopes = validate_authorization(body.params)
    callback = {"iss": ISSUER}
    if "state" in body.params:
        callback["state"] = body.params["state"]
    if body.approve:
        alias = dependencies.validated_alias(body.athlete_alias, fallback=False)
        repo = dependencies.repository()
        rights = assistant_commands.access(repo, alias, body.actor)
        if not rights["can_view_plan"]:
            raise HTTPException(403, "Нямате право за преглед на плана.")
        granted = [READ]
        if WRITE in scopes and body.allow_write:
            if not rights["can_edit_plan"]:
                raise HTTPException(403, "Нямате право за редакция на календара.")
            granted.append(WRITE)
        code = secrets.token_urlsafe(32)
        repo._request("POST", "/onflows_chatgpt_grants", json={"actor": str(body.actor),
            "athlete_alias": alias, "scopes": granted, "code_hash": digest(code),
            "challenge": body.params["code_challenge"]})
        callback["code"] = code
    else:
        callback["error"] = "access_denied"
    return JSONResponse({"location": CALLBACK + "?" + urlencode(callback)}, headers=NO_STORE)


@router.post("/api/v2/integrations/chatgpt/disconnect/{actor}")
def disconnect(actor: UUID, authorization: str | None = Header(default=None)):
    require_enabled()
    dependencies.authorize(authorization)
    dependencies.repository()._request("DELETE", "/onflows_chatgpt_grants", params={"actor": f"eq.{actor}"})
    return JSONResponse({"revoked": True}, headers=NO_STORE)


def oauth_error(error):
    return JSONResponse({"error": error}, status_code=400, headers=NO_STORE)


@router.post("/oauth/chatgpt/token")
async def token(request: Request):
    from starlette.concurrency import run_in_threadpool
    require_enabled()
    if request.headers.get("content-type", "").split(";")[0].strip() != "application/x-www-form-urlencoded":
        return oauth_error("invalid_request")
    raw = bytearray()
    async for chunk in request.stream():
        if len(raw) + len(chunk) > 16384:
            return oauth_error("invalid_request")
        raw.extend(chunk)
    try:
        items = parse_qsl(raw.decode("ascii"), keep_blank_values=True, strict_parsing=True, max_num_fields=18)
    except ValueError:
        return oauth_error("invalid_request")
    params = dict(items)
    if len(items) != len(params) or params.get("client_id") != CLIENT:
        return oauth_error("invalid_client")
    if params.get("resource", RESOURCE) != RESOURCE:
        return oauth_error("invalid_target")
    kind = params.get("grant_type")
    challenge = None
    if kind == "authorization_code":
        verifier = params.get("code_verifier", "")
        if not VERIFIER.fullmatch(verifier) or params.get("redirect_uri") != CALLBACK:
            return oauth_error("invalid_grant")
        challenge = pkce(verifier)
        presented = params.get("code", "")
    elif kind == "refresh_token":
        presented = params.get("refresh_token", "")
        if "scope" in params:
            return oauth_error("invalid_scope")
    else:
        return oauth_error("unsupported_grant_type")
    if not CHALLENGE.fullmatch(presented):
        return oauth_error("invalid_grant")
    access, refresh = secrets.token_urlsafe(32), secrets.token_urlsafe(32)

    def exchange():
        repo = dependencies.repository()
        return repo._json(repo._request("POST", "/rpc/exchange_onflows_chatgpt_token", json={
            "p_kind": kind, "p_digest": digest(presented), "p_challenge": challenge,
            "p_access": digest(access), "p_refresh": digest(refresh)}))

    granted = await run_in_threadpool(exchange)
    if not granted:
        return oauth_error("invalid_grant")
    return JSONResponse({"access_token": access, "token_type": "Bearer", "expires_in": 3600,
                         "scope": " ".join(granted), "refresh_token": refresh}, headers=NO_STORE)
