"""Explicit event conditions and deterministic execution guidance.

No terrain-speed inversion or additional training load is inferred here.
Percentages are coach conventions; all numerical planning uses its checked dose.
"""
from copy import deepcopy
from datetime import date
import math

VERSION = "race-course-v1"
SURFACES = {"TRACK", "ROAD", "CROSS", "TRAIL", "SNOW"}
PROFILES = {"FLAT", "UPHILL", "ROLLING"}
FIELDS = {"distance_m", "surface", "profile", "gradient_percent", "ascent_m", "descent_m",
          "expected_duration_min", "specific_sessions_percent", "familiar_terrain"}


def number(value, low, high):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and low <= value <= high


def validate(value):
    if not isinstance(value, dict) or set(value)-FIELDS or not {"distance_m", "surface", "profile"} <= set(value):
        raise ValueError("Невалидни данни за състезателното трасе.")
    if any(v is None for v in value.values()):
        raise ValueError("Непопълнените условия се пропускат; попълнените диапазони изискват две граници.")
    if (not number(value["distance_m"], 50, 1_000_000) or not isinstance(value["surface"], str)
            or value["surface"] not in SURFACES or not isinstance(value["profile"], str) or value["profile"] not in PROFILES):
        raise ValueError("Проверете дистанцията, настилката и профила на трасето.")
    for key, low, high in (("gradient_percent", .1, 60), ("expected_duration_min", .1, 1440)):
        pair = value.get(key)
        if pair is not None and (not isinstance(pair, (list, tuple)) or len(pair) != 2
                or not all(number(v, low, high) for v in pair) or pair[0] > pair[1]):
            raise ValueError("Проверете долната и горната граница на наклона или продължителността.")
    for key in ("ascent_m", "descent_m"):
        if value.get(key) is not None and not number(value[key], 0, 100_000):
            raise ValueError("Изкачването и спускането трябва да са неотрицателни метри.")
    if not number(value.get("specific_sessions_percent", 70), 0, 100):
        raise ValueError("Делът на специфичните занимания трябва да е от 0 до 100%.")
    if type(value.get("familiar_terrain", False)) is not bool:
        raise ValueError("Потвърдете дали условията са познати на атлета.")
    if value["profile"] != "UPHILL" and value.get("gradient_percent") is not None:
        raise ValueError("Постоянен наклон се задава само при трасе с изкачване.")
    if value["surface"] == "TRACK" and value["profile"] != "FLAT":
        raise ValueError("Пистовото бягане използва равен профил.")
    return deepcopy(value)


def target_event(events, day):
    key = day.isoformat() if isinstance(day, date) else str(day)
    future = [e for e in events if str(e["end_date"]) >= key]
    for kind in ("MAIN_RACE", "CONTROL_RACE"):
        candidates = [e for e in future if e["event_type"] == kind]
        if candidates:
            return min(candidates, key=lambda e: (str(e["start_date"]), str(e["end_date"]), e.get("event_id", "")))
    return None


def apply_event(profile, events, day):
    event = target_event(events, day)
    course = (event or {}).get("race_course")
    if not course:
        return {k: v for k, v in profile.items() if k not in {"_race_course", "_race_event"}}
    course = validate(course)
    return {**profile, "discipline": f"{course['distance_m']:g} m", "_race_course": course,
            "_race_event": {k: event[k] for k in ("event_id", "name", "start_date", "end_date")}}


def flat(course):
    return course["profile"] == "FLAT" and course["surface"] in {"TRACK", "ROAD"}


def instruction(course):
    surface = {"TRACK": "писта или измерен равен участък", "ROAD": "сходна пътна настилка",
               "CROSS": "сходно кросово трасе", "TRAIL": "сходна планинска пътека", "SNOW": "сходно снежно трасе"}[course["surface"]]
    text = f"Основната работа се изпълнява на {surface}."
    if course["profile"] == "UPHILL":
        slope = course.get("gradient_percent")
        text += " Работните отсечки са на изкачване" + (f" с наклон {slope[0]:g}–{slope[1]:g}%" if slope else ", сходно със състезателното") + "."
        text += " Връщането надолу е възстановяване, без състезателно спускане."
    elif course["profile"] == "ROLLING":
        text += " Използвай изкачвания, контролирани спускания и преходи, сходни с трасето."
    if course.get("ascent_m") is not None or course.get("descent_m") is not None:
        text += " Състезателното трасе:" + (f" +{course['ascent_m']:g} м" if course.get("ascent_m") is not None else "") + (f" / −{course['descent_m']:g} м" if course.get("descent_m") is not None else "") + ". Не възпроизвеждай цялата денивелация във всяка тренировка."
    if not flat(course):
        text += " Водещи са работното време, усилието и техниката. Равнинното темпо и дистанция са еквивалент, не цел по наклона."
    return text


def annotate_days(days, events):
    """Select approximately the configured share, with all eligible models first.

    Only new, suitable main sessions participate. A locked task is immutable.
    This is a target within the generated draft, not a claim about past exposure.
    """
    buckets = {}
    for day in days:
        event = target_event(events, day["date"])
        course = (event or {}).get("race_course")
        if not course or day.get("locked") or day.get("period") not in {"SPECIAL_PREPARATION", "PRECOMPETITION", "COMPETITION"}:
            continue
        for session in day.get("sessions", []):
            if session.get("sport") not in {"Run", "NordicSki"} or session.get("purpose") not in {"BUILDING", "MAINTENANCE", "ACTIVATION"}:
                continue
            if any(b.get("zone") in {"STR", "NMS"} for b in session.get("blocks", [])):
                continue
            model = session.get("method_id", "").startswith("ONFLOWS-RACE-")
            eligible = model or session.get("purpose") == "BUILDING" or session.get("zone") in {"Z3", "Z4", "Z5"}
            if not eligible:
                continue
            if day.get("taper") and not course.get("familiar_terrain", False):
                session["race_course_guidance"] = {"event_id": event["event_id"], "event_name": event["name"],
                    "status": "FAMILIAR_TERRAIN_REQUIRED", "note": "В тейпъра използвай познати условия. Новото състезателно трасе не се въвежда автоматично."}
                continue
            buckets.setdefault(event["event_id"], []).append((day, session, event, course, model))
    for entries in buckets.values():
        share = entries[0][3].get("specific_sessions_percent", 70)
        wanted = max(sum(e[4] for e in entries), math.floor(len(entries)*share/100 + .5)) if share else 0
        ranked = sorted(entries, key=lambda e: (not e[4], e[0]["date"], e[1].get("method_id", "")))
        for day, session, event, course, _ in ranked[:wanted]:
            note = instruction(course)
            session["race_course_guidance"] = {"version": VERSION, "event_id": event["event_id"], "event_name": event["name"],
                "status": "SPECIFIC", "course": deepcopy(course), "note": note,
                "target_share_percent": share, "eligible_sessions": len(entries), "specific_sessions": wanted,
                "scope": "CURRENT_DRAFT_ELIGIBLE_MAIN_SESSIONS"}
            for block in session.get("blocks", []):
                if block["kind"] == "WORK":
                    block["instructions"] = note + " " + block.get("instructions", "").replace("на подходящ равен участък", "в посочените условия")
                    if not flat(course):
                        block["primary_control"] = "EFFORT_ON_TERRAIN"
                        block["speed_basis"] = "FLAT_EQUIVALENT"
                    for key in ("method_library", "parent_method_card"):
                        if (block.get(key) or {}).get("guidance"):
                            block[key]["guidance"]["execution"] = note
            # The selected session's own frozen card must not contradict the block.
            card = session.get("method_library")
            if card and card.get("guidance"):
                card["guidance"]["execution"] = note
