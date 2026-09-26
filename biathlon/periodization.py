"""Deterministic, explainable calendar periodization for the planning engine.

This is a coach-defined calendar template, not a physiological readiness model.
Public date ranges are inclusive; all arithmetic uses half-open intervals.
Taper windows overlay preparation and never add days to the calendar.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any


ENGINE_VERSION = "periodization-v2"
DAY = timedelta(days=1)
PHASE_LABELS_BG = {
    "RE_ENTRY": "Вработващ",
    "GENERAL_PREPARATION": "Общо подготвителен",
    "SPECIAL_PREPARATION": "Специално подготвителен",
    "PRECOMPETITION": "Предсъстезателен",
    "COMPETITION": "Състезателен",
    "TRANSITION": "Преходен",
}
PREPARATION_KINDS = tuple(PHASE_LABELS_BG)[:4]
BASE_DAYS = (7, 28, 28, 14)
# Each complete 14-day extension gives 1:5:5:3 days. Interleaving keeps
# GENERAL and SPECIAL within one day, including incomplete extensions.
EXTENSION_SEQUENCE = (0, 1, 2, 3, 1, 2, 1, 2, 3, 1, 2, 1, 2, 3)
EVENT_TYPES = {"MAIN_RACE", "CONTROL_RACE", "CAMP", "TEST", "UNAVAILABLE"}
DENSE_MAIN_RACE_GAP_DAYS = 14


def _date(value: Any, name: str) -> date:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError:
            pass
    raise ValueError(f"{name} must be a date or an ISO date string")


def _bounded_days(value: Any, name: str, *, optional: bool = False) -> None:
    if optional and value is None:
        return
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 21:
        raise ValueError(f"{name} must be an integer between 0 and 21")


def _clip_from_front(lengths: list[int], total: int) -> list[int]:
    missing = max(0, sum(lengths) - total)
    for index, length in enumerate(lengths):
        removed = min(missing, length)
        lengths[index] -= removed
        missing -= removed
    return lengths


def _allocate_preparation(total: int, reentry_override: int | None) -> list[int]:
    if reentry_override is None:
        lengths = list(BASE_DAYS)
        if total <= sum(lengths):
            return _clip_from_front(lengths, total)
        sequence = EXTENSION_SEQUENCE
    else:
        # An explicit override reserves entry days first. The other phases
        # retain their minima and 5:5:3 extension weights. Entry does not grow.
        entry = min(total, reentry_override)
        lengths = [entry, *BASE_DAYS[1:]]
        if total <= sum(lengths):
            return [entry, *_clip_from_front(lengths[1:], total - entry)]
        sequence = tuple(index for index in EXTENSION_SEQUENCE if index != 0)

    remaining = total - sum(lengths)
    offset = 0
    while remaining:
        index = sequence[offset % len(sequence)]
        offset += 1
        if (index == 0 and lengths[0] >= 21) or (index == 3 and lengths[3] >= 56):
            continue
        lengths[index] += 1
        remaining -= 1
    return lengths


def build_periodization(
    start: date,
    end: date,
    events: list[dict],
    *,
    reentry_days_override: int | None = None,
    taper_days: int = 7,
    transition_days: int = 0,
    control_taper_days: int = 2,
) -> dict[str, Any]:
    """Build a calendar template of at most 366 inclusive calendar days.

    A future MAIN_RACE beyond ``end`` can anchor the displayed preparation,
    provided it falls within one year of ``start``. An in-progress MAIN_RACE
    remains competition. Later cycles omit automatic re-entry. Gaps shorter
    than 14 days between main races remain competition and require review;
    the 14-day boundary is an explicit initial coach heuristic.

    CONTROL_RACE, CAMP, TEST and UNAVAILABLE remain calendar context. They
    neither create a full taper nor increase the allowed training dose.
    Scheduling must still enforce UNAVAILABLE events. TRANSITION is part of
    the vocabulary but is not inferred from an absence of calendar events.
    """
    start = _date(start, "start")
    end = _date(end, "end")
    horizon_days = (end - start).days + 1
    if not 1 <= horizon_days <= 366:
        raise ValueError("periodization horizon must contain 1 to 366 inclusive days")
    _bounded_days(reentry_days_override, "reentry_days_override", optional=True)
    _bounded_days(taper_days, "taper_days")
    if type(transition_days) is not int or not 0 <= transition_days <= 28:
        raise ValueError("transition_days must be 0 to 28")
    if type(control_taper_days) is not int or not 0 <= control_taper_days <= 3:
        raise ValueError("control_taper_days must be 0 to 3")
    if not isinstance(events, list):
        raise ValueError("events must be a list of event dictionaries")

    normalized: list[dict[str, Any]] = []
    for raw in events:
        if not isinstance(raw, dict) or raw.get("event_type") not in EVENT_TYPES:
            raise ValueError("unsupported planning calendar event type")
        event_start = _date(raw.get("start_date"), "event.start_date")
        event_end = _date(raw.get("end_date", raw.get("start_date")), "event.end_date")
        if event_end < event_start:
            raise ValueError("calendar event end precedes its start")
        normalized.append({
            "event_id": str(raw.get("event_id", "")),
            "event_type": raw["event_type"],
            "name": str(raw.get("name", "")),
            "start_date": event_start.isoformat(),
            "end_date": event_end.isoformat(),
        })
    normalized.sort(key=lambda event: (
        event["start_date"], event["end_date"], event["event_type"], event["event_id"],
    ))
    stop = end + DAY
    phases: list[dict[str, Any]] = []
    tapers: list[dict[str, Any]] = []
    warnings: list[dict[str, str]] = []

    def warn(code: str, message: str) -> None:
        item = {"code": code, "message": message}
        if item not in warnings:
            warnings.append(item)

    def add_phase(kind: str, left: date, right: date, reason: str, event_id: str | None) -> None:
        left, right = max(left, start), min(right, stop)
        if left < right:
            phases.append({
                "kind": kind,
                "start_date": left.isoformat(),
                "end_date": (right - DAY).isoformat(),
                "days": (right - left).days,
                "label_bg": PHASE_LABELS_BG[kind],
                "reason": reason,
                "main_race_event_id": event_id,
            })

    future_main = [event for event in normalized if event["event_type"] == "MAIN_RACE"
                   and _date(event["start_date"], "event.start_date") >= start]
    next_main = future_main[0] if future_main else None
    anchor_limit = start + timedelta(days=366)
    main_races = [event for event in normalized if event["event_type"] == "MAIN_RACE"
                  and _date(event["end_date"], "event.end_date") >= start
                  and _date(event["start_date"], "event.start_date") <= anchor_limit]

    if not main_races:
        warn("NO_MAIN_RACE_IN_ANNUAL_WINDOW",
             "Няма основен старт в годишния хоризонт. Показана е условна обща подготовка; "
             "тя не доказва готовност и не определя автоматично тренировъчната доза.")
    if next_main and _date(next_main["start_date"], "event.start_date") > anchor_limit:
        warn("MAIN_RACE_BEYOND_ANNUAL_WINDOW",
             "Следващият основен старт е извън годишния хоризонт и не разтяга този цикъл.")

    cursor = start
    previous_race = None
    for race in main_races:
        race_start = _date(race["start_date"], "event.start_date")
        race_stop = _date(race["end_date"], "event.end_date") + DAY
        if previous_race and race_start < cursor:
            warn("OVERLAPPING_MAIN_RACES",
                 "Основни стартове се припокриват. Състезателните дни са отчетени веднъж; "
                 "приоритетът и възстановяването изискват преглед.")
        available_days = max(0, (race_start - cursor).days)
        dense = previous_race is not None and available_days < DENSE_MAIN_RACE_GAP_DAYS
        if dense:
            warn("DENSE_MAIN_RACES_REQUIRE_REVIEW",
                 "Между основни стартове има под 14 свободни дни. Запазен е състезателен "
                 "период без нов автоматичен пълен тейпър; нужни са индивидуални решения. "
                 "Границата 14 дни е начално треньорско правило.")
            add_phase("COMPETITION", cursor, race_start,
                      "Междинни дни в гъста състезателна серия; натоварването и "
                      "възстановяването се решават отделно.", race["event_id"])
        elif available_days:
            override = 0 if previous_race else reentry_days_override
            lengths = _allocate_preparation(available_days, override)
            base_length = sum(BASE_DAYS) if override is None else sum(BASE_DAYS[1:]) + override
            if available_days < base_length:
                warn("PARTIAL_PREPARATION_CYCLE",
                     "Налична е само последната част на подготвителния цикъл. Календарът "
                     "не доказва, че пропуснатата основа е изградена; проверете историята.")
            if override is not None and override > available_days:
                warn("REENTRY_TRUNCATED_BY_RACE",
                     "Заявеното вработване не се побира преди старта. Показана е наличната "
                     "част; участието и тренировъчната задача изискват преглед.")
            phase_cursor = cursor
            for kind, length in zip(PREPARATION_KINDS, lengths):
                phase_stop = phase_cursor + timedelta(days=length)
                reason = "Календарно разпределение спрямо основния старт; дозата се определя отделно."
                if available_days < base_length:
                    reason = "Оставаща част от минималния подготвителен цикъл; необходима е проверка на предходната подготовка."
                if kind == "RE_ENTRY" and override is not None:
                    reason = "Продължителност на вработването, изрично зададена в профила."
                if kind == "PRECOMPETITION":
                    reason += " Тейпърът е само крайната отбелязана част, а не целият период."
                add_phase(kind, phase_cursor, phase_stop, reason, race["event_id"])
                if kind == "PRECOMPETITION" and length and taper_days:
                    full_taper_days = min(taper_days, length)
                    taper_start = max(start, phase_stop - timedelta(days=full_taper_days))
                    taper_stop = min(stop, phase_stop)
                    if taper_start < taper_stop:
                        tapers.append({
                            "kind": "TAPER",
                            "start_date": taper_start.isoformat(),
                            "end_date": (taper_stop - DAY).isoformat(),
                            "days": (taper_stop - taper_start).days,
                            "planned_days": full_taper_days,
                            "requested_days": taper_days,
                            "main_race_event_id": race["event_id"],
                            "label_bg": "Тейпър преди основен старт",
                            "reason": "Част от предсъстезателния период. Намаленият товар е "
                                      "планиран и не се запълва автоматично поради дефицит в 7/40.",
                            "prevent_deficit_refill": True,
                            "volume_factor": .5,
                        })
                    if length < taper_days:
                        warn("TAPER_TRUNCATED_BY_AVAILABLE_PREPARATION",
                             "Оставащата предсъстезателна подготовка е по-кратка от зададения тейпър.")
                phase_cursor = phase_stop
        add_phase("COMPETITION", max(cursor, race_start), race_stop,
                  "Реални дати на основен старт; състезателният товар се отчита отделно.",
                  race["event_id"])
        cursor = max(cursor, race_stop)
        previous_race = race
        if cursor >= stop:
            break

    if cursor < stop:
        if previous_race and transition_days:
            transition_stop = min(stop, cursor + timedelta(days=transition_days))
            add_phase("TRANSITION", cursor, transition_stop,
                      "Преход след последния основен старт с изрично зададената продължителност.", previous_race["event_id"])
            cursor = transition_stop
        add_phase("GENERAL_PREPARATION", cursor, stop,
                  "Условна обща подготовка без следваща основна цел; не е автоматично "
                  "предписание за натоварване и изисква съобразяване с историята.", None)
        if previous_race and not transition_days:
            warn("POST_RACE_PERIOD_REQUIRES_REVIEW",
                 "След последния основен старт няма следваща цел. Преходният период и "
                 "връщането към подготовка не са автоматично определени.")

    for event in normalized:
        if event["event_type"] == "CONTROL_RACE" and control_taper_days:
            right = min(stop, _date(event["start_date"], "control.start"))
            left = max(start, _date(event["start_date"], "control.start") - timedelta(days=control_taper_days))
            if left < right:
                tapers.append({"kind": "CONTROL_FRESHENING", "start_date": left.isoformat(),
                    "end_date": (right-DAY).isoformat(), "days": (right-left).days,
                    "main_race_event_id": event["event_id"], "volume_factor": .8,
                    "label_bg": "Кратко освежаване преди контролен старт", "prevent_deficit_refill": True,
                    "reason": "Два дни с по-малък обем; начално правило, различно от тейпъра за основния старт."})

    context_notes = {
        "MAIN_RACE": "Основна цел за периодизацията.",
        "CONTROL_RACE": "Контролен старт без автоматичен пълен тейпър; задачата се решава отделно.",
        "CAMP": "Лагерът задава контекст и налични условия; не увеличава автоматично допустимия товар.",
        "TEST": "Тестът е календарен контекст; неговото натоварване се планира отделно.",
        "UNAVAILABLE": "Забрана за планиране на тренировка; изпълнява се от дневния планировчик.",
    }
    calendar_context = [{**event, "note_bg": context_notes[event["event_type"]]}
                        for event in normalized
                        if _date(event["start_date"], "event.start_date") <= end
                        and _date(event["end_date"], "event.end_date") >= start]

    return {
        "engine_version": ENGINE_VERSION,
        "phases": phases,
        "taper_windows": tapers,
        "calendar_context": calendar_context,
        "next_main_race": next_main,
        "warnings": warnings,
        "parameters": {
            "source": "INITIAL_COACH_RULES",
            "date_ranges": "INCLUSIVE",
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
            "horizon_days": horizon_days,
            "max_horizon_days": 366,
            "base_days": dict(zip(PREPARATION_KINDS, BASE_DAYS)),
            "extension_days_per_14": dict(zip(PREPARATION_KINDS, (1, 5, 5, 3))),
            "extension_order": [PREPARATION_KINDS[index] for index in EXTENSION_SEQUENCE],
            "caps_days": {"RE_ENTRY": 21, "PRECOMPETITION": 56},
            "reentry_days_override": reentry_days_override,
            "override_extension_weights": {"GENERAL_PREPARATION": 5, "SPECIAL_PREPARATION": 5, "PRECOMPETITION": 3},
            "later_cycles_reentry_days": 0,
            "taper_days": taper_days,
            "taper_is_overlay": True,
            "dense_main_race_gap_days": DENSE_MAIN_RACE_GAP_DAYS,
            "phase_labels_bg": dict(PHASE_LABELS_BG),
            "transition_policy": "EXPLICIT_DURATION_AFTER_FINAL_MAIN_RACE",
            "transition_days": transition_days, "control_taper_days": control_taper_days,
            "readiness_inferred_from_calendar": False,
        },
    }
