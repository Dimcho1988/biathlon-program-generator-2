"""One canonical proposal path for forms and chat; only the worker doses training."""
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from pydantic import ValidationError

from biathlon import planning_schedule
from biathlon.personal_methods import normalize_personal_methods
from biathlon.training_structure import method_aliases, session_method_ids
from .management_plan_store import PlanStore
from .management_schemas import ManagementProfile
from .management_store import ManagementStore, PROFILE_KEY, validate_profile_size
from .oauth_store import PersistentStoreFailure
from .training_proposal_schemas import PROPOSALS


def input_contract():
    return PROPOSALS.json_schema()


def _state(repository, alias):
    stored = ManagementStore(repository).profile(alias)
    if not stored.get("profile"):
        raise HTTPException(409, "Първо попълнете тренировъчния профил.")
    profile = ManagementProfile.model_validate(stored["profile"]).model_dump(mode="json")
    reader = getattr(repository, "athlete_planning_calendar", None)
    calendar = (reader(alias) if callable(reader) else None) or {"events": []}
    calendar = calendar.to_payload() if hasattr(calendar, "to_payload") else dict(calendar)
    effective, _ = planning_schedule.horizon(profile, calendar["events"])
    active = PlanStore(repository).current_overview(alias)
    settings = repository.athlete_settings(alias)
    today = datetime.now(timezone.utc).astimezone(ZoneInfo(settings.timezone if settings else "UTC")).date()
    result = {"kind": "TRAINING_PROPOSAL", "entry_key": PROFILE_KEY, "availability": "AVAILABLE",
        "revision": stored["revision"], "profile_revision": stored["revision"],
        "active_plan_revision": active["revision"] if active else 0,
        "payload": {"personal_methods": deepcopy(profile["method_library"]["personal_methods"]),
            "training_structure": deepcopy(profile["training_structure"]),
            "program_start": effective["program_start"], "program_end": effective["program_end"],
            "today": today.isoformat(),
            "current_plan": {k: active[k] for k in ("start_date", "end_date", "revision")} if active else None},
        "input_contract": input_contract()}
    return result, profile, effective, today


def state(repository, alias):
    return _state(repository, alias)[0]


def _target(start, length, effective, today, *, allow_started=False):
    end = start+timedelta(days=length-1)
    if start < today and not (allow_started and end >= today):
        raise HTTPException(422, "Променяйте днешни или бъдещи тренировки; изпълнената история се запазва.")
    if start.isoformat() < effective["program_start"] or end.isoformat() > effective["program_end"]:
        raise HTTPException(422, "Целият целеви блок трябва да бъде в периода на програмата.")


def _methods(profile):
    from biathlon.training_methods import resolved_methods
    from biathlon.method_library import profile_entries
    result = {}
    for method in resolved_methods(profile):
        for identifier in method_aliases(method):
            result[identifier] = method
    for method in profile_entries(profile):
        if method.get("prescription"):
            result[method["id"]] = method
    return result


def _validate_sessions(sessions, profile, methods):
    allowed_sports = (profile.get("planning_controls") or {}).get("training_sports") or [profile["actual_sport"]]
    for session in sessions:
        if session["sport"] not in allowed_sports:
            raise HTTPException(422, "Средството в блока не е включено в текущия тренировъчен профил.")
        for identifier in session["method_ids"]:
            if identifier not in methods:
                raise HTTPException(422, f"Методът {identifier} няма изпълнима дефиниция в текущия профил. Изберете актуален метод.")
            if (profile["method_library"].get("overrides", {}).get(identifier) or {}).get("enabled") is False:
                raise HTTPException(422, f"Методът {identifier} е изключен в текущия профил.")


def _source(repository, alias, proposal, profile):
    start, end = date.fromisoformat(proposal["source_start"]), date.fromisoformat(proposal["source_end"])
    length = (end-start).days+1
    if length not in (7, 14):
        raise HTTPException(422, "Изберете точно 7 или 14 последователни дни за изходния блок.")
    revisions = proposal.get("source_revisions")
    if revisions and len(set(revisions)) != len(revisions):
        raise HTTPException(422, "Версиите на изходната програма трябва да са различни.")
    raw = repository._json(repository._request("POST", "/rpc/read_onflows_training_structure", json={
        "p_alias": alias, "p_start": start.isoformat(), "p_end": end.isoformat(), "p_revisions": revisions}))
    if not isinstance(raw, dict) or not isinstance(raw.get("days"), list) or not isinstance(raw.get("missing_dates"), list):
        raise PersistentStoreFailure("Invalid frozen training structure")
    if raw["missing_dates"]:
        raise HTTPException(422, "Няма запазена тренировъчна структура за: " + ", ".join(raw["missing_dates"]) + ". Изберете наличен блок.")
    days, provenance = [], []
    expected_dates = [(start+timedelta(days=n)).isoformat() for n in range(length)]
    if [d.get("date") for d in raw["days"]] != expected_dates:
        raise PersistentStoreFailure("Frozen block dates are inconsistent")
    methods = _methods(profile)
    for offset, day in enumerate(raw["days"]):
        if type(day.get("revision")) is not int or day["revision"] < 1 or revisions and day["revision"] not in revisions:
            raise PersistentStoreFailure("Frozen block revision is inconsistent")
        source_sessions = day.get("sessions")
        if not isinstance(source_sessions, list) or len(source_sessions) > 3 or (
                not source_sessions and day.get("status") not in {"REST", "UNAVAILABLE"}):
            raise HTTPException(422, f"Денят {day['date']} няма запазена изпълнима тренировъчна структура.")
        sessions = []
        for slot, session in enumerate(source_sessions, 1):
            if not isinstance(session, dict):
                raise HTTPException(422, f"Непълна структура на сесия за {day['date']}.")
            if type(session.get("slot", slot)) is not int or session.get("slot", slot) != slot:
                raise HTTPException(422, f"Денят {day['date']} съдържа само част от сесиите. Изберете версия с цялата дневна структура.")
            identities = session_method_ids(session)
            if not identities or len(identities) > 2 or session.get("sport") not in {"Run", "NordicSki", "RollerSki"}:
                raise HTTPException(422, f"Методът за {day['date']} не може да бъде възстановен еднозначно.")
            sessions.append({"slot": slot, "method_ids": identities, "sport": session["sport"]})
        _validate_sessions(sessions, profile, methods)
        days.append({"offset": offset, "sessions": sessions})
        provenance.append({"date": day["date"], "plan_revision": day["revision"]})
    pins = sorted({d["plan_revision"] for d in provenance})
    return days, {"start_date": start.isoformat(), "end_date": end.isoformat(), "plan_revisions": pins, "days": provenance}


def _evaluate(repository, alias, proposal, expected_revision=None):
    try:
        request = PROPOSALS.validate_python(proposal).model_dump(mode="json", exclude_none=True)
    except ValidationError as exc:
        raise HTTPException(422, "Проверете вида на предложението, датите и полетата; дозите се изчисляват от алгоритъма.") from exc
    result, profile, effective, today = _state(repository, alias)
    if expected_revision is not None and expected_revision != result["revision"]:
        raise HTTPException(409, "Профилът е променен. Прегледайте предложението с актуалните данни.")
    action, corrections, days = request["action"], [], []
    plan_revision = None
    if action in {"FIX_STRUCTURE", "REPEAT_STRUCTURE", "SESSION_METHOD"}:
        plan_revision = request.get("expected_plan_revision", result["active_plan_revision"])
        if plan_revision != result["active_plan_revision"]:
            raise HTTPException(409, "Програмата е променена. Прегледайте предложението с актуалната версия.")
        request["expected_plan_revision"] = plan_revision
    if action == "PERSONAL_METHOD":
        definitions = profile["method_library"]["personal_methods"]
        definitions = [m for m in definitions if m["id"] != request["method"]["id"]] + [request["method"]]
        try:
            definitions, reports = normalize_personal_methods(definitions, profile)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        profile["method_library"]["personal_methods"] = definitions
        request["method"] = next(m for m in definitions if m["id"] == request["method"]["id"])
        corrections = [c for r in reports if r["id"] == request["method"]["id"] for c in r["corrections"]]
        summary = "Личният вариант се запазва само за този спортист. Дозата се определя при изготвянето на тренировката."
    elif action == "REMOVE_PERSONAL_METHOD":
        identifier = request["method_id"]
        definitions = profile["method_library"]["personal_methods"]
        if not any(m["id"] == identifier for m in definitions):
            raise HTTPException(404, "Личният метод не е намерен.")
        if any(identifier in s["method_ids"] for d in profile["training_structure"] for day in d["days"] for s in day["sessions"]):
            raise HTTPException(409, "Методът участва в запазена структура. Първо променете или премахнете съответния блок.")
        profile["method_library"]["personal_methods"] = [m for m in definitions if m["id"] != identifier]
        profile["method_library"]["overrides"].pop(identifier, None)
        summary = "Личният метод се премахва от бъдещия избор; изпълнените тренировки се запазват."
    elif action in {"FIX_STRUCTURE", "REPEAT_STRUCTURE"}:
        days, source = _source(repository, alias, request, profile)
        target = date.fromisoformat(request["target_start"])
        if action == "FIX_STRUCTURE" and request["target_start"] != request["source_start"]:
            raise HTTPException(422, "Фиксирането запазва същите дати. За преместване изберете повторение на блок.")
        _target(target, len(days), effective, today, allow_started=action == "FIX_STRUCTURE")
        request["source_revisions"] = source["plan_revisions"]
        mode = "FIX" if action == "FIX_STRUCTURE" else "REPEAT"
        directive = {"id": f"{mode}-{target.isoformat()}", "name": request.get("name") or ("Фиксиран блок" if mode == "FIX" else "Повторен блок"),
            "mode": mode, "start_date": target.isoformat(), "days": days, "source": source}
        profile["training_structure"] = [d for d in profile["training_structure"] if d["id"] != directive["id"]] + [directive]
        summary = "Запазват се дните, сесиите и методите. Обемът и интензивността се преизчисляват според текущото състояние и етапа на подготовка."
    elif action == "SESSION_METHOD":
        target = date.fromisoformat(request["target_date"])
        _target(target, 1, effective, today)
        request["sport"] = request.get("sport") or profile["actual_sport"]
        sessions = [{"slot": request["session_index"], "method_ids": request["method_ids"], "sport": request["sport"]}]
        _validate_sessions(sessions, profile, _methods(profile))
        days = [{"offset": 0, "sessions": sessions}]
        existing = next((d for d in profile["training_structure"] if
            0 <= (target-date.fromisoformat(d["start_date"])).days < len(d["days"])), None)
        if existing is not None:
            offset = (target-date.fromisoformat(existing["start_date"])).days
            previous = existing["days"][offset]["sessions"]
            slot = request["session_index"]
            if existing["mode"] != "SESSION" and not any(s["slot"] == slot for s in previous):
                raise HTTPException(422, "Избраната сесия липсва във фиксирания блок. Изберете съществуваща сесия.")
            existing["days"][offset]["sessions"] = sorted([s for s in previous if s["slot"] != slot]+sessions, key=lambda s: s["slot"])
        else:
            directive = {"id": f"SESSION-{target.isoformat()}", "name": request.get("name") or "Избран метод за сесията",
                "mode": "SESSION", "start_date": target.isoformat(), "days": days}
            profile["training_structure"].append(directive)
        summary = "За избраната сесия се задава метод; останалите сесии се запазват, а дозата се изчислява отново."
    else:
        identifier = request["directive_id"]
        if not any(d["id"] == identifier for d in profile["training_structure"]):
            raise HTTPException(404, "Запазената структура не е намерена.")
        profile["training_structure"] = [d for d in profile["training_structure"] if d["id"] != identifier]
        summary = "Фиксираната структура се премахва; алгоритъмът отново избира методите за тези дни."
    try:
        profile = ManagementProfile.model_validate(profile).model_dump(mode="json")
        from .method_library_service import validate_preferences
        profile["method_library"] = validate_preferences(profile["method_library"], profile)
    except ValidationError as exc:
        raise HTTPException(422, "Предложението се застъпва с друг блок или е несъвместимо с профила. Променете съответната структура.") from exc
    validate_profile_size(profile)
    display_days = deepcopy(days)
    if display_days:
        labels = _methods(profile)
        first = date.fromisoformat(request.get("target_start", request.get("target_date")))
        for day in display_days:
            day["date"] = (first+timedelta(days=day["offset"])).isoformat()
            for session in day["sessions"]:
                session["method_titles"] = [labels.get(identifier, {}).get("title", identifier) for identifier in session["method_ids"]]
    result["preview"] = {"proposal": request, "corrections": corrections, "days": display_days, "summary": summary,
        "dose_status": "RECALCULATED_ON_PUBLICATION"}
    result["command"] = {"kind": "TRAINING_PROPOSAL", "entry_key": PROFILE_KEY,
        "expected_revision": result["revision"], "patch": request}
    prepared = {"kind": "MANAGEMENT_PROFILE", "key": PROFILE_KEY, "payload": profile,
        "expected_revision": result["revision"], "training_proposal": True,
        "expected_plan_revision": plan_revision, "proposal_preview": result["preview"]}
    return result, prepared


def preview(repository, alias, proposal=None):
    return _evaluate(repository, alias, proposal)[0] if proposal is not None else state(repository, alias)


def prepare(repository, alias, patch, expected_revision, actor=None):
    action = patch.get("action") if isinstance(patch, dict) else None
    if action in {"FIX_STRUCTURE", "REPEAT_STRUCTURE", "SESSION_METHOD"} and type(patch.get("expected_plan_revision")) is not int:
        raise HTTPException(422, "Първо прегледайте предложението спрямо текущата програма.")
    if action in {"FIX_STRUCTURE", "REPEAT_STRUCTURE"} and not patch.get("source_revisions"):
        raise HTTPException(422, "Липсват проверените версии на изходния блок. Прегледайте предложението.")
    return _evaluate(repository, alias, patch, expected_revision)[1]
