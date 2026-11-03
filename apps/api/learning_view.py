"""Private, read-only view of the same learner used by plan generation.

Only the dedicated recovery-authorized page transports these observations.
Ordinary plan projections continue to omit replay/health evidence.
"""
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from fastapi import HTTPException

from .component_load_context import ComponentCapacityUnavailable
from .component_load_projection import ComponentLoadRefreshRequired, project_snapshot
from .management_schemas import ManagementProfile
from .management_store import ManagementStore
from .oauth_store import PersistentStoreFailure
from .read_session import ReadSession
from .response_monitoring import build_history
from .response_service import ResponseStore
from . import learning_service, training_plan_engine


def _fields(value, names):
    return {key: value.get(key) for key in names} if isinstance(value, dict) else {}


SERIES_FIELDS = ("day", "actual_q", "stress", "baseline")
EPISODE_FIELDS = ("id", "start", "load_end", "end", "loading_days", "recovery_limit_days",
                  "recovery_days", "recovered", "ratio", "status", "proposed_change_percent",
                  "reason", "performance_change_percent")
DECISION_FIELDS = ("component", "status", "proposed_change_percent", "applied_change_percent", "reason")
CHANNEL_FIELDS = ("key", "label", "observed_days")
CHANNEL_LABELS = {"hrv": "HRV", "resting_hr": "Пулс в покой", "sleep_duration": "Продължителност на съня",
    "fatigue": "Умора", "sleep_quality": "Качество на съня", "stress": "Субективен стрес",
    "soreness": "Мускулна болезненост", "motivation": "Мотивация",
    "competition_motivation": "Състезателна мотивация", "morning_weight": "Сутрешна телесна маса"}


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
    notices = []
    try:
        source = (project_snapshot(phase, alias, {"load_history": source}) or {}).get("load_history") or {}
    except (ComponentLoadRefreshRequired, ComponentCapacityUnavailable):
        source = {}
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
    return {
        "schema_version": "recovery-learning-view-v1", "as_of": today.isoformat(),
        "timezone": settings.timezone, "source_date": source.get("period_end"),
        "mode": (report or {}).get("mode", configured_mode),
        "status": (report or {}).get("status", "OFF" if profile else "NOT_CONFIGURED"),
        "summary": (report or {}).get("summary") or ("Самообучението е изключено. Наблюденията остават видими." if profile else "Настройте програмата, за да включите самообучението."),
        "evidence_count": (report or {}).get("evidence_count", 0),
        "validation": _fields((report or {}).get("validation") or {"status": "WARMUP", "evaluated": 0}, ("status", "evaluated")),
        "series": [_fields(row, SERIES_FIELDS) for row in detail.get("series", []) if row.get("day", "") >= start],
        "episodes": [_fields(row, EPISODE_FIELDS) for row in detail.get("episodes", [])][-30:],
        "decision": _fields(decision, DECISION_FIELDS),
        "observations": {key: value for key, value in observations.items()
                         if key in {"stress_days", "load_days", "performance_observations", "accepted_episodes", "reaction_episodes", "excluded_periods", "examined_periods"}
                         and type(value) in {int, float}},
        "channels": [{**_fields(row, CHANNEL_FIELDS), "label": row.get("label") or CHANNEL_LABELS.get(row.get("key"), "Друг показател")}
                     for row in detail.get("channels", [])],
        "limitations": [*notices, *(report or {}).get("limitations", [])],
    }
