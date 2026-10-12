"""Read-only method personalization and narrowly scoped, revisioned preferences.

The same scientific resolver serves the catalog and weekly planner. Preference
writes replace only their own profile key; the database RPC retains actor
authorization and compare-and-swap enforcement.
"""
from copy import deepcopy
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from fastapi import HTTPException

from biathlon import method_library, method_registry, planning_schedule
from biathlon.periodization import build_periodization

from . import model_service
from .management_schemas import MethodLibraryPreferences
from .management_store import ManagementStore


def validate_preferences(preferences, profile=None):
    """Validate immutable catalog membership as well as numeric schema bounds."""
    parsed = MethodLibraryPreferences.model_validate(preferences)
    payload = parsed.model_dump(mode="json", exclude_none=True)
    entries = {entry["id"]: entry for entry in method_library.entries()}
    from biathlon.training_methods import resolved_methods
    entries.update({m["id"]: method_registry.coach_card(m) for m in resolved_methods(profile or {})})
    for key, override in payload["overrides"].items():
        entry = entries.get(key)
        if entry is None and key in {f"ONFLOWS-RACE-{role}-{zone}-V1" for role in ("BELOW", "RACE", "ABOVE") for zone in ("Z2", "Z3", "Z4", "Z5")}:
            entry = {"id": key, "prescription": None}
        if entry is None:
            raise HTTPException(422, f"Unknown training method: {key}")
        prescription = entry.get("prescription")
        numeric = set(override) - {"enabled"}
        if numeric and not prescription:
            raise HTTPException(422, f"Method {key} has no executable prescription to override")
        if "speed_multiplier" in numeric and not prescription.get("speed_multiplier"):
            raise HTTPException(422, f"Method {key} has no speed multiplier")
        if "speed_multiplier" in numeric and any("speed_multiplier" in segment for segment in prescription.get("pattern", [])):
            raise HTTPException(422, f"Method {key} requires separate speed multipliers for its component segments")
        if "work_range" in numeric and (prescription.get("work") or {}).get("unit") not in {"seconds", "meters"}:
            raise HTTPException(422, f"Method {key} has no supported work-length unit")
        if "work_range" in numeric and prescription.get("pattern"):
            raise HTTPException(422, f"Method {key} requires separate work lengths for its component segments")
        if "repetitions" in numeric and not prescription.get("repetitions"):
            raise HTTPException(422, f"Method {key} has no repetition range")
        if "repetitions" in numeric and prescription.get("structure") in {"SEQUENCE", "SCALED_SEQUENCE", "PROGRESSIVE_CONTINUOUS"} and override["repetitions"] != [1, 1]:
            raise HTTPException(422, f"Method {key} is one complete sequence; its dose varies without repeating the whole structure")
    return payload


def _current_phase(repository, alias, profile, today):
    if not profile:
        return "GENERAL_PREPARATION", "DEFAULT_PREVIEW"
    reader = getattr(repository, "athlete_planning_calendar", None)
    calendar = (reader(alias) if reader else None) or {"events": []}
    # The repository returns a typed calendar, including typed/date events.
    # Periodization consumes the same JSON payload used by the weekly planner.
    calendar = calendar.to_payload() if hasattr(calendar, "to_payload") else dict(calendar)
    scheduled, _ = planning_schedule.horizon(profile, calendar["events"])
    periods = build_periodization(
        scheduled["program_start"], scheduled["program_end"], calendar["events"],
        reentry_days_override=profile.get("reentry_days"),
        taper_days=profile.get("taper_days", 7), transition_days=profile.get("transition_days", 0),
    )
    phase = next((item["kind"] for item in periods["phases"]
                  if item["start_date"] <= today.isoformat() <= item["end_date"]), None)
    return (phase, "CALENDAR_PREVIEW") if phase else ("GENERAL_PREPARATION", "OUTSIDE_PROGRAMME_PREVIEW")


def _coach_previews(catalog, profile, speed, settings, today, phase):
    """Reuse the weekly engine's capacity calculation, once per compatible card."""
    if not speed or not getattr(settings, "hrmax_bpm", None) or not getattr(settings, "zone_bounds_bpm", None):
        return
    from . import training_plan_engine as engine
    from biathlon.training_methods import resolved_methods
    methods = {m["id"]: m for m in resolved_methods(profile)}
    context = engine._capacity_context(speed, settings)
    for card in catalog["methods"]:
        method = methods.get(card["id"])
        r = card.get("resolution") or {}
        if not method or r.get("status") != "READY":
            continue
        method = {**method, "actual_sport": speed["sport"]}
        cap = engine.capacity_for(method, settings, speed, context, today,
            allow_fallback=profile.get("allow_expert_fallback", True),
            use_model_prior=(profile.get("planning_controls") or {}).get("capacity_policy", "MODEL_WITH_PRIOR") == "MODEL_WITH_PRIOR")
        if cap is None:
            r.update(status="UNAVAILABLE", reason="INDIVIDUAL_METHOD_CAPACITY_REQUIRED")
            continue
        fraction = engine._nominal_fraction(method, method["purpose"], profile, phase, cap, profile.get("planning_controls") or {})
        maximum = min(method["max_work_min"], cap["capacity_minutes"] * fraction) * 60
        minimum = method["min_work_min"] * 60
        if maximum + 1e-6 < minimum:
            r.update(status="UNAVAILABLE", reason="NO_COMPLETE_REPEAT_WITHIN_METHOD_BUDGET")
            continue
        effort = cap.get("effort_profile") or {}
        seconds = effort.get("work_seconds")
        time_range = [seconds, seconds] if seconds else [minimum, max(minimum, maximum)]
        r.update(work_unit="seconds", work_range=time_range, work_duration_range_seconds=time_range,
                 minimum_work_seconds=minimum, maximum_work_seconds=maximum,
                 anchor={"type":"INDIVIDUAL_ZONE", "speed_kmh":cap.get("target_speed_kmh")},
                 message="Лична работна опора от същия изчислител като седмичния план. Крайната доза зависи и от Recovery, наличното време и общия бюджет.")
        velocity = cap.get("target_speed_kmh")
        if velocity:
            r.update(target_speed_kmh=velocity, target_speed_range_kmh=[velocity,velocity])
            r.update(method_library._hr_reference({"target_speed_range_kmh":[velocity,velocity],
                "zone":method["zone"], "work_duration_seconds":time_range[0]}, context[0]))
        elif method["zone"] != "STR" and context[0]:
            i=int(method["zone"][1:])-1
            r.update(hr_reference_range_bpm=list(context[0].bounds[i:i+2]),
                     hr_reference_basis="INDIVIDUAL_ZONE_OBSERVATION_NOT_SPEED_PREDICTION", hr_role="SECONDARY_OBSERVATION")


def view(repository, alias, *, phase=None, now=None):
    stored = ManagementStore(repository).profile(alias)
    profile = deepcopy(stored.get("profile") or {})
    settings = repository.athlete_settings(alias)
    tz = settings.timezone if settings else "UTC"
    today = (now or datetime.now(timezone.utc)).astimezone(ZoneInfo(tz)).date()
    selected_phase, phase_source = (phase, "SELECTED_PREVIEW") if phase else _current_phase(repository, alias, profile, today)
    sport = profile.get("actual_sport") or profile.get("sport")
    speed = model_service.speed_view(repository, alias, sport) if settings and sport else None
    preferences = MethodLibraryPreferences.model_validate(profile.get("method_library") or {}).model_dump(mode="json", exclude_none=True)
    if profile:
        from datetime import timedelta
        from . import method_progression_service
        reader = getattr(repository, "active_planning_calendar", None) or getattr(repository, "active_activity_calendar", None)
        if reader:
            envelope = reader(alias, today-timedelta(days=89), today) or {}
            source = (envelope.get("snapshot_payload") or {}).get("load_history") or {}
            profile["_method_progression"] = method_progression_service.context(repository, alias, source, today)
    # A GET must never create a profile or silently save normalized preferences.
    catalog = method_registry.catalog(profile, speed, phase=selected_phase, today=today)
    _coach_previews(catalog, profile, speed, settings, today, selected_phase)
    curve = {key: speed.get(key) for key in (
        "status", "sport", "model_version", "source_generation_id", "source_revision", "active_test_count",
    )} if speed else {"status": "UNAVAILABLE", "sport": sport,
                     "reason": "PROFILE_REQUIRED" if not profile else "ATHLETE_SETTINGS_REQUIRED"}
    return {**catalog, "configured": stored["configured"], "revision": stored["revision"],
            "preferences": preferences,
            "profile": {key: profile.get(key) for key in ("sport", "actual_sport", "discipline")},
            "phase": selected_phase, "phase_source": phase_source, "today": today.isoformat(),
            "timezone": tz, "curve": curve}


def save_preferences(repository, alias, body, actor):
    store = ManagementStore(repository)
    stored = store.profile(alias)
    if not stored["configured"] or not stored.get("profile"):
        raise HTTPException(409, "Create a planning profile before saving method preferences")
    if stored["revision"] != body.expected_revision:
        raise HTTPException(409, "Planning input changed; reload before saving")
    preferences = validate_preferences(body.preferences.model_dump(mode="json", exclude_none=True), stored["profile"])
    profile = deepcopy(stored["profile"])
    profile["method_library"] = preferences
    saved = store.save_profile(alias, profile, body.expected_revision, actor)
    return {"configured": True, "revision": saved["revision"], "preferences": preferences}
