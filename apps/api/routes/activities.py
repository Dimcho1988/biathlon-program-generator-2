"""Activities HTTP routes."""

from datetime import date, timedelta
import re
from typing import Annotated, Mapping
from fastapi import Header, HTTPException
from ..trainability_history import history_from_calendar, read_calendar
from ..oauth_store import PersistentStoreFailure
from ..activity_catalog import (
    ACTIVITY_REF_PATTERN,
    activity_calendar_payload,
    activity_detail_payload,
    downsample_model_input,
)
from ..schemas import (
    ActivityCalendarResponse,
    ActivityDetailResponse,
    ActivitySeriesResponse,
    ActivityViewResponse,
)
from fastapi import APIRouter
from .. import dependencies
from ..sync_service import optional_iso

router = APIRouter()
ACTIVITY_SHADOW_REF_PATTERN = re.compile(r"^(?:shadow-|act_)[a-f0-9]{32}$")


@router.get("/api/v2/real/activity-shadows")
def real_activity_shadow_index(
    authorization: Annotated[str | None, Header()] = None,
    athlete_alias: Annotated[
        str | None, Header(alias="X-OnFlows-Athlete-Alias")
    ] = None,
):
    dependencies.authorize(authorization)
    try:
        rows = dependencies.repository().activity_shadow_index(dependencies.validated_alias(athlete_alias))
    except PersistentStoreFailure as exc:
        raise HTTPException(
            status_code=503, detail="Persistent server storage is unavailable"
        ) from exc
    return {"schema_version": "activity-shadow-index-v1", "activities": rows}


@router.get("/api/v2/real/trainability")
def real_trainability_history(
    period_start: date | None = None,
    period_end: date | None = None,
    authorization: Annotated[str | None, Header()] = None,
    athlete_alias: Annotated[str | None, Header(alias="X-OnFlows-Athlete-Alias")] = None,
):
    dependencies.authorize(authorization)
    alias = dependencies.validated_alias(athlete_alias)
    end = period_end or date.today()
    start = period_start or end - timedelta(days=89)
    if start > end or (end - start).days >= 90:
        raise HTTPException(status_code=422, detail="Index period must contain 1–90 days")
    try:
        repository = dependencies.repository()
        envelope = read_calendar(repository, alias, date.min, end)
        if not isinstance(envelope, Mapping):
            raise ValueError("No active analysis generation")
        activities = history_from_calendar(repository, alias, envelope)
        activities = [row for row in activities if start.isoformat() <= row["local_date"] <= end.isoformat()]
        return {
            "schema_version": "trainability-history-v1", "period_start": start.isoformat(),
            "period_end": end.isoformat(), "generation_id": envelope.get("generation_id"),
            "revision": envelope.get("revision", 0),
            "activities": sorted(activities, key=lambda row: (row["start_at_utc"], row["activity_ref"])),
        }
    except (PersistentStoreFailure, TypeError, ValueError, KeyError) as exc:
        raise HTTPException(status_code=503, detail="Trainability history is unavailable") from exc


@router.get("/api/v2/real/activities", response_model=ActivityCalendarResponse)
def real_activity_calendar(
    period_start: date | None = None,
    period_end: date | None = None,
    authorization: Annotated[str | None, Header()] = None,
    athlete_alias: Annotated[
        str | None, Header(alias="X-OnFlows-Athlete-Alias")
    ] = None,
):
    dependencies.authorize(authorization)
    end = period_end or date.today()
    start = period_start or end - timedelta(days=89)
    if start > end or (end - start).days >= 90:
        raise HTTPException(status_code=422, detail="Activity period must contain 1–90 days")
    alias = dependencies.validated_alias(athlete_alias)
    try:
        repository = dependencies.repository()
        active_calendar = getattr(repository, "active_activity_calendar", None)
        if callable(active_calendar):
            envelope = active_calendar(alias, start, end)
            if not isinstance(envelope, Mapping):
                raise ValueError("No active activity generation is available")
            raw_rows = envelope.get("activities")
            snapshot = envelope.get("snapshot_payload")
            if not isinstance(raw_rows, list) or not isinstance(snapshot, Mapping):
                raise ValueError("Active activity generation is invalid")
            rows = tuple(
                row for row in raw_rows if isinstance(row, Mapping)
            )
            if len(rows) != len(raw_rows):
                raise ValueError("Active activity rows are invalid")
            shadow_zones = {}
            for row in rows:
                raw_zone_summary = row.get("hrmod_zone_summary")
                if raw_zone_summary is None:
                    raw_zone_summary = []
                if not isinstance(raw_zone_summary, list) or not all(
                    isinstance(item, Mapping) for item in raw_zone_summary
                ):
                    raise ValueError("Pinned HRmod summary is invalid")
                shadow_zones[str(row.get("activity_ref") or "")] = [
                    dict(item) for item in raw_zone_summary
                ]
            generation_metadata: Mapping[str, object] | None = {
                "generation_id": envelope.get("generation_id"),
                "revision": int(envelope.get("revision") or 0),
                "analysis_as_of": optional_iso(
                    envelope.get("analysis_as_of")
                ),
                "activated_at": optional_iso(envelope.get("activated_at")),
            }
        else:
            # Test/rollout compatibility only. The production repository
            # implements the one-RPC generation-pinned read above.
            rows = repository.activity_calendar(alias, start, end)
            shadow_zones = repository.activity_shadow_zone_summaries(
                alias,
                tuple(str(row.get("activity_ref") or "") for row in rows),
            )
            snapshot = repository.latest(alias)
            generation_metadata = None
    except (PersistentStoreFailure, TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=503, detail="Persistent server storage is unavailable"
        ) from exc
    snapshot_mapping = snapshot if isinstance(snapshot, Mapping) else {}
    snapshot_has_wellness_calendar = "wellness_calendar" in snapshot_mapping
    wellness_days = snapshot_mapping.get("wellness_calendar", [])
    wellness_days = wellness_days if isinstance(wellness_days, list) else []
    recovery_history = snapshot_mapping.get("recovery_history")
    recovery_history = recovery_history if isinstance(recovery_history, Mapping) else {}
    diagnostics = recovery_history.get("wellness_diagnostics")
    diagnostics = diagnostics if isinstance(diagnostics, Mapping) else {}
    records_received = diagnostics.get("records_received", 0)
    records_received = records_received if isinstance(records_received, int) else 0
    latest_observed_date = diagnostics.get("latest_observed_date")
    latest_observed_date = latest_observed_date if isinstance(latest_observed_date, str) else None
    displayed_wellness_days = [
        day
        for day in wellness_days
        if isinstance(day, Mapping)
        and start.isoformat() <= str(day.get("date") or "") <= end.isoformat()
    ]
    if not snapshot_has_wellness_calendar:
        wellness_state = "refresh_required"
    elif displayed_wellness_days:
        wellness_state = "available"
    elif wellness_days:
        wellness_state = "outside_snapshot_period"
    elif records_received == 0:
        wellness_state = "no_provider_records"
    else:
        wellness_state = "no_recognized_values"
    return activity_calendar_payload(
        athlete_alias=alias,
        period_start=start,
        period_end=end,
        rows=rows,
        shadow_zones=shadow_zones,
        wellness_days=displayed_wellness_days,
        wellness_status={
            "state": wellness_state,
            "records_received": records_received,
            "stored_days": len(wellness_days),
            "displayed_days": len(displayed_wellness_days),
            "latest_observed_date": latest_observed_date,
        },
        generation_metadata=generation_metadata,
    )


def _activity_view_payload(
    row: Mapping[str, object], activity_ref: str
) -> dict[str, object]:
    catalog = row.get("catalog_payload")
    raw_series = row.get("series_payload")
    raw_shadow = row.get("shadow_payload")
    if not isinstance(catalog, Mapping):
        raise ValueError("Activity view catalog is invalid")
    if raw_series is not None and not isinstance(raw_series, Mapping):
        raise ValueError("Activity view series is invalid")
    if raw_shadow is not None and not isinstance(raw_shadow, Mapping):
        raise ValueError("Activity view shadow is invalid")

    revision = row.get("revision")
    generation_id = row.get("generation_id")
    if (
        isinstance(revision, bool)
        or not isinstance(revision, int)
        or revision < 0
        or (generation_id is not None and not isinstance(generation_id, str))
        or (generation_id is None) != (revision == 0)
    ):
        raise ValueError("Activity view generation is invalid")

    pointers = {
        "input_key": raw_series,
        "shadow_run_key": raw_shadow,
    }
    for key, payload in pointers.items():
        pointer = row.get(key)
        if pointer is not None and (
            not isinstance(pointer, str) or len(pointer) != 64
        ):
            raise ValueError("Activity view pointer is invalid")
        if (pointer is None) != (payload is None):
            raise ValueError("Activity view payload is not pinned")
    canonical_key = row.get("canonical_run_key")
    if canonical_key is not None and (
        not isinstance(canonical_key, str) or len(canonical_key) != 64
    ):
        raise ValueError("Activity view canonical pointer is invalid")

    shadow_zone_summary = (
        raw_shadow.get("zone_summary")
        if isinstance(raw_shadow, Mapping)
        and isinstance(raw_shadow.get("zone_summary"), list)
        else []
    )
    detail = activity_detail_payload(
        {
            **dict(catalog),
            "activity_ref": activity_ref,
            "latest_canonical_run_key": canonical_key,
            "latest_shadow_run_key": row.get("shadow_run_key"),
            "previous_activity_ref": row.get("previous_activity_ref"),
            "next_activity_ref": row.get("next_activity_ref"),
            "shadow_available": raw_shadow is not None,
        },
        [item for item in shadow_zone_summary if isinstance(item, Mapping)],
    )
    series = None
    if isinstance(raw_series, Mapping):
        series = {
            **downsample_model_input(raw_series),
            "activity_ref": activity_ref,
        }
    return {
        "schema_version": "activity-view-v1",
        "generation_id": generation_id,
        "revision": revision,
        "analysis_as_of": optional_iso(row.get("analysis_as_of")),
        "activated_at": optional_iso(row.get("activated_at")),
        "activity": detail,
        "series": series,
        "shadow": dict(raw_shadow) if isinstance(raw_shadow, Mapping) else None,
    }


@router.get(
    "/api/v2/real/activities/{activity_ref}/view",
    response_model=ActivityViewResponse,
)
def real_activity_view(
    activity_ref: str,
    authorization: Annotated[str | None, Header()] = None,
    athlete_alias: Annotated[
        str | None, Header(alias="X-OnFlows-Athlete-Alias")
    ] = None,
):
    """Return detail, charts and shadow pinned to one active generation."""

    dependencies.authorize(authorization)
    if not ACTIVITY_REF_PATTERN.fullmatch(activity_ref):
        raise HTTPException(status_code=422, detail="Invalid activity reference")
    try:
        row = dependencies.repository().active_activity_view(
            dependencies.validated_alias(athlete_alias), activity_ref
        )
        payload = (
            _activity_view_payload(row, activity_ref)
            if isinstance(row, Mapping)
            else None
        )
    except (PersistentStoreFailure, TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=503, detail="Persistent server storage is unavailable"
        ) from exc
    if payload is None:
        raise HTTPException(status_code=404, detail="Activity is unavailable")
    return payload


@router.get(
    "/api/v2/real/activities/{activity_ref}",
    response_model=ActivityDetailResponse,
)
def real_activity_detail(
    activity_ref: str,
    authorization: Annotated[str | None, Header()] = None,
    athlete_alias: Annotated[
        str | None, Header(alias="X-OnFlows-Athlete-Alias")
    ] = None,
):
    dependencies.authorize(authorization)
    if not ACTIVITY_REF_PATTERN.fullmatch(activity_ref):
        raise HTTPException(status_code=422, detail="Invalid activity reference")
    try:
        row = dependencies.repository().activity_detail(
            dependencies.validated_alias(athlete_alias), activity_ref
        )
    except PersistentStoreFailure as exc:
        raise HTTPException(
            status_code=503, detail="Persistent server storage is unavailable"
        ) from exc
    if row is None:
        raise HTTPException(status_code=404, detail="Activity is unavailable")
    return activity_detail_payload(row)


@router.get(
    "/api/v2/real/activities/{activity_ref}/series",
    response_model=ActivitySeriesResponse,
)
def real_activity_series(
    activity_ref: str,
    authorization: Annotated[str | None, Header()] = None,
    athlete_alias: Annotated[
        str | None, Header(alias="X-OnFlows-Athlete-Alias")
    ] = None,
):
    dependencies.authorize(authorization)
    if not ACTIVITY_REF_PATTERN.fullmatch(activity_ref):
        raise HTTPException(status_code=422, detail="Invalid activity reference")
    try:
        payload = dependencies.repository().activity_series(
            dependencies.validated_alias(athlete_alias), activity_ref
        )
    except PersistentStoreFailure as exc:
        raise HTTPException(
            status_code=503, detail="Persistent server storage is unavailable"
        ) from exc
    if payload is None:
        raise HTTPException(status_code=404, detail="Activity series is unavailable")
    return {**payload, "activity_ref": activity_ref}


@router.get("/api/v2/real/activities/{activity_ref}/shadow")
def real_activity_shadow_detail(
    activity_ref: str,
    authorization: Annotated[str | None, Header()] = None,
    athlete_alias: Annotated[
        str | None, Header(alias="X-OnFlows-Athlete-Alias")
    ] = None,
):
    dependencies.authorize(authorization)
    if not ACTIVITY_REF_PATTERN.fullmatch(activity_ref):
        raise HTTPException(status_code=422, detail="Invalid activity reference")
    try:
        payload = dependencies.repository().activity_shadow(
            dependencies.validated_alias(athlete_alias), activity_ref
        )
    except PersistentStoreFailure as exc:
        raise HTTPException(
            status_code=503, detail="Persistent server storage is unavailable"
        ) from exc
    if payload is None:
        raise HTTPException(status_code=404, detail="Activity shadow result is unavailable")
    return payload


@router.get("/api/v2/real/activity-shadow")
def real_activity_shadow(
    activity_ref: str,
    authorization: Annotated[str | None, Header()] = None,
    athlete_alias: Annotated[
        str | None, Header(alias="X-OnFlows-Athlete-Alias")
    ] = None,
):
    dependencies.authorize(authorization)
    if not ACTIVITY_SHADOW_REF_PATTERN.fullmatch(activity_ref):
        raise HTTPException(status_code=422, detail="Invalid activity shadow reference")
    try:
        payload = dependencies.repository().activity_shadow(
            dependencies.validated_alias(athlete_alias), activity_ref
        )
    except PersistentStoreFailure as exc:
        raise HTTPException(
            status_code=503, detail="Persistent server storage is unavailable"
        ) from exc
    if payload is None:
        raise HTTPException(status_code=404, detail="Activity shadow result is unavailable")
    return payload
