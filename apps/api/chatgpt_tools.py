"""Profile-bound MCP tools for the saved daily plan and race calendar."""
from datetime import date, datetime
from zoneinfo import ZoneInfo
import json
from typing import Annotated
from uuid import uuid5, NAMESPACE_URL

from fastapi import HTTPException
from mcp import types
from mcp.server.fastmcp import Context
from pydantic import Field
from starlette.concurrency import run_in_threadpool

from . import assistant_commands, assistant_context, dependencies
from .assistant_planning_commands import calendar_state
from .oauth_store import PersistentStoreFailure
from .routes import chatgpt_oauth as oauth


def _pick(row, names):
    return {key: row[key] for key in names if key in row}


def _block(row):
    fields = ("kind", "label", "zone", "duration_s", "duration_min", "target_hr_bpm",
              "target_speed_kmh", "repeat_count", "recovery_type", "passive_rest", "instructions")
    result = _pick(row, fields)
    if "sequence" in row:
        result["sequence"] = [_block(item) for item in row["sequence"]]
    return result


def training_day(repo, alias, actor, rights, day):
    report = assistant_context.read(repo, alias, rights, {"kind": "day", "date": day.isoformat()}, actor=actor)
    result = _pick(report, ("availability", "reason", "date", "plan_revision", "plan_status",
                            "day_status", "prescription_active", "timezone"))
    result["sessions"] = [
        {**_pick(row, ("title", "sport_label_bg", "zone", "purpose", "total_minutes", "main_work_minutes")),
         "blocks": [_block(block) for block in row.get("blocks", [])]} for row in report.get("sessions", [])
    ]
    return result


def races(repo, alias, actor, rights):
    state = calendar_state(repo, alias)
    return {"athlete_alias": alias, "revision": state["revision"], "events": [
        _pick(event, ("event_id", "name", "event_type", "start_date", "end_date"))
        for event in state["payload"]["events"] if event.get("event_type") in {"MAIN_RACE", "CONTROL_RACE"}]}


def move_race(repo, alias, actor, rights, expected_athlete, event_id, revision, new_day):
    if expected_athlete != alias:
        raise HTTPException(409, "Свързаният профил се различава от избрания календар.")
    operation = uuid5(NAMESPACE_URL, json.dumps([oauth.RESOURCE, alias, actor, event_id, revision, new_day.isoformat()]))
    previous = assistant_commands.operation_status(repo, alias, actor, operation)
    if previous.get("status") == "SAVED":
        return {"status": "SAVED", "event_id": event_id, "saved_start_date": new_day.isoformat(),
                "revision": previous.get("revision"), "already_applied": True}
    calendar = races(repo, alias, actor, rights)
    if calendar["revision"] != revision:
        raise HTTPException(409, "Календарът е променен. Прочетете отново стартовете.")
    event = next((row for row in calendar["events"] if row["event_id"] == event_id), None)
    if not event:
        raise HTTPException(404, "Този старт липсва в избрания календар.")
    duration = date.fromisoformat(event["end_date"]) - date.fromisoformat(event["start_date"])
    if date.max - new_day < duration:
        raise HTTPException(422, "Крайната дата е извън допустимия период.")
    patch = {"start_date": new_day.isoformat(), "end_date": (new_day + duration).isoformat()}
    receipt = assistant_commands.execute(repo, alias, actor, operation,
        {"kind": "CALENDAR_EVENT", "entry_key": event_id, "expected_revision": revision, "patch": patch})
    if receipt.get("status") != "SAVED":
        raise HTTPException(409, "Промяната не е записана. Проверете актуалния календар.")
    return {"status": "SAVED", "event_id": event_id, **patch, "revision": receipt.get("revision")}


def error(message, challenge=None):
    return types.CallToolResult(isError=True, content=[types.TextContent(type="text", text=message)],
                               _meta={"mcp/www_authenticate": [challenge]} if challenge else None)


async def call(ctx, scope, action, *args, athlete_alias=None, list_if_multiple=False):
    authorization = ctx.request_context.request.headers.get("authorization", "")
    scheme, _, credential = authorization.partition(" ")
    wanted = f"{oauth.READ} {oauth.WRITE}" if scope == oauth.WRITE else oauth.READ
    challenge = f'Bearer resource_metadata="{oauth.METADATA}", scope="{wanted}"'

    def perform():
        if scheme.lower() != "bearer" or not oauth.CHALLENGE.fullmatch(credential):
            return error("Свържи профила си в onFlows.", challenge)
        repo = dependencies.repository()
        grant = oauth.read_grant(repo, credential)
        if not grant:
            return error("Свържи отново профила си в onFlows.", challenge)
        if scope not in grant["scopes"]:
            return error("За това действие е необходимо допълнително разрешение.",
                         challenge + ', error="insufficient_scope"')
        actor = grant["actor"]
        single = grant["athlete_alias"]
        selected = ([{**grant, "allow_write": oauth.WRITE in grant["scopes"]}]
                    if single else grant["onflows_chatgpt_athletes"])
        def identity(row):
            owner = row.get("onflows_user_athletes") or {}
            name = (owner.get("onflows_profiles") or {}).get("display_name")
            return {"athlete_alias": row["athlete_alias"], "athlete_name": name}

        if list_if_multiple and athlete_alias is None and not single:
            athletes = []
            for row in selected:
                try:
                    rights = assistant_commands.access(repo, row["athlete_alias"], actor)
                except HTTPException as exc:
                    if exc.status_code == 403:
                        continue
                    raise
                if rights["can_view_plan"]:
                    athletes.append({**identity(row), "can_move_race": rights["can_edit_plan"] and row["allow_write"]})
            result = {"athletes": athletes}
        else:
            alias = athlete_alias if athlete_alias is not None else single
            if not alias:
                return error("Избери спортист от get_connected_athlete и подай неговия athlete_alias.")
            row = next((item for item in selected if item["athlete_alias"] == alias), None)
            if row is None:
                return error("Спортистът не е сред профилите, избрани при свързване.")
            rights = assistant_commands.access(repo, alias, actor)
            if not rights["can_view_plan"] or scope == oauth.WRITE and not (rights["can_edit_plan"] and row["allow_write"]):
                return error("Текущите права за този спортист не позволяват действието.")
            result = {**action(repo, alias, actor, rights, *args), **identity(row)}
        return types.CallToolResult(structuredContent=result, content=[
            types.TextContent(type="text", text=json.dumps(result, ensure_ascii=False))])

    try:
        return await run_in_threadpool(perform)
    except HTTPException as exc:
        return error(str(exc.detail))
    except PersistentStoreFailure:
        return error("Връзката с данните временно е недостъпна. Няма потвърждение за нов запис.")


def register(server):
    read = {"annotations": types.ToolAnnotations(readOnlyHint=True, openWorldHint=False),
            "meta": {"securitySchemes": [{"type": "oauth2", "scopes": [oauth.READ]}]}}
    write = {"annotations": types.ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=True,
                                                   openWorldHint=False),
             "meta": {"securitySchemes": [{"type": "oauth2", "scopes": [oauth.READ, oauth.WRITE]}]}}

    @server.tool(**read)
    async def get_connected_athlete(ctx: Context, athlete_alias: str | None = None) -> types.CallToolResult:
        """Без параметър показва свързаните спортисти. С athlete_alias връща името и местната дата на конкретния спортист."""
        def profile(repo, alias, actor, rights):
            settings = repo.athlete_settings(alias)
            return {"timezone": settings.timezone, "today": datetime.now(ZoneInfo(settings.timezone)).date().isoformat()} if settings else {}
        return await call(ctx, oauth.READ, profile, athlete_alias=athlete_alias, list_if_multiple=True)

    @server.tool(**read)
    async def get_training_day(day: date, ctx: Context, athlete_alias: str | None = None) -> types.CallToolResult:
        """Прочита запазената тренировка за местната дата и свързания спортист."""
        return await call(ctx, oauth.READ, training_day, day, athlete_alias=athlete_alias)

    @server.tool(**read)
    async def list_races(ctx: Context, athlete_alias: str | None = None) -> types.CallToolResult:
        """Прочита основните и контролните стартове и текущата версия на календара."""
        return await call(ctx, oauth.READ, races, athlete_alias=athlete_alias)

    @server.tool(**write)
    async def move_race_date(athlete_alias: str, event_id: Annotated[str, Field(min_length=1, max_length=80)],
                             calendar_revision: Annotated[int, Field(ge=0)], new_start_date: date,
                             ctx: Context) -> types.CallToolResult:
        """Премества един съществуващ старт при изрична заявка. Вземи event_id и версията от list_races."""
        return await call(ctx, oauth.WRITE, move_race, athlete_alias, event_id, calendar_revision, new_start_date, athlete_alias=athlete_alias)
