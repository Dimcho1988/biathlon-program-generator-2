"""Narrow, revisioned commands shared by the assistant and form adapters.

The language model supplies input fields, never a stored scientific payload.
Existing services validate and enrich the merged input; only their final save
is redirected to the transaction which also writes the operation receipt.
"""
from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import json
from urllib.parse import quote
from uuid import UUID

from fastapi import HTTPException
from pydantic import ValidationError

from .body_observations import LabReport, WeightReport
from .assistant_app_actions import APP_COMMAND_KINDS, PLAN_KINDS
from .model_schemas import ManualSpeedTestInput
from .oauth_store import PersistentStoreFailure
from .response_monitoring import DailyReport, OptionalTest, ResponseBlock, SessionReport

OBSERVATION_MODELS = {"DAILY": DailyReport, "WEIGHT": WeightReport,
    "SESSION": SessionReport, "TEST": OptionalTest, "LAB": LabReport, "BLOCK": ResponseBlock}
OWNER_KINDS = frozenset({"DAILY", "WEIGHT", "SESSION"})
PLANNING_KINDS = frozenset({"MANAGEMENT_PROFILE", "CALENDAR_EVENT"})
COMMAND_KINDS = frozenset(OBSERVATION_MODELS) | PLANNING_KINDS | {"MANUAL_SPEED_TEST"} | APP_COMMAND_KINDS


def _rpc(repository, name, body):
    result = repository._json(repository._request("POST", "/rpc/" + name, json=body))
    if not isinstance(result, dict):
        raise PersistentStoreFailure("Invalid assistant operation result")
    if result.get("forbidden"):
        raise HTTPException(403, "Достъпът до този профил е променен.")
    return result


def access(repository, alias, actor):
    """Re-read current database grants, including when a receipt is replayed."""
    result = _rpc(repository, "onflows_assistant_access", {"p_alias": alias, "p_actor": str(actor)})
    keys = ("is_owner", "can_view_plan", "can_view_recovery", "can_edit_plan")
    if any(type(result.get(key)) is not bool for key in keys):
        raise PersistentStoreFailure("Invalid assistant access result")
    if not any(result[key] for key in keys):
        raise HTTPException(403, "Нямате достъп до този спортист.")
    return {key: result[key] for key in keys}


def operation_status(repository, alias, actor, operation_id):
    return _rpc(repository, "onflows_assistant_operation_status", {
        "p_alias": alias, "p_actor": str(actor), "p_operation_id": str(UUID(str(operation_id)))})


def read_entry(repository, alias, kind, key):
    """One latest record, independent of the lifetime revision count."""
    if kind not in OBSERVATION_MODELS and kind != "MANUAL_SPEED_TEST":
        raise HTTPException(422, "Неподдържан вид наблюдение.")
    if not isinstance(key, str) or not 1 <= len(key) <= 80:
        raise HTTPException(422, "Посочете точното наблюдение.")
    table = "onflows_model_entries" if kind == "MANUAL_SPEED_TEST" else "onflows_response_entries"
    stored_kind = "SPEED_TEST" if kind == "MANUAL_SPEED_TEST" else kind
    rows = repository._json(repository._request("GET", f"/{table}?select=revision,payload"
        f"&athlete_alias=eq.{quote(alias,safe='')}&kind=eq.{stored_kind}"
        f"&entry_key=eq.{quote(key,safe='')}&order=revision.desc&limit=1"))
    if not isinstance(rows, list) or len(rows) > 1 or any(not isinstance(r, dict)
            or type(r.get("revision")) is not int or r["revision"] < 1
            or not isinstance(r.get("payload"), dict) for r in rows):
        raise PersistentStoreFailure("Invalid assistant input read")
    return rows[0] if rows else {"revision": 0, "payload": {}}


def _merge(current, patch):
    """Omitted keys survive; null is a deliberate value checked by the schema."""
    result = deepcopy(current)
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _merge(result[key], value)
        elif key == "sessions" and isinstance(value, list) and value:
            previous = {item.get("session"): deepcopy(item) for item in result.get(key, [])
                        if isinstance(item, dict)}
            seen = set()
            for item in value:
                if not isinstance(item, dict) or type(item.get("session")) is not int:
                    raise HTTPException(422, "Посочете номера на тренировъчната сесия.")
                number = item["session"]
                if number in seen:
                    raise HTTPException(422, "Номерът на сесията е повторен.")
                seen.add(number)
                previous[number] = _merge(previous.get(number, {}), item)
            result[key] = [previous[number] for number in sorted(previous)]
        else:
            result[key] = deepcopy(value)
    return result


def request_hash(command):
    try:
        return sha256(json.dumps(command, sort_keys=True, separators=(",", ":"),
                                 ensure_ascii=False, allow_nan=False).encode()).hexdigest()
    except (TypeError, ValueError) as exc:
        raise HTTPException(422, "Невалидна промяна.") from exc


def apply_prepared(repository, alias, actor, operation_id, prepared, request_hash, *, origin="CHAT"):
    """Commit canonical payload and durable receipt in a single SQL transaction."""
    if origin not in {"CHAT", "FORM"}:
        raise ValueError("Unknown operation origin")
    return _rpc(repository, "save_onflows_assistant_operation", {
        "p_alias": alias, "p_actor": str(actor), "p_operation_id": str(UUID(str(operation_id))),
        "p_request_hash": request_hash, "p_kind": prepared["kind"],
        "p_key": prepared["key"], "p_payload": prepared["payload"],
        "p_day": prepared.get("day"), "p_expected_revision": prepared["expected_revision"],
        "p_origin": origin})


class _ReceiptRepository:
    def __init__(self, repository, alias, actor, operation_id, digest, kind, key, previous):
        self.repository, self.alias, self.actor = repository, alias, actor
        self.operation_id, self.digest, self.kind, self.key = operation_id, digest, kind, key
        self.previous = previous

    def __getattr__(self, name):
        return getattr(self.repository, name)

    def _request(self, method, path, **kwargs):
        if method == "POST" and path in {"/rpc/save_onflows_response_entry", "/rpc/save_onflows_model_entry"}:
            data = kwargs["json"]
            if data["p_alias"] != self.alias or data["p_key"] != self.key:
                raise HTTPException(422, "Идентичността на наблюдението не съвпада.")
            payload = {**self.previous, **data["p_payload"]}
            return {"_assistant_receipt": apply_prepared(self.repository, self.alias, self.actor,
                self.operation_id, {"kind": self.kind, "key": self.key, "day": data.get("p_day"),
                    "payload": payload, "expected_revision": data["p_expected_revision"]}, self.digest)}
        return self.repository._request(method, path, **kwargs)

    def _json(self, response):
        if isinstance(response, dict) and "_assistant_receipt" in response:
            return response["_assistant_receipt"]
        return self.repository._json(response)


def execute(repository, alias, actor, operation_id, command):
    """Apply one explicit input change. Never activates or regenerates a plan."""
    if not isinstance(command, dict) or set(command) - {"kind", "entry_key", "expected_revision", "patch"}:
        raise HTTPException(422, "Невалидна команда.")
    kind, patch = command.get("kind"), command.get("patch")
    revision, key = command.get("expected_revision"), command.get("entry_key", "")
    if kind not in COMMAND_KINDS or not isinstance(patch, dict) or not patch:
        raise HTTPException(422, "Посочете разрешена промяна и стойностите ѝ.")
    if type(revision) is not int or revision < 0 or not isinstance(key, str):
        raise HTTPException(422, "Липсва актуалната версия на записа.")
    operation_id = UUID(str(operation_id))
    permissions = access(repository, alias, actor)
    if kind not in PLANNING_KINDS | PLAN_KINDS | {"ATHLETE_SETTINGS"} and not permissions["can_view_recovery"]:
        raise HTTPException(403, "Нямате достъп до наблюденията на този спортист.")
    if (kind in OWNER_KINDS and not permissions["is_owner"]
            or kind not in OWNER_KINDS and not permissions["can_edit_plan"]
            or kind in PLANNING_KINDS | PLAN_KINDS and not permissions["can_view_plan"]):
        raise HTTPException(403, "Нямате право да променяте тези данни.")
    digest = request_hash(command)
    previous_receipt = operation_status(repository, alias, actor, operation_id)
    if previous_receipt.get("status") != "NOT_FOUND":
        if previous_receipt.get("request_hash") != digest:
            raise HTTPException(409, "Тази операция вече е използвана за друга промяна.")
        return previous_receipt
    if kind in APP_COMMAND_KINDS:
        from .assistant_app_actions import execute as execute_app
        return execute_app(repository, alias, actor, operation_id, command, digest)
    if kind in PLANNING_KINDS:
        from .assistant_planning_commands import prepare
        prepared = prepare(repository, alias, kind, key, patch, revision, actor)
        return apply_prepared(repository, alias, actor, operation_id, prepared, digest)

    model = ManualSpeedTestInput if kind == "MANUAL_SPEED_TEST" else OBSERVATION_MODELS[kind]
    fields = set(model.model_fields) - {"expected_revision"}
    if set(patch) - fields:
        raise HTTPException(422, "Промяната съдържа неподдържани полета.")
    if kind == "MANUAL_SPEED_TEST" and not key:
        key = "manual_" + operation_id.hex
    if kind == "LAB" and not key:
        key = operation_id.hex
    if kind in {"TEST", "BLOCK"} and not key:
        key = (sha256(f"{patch.get('day')}:{patch.get('protocol')}:{patch.get('protocol_version')}".encode()).hexdigest()[:32]
               if kind == "TEST" else patch.get("start", ""))
    row = read_entry(repository, alias, kind, key)
    if row["revision"] != revision:
        raise HTTPException(409, "Записът е променен. Заредете актуалните данни.")
    current = {field: value for field, value in row["payload"].items() if field in fields}
    merged = _merge(current, patch)
    if kind in {"DAILY", "WEIGHT"}:
        if merged.get("day", key) != key:
            raise HTTPException(422, "Датата не съвпада с избрания запис.")
        merged["day"] = key
    elif kind == "SESSION":
        if merged.get("activity_ref", key) != key:
            raise HTTPException(422, "Активността не съвпада с избрания запис.")
        merged["activity_ref"] = key
    elif kind == "LAB":
        if merged.get("sample_id", key) != key:
            raise HTTPException(422, "Пробата не съвпада с избрания запис.")
        merged["sample_id"] = key
    elif kind == "MANUAL_SPEED_TEST":
        if not key.startswith("manual_"):
            raise HTTPException(422, "Посочете ръчно въведения максимален тест.")
        try:
            test_id = UUID(hex=key[7:])
            matches = "test_id" not in patch or UUID(str(patch["test_id"])) == test_id
        except (ValueError, TypeError, AttributeError) as exc:
            raise HTTPException(422, "Невалиден идентификатор на теста.") from exc
        if not matches:
            raise HTTPException(422, "Тестът не съвпада с избрания запис.")
        merged["test_id"] = str(test_id)
        # Frozen manual payloads retain both derived values. The input contract
        # accepts one measurement; keep its original measurement basis on edit.
        if "distance_m" in patch and patch["distance_m"] is not None:
            merged["speed_kmh"] = None
        elif "speed_kmh" in patch and patch["speed_kmh"] is not None:
            merged["distance_m"] = None
        elif row["payload"].get("measurement_input") == "DISTANCE":
            merged["speed_kmh"] = None
        elif row["payload"].get("measurement_input") == "SPEED":
            merged["distance_m"] = None
    if "input_method" in fields:
        merged["input_method"] = "CHAT"
    merged["expected_revision"] = revision
    try:
        body = model.model_validate(merged)
    except (ValidationError, ValueError) as exc:
        raise HTTPException(422, "Проверете необходимите полета, мерните единици и контекста на наблюдението.") from exc
    adapter = _ReceiptRepository(repository, alias, actor, operation_id, digest, kind, key, row["payload"])
    if kind == "MANUAL_SPEED_TEST":
        from .model_service import save_manual_test
        return save_manual_test(adapter, alias, body, actor)
    from .response_service import save_report
    return save_report(adapter, alias, kind, body, actor)
