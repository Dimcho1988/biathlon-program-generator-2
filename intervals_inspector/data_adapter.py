"""Field-level Intervals.icu to onFlows mapping for the pilot.

This module is deliberately pure: it receives deidentified field inventories,
never OAuth credentials or Streamlit state.  It reports which internal inputs
can be mapped from the fields that a real athlete's API responses exposed.
Actual model execution remains a later, separately gated shadow-mode step.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import re
from typing import Any


MAX_SHADOW_PERIOD_DAYS = 90
_NORMALISE_FIELD_RE = re.compile(r"[^a-z0-9]+")


@dataclass(frozen=True)
class SourceRequirement:
    group: str
    alternatives: tuple[str, ...]


@dataclass(frozen=True)
class MappingSpec:
    target_field: str
    requirements: tuple[SourceRequirement, ...]
    mapping_kind: str
    consumers: tuple[str, ...]
    note: str


def _require(group: str, *alternatives: str) -> SourceRequirement:
    return SourceRequirement(group=group, alternatives=tuple(alternatives))


MAPPING_SPECS: tuple[MappingSpec, ...] = (
    MappingSpec(
        "athlete_id",
        (_require("oauth", "athlete_id"),),
        "direct",
        ("identity", "all_models"),
        "OAuth athlete ID; never included in technical exports.",
    ),
    MappingSpec(
        "activity_id",
        (_require("activities", "id"),),
        "direct",
        ("activity_metadata",),
        "Intervals activity ID stays session-local.",
    ),
    MappingSpec(
        "date",
        (_require("activities", "start_date_local", "start_date"),),
        "derived",
        ("activity_metadata", "load_model"),
        "ISO date extracted from the activity start timestamp.",
    ),
    MappingSpec(
        "start_time",
        (_require("activities", "start_date_local", "start_date"),),
        "direct",
        ("activity_metadata",),
        "Original timestamp is normalized without changing the instant.",
    ),
    MappingSpec(
        "sport",
        (_require("activities", "type", "sub_type"),),
        "direct",
        ("activity_metadata", "zone_selection"),
        "Provider sport label must be mapped explicitly to an onFlows sport.",
    ),
    MappingSpec(
        "moving_min",
        (_require("activities", "moving_time"),),
        "derived",
        ("activity_metadata", "volume"),
        "Intervals seconds divided by 60.",
    ),
    MappingSpec(
        "hr",
        (_require("streams", "heartrate", "fixed_heartrate"),),
        "direct",
        ("hr_zoning",),
        "The selected activity stream must be aligned to the time stream.",
    ),
    MappingSpec(
        "offset_sec",
        (_require("streams", "time", "elapsed_time"),),
        "direct",
        ("hr_zoning",),
        "Required for validation and 1 Hz alignment.",
    ),
    MappingSpec(
        "moving",
        (_require("streams", "moving", "velocity_smooth"),),
        "derived",
        ("hr_zoning",),
        "Prefer the moving stream; speed may be used only with an explicit rule.",
    ),
    MappingSpec(
        "hr_zones",
        (_require("sport_settings", "hr_zones"),),
        "derived",
        ("hr_zoning", "load_model"),
        "Sport-specific boundaries require semantic validation before use.",
    ),
    MappingSpec(
        "real_Z1..real_Z5",
        (
            _require("streams", "heartrate", "fixed_heartrate"),
            _require("streams", "time", "elapsed_time"),
            _require("streams", "moving", "velocity_smooth"),
            _require("sport_settings", "hr_zones"),
        ),
        "derived",
        ("load_model",),
        "Derived from 1 Hz moving HR samples and the matching sport zones.",
    ),
    MappingSpec(
        "sleep_quality",
        (_require("wellness", "sleepQuality", "sleepScore"),),
        "direct",
        ("wellness_model",),
        "Provider scale must be validated before numerical scoring.",
    ),
    MappingSpec(
        "fatigue",
        (_require("wellness", "fatigue"),),
        "direct",
        ("wellness_model",),
        "Provider scale must be validated before numerical scoring.",
    ),
    MappingSpec(
        "soreness_legs",
        (_require("manual", "soreness_legs"),),
        "manual",
        ("wellness_model",),
        "Generic Intervals soreness cannot safely be split into body regions.",
    ),
    MappingSpec(
        "soreness_upper",
        (_require("manual", "soreness_upper"),),
        "manual",
        ("wellness_model",),
        "Generic Intervals soreness cannot safely be split into body regions.",
    ),
    MappingSpec(
        "stress",
        (_require("wellness", "stress"),),
        "direct",
        ("wellness_model",),
        "Provider scale must be validated before numerical scoring.",
    ),
    MappingSpec(
        "motivation",
        (_require("wellness", "motivation"),),
        "direct",
        ("wellness_model",),
        "Provider scale must be validated before numerical scoring.",
    ),
    MappingSpec(
        "pain",
        (_require("manual", "pain"),),
        "manual",
        ("wellness_model", "safety_flags"),
        "No confirmed equivalent; never infer it from comments or injury text.",
    ),
    MappingSpec(
        "illness",
        (_require("manual", "illness"),),
        "manual",
        ("wellness_model", "safety_flags"),
        "No confirmed equivalent; missing values remain null.",
    ),
    MappingSpec(
        "morning_hr",
        (_require("wellness", "restingHR"),),
        "direct",
        ("wellness_model",),
        "Mapped from restingHR after unit/range validation.",
    ),
    MappingSpec(
        "hrv",
        (_require("wellness", "hrv"),),
        "direct",
        ("wellness_model",),
        "Mapped only after confirming the HRV metric and unit.",
    ),
    MappingSpec(
        "sleep_hours",
        (_require("wellness", "sleepSecs"),),
        "derived",
        ("wellness_model",),
        "Intervals seconds divided by 3600.",
    ),
    MappingSpec(
        "event_id",
        (_require("calendar", "id"),),
        "direct",
        ("calendar_display",),
        "Provider event ID stays session-local.",
    ),
    MappingSpec(
        "event_name",
        (_require("calendar", "name"),),
        "direct",
        ("calendar_display",),
        "Displayed in the private session but excluded from technical exports.",
    ),
    MappingSpec(
        "event_start",
        (_require("calendar", "start_date_local"),),
        "direct",
        ("calendar_display",),
        "Calendar start timestamp.",
    ),
    MappingSpec(
        "event_end",
        (_require("calendar", "end_date_local"),),
        "direct",
        ("calendar_display",),
        "Calendar end timestamp.",
    ),
    MappingSpec(
        "event_semantic_type",
        (_require("manual", "event_semantic_type"),),
        "manual",
        ("planning_model",),
        "MAIN_RACE/CAMP/TEST/UNAVAILABLE must not be guessed from provider text.",
    ),
    MappingSpec(
        "test_protocol",
        (_require("manual", "test_protocol"),),
        "manual",
        ("testing_model",),
        "Requires an explicit onFlows protocol and comparability metadata.",
    ),
    MappingSpec(
        "strength_subtype",
        (_require("manual", "strength_subtype"),),
        "manual",
        ("strength_model",),
        "Intervals activity type does not identify the four onFlows subtypes.",
    ),
    MappingSpec(
        "planning_preferences",
        (_require("manual", "planning_preferences"),),
        "manual",
        ("planning_model",),
        "Requires explicit athlete goals, availability and annual targets.",
    ),
)


def validate_shadow_period(period_days: int) -> int:
    """Validate a bounded future shadow-model history window."""

    if isinstance(period_days, bool) or not isinstance(period_days, int):
        raise ValueError("period_days must be an integer")
    if not 1 <= period_days <= MAX_SHADOW_PERIOD_DAYS:
        raise ValueError(
            f"period_days must be between 1 and {MAX_SHADOW_PERIOD_DAYS}"
        )
    return period_days


def _normalised_field(value: Any) -> str:
    return _NORMALISE_FIELD_RE.sub("", str(value).casefold())


def _available_fields(
    coverage: Mapping[str, Sequence[Mapping[str, Any]]],
    stream_summary: Sequence[Mapping[str, Any]],
) -> dict[str, set[str]]:
    available: dict[str, set[str]] = {
        "oauth": {_normalised_field("athlete_id")},
        "manual": set(),
    }
    for group, rows in coverage.items():
        group_fields = available.setdefault(str(group), set())
        for row in rows:
            non_empty = row.get("non_empty_records")
            value_types = {
                item.strip().casefold()
                for item in str(row.get("value_types", "")).split(",")
                if item.strip()
            }
            if (
                not isinstance(non_empty, int)
                or isinstance(non_empty, bool)
                or non_empty <= 0
                or not value_types
                or value_types == {"null"}
            ):
                continue
            path = str(row.get("json_path", ""))
            if not path:
                continue
            group_fields.add(_normalised_field(path))
            leaf = path.replace("[]", "").rsplit(".", 1)[-1]
            group_fields.add(_normalised_field(leaf))

    stream_fields = available.setdefault("streams", set())
    for row in stream_summary:
        total_points = row.get("total_points")
        if (
            not isinstance(total_points, int)
            or isinstance(total_points, bool)
            or total_points <= 0
        ):
            continue
        name = row.get("stream_name")
        if name not in (None, ""):
            stream_fields.add(_normalised_field(name))
    return available


def build_mapping_report(
    coverage: Mapping[str, Sequence[Mapping[str, Any]]],
    stream_summary: Sequence[Mapping[str, Any]] = (),
) -> list[dict[str, Any]]:
    """Return value-free mapping diagnostics for the fields actually seen."""

    available = _available_fields(coverage, stream_summary)
    rows: list[dict[str, Any]] = []
    for spec in MAPPING_SPECS:
        matched: list[str] = []
        missing_groups: list[str] = []
        for requirement in spec.requirements:
            group_fields = available.get(requirement.group, set())
            selected = next(
                (
                    candidate
                    for candidate in requirement.alternatives
                    if _normalised_field(candidate) in group_fields
                ),
                None,
            )
            if selected is None:
                missing_groups.append(
                    f"{requirement.group}:"
                    + "|".join(requirement.alternatives)
                )
            else:
                matched.append(f"{requirement.group}:{selected}")

        status = spec.mapping_kind if not missing_groups else "missing"
        rows.append(
            {
                "target_field": spec.target_field,
                "status": status,
                "matched_source_fields": ", ".join(matched),
                "missing_source_fields": ", ".join(missing_groups),
                "model_consumers": ", ".join(spec.consumers),
                "note": spec.note,
            }
        )
    return rows


def build_model_readiness(
    mapping_rows: Sequence[Mapping[str, Any]],
) -> list[dict[str, str]]:
    """Summarize which unchanged onFlows model stages can be used safely."""

    statuses = {
        str(row.get("target_field")): str(row.get("status"))
        for row in mapping_rows
    }

    def present(*fields: str) -> bool:
        return all(statuses.get(field, "missing") != "missing" for field in fields)

    def any_present(*fields: str) -> bool:
        return any(statuses.get(field, "missing") != "missing" for field in fields)

    load_ready = present("real_Z1..real_Z5", "date")
    wellness_fields = (
        "sleep_quality",
        "fatigue",
        "soreness_legs",
        "soreness_upper",
        "stress",
        "motivation",
        "pain",
        "morning_hr",
        "hrv",
        "sleep_hours",
    )
    wellness_partial = any_present(*wellness_fields)

    return [
        {
            "model": "Identity/profile display",
            "readiness": (
                "ready" if present("athlete_id") else "blocked"
            ),
            "missing_or_limit": (
                "OAuth identity is session-local and is never exported."
            ),
        },
        {
            "model": "Activity metadata",
            "readiness": (
                "partial"
                if present("activity_id", "date", "sport", "moving_min")
                else "blocked"
            ),
            "missing_or_limit": (
                "The schema is mapped, but the value adapter and validation "
                "are not implemented in this stage."
                if present("activity_id", "date", "sport", "moving_min")
                else "One or more activity identity/date/duration fields are absent."
            ),
        },
        {
            "model": "HR zoning → Q/E → 7/40/Tref/load readiness",
            "readiness": "partial" if load_ready else "blocked",
            "missing_or_limit": (
                "Requires a selected activity with time, HR, moving signal and "
                "matching sport HR zones."
                if not load_ready
                else "Schema candidates exist, but values/units, 1 Hz alignment, "
                "zone-profile coefficients and bounded multi-activity history "
                "must still be normalized."
            ),
        },
        {
            "model": "Wellness and integrated readiness",
            "readiness": (
                "partial" if wellness_partial or load_ready else "blocked"
            ),
            "missing_or_limit": (
                "Generic soreness cannot be split; pain/illness and scale "
                "validation are still required."
                if not present(*wellness_fields)
                else "Wellness inputs are present; safety validation is still gated."
            ),
        },
        {
            "model": "Season/annual volume",
            "readiness": (
                "partial" if present("date", "moving_min") else "blocked"
            ),
            "missing_or_limit": (
                "The pilot is capped at 90 days, so a full season and annual "
                "target are not available."
            ),
        },
        {
            "model": "Calendar display",
            "readiness": (
                "partial"
                if present("event_id", "event_start", "event_name")
                else "blocked"
            ),
            "missing_or_limit": (
                "The structural schema is mapped; values and event semantics "
                "are not normalized or inferred in this stage."
            ),
        },
        {
            "model": "Tests, strength and weekly planning",
            "readiness": "blocked",
            "missing_or_limit": (
                "Explicit test protocols, strength subtype, semantic calendar "
                "types and athlete planning preferences are missing."
            ),
        },
    ]
