"""Dated structure intentions feed the canonical allocator, never stored doses."""
from copy import deepcopy
from datetime import date, timedelta

VERSION = "training-structure-v1-dated-canonical-dose"


def requests(profile):
    result = {}
    for directive in profile.get("training_structure") or []:
        first = date.fromisoformat(str(directive["start_date"]))
        for day in directive["days"]:
            target = first+timedelta(days=day["offset"])
            if target in result:
                raise ValueError("Overlapping training structure dates")
            result[target] = {"id": directive["id"], "name": directive["name"],
                "mode": directive["mode"], "sessions": deepcopy(day["sessions"])}
    return result


def requested_session(dated, day, slot):
    request = dated.get(day)
    return next((s for s in request["sessions"] if s.get("slot", 1) == slot+1), None) if request else None


def slots(schedule, dated, profile, available):
    """Only the requested dates change; current availability remains a ceiling."""
    counts = {d: count for d, _, count in schedule}
    limits = (profile.get("planning_controls") or {}).get("sessions_by_day")
    for day, request in dated.items():
        if day not in counts:
            continue
        desired = max((s.get("slot", 1) for s in request["sessions"]), default=0)
        if request["mode"] == "SESSION":
            desired = max(counts[day], desired)
        maximum = limits[day.weekday()] if limits is not None else 3
        request["slot_limit"] = maximum if available[day.weekday()] > 0 else 0
        counts[day] = min(desired, request["slot_limit"])
    return [(day, slot, count) for day, count in counts.items() for slot in range(max(1, count))]


def method_aliases(method):
    aliases = {method["id"]}
    for value in (method.get("method_library_id"), (method.get("method_library") or {}).get("id")):
        if value:
            aliases.add(value)
    if method.get("developmental_variant") and method["id"].endswith("-SHORT"):
        aliases.add(method["id"][:-6])
    elif method["structure"] in {"MODEL_INTERVALS", "METABOLIC_INTERVALS"}:
        aliases.add(method["id"]+"-SHORT")
    return aliases


def matching_method(method, wanted):
    parts = method.get("combined_methods")
    if len(wanted) == 1:
        # A two-session bundle must not consume a second independently requested
        # slot merely because it inherited the first parent's library card.
        if method.get("double_threshold"):
            return wanted[0] == method["id"]
        return not parts and wanted[0] in method_aliases(method)
    return bool(parts and len(parts) == len(wanted) and all(
        identifier in method_aliases(part) for identifier, part in zip(wanted, parts)))


def select_candidates(candidates, dated, day, slot):
    request = requested_session(dated, day, slot)
    if request is None:
        return candidates
    return [(sport, method) for sport, method in candidates if sport == request["sport"]
            and matching_method(method, request["method_ids"])]


def session_method_ids(session):
    """Extract identity from a server-owned revision, never prescribe its dose."""
    if session.get("double_threshold_part"):
        return [session["method_id"]] if session.get("method_id") else []
    library = session.get("method_library") or {}
    parts = library.get("components") or (session.get("execution_descriptor") or {}).get("parts") or []
    if parts:
        identifiers = [part.get("id") for part in parts]
        return identifiers if all(identifiers) else []
    identity = library.get("id") or session.get("method_id")
    return [identity] if identity else []


def mark_session(session, request, slot, method=None):
    """Keep lineage matches separate from a changed introductory execution form."""
    exact = session_method_ids(session) == request["method_ids"]
    matching = matching_method(method, request["method_ids"]) if method is not None else exact
    session["structure_slot"] = slot
    session["structure_identity_matches"] = session.get("sport") == request["sport"] and matching
    introductory = bool(method and method.get("developmental_variant")
                        and method["id"].endswith("-SHORT") and method["id"][:-6] in request["method_ids"])
    if matching and (not exact or introductory):
        session["structure_form_adjustment"] = True


def annotate(days, dated):
    """Report actual canonical outcomes, including priorities overriding a request."""
    for day in days:
        request = dated.get(date.fromisoformat(day["date"]))
        if request is None:
            continue
        outcomes = day.pop("structure_outcomes", [])
        actual = day.get("sessions") or ([day["session"]] if day.get("session") else [])
        adjustments = []
        wanted = request["sessions"]
        if not wanted and (actual or day["status"] not in {"REST", "UNAVAILABLE"}):
            adjustments.append({"code": "CURRENT_DAY_PRIORITY", "reason": day.get("explanation", "Текущото състояние на деня има предимство.")})
        for preference in wanted:
            slot = preference.get("slot", 1)
            selected = next((s for s in actual if s.get("structure_slot") == slot), None)
            if selected is None:
                outcome = next((o for o in outcomes if o["slot"] == slot), {})
                reasons = [{"code": r["code"], "reason": r["reason"]} for r in outcome.get("constraints", day.get("rejected_alternatives", []))
                           if r.get("code") != "LOWER_CURRENT_PRIORITY"]
                reason = outcome.get("reason", day.get("explanation", "Текущите ограничения не допускат поисканата сесия."))
                if slot > request.get("slot_limit", 3):
                    reason = f"Наличното време и дневните настройки допускат най-много {request['slot_limit']} сесии за този ден."
                adjustments.append({"slot": slot, "code": "STRUCTURE_SESSION_UNAVAILABLE",
                    "reason": reason,
                    "constraints": reasons[:8]})
            elif not selected.get("structure_identity_matches"):
                adjustments.append({"slot": slot, "code": "CURRENT_DAY_PRIORITY",
                    "reason": "Съществуваща задача или текущо ограничение има предимство пред предложената структура."})
            elif selected.get("structure_form_adjustment"):
                adjustments.append({"slot": slot, "code": "METHOD_FORM_ADAPTED",
                    "reason": "Изпълнението е адаптирано към актуалния опит и допустимата форма на метода; дозата е изчислена отново.",
                    "method_id": selected.get("method_id")})
        day["structure_request"] = {"id": request["id"], "name": request["name"], "mode": request["mode"],
            "version": VERSION, "status": "ADJUSTED" if adjustments else "APPLIED",
            "dose_policy": "CURRENT_DAY_REVALIDATED" if day.get("locked") else "RECOMPUTED_FROM_CURRENT_STATE",
            "requested_sessions": deepcopy(wanted),
            "adjustments": adjustments}
        if not wanted and not adjustments:
            day["explanation"] = "Почивка според избраната структура на блока."
        elif adjustments:
            day["explanation"] += " Поисканата структура е коригирана според текущите ограничения."
