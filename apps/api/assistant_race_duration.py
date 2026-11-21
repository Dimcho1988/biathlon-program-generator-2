"""Unsaved race-duration preview from the canonical resolver and saved curve.

Only the small race preview is calculated here. The costly speed model remains
worker-owned; a missing prepared curve never triggers synchronous model work.
"""
from copy import deepcopy

from fastapi import HTTPException
from pydantic import ValidationError

from . import prepared_views, race_duration
from .management_schemas import RaceDurationRequest


def _pick(value, fields):
    return {key: deepcopy(item) for key, item in value.items() if key in fields} if isinstance(value, dict) else {}


def _plan_preview(value):
    result = _pick(value, {"source", "duration_min", "duration_range_min", "distance_m", "sport", "reason", "approximate", "duration_basis", "method"})
    reference = value.get("specific_reference")
    if isinstance(reference, dict):
        shown = _pick(reference, {"version", "status", "reason", "discipline", "sport", "duration_s", "duration_range_s", "speed_kmh", "dose_from_maximum_time", "speed_role", "initial_heuristic", "calibration_eligible"})
        for name in ("bands", "dosing_bands"):
            if isinstance(reference.get(name), list):
                shown[name] = [_pick(row, {"role", "label", "speed_kmh", "speed_range_kmh", "pace_seconds_km", "zone"})
                               for row in reference[name] if isinstance(row, dict)]
        result["specific_reference"] = shown
    return result


def read(repository, alias, access, query):
    # The canonical UI exposes this POST preview in the profile editor only.
    if access.get("can_view_plan") is not True or access.get("can_edit_plan") is not True:
        raise HTTPException(403, "Нужно е право за редакция на плановия профил.")
    fields = {name: query[name] for name in ("sport", "discipline", "race_duration_min") if query.get(name) is not None}
    try:
        request = RaceDurationRequest.model_validate(fields, strict=True)
    except ValidationError as exc:
        raise HTTPException(422, "Посочи спорт, дисциплина и допустима резервна продължителност.") from exc
    profile = request.model_dump(mode="json")
    curve = None
    if race_duration.distance_m(request.discipline) is not None:
        try:
            curve = prepared_views.read(repository, alias, "speed", {"sport": request.sport})
        except HTTPException as exc:
            if exc.status_code == 503:
                return {"availability": "PENDING", "reason": "PREPARED_VIEW_PENDING", "view_kind": "speed"}
            raise
    result = race_duration.preview(repository, alias, profile, speed_view=curve)
    if access.get("can_view_recovery") is not True:
        result = _plan_preview(result)
    return {"preview_only": True, "saved": False, "input": profile, "estimate": result,
            "interpretation": "Unsaved estimate for these inputs, not an approved session or a guaranteed race result. Does not update the profile or generate a plan."}
