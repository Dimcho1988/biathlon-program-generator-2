"""Explicit read-only replay of a stored plan and speed execution.

No plan generation, activation or history write occurs here. The existing
bounded speed report cache performs sample integration only after an explicit
request; normal management page reads never invoke this service.
"""
from copy import deepcopy
from datetime import date, datetime, timezone
from urllib.parse import quote
from zoneinfo import ZoneInfo

from fastapi import HTTPException

from biathlon import planning_schedule
from .component_load_context import read_contexts
from .management_plan_store import PlanStore
from .management_store import ManagementStore
from .oauth_store import PersistentStoreFailure
from .plan_speed_comparison import build
from .read_session import ReadSession
from .speed_load import history_view


def _stored_plan(repository, alias, kind, revision, start_date):
    if kind == "ACTIVE":
        if start_date is not None:
            raise HTTPException(422, "An active plan does not take a draft start date")
        record = PlanStore(repository).current(alias)
        if record is None:
            raise HTTPException(404, "No active plan exists")
        if record["revision"] != revision:
            raise HTTPException(409, "The active plan changed; reload the current revision")
        plan = record["payload"].get("plan")
    else:
        if start_date is None:
            raise HTTPException(422, "A draft start date is required")
        path = ("/onflows_management_entries?select=kind,entry_key,revision,payload"
                f"&athlete_alias=eq.{quote(alias, safe='')}&kind=eq.DRAFT"
                f"&entry_key=eq.{start_date.isoformat()}&revision=eq.{revision}&limit=1")
        rows = repository._json(repository._request("GET", path))
        rows = ManagementStore._validated_rows(rows, "DRAFT", 1, start_date.isoformat())
        if not rows:
            raise HTTPException(404, "The selected stored draft was not found")
        if rows[0]["revision"] != revision:
            raise PersistentStoreFailure("Unexpected draft revision")
        plan = rows[0]["payload"]
    if not isinstance(plan, dict) or not isinstance(plan.get("days"), list):
        raise PersistentStoreFailure("Invalid stored plan")
    try:
        first, last = date.fromisoformat(plan["start_date"]), date.fromisoformat(plan["end_date"])
    except (KeyError, TypeError, ValueError) as exc:
        raise PersistentStoreFailure("Invalid stored plan period") from exc
    if first > last or (last-first).days >= 366:
        raise HTTPException(422, "The stored comparison period must be between 1 and 366 days")
    if kind == "DRAFT" and first != start_date:
        raise PersistentStoreFailure("Draft period does not match its stored identity")
    return plan, first, last


def view(repository, alias, *, kind, revision, start_date=None, now=None):
    if kind not in {"DRAFT", "ACTIVE"} or type(revision) is not int or not 1 <= revision <= 9007199254740991:
        raise HTTPException(422, "A valid stored plan kind and revision are required")
    repository = repository if isinstance(repository, ReadSession) else ReadSession(repository)
    plan, first, last = _stored_plan(repository, alias, kind, revision, start_date)
    settings = repository.athlete_settings(alias)
    if settings is None:
        raise HTTPException(409, "Athlete settings are required")
    now = now or datetime.now(timezone.utc)
    today = now.astimezone(ZoneInfo(settings.timezone)).date()
    through = min(today, last)
    days = deepcopy(plan["days"])
    sports = {session.get("sport") for day in days for session in planning_schedule.day_sessions(day)
              if session.get("sport")}
    running_sports = sports & {"Run", "VirtualRun"}
    activities = []
    generation = None
    if first <= through and running_sports:
        # One existing report supplies retained compact activity comparisons.
        # Its legacy TI status cannot veto the independent V(t) comparison.
        selected_sport = next(iter(running_sports)) if len(running_sports) == 1 else None
        report = history_view(repository, alias, selected_sport, today=today,
                              period_start=first, period_end=through)
        activities = report.get("activities")
        if not isinstance(activities, list):
            raise PersistentStoreFailure("Invalid speed execution report")
        generation = {"generation_id": report.get("source_generation_id"),
                      "revision": report.get("source_revision")}
        sports.update(activity["sport"] for activity in activities if activity.get("sport"))
    # Local weights are currently a running metric. Unsupported sports retain
    # an explicit unavailable projection without reading a foreign speed model.
    contexts = read_contexts(repository, alias, sorted(sports & {"Run", "VirtualRun"}))
    result = build(days, contexts, source={"activities": activities},
                   start_date=first.isoformat(), end_date=last.isoformat())
    result.update(source_plan={"kind": kind, "revision": revision,
                               "start_date": first.isoformat(), "fingerprint": plan.get("fingerprint")},
                  original_stored_comparison=deepcopy(plan.get("speed_equivalence_comparison")),
                  evaluated_at=now.isoformat(), actual_through=through.isoformat() if first <= through else None,
                  execution_source=generation,
                  reference_policy="CURRENT_INDIVIDUAL_CURVE_RETROSPECTIVE_COMPARISON",
                  stored_plan_unchanged=True,
                  planned_scope="STORED_PRESCRIPTIONS_NOT_AUTO_RECONCILED_WITH_COMPLETION",
                  targets_converted=False,
                  note="Показани са запазаният план и наличното изпълнение при едни и същи актуални скоростни опори. Това е ретроспективно сравнение; планът, пулсовите цели, 7/40 и Recovery не се променят. Запазените предписания и изпълнението се показват отделно и не се събират като две различни тренировки.")
    return result
