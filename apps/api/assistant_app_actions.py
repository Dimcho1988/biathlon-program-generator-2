"""Typed adapters for the same settings and plan actions exposed by the app.

Only canonical services construct stored payloads. Their final RPC is redirected
through an atomic receipt transaction; the model never supplies RPC arguments.
"""
from hashlib import sha256
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo
from urllib.parse import quote

from fastapi import HTTPException
from pydantic import ValidationError

from .management_schemas import ManagementActivateRequest, ManagementDayAction, ManagementPlanAction, ManagementGenerateRequest
from .model_schemas import RecoveryConfigInput, SpeedTestInput
from .schemas import (AthleteSettingsInput, AthletePlanningProfileInput,
    MesocycleAccentPreferencesInput, SyncJobRequest)
from .oauth_store import PersistentStoreFailure

APP_INPUT_MODELS = {
    "ATHLETE_SETTINGS": AthleteSettingsInput,
    "RECOVERY_CONFIG": RecoveryConfigInput,
    "SPEED_TEST": SpeedTestInput,
    "PLAN_ACTION": ManagementPlanAction,
    "PLAN_DAY": ManagementDayAction,
    "PLAN_ACTIVATE": ManagementActivateRequest,
    "PLAN_GENERATE": ManagementGenerateRequest,
    "PLANNING_PROFILE": AthletePlanningProfileInput,
    "MESOCYCLE_PREFERENCES": MesocycleAccentPreferencesInput,
    "SYNC_DATA": SyncJobRequest,
}
APP_COMMAND_KINDS = frozenset(APP_INPUT_MODELS)
PLAN_KINDS = frozenset({"PLAN_ACTION", "PLAN_DAY", "PLAN_ACTIVATE", "PLAN_GENERATE"})
EDIT_ONLY_KINDS = frozenset({"ATHLETE_SETTINGS", "PLANNING_PROFILE", "MESOCYCLE_PREFERENCES"})
LEGACY_KEYS = {"PLANNING_PROFILE": "planning-profile-v1", "MESOCYCLE_PREFERENCES": "mesocycle-accent-preferences-v1"}


def input_contract(kind):
    schema = APP_INPUT_MODELS[kind].model_json_schema()
    return {
        "properties": {k: v for k, v in schema.get("properties", {}).items() if k != "expected_revision"},
        "required_for_new_record": [k for k in schema.get("required", []) if k != "expected_revision"],
        **({"$defs": schema["$defs"]} if "$defs" in schema else {}),
        "patch_rule": ("Explicit data refresh can enqueue directly after this read; completion happens in the worker."
            if kind == "SYNC_DATA" else "Retain omitted fields; use the revision and entry_key from this read. Clear reversible planning requests can execute directly; scientific settings and archived-plan selection use one exact preview."),
        **({"purpose": "Edits the /planning form record. For current managed-program controls use MANAGEMENT_PROFILE.planning_controls; these are separate app records."} if kind in LEGACY_KEYS else {}),
        **({"draft_selector": "To inspect a particular draft before activation, query inputs with date=start_date and revision=draft_revision. Omitting revision selects the latest saved draft for that date. The returned outer revision belongs to the active-plan pointer; keep it as command.expected_revision."} if kind == "PLAN_ACTIVATE" else {}),
    }


def _settings_state(repository, alias):
    rows = repository._json(repository._request("GET", "/onflows_athlete_settings?select=settings_revision,hr_zone_bounds,timezone,hrmax_bpm,hr_zone_source,hr_zone_percentages,model_preferences"
        f"&athlete_alias=eq.{quote(alias, safe='')}&limit=1"))
    if (not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict)
            or type(rows[0].get("settings_revision")) is not int):
        raise PersistentStoreFailure("Athlete settings revision is unavailable")
    from .oauth_store import _PlanningSettingsBundle
    settings = _PlanningSettingsBundle(rows).athlete_settings()
    if settings is None:
        raise HTTPException(409, "Настройките на спортиста не са налични.")
    from .routes.settings import _settings_response
    payload = _settings_response(settings).model_dump(mode="json")
    return rows[0]["settings_revision"], {k: v for k, v in payload.items() if k in AthleteSettingsInput.model_fields}


def _legacy_state(repository, alias, kind):
    rows = repository._json(repository._request("GET", "/onflows_athlete_settings?select=settings_revision,planning_profile,mesocycle_accent_preferences"
        f"&athlete_alias=eq.{quote(alias, safe='')}&limit=1"))
    if (not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict)
            or type(rows[0].get("settings_revision")) is not int):
        raise PersistentStoreFailure("Planning settings revision is unavailable")
    from .oauth_store import _PlanningSettingsBundle
    bundle = _PlanningSettingsBundle(rows)
    value = (bundle.athlete_planning_profile() if kind == "PLANNING_PROFILE"
        else bundle.athlete_mesocycle_accent_preferences())
    return rows[0]["settings_revision"], value.to_payload() if value else {"schema_version": LEGACY_KEYS[kind]}



def _plan_summary(plan):
    if not isinstance(plan, dict):
        return None
    from biathlon.planning_schedule import day_sessions
    fields = {"title", "sport", "zone", "purpose", "main_work_minutes", "total_minutes"}
    block_fields = {"kind", "label", "zone", "duration_min", "duration_s", "repetitions", "repetition",
        "instructions", "target_hr_bpm", "target_speed_kmh", "target_speed_range_kmh", "distance_range_m"}
    return {"start_date": plan.get("start_date"), "end_date": plan.get("end_date"),
        "activation_eligible": plan.get("activation_eligible"),
        "days": [{"date": day.get("date"), "status": day.get("status"),
            "sessions": [{**{k: v for k, v in session.items() if k in fields},
                "blocks": [{k: v for k, v in block.items() if k in block_fields}
                           for block in session.get("blocks", []) if isinstance(block, dict)]}
                for session in day_sessions(day) if isinstance(session, dict)]}
            for day in plan.get("days", []) if isinstance(day, dict)]}


def read_input(repository, alias, kind, key=None, *, draft_date=None, draft_revision=None):
    if kind not in APP_COMMAND_KINDS:
        raise HTTPException(422, "Неподдържано действие.")
    payload, revision, preview_summary = {}, 0, None
    if kind in LEGACY_KEYS:
        if key not in (None, "", LEGACY_KEYS[kind]):
            raise HTTPException(422, "Невалиден ключ за настройките за планиране.")
        revision, payload = _legacy_state(repository, alias, kind)
        key = LEGACY_KEYS[kind]
    elif kind == "SYNC_DATA":
        if key not in (None, "", "intervals-sync"):
            raise HTTPException(422, "Невалиден ключ за обновяването на данните.")
        connection = repository.connection(alias)
        if connection is None or connection.status != "CONNECTED":
            raise HTTPException(409, "Профилът в Intervals.icu не е свързан.")
        key = "intervals-sync"
        # Queueing is an intent, not an overwrite of a domain record.
        payload = {"scope": "FULL"}
    elif kind == "ATHLETE_SETTINGS":
        if key not in (None, "", "athlete-settings"):
            raise HTTPException(422, "Невалиден ключ за настройките.")
        revision, payload = _settings_state(repository, alias)
        key = "athlete-settings"
    elif kind == "RECOVERY_CONFIG":
        from .model_service import ModelStore
        if key not in (None, "", "recovery-v2"):
            raise HTTPException(422, "Невалиден ключ за възстановяването.")
        payload = ModelStore(repository).config(alias)
        revision = payload.pop("expected_revision")
        key = "recovery-v2"
    elif kind == "PLAN_GENERATE":
        from .management_store import ManagementStore
        settings = repository.athlete_settings(alias)
        today = datetime.now(timezone.utc).astimezone(ZoneInfo(settings.timezone if settings else "UTC")).date()
        try:
            selected = date.fromisoformat(key) if key else today
        except (ValueError, TypeError) as exc:
            raise HTTPException(422, "Посочете точната начална дата.") from exc
        if not 0 <= (selected - today).days <= 7:
            raise HTTPException(422, "Изберете начална дата от днес до следващите седем дни.")
        store = ManagementStore(repository)
        profile = store.profile(alias)
        if not profile["configured"]:
            raise HTTPException(409, "Първо запишете профила за планиране.")
        revision = profile["revision"]
        key = selected.isoformat()
        payload = {"start_date": key, "expected_profile_revision": revision,
            "expected_draft_revision": store.draft_metadata(alias, selected)["revision"]}
    elif kind == "SPEED_TEST":
        import re
        if key:
            if not re.fullmatch(r"[a-f0-9]{32}", key):
                raise HTTPException(422, "Посочете точния записан участък.")
            rows = repository._json(repository._request("GET", "/onflows_model_entries?select=revision,payload"
                f"&athlete_alias=eq.{quote(alias, safe='')}&kind=eq.SPEED_TEST&entry_key=eq.{key}&order=revision.desc&limit=1"))
            if not isinstance(rows, list) or len(rows) > 1 or any(not isinstance(r, dict)
                    or type(r.get("revision")) is not int or not isinstance(r.get("payload"), dict) for r in rows):
                raise PersistentStoreFailure("Invalid recorded speed test")
            if rows:
                revision = rows[0]["revision"]
                payload = {k: v for k, v in rows[0]["payload"].items() if k in SpeedTestInput.model_fields}
                payload["expected_source_run_key"] = rows[0]["payload"].get("source_run_key")
        key = key or ""
    else:
        from .management_plan_store import PlanStore
        if key not in (None, "", "active-plan"):
            raise HTTPException(422, "Невалиден ключ на програмата.")
        current = PlanStore(repository).current(alias)
        revision = current["revision"] if current else 0
        key = "active-plan"
        # Action previews contain only visible prescriptions. Physiological
        # evidence is retrieved separately through its normal access gate.
        stored = current["payload"] if current else {}
        payload = {"status": stored.get("status"), "has_proposal": bool(stored.get("proposal")),
            "plan_start": (stored.get("plan") or {}).get("start_date"),
            "plan_end": (stored.get("plan") or {}).get("end_date"),
            "proposal_start": (stored.get("proposal") or {}).get("start_date"),
            "proposal_end": (stored.get("proposal") or {}).get("end_date")}
        preview_summary = {"revision": revision, "status": stored.get("status"),
            "approved_plan": _plan_summary(stored.get("plan")),
            "proposal": _plan_summary(stored.get("proposal"))}
        if kind == "PLAN_ACTIVATE":
            from .management_store import ManagementStore
            store = ManagementStore(repository)
            if draft_date is not None:
                try:
                    selected = date.fromisoformat(draft_date)
                except (TypeError, ValueError) as exc:
                    raise HTTPException(422, "Посочете точната начална дата на черновата.") from exc
                if draft_revision is not None:
                    if type(draft_revision) is not int or draft_revision < 1:
                        raise HTTPException(422, "Посочете положителна версия на черновата.")
                    rows = store.draft_revision(alias, selected, draft_revision)
                else:
                    rows = store.drafts(alias, start_date=selected, limit=1)
                if not rows:
                    raise HTTPException(404, "Избраната чернова не е налична за този спортист.")
            else:
                if draft_revision is not None:
                    raise HTTPException(422, "Версията на черновата изисква начална дата.")
                latest = {}
                for row in store.drafts(alias, limit=20):
                    latest.setdefault(row["entry_key"], row)
                rows = list(latest.values())[:3]
            preview_summary["drafts"] = [{"start_date": row["entry_key"], "draft_revision": row["revision"],
                "plan": _plan_summary(row["payload"])} for row in rows]
    return {"availability": "AVAILABLE", "kind": kind, "entry_key": key, "revision": revision,
        "payload": payload, "input_contract": input_contract(kind),
        **({"preview_summary": preview_summary} if preview_summary is not None else {})}


def _commit(repository, alias, actor, operation_id, digest, kind, key, revision, payload):
    from .assistant_commands import _rpc
    return _rpc(repository, "save_onflows_assistant_app_operation", {
        "p_alias": alias, "p_actor": str(actor), "p_operation_id": str(operation_id),
        "p_request_hash": digest, "p_kind": kind, "p_key": key,
        "p_payload": payload, "p_expected_revision": revision,
    })


class _AtomicAdapter:
    """Redirect only known canonical final writes, not arbitrary service calls."""
    def __init__(self, repository, alias, actor, operation_id, digest, kind, key, revision):
        self.repository, self.alias, self.actor = repository, alias, actor
        self.operation_id, self.digest, self.kind, self.key, self.revision = operation_id, digest, kind, key, revision
        self.receipt = None

    def __getattr__(self, name):
        return getattr(self.repository, name)

    def enqueue_sync_job(self, *, athlete_alias, job_kind, idempotency_key, request_payload):
        if self.kind != "SYNC_DATA" or athlete_alias != self.alias:
            raise HTTPException(422, "Профилът за обновяване не съвпада.")
        self.receipt = _commit(self.repository, self.alias, self.actor, self.operation_id, self.digest,
            self.kind, self.key, self.revision, {"job_kind": job_kind, "request_payload": dict(request_payload)})
        if self.receipt.get("conflict"):
            raise HTTPException(409, "Заявката за обновяване е променена.")
        return self.receipt.get("sync_job", {})

    def planning_job(self, operation, **arguments):
        if operation != "ENQUEUE" or self.kind != "PLAN_GENERATE":
            raise HTTPException(422, "Неподдържана операция за подготовка на програма.")
        if arguments.get("alias") != self.alias or arguments.get("actor") != str(self.actor):
            raise HTTPException(422, "Профилът за подготовка не съвпада.")
        self.receipt = _commit(self.repository, self.alias, self.actor, self.operation_id, self.digest,
            self.kind, self.key, self.revision, {"request": arguments["request"]})
        if self.receipt.get("conflict"):
            return self.receipt
        return self.receipt.get("job")

    def _request(self, method, path, **kwargs):
        supported = {"/rpc/save_onflows_model_entry", "/rpc/save_onflows_management_plan_compact",
                     "/rpc/queue_onflows_plan_refresh"}
        if method == "POST" and path in supported:
            data = kwargs["json"]
            if data.get("p_alias") != self.alias or data.get("p_actor") != str(self.actor):
                raise HTTPException(422, "Действието не съответства на текущия профил.")
            if data.get("p_expected_revision") != self.revision:
                raise HTTPException(409, "Версията на действието се промени.")
            if path == "/rpc/save_onflows_model_entry":
                expected = "RECOVERY" if self.kind == "RECOVERY_CONFIG" else "SPEED_TEST"
                if self.kind not in {"RECOVERY_CONFIG", "SPEED_TEST"} or data["p_kind"] != expected or data["p_key"] != self.key:
                    raise HTTPException(422, "Идентичността на модела не съвпада.")
                payload = data["p_payload"]
            else:
                if self.kind not in PLAN_KINDS:
                    raise HTTPException(422, "Неподдържано действие за програмата.")
                payload = {"rpc": path.rsplit("/", 1)[-1], "arguments": data}
            self.receipt = _commit(self.repository, self.alias, self.actor, self.operation_id, self.digest,
                self.kind, self.key, self.revision, payload)
            return {"_assistant_app_receipt": self.receipt}
        return self.repository._request(method, path, **kwargs)

    def _json(self, response):
        if isinstance(response, dict) and "_assistant_app_receipt" in response:
            return response["_assistant_app_receipt"]
        return self.repository._json(response)


def execute(repository, alias, actor, operation_id, command, digest):
    from .assistant_commands import _merge
    kind, key, revision, patch = (command[k] for k in ("kind", "entry_key", "expected_revision", "patch"))
    model = APP_INPUT_MODELS[kind]
    fields = set(model.model_fields) - {"expected_revision"}
    if set(patch) - fields:
        raise HTTPException(422, "Промяната съдържа неподдържани полета.")
    current = read_input(repository, alias, kind, key,
        **({"draft_date": patch.get("start_date"), "draft_revision": patch.get("draft_revision")} if kind == "PLAN_ACTIVATE" else {}))
    if current["revision"] != revision:
        raise HTTPException(409, "Данните са променени. Заредете актуалната версия.")
    key = current["entry_key"]
    previous = {k: v for k, v in current["payload"].items() if k in fields}
    merged = _merge(previous, patch)
    try:
        body = model.model_validate({**merged, **({"expected_revision": revision} if "expected_revision" in model.model_fields else {})})
    except (ValidationError, ValueError) as exc:
        raise HTTPException(422, "Проверете необходимите полета и стойностите за избраното действие.") from exc
    if kind == "PLAN_GENERATE" and (body.start_date.isoformat() != key or body.expected_profile_revision != revision):
        raise HTTPException(409, "Началната дата или профилът се промени; заредете актуалните входни данни.")
    if kind == "PLAN_ACTION" and body.action == "APPROVE" and not current["payload"].get("has_proposal"):
        raise HTTPException(409, "Няма актуално предложение за утвърждаване.")
    if kind in LEGACY_KEYS:
        from .cloud import AthletePlanningProfile, AthleteMesocycleAccentPreferences
        try:
            value = (AthletePlanningProfile(**body.model_dump()).validate() if kind == "PLANNING_PROFILE"
                else AthleteMesocycleAccentPreferences(**body.model_dump()).validate())
        except ValueError as exc:
            raise HTTPException(422, "Стойностите за планиране са несъвместими.") from exc
        if kind == "PLANNING_PROFILE" and repository.athlete_settings(alias) is None:
            raise HTTPException(409, "Първо задайте пулсовите зони и часовата зона.")
        if kind == "MESOCYCLE_PREFERENCES" and repository.athlete_planning_profile(alias) is None:
            raise HTTPException(409, "Първо запишете профила за планиране.")
        return _commit(repository, alias, actor, operation_id, digest, kind, key, revision, value.to_payload())
    if kind == "ATHLETE_SETTINGS":
        from .routes.settings import _resolve_settings
        connection = repository.connection(alias)
        if connection is None or connection.status != "CONNECTED":
            raise HTTPException(409, "Профилът в Intervals.icu не е свързан.")
        try:
            settings = _resolve_settings(body, repository.athlete_settings(alias))
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        payload = {"hr_zone_bounds": list(settings.zone_bounds_bpm), "timezone": settings.timezone,
            "hrmax_bpm": settings.hrmax_bpm, "hr_zone_source": settings.hr_zone_source,
            "hr_zone_percentages": list(settings.hr_zone_percentages) if settings.hr_zone_percentages is not None else None,
            "model_preferences": settings.model_preferences()}
        return _commit(repository, alias, actor, operation_id, digest, kind, key, revision, payload)
    if kind == "SPEED_TEST":
        resolved = sha256(f"{body.activity_ref}:{body.start_s}:{body.duration_s}".encode()).hexdigest()[:32]
        if key and key != resolved:
            raise HTTPException(422, "Участъкът не съвпада с избрания тест.")
        key = resolved
    adapter = _AtomicAdapter(repository, alias, actor, operation_id, digest, kind, key, revision)
    if kind == "SYNC_DATA":
        from .sync_service import enqueue_sync_job
        enqueue_sync_job(repository=adapter, athlete_alias=alias, scope=body.scope)
        return adapter.receipt
    if kind in {"RECOVERY_CONFIG", "SPEED_TEST"}:
        from . import model_service
        return (model_service.save_config if kind == "RECOVERY_CONFIG" else model_service.save_test)(adapter, alias, body, actor)
    from . import management_lifecycle
    if kind == "PLAN_GENERATE":
        from .planning_jobs import enqueue
        enqueue(adapter, alias, body, actor)
    elif kind == "PLAN_ACTION" and body.action == "REFRESH":
        adapter._json(adapter._request("POST", "/rpc/queue_onflows_plan_refresh", json={
            "p_alias": alias, "p_actor": str(actor), "p_expected_revision": revision,
            "p_record_unchanged_check": body.record_unchanged_check}))
    elif kind == "PLAN_ACTION":
        management_lifecycle.action(adapter, alias, body, actor, include_saved_payload=False)
    elif kind == "PLAN_DAY":
        management_lifecycle.refresh(adapter, alias, actor, expected_revision=revision, force=True,
            day_action=body, include_saved_payload=False)
    else:
        management_lifecycle.activate(adapter, alias, body, actor, include_saved_payload=False)
    if adapter.receipt is None:
        raise PersistentStoreFailure("Plan action returned without an atomic receipt")
    return adapter.receipt
