"""Private, read-only view of the same learner used by plan generation.

Only the dedicated recovery-authorized page transports these observations.
Ordinary plan projections continue to omit replay/health evidence.
"""
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from biathlon.constants import COMPONENTS
from biathlon import load_evidence
from biathlon.learning_provenance import initial_heuristic_load

from .component_load_context import ComponentCapacityUnavailable
from .component_load_projection import ComponentLoadRefreshRequired, project_snapshot
from .management_schemas import ManagementProfile
from .management_store import ManagementStore
from .oauth_store import PersistentStoreFailure
from .read_session import ReadSession
from .response_monitoring import build_history, number
from .response_service import ResponseStore
from . import learning_service, training_plan_engine


def _fields(value, names):
    return {key: value.get(key) for key in names} if isinstance(value, dict) else {}


SERIES_FIELDS = ("day", "actual_q", "stress", "baseline", "stress_basis", "stress_reference", "q3", "q3_partial")
EPISODE_FIELDS = ("id", "start", "load_end", "end", "loading_days", "recovery_limit_days",
                  "recovery_days", "recovered", "ratio", "status", "proposed_change_percent",
                  "reason", "performance_change_percent")
DECISION_FIELDS = ("component", "status", "proposed_change_percent", "applied_change_percent", "reason")
CHANNEL_FIELDS = ("key", "label", "observed_days", "nominal_weight")
COMPONENT_FIELDS = ("component", "q3", "q3_observed_days", "recent_weekly_q", "reference_weekly_q", "recent_observed_days",
    "reference_observed_days", "accent_ratio", "stress_evidence_count", "predicted_stress_points", "predicted_stress_low", "predicted_stress_high", "evidence_count", "isolated_changes", "validation_status",
    "model_scope", "response_status", "test_step_percent", "predicted_performance_percent",
    "predicted_performance_low", "predicted_performance_high", "proposed_change_percent",
    "applied_change_percent", "baseline_actual_weekly_q", "target_weekly_q", "execution_weekly_q_ceiling",
    "effective_from", "expires_on", "action_status", "management_basis", "reason")
CHANNEL_LABELS = {"hrv": "HRV", "resting_hr": "Пулс в покой", "sleep_duration": "Продължителност на съня",
    "fatigue": "Умора", "sleep_quality": "Качество на съня", "stress": "Субективен стрес",
    "soreness": "Мускулна болезненост", "motivation": "Мотивация",
    "competition_motivation": "Състезателна мотивация", "morning_weight": "Сутрешна телесна маса"}


def _accounting_series(source, catalog=(), *, refresh_required=False):
    """Transport the existing 7/40 ledger, separately from learner admission.

    A rejected learning observation does not erase its recorded activity or
    replace an existing canonical index with zero. No index is reconstructed
    from Q: the authoritative aerobic and strength daily values are reused.
    """
    daily = {}
    for row in source.get("daily", []):
        if row.get("zone") in COMPONENTS:
            daily.setdefault(row["date"], {})[row["zone"]] = row
    strength = {row["date"]: row for row in (source.get("strength") or {}).get("daily", [])}
    for day, row in strength.items():
        daily.setdefault(day, {})["STR"] = row
    activities = {}
    for activity in source.get("activities", []):
        day = activity.get("date") or activity.get("local_date")
        if day:
            activities.setdefault(day, []).append(activity)
    omitted = {}
    provider_missing_dates = set()
    known = {a.get("activity_ref") for a in source.get("activities", [])}
    for activity in catalog:
        day = activity.get("local_date") or activity.get("date")
        if day and activity.get("quality_status") == "provider_missing":
            provider_missing_dates.add(day)
        if day and activity.get("activity_ref") not in known:
            omitted.setdefault(day, []).append(activity)
    dated_exclusions = {issue.get("date") or issue.get("local_date")
        for issue in (source.get("quality") or {}).get("exclusions", []) if isinstance(issue, dict)}
    unidentified = learning_service.recovery_policy.unidentified_activity_count(source)
    result = {}
    for day, values in sorted(daily.items()):
        recorded = activities.get(day, [])
        proofs = [load_evidence.assess(a, unified=bool(source.get("load_selection"))) for a in recorded]
        complete = set(values) == set(COMPONENTS) and all(number(r.get("effective_load")) is not None for r in values.values())
        partial = (not complete or bool(omitted.get(day)) or day in provider_missing_dates or day in dated_exclusions or bool(unidentified)
                   or any(not proof["complete"] or a.get("planning_estimated")
                          for a, proof in zip(recorded, proofs)))
        modelled = any(initial_heuristic_load(a) for a in recorded)
        positive_without_activity = not recorded and any((number(r.get("effective_load")) or 0) > 0 for r in values.values())
        partial = partial or positive_without_activity
        if not complete:
            status, reason = "UNAVAILABLE", "Липсва пълна дневна история по компоненти."
        elif partial:
            status, reason = "PARTIAL", "Има отчетена активност с непълни данни за натоварването. Наличната част участва в самообучението с отбелязана неопределеност."
            if unidentified:
                reason = "Има изключени активности без установена дата. Пълнотата на дневното натоварване не е потвърдена."
        elif modelled:
            status, reason = "MODELLED", "Активността е отчетена, но част от интензивността използва начална оценка по скорост. Оценката участва с отбелязан произход и по-ниска тежест."
        elif not recorded and all(r["effective_load"] == 0 for r in values.values()):
            status, reason = "REST", "В покрития календар няма отчетена тренировка."
        else:
            status, reason = "COMPLETE", None
        if refresh_required:
            status, reason = "PARTIAL", "Показаният 7/40 е от последната записана обработка. Нужно е обновяване на реалните данни; стойностите не са потвърдени с текущата проекция."
        q = {z: 0. for z in COMPONENTS}
        for activity in recorded:
            if load_evidence.is_strength(activity):
                continue
            zones = {r.get("zone"): r for r in activity.get("zones", [])}
            for z in COMPONENTS[:-1]:
                value = number(zones.get(z, {}).get("equivalent_time_min"))
                q[z] = q[z]+value if value is not None and q[z] is not None else None
        q["STR"] = number(strength.get(day, {}).get("equivalent_time_min"))
        if not complete or positive_without_activity or (not recorded and (omitted.get(day) or unidentified)):
            q = {z: None for z in COMPONENTS}
        unknown = [proof["ledger"]["unknown_seconds"]/60 for proof in proofs if proof.get("ledger") is not None]
        unknown_minutes = sum(unknown) if len(unknown) == sum(not load_evidence.is_strength(a) for a in recorded) and not omitted.get(day) and day not in provider_missing_dates and not unidentified else None
        result[day] = {"index_7_40_by_component": {z: number(values.get(z, {}).get("status_7_40")) for z in COMPONENTS},
            "recorded_q_by_component": q, "load_status": status, "load_reason": reason,
            "load_activity_count": len(recorded)+len(omitted.get(day, [])),
            "load_unknown_minutes": unknown_minutes}
    # E7/E40 and the stabilizer B=max(B0, .5*E50) depend on this window.
    # A clean current recording does not erase missing earlier observations.
    first = date.fromisoformat(min(daily)) if daily else None
    for day, row in result.items():
        last = date.fromisoformat(day)
        start = max(first, last-timedelta(days=49))
        window = [(start+timedelta(days=i)).isoformat() for i in range((last-start).days+1)]
        statuses = [result.get(key, {}).get("load_status", "UNAVAILABLE") for key in window]
        row["index_history_days"] = sum(key in result for key in window)
        row["index_load_status"] = ("PARTIAL" if any(s in {"PARTIAL", "UNAVAILABLE"} for s in statuses)
                                    else "MODELLED" if "MODELLED" in statuses else "COMPLETE")
    return result


def _series_row(row, accounting=None):
    """Allowlist nested metadata as well as the public row's scalar fields."""
    result=_fields(row,SERIES_FIELDS)
    result["actual_q_by_component"]=_fields(row.get("actual_q_by_component") or {},COMPONENTS)
    result["q3_by_component"]=_fields(row.get("q3_by_component") or {},COMPONENTS)
    result["q3_observed_days"]=_fields(row.get("q3_observed_days") or {},COMPONENTS)
    result["stress_weights"]={key:value for key,value in (row.get("stress_weights") or {}).items() if key in CHANNEL_LABELS}
    result.update(accounting or {"index_7_40_by_component": {z: None for z in COMPONENTS},
        "recorded_q_by_component": {z: None for z in COMPONENTS}, "load_status": "UNAVAILABLE",
        "load_reason": "Няма дневни данни за натоварването.", "load_activity_count": 0,
        "load_unknown_minutes": None, "index_history_days": 0, "index_load_status": "UNAVAILABLE"})
    result["learning_load_eligible"] = row.get("actual_q") is not None
    return result


def view(repository, alias, *, days=90, now=None):
    phase = ReadSession.planning(repository)
    phase.capture_planning_inputs(alias)
    settings = phase.athlete_settings(alias)
    if settings is None:
        raise HTTPException(409, "Athlete settings are required")
    now = now or datetime.now(timezone.utc)
    today = now.astimezone(ZoneInfo(settings.timezone)).date()
    first = today - timedelta(days=89)
    reader = getattr(phase, "active_planning_calendar", None) or phase.active_activity_calendar
    envelope = reader(alias, first, today) or {}
    analysis = phase.active_analysis(alias) or {}
    if analysis and envelope and any(analysis.get(key) != envelope.get(key) for key in ("generation_id", "revision")):
        raise PersistentStoreFailure("Learning inputs changed during read")
    snapshot = envelope.get("snapshot_payload") or {}
    source = snapshot.get("load_history") or {}
    accounting_source = source
    notices = []
    refresh_required = False
    try:
        source = (project_snapshot(phase, alias, {"load_history": source}) or {}).get("load_history") or {}
        accounting_source = source
    except (ComponentLoadRefreshRequired, ComponentCapacityUnavailable):
        source = {}
        refresh_required = True
        notices.append("Натоварването изисква обновяване на реалните данни. Наличните наблюдения за състоянието са запазени.")
    rows = training_plan_engine._daily_rows(source, today)
    pinned = {**envelope, "snapshot_payload": {**snapshot, "load_history": source}}
    stored = ManagementStore(phase).profile(alias)
    profile = ManagementProfile.model_validate(stored["profile"]).model_dump(mode="json") if stored.get("configured") else None
    calendar = phase.athlete_planning_calendar(alias)
    calendar = calendar.to_payload() if hasattr(calendar, "to_payload") else calendar or {}
    events = calendar.get("events") or []
    controls = (profile or {}).get("planning_controls") or {}
    history_context = training_plan_engine.planning_controls.volume_history(source, today, 0,
        gap_days=controls.get("history_gap_days", training_plan_engine.planning_history.DEFAULT_GAP_DAYS)) if profile else None
    reentry_days, _ = training_plan_engine.planning_history.reentry(profile, history_context) if profile else (None, None)
    periods = training_plan_engine.build_periodization(
        profile["program_start"], profile["program_end"], events,
        reentry_days_override=reentry_days,
        taper_days=profile["taper_days"], transition_days=profile["transition_days"],
    ) if profile else None
    report = learning_service.context(phase, alias, profile, source, rows, today,
        envelope=pinned, periodization=periods) if profile else None
    detail = (report or {}).get("recovery_policy")
    if detail is None:
        from .recovery_policy import build_view_data
        from .trainability_history import history_from_calendar
        entries = ResponseStore(phase).entries(alias)
        try:
            ti = history_from_calendar(phase, alias, envelope) if envelope.get("activities") else []
        except (ValueError, PersistentStoreFailure):
            ti = []
            notices.append("Съпоставимите показатели за работоспособност временно не са налични.")
        history = build_history(entries=entries, wellness=snapshot.get("wellness_calendar") or [],
            activities=envelope.get("activities") or [], start=first, end=today, today=today, trainability=ti)
        detail = build_view_data(source=source, rows=rows, history=history, entries=entries,
            trainability=ti, today=today, periodization=periods, events=events)["recovery_policy"]
    configured_mode = ((profile or {}).get("individual_learning") or {}).get("mode", "OFF")
    decision = detail.get("decision") or {
        "component": None, "status": "COLLECTING", "proposed_change_percent": 0.,
        "applied_change_percent": 0., "reason": "Нужни са още съпоставими наблюдения.",
    }
    if not report:
        decision = {**decision, "applied_change_percent": 0.}
    start = (today-timedelta(days=days-1)).isoformat()
    observations = detail.get("observations") or {}
    observation_map = {"load_days": "measured_days", "accepted_episodes": "accepted_blocks",
                       "reaction_episodes": "reaction_only_blocks", "performance_observations": "performance_blocks"}
    observations = {**observations, **{public: observations[private] for public, private in observation_map.items() if private in observations}}
    accounting = _accounting_series(accounting_source, envelope.get("activities") or [], refresh_required=refresh_required)
    learner_series = {row["day"]: row for row in detail.get("series", [])}
    series_days = sorted(day for day in set(learner_series) | set(accounting) if start <= day <= today.isoformat())
    series = []
    for day in series_days:
        row = learner_series.get(day) or {"day": day}
        # The current day is still in progress: existing 7/40 is visible, but
        # an incomplete daily dose is never admitted as a learning outcome.
        if day == today.isoformat():
            row = {"day": day}
        series.append(_series_row(row, accounting.get(day)))
    return {
        "schema_version": "recovery-learning-view-v1", "as_of": today.isoformat(),
        "timezone": settings.timezone, "source_date": accounting_source.get("period_end"),
        "mode": (report or {}).get("mode", configured_mode),
        "status": (report or {}).get("status", "OFF" if profile else "NOT_CONFIGURED"),
        "summary": (report or {}).get("summary") or ("Самообучението е изключено. Наблюденията остават видими." if profile else "Настройте програмата, за да включите самообучението."),
        "evidence_count": (report or {}).get("evidence_count", 0),
        "validation": _fields((report or {}).get("validation") or {"status": "WARMUP", "evaluated": 0}, ("status", "evaluated")),
        "series": series,
        "episodes": [_fields(row, EPISODE_FIELDS) for row in detail.get("episodes", [])][-30:],
        "decision": _fields(decision, DECISION_FIELDS),
        "observations": {key: value for key, value in observations.items()
                         if key in {"stress_days", "load_days", "performance_observations", "accepted_episodes", "reaction_episodes", "excluded_periods", "examined_periods"}
                         and type(value) in {int, float}},
        "channels": [{**_fields(row, CHANNEL_FIELDS), "label": row.get("label") or CHANNEL_LABELS.get(row.get("key"), "Друг показател")}
                     for row in detail.get("channels", [])],
        "components": [_fields(row, COMPONENT_FIELDS) for row in detail.get("component_assessments", []) if row.get("component") in COMPONENTS],
        "limitations": [*notices, *(report or {}).get("limitations", [])],
    }
