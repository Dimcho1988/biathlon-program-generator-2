"""Small independent records. Canonical snapshots and model results are read-only."""
from datetime import date, datetime, timedelta, timezone
from hashlib import sha256
from urllib.parse import quote
from zoneinfo import ZoneInfo

from fastapi import HTTPException

from .oauth_store import PersistentStoreFailure
from .response_monitoring import baselines, build_history, latest_entries


class ResponseStore:
    def __init__(self, repository):
        self.repository = repository

    def entries(self, alias):
        rows = []
        for offset in range(0, 10000, 1000):
            response = self.repository._request("GET", "/onflows_response_entries?select=kind,entry_key,observed_date,revision,payload,recorded_at"
                f"&athlete_alias=eq.{quote(alias,safe='')}&order=recorded_at.desc,kind.asc,entry_key.asc,revision.desc&limit=1000&offset={offset}")
            batch = self.repository._json(response)
            if not isinstance(batch,list):
                raise PersistentStoreFailure("Invalid response history")
            rows.extend(batch)
            if len(batch)<1000:
                return list(latest_entries(rows).values())
        raise PersistentStoreFailure("Response history exceeds the bounded read")

    def save(self, alias, kind, key, day, payload, expected_revision, actor_id):
        # Conflict is returned as ordinary JSON, avoiding retries of writes.
        response = self.repository._request("POST","/rpc/save_onflows_response_entry",json={
            "p_alias":alias,"p_kind":kind,"p_key":key,"p_day":day,"p_payload":payload,
            "p_expected_revision":expected_revision,"p_actor":actor_id})
        result = self.repository._json(response)
        if not isinstance(result,dict):
            raise PersistentStoreFailure("Invalid response write result")
        if result.get("conflict"):
            raise HTTPException(409,"The report changed; reload before editing")
        return result


def sources(repository, alias, start, end):
    settings = repository.athlete_settings(alias)
    if settings is None:
        raise HTTPException(409,"Athlete settings are required")
    calendar = repository.active_activity_calendar(alias,start,end) or {}
    snapshot = calendar.get("snapshot_payload") or {}
    return {"timezone":settings.timezone,"generation_id":calendar.get("generation_id"),
        "revision":calendar.get("revision"),"wellness":snapshot.get("wellness_calendar") or [],
        "load_history":snapshot.get("load_history") or {},
        "activities":calendar.get("activities") or []}


def history(repository, alias, start, end, now=None):
    now = now or datetime.now(timezone.utc)
    settings = repository.athlete_settings(alias)
    if settings is None:
        raise HTTPException(409,"Athlete settings are required")
    today = now.astimezone(ZoneInfo(settings.timezone)).date()
    end = end or today
    start = start or end-timedelta(days=29)
    if start>end or (end-start).days>=90 or end>today:
        raise HTTPException(422,"Choose a past or current period of 1 to 90 days")
    data = sources(repository,alias,start-timedelta(days=60),end)
    result = build_history(entries=ResponseStore(repository).entries(alias),wellness=data["wellness"],activities=data["activities"],start=start,end=end,today=today)
    result.update({"timezone":data["timezone"],"generation_id":data["generation_id"],"revision":data["revision"]})
    return result


def save_report(repository, alias, kind, body, actor, now=None):
    now = now or datetime.now(timezone.utc)
    settings = repository.athlete_settings(alias)
    if settings is None:
        raise HTTPException(409,"Athlete settings are required")
    tz = ZoneInfo(settings.timezone)
    today = now.astimezone(tz).date()
    store = ResponseStore(repository)
    payload = body.model_dump(mode="json",exclude={"expected_revision"})
    payload["schema_version"] = "response-entry-v1"
    if kind == "DAILY":
        if body.observed_at is not None and (body.observed_at.tzinfo is None or body.observed_at>now+timedelta(minutes=5) or body.observed_at.astimezone(tz).date()!=body.day):
            raise HTTPException(422,"Report timestamp must match athlete-local day")
        if not today-timedelta(days=90)<=body.day<=today:
            raise HTTPException(422,"Report date is outside the editable period")
        key, day = body.day.isoformat(), body.day.isoformat()
        payload["source"] = "ONFLOWS"
        payload["scale_version"] = "wellness-1-5-higher-worse-v1"
    elif kind == "SESSION":
        activity = repository.activity_detail(alias,body.activity_ref)
        if activity is None:
            raise HTTPException(404,"Activity is unavailable for this athlete")
        day = activity["local_date"]
        if not (today-timedelta(days=90)).isoformat()<=day<=today.isoformat():
            raise HTTPException(422,"Activity date is outside the editable period")
        key = body.activity_ref
        payload["source"] = "ONFLOWS"
    elif kind == "BLOCK":
        key, day = body.start.isoformat(),body.start.isoformat()
        existing = latest_entries(store.entries(alias))
        previous = existing.get((kind,key))
        if body.start<today or (previous and body.start<=today):
            raise HTTPException(409,"A started block is fixed; create the next block prospectively")
        for (entry_kind,entry_key),e in existing.items():
            if entry_kind=="BLOCK" and entry_key!=key and day<=e["payload"]["recovery_end"] and payload["recovery_end"]>=e["payload"]["start"]:
                raise HTTPException(409,"Monitoring blocks must not overlap")
        if (body.recovery_end-today).days>90:
            raise HTTPException(422,"Block is outside the planning horizon")
        # Freeze only observations already available at creation, never future
        # reports. The anchor and numeric reference survive historical edits.
        data = sources(repository,alias,today-timedelta(days=28),today)
        daily = {k:e["payload"] for (kind,k),e in existing.items() if kind=="DAILY"}
        devices = {r["date"]:r.get("metrics",{}) for r in data["wellness"]}
        payload["baseline"] = previous["payload"].get("baseline",{}) if previous else baselines(daily,devices,today)
        payload["baseline_frozen_on"] = previous["payload"].get("baseline_frozen_on") if previous else today.isoformat()
    else:
        if not today-timedelta(days=90)<=body.day<=today:
            raise HTTPException(422,"Test date is outside the editable period")
        key = sha256(f"{body.day}:{body.protocol}:{body.protocol_version}".encode()).hexdigest()[:32]
        day = body.day.isoformat()
        payload["automatic_weight"] = 0
        # Persist actual load windows with the outcome, so the learner retains
        # its observations after the import's rolling history has moved on.
        from .load_adaptation import load_observations
        # Align backdated outcomes with their own observation date. Missing
        # historical data stays unknown; it is never manufactured as zero load.
        data = sources(repository,alias,body.day-timedelta(days=89),body.day)
        source = data["load_history"]
        rows = [*source.get("daily", []), *[{**r,"zone":"STR"} for r in source.get("strength", {}).get("daily", [])]]
        quality = source.get("quality") or {}
        payload["observed_load_windows"] = [] if quality.get("limited_activities") or quality.get("excluded_activities") else load_observations(store.entries(alias), rows, body.day)
        payload["load_source"] = {"generation_id":data["generation_id"],"revision":data["revision"]}
    return store.save(alias,kind,key,day,payload,body.expected_revision,str(actor))
