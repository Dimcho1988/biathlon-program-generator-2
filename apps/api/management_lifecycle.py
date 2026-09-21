"""Approve once, reconcile actual work, and adapt only unexecuted future days.

The daily worker and post-sync hook share this idempotent service. A paused plan
cannot be restarted by a worker. Missing load coverage never means a rest day.
"""
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from fastapi import HTTPException

from . import management_service
from .management_schemas import ManagementProfile
from .management_store import ManagementStore
from .management_plan_store import PlanStore

_hash = management_service._hash


def _context(repository, alias, now):
    stored = ManagementStore(repository).profile(alias)
    if not stored["configured"]:
        raise HTTPException(409, "Configure a planning profile first")
    profile = ManagementProfile.model_validate(stored["profile"]).model_dump(mode="json")
    state = management_service.input_state(repository, alias, evaluated_at=now)
    frozen = {**state, "management_profile": profile, "profile_revision": stored["revision"]}
    # Calendar/preferences edits and new speed observations are ordinary input
    # changes. New dosing rules, physiology or Recovery settings need approval.
    critical = {k: frozen[k] for k in ("physiology", "rule_versions", "management_profile", "profile_revision")}
    critical["recovery"] = [e for e in frozen["model_entries"] if e["kind"] == "RECOVERY"]
    return stored["revision"], profile, frozen, _hash(frozen), _hash(critical)


def _require_revision(record, expected):
    if (record["revision"] if record else 0) != expected:
        raise HTTPException(409, "The active plan changed; reload before editing")


def _eligible(plan):
    return plan.get("activation_eligible") is True and plan.get("source", {}).get("readiness_known") is True


def current(repository, alias, *, now=None):
    store = PlanStore(repository)
    record = store.current(alias)
    if record is None:
        return {"active": None, "history": []}
    now = now or datetime.now(timezone.utc)
    _, _, _, fingerprint, _ = _context(repository, alias, now)
    stale = record["payload"].get("processed_input_fingerprint") != fingerprint
    return {"active": {**record, "stale": stale,
                       "actionable": record["payload"]["status"] == "ACTIVE" and not stale,
                       "stale_reason": "Нови данни очакват преизчисление. Изчакайте обновяването или изберете „Обнови сега“." if stale else None},
            "history": store.history(alias)}


def activate(repository, alias, body, actor, *, now=None):
    now = now or datetime.now(timezone.utc)
    store = PlanStore(repository)
    previous = store.current(alias)
    _require_revision(previous, body.expected_revision)
    checkpoint = store.checkpoint(alias)
    profile_revision, profile, frozen, fingerprint, approval = _context(repository, alias, now)
    rows = ManagementStore(repository).drafts(alias, start_date=body.start_date)
    draft = next((r for r in rows if r["revision"] == body.draft_revision), None)
    if not draft or rows[0]["revision"] != body.draft_revision:
        raise HTTPException(409, "Select the latest saved draft for this start date")
    plan = deepcopy(draft["payload"])
    if plan.get("input_fingerprint") != fingerprint or not _eligible(plan):
        raise HTTPException(409, "Regenerate and review a current plan with sufficient data")
    plan.update(review_required=False, automatically_published=False)
    payload = {"schema_version": "active-plan-v2", "status": "ACTIVE", "mode": profile["adaptation_mode"],
               "approved_by": str(actor), "approved_profile_revision": profile_revision,
               "approved_settings_fingerprint": approval, "processed_input_fingerprint": fingerprint,
               "evaluated_on": frozen["evaluation_date"], "plan": plan, "proposal": None,
               "origin": {"start_date": body.start_date.isoformat(), "draft_revision": body.draft_revision},
               "decisions": {}, "outcomes": [], "changes": [],
               "reason": "Програмата е утвърдена. Следващите дни се адаптират според избрания режим."}
    return store.save(alias, payload, body.expected_revision, actor, "ACTIVATE", profile_revision, checkpoint)


def reconcile(plan, source, today, decisions, previous_outcomes=()):
    """Calendar-day matching is transparent, not a claim of workout identity."""
    outcomes = {o["date"]: deepcopy(o) for o in previous_outcomes}
    daily = source.get("daily", [])
    planned_days = {o["date"]: {"date": o["date"], "session": {
        "title": o.get("planned_title"), "total_minutes": o.get("planned_minutes", 0),
        "sport": o.get("planned_sport")} if o.get("planned_title") else None} for o in previous_outcomes}
    planned_days.update({d["date"]: d for d in plan.get("days", [])})
    for day in planned_days.values():
        key = day["date"]
        if key >= today.isoformat():
            continue
        actual = [a for a in source.get("activities", []) if a.get("date") == key]
        covered = {r["zone"] for r in daily if r["date"] == key} >= {"Z1", "Z2", "Z3", "Z4", "Z5"}
        covered = covered and any(r["date"] == key for r in (source.get("strength") or {}).get("daily", []))
        if key in outcomes and not covered and not actual:
            continue  # An older outcome outside the current import window stays historical evidence.
        session = day.get("session")
        status = "RECORDED" if actual else "SKIPPED" if decisions.get(key, {}).get("action") == "SKIP" else "MISSED" if covered and session else "REST" if covered else "UNKNOWN"
        same_sport = [a for a in actual if a.get("sport") == (session or {}).get("sport")]
        if actual and session and not same_sport:
            status = "DIFFERENT_ACTIVITY"
        outcomes[key] = {"date": key, "status": status,
            "planned_title": session.get("title") if session else None,
            "planned_sport": session.get("sport") if session else None,
            "planned_minutes": session.get("total_minutes", 0.) if session else 0.,
            "actual_minutes": round(sum(float(a.get("duration_min") or 0.) for a in actual), 2) if covered or actual else None,
            "activity_refs": [a.get("activity_ref") for a in actual],
            "matching": "CALENDAR_DAY_AND_ACTUAL_SPORT_NOT_INFERRED_WORKOUT_IDENTITY",
            "load_source": "IMPORTED_ACTUAL_ONLY", "catchup_required": False}
    return [outcomes[k] for k in sorted(outcomes)][-366:]


def changes_between(old, new):
    previous = {d["date"]: d for d in old.get("days", [])}
    changes = []
    for day in new.get("days", []):
        before = previous.get(day["date"])
        prior = before.get("session") if before else None
        following = day.get("session")
        signature = lambda s: (s.get("method_id"), s.get("total_minutes"), s.get("blocks")) if s else None
        if before is None or signature(prior) != signature(following) or before["status"] != day["status"]:
            changes.append({"date": day["date"], "before": prior.get("title") if prior else None,
                            "after": following.get("title") if following else day["explanation"],
                            "before_minutes": prior.get("total_minutes") if prior else 0,
                            "after_minutes": following.get("total_minutes") if following else 0,
                            "reason": day["explanation"]})
    return changes


def refresh(repository, alias, actor=None, *, expected_revision=None, now=None, automatic=False,
            force=False, resume=False, day_action=None):
    now = now or datetime.now(timezone.utc)
    store = PlanStore(repository)
    record = store.current(alias)
    if record is None:
        return None
    if expected_revision is not None:
        _require_revision(record, expected_revision)
    old = record["payload"]
    actor = actor or old["approved_by"]
    if automatic and old["status"] in {"PAUSED", "COMPLETED"}:
        return record
    if old["status"] == "PAUSED" and not resume:
        if day_action:
            raise HTTPException(409, "Resume the plan before changing its days")
        return record
    checkpoint = store.checkpoint(alias)
    profile_revision, profile, frozen, fingerprint, approval_hash = _context(repository, alias, now)
    if not force and not day_action and old.get("processed_input_fingerprint") == fingerprint:
        store.defer_check(alias, record["revision"])
        return record
    today = date.fromisoformat(frozen["evaluation_date"])
    payload = deepcopy(old)
    decisions = payload.setdefault("decisions", {})
    if day_action:
        if not today <= day_action.date <= date.fromisoformat(profile["program_end"]):
            raise HTTPException(422, "Only today and future days can be changed")
        if day_action.action == "CLEAR":
            decisions.pop(day_action.date.isoformat(), None)
        else:
            decisions[day_action.date.isoformat()] = {"action": day_action.action, "note": day_action.note}
    envelope = repository.active_activity_calendar(alias, today - timedelta(days=89), today) or {}
    source = (envelope.get("snapshot_payload") or {}).get("load_history") or {}
    payload["outcomes"] = reconcile(old["plan"], source, today, decisions, old.get("outcomes", []))
    payload.update(evaluated_on=today.isoformat(), processed_input_fingerprint=fingerprint)
    if today > date.fromisoformat(profile["program_end"]):
        payload.update(status="COMPLETED", proposal=None, changes=[], reason="Периодът на програмата е завършен. Историята е запазена.")
        return store.save(alias, payload, record["revision"], actor, "COMPLETE", profile_revision, checkpoint, automatic=automatic)
    start = max(today, date.fromisoformat(old["origin"]["start_date"]), date.fromisoformat(profile["program_start"]))
    if start > today + timedelta(days=7):
        raise HTTPException(409, "The new program start requires a separate draft")
    locked = next((d for d in old["plan"]["days"] if d["date"] == today.isoformat() and d.get("session")), None)
    if day_action and day_action.date == today:
        locked = None
    plan = management_service.build_draft(repository, alias, start, profile_revision, now=now, decisions=decisions, locked_day=locked)
    if plan.get("source", {}).get("generation_id") != checkpoint.get("generation_id"):
        raise HTTPException(409, "Analysis changed during replanning")
    payload["changes"] = changes_between(old["plan"], plan)
    needs_rule_approval = approval_hash != old["approved_settings_fingerprint"] or profile_revision != old["approved_profile_revision"]
    if _eligible(plan) and old["mode"] == "AUTO" and not needs_rule_approval:
        plan.update(review_required=False, automatically_published=automatic)
        payload.update(status="ACTIVE", plan=plan, proposal=None,
                       reason="Следващите дни са адаптирани към реалното изпълнение. Пропуснатото не се наваксва автоматично.")
        operation = "RESUME" if resume else "DAY" if day_action else "ADAPT"
        if resume:
            payload["approved_by"] = str(actor)
    else:
        payload.update(status="REVIEW_REQUIRED", proposal=plan,
                       reason="Нужен е преглед на променените правила." if needs_rule_approval else
                       "Предложената адаптация очаква утвърждаване." if _eligible(plan) else
                       "Липсват достатъчно актуални данни за следващата доза. Обновете активностите и прегледайте причините.")
        operation = "REVIEW"
    return store.save(alias, payload, record["revision"], actor, operation, profile_revision, checkpoint, automatic=automatic)


def approve_proposal(repository, alias, body, actor, *, now):
    """Approve the exact visible proposal; never silently regenerate on click."""
    store = PlanStore(repository)
    record = store.current(alias)
    _require_revision(record, body.expected_revision)
    if not record or record["payload"]["status"] != "REVIEW_REQUIRED":
        raise HTTPException(409, "There is no pending proposal to approve")
    checkpoint = store.checkpoint(alias)
    profile_revision, profile, frozen, fingerprint, approval_hash = _context(repository, alias, now)
    payload = deepcopy(record["payload"])
    proposal = payload.get("proposal")
    if not proposal or proposal.get("input_fingerprint") != fingerprint or not _eligible(proposal):
        raise HTTPException(409, "Refresh and review the current proposal before approval")
    proposal.update(review_required=False, automatically_published=False)
    payload.update(status="ACTIVE", plan=proposal, proposal=None, approved_by=str(actor),
                   approved_profile_revision=profile_revision, approved_settings_fingerprint=approval_hash,
                   mode=profile["adaptation_mode"], evaluated_on=frozen["evaluation_date"],
                   processed_input_fingerprint=fingerprint, reason="Показаната адаптация е утвърдена без промяна на прегледаните задачи.")
    return store.save(alias, payload, record["revision"], actor, "APPROVE", profile_revision, checkpoint)


def action(repository, alias, body, actor, *, now=None):
    now = now or datetime.now(timezone.utc)
    if body.action == "APPROVE":
        return approve_proposal(repository, alias, body, actor, now=now)
    if body.action in {"REFRESH", "RESUME"}:
        return refresh(repository, alias, actor, expected_revision=body.expected_revision, now=now,
                       force=True, resume=body.action == "RESUME")
    store = PlanStore(repository)
    record = store.current(alias)
    _require_revision(record, body.expected_revision)
    if record is None:
        raise HTTPException(404, "No active plan")
    checkpoint = store.checkpoint(alias)
    profile_revision, _, frozen, fingerprint, _ = _context(repository, alias, now)
    payload = deepcopy(record["payload"])
    payload.update(status="PAUSED", evaluated_on=frozen["evaluation_date"], processed_input_fingerprint=fingerprint,
                   changes=[], reason="Автоматичното планиране е поставено на пауза. Историята е запазена.")
    return store.save(alias, payload, record["revision"], actor, "PAUSE", profile_revision, checkpoint)


def run_due(repository, *, now=None):
    """Bounded retries are independent of the successful activity sync."""
    import logging
    now = now or datetime.now(timezone.utc)
    store = PlanStore(repository)
    rows = store.due(now)
    for row in rows:
        try:
            store.queue_import(row["athlete_alias"])
            refresh(repository, row["athlete_alias"], row["approved_by"], expected_revision=row["revision"], now=now, automatic=True)
        except Exception as exc:
            logging.getLogger(__name__).warning("management_adaptation_deferred error_type=%s", type(exc).__name__)
            store.defer_check(row["athlete_alias"], row["revision"])
    return len(rows)
