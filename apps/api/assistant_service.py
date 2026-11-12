"""Bounded conversation orchestration; training decisions stay in canonical services."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
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
WRITE_KINDS = {"DAILY", "WEIGHT", "SESSION", "TEST", "LAB", "BLOCK", "MANAGEMENT_PROFILE", "CALENDAR_EVENT", "MANUAL_SPEED_TEST"}
READ_KINDS = {"day", "status", "history", "method", "inputs"}
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
    if access.get("can_view_recovery"):
        if access.get("is_owner"):
            writes.extend(["DAILY","WEIGHT","SESSION"])
        if access.get("can_edit_plan"):
            writes.extend(["TEST","LAB","BLOCK","MANUAL_SPEED_TEST"])
    if access.get("can_edit_plan") and access.get("can_view_plan"):
        writes.extend(["MANAGEMENT_PROFILE","CALENDAR_EVENT"])
    return {"read": sorted(READ_KINDS), "write": writes,
        "plan_approval":"Only the explicit existing /management interface accepts a shown proposal/revision.",
        "session_dose_change":"UNAVAILABLE_NO_CANONICAL_PREVIEW",
        "input_contract":"Read inputs first. command key=entry_key, expected_revision=current revision; patch only supplied fields. No approval_mode or scientific settings.",
        "scales":{"daily":"1–5, higher=worse; motivation 1=high, 5=low", "rpe":"0–10, timing required", "feel":"1–5, higher=worse"}}


def _sources(context):
    if not context:
        return []
    return [{"kind":str(context.get("query_kind","onflows")), "label":"Запазени данни на onFlows",
             "revision":context.get("plan_revision",context.get("revision")),
             "as_of":context.get("as_of") or (context.get("input_snapshot") or {}).get("analysis_as_of")}]


def _read(repository, alias, actor, query):
    # No cached grant is reused for tool calls, including calls after a model response.
    access = _permissions(repository,alias,actor)
    if not isinstance(query,dict) or query.get("kind") not in READ_KINDS:
        raise HTTPException(422,"Неподдържана справка.")
    return assistant_context.read(repository,alias,access,query)


def _command(reply):
    raw = reply.get("command")
    if not isinstance(raw,dict) or raw.get("kind") not in WRITE_KINDS:
        raise HTTPException(422,"Неподдържана команда.")
    try:
        patch = json.loads(raw["patch_json"]) if isinstance(raw.get("patch_json"),str) else raw.get("patch")
    except (ValueError,TypeError) as exc:
        raise HTTPException(422,"Невалидна частична промяна.") from exc
    if not isinstance(patch,dict) or not patch or len(json.dumps(patch,ensure_ascii=False))>12000:
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


def _write_intent(command, message):
    # Retrieved notes can never supply the current user's intention to write.
    patterns = {"DAILY":r"умор|сън|спах|стрес|болезнен|мотивац|болк|болен|оценк",
        "WEIGHT":r"тегл|теж|килограм|\bкг\b", "SESSION":r"rpe|усили|оценк|лактат|трениров|изпълн",
        "TEST":r"тест|измер", "BLOCK":r"блок|натовар|период", "LAB":r"лаборатор|феритин|хемоглобин|кортизол|изследван",
        "MANUAL_SPEED_TEST":r"тест|максимал", "MANAGEMENT_PROFILE":r"искам|промени|разреш|почива|тренирам|планира|задай",
        "CALENDAR_EVENT":r"премест|лагер|старт|страт|състезан|събити|отмени|недостъп|добави"}
    return re.search(patterns.get(command["kind"],r"(?!)"),message.lower()) is not None


def _input_request(state, message, now):
    """Keep a short clarification thread, sourced only from the user's words.

    This permits a *preview*, never an automatic write from a previous turn.
    The conversation RPC already binds state to actor, athlete and exact grants.
    """
    text = message.lower()
    pending = state.get("input_request")
    if not isinstance(pending, dict) or pending.get("expires_at", "") <= now.isoformat():
        pending = None
    action = re.search(r"искам|въвед|запиш|промен|промян|премест|създа|добав|задай|отмени", text)
    if re.search(r"\bне\s+(?:искам|записвай|променяй|създавай)\b", text):
        state.pop("input_request", None)
        return None
    kind = None
    if action:
        if re.search(r"събити|старт|страт|състезан|лагер", text):
            kind = "CALENDAR_EVENT"
        elif re.search(r"тегл|теж|килограм|\bкг\b", text):
            kind = "WEIGHT"
        elif re.search(r"rpe|усили|след.{0,30}трениров|трениров.{0,30}(?:от\s*10|/\s*10)", text):
            kind = "SESSION"
        elif re.search(r"умор|сън|стрес|болезнен|мотивац|болк|болен|оценк", text):
            kind = "DAILY"
        elif re.search(r"почив|тренировъчни дни|седмич|планира", text):
            kind = "MANAGEMENT_PROFILE"
    if kind:
        pending = {"kind": kind, "request": message, "expires_at": (now + timedelta(minutes=15)).isoformat(), "followups": 0}
    elif pending:
        # An unrelated read/question ends the write thread. A short answer,
        # date, name, number, or corrected scale can complete its missing fields.
        related = pending.get("kind")=="SESSION" and re.search(r"rpe|усили|скал|оценк|след.{0,30}трениров|стрес и възстанов", text)
        if ((not related and re.search(r"^(?:а\s+)?(?:покажи|обясни|виж|какво|каква|какви|защо|колко|как\b|дали\b|кога\b|искам да (?:разбера|видя))", text))
                or text.strip() in CANCEL or pending.get("followups", 0) >= 8):
            pending = None
        else:
            pending = {**pending, "followups": pending.get("followups", 0) + 1}
    if pending:
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
    the resulting command still needs the ordinary shown-preview confirmation.
    """
    if not request or request.get("kind")!="SESSION":
        return None
    if request.get("model_clarification"):
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
    selected,clarification=_fallback_day_query(repository,alias,anchor)
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
        if re.search(r"веднага|непосредствено",text):
            timing="IMMEDIATE"
        elif re.search(r"на следващия ден",text):
            timing="NEXT_DAY"
        elif (re.fullmatch(r"(?:около\s+)?(?:10\s*[–-]\s*60|[1-5]\d|60)\s*мин(?:ути)?[.! ]*",text)
                or re.search(r"(?:10\s*[–-]\s*60|\b(?:[1-5]\d|60))\s*мин(?:ути)?\s*(?:по-късно|след края)",text)
                or re.search(r"\bслед\s+(?:[1-5]\d|60)\s*мин",text)):
            timing="DELAYED"
    timing=timing or payload.get("timing")
    if timing not in {"IMMEDIATE","DELAYED","NEXT_DAY"}:
        if message!=anchor:
            if not correction:
                request["model_clarification"]=True
            return None
        return {"context":context,"message":f"Усилието след тренировката е {score}/10. Кога направи оценката: веднага след края, 10–60 минути по-късно или на следващия ден?"}
    command={"kind":"SESSION","entry_key":entry["entry_key"],"expected_revision":entry["revision"],
        "patch":{"rpe":score,"rpe_source":"ONFLOWS","timing":timing,"duration_minutes":duration}}
    preview=_preview(command,entry)
    state["pending_command"]=command
    state["pending_command_at"]=datetime.now(timezone.utc).isoformat()
    return {"context":context,"message":preview,"suggestions":["Запиши промяната","Откажи промяната"]}


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


def _preview(command, entry=None):
    # A compact literal preview is independent of a model's description.
    labels={"DAILY":"дневна оценка","WEIGHT":"тегло","SESSION":"оценка на тренировка","TEST":"мониторингов тест", "LAB":"лабораторен резултат",
            "MANAGEMENT_PROFILE":"настройки за планиране","CALENDAR_EVENT":"календарно събитие","MANUAL_SPEED_TEST":"максимален скоростен тест"}
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
        "laboratory":"Лаборатория","results":"Резултати","sample_id":"Проба","collection_time":"Час на пробата"}
    days=["понеделник","вторник","сряда","четвъртък","петък","събота","неделя"]
    def value(key,item):
        if item is None: return "изчистване"
        if isinstance(item,bool): return "да" if item else "не"
        if key=="timing":
            return {"IMMEDIATE":"непосредствено след края", "DELAYED":"10–60 минути след края", "NEXT_DAY":"на следващия ден", "UNKNOWN":"неуточнено"}.get(item,str(item))
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
    return (f"Предложена промяна: {labels.get(command['kind'],'тренировъчен блок')} · версия {command['expected_revision']}.\n"
            +(target+"\n" if target else "")
            +"\n".join(fields(command["patch"]))
            +"\nЗа този показан обхват избери „Запиши промяната“ или „Откажи промяната“. Утвърждаването на нова програма остава в „Седмична програма“.")


def _receipt_message(receipt):
    if receipt.get("conflict") is True or receipt.get("status")=="CONFLICT":
        return "Записът е променен от друга редакция. Тази промяна не е приложена. Зареди актуалната версия и опиши отново искането."
    if receipt.get("saved") is not True and receipt.get("status") not in {"SAVED","SUCCEEDED","COMMITTED"}:
        return "Резултатът от записа още не е потвърден. Провери статуса на същата заявка."
    revision=receipt.get("revision")
    return ("Входът е записан"+(f" като версия {revision}" if revision is not None else "")+". "
            "Производните изгледи се обновяват по съществуващите правила. Това потвърждение не означава, че нова тренировъчна програма е готова или приета.")


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
        lines.append(f"\n{index}. {session.get('title') or session.get('method_id') or 'Тренировъчна сесия'}"+(f" · {duration:g} мин" if isinstance(duration,(int,float)) else ""))
        for block in session.get("blocks",[]):
            title=block.get("label") or block.get("kind") or "Част от тренировката"
            details=[]
            if block.get("duration_min") is not None: details.append(f"{block['duration_min']} мин")
            elif block.get("duration_s") is not None: details.append(f"{block['duration_s']} сек")
            if block.get("zone"): details.append(str(block["zone"]))
            if block.get("target_speed_kmh") is not None: details.append(f"{block['target_speed_kmh']} км/ч")
            if block.get("target_hr_bpm") is not None: details.append(f"{block['target_hr_bpm']} уд/мин")
            lines.append("• "+str(title)+(": "+", ".join(details) if details else ""))
            if isinstance(block.get("instructions"),str): lines.append(block["instructions"])
        evidence=session.get("dose_evidence") or {}
        if isinstance(evidence.get("explanation"),str): lines.append("Основание за дозата: "+evidence["explanation"])
    lines.append("\nТочната структура остава достъпна и в „Седмична програма“.")
    return "\n".join(lines)[:18000]


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
    result={"conversation_id":conversation,"operation_id":operation,"message":"","sources":[],"suggestions":SUGGESTIONS}
    provider=_configured_provider(repository) if provider is None else provider
    result["provider_available"]=_provider_available(provider)
    context=None
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
            if created and datetime.now(timezone.utc)-datetime.fromisoformat(created)>timedelta(minutes=15):
                pending=None
            if not isinstance(pending,dict):
                result["message"]="Няма показана промяна за запис. Опиши какво искаш да промениш."
            else:
                receipt=assistant_commands.execute(repository,alias,actor,operation,pending)
                result.update(receipt=receipt,message=_receipt_message(receipt))
            state.pop("input_request",None)
        elif re.search(r"(още|добави).{0,25}(отсеч|интервал|повторени)",normalized):
            result["message"]=UNAVAILABLE_SESSION_CHANGE
        elif _provider_available(provider) and (session_entry:=_session_rating_entry(repository,alias,actor,state,input_request,message)):
            context=session_entry.pop("context")
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
            for step in range(3):
                reply=provider.complete(messages, {"results":contexts,"confirmed_context":state.get("preferences",[]),
                    "completed_queries":completed_queries,"remaining_model_calls":3-step,
                    "pending_change":pending_change if input_request else None,"now":datetime.now(timezone.utc).isoformat(),
                    "active_input_request":input_request,
                    "athlete_timezone":athlete_timezone,"today":local_today,
                    "new_input_keys":{"CALENDAR_EVENT":"event_"+UUID(operation).hex,
                        "LAB":UUID(operation).hex,"MANUAL_SPEED_TEST":"manual_"+UUID(operation).hex}},_capabilities(permissions))
                for key in totals:
                    value=reply.get("usage",{}).get(key,0)
                    totals[key]=None if key=="estimated_cost_usd" and (totals[key] is None or value is None) else totals[key]+(value or 0)
                if reply.get("query"):
                    requested=reply["query"]
                    query_key=json.dumps({k:v for k,v in requested.items() if v is not None}
                        if isinstance(requested,dict) else requested,sort_keys=True)
                    if query_key in query_keys:
                        # Results are current to this turn. Repeating a read
                        # cannot consume the whole dialogue or duplicate data.
                        _permissions(repository,alias,actor)
                        context=contexts[query_keys.index(query_key)]
                        continue
                    context=_read(repository,alias,actor,requested)
                    contexts.append(context)
                    completed_queries.append(requested)
                    query_keys.append(query_key)
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
                    if not _write_intent(command,message) and not continuing:
                        raise HTTPException(422,"В текущото съобщение няма изрично искане за тази промяна. Опиши стойността, която искаш да запишеш.")
                    if input_request and input_request["kind"]=="SESSION" and command["kind"]=="DAILY":
                        raise HTTPException(422,"Оценката след тренировката е усилие RPE по скала 0–10. Тя се записва към изпълнената активност, без преобразуване в дневна умора 1–5.")
                    entry={**entry,"today":contexts[-1].get("today")}
                    if _automatic_observation(command,entry,message):
                        receipt=assistant_commands.execute(repository,alias,actor,operation,command)
                        result.update(receipt=receipt,message=_receipt_message(receipt))
                        state.pop("input_request",None)
                    else:
                        preview=_preview(command,entry)
                        state["pending_command"]=command
                        state["pending_command_at"]=datetime.now(timezone.utc).isoformat()
                        result.update(message=preview,suggestions=["Запиши промяната","Откажи промяната"])
                    break
                result["message"]=str(reply.get("text") or "Липсва отговор. Опитай с по-конкретен въпрос.")[:18000]
                if re.search(r"\b(записах|запазих|утвърдих|одобрих)\b|(?:входът|промяната|стойността|оценката|теглото|данните|резултатът)\s+(?:е|са)\s+(?:записан[аои]?|запазен[аои]?)\b|^(?:записан[аои]?|запазен[аои]?)\b",result["message"].lower()):
                    result["message"]="Няма потвърден нов запис от тази заявка. Опиши конкретната промяна или използвай съответния формуляр."
                break
            else:
                # A model read loop is not an ambiguous user request. Preserve
                # an already resolved day and all its sessions, with its source.
                if not input_request and context and context.get("query_kind") in {"day","method"} and "sessions" in context and "date" in context:
                    result["message"]=_render_day(context)
                else:
                    result["message"]="Справката изисква още уточнение. Посочи конкретна дата, тренировка или стойност."
            result["usage"]=totals
        result["sources"]=_sources(context)
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
    # Final authorization + persisted scope check prevent cached grants leaking
    # data after provider latency. Never invent success on a storage timeout.
    _permissions(repository,alias,actor)
    finished=_rpc(repository,"FINISH",alias,actor,conversation,operation,{"response":result,"state":state})
    scenario=("write_"+result["receipt"]["kind"] if isinstance(result.get("receipt"),dict) and result["receipt"].get("kind") in WRITE_KINDS
        else "read_"+context["query_kind"] if context and context.get("query_kind") in READ_KINDS else "dialogue")
    usage=result.get("usage") or {}
    logger.info("assistant_turn scenario=%s duration_ms=%d model_calls=%d input_tokens=%d output_tokens=%d estimated_cost_usd=%s",
        scenario,round((monotonic()-started)*1000),usage.get("model_calls",0),usage.get("input_tokens",0),usage.get("output_tokens",0),usage.get("estimated_cost_usd"))
    return finished
