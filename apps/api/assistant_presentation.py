"""Deterministic assistant presentation of saved records and command receipts.

These helpers never query storage, invoke a model, authorize access or execute a
command. Their inputs have already passed the canonical services' checks.
"""
import json
from math import isfinite
import re

from fastapi import HTTPException


def _plan_preview(command, entry):
    summary=(entry or {}).get("preview_summary") or {}
    if summary.get("revision")!=command["expected_revision"]:
        raise HTTPException(409,"Прочети актуалната програма, преди да предложиш действието.")
    patch=command["patch"]
    action=patch.get("action")
    title={"APPROVE":"Утвърждаване на предложената програма", "PAUSE":"Пауза на програмата",
           "RESUME":"Възобновяване на програмата", "REFRESH":"Обновяване на програмата",
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
        lines.append("Подготовката се изпълнява във фонов режим; валидният резултат се прилага автоматично.")
    lines.append("Избери „Запиши промяната“ или „Откажи промяната“.")
    return "\n".join(lines)


def _preview(command, entry=None):
    if command["kind"]=="TRAINING_PROPOSAL":
        patch=command["patch"]
        action=patch.get("action")
        labels={"PERSONAL_METHOD":"Личен вариант: "+str((patch.get("method") or {}).get("name", "метод")),
            "REMOVE_PERSONAL_METHOD":"Премахване на личен метод", "FIX_STRUCTURE":"Фиксиране на структурата",
            "REPEAT_STRUCTURE":"Повторение на структурата", "SESSION_METHOD":"Метод за конкретна сесия",
            "REMOVE_STRUCTURE":"Премахване на настройката на структурата"}
        lines=["Предложена промяна: "+labels.get(action,"тренировъчно предложение")+"."]
        method=patch.get("method") or {}
        if method:
            for field,label in (("work_range","Работна отсечка"),("repetitions","Повторения"),("recovery_seconds","Почивка")):
                values=method.get(field)
                if isinstance(values,(list,tuple)) and len(values)==2:
                    shown=str(values[0]) if values[0]==values[1] else f"{values[0]}–{values[1]}"
                    unit=" сек" if field=="recovery_seconds" or field=="work_range" and method.get("work_unit")=="seconds" else " м" if field=="work_range" and method.get("work_unit")=="meters" else ""
                    lines.append(f"{label}: {shown}{unit}.")
        if action=="SESSION_METHOD":
            cards={card.get("id"):card for card in (entry or {}).get("catalog_methods",[])}
            names=[cards.get(identifier,{}).get("title") or identifier for identifier in patch.get("method_ids",[])]
            lines.append(f"Сесия {patch.get('session_index')}: "+", ".join(names)+".")
        if patch.get("source_start"):
            lines.append(f"Източник: {patch['source_start']} – {patch.get('source_end')}.")
        if patch.get("target_start") or patch.get("target_date"):
            lines.append("Прилагане от: "+str(patch.get("target_start") or patch.get("target_date"))+".")
        lines.append("Обхватът е само този спортист. Алгоритъмът преизчислява дозата според актуалните данни.")
        lines.append("Избери „Запиши промяната“ или „Откажи промяната“.")
        return "\n".join(lines)
    if command["kind"]=="PLAN_GENERATE":
        start_date=command["patch"].get("start_date") or (entry or {}).get("payload",{}).get("start_date")
        if not start_date or command["entry_key"]!=start_date:
            raise HTTPException(422,"Посочи началната дата на новата програма.")
        return (f"Предложена промяна: подготовка на програма от {start_date}.\n"
                "Изчислението се изпълнява във фонов режим. Програмата става текуща автоматично след проверките на алгоритъма.\n"
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
        if key=="threshold_method": return {"AUTO":"автоматичен избор", "CONTINUOUS":"непрекъсната работа", "INTERVALS":"интервална работа"}.get(item,str(item))
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
            elif key=="method_library" and isinstance(item,dict) and isinstance(item.get("overrides"),dict):
                cards={card.get("id"):card for card in (entry or {}).get("catalog_methods",[])}
                control_names={"repetitions":"повторения", "work_range":"дължина на работата", "speed_multiplier":"скоростен множител",
                    "total_capacity_ratio":"дял от индивидуалния капацитет", "speed_tolerance_fraction":"допуск на скоростта", "hr_tolerance_bpm":"пулсов допуск"}
                for identifier,controls in item["overrides"].items():
                    card=cards.get(identifier,{})
                    label=card.get("title") or identifier
                    parts=[]
                    for control,setting in (controls or {}).items():
                        if control=="enabled":
                            parts.append("включен" if setting else "изключен")
                        elif isinstance(setting,(list,tuple)) and len(setting)==2:
                            shown=str(setting[0]) if setting[0]==setting[1] else f"{setting[0]}–{setting[1]}"
                            unit=((card.get("prescription") or {}).get("work") or {}).get("unit") if control=="work_range" else None
                            parts.append(control_names.get(control,control)+": "+shown+({"seconds":" сек", "meters":" м"}.get(unit,"")))
                        else:
                            parts.append(control_names.get(control,control)+": "+str(setting))
                    lines.append("• Метод "+str(label)+": "+(", ".join(parts) if controls else "възстановени стандартни настройки"))
                if "enabled" in item:
                    lines.append("• Каталог на методите: "+("включен" if item["enabled"] else "изключен"))
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
    if kind=="TRAINING_PROPOSAL":
        labels={"PERSONAL_METHOD":"Личният вариант на метода е записан само за този спортист.",
            "REMOVE_PERSONAL_METHOD":"Личният вариант на метода е премахнат.",
            "FIX_STRUCTURE":"Структурата е фиксирана за избрания период.",
            "REPEAT_STRUCTURE":"Повторението на структурата е зададено за избрания период.",
            "SESSION_METHOD":"Методът е зададен само за избраната сесия.",
            "REMOVE_STRUCTURE":"Избраната настройка на структурата е премахната."}
        message=labels.get(action,"Предложението е записано за този спортист.")
        corrections=((entry or {}).get("preview") or {}).get("corrections") or []
        field_names={"work_range":"Работна отсечка", "repetitions":"Повторения", "recovery_seconds":"Почивка"}
        unit=(((command or {}).get("patch") or {}).get("method") or {}).get("work_unit")
        def range_label(value):
            if not isinstance(value,(list,tuple)) or len(value)!=2:
                return str(value)
            return str(value[0]) if value[0]==value[1] else f"{value[0]}–{value[1]}"
        for correction in corrections:
            field=correction.get("field")
            if field in field_names:
                suffix=" сек" if field=="recovery_seconds" or field=="work_range" and unit=="seconds" else " м" if field=="work_range" and unit=="meters" else ""
                message+=f"\n{field_names[field]}: {range_label(correction.get('requested'))}{suffix} → {range_label(correction.get('applied'))}{suffix}, според допустимите граници на метода."
        return message+"\nДозата се определя от алгоритъма според актуалното състояние. Програмата се преизчислява във фонов режим и валидният резултат се прилага автоматично."
    if kind=="SYNC_DATA":
        scope=(receipt.get("sync") or {}).get("scope") or (receipt.get("job") or {}).get("scope") or ((command or {}).get("patch") or {}).get("scope")
        label={"WELLNESS":"данните за благосъстоянието", "RECOVERY":"данните за възстановяването"}.get(scope,"данните")
        return f"Синхронизирането на {label} е заявено. Данните се обновяват във фонов режим; заявката не означава, че обработката вече е приключила."
    if kind=="PLAN_GENERATE":
        job=receipt.get("job") or {}
        status=job.get("status")
        if status=="SUCCEEDED":
            if job.get("published") is True and type(job.get("result_active_revision")) is int:
                return "Програмата за този период е изчислена и вече е текущата програма."
            return "Програмата е изчислена, но не е приложена. Провери посоченото състояние в „Седмична програма“; при пауза или липсващи данни отделно утвърждаване не решава причината."
        if status in {"FAILED","CANCELLED"}:
            return "Подготовката на програмата не е завършила успешно. Предишната запазена програма не е заменена."
        return "Подготовката на новата програма е заявена. Тя се изчислява във фонов режим и се прилага автоматично след проверките на алгоритъма."
    if receipt.get("queued") or receipt.get("refresh_state")=="QUEUED":
        return "Обновяването е заявено. Програмата се подготвя във фонов режим; текущата запазена версия остава достъпна, докато валидният резултат се приложи автоматично."
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
            message += "\nНастройката е записана. Програмата се преизчислява във фонов режим и валидният резултат се прилага автоматично; отделно утвърждаване не е необходимо."
    return message


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
        return "За тази дата още няма запазена програма. Провери състоянието на подготовката в „Седмична програма“; не е необходимо отделно утвърждаване."
    lines=[f"Програма за {context.get('date',context.get('today','днес'))} · версия {context.get('plan_revision','неизвестна')}"]
    if context.get("prescription_active") is False:
        states={"PAUSED":"Програмата е на пауза.", "COMPLETED":"Периодът на програмата е завършен.",
            "REVIEW_REQUIRED":"Новият разчет не е приложен, защото има условие за изясняване в данните."}
        lines.append(states.get(context.get("plan_status"),"Показана е запазената версия на програмата.")+" Сесиите остават достъпни за справка.")
        if context.get("plan_status_detail"):
            lines.append(str(context["plan_status_detail"]))
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
            if item.get("query_kind") == "history" and item.get("report"):
                return _render_history(item)
        for item in reversed(contexts):
            if item.get("query_kind") in {"day", "method"} and "sessions" in item and "date" in item:
                return _render_day(item)
        for item in reversed(contexts):
            if item.get("query_kind")=="status":
                return _render_status(item)
    return "Не успях да завърша обработката на тази заявка. Данните не са променени. Можеш да продължиш със същото искане."


def _render_history(context):
    """A saved arithmetic report stays usable if language synthesis fails."""
    report = context["report"]
    lines = [f"Изпълнен обем по зони за {report['period_start']} – {report['period_end']} включително:"]
    for row in report["zones"]:
        minutes = round(row["raw_time_min"])
        hours, remainder = divmod(minutes, 60)
        decimal = f"{row['raw_time_hours']:.2f}".replace(".", ",")
        lines.append(f"• {row['zone']}: {decimal} ч ({hours} ч {remainder} мин).")
    if not context.get("period_coverage", {}).get("full_requested_range", True):
        lines.append(f"Поискан е периодът {context['start']} – {context['end']}; показаният сбор обхваща само наличните дати по-горе.")
    quality = report.get("quality", {})
    if quality.get("limited_activities"):
        lines.append(f"В избрания период {quality['limited_activities']} от {quality['modeled_activities']} активности са с ограничено качество; отчетеният обем по зони може да е непълен.")
    if context.get("freshness") == "STALE":
        lines.append(f"Източникът е запазеният отчет към {context.get('as_of')}.")
    return "\n".join(lines)
