"""Bounded conversation orchestration; training decisions stay in canonical services."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from math import isfinite
import json
import logging
import os
import re
from time import monotonic
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import HTTPException

from .oauth_store import PersistentStoreFailure
from . import assistant_commands, assistant_context
from .assistant_provider import AssistantProviderError, DisabledAssistantProvider, get_assistant_provider

SUGGESTIONS = ["Какво тренирам днес?", "Защо е тази доза?", "Как да изпълня тренировката?", "Искам да запиша умората си"]
CONFIRM = {"запиши промяната", "потвърждавам промяната", "потвърждавам", "да", "да, запиши", "запиши", "създай събитието"}
CANCEL = {"откажи промяната", "отказ", "отмени"}
WRITE_KINDS = assistant_commands.COMMAND_KINDS
READ_KINDS = assistant_context.QUERY_KINDS
UNAVAILABLE_SESSION_CHANGE = "Промяната на броя отсечки още няма надеждна проверка през тренировъчния двигател. Не мога да потвърдя допълнителна доза. Отвори седмичната програма и обсъди промяната с треньора."
logger=logging.getLogger(__name__)


def _uuid(value):
    try:
        return str(UUID(str(value)))
    except (ValueError, TypeError, AttributeError) as exc:
        raise HTTPException(422, "Невалиден идентификатор на разговора или заявката.") from exc


def retention_days():
    try:
        value = int(os.environ.get("ONFLOWS_ASSISTANT_RETENTION_DAYS", "30"))
    except ValueError:
        value = 30
    return max(1, min(365, value))


def _permissions(repository, alias, actor):
    value = assistant_commands.access(repository, alias, actor)
    if not (value.get("can_view_plan") or value.get("can_view_recovery")):
        raise HTTPException(403, "Нямаш достъп до данните на този спортист.")
    return value


def _rpc(repository, action, alias, actor, conversation=None, operation=None, payload=None):
    value = repository._json(repository._request("POST", "/rpc/onflows_assistant_conversation", json={
        "p_action": action, "p_alias": alias, "p_actor": str(actor),
        "p_conversation": conversation, "p_operation": operation,
        "p_payload": payload or {}, "p_retention_days": retention_days(),
    }))
    if not isinstance(value, dict):
        raise PersistentStoreFailure("Invalid assistant conversation result")
    if value.get("error"):
        code = value["error"]
        status = {"forbidden":403,"not_found":404,"scope_changed":412,"busy":409,"rate_limit":429,
                  "operation_conflict":409,"invalid":422}.get(code,503)
        messages = {"scope_changed":"Достъпът е променен. Започни нов разговор.",
                    "busy":"Предишната заявка още се обработва. Провери нейния статус.",
                    "rate_limit":"Достигнат е лимитът за кратък период. Опитай след малко.",
                    "operation_conflict":"Този идентификатор вече е използван за друга заявка.",
                    "not_found":"Разговорът не е намерен или срокът му е изтекъл."}
        raise HTTPException(status,messages.get(code,"Разговорът временно не е достъпен."))
    return value


def _provider_available(provider):
    return provider is not None and not isinstance(provider, DisabledAssistantProvider)


def _configured_provider(repository):
    try:
        return get_assistant_provider(repository=repository)
    except AssistantProviderError:
        return DisabledAssistantProvider()


def _messages(rows):
    result = []
    for row in rows:
        if row.get("user_message"):
            result.append({"role":"user","content":row["user_message"]})
        if row.get("assistant_message"):
            result.append({"role":"assistant","content":row["assistant_message"]})
    return result[-16:]


def _capabilities(access):
    writes = []
    reads = {"inputs", "navigation"}
    read_views={}
    if access.get("can_view_recovery"):
        reads.update({"status", "history", "activities", "activity", "speed", "trainability", "learning", "response"})
        if access.get("is_owner"):
            writes.extend(["DAILY", "WEIGHT", "SESSION"])
        if access.get("can_edit_plan"):
            writes.extend(["TEST", "LAB", "BLOCK", "MANUAL_SPEED_TEST", "RECOVERY_CONFIG", "SPEED_TEST"])
    if access.get("can_view_plan"):
        reads.update({"day", "method", "management", "methods"})
    if access.get("can_view_recovery"):
        reads.add("settings")
        if not access.get("can_view_plan"):
            reads.add("management")
            read_views["management"]=["sync-state"]
    if access.get("can_edit_plan"):
        writes.extend(["ATHLETE_SETTINGS","PLANNING_PROFILE","MESOCYCLE_PREFERENCES"])
        if access.get("can_view_plan"):
            writes.extend(["MANAGEMENT_PROFILE", "CALENDAR_EVENT", "PLAN_ACTION", "PLAN_DAY", "PLAN_ACTIVATE", "PLAN_GENERATE"])
    if access.get("can_view_recovery") or access.get("can_view_plan") and access.get("can_edit_plan"):
        writes.append("SYNC_DATA")
    return {"read": sorted(reads), "read_views":read_views,"write": [kind for kind in writes if kind in WRITE_KINDS],
        "plan_approval": "Read the current proposal and show its exact revision before explicit approval.",
        "session_dose_change": "UNAVAILABLE_NO_CANONICAL_PREVIEW",
        "input_contract": "Read inputs first. command key=entry_key, expected_revision=current revision; patch only requested fields. "
                          "Explicit reversible input requests can be saved directly; deletion, scientific settings and plan approval need one preview.",
        "scales": {"daily": "1–5, higher=worse; motivation 1=high, 5=low", "rpe": "0–10, timing required", "feel": "1–5, higher=worse"}}


def _sources(context):
    if not context:
        return []
    kind=str(context.get("query_kind","onflows"))
    labels={"day":"Запазена тренировъчна програма", "method":"Методика на избраната тренировка",
        "methods":"Каталог на тренировъчните методи", "status":"Тренировъчно състояние — моделна оценка",
        "history":"История на изпълненото натоварване", "inputs":"Актуални входни данни",
        "settings":"Настройки на профила", "activities":"Записани активности", "activity":"Анализ на избраната активност",
        "speed":"Скоростен модел и тестове", "trainability":"Индекс на тренираност", "learning":"Резултати от самообучението",
        "response":"Наблюдения и оценки", "management":"Управление на тренировъчната програма",
        "navigation":"Функции на приложението"}
    metadata=context.get("generation_metadata") or {}
    label="Сценарна оценка на onFlows" if context.get("source")=="CANONICAL_WHAT_IF_PREVIEW" else labels.get(kind,"Данни на onFlows")
    return [{"kind":kind, "label":label,
             "revision":context.get("plan_revision",context.get("revision",context.get("source_revision",metadata.get("revision")))),
             "as_of":context.get("as_of") or (context.get("input_snapshot") or {}).get("analysis_as_of") or metadata.get("analysis_as_of")}]


def _read(repository, alias, actor, query):
    # No cached grant is reused for tool calls, including calls after a model response.
    access = _permissions(repository,alias,actor)
    if not isinstance(query,dict) or query.get("kind") not in READ_KINDS:
        raise HTTPException(422,"Неподдържана справка.")
    return assistant_context.read(repository,alias,access,query,actor=actor)


def _command(reply):
    raw = reply.get("command")
    if not isinstance(raw,dict) or raw.get("kind") not in WRITE_KINDS:
        raise HTTPException(422,"Неподдържана команда.")
    try:
        patch = json.loads(raw["patch_json"]) if isinstance(raw.get("patch_json"),str) else raw.get("patch")
    except (ValueError,TypeError) as exc:
        raise HTTPException(422,"Невалидна частична промяна.") from exc
    if not isinstance(patch,dict) or not patch or len(json.dumps(patch,ensure_ascii=False))>12000 or len(json.dumps(patch,ensure_ascii=False).encode("utf-8"))>24576:
        raise HTTPException(422,"Невалидна частична промяна.")
    revision=raw.get("expected_revision")
    if type(revision) is not int or revision<0:
        raise HTTPException(422,"Липсва актуална версия на записа.")
    return {"kind":raw["kind"],"entry_key":raw.get("key",raw.get("entry_key")) or "","expected_revision":revision,"patch":patch}


def _matching_input(command, contexts):
    for context in reversed(contexts):
        if context.get("query_kind") != "inputs":
            continue
        for entry in context.get("entries") or [context]:
            if ((entry.get("input_kind") or entry.get("kind")) == command["kind"]
                and entry.get("entry_key", "") == command["entry_key"]
                and entry.get("revision") == command["expected_revision"]):
                return entry
    return None


def _read_request(message):
    text=message.lower().strip()
    # Polite/discourse prefixes do not turn an explanation into an action.
    text=re.sub(r"^(?:(?:а|сега|моля(?: те)?)\b[\s,.:]*)+", "", text)
    return bool(re.search(r"^(?:покажи|обясни|разкажи|виж|кажи|какво|каква|какви|какъв|как\b|защо|колко|кога|къде|дали|интересува ме|искам да (?:разбера|видя|знам)|ще ми обясниш)",text)
                or re.search(r"\bкак\s+(?:да|мога|можем)\b",text))


def _rating_timing(text):
    text=text.lower()
    if re.search(r"веднага|непосредствено",text):
        return "IMMEDIATE"
    if re.search(r"на следващия ден",text):
        return "NEXT_DAY"
    if (re.fullmatch(r"(?:около\s+)?(?:10\s*[–-]\s*60|[1-5]\d|60)\s*мин(?:ути)?[.! ]*",text)
            or re.search(r"(?:10\s*[–-]\s*60|\b(?:[1-5]\d|60))\s*мин(?:ути)?\s*(?:по-късно|след края)",text)
            or re.search(r"\bслед\s+(?:[1-5]\d|60)\s*мин",text)):
        return "DELAYED"
    return None


def _write_disallowed(message):
    text=message.lower().strip()
    return bool(re.search(r"\b(?:ако|пример|примерно|представи си|цитат|само обсъждаме|само обсъждам|без запис|без промяна)\b"
                         r"|\bне\s+(?:искам|прав\w*|запис\w*|запиш\w*|запаз\w*|промен\w*|създа\w*|премест\w*|добав\w*|отмен\w*|изтри\w*|премах\w*)"
                         r"|(?:искам|искал|искала)\s+да\s+(?:знам|разбера|обсъдим|науча)"
                         r"|^(?:[„“«\"']|отказвам|отказах|нека да не)",text))


def _explicit_write_request(message):
    """Use the user's action request, never a noun or a retrieved instruction."""
    text = message.lower().strip()
    if _write_disallowed(message) or _read_request(message):
        return False
    if re.search(r"^(?:а\s+)?(?:какво|каква|какви|какъв|защо|колко|кога|къде|дали|обясни|покажи|виж|как\b)", text):
        return False
    return bool(re.search(r"\b(?:въвед\w*|запиш\w*|записвай|промен\w*|премест\w*|създа\w*|добав\w*|задай|отмени|изтрий|премахни|утвърди|одобри|приеми|активирай|спри|възобнови|генерирай|обнови(?:ш|те|м)?|синхронизира(?:й|ш|те|ме))\b", text)
                or re.search(r"\bискам\s+(?:да\s+)?(?:почив\w*|тренирам|разреш\w*|забран\w*|обновя|синхронизирам)", text))


def _write_intent(command, message):
    domains = {"DAILY": r"умор|сън|спах|стрес|болезнен|мотивац|болк|болен|оценк",
        "WEIGHT": r"тегл|теж|килограм|\bкг\b", "SESSION": r"rpe|усили|оценк|лактат|трениров|изпълн",
        "TEST": r"тест|измер", "LAB": r"лаборатор|феритин|хемоглобин|изследван", "BLOCK": r"блок|период",
        "MANUAL_SPEED_TEST": r"тест|максимал", "MANAGEMENT_PROFILE": r"почив|тренир|седмич|планира|профил|метод|обучен|спорт|програм",
        "CALENDAR_EVENT": r"старт|страт|състезан|лагер|събити|календар|недостъп",
        "ATHLETE_SETTINGS": r"пулс|hrmax|зон|настрой|профил|показател|часов", "RECOVERY_CONFIG": r"възстанов|recovery|умор|настрой",
        "SPEED_TEST": r"тест|скорост|участък", "PLAN_ACTION": r"план|програм|предлож|седмиц",
        "PLAN_DAY": r"почив|тренир|ден|сесия", "PLAN_ACTIVATE": r"план|програм", "PLAN_GENERATE": r"план|програм|чернов",
        "PLANNING_PROFILE":r"планира|профил|сезон|седмич|тренир|почив|спорт|дисциплин|период",
        "MESOCYCLE_PREFERENCES":r"мезоцикл|акцент|компонент|период",
        "SYNC_DATA":r"данн|синхрон|активност|възстанов|благосъстоян|wellness|recovery|intervals"}
    if _write_disallowed(message) or _read_request(message):
        return False
    if _explicit_write_request(message):
        return bool(re.search(domains.get(command["kind"], r"(?!)"), message.lower()))
    # Self-reported numeric observations are already literal user instructions
    # in this interface. Questions and hypothetical examples never become writes.
    if re.search(r"\?|\b(?:ако|пример|примерно|не|как|какво|каква|какъв|какви|колко|дали|защо|кога|ли)\b", message.lower()):
        return False
    patterns = {"DAILY": r"умор|сън|спах|стрес|болезнен|мотивац|болк|болен|оценк",
                "WEIGHT": r"тегл|теж|килограм|\bкг\b", "SESSION": r"rpe|усили|оценк|лактат|трениров|изпълн"}
    return bool(re.search(patterns.get(command["kind"], r"(?!)"), message.lower()))


def _request_text(request, message):
    if not request:
        return message
    return "\n".join([request.get("request", ""), *request.get("answers", [])])


def _direct_request(command, entry, request, message):
    """A concrete reversible request is its authorization; a suggestion is not."""
    text = _request_text(request, message)
    if _read_request(message) or not _explicit_write_request(text) or not _write_intent(command, message) and not request:
        return False
    patch = command["patch"]
    if request and request.get("kind") != command["kind"]:
        return False
    if command["kind"] not in {"DAILY", "WEIGHT", "SESSION", "MANAGEMENT_PROFILE", "CALENDAR_EVENT", "SYNC_DATA"}:
        return False
    if any(value is None for value in patch.values()) or patch.get("delete") or patch.get("include_identical_copies"):
        return False
    if command["kind"]=="SYNC_DATA":
        if set(patch)!={"scope"}:
            return False
        scope="FULL"
        for part in text.lower().splitlines():
            if re.search(r"благосъстоян|wellness",part):
                scope="WELLNESS"
            elif re.search(r"възстанов|recovery",part):
                scope="RECOVERY"
            elif re.search(r"всички|пълн|full",part):
                scope="FULL"
        if patch["scope"]!=scope:
            return False
    if command["kind"] in {"DAILY", "WEIGHT"}:
        # Strip only the polite request wrapper, keeping the user's exact
        # observation labels and values. A rating's denominator is not its value.
        literal=re.sub(r"(?:може ли|моля|\?)", "", text.lower())
        if not _automatic_observation(command,entry,literal):
            return False
    if command["kind"] == "SESSION":
        if set(patch)-{"rpe","rpe_source","timing","duration_minutes","feel"}:
            return False
        if "rpe" in patch:
            ratings=re.findall(r"(?<![\d-])(\d{1,2})\s*(?:от|/)\s*10\b",text.lower())
            if not ratings or int(ratings[-1])!=patch["rpe"]:
                return False
        current=entry.get("payload") or {}
        if patch.get("rpe_source","ONFLOWS")!="ONFLOWS":
            return False
        if "timing" in patch and patch["timing"] not in {current.get("timing"), *(_rating_timing(part) for part in text.splitlines())}:
            return False
        if "duration_minutes" in patch and patch["duration_minutes"]!=(current.get("duration_minutes") or entry.get("suggested_duration_minutes")):
            return False
        if "feel" in patch and not re.search(r"(?:усещан|състояни)[^\d]{0,20}"+re.escape(str(patch["feel"])),text.lower()):
            return False
    if command["kind"] == "CALENDAR_EVENT":
        if set(patch)-{"name","start_date","end_date","event_type","race_course","note"}:
            return False
        months=["януари","февруари","март","април","май","юни","юли","август","септември","октомври","ноември","декември"]
        date_text=text
        if request:
            # A later date correction supersedes the initial dates. Keep the
            # separately supplied year/name, but never authorize the old day.
            date_answers=[answer for answer in request.get("answers",[]) if re.search(r"\d{1,4}[./-]\d{1,2}|"+"|".join(months),answer.lower())]
            if date_answers:
                date_text=date_answers[-1]
        for field in ("start_date","end_date"):
            if field not in patch:
                continue
            try:
                selected=date.fromisoformat(patch[field])
            except (ValueError,TypeError):
                return False
            existing=(entry.get("payload") or {}).get(field, "")
            year_grounded=str(selected.year) in text or existing.startswith(str(selected.year)+"-")
            named=(months[selected.month-1] in date_text.lower() and re.search(r"(?<!\d)"+str(selected.day)+r"(?!\d)",date_text))
            dotted=re.search(r"(?<!\d)0?"+str(selected.day)+r"[./]0?"+str(selected.month)+r"(?!\d)",date_text)
            if patch[field] not in date_text and not (year_grounded and (named or dotted)):
                return False
        if "name" in patch and str(patch["name"]).lower() not in text.lower():
            return False
        # Creation/type/terrain have additional semantic choices. A date/name
        # edit preserves them; changes to them receive one explicit preview.
        for field in ("event_type","race_course","note"):
            if field in patch and patch[field]!=(entry.get("payload") or {}).get(field):
                return False
    # These settings alter planning policy or learned scientific controls, and
    # need the exact before/after preview even when the user asks to change them.
    if command["kind"] == "MANAGEMENT_PROFILE":
        if set(patch)-{"planning_controls", "program_start", "program_end"}:
            return False
        lowered=text.lower()
        for field in ("program_start","program_end"):
            if field in patch and patch[field] not in text:
                return False
        controls=patch.get("planning_controls",{})
        if not isinstance(controls,dict):
            return False
        day_labels=["понеделник","вторник","сряда","четвъртък","петък","събота","неделя"]
        requested_days=[index for index,label in enumerate(day_labels) if label in lowered]
        day_fields={"training_days":r"трени|почив", "threshold_days":r"праг", "double_threshold_days":r"двойн.{0,12}праг",
                    "intensity_days":r"интензив", "strength_days":r"сил", "long_session_day":r"продълж|дълг"}
        for field,value in controls.items():
            if field=="weekly_target_hours":
                hours=re.findall(r"(\d+(?:[.,]\d+)?)\s*(?:час\w*|ч\b)",lowered)
                if not any(float(v.replace(",","."))==value for v in hours):
                    return False
            elif field=="sessions_per_week":
                counts=re.findall(r"(?<![\d-])(\d+)\s*(?:трениров\w*|заниман\w*|сеси\w*)",lowered)
                if not any(int(v)==value for v in counts):
                    return False
            elif field in day_fields:
                expected=requested_days
                if field=="training_days" and re.search(r"почив",lowered):
                    expected=[day for day in range(7) if day not in requested_days]
                actual=[value] if type(value) is int else value
                if not requested_days or not re.search(day_fields[field],lowered) or actual!=expected:
                    return False
            else:
                return False
    # Do not silently turn "show a proposal" into a stored change.
    if re.search(r"предлож\w*|първо\s+(?:покажи|преглед)|без\s+(?:запис|запаз)|не\s+(?:записвай|запазвай)", text.lower()):
        return False
    return True


def _input_request(state, message, now):
    """Keep a short clarification thread, sourced only from the user's words.

    Answers complete the original explicit request; they do not authorize unrelated changes.
    The conversation RPC already binds state to actor, athlete and exact grants.
    """
    text = message.lower()
    pending = state.get("input_request")
    if not isinstance(pending, dict) or pending.get("expires_at", "") <= now.isoformat():
        pending = None
    action = _explicit_write_request(message)
    if _write_disallowed(message):
        state.pop("input_request", None)
        return None
    kind = None
    if action:
        if re.search(r"обнов|синхрон",text) and re.search(r"данн|активност|възстанов|благосъстоян|wellness|recovery|intervals",text):
            kind="SYNC_DATA"
        elif re.search(r"мезоцикл|акцент",text):
            kind="MESOCYCLE_PREFERENCES"
        elif re.search(r"периодизац|сезон|седмичен профил",text):
            kind="PLANNING_PROFILE"
        elif re.search(r"събити|старт|страт|състезан|лагер", text):
            kind = "CALENDAR_EVENT"
        elif re.search(r"тегл|теж|килограм|\bкг\b", text):
            kind = "WEIGHT"
        elif re.search(r"rpe|усили|след.{0,30}трениров|трениров.{0,30}(?:от\s*10|/\s*10)", text):
            kind = "SESSION"
        elif re.search(r"умор|сън|стрес|болезнен|мотивац|болк|болен|оценк", text):
            kind = "DAILY"
        elif re.search(r"почив|тренировъчни дни|седмич|планира", text):
            kind = "MANAGEMENT_PROFILE"
        elif re.search(r"hrmax|максимал.{0,10}пулс|границ.{0,10}зон|часов.{0,10}зон", text):
            kind = "ATHLETE_SETTINGS"
        elif re.search(r"тест",text):
            kind = "SPEED_TEST" if re.search(r"скорост|максимал",text) else "TEST"
    if kind:
        if pending and pending.get("kind") == kind:
            pending = {**pending, "answers": [*pending.get("answers", []), message][-8:],
                       "followups": pending.get("followups", 0) + 1}
        else:
            pending = {"kind": kind, "request": message, "answers": [], "expires_at": (now + timedelta(minutes=15)).isoformat(), "followups": 0}
    elif pending:
        # An unrelated read/question ends the write thread. A short answer,
        # date, name, number, or corrected scale can complete its missing fields.
        related = pending.get("kind")=="SESSION" and re.search(r"rpe|усили|скал|оценк|състояни|след.{0,30}трениров|стрес и възстанов|(?:от|до)\s*10", text)
        if ((not related and _read_request(message))
                or text.strip() in CANCEL or pending.get("followups", 0) >= 8):
            pending = None
        else:
            pending = {**pending, "answers": [*pending.get("answers", []), message][-8:], "followups": pending.get("followups", 0) + 1}
    if pending:
        if len(json.dumps(pending,ensure_ascii=False).encode("utf-8"))>32768:
            raise HTTPException(422,"Уточнението е твърде дълго. Изпрати по-кратко само липсващите стойности; предишните уточнения са запазени.")
        state["input_request"] = pending
    else:
        state.pop("input_request", None)
    return pending


def _automatic_observation(command, entry, message):
    """Only exact, explicit low-risk numeric facts bypass the shown preview."""
    text=message.lower()
    if re.search(r"\?|\b(не|ако|вчера|завчера|пример|примерно|означава|означават|значи|значат|"
                 r"как|какво|каква|какъв|какви|колко|дали|защо|кога|къде|кой|коя|кое|кои|ли)\b|(?:^|\s)-\s*\d",text):
        return False
    patch=command["patch"]
    day=command["entry_key"]
    if day != entry.get("today") and day not in text:
        return False
    if command["kind"]=="WEIGHT":
        if set(patch)-{"day","morning_kg"}:
            return False
        if not re.search(r"сутрин",text) or "morning_kg" not in patch:
            return False
        number=patch["morning_kg"]
        matches=re.findall(r"сутрин(?:та)?[^\d]{0,35}(\d+(?:[.,]\d+)?)\s*(?:кг|килограм)",text)
        return type(number) in {int,float} and len(matches)==1 and float(matches[0].replace(",","."))==number
    if command["kind"] != "DAILY":
        return False
    if re.search(r"(?:от\s*10|/\s*10)\b", text):
        return False
    aliases={"fatigue":r"умора(?:та)?|уморен(?:а)?", "sleep_quality":r"качество(?:то)? на съня|сън(?:ят)?",
        "soreness":r"болезненост(?:та)?", "stress":r"стрес(?:ът)?", "motivation":r"мотивация(?:та)?",
        "competition_motivation":r"състезателна мотивация", "sleep_hours":r"спах|часове сън"}
    values={key:value for key,value in patch.items() if key!="day"}
    if not values or set(values)-set(aliases):
        return False
    for field,value in values.items():
        matches=re.findall(r"(?:"+aliases[field]+r")[^\d]{0,20}(\d+(?:[.,]\d+)?)",text)
        if type(value) not in {int,float} or not any(float(v.replace(",","."))==value for v in matches):
            return False
    return True


def _session_rating_entry(repository, alias, actor, state, request, message):
    """Resolve an explicit post-workout x/10 without another model decision.

    Only a fresh activity read supplies identity, duration and revision. The
    supplied rating is retained while asking for its missing observation time;
    answering the missing field completes the user's original write request.
    """
    if not request or request.get("kind")!="SESSION":
        return None
    if request.get("model_clarification"):
        return None
    if _read_request(message) or re.search(r"\?|\b(?:ако|пример|не)\b",message.lower()) and not _explicit_write_request(message):
        return None
    anchor=request.get("request", "")
    if re.search(r"\b(?:пример|ако|не)\b", anchor.lower()):
        return None
    scores=re.findall(r"(?<![\d-])(\d{1,2})\s*(?:от|/)\s*10\b",anchor.lower())
    if len(scores)!=1 or not 0<=int(scores[0])<=10:
        return None
    score=request.get("rpe_value",int(scores[0]))
    correction=re.findall(r"(?<![\d-])(\d{1,2})\s*(?:от|/)\s*10\b",message.lower())
    if message!=anchor and len(correction)==1 and 0<=int(correction[0])<=10 and not re.search(r"\?|\b(?:не|ако|пример)\b",message.lower()):
        score=int(correction[0])
        request["rpe_value"]=score
    if "SESSION" not in _capabilities(_permissions(repository,alias,actor))["write"]:
        return None
    date_text=re.sub(r"(?<![\d-])\d{1,2}\s*/\s*10\b","",anchor)
    selected,clarification=_fallback_day_query(repository,alias,date_text)
    if clarification:
        return None
    requested={"kind":"inputs","input_kind":"SESSION","date":selected.get("date")}
    context=_read(repository,alias,actor,requested)
    entries=context.get("entries") or []
    if context.get("activity_count")!=1 or len(entries)!=1:
        return None
    entry=entries[0]
    if entry.get("kind")!="SESSION" or not isinstance(entry.get("entry_key"),str) or type(entry.get("revision")) is not int:
        return None
    payload=entry.get("payload") or {}
    duration=payload.get("duration_minutes") or entry.get("suggested_duration_minutes")
    if type(duration) not in {int,float} or not 0<duration<=1440:
        return None
    # A follow-up answers timing only; a previous assistant suggestion or a
    # record note can never supply a measurement context.
    timing=None
    text=message.lower()
    if not re.search(r"\?|\b(?:не|ако|пример)\b",text):
        timing=_rating_timing(text)
    timing=timing or payload.get("timing")
    if timing not in {"IMMEDIATE","DELAYED","NEXT_DAY"}:
        if message!=anchor:
            if not correction:
                request["model_clarification"]=True
            return None
        return {"context":context,"message":f"Усилието след тренировката е {score}/10. Кога направи оценката: веднага след края, 10–60 минути по-късно или на следващия ден?"}
    command={"kind":"SESSION","entry_key":entry["entry_key"],"expected_revision":entry["revision"],
        "patch":{"rpe":score,"rpe_source":"ONFLOWS","timing":timing,"duration_minutes":duration}}
    return {"context":context,"command":command,"entry":entry}


def _calendar_restatement(command, message):
    """A literal repetition of shown dates/name is still the same preview."""
    if not command or command.get("kind")!="CALENDAR_EVENT":
        return False
    patch=command.get("patch") or {}
    if set(patch)-{"name","start_date","end_date","include_identical_copies"}:
        return False
    values=[str(v) for k,v in patch.items() if k!="include_identical_copies"]
    if not values or any(value not in message for value in values):
        return False
    remainder=message
    for value in sorted(set(values),key=len,reverse=True):
        remainder=remainder.replace(value,"")
    return not re.sub(r'[\s,.;:!„“"\'–—-]',"",remainder)


def _plan_preview(command, entry):
    summary=(entry or {}).get("preview_summary") or {}
    if summary.get("revision")!=command["expected_revision"]:
        raise HTTPException(409,"Прочети актуалната програма, преди да предложиш действието.")
    patch=command["patch"]
    action=patch.get("action")
    title={"APPROVE":"Утвърждаване на предложената програма", "PAUSE":"Пауза на програмата",
           "RESUME":"Възобновяване на програмата", "REFRESH":"Обновяване на предложението",
           "REST":"Почивка за избрания ден", "SKIP":"Пропускане на избрания ден", "CLEAR":"Премахване на промяната за деня"}.get(action)
    plan=summary.get("approved_plan")
    if command["kind"]=="PLAN_ACTIVATE":
        title="Активиране на избраната програма"
        draft=next((row for row in summary.get("drafts",[]) if row.get("start_date")==patch.get("start_date")
                    and row.get("draft_revision")==patch.get("draft_revision")),None)
        if draft is None:
            raise HTTPException(409,"Прочети точната чернова и нейната версия, преди да я активираш.")
        plan=draft.get("plan")
    elif action=="APPROVE":
        plan=summary.get("proposal")
    if not title:
        raise HTTPException(422,"Посочи конкретното действие за програмата.")
    if (action=="APPROVE" or command["kind"]=="PLAN_ACTIVATE") and (not plan or not plan.get("start_date")
            or not plan.get("end_date") or not plan.get("days")):
        raise HTTPException(409,"Няма пълно прочетено предложение за утвърждаване.")
    lines=[f"Предложена промяна: {title.lower()} · версия {command['expected_revision']}."]
    if command["kind"]=="PLAN_ACTIVATE":
        lines.append(f"Чернова: {patch['start_date']} · версия {patch['draft_revision']}.")
    if plan:
        lines.append(f"Период: {plan.get('start_date')} – {plan.get('end_date')}.")
        for day in plan.get("days",[]):
            if command["kind"]=="PLAN_DAY" and day.get("date")!=patch.get("date"):
                continue
            sessions=day.get("sessions") or []
            if sessions:
                descriptions=[]
                for session in sessions:
                    description=str(session.get("title") or session.get("sport") or "Тренировка")
                    if session.get("total_minutes") is not None:
                        description+=f" · {session['total_minutes']} мин"
                    if session.get("zone"):
                        description+=f" · {session['zone']}"
                    descriptions.append(description)
                lines.append(str(day.get("date"))+": "+"; ".join(descriptions))
            else:
                lines.append(str(day.get("date"))+": "+{"REST":"почивка","SKIP":"пропуснат ден"}.get(day.get("status"),"няма назначена сесия"))
    if command["kind"]=="PLAN_DAY":
        lines.append("Дата на промяната: "+str(patch.get("date")))
    if patch.get("note"):
        lines.append("Бележка: "+str(patch["note"]))
    if action=="REFRESH":
        lines.append("Подготовката се изпълнява във фонов режим; това действие не приема ново предложение.")
    lines.append("Избери „Запиши промяната“ или „Откажи промяната“.")
    return "\n".join(lines)


def _preview(command, entry=None):
    if command["kind"]=="PLAN_GENERATE":
        start_date=command["patch"].get("start_date") or (entry or {}).get("payload",{}).get("start_date")
        if not start_date or command["entry_key"]!=start_date:
            raise HTTPException(422,"Посочи началната дата на новата програма.")
        return (f"Предложена промяна: подготовка на програма от {start_date}.\n"
                "Изчислението се изпълнява във фонов режим и създава чернова за преглед. Активирането е отделно действие.\n"
                "Избери „Запиши промяната“ или „Откажи промяната“.")
    if command["kind"] in {"PLAN_ACTION","PLAN_DAY","PLAN_ACTIVATE"}:
        return _plan_preview(command,entry)
    # A compact literal preview is independent of a model's description.
    labels={"DAILY":"дневна оценка","WEIGHT":"тегло","SESSION":"оценка на тренировка","TEST":"мониторингов тест", "LAB":"лабораторен резултат",
            "MANAGEMENT_PROFILE":"настройки за планиране","CALENDAR_EVENT":"календарно събитие","MANUAL_SPEED_TEST":"максимален скоростен тест",
            "ATHLETE_SETTINGS":"настройки на спортиста","RECOVERY_CONFIG":"настройки за възстановяване","SPEED_TEST":"скоростен тест от записана активност",
            "PLANNING_PROFILE":"профил за сезонно и седмично планиране","MESOCYCLE_PREFERENCES":"предпочитания за мезоцикловите акценти", "SYNC_DATA":"синхронизиране на данните"}
    names={"day":"Дата","morning_kg":"Сутрешно тегло (кг)","morning_standardized":"Стандартизирано сутрешно измерване",
        "fatigue":"Умора (1–5)","sleep_quality":"Качество на съня (1–5)","sleep_hours":"Сън (часове)","soreness":"Болезненост (1–5)",
        "stress":"Стрес (1–5)","motivation":"Мотивация (1–5)","competition_motivation":"Състезателна мотивация (1–5)",
        "pain_or_illness":"Болка или заболяване","note":"Бележка","sessions":"Измервания при сесиите","session":"Номер на сесия",
        "before_kg":"Преди тренировка (кг)","after_kg":"След тренировка (кг)","comparable":"Сравними условия","fluid_l":"Приети течности (л)","urine_l":"Урина (л)",
        "rpe":"Усилие RPE (0–10)","rpe_source":"Източник на оценката","feel":"Усещане (1–5)","timing":"Време на оценката","duration_minutes":"Продължителност (мин)",
        "execution_reason":"Причина за отклонение","method_confirmed":"Потвърден изпълнен метод","executed_method_id":"Изпълнен метод",
        "lactate_samples":"Лактатни измервания","lactate_device":"Уред за лактат","neuromuscular":"Невромускулни наблюдения",
        "planning_controls":"Условия за планиране","available_minutes":"Налични минути по дни","training_days":"Тренировъчни дни",
        "sessions_per_week":"Тренировки за седмицата","sessions_by_day":"Тренировки по дни","threshold_days":"Прагови дни",
        "double_threshold_days":"Дни с двойна прагова тренировка","double_threshold_components":"Компоненти на двойната прагова тренировка",
        "threshold_method":"Прагов метод","intensity_days":"Интензивни дни","strength_days":"Дни за сила","long_session_day":"Ден за продължителна тренировка",
        "mixed_sessions_enabled":"Комбинирани сесии","weekly_target_hours":"Седмична цел (часове)","training_sports":"Видове спорт",
        "event_type":"Вид събитие","name":"Име","start_date":"Начална дата","end_date":"Крайна дата","race_course":"Състезателно трасе","delete":"Изтриване", "include_identical_copies":"Включително еднаквите копия на събитието",
        "program_start":"Начало на програмата","program_end":"Край на програмата","sport":"Спорт","actual_sport":"Реален спорт","discipline":"Дисциплина",
        "duration_s":"Време (сек)","distance_m":"Дистанция (м)","speed_kmh":"Скорост (км/ч)","maximal":"Максимално усилие","flat":"Равен терен",
        "conditions":"Условия","protocol":"Протокол","protocol_version":"Версия на протокола","value":"Стойност","unit":"Мерна единица",
        "laboratory":"Лаборатория","results":"Резултати","sample_id":"Проба","collection_time":"Час на пробата",
        "hrmax_bpm":"Максимална сърдечна честота (уд/мин)","hr_zone_bounds":"Граници на пулсовите зони (уд/мин)",
        "timezone":"Часова зона","activity_ref":"Избрана активност","start_s":"Начало на участъка (сек)",
        "adaptation_mode":"Режим на адаптация","individual_learning":"Самообучение","method_library":"Настройки на методите", "scope":"Данни за обновяване"}
    days=["понеделник","вторник","сряда","четвъртък","петък","събота","неделя"]
    def value(key,item):
        if item is None: return "изчистване"
        if isinstance(item,bool): return "да" if item else "не"
        if key=="timing":
            return {"IMMEDIATE":"непосредствено след края", "DELAYED":"10–60 минути след края", "NEXT_DAY":"на следващия ден", "UNKNOWN":"неуточнено"}.get(item,str(item))
        if key=="scope": return {"FULL":"всички данни", "WELLNESS":"данни за благосъстоянието", "RECOVERY":"данни за възстановяването"}.get(item,str(item))
        if key=="rpe_source": return {"ONFLOWS":"ваша оценка", "INTERVALS":"Intervals"}.get(item,str(item))
        if key=="duration_minutes" and type(item) is float: return f"{item:.2f}".rstrip("0").rstrip(".")
        if isinstance(item,list) and (key.endswith("_days") or key=="training_days"):
            return ", ".join(days[v] if type(v) is int and 0<=v<=6 else str(v) for v in item) or "няма"
        if isinstance(item,(dict,list)): return json.dumps(item,ensure_ascii=False)
        return str(item)
    def fields(patch):
        lines=[]
        for key,item in patch.items():
            if key=="planning_controls" and isinstance(item,dict): lines.extend(fields(item))
            else: lines.append("• "+names.get(key,key.replace("_"," "))+": "+value(key,item))
        return lines
    activity=(entry or {}).get("activity") or {}
    target=" · ".join(str(activity[k]) for k in ("local_date","name") if activity.get(k))
    if command["kind"]=="CALENDAR_EVENT" and command["patch"].get("include_identical_copies"):
        count=(entry or {}).get("identical_copy_count",0)
        if type(count) is not int or count<2:
            raise HTTPException(409,"Прочети отново събитието и еднаквите му копия преди общата промяна.")
        target=f"Обхват: {count} еднакви записа на същото събитие."
    if not target and re.fullmatch(r"\d{4}-\d{2}-\d{2}",command.get("entry_key", "")):
        target="Дата: "+command["entry_key"]
    return (f"Предложена промяна: {labels.get(command['kind'],'настройки на профила')}.\n"
            +(target+"\n" if target else "")
            +"\n".join(fields(command["patch"]))
            +"\nИзбери „Запиши промяната“ или „Откажи промяната“.")


def _receipt_message(receipt, command=None, entry=None):
    if receipt.get("conflict") is True or receipt.get("status")=="CONFLICT":
        return "Записът е променен от друга редакция. Тази промяна не е приложена. Зареди актуалната версия и опиши отново искането."
    if receipt.get("saved") is not True and receipt.get("status") not in {"SAVED","SUCCEEDED","COMMITTED"}:
        return "Резултатът от записа още не е потвърден. Провери статуса на същата заявка."
    kind=(command or {}).get("kind") or receipt.get("kind")
    action=((command or {}).get("patch") or {}).get("action") or receipt.get("plan_operation")
    if kind=="SYNC_DATA":
        scope=(receipt.get("sync") or {}).get("scope") or (receipt.get("job") or {}).get("scope") or ((command or {}).get("patch") or {}).get("scope")
        label={"WELLNESS":"данните за благосъстоянието", "RECOVERY":"данните за възстановяването"}.get(scope,"данните")
        return f"Синхронизирането на {label} е заявено. Данните се обновяват във фонов режим; заявката не означава, че обработката вече е приключила."
    if kind=="PLAN_GENERATE":
        status=(receipt.get("job") or {}).get("status")
        if status=="SUCCEEDED":
            return "Черновата за този период вече е подготвена. Тя може да бъде прегледана и активирана."
        if status in {"FAILED","CANCELLED"}:
            return "Подготовката на черновата не е завършила успешно. Нова програма не е активирана."
        return "Подготовката на новата програма е заявена. Черновата се изчислява във фонов режим и предстои да бъде прегледана и активирана."
    if receipt.get("queued") or receipt.get("refresh_state")=="QUEUED":
        return "Обновяването е заявено. Предложението се подготвя във фонов режим; текущата утвърдена програма остава активна."
    if kind in {"PLAN_ACTION","PLAN_DAY","PLAN_ACTIVATE"}:
        messages={"APPROVE":"Предложението за тренировъчна програма е утвърдено.","PAUSE":"Програмата е поставена на пауза.",
            "RESUME":"Програмата е възобновена.","REST":"За избрания ден е зададена почивка.",
            "SKIP":"Избраният ден е отбелязан като пропуснат.","CLEAR":"Промяната за избрания ден е премахната."}
        if kind=="PLAN_ACTIVATE":
            return "Избраната програма е активирана."
        return messages.get(action,"Действието за програмата е изпълнено.")
    message="Входът е записан."
    if command:
        summary_command=command
        if command["patch"].get("include_identical_copies") and not entry:
            summary_command={**command,"patch":{k:v for k,v in command["patch"].items() if k!="include_identical_copies"}}
        details=_preview(summary_command,entry).splitlines()[1:-1]
        if details:
            message += "\n" + "\n".join(details)
        if command["kind"] in {"MANAGEMENT_PROFILE", "CALENDAR_EVENT", "ATHLETE_SETTINGS", "RECOVERY_CONFIG", "SPEED_TEST", "MANUAL_SPEED_TEST", "PLANNING_PROFILE", "MESOCYCLE_PREFERENCES"}:
            message += "\nПромяната е във входните данни. Текущата утвърдена програма остава активна до приемане на ново предложение."
    return message


def _fallback_day_query(repository, alias, message):
    """Resolve the fallback's small date vocabulary without changing the day."""
    normalized=message.lower().strip()
    explicit=set(re.findall(r"\b\d{4}-\d{2}-\d{2}\b",normalized))
    relative=set()
    for pattern,offset in ((r"\b(?:днес|днеш\w*)\b",0),
                           (r"\b(?:утре|утреш\w*)\b",1),
                           (r"\b(?:вчера|вчераш\w*)\b",-1)):
        if re.search(pattern,normalized):
            relative.add(offset)
    clarification="За коя точна дата да покажа програмата? Поддържам „днес“, „утре“, „вчера“ или дата като 2026-11-15."
    if len(explicit)>1 or len(relative)>1 or (explicit and relative):
        return None,clarification
    # Partial, named and offset dates require clarification. Never replace one
    # of these unsupported expressions with today's prescription.
    remainder=re.sub(r"\b\d{4}-\d{2}-\d{2}\b","",normalized)
    unclear=(r"\b\d{1,4}[./-]\d{1,2}(?:[./-]\d{1,4})?\b"
        r"|\b(?:понеделник|вторник|сряда|четвъртък|петък|събота|неделя|завчера|другиден|вдругиден)\b"
        r"|\b(?:януари|февруари|март|април|май|юни|юли|август|септември|октомври|ноември|декември)\b"
        r"|\b(?:след|преди)\s+\d+\s+д(?:ен|ни)\b|\bседмиц\w*\b"
        r"|\b(?:на|за)\s+\d{1,2}\b")
    if re.search(unclear,remainder):
        return None,clarification
    if explicit:
        selected=next(iter(explicit))
        try:
            date.fromisoformat(selected)
        except ValueError:
            return None,"Посочи валидна календарна дата във формат ГГГГ-ММ-ДД."
        return {"kind":"day","date":selected},None
    if relative and relative!={0}:
        reader=getattr(repository,"athlete_settings",None)
        settings=reader(alias) if callable(reader) else None
        if settings is None:
            return None,"Не е налична часовата зона на спортиста. Посочи точната дата във формат ГГГГ-ММ-ДД."
        try:
            local_today=datetime.now(timezone.utc).astimezone(ZoneInfo(settings.timezone)).date()
        except (AttributeError,TypeError,ValueError,ZoneInfoNotFoundError):
            return None,"Не е налична валидна часова зона на спортиста. Посочи точната дата във формат ГГГГ-ММ-ДД."
        return {"kind":"day","date":(local_today+timedelta(days=next(iter(relative)))).isoformat()},None
    # The canonical day reader resolves 'today' in the athlete's timezone.
    return {"kind":"day"},None


def _fallback(repository,alias,actor,message):
    normalized=message.lower().strip()
    if re.search(r"(още|добави).{0,25}(отсеч|интервал|повторени)",normalized):
        return UNAVAILABLE_SESSION_CHANGE, None
    if any(word in normalized for word in ["умор", "умора"]):
        return "Оцени умората от 1 до 5: 1 — без умора, 2 — лека, 3 — умерена, 4 — силна, 5 — много силна. За запис използвай дневната форма „Стрес и възстановяване“, докато разговорният модел е изключен.",None
    if any(word in normalized for word in ["статус","възстанов","готовност"]):
        context=_read(repository,alias,actor,{"kind":"status"})
        return _render_status(context),context
    if any(word in normalized for word in ["днес","доз","изпълн","тренирам","тренировката"]):
        requested,clarification=_fallback_day_query(repository,alias,message)
        if requested is None:
            return clarification,None
        context=_read(repository,alias,actor,requested)
        text=_render_day(context)
        return text,context
    return ("Мога да покажа запазената тренировка за днес и нейното изпълнение. Разговорният модел още не е включен за тази среда. Формулярите за оценки, тестове и планиране остават достъпни.",None)


def _render_status(context):
    if context.get("availability")=="PENDING":
        return "Анализът се подготвя във фонов режим. Все още няма готова справка за възстановяването."
    lines=["Справка от запазения анализ"+(f" към {context['as_of']}" if context.get("as_of") else "")+"."]
    if context.get("freshness")=="STALE":
        lines.append("Анализът е от предишен ден. Провери последното обновяване.")
    recovery=context.get("recovery") or {}
    available=recovery.get("availability")
    if available not in {"AVAILABLE","ESTIMATED","STALE"}:
        lines.append("Няма допустима числена оценка за възстановяването. Данните за натоварването са непълни или оценката още не е подготвена.")
    else:
        lines.append("Дневна моделна оценка на готовността по компоненти:")
        for row in recovery.get("current",[]):
            number=row.get("readiness_percent")
            if isinstance(number,(int,float)) and not isinstance(number,bool):
                lines.append(f"• {row.get('zone','Компонент')}: {number:g}%")
        if available=="ESTIMATED": lines.append("Използвана е оценка на част от натоварването.")
        if available=="STALE": lines.append("Оценката е от по-стар анализ.")
        lines.append("Това е резултат от модела за дневния товар, а не измерен физиологичен процент или точен час за готовност.")
    lines.append("Разговорното тълкуване е налично след включване на езиковия модел; подробностите са в „Стрес и възстановяване“.")
    return "\n".join(lines)


def _duration_label(value, *, seconds=False):
    """Render stored durations to the nearest second, not float artifacts."""
    unit="сек" if seconds else "мин"
    if type(value) not in {int,float} or not isfinite(value):
        return str(value)+" "+unit
    total_seconds=round(value if seconds else value*60)
    if total_seconds<0:
        return f"{value:g} {unit}"
    minutes,remainder=divmod(total_seconds,60)
    if not remainder:
        return f"{minutes} мин"
    if seconds and minutes==0:
        return f"{remainder} сек"
    return f"{minutes}:{remainder:02d} мин"


def _render_blocks(blocks, prefix="• "):
    lines=[]
    kind_labels={"WORK":"Работна отсечка","RECOVERY":"Възстановяване","WARMUP":"Загряване",
                 "COOLDOWN":"Заключителна част","REST":"Почивка"}
    for block in blocks:
        if block.get("kind")=="REPEAT_GROUP":
            lines.append(prefix+f"{block['repeat_count']} повторения на следната последователност:")
            lines.extend(_render_blocks(block["sequence"],prefix="  – "))
            continue
        title=block.get("label") or kind_labels.get(block.get("kind"),block.get("kind")) or "Част от тренировката"
        details=[]
        if block.get("duration_min") is not None: details.append(_duration_label(block["duration_min"]))
        elif block.get("duration_s") is not None: details.append(_duration_label(block["duration_s"],seconds=True))
        if block.get("zone"): details.append(str(block["zone"]))
        if block.get("target_speed_kmh") is not None: details.append(f"{block['target_speed_kmh']} км/ч")
        if block.get("target_hr_bpm") is not None: details.append(f"{block['target_hr_bpm']} уд/мин")
        lines.append(prefix+str(title)+(": "+", ".join(details) if details else ""))
        if isinstance(block.get("instructions"),str): lines.append(block["instructions"])
    return lines


def _render_day(context):
    # Also useful during provider outage; do not manufacture a workout.
    if context.get("availability") in {"UNAVAILABLE","PENDING"}:
        return "Няма налична утвърдена програма за тази дата. Отвори „Седмична програма“, за да провериш текущия план и предложенията."
    lines=[f"Програма за {context.get('date',context.get('today','днес'))} · версия {context.get('plan_revision','неизвестна')}"]
    if context.get("prescription_active") is False:
        lines.append("Програмата е спряна. Показаните сесии са запазени за справка.")
    sessions=context.get("sessions") or []
    if not sessions:
        lines.append("Почивка според запазения план." if context.get("day_status") in {"REST","SKIPPED"} else "В запазения ден няма сесии. Това не установява изпълнена почивка.")
    for index,session in enumerate(sessions,1):
        duration=session.get("total_minutes")
        lines.append(f"\n{index}. {session.get('title') or session.get('method_id') or 'Тренировъчна сесия'}"+(" · "+_duration_label(duration) if isinstance(duration,(int,float)) else ""))
        lines.extend(_render_blocks(session.get("blocks",[])))
        evidence=session.get("dose_evidence") or {}
        if isinstance(evidence.get("explanation"),str): lines.append("Основание за дозата: "+evidence["explanation"])
    lines.append("\nТочната структура остава достъпна и в „Седмична програма“.")
    return "\n".join(lines)[:18000]


def _resolved_answer(contexts, input_request=None):
    """A tool budget limit does not mean the user's question was ambiguous."""
    if not input_request:
        for item in reversed(contexts):
            if item.get("query_kind") in {"day", "method"} and "sessions" in item and "date" in item:
                return _render_day(item)
        for item in reversed(contexts):
            if item.get("query_kind")=="status":
                return _render_status(item)
    return "Не успях да завърша обработката на тази заявка. Данните не са променени. Можеш да продължиш със същото искане."


def overview(repository,alias,actor,conversation_id=None,operation_id=None,provider=None):
    _permissions(repository,alias,actor)
    provider=_configured_provider(repository) if provider is None else provider
    base={"provider_available":_provider_available(provider),"retention_days":retention_days(),"suggestions":SUGGESTIONS}
    if operation_id:
        operation=_uuid(operation_id)
        value=_rpc(repository,"STATUS",alias,actor,conversation_id,operation)
        if value.get("response"):
            return {**value["response"],"status":"COMPLETED"}
        # A domain receipt survives a process failure after a committed write.
        receipt=assistant_commands.operation_status(repository,alias,actor,operation)
        settled=receipt and receipt.get("status") not in {"unknown","UNKNOWN","NOT_FOUND"}
        old=False
        if value.get("created_at"):
            old=datetime.now(timezone.utc)-datetime.fromisoformat(value["created_at"].replace("Z","+00:00"))>timedelta(minutes=10)
        if value.get("conversation_id") and value.get("status")=="PENDING" and (settled or old):
            conversation=value["conversation_id"]
            saved=_rpc(repository,"READ",alias,actor,conversation)
            response={**base,"conversation_id":conversation,"operation_id":operation,
                "message":_receipt_message(receipt) if settled else "Обработката е прекъсната. Не е потвърден запис. Провери стойностите във формуляра, преди да зададеш нова промяна.",
                **({"receipt":receipt} if settled else {})}
            recovered_state=dict(saved.get("state") or {})
            if settled:
                recovered_state.pop("pending_command",None)
                recovered_state.pop("pending_command_at",None)
            response=_rpc(repository,"FINISH",alias,actor,conversation,operation,{"response":response,"state":recovered_state})
            return {**response,"status":"COMPLETED"}
        if settled:
            return {**base,"conversation_id":value.get("conversation_id"),"operation_id":operation,"receipt":receipt,
                    "message":_receipt_message(receipt),"status":"COMPLETED"}
        return {**base,**value,"message":"Заявката още се обработва. Не е потвърден нов запис.","pending":value.get("status")=="PENDING"}
    value=_rpc(repository,"READ" if conversation_id else "LATEST",alias,actor,_uuid(conversation_id) if conversation_id else None)
    return {**base,"conversation_id":value.get("conversation_id"),"messages":_messages(value.get("messages",[])),
        "pending_operation":value.get("pending_operation")}


def clear(repository,alias,actor,conversation_id):
    _permissions(repository,alias,actor)
    return _rpc(repository,"CLEAR",alias,actor,_uuid(conversation_id))


def turn(repository,alias,actor,body,provider=None):
    started=monotonic()
    permissions=_permissions(repository,alias,actor)
    message=body.get("message")
    if not isinstance(message,str) or not message.strip() or len(message)>4000:
        raise HTTPException(422,"Въведи съобщение до 4000 знака.")
    message=message.strip()
    operation=_uuid(body.get("operation_id"))
    conversation=_uuid(body["conversation_id"]) if body.get("conversation_id") else None
    start=_rpc(repository,"BEGIN",alias,actor,conversation,operation,{"message":message})
    conversation=start["conversation_id"]
    if start.get("replayed"):
        return start.get("response") or {"conversation_id":conversation,"operation_id":operation,"pending":True,"message":"Предишната заявка още се обработва. Провери статуса ѝ."}
    state=dict(start.get("state") or {})
    original_state=json.loads(json.dumps(state))
    result={"conversation_id":conversation,"operation_id":operation,"message":"","sources":[],"suggestions":SUGGESTIONS}
    provider=_configured_provider(repository) if provider is None else provider
    result["provider_available"]=_provider_available(provider)
    context=None
    contexts=[]
    totals=None
    now=datetime.now(timezone.utc)
    state["preferences"]=[p for p in state.get("preferences",[]) if isinstance(p,dict) and isinstance(p.get("expires_at"),str)
        and p["expires_at"]>now.isoformat()]
    try:
        normalized=message.lower().strip().rstrip(".! ")
        input_request=_input_request(state,message,now)
        pending_change=state.get("pending_command")
        if normalized not in CONFIRM:
            # A correction needs a new preview; an unrelated question must not
            # leave an older write waiting behind a later conversational “да”.
            state.pop("pending_command",None)
            state.pop("pending_command_at",None)
        if normalized in {"не ме подсещай","изключи подсещанията"}:
            state["nudges_enabled"]=False
            result["message"]="Подсещанията в този разговор са изключени."
        elif normalized in {"включи подсещанията","подсещай ме"}:
            state["nudges_enabled"]=True
            result["message"]="Подсещанията са включени: до едно на ден, между 8 и 20 ч. по часовата зона на спортиста."
        elif normalized in {"отложи за утре","по-късно"}:
            state["snoozed_until"]=(datetime.now(timezone.utc)+timedelta(days=1)).isoformat()
            result["message"]="Подсещанията са отложени за 24 часа."
        elif normalized in {"забрави предпочитанията","изтрий предпочитанията"}:
            state["preferences"]=[]
            result["message"]="Потвърдените предпочитания в този разговор са изтрити. Научните наблюдения се запазват."
        elif normalized.startswith("запомни:"):
            content=message.split(":",1)[1].strip()
            if not content or len(content)>500:
                raise HTTPException(422,"Опиши предпочитанието с до 500 знака.")
            now=datetime.now(timezone.utc)
            preference={"content":content,"source":"EXPLICIT_USER_REQUEST","recorded_at":now.isoformat(),"scope":"THIS_ACTOR_AND_ATHLETE","expires_at":(now+timedelta(days=90)).isoformat()}
            state["preferences"]=[*state.get("preferences",[])[-9:],preference]
            result["message"]="Предпочитанието е запазено като контекст на този разговор за до 90 дни. То не променя тренировъчните коефициенти."
        elif normalized in CANCEL:
            state.pop("pending_command",None)
            state.pop("pending_command_at",None)
            state.pop("input_request",None)
            result["message"]="Предложената промяна е отказана."
        elif normalized in CONFIRM and (state.get("pending_command") or normalized in {"запиши промяната", "потвърждавам промяната"}):
            pending=state.pop("pending_command",None)
            created=state.pop("pending_command_at",None)
            expired=bool(created and datetime.now(timezone.utc)-datetime.fromisoformat(created)>timedelta(minutes=15))
            if not isinstance(pending,dict):
                result["message"]="Няма показана промяна за запис. Опиши какво искаш да промениш."
            elif expired:
                refresh_query={"kind":"inputs","input_kind":pending["kind"],"key":pending["entry_key"]}
                if pending["kind"]=="PLAN_ACTIVATE":
                    refresh_query.update(date=pending["patch"].get("start_date"),revision=pending["patch"].get("draft_revision"))
                context=_read(repository,alias,actor,refresh_query)
                entries=context.get("entries") or [context]
                entry=next((row for row in entries if (row.get("input_kind") or row.get("kind"))==pending["kind"]
                    and row.get("entry_key", "")==pending["entry_key"] and type(row.get("revision")) is int),None)
                if entry is None or context.get("availability") in {"UNAVAILABLE","MISSING","FORBIDDEN"}:
                    result["message"]="Предишното предложение е изтекло, а записът вече не е наличен. Опиши актуалната промяна."
                else:
                    refreshed={**pending,"expected_revision":entry["revision"]}
                    state["pending_command"]=refreshed
                    state["pending_command_at"]=datetime.now(timezone.utc).isoformat()
                    result.update(message="Проверих актуалния запис, защото предложението е от предишен разговорен момент.\n"+_preview(refreshed,entry),
                                  suggestions=["Запиши промяната","Откажи промяната"])
            else:
                receipt=assistant_commands.execute(repository,alias,actor,operation,pending)
                result.update(receipt=receipt,message=_receipt_message(receipt,pending))
            state.pop("input_request",None)
        elif re.search(r"(още|добави).{0,25}(отсеч|интервал|повторени)",normalized):
            result["message"]=UNAVAILABLE_SESSION_CHANGE
        elif _provider_available(provider) and (session_entry:=_session_rating_entry(repository,alias,actor,state,input_request,message)):
            context=session_entry.pop("context")
            direct_command=session_entry.pop("command", None)
            if direct_command:
                entry=session_entry.pop("entry")
                receipt=assistant_commands.execute(repository,alias,actor,operation,direct_command)
                result.update(receipt=receipt,message=_receipt_message(receipt,direct_command,entry))
                state.pop("input_request",None)
            else:
                result.update(session_entry)
        elif input_request and _calendar_restatement(pending_change,message):
            context=_read(repository,alias,actor,{"kind":"inputs","input_kind":"CALENDAR_EVENT","key":pending_change["entry_key"]})
            if context.get("revision")!=pending_change["expected_revision"]:
                raise HTTPException(409,"Календарът е променен. Прочети актуалното събитие преди новото предложение.")
            preview=_preview(pending_change,context)
            state["pending_command"]=pending_change
            state["pending_command_at"]=datetime.now(timezone.utc).isoformat()
            result.update(message=preview,suggestions=["Запиши промяната","Откажи промяната"])
        elif not _provider_available(provider):
            result["message"],context=_fallback(repository,alias,actor,message)
        else:
            messages=_messages(start.get("messages",[]))+[{"role":"user","content":message}]
            contexts=[]
            completed_queries=[]
            query_keys=[]
            settings_reader=getattr(repository,"athlete_settings",None)
            settings=settings_reader(alias) if callable(settings_reader) else None
            athlete_timezone=settings.timezone if settings else "UTC"
            local_today=datetime.now(timezone.utc).astimezone(ZoneInfo(athlete_timezone)).date().isoformat()
            totals={"model_calls":0,"input_tokens":0,"output_tokens":0,"estimated_cost_usd":0.0}
            query_feedback=None
            for step in range(4):
                timeout=getattr(getattr(provider,"config",None),"timeout_seconds",20)
                if step and monotonic()-started+timeout+5>90:
                    result["message"]=_resolved_answer(contexts,input_request)
                    break
                synthesis_only=step==3
                capabilities=_capabilities(_permissions(repository,alias,actor))
                if synthesis_only:
                    capabilities={**capabilities,"read":[],"write":[]}
                reply=provider.complete(messages, {"results":contexts,"confirmed_context":state.get("preferences",[]),
                    "completed_queries":completed_queries,"remaining_model_calls":4-step,"remaining_read_calls":max(0,3-step),
                    "query_feedback":query_feedback,
                    "final_answer_only":synthesis_only,"synthesis_reason":"REPEATED_READ" if synthesis_only and query_feedback else "READ_BUDGET" if synthesis_only else None,"read_targets":state.get("read_targets",[]),
                    "pending_change":pending_change if input_request else None,"now":datetime.now(timezone.utc).isoformat(),
                    "active_input_request":input_request,
                    "athlete_timezone":athlete_timezone,"today":local_today,
                    "new_input_keys":{"CALENDAR_EVENT":"event_"+UUID(operation).hex,
                        "LAB":UUID(operation).hex,"MANUAL_SPEED_TEST":"manual_"+UUID(operation).hex}},capabilities)
                for key in totals:
                    value=reply.get("usage",{}).get(key,0)
                    totals[key]=None if key=="estimated_cost_usd" and (totals[key] is None or value is None) else totals[key]+(value or 0)
                if synthesis_only and (reply.get("query") or reply.get("command")):
                    result["message"]=_resolved_answer(contexts,input_request)
                    break
                if reply.get("query"):
                    requested=reply["query"]
                    query_key=json.dumps({k:v for k,v in requested.items() if v is not None}
                        if isinstance(requested,dict) else requested,sort_keys=True)
                    if query_key in query_keys:
                        # Results are current to this turn. Repeating a read
                        # cannot consume the whole dialogue or duplicate data.
                        _permissions(repository,alias,actor)
                        context=contexts[query_keys.index(query_key)]
                        # Replay the completed call/result and explain the
                        # duplicate without spending another store read. Keep
                        # the next read slot available for a different domain;
                        # the fourth model call is always answer-only.
                        query_feedback={"status":"ALREADY_COMPLETED","query":requested,
                            "result_index":query_keys.index(query_key),
                            "instruction":"Use the completed result. Read another needed domain if a read slot remains; otherwise answer."}
                        continue
                    context=_read(repository,alias,actor,requested)
                    query_feedback=None
                    # These are separate app forms within the same planning
                    # intent. A canonical input read resolves the form without
                    # requiring the user to repeat an earlier clarification.
                    planning_forms={"MANAGEMENT_PROFILE","PLANNING_PROFILE","MESOCYCLE_PREFERENCES"}
                    if (input_request and input_request.get("kind") in planning_forms and requested.get("kind")=="inputs"
                            and requested.get("input_kind") in planning_forms):
                        input_request["kind"]=requested["input_kind"]
                    contexts.append(context)
                    completed_queries.append(requested)
                    query_keys.append(query_key)
                    # Keep only scoped selectors between turns. Every payload
                    # must be freshly read, including after a plan approval.
                    target={k:v for k,v in requested.items() if v is not None}
                    for key in ("date", "session_id", "activity_ref"):
                        if context.get(key) and key not in target:
                            target[key]=context[key]
                    state["read_targets"]=[*state.get("read_targets",[])][-3:]+[target]
                    continue
                if reply.get("command"):
                    command=_command(reply)
                    current=_permissions(repository,alias,actor)
                    if command["kind"] not in _capabilities(current)["write"]:
                        raise HTTPException(403,"Нямаш право за тази промяна.")
                    # Every write is based on a current inputs query in this turn;
                    # the domain service independently rechecks the CAS revision.
                    entry=_matching_input(command,contexts)
                    if entry is None:
                        raise HTTPException(409,"Първо е необходим прочит на актуалния запис и неговата версия.")
                    continuing=bool(input_request and input_request["kind"]==command["kind"])
                    if _write_disallowed(message) or _read_request(message) or not _write_intent(command,message) and not continuing:
                        raise HTTPException(422,"В текущото съобщение няма изрично искане за тази промяна. Опиши стойността, която искаш да запишеш.")
                    if input_request and input_request["kind"]=="SESSION" and command["kind"]=="DAILY":
                        raise HTTPException(422,"Оценката след тренировката е усилие RPE по скала 0–10. Тя се записва към изпълнената активност, без преобразуване в дневна умора 1–5.")
                    entry={**entry,"today":contexts[-1].get("today")}
                    if _automatic_observation(command,entry,message) or _direct_request(command,entry,input_request,message):
                        receipt=assistant_commands.execute(repository,alias,actor,operation,command)
                        result.update(receipt=receipt,message=_receipt_message(receipt,command,entry))
                        state.pop("input_request",None)
                    else:
                        preview=_preview(command,entry)
                        state["pending_command"]=command
                        state["pending_command_at"]=datetime.now(timezone.utc).isoformat()
                        result.update(message=preview,suggestions=["Запиши промяната","Откажи промяната"])
                    break
                result["message"]=str(reply.get("text") or "Липсва отговор. Опитай с по-конкретен въпрос.")[:18000]
                first_person_claim=re.search(r"\b(записах|запазих|утвърдих|одобрих)\b",result["message"].lower())
                stored_claim=re.search(r"(?:входът|промяната|стойността|оценката|теглото|данните|резултатът)\s+(?:е|са)\s+(?:записан[аои]?|запазен[аои]?)\b|^(?:записан[аои]?|запазен[аои]?)\b",result["message"].lower())
                if first_person_claim or stored_claim and (input_request or _explicit_write_request(message) or not contexts):
                    result["message"]="Няма потвърден нов запис от тази заявка. Опиши конкретната промяна или използвай съответния формуляр."
                break
            else:
                result["message"]=_resolved_answer(contexts,input_request)
            result["usage"]=totals
        result["sources"]=[source for item in (contexts or [context]) for source in _sources(item)]
        # One in-app prompt per local day, only after a plan read. The current
        # observation is checked first; no background model loop is involved.
        now=datetime.now(timezone.utc)
        if (context and context.get("query_kind")=="day" and _provider_available(provider) and permissions.get("is_owner")
            and permissions.get("can_view_recovery") and state.get("nudges_enabled",True)
            and not state.get("pending_command") and not result.get("receipt")):
            local=now.astimezone(ZoneInfo(context.get("timezone","UTC")))
            snooze=state.get("snoozed_until","")
            if (8<=local.hour<20 and snooze<now.isoformat() and state.get("last_nudge_day")!=local.date().isoformat()):
                try:
                    daily=_read(repository,alias,actor,{"kind":"inputs","input_kind":"DAILY"})
                except HTTPException as exc:
                    if exc.status_code in {401,403,412}:
                        raise
                    daily=None
                except PersistentStoreFailure:
                    daily=None
                if daily is not None and (daily.get("payload") or {}).get("fatigue") is None:
                    result["message"]+="\n\nЛипсва днешна оценка на умората. Каква е тя от 1 (без умора) до 5 (много силна)?"
                    result["suggestions"]=["Умората ми е 2 от 5","Умората ми е 4 от 5","Отложи за утре","Не ме подсещай"]
                    state["last_nudge_day"]=local.date().isoformat()
    except AssistantProviderError as exc:
        if exc.code == "budget_exhausted":
            result["message"]="Тестовият бюджет на ИИ асистента е изчерпан или спрян. Платените заявки са прекратени до изрично добавяне на нов кредит. Запазената програма и формулярите остават достъпни."
            result["error_code"]="budget_exhausted"
        elif exc.code in {"budget_unavailable", "invalid_budget", "budget_usage_unknown", "budget_model_not_allowed"}:
            result["message"]="Не може да се провери остатъкът от тестовия бюджет. Платените заявки са спрени. Можеш да използваш запазената програма и формулярите."
            result["error_code"]="budget_unavailable"
        elif exc.code == "credit_balance_exhausted":
            result["message"]="Кредитът при доставчика на ИИ услугата е изчерпан. Платените заявки са спрени до ново зареждане. Запазената програма и формулярите остават достъпни."
            result["error_code"]="credit_balance_exhausted"
        elif exc.code == "provider_limit_exhausted":
            result["message"]="Достигнат е лимитът при доставчика на ИИ услугата. Платените заявки са спрени до промяна на разрешения лимит. Запазената програма и формулярите остават достъпни."
            result["error_code"]="provider_limit_exhausted"
        elif exc.code == "input_too_large":
            result["message"]="Справката е твърде обемна за една заявка. Посочи конкретна дата или сесия, за да я разгледаме подробно. Не е потвърден нов запис."
            result["error_code"]="input_too_large"
        else:
            result["message"]="Разговорният модел временно не отговаря. Не е потвърден нов запис. Можеш да използваш формулярите и запазената програма."
    except HTTPException as exc:
        # 403/412 never return previously read data after access changed.
        if exc.status_code in {401,403,412}:
            raise
        result["message"]=str(exc.detail)
        result["error_code"]=exc.status_code
    if totals is not None:
        # Retain measured usage from completed calls if a later tool/provider
        # fails; do not invent token counts for an unsuccessful provider call.
        result["usage"]=totals
    # Final authorization + persisted scope check prevent cached grants leaking
    # data after provider latency. Never invent success on a storage timeout.
    _permissions(repository,alias,actor)
    if len(json.dumps(state,ensure_ascii=False).encode("utf-8"))>65536:
        # No user values or preferences are silently truncated. The prior
        # conversation remains usable after an overlarge proposed state.
        state=original_state
        state.pop("pending_command",None)
        state.pop("pending_command_at",None)
        result.update(message="Промяната е твърде обемна за едно съобщение. Изпрати по-кратко конкретните полета, които искаш да промениш.",error_code=422)
    if len(json.dumps(result,ensure_ascii=False).encode("utf-8"))>60000:
        state.pop("pending_command",None)
        state.pop("pending_command_at",None)
        result.update(message="Предложението е твърде обемно за един преглед. Посочи по-кратък период или конкретна сесия, за да покажем целия обхват преди промяната.",error_code=422)
    finished=_rpc(repository,"FINISH",alias,actor,conversation,operation,{"response":result,"state":state})
    scenario=("write_"+result["receipt"]["kind"] if isinstance(result.get("receipt"),dict) and result["receipt"].get("kind") in WRITE_KINDS
        else "read_"+context["query_kind"] if context and context.get("query_kind") in READ_KINDS else "dialogue")
    usage=result.get("usage") or {}
    logger.info("assistant_turn scenario=%s duration_ms=%d model_calls=%d input_tokens=%d output_tokens=%d estimated_cost_usd=%s",
        scenario,round((monotonic()-started)*1000),usage.get("model_calls",0),usage.get("input_tokens",0),usage.get("output_tokens",0),usage.get("estimated_cost_usd"))
    return finished
