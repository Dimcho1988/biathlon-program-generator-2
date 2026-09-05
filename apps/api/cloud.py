"""Cloud-pilot boundaries: configuration, auth, wellness and snapshots.

This module contains no provider credentials or physiology formulae.  The
single-profile implementation is deliberately an adapter around an
``AthleteContext`` and replaceable repository protocol.
"""
from __future__ import annotations

from dataclasses import dataclass
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
import hashlib
import hmac
import json
import math
import re
from threading import RLock
from typing import Any, Mapping, Protocol
import uuid
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


@dataclass(frozen=True)
class AthleteModelSettings:
    """Athlete-specific inputs; shared model versions remain service-wide."""

    zone_bounds_bpm: tuple[int, int, int, int, int, int]
    timezone: str
    hrmax_bpm: int | None = None

    def validate(self) -> "AthleteModelSettings":
        if len(self.zone_bounds_bpm) != 6 or any(
            not 30 <= value <= 240 for value in self.zone_bounds_bpm
        ) or any(
            left >= right
            for left, right in zip(self.zone_bounds_bpm, self.zone_bounds_bpm[1:])
        ):
            raise ValueError("six strictly increasing HR boundaries are required")
        try:
            ZoneInfo(self.timezone)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError("a valid IANA timezone is required") from exc
        if self.hrmax_bpm is not None:
            if not 30 <= self.hrmax_bpm <= 240:
                raise ValueError("explicit HRmax must be between 30 and 240 bpm")
            if self.zone_bounds_bpm[-1] > self.hrmax_bpm:
                raise ValueError("HR zones cannot exceed explicit HRmax")
        return self


@dataclass(frozen=True)
class AthletePlanningProfile:
    """Athlete-owned planning choices; shared scientific limits stay in code."""

    season_start: date
    season_end: date
    annual_target_hours: float
    sessions_per_week: int
    rest_days: tuple[int, ...]
    double_session_days: tuple[int, ...]
    long_session_day: int
    intensity_days: tuple[int, ...]
    strength_days: tuple[int, ...]
    max_key_sessions_per_week: int
    mesocycle_anchor_date: date
    mesocycle_length_weeks: int
    camp_default_accent_limit: int
    double_threshold_enabled: bool
    double_threshold_day: int
    double_threshold_components: tuple[str, ...]
    schema_version: str = "planning-profile-v1"

    def validate(self) -> "AthletePlanningProfile":
        if self.schema_version != "planning-profile-v1":
            raise ValueError("unsupported planning profile version")
        if self.season_end <= self.season_start:
            raise ValueError("season end must be after season start")
        if not 50.0 <= self.annual_target_hours <= 1500.0:
            raise ValueError("annual target must be between 50 and 1500 hours")

        weekday_groups = (
            self.rest_days,
            self.double_session_days,
            self.intensity_days,
            self.strength_days,
        )
        if any(
            len(group) != len(set(group))
            or any(day < 0 or day > 6 for day in group)
            for group in weekday_groups
        ):
            raise ValueError("weekday lists must contain unique values from 0 to 6")
        if len(self.rest_days) >= 7:
            raise ValueError("at least one active weekday is required")
        if set(self.rest_days) & set(self.double_session_days):
            raise ValueError("double-session days cannot be rest days")
        max_sessions = (7 - len(self.rest_days)) + len(
            self.double_session_days
        )
        if not 1 <= self.sessions_per_week <= max_sessions:
            raise ValueError("session count exceeds the available training days")
        if not 0 <= self.long_session_day <= 6:
            raise ValueError("long-session weekday must be between 0 and 6")
        if not 0 <= self.max_key_sessions_per_week <= 8:
            raise ValueError("maximum key sessions must be between 0 and 8")
        if not 2 <= self.mesocycle_length_weeks <= 6:
            raise ValueError("mesocycle length must be between 2 and 6 weeks")
        if not 1 <= self.camp_default_accent_limit <= 6:
            raise ValueError("camp accent limit must be between 1 and 6")
        if not 0 <= self.double_threshold_day <= 6:
            raise ValueError("double-threshold weekday must be between 0 and 6")
        if (
            not self.double_threshold_components
            or len(self.double_threshold_components)
            != len(set(self.double_threshold_components))
            or any(component not in {"Z3", "Z4"} for component in self.double_threshold_components)
        ):
            raise ValueError("double-threshold components must be Z3 and/or Z4")
        if self.double_threshold_enabled:
            if self.double_threshold_day in self.rest_days:
                raise ValueError("double-threshold day cannot be a rest day")
            if self.double_threshold_day not in self.double_session_days:
                raise ValueError(
                    "double-threshold day must allow a double session"
                )
            if self.max_key_sessions_per_week < 2:
                raise ValueError("double threshold requires at least two key sessions")
        return self

    def to_payload(self) -> dict[str, Any]:
        self.validate()
        return {
            "schema_version": self.schema_version,
            "season_start": self.season_start.isoformat(),
            "season_end": self.season_end.isoformat(),
            "annual_target_hours": self.annual_target_hours,
            "sessions_per_week": self.sessions_per_week,
            "rest_days": list(self.rest_days),
            "double_session_days": list(self.double_session_days),
            "long_session_day": self.long_session_day,
            "intensity_days": list(self.intensity_days),
            "strength_days": list(self.strength_days),
            "max_key_sessions_per_week": self.max_key_sessions_per_week,
            "mesocycle_anchor_date": self.mesocycle_anchor_date.isoformat(),
            "mesocycle_length_weeks": self.mesocycle_length_weeks,
            "camp_default_accent_limit": self.camp_default_accent_limit,
            "double_threshold_enabled": self.double_threshold_enabled,
            "double_threshold_day": self.double_threshold_day,
            "double_threshold_components": list(self.double_threshold_components),
        }

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "AthletePlanningProfile":
        expected = {
            "schema_version",
            "season_start",
            "season_end",
            "annual_target_hours",
            "sessions_per_week",
            "rest_days",
            "double_session_days",
            "long_session_day",
            "intensity_days",
            "strength_days",
            "max_key_sessions_per_week",
            "mesocycle_anchor_date",
            "mesocycle_length_weeks",
            "camp_default_accent_limit",
            "double_threshold_enabled",
            "double_threshold_day",
            "double_threshold_components",
        }
        if set(payload) != expected:
            raise ValueError("planning profile fields are invalid")
        integer_fields = {
            "sessions_per_week",
            "long_session_day",
            "max_key_sessions_per_week",
            "mesocycle_length_weeks",
            "camp_default_accent_limit",
            "double_threshold_day",
        }
        weekday_fields = {
            "rest_days",
            "double_session_days",
            "intensity_days",
            "strength_days",
        }
        if any(
            not isinstance(payload[field], int)
            or isinstance(payload[field], bool)
            for field in integer_fields
        ) or any(
            not isinstance(payload[field], list)
            or any(
                not isinstance(day, int) or isinstance(day, bool)
                for day in payload[field]
            )
            for field in weekday_fields
        ):
            raise ValueError("planning profile fields are invalid")
        if (
            not isinstance(payload["annual_target_hours"], (int, float))
            or isinstance(payload["annual_target_hours"], bool)
            or not isinstance(payload["double_threshold_enabled"], bool)
            or not isinstance(payload["double_threshold_components"], list)
            or any(
                not isinstance(component, str)
                for component in payload["double_threshold_components"]
            )
            or not isinstance(payload["season_start"], str)
            or not isinstance(payload["season_end"], str)
            or not isinstance(payload["mesocycle_anchor_date"], str)
        ):
            raise ValueError("planning profile fields are invalid")
        try:
            profile = cls(
                schema_version=str(payload["schema_version"]),
                season_start=date.fromisoformat(str(payload["season_start"])),
                season_end=date.fromisoformat(str(payload["season_end"])),
                annual_target_hours=float(payload["annual_target_hours"]),
                sessions_per_week=int(payload["sessions_per_week"]),
                rest_days=tuple(int(day) for day in payload["rest_days"]),
                double_session_days=tuple(
                    int(day) for day in payload["double_session_days"]
                ),
                long_session_day=int(payload["long_session_day"]),
                intensity_days=tuple(int(day) for day in payload["intensity_days"]),
                strength_days=tuple(int(day) for day in payload["strength_days"]),
                max_key_sessions_per_week=int(payload["max_key_sessions_per_week"]),
                mesocycle_anchor_date=date.fromisoformat(
                    str(payload["mesocycle_anchor_date"])
                ),
                mesocycle_length_weeks=int(payload["mesocycle_length_weeks"]),
                camp_default_accent_limit=int(payload["camp_default_accent_limit"]),
                double_threshold_enabled=payload["double_threshold_enabled"],
                double_threshold_day=int(payload["double_threshold_day"]),
                double_threshold_components=tuple(
                    str(component) for component in payload["double_threshold_components"]
                ),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("planning profile fields are invalid") from exc
        return profile.validate()


MESOCYCLE_ACCENT_COMPONENTS = ("Z1", "Z2", "Z3", "Z4", "Z5", "STR")
MESOCYCLE_ACCENT_MODES = ("AUTO", "MANUAL", "HYBRID")


@dataclass(frozen=True)
class AthleteMesocycleAccentPreferences:
    """Athlete-owned accent choices; scientific factors remain service-wide."""

    accent_mode: str
    accent_limit: int
    manual_components: tuple[str, ...]
    schema_version: str = "mesocycle-accent-preferences-v1"

    def validate(self) -> "AthleteMesocycleAccentPreferences":
        if self.schema_version != "mesocycle-accent-preferences-v1":
            raise ValueError("unsupported mesocycle accent preferences version")
        if self.accent_mode not in MESOCYCLE_ACCENT_MODES:
            raise ValueError("unsupported mesocycle accent mode")
        if not 1 <= self.accent_limit <= len(MESOCYCLE_ACCENT_COMPONENTS):
            raise ValueError("mesocycle accent limit must be between 1 and 6")
        if (
            len(self.manual_components) != len(set(self.manual_components))
            or any(
                component not in MESOCYCLE_ACCENT_COMPONENTS
                for component in self.manual_components
            )
        ):
            raise ValueError("manual mesocycle accents must be unique components")
        if len(self.manual_components) > self.accent_limit:
            raise ValueError("manual mesocycle accents exceed the accent limit")
        if self.accent_mode == "AUTO" and self.manual_components:
            raise ValueError("automatic mesocycle accents cannot contain manual choices")
        if self.accent_mode in {"MANUAL", "HYBRID"} and not self.manual_components:
            raise ValueError("manual and hybrid modes require a manual component")
        return self

    @property
    def canonical_manual_components(self) -> tuple[str, ...]:
        selected = set(self.manual_components)
        return tuple(
            component
            for component in MESOCYCLE_ACCENT_COMPONENTS
            if component in selected
        )

    def to_payload(self) -> dict[str, Any]:
        self.validate()
        return {
            "schema_version": self.schema_version,
            "accent_mode": self.accent_mode,
            "accent_limit": self.accent_limit,
            "manual_components": list(self.canonical_manual_components),
        }

    def resolution_preview(self) -> dict[str, Any]:
        """Describe fixed choices and deferred dynamic slots without guessing them."""

        self.validate()
        fixed = (
            ()
            if self.accent_mode == "AUTO"
            else self.canonical_manual_components
        )
        automatic_slots = (
            self.accent_limit
            if self.accent_mode == "AUTO"
            else 0
            if self.accent_mode == "MANUAL"
            else self.accent_limit - len(fixed)
        )
        return {
            "fixed_components": list(fixed),
            "automatic_slots": automatic_slots,
            "resolution_stage": "PLAN_GENERATION",
        }

    @classmethod
    def from_mapping(
        cls, payload: Mapping[str, Any]
    ) -> "AthleteMesocycleAccentPreferences":
        expected = {
            "schema_version",
            "accent_mode",
            "accent_limit",
            "manual_components",
        }
        if set(payload) != expected:
            raise ValueError("mesocycle accent preference fields are invalid")
        if (
            not isinstance(payload["schema_version"], str)
            or not isinstance(payload["accent_mode"], str)
            or not isinstance(payload["accent_limit"], int)
            or isinstance(payload["accent_limit"], bool)
            or not isinstance(payload["manual_components"], list)
            or any(
                not isinstance(component, str)
                for component in payload["manual_components"]
            )
        ):
            raise ValueError("mesocycle accent preference fields are invalid")
        selected = set(payload["manual_components"])
        if (
            len(selected) != len(payload["manual_components"])
            or any(
                component not in MESOCYCLE_ACCENT_COMPONENTS
                for component in selected
            )
        ):
            raise ValueError("mesocycle accent preference fields are invalid")
        preferences = cls(
            schema_version=payload["schema_version"],
            accent_mode=payload["accent_mode"],
            accent_limit=payload["accent_limit"],
            manual_components=tuple(
                component
                for component in MESOCYCLE_ACCENT_COMPONENTS
                if component in selected
            ),
        )
        return preferences.validate()


PLANNING_CALENDAR_EVENT_TYPES = (
    "MAIN_RACE",
    "CONTROL_RACE",
    "CAMP",
    "TEST",
    "UNAVAILABLE",
)
PLANNING_CALENDAR_EVENT_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{7,63}$")


@dataclass(frozen=True)
class AthletePlanningCalendarEvent:
    """A coach-owned calendar boundary used later by the plan generator."""

    event_id: str
    event_type: str
    name: str
    start_date: date
    end_date: date

    def validate(self) -> "AthletePlanningCalendarEvent":
        if not PLANNING_CALENDAR_EVENT_ID_PATTERN.fullmatch(self.event_id):
            raise ValueError("planning calendar event id is invalid")
        if self.event_type not in PLANNING_CALENDAR_EVENT_TYPES:
            raise ValueError("planning calendar event type is invalid")
        if (
            not self.name
            or self.name.strip() != self.name
            or len(self.name) > 120
            or any(ord(character) < 32 for character in self.name)
        ):
            raise ValueError("planning calendar event name is invalid")
        if self.end_date < self.start_date:
            raise ValueError("planning calendar event end precedes its start")
        return self

    def to_payload(self) -> dict[str, str]:
        self.validate()
        return {
            "event_id": self.event_id,
            "event_type": self.event_type,
            "name": self.name,
            "start_date": self.start_date.isoformat(),
            "end_date": self.end_date.isoformat(),
        }

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "AthletePlanningCalendarEvent":
        if set(payload) != {
            "event_id",
            "event_type",
            "name",
            "start_date",
            "end_date",
        } or any(not isinstance(payload[field], str) for field in payload):
            raise ValueError("planning calendar event fields are invalid")
        try:
            event = cls(
                event_id=payload["event_id"],
                event_type=payload["event_type"],
                name=payload["name"],
                start_date=date.fromisoformat(payload["start_date"]),
                end_date=date.fromisoformat(payload["end_date"]),
            )
        except (TypeError, ValueError) as exc:
            raise ValueError("planning calendar event fields are invalid") from exc
        return event.validate()


@dataclass(frozen=True)
class AthletePlanningCalendar:
    """Versioned, athlete-scoped planning calendar without inferred events."""

    events: tuple[AthletePlanningCalendarEvent, ...]
    schema_version: str = "planning-calendar-v1"

    def validate(self) -> "AthletePlanningCalendar":
        if self.schema_version != "planning-calendar-v1":
            raise ValueError("unsupported planning calendar version")
        if len(self.events) > 100:
            raise ValueError("planning calendar supports at most 100 events")
        validated = tuple(event.validate() for event in self.events)
        identifiers = tuple(event.event_id for event in validated)
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("planning calendar event ids must be unique")
        return self

    @property
    def canonical_events(self) -> tuple[AthletePlanningCalendarEvent, ...]:
        self.validate()
        return tuple(
            sorted(
                self.events,
                key=lambda event: (
                    event.start_date,
                    event.end_date,
                    event.event_type,
                    event.event_id,
                ),
            )
        )

    def to_payload(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "events": [event.to_payload() for event in self.canonical_events],
        }

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "AthletePlanningCalendar":
        if set(payload) != {"schema_version", "events"}:
            raise ValueError("planning calendar fields are invalid")
        if not isinstance(payload["schema_version"], str) or not isinstance(
            payload["events"], list
        ) or any(not isinstance(event, Mapping) for event in payload["events"]):
            raise ValueError("planning calendar fields are invalid")
        calendar = cls(
            schema_version=payload["schema_version"],
            events=tuple(
                AthletePlanningCalendarEvent.from_mapping(event)
                for event in payload["events"]
            ),
        )
        return calendar.validate()


def planning_generation_context(
    *,
    calendar: AthletePlanningCalendar | None,
    profile: AthletePlanningProfile | None,
    accent_preferences: AthleteMesocycleAccentPreferences | None,
    training_snapshot: Mapping[str, Any] | None,
    as_of: date,
) -> dict[str, Any]:
    """Report required planning inputs without generating or guessing a plan."""

    future_main_events = (
        [
            event
            for event in calendar.canonical_events
            if event.event_type == "MAIN_RACE" and event.start_date >= as_of
        ]
        if calendar is not None
        else []
    )
    missing_inputs = []
    if profile is None:
        missing_inputs.append("PLANNING_PROFILE")
    if accent_preferences is None:
        missing_inputs.append("MESOCYCLE_ACCENTS")
    if not future_main_events:
        missing_inputs.append("FUTURE_MAIN_RACE")
    if training_snapshot is None:
        missing_inputs.append("TRAINING_SNAPSHOT")
    next_main_race = future_main_events[0] if future_main_events else None
    return {
        "schema_version": "planning-context-v1",
        "as_of": as_of.isoformat(),
        "ready_for_generation": not missing_inputs,
        "generator_status": "NOT_ACTIVE",
        "missing_inputs": missing_inputs,
        "next_main_race": (
            next_main_race.to_payload() if next_main_race is not None else None
        ),
        "methodology_version": "onflows-canonical-v1",
        "recovery_basis": "LOAD_ONLY",
        "wellness_integration": "DIAGNOSTIC_ONLY",
    }


CANONICAL_TREF_PROFILE_VERSION = "tref-bounded-40d-expert-v1"


@dataclass(frozen=True)
class AthleteContext:
    public_alias: str
    provider_athlete_id: str
    zone_bounds_bpm: tuple[int, int, int, int, int, int]
    timezone: str
    intra_zone_version: str
    tref_version: str
    recovery_parameter_version: str
    hrmax_bpm: int | None = None

    def validate(self) -> "AthleteContext":
        if not self.public_alias or self.public_alias.strip() != self.public_alias:
            raise ValueError("a pseudonymous athlete alias is required")
        if not self.provider_athlete_id:
            raise ValueError("backend provider athlete id is required")
        if len(self.zone_bounds_bpm) != 6 or any(
            a >= b for a, b in zip(self.zone_bounds_bpm, self.zone_bounds_bpm[1:])
        ):
            raise ValueError("six strictly increasing HR boundaries are required")
        if self.intra_zone_version != "intra_zone_linear_v1":
            raise ValueError("unapproved intra-zone version")
        if self.tref_version != CANONICAL_TREF_PROFILE_VERSION:
            raise ValueError("unapproved Tref profile version")
        if not all((self.timezone, self.recovery_parameter_version)):
            raise ValueError("all configuration versions are required")
        if self.hrmax_bpm is not None and (
            not 30 <= self.hrmax_bpm <= 240
            or self.zone_bounds_bpm[-1] > self.hrmax_bpm
        ):
            raise ValueError("explicit HRmax is invalid")
        return self

    @property
    def fingerprint(self) -> str:
        self.validate()
        # Provider identity is intentionally excluded from model configuration.
        payload = {"alias": self.public_alias, "zones": self.zone_bounds_bpm,
                   "timezone": self.timezone, "intra": self.intra_zone_version,
                   "tref": self.tref_version, "recovery": self.recovery_parameter_version,
                   "explicit_hrmax": self.hrmax_bpm}
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def service_token_valid(provided: str | None, expected: str) -> bool:
    """Constant-time validation with identical handling of absent/bad tokens."""
    candidate = provided or ""
    return bool(expected) and hmac.compare_digest(candidate.encode(), expected.encode())


WELLNESS_FIELDS = {
    "weight": (("weight", "weightKg", "weight_kg"), "kg", "number"),
    "steps": (("steps", "stepCount", "step_count"), "count", "number"),
    "sleep_duration": (
        ("sleepSecs", "sleepSeconds", "sleepDuration", "sleep_secs"),
        "s",
        "number",
    ),
    # Intervals can expose both fields.  They are counted separately because
    # their provider semantics and scales are not interchangeable.
    "sleep_score": (("sleepScore", "sleep_score"), "score", "number"),
    "sleep_quality": (("sleepQuality", "sleep_quality"), "score", "number"),
    "resting_hr": (("restingHR", "restingHr", "resting_hr"), "bpm", "number"),
    "average_sleeping_hr": (
        ("avgSleepingHR", "averageSleepingHR", "avg_sleeping_hr"),
        "bpm",
        "number",
    ),
    "hrv": (("hrv", "hrvRMSSD", "hrv_rmssd"), "ms", "number"),
    "hrv_sdnn": (("hrvSDNN", "hrv_sdnn"), "ms", "number"),
    "readiness": (("readiness",), "score", "number"),
    "respiration": (("respiration",), "breaths/min", "number"),
    "spo2": (("spO2",), "%", "number"),
    "fatigue": (("fatigue",), "score", "number"),
    "stress": (("stress",), "score", "number"),
    "mood": (("mood",), "score", "number"),
    "motivation": (("motivation",), "score", "number"),
    "soreness": (("soreness",), "score", "number"),
    "injury": (("injury",), "score", "number"),
    "illness": (("illness",), "boolean", "boolean"),
}

# The recovery coverage metric remains presence-only and keeps its v1
# denominator stable. Separately, selected normalized values are retained for
# the athlete-private calendar; the raw provider rows are never persisted.
# Generic soreness is not silently split into region-specific model inputs.
WELLNESS_COVERAGE_FIELDS = tuple(
    field for field in WELLNESS_FIELDS if field not in {"illness", "weight", "steps"}
)
UNRESOLVED_WELLNESS_MODEL_INPUTS = (
    "soreness_legs",
    "soreness_upper",
    "pain",
    "illness",
)


def _wellness_observed_date(row: Mapping[str, Any]) -> date | None:
    stamp = row.get("id") or row.get("date")
    if not isinstance(stamp, str):
        return None
    try:
        return date.fromisoformat(stamp[:10])
    except ValueError:
        return None


def _wellness_raw_value(
    row: Mapping[str, Any], sources: tuple[str, ...]
) -> Any:
    for source in sources:
        value = row.get(source)
        if value is not None:
            return value
    return None


def wellness_rows_from_payload(payload: Any) -> list[Mapping[str, Any]]:
    """Accept the documented list and bounded provider envelope variants."""
    candidate: Any = payload
    if isinstance(payload, Mapping):
        for key in ("wellness", "data", "items"):
            if isinstance(payload.get(key), list):
                candidate = payload[key]
                break
    if not isinstance(candidate, list):
        return []
    return [row for row in candidate if isinstance(row, Mapping)]


def normalize_wellness(row: Mapping[str, Any], *, now: datetime | None = None) -> dict[str, Any]:
    """Map only unambiguous Intervals fields; missing never becomes neutral."""
    observed_date = _wellness_observed_date(row)
    observed = (
        datetime.combine(observed_date, datetime.min.time(), tzinfo=timezone.utc)
        if observed_date is not None
        else None
    )
    clock = now or datetime.now(timezone.utc)
    values: dict[str, Any] = {}
    invalid: list[str] = []
    for target, (sources, unit, kind) in WELLNESS_FIELDS.items():
        raw = _wellness_raw_value(row, sources)
        if raw is None:
            values[target] = {"value": None, "unit": unit, "state": "missing"}
        elif kind == "boolean" and isinstance(raw, bool):
            values[target] = {"value": raw, "unit": unit, "state": "valid"}
        elif (
            kind == "number"
            and isinstance(raw, (int, float))
            and not isinstance(raw, bool)
            and math.isfinite(float(raw))
        ):
            values[target] = {"value": float(raw), "unit": unit, "state": "valid"}
        else:
            values[target] = {"value": None, "unit": unit, "state": "invalid"}; invalid.append(target)
    age_hours = None if observed is None else max(0.0, (clock - observed).total_seconds() / 3600)
    valid = sum(v["state"] == "valid" for v in values.values())
    return {"source": "intervals", "observed_at": observed.isoformat() if observed else None,
            "freshness": "unknown" if age_hours is None else ("fresh" if age_hours <= 48 else "stale"),
            "coverage": valid / len(values), "values": values,
            "warnings": [f"invalid wellness field: {x}" for x in invalid]}


def daily_wellness_summaries(
    rows: list[Mapping[str, Any]],
    *,
    period_start: date,
    period_end: date,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """Persist display-safe daily values, never the provider wellness payload."""
    by_date: dict[date, dict[str, Any]] = {}
    for row in rows:
        observed_date = _wellness_observed_date(row)
        if observed_date is None or not period_start <= observed_date <= period_end:
            continue
        normalized = normalize_wellness(row, now=now)
        metrics = {
            field: {
                "value": value["value"],
                "unit": value["unit"],
            }
            for field, value in normalized["values"].items()
            if value["state"] == "valid"
        }
        if metrics:
            by_date[observed_date] = {
                "date": observed_date.isoformat(),
                "metrics": metrics,
            }
    return [by_date[day] for day in sorted(by_date)]


def summarize_wellness_coverage(
    rows: list[Mapping[str, Any]],
    *,
    period_start: date,
    period_end: date,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Return privacy-minimized field coverage without scoring wellness.

    Raw values never leave this function.  The result contains distinct-day
    counts only and is deliberately marked as non-canonical for recovery.
    """

    if period_end < period_start:
        raise ValueError("wellness coverage period is invalid")
    calendar_days = (period_end - period_start).days + 1
    present_dates = {field: set() for field in WELLNESS_COVERAGE_FIELDS}
    valid_dates = {field: set() for field in WELLNESS_COVERAGE_FIELDS}
    invalid_dates = {field: set() for field in WELLNESS_COVERAGE_FIELDS}
    recognized_dates: set[date] = set()
    received_records = 0

    for row in rows:
        observed_date = _wellness_observed_date(row)
        if observed_date is None or not period_start <= observed_date <= period_end:
            continue
        received_records += 1
        normalized = normalize_wellness(row, now=now)
        for field in WELLNESS_COVERAGE_FIELDS:
            state = normalized["values"][field]["state"]
            if state == "missing":
                continue
            present_dates[field].add(observed_date)
            recognized_dates.add(observed_date)
            if state == "valid":
                valid_dates[field].add(observed_date)
            else:
                invalid_dates[field].add(observed_date)

    def percentage(numerator: int, denominator: int) -> float:
        return round(100.0 * numerator / denominator, 2) if denominator else 0.0

    field_rows = []
    total_valid_field_days = 0
    for field in WELLNESS_COVERAGE_FIELDS:
        valid = len(valid_dates[field])
        total_valid_field_days += valid
        # Keep the public wellness-coverage-v1 contract on the documented
        # Intervals field while accepting bounded aliases at the input edge.
        sources = [WELLNESS_FIELDS[field][0][0]]
        field_rows.append(
            {
                "field": field,
                "source_fields": sources,
                "present_days": len(present_dates[field]),
                "valid_days": valid,
                "invalid_days": len(invalid_dates[field] - valid_dates[field]),
                "coverage_percent": percentage(valid, calendar_days),
            }
        )

    latest_observed = max(recognized_dates) if recognized_dates else None
    if latest_observed is None:
        freshness = "unknown"
    else:
        observed_at = datetime.combine(
            latest_observed, datetime.min.time(), tzinfo=timezone.utc
        )
        age_hours = max(
            0.0,
            ((now or datetime.now(timezone.utc)) - observed_at).total_seconds()
            / 3600.0,
        )
        freshness = "fresh" if age_hours <= 48 else "stale"

    return {
        "schema_version": "wellness-coverage-v1",
        "period_start": period_start.isoformat(),
        "period_end": period_end.isoformat(),
        "calendar_days": calendar_days,
        "records_received": received_records,
        "days_with_any_recognized_data": len(recognized_dates),
        "daily_presence_percent": percentage(len(recognized_dates), calendar_days),
        "recognized_field_coverage_percent": percentage(
            total_valid_field_days,
            calendar_days * len(WELLNESS_COVERAGE_FIELDS),
        ),
        "latest_observed_date": (
            latest_observed.isoformat() if latest_observed is not None else None
        ),
        "freshness": freshness,
        "fields": field_rows,
        "unresolved_canonical_inputs": list(UNRESOLVED_WELLNESS_MODEL_INPUTS),
        "model_status": "diagnostic-only",
        "affects_recovery": False,
    }


class SnapshotRepository(Protocol):
    def latest(self, athlete_alias: str) -> Mapping[str, Any] | None: ...
    def latest_envelope(self, athlete_alias: str) -> Mapping[str, Any] | None: ...
    def active_analysis(self, athlete_alias: str) -> Mapping[str, Any] | None: ...
    def active_activity_calendar(
        self, athlete_alias: str, period_start: date, period_end: date
    ) -> Mapping[str, Any] | None: ...
    def active_activity_view(
        self, athlete_alias: str, activity_ref: str
    ) -> Mapping[str, Any] | None: ...
    def replace(self, athlete_alias: str, snapshot: Mapping[str, Any]) -> None: ...
    def enqueue_sync_job(
        self,
        *,
        athlete_alias: str,
        job_kind: str,
        idempotency_key: str,
        request_payload: Mapping[str, Any],
    ) -> Mapping[str, Any]: ...
    def sync_state(self, athlete_alias: str) -> Mapping[str, Any]: ...
    def claim_sync_job(
        self, *, worker_id: str, lease_seconds: int = 300
    ) -> Mapping[str, Any] | None: ...
    def renew_sync_lease(
        self,
        *,
        job_id: str,
        generation_id: str,
        lease_token: str,
        lease_seconds: int = 300,
    ) -> bool: ...
    def stage_analysis_generation(
        self,
        *,
        job_id: str,
        generation_id: str,
        lease_token: str,
        snapshot_payload: Mapping[str, Any],
        snapshot_hash: str,
        period_start: date,
        period_end: date,
        as_of: date,
        provenance: Mapping[str, Any],
        activities: list[Mapping[str, Any]],
        inherit_activities: bool = False,
    ) -> Mapping[str, Any]: ...
    def activate_analysis_generation(
        self, *, job_id: str, generation_id: str, lease_token: str
    ) -> Mapping[str, Any]: ...
    def fail_sync_job(
        self,
        *,
        job_id: str,
        generation_id: str,
        lease_token: str,
        error_code: str,
        retryable: bool,
        retry_after_seconds: int | None = None,
    ) -> Mapping[str, Any]: ...
    def rollback_analysis_generation(
        self, *, athlete_alias: str, target_generation_id: str
    ) -> Mapping[str, Any]: ...
    def prune_analysis_generations(
        self,
        *,
        athlete_alias: str,
        keep_superseded: int = 5,
        terminal_older_than_days: int = 30,
        batch_limit: int = 100,
    ) -> Mapping[str, int]: ...
    def publish_activity_shadow(
        self,
        *,
        athlete_alias: str,
        activity_ref: str,
        input_payload: Mapping[str, Any],
        derived_payload: Mapping[str, Any],
    ) -> str: ...
    def publish_canonical_activity_result(
        self,
        *,
        athlete_alias: str,
        activity_ref: str,
        scientific_input_hash: str,
        result_payload: Mapping[str, Any],
    ) -> str: ...
    def store_canonical_activity_result(
        self,
        *,
        athlete_alias: str,
        activity_ref: str,
        scientific_input_hash: str,
        result_payload: Mapping[str, Any],
    ) -> str: ...
    def activity_shadow(
        self, athlete_alias: str, activity_ref: str
    ) -> Mapping[str, Any] | None: ...
    def activity_shadow_index(
        self, athlete_alias: str
    ) -> tuple[Mapping[str, Any], ...]: ...
    def activity_shadow_zone_summaries(
        self, athlete_alias: str, activity_refs: tuple[str, ...]
    ) -> Mapping[str, list[Mapping[str, Any]]]: ...
    def resolve_activity_ref(
        self, athlete_alias: str, provider_activity_key: str
    ) -> str: ...
    def upsert_activity_catalog(
        self, athlete_alias: str, activities: list[Mapping[str, Any]]
    ) -> None: ...
    def activity_calendar(
        self, athlete_alias: str, period_start: date, period_end: date
    ) -> tuple[Mapping[str, Any], ...]: ...
    def activity_detail(
        self, athlete_alias: str, activity_ref: str
    ) -> Mapping[str, Any] | None: ...
    def activity_series(
        self, athlete_alias: str, activity_ref: str
    ) -> Mapping[str, Any] | None: ...
    def latest_activity_input_hash(
        self, athlete_alias: str, activity_ref: str
    ) -> str | None: ...
    def latest_activity_shadow_run_metadata(
        self, athlete_alias: str, activity_ref: str
    ) -> Mapping[str, Any] | None: ...
    def latest_activity_shadow_run_key(
        self, athlete_alias: str, activity_ref: str
    ) -> str | None: ...


class InMemorySnapshotRepository:
    """Atomic process-local pilot repository; replaceable by persistent storage."""

    def __init__(self) -> None:
        self._lock, self._items = RLock(), {}
        self._sync_states: dict[str, dict[str, Any]] = {}
        self._sync_jobs: dict[str, dict[str, Any]] = {}
        self._analysis_generations: dict[str, dict[str, Any]] = {}
        self._generation_activities: dict[
            tuple[str, str], dict[str, Any]
        ] = {}
        self._activity_inputs: dict[tuple[str, str, str], dict[str, Any]] = {}
        self._activity_runs: dict[tuple[str, str], list[dict[str, Any]]] = {}
        self._canonical_activity_runs: dict[
            tuple[str, str], list[dict[str, Any]]
        ] = {}
        self._activity_identity: dict[tuple[str, str], str] = {}
        self._activity_catalog: dict[tuple[str, str], dict[str, Any]] = {}

    def latest(self, athlete_alias: str) -> Mapping[str, Any] | None:
        with self._lock:
            state = self._state(athlete_alias)
            generation = self._analysis_generations.get(
                str(state.get("active_generation_id") or "")
            )
            payload = (
                generation.get("snapshot_payload")
                if generation and generation.get("status") == "ACTIVE"
                else self._items.get(athlete_alias)
            )
            return deepcopy(payload) if payload is not None else None

    def latest_envelope(self, athlete_alias: str) -> Mapping[str, Any] | None:
        with self._lock:
            state = self._state(athlete_alias)
            generation = self._analysis_generations.get(
                str(state.get("active_generation_id") or "")
            )
            payload = (
                generation.get("snapshot_payload")
                if generation and generation.get("status") == "ACTIVE"
                else self._items.get(athlete_alias)
            )
            if payload is None:
                return None
            return {
                "payload": deepcopy(payload),
                "generation_id": state.get("active_generation_id"),
                "revision": int(state.get("active_revision", 0)),
                "activated_at": (
                    generation.get("activated_at") if generation else None
                ),
            }

    def active_analysis(self, athlete_alias: str) -> Mapping[str, Any] | None:
        envelope = self.latest_envelope(athlete_alias)
        if envelope is None:
            return None
        with self._lock:
            generation = self._analysis_generations.get(
                str(envelope.get("generation_id") or "")
            )
            return {
                "generation_id": envelope.get("generation_id"),
                "revision": int(envelope.get("revision", 0)),
                "analysis_as_of": generation.get("as_of") if generation else None,
                "activated_at": self._iso(envelope.get("activated_at")),
                "snapshot_payload": deepcopy(envelope["payload"]),
            }

    def active_activity_calendar(
        self, athlete_alias: str, period_start: date, period_end: date
    ) -> Mapping[str, Any] | None:
        analysis = self.active_analysis(athlete_alias)
        if analysis is None:
            return None
        rows = self.activity_calendar(athlete_alias, period_start, period_end)
        with self._lock:
            activities = []
            for row in rows:
                rendered = deepcopy(dict(row))
                activity_ref = str(rendered.get("activity_ref") or "")
                pinned = self._active_activity_row(athlete_alias, activity_ref)
                shadow_key = (
                    pinned.get("shadow_run_key") if pinned else None
                ) or rendered.get("latest_shadow_run_key")
                shadow = self._run_by_key(
                    self._activity_runs.get((athlete_alias, activity_ref), []),
                    shadow_key,
                )
                rendered["hrmod_zone_summary"] = deepcopy(
                    (shadow.get("result_payload") or {}).get("zone_summary", [])
                    if shadow
                    else []
                )
                activities.append(rendered)
            return {**analysis, "activities": activities}

    def active_activity_view(
        self, athlete_alias: str, activity_ref: str
    ) -> Mapping[str, Any] | None:
        with self._lock:
            analysis = self.active_analysis(athlete_alias)
            detail = self.activity_detail(athlete_alias, activity_ref)
            if analysis is None or detail is None:
                return None
            pinned = self._active_activity_row(athlete_alias, activity_ref)
            shadow_key = (
                pinned.get("shadow_run_key")
                if pinned
                else detail.get("latest_shadow_run_key")
            )
            legacy_runs = self._activity_runs.get(
                (athlete_alias, activity_ref), []
            )
            if (
                not shadow_key
                and analysis["generation_id"] is None
                and legacy_runs
            ):
                shadow_key = legacy_runs[-1].get("run_key")
            canonical_key = (
                pinned.get("canonical_run_key")
                if pinned
                else detail.get("latest_canonical_run_key")
            )
            shadow = self._run_by_key(
                legacy_runs,
                shadow_key,
            )
            input_key = (
                pinned.get("input_key")
                if pinned
                else shadow.get("input_key")
                if shadow
                else None
            )
            if input_key is None and analysis["generation_id"] is None:
                for (alias, ref, input_hash), _ in reversed(
                    self._activity_inputs.items()
                ):
                    if alias == athlete_alias and ref == activity_ref:
                        input_key = hashlib.sha256(
                            f"{alias}|{ref}|{input_hash}".encode("utf-8")
                        ).hexdigest()
                        break
            series_payload = None
            if input_key:
                for (alias, ref, _), payload in self._activity_inputs.items():
                    if alias != athlete_alias or ref != activity_ref:
                        continue
                    candidate_key = hashlib.sha256(
                        f"{alias}|{ref}|{payload.get('input_hash', '')}".encode(
                            "utf-8"
                        )
                    ).hexdigest()
                    if candidate_key == input_key:
                        series_payload = deepcopy(payload)
                        break
            catalog_payload = {
                key: deepcopy(value)
                for key, value in detail.items()
                if key
                not in {
                    "previous_activity_ref",
                    "next_activity_ref",
                    "shadow_available",
                }
            }
            return {
                "generation_id": analysis["generation_id"],
                "revision": analysis["revision"],
                "analysis_as_of": analysis["analysis_as_of"],
                "activated_at": analysis["activated_at"],
                "catalog_payload": catalog_payload,
                "input_key": input_key,
                "canonical_run_key": canonical_key,
                "shadow_run_key": shadow_key,
                "previous_activity_ref": detail.get("previous_activity_ref"),
                "next_activity_ref": detail.get("next_activity_ref"),
                "series_payload": series_payload,
                "shadow_payload": (
                    deepcopy(shadow.get("result_payload")) if shadow else None
                ),
            }

    def replace(self, athlete_alias: str, snapshot: Mapping[str, Any]) -> None:
        """Legacy revision-zero replacement retained only for rollout fallback."""

        with self._lock:
            self._items[athlete_alias] = deepcopy(dict(snapshot))
            self._state(athlete_alias)

    def _state(self, athlete_alias: str) -> dict[str, Any]:
        return self._sync_states.setdefault(
            athlete_alias,
            {
                "athlete_alias": athlete_alias,
                "active_generation_id": None,
                "active_revision": 0,
                "request_sequence": 0,
                "updated_at": datetime.now(timezone.utc),
            },
        )

    def _active_activity_row(
        self, athlete_alias: str, activity_ref: str
    ) -> dict[str, Any] | None:
        state = self._state(athlete_alias)
        generation_id = state.get("active_generation_id")
        if not generation_id:
            return None
        activity_set_id = self._activity_set_generation_id(str(generation_id))
        return self._generation_activities.get((activity_set_id, activity_ref))

    def _activity_set_generation_id(self, generation_id: str) -> str:
        generation = self._analysis_generations.get(generation_id)
        if generation is None:
            return generation_id
        return str(
            generation.get("activity_set_generation_id") or generation_id
        )

    @staticmethod
    def _run_by_key(
        runs: list[dict[str, Any]], run_key: Any
    ) -> dict[str, Any] | None:
        if not run_key:
            return None
        return next(
            (run for run in runs if run.get("run_key") == run_key),
            None,
        )

    def _pinned_activity_keys(
        self,
        *,
        athlete_alias: str,
        activity_ref: str,
        input_key: Any,
        canonical_run_key: Any,
        shadow_run_key: Any,
    ) -> tuple[str | None, str | None, str | None]:
        """Validate and normalize one immutable generation pointer set.

        A captured catalog row normally contains only ``latest_shadow_run_key``.
        The exact input must therefore be resolved from that immutable derived
        run, never from a latest-by-time query.
        """

        normalized_shadow = str(shadow_run_key) if shadow_run_key else None
        normalized_input = str(input_key) if input_key else None
        if normalized_shadow is not None:
            shadow = self._run_by_key(
                self._activity_runs.get((athlete_alias, activity_ref), []),
                normalized_shadow,
            )
            if shadow is None:
                raise ValueError("staged activity shadow pointer is unavailable")
            resolved_input = str(shadow.get("input_key") or "") or None
            if resolved_input is None or (
                normalized_input is not None and normalized_input != resolved_input
            ):
                raise ValueError("staged activity input pointer is inconsistent")
            normalized_input = resolved_input

        normalized_canonical = (
            str(canonical_run_key) if canonical_run_key else None
        )
        if normalized_canonical is not None:
            canonical_runs = self._canonical_activity_runs.get(
                (athlete_alias, activity_ref), []
            )
            if not self._run_by_key(canonical_runs, normalized_canonical):
                raise ValueError("staged canonical activity pointer is unavailable")
        return normalized_input, normalized_canonical, normalized_shadow

    @staticmethod
    def _iso(value: Any) -> Any:
        return value.isoformat() if isinstance(value, datetime) else value

    @classmethod
    def _public_job(cls, job: Mapping[str, Any]) -> dict[str, Any]:
        return {
            key: cls._iso(value)
            for key, value in job.items()
            if key != "lease_token_internal"
        }

    @staticmethod
    def _payload_hash(payload: Mapping[str, Any]) -> str:
        rendered = json.dumps(
            dict(payload),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        return hashlib.sha256(rendered.encode("utf-8")).hexdigest()

    def enqueue_sync_job(
        self,
        *,
        athlete_alias: str,
        job_kind: str,
        idempotency_key: str,
        request_payload: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        if job_kind not in {"FULL_SYNC", "WELLNESS_SYNC", "RECOVERY_RESTORE"}:
            raise ValueError("unsupported sync job kind")
        if len(idempotency_key) != 64 or not isinstance(request_payload, Mapping):
            raise ValueError("invalid sync job request")
        with self._lock:
            state = self._state(athlete_alias)
            for job in self._sync_jobs.values():
                if (
                    job["athlete_alias"] == athlete_alias
                    and job["job_kind"] == job_kind
                    and (
                        job["idempotency_key"] == idempotency_key
                        or job["status"] in {"QUEUED", "RETRY_WAIT"}
                    )
                ):
                    return {
                        **self._public_job(job),
                        "deduplicated": True,
                    }
            state["request_sequence"] += 1
            state["updated_at"] = datetime.now(timezone.utc)
            job_id = str(uuid.uuid4())
            now = datetime.now(timezone.utc)
            job = {
                "job_id": job_id,
                "athlete_alias": athlete_alias,
                "job_kind": job_kind,
                "idempotency_key": idempotency_key,
                "request_sequence": state["request_sequence"],
                "request_payload": deepcopy(dict(request_payload)),
                "status": "QUEUED",
                "priority": 0,
                "attempt_count": 0,
                "max_attempts": 3,
                "available_at": now,
                "lease_token": None,
                "lease_owner": None,
                "lease_expires_at": None,
                "current_generation_id": None,
                "progress_stage": None,
                "progress_percent": None,
                "error_code": None,
                "requested_at": now,
                "started_at": None,
                "completed_at": None,
                "updated_at": now,
            }
            self._sync_jobs[job_id] = job
            return {**self._public_job(job), "deduplicated": False}

    def sync_state(self, athlete_alias: str) -> Mapping[str, Any]:
        with self._lock:
            state = deepcopy(self._state(athlete_alias))
            jobs = [
                job
                for job in self._sync_jobs.values()
                if job["athlete_alias"] == athlete_alias
            ]
            selected_job = min(
                jobs,
                key=lambda row: (
                    0
                    if row["status"] == "RUNNING"
                    else 1
                    if row["status"] in {"QUEUED", "RETRY_WAIT"}
                    else 2,
                    (
                        int(row["request_sequence"])
                        if row["status"] in {"QUEUED", "RETRY_WAIT"}
                        else -int(row["request_sequence"])
                    ),
                    row["requested_at"],
                    row["job_id"],
                ),
                default=None,
            )
            generation = self._analysis_generations.get(
                str(state.get("active_generation_id") or "")
            )
            return {
                "athlete_alias": athlete_alias,
                "active_generation_id": state.get("active_generation_id"),
                "active_revision": int(state.get("active_revision", 0)),
                "request_sequence": int(state.get("request_sequence", 0)),
                "active_as_of": generation.get("as_of") if generation else None,
                "activated_at": self._iso(
                    generation.get("activated_at") if generation else None
                ),
                "job_id": selected_job.get("job_id") if selected_job else None,
                "job_kind": selected_job.get("job_kind") if selected_job else None,
                "status": selected_job.get("status") if selected_job else None,
                "progress_stage": (
                    selected_job.get("progress_stage") if selected_job else None
                ),
                "progress_percent": (
                    selected_job.get("progress_percent") if selected_job else None
                ),
                "error_code": selected_job.get("error_code") if selected_job else None,
                "requested_at": self._iso(
                    selected_job.get("requested_at") if selected_job else None
                ),
                "started_at": self._iso(
                    selected_job.get("started_at") if selected_job else None
                ),
                "completed_at": self._iso(
                    selected_job.get("completed_at") if selected_job else None
                ),
                "available_at": self._iso(
                    selected_job.get("available_at") if selected_job else None
                ),
                "pending_job_count": sum(
                    job["status"] in {"QUEUED", "RETRY_WAIT"} for job in jobs
                ),
            }

    def _lease_valid(
        self,
        job: Mapping[str, Any] | None,
        *,
        generation_id: str,
        lease_token: str,
    ) -> bool:
        return bool(
            job
            and job.get("status") == "RUNNING"
            and job.get("current_generation_id") == generation_id
            and job.get("lease_token") == lease_token
            and isinstance(job.get("lease_expires_at"), datetime)
            and job["lease_expires_at"] >= datetime.now(timezone.utc)
        )

    def claim_sync_job(
        self, *, worker_id: str, lease_seconds: int = 300
    ) -> Mapping[str, Any] | None:
        if not 1 <= len(worker_id) <= 128 or not 30 <= lease_seconds <= 3600:
            raise ValueError("invalid worker lease request")
        now = datetime.now(timezone.utc)
        with self._lock:
            for job in self._sync_jobs.values():
                if (
                    job["status"] == "RUNNING"
                    and job["lease_expires_at"] < now
                ):
                    generation = self._analysis_generations.get(
                        str(job.get("current_generation_id") or "")
                    )
                    if generation and generation["status"] in {"BUILDING", "READY"}:
                        generation["status"] = "FAILED"
                        generation["failed_at"] = now
                    successor_exists = any(
                        candidate["job_id"] != job["job_id"]
                        and candidate["athlete_alias"] == job["athlete_alias"]
                        and candidate["job_kind"] == job["job_kind"]
                        and candidate["status"] in {"QUEUED", "RETRY_WAIT"}
                        for candidate in self._sync_jobs.values()
                    )
                    if successor_exists:
                        job["status"] = "SUPERSEDED"
                        job["completed_at"] = now
                        job["progress_stage"] = "SUPERSEDED"
                    elif job["attempt_count"] < job["max_attempts"]:
                        job["status"] = "RETRY_WAIT"
                        job["available_at"] = now
                    else:
                        job["status"] = "FAILED"
                        job["error_code"] = "LEASE_EXPIRED"
                        job["completed_at"] = now
                    job["lease_token"] = None
                    job["lease_owner"] = None
                    job["lease_expires_at"] = None
                    job["current_generation_id"] = None
                    job["updated_at"] = now

            running_aliases = {
                str(job["athlete_alias"])
                for job in self._sync_jobs.values()
                if job["status"] == "RUNNING"
            }
            candidates = [
                job
                for job in self._sync_jobs.values()
                if job["status"] in {"QUEUED", "RETRY_WAIT"}
                and job["available_at"] <= now
                and job["athlete_alias"] not in running_aliases
            ]
            if not candidates:
                return None
            job = min(
                candidates,
                key=lambda row: (
                    -int(row["priority"]),
                    row["available_at"],
                    row["requested_at"],
                    row["job_id"],
                ),
            )
            state = self._state(str(job["athlete_alias"]))
            generation_id = str(uuid.uuid4())
            lease_token = str(uuid.uuid4())
            lease_expires_at = now + timedelta(seconds=lease_seconds)
            attempt_no = int(job["attempt_count"]) + 1
            generation = {
                "generation_id": generation_id,
                "athlete_alias": job["athlete_alias"],
                "job_id": job["job_id"],
                "attempt_no": attempt_no,
                "job_kind": job["job_kind"],
                "request_sequence": job["request_sequence"],
                "base_generation_id": state["active_generation_id"],
                "base_revision": state["active_revision"],
                "activated_revision": None,
                "status": "BUILDING",
                "created_at": now,
            }
            self._analysis_generations[generation_id] = generation
            job.update(
                {
                    "status": "RUNNING",
                    "attempt_count": attempt_no,
                    "lease_token": lease_token,
                    "lease_owner": worker_id,
                    "lease_expires_at": lease_expires_at,
                    "current_generation_id": generation_id,
                    "progress_stage": "CLAIMED",
                    "progress_percent": 0,
                    "started_at": job.get("started_at") or now,
                    "completed_at": None,
                    "error_code": None,
                    "updated_at": now,
                }
            )
            base = self._analysis_generations.get(
                str(state.get("active_generation_id") or "")
            )
            return {
                "job_id": job["job_id"],
                "athlete_alias": job["athlete_alias"],
                "job_kind": job["job_kind"],
                "request_payload": deepcopy(job["request_payload"]),
                "request_sequence": job["request_sequence"],
                "generation_id": generation_id,
                "attempt_no": attempt_no,
                "base_generation_id": state["active_generation_id"],
                "base_revision": state["active_revision"],
                "base_activity_set_hash": (
                    (base.get("provenance") or {}).get("activity_set_hash")
                    if base
                    else None
                ),
                "lease_token": lease_token,
                "lease_expires_at": lease_expires_at.isoformat(),
            }

    def renew_sync_lease(
        self,
        *,
        job_id: str,
        generation_id: str,
        lease_token: str,
        lease_seconds: int = 300,
    ) -> bool:
        if not 30 <= lease_seconds <= 3600:
            return False
        with self._lock:
            job = self._sync_jobs.get(job_id)
            if not self._lease_valid(
                job, generation_id=generation_id, lease_token=lease_token
            ):
                return False
            job["lease_expires_at"] = datetime.now(timezone.utc) + timedelta(
                seconds=lease_seconds
            )
            job["updated_at"] = datetime.now(timezone.utc)
            return True

    def stage_analysis_generation(
        self,
        *,
        job_id: str,
        generation_id: str,
        lease_token: str,
        snapshot_payload: Mapping[str, Any],
        snapshot_hash: str,
        period_start: date,
        period_end: date,
        as_of: date,
        provenance: Mapping[str, Any],
        activities: list[Mapping[str, Any]],
        inherit_activities: bool = False,
    ) -> Mapping[str, Any]:
        if (
            len(snapshot_hash) != 64
            or period_start > period_end
            or snapshot_payload.get("schema_version") != "athlete-snapshot-v1"
        ):
            raise ValueError("analysis generation payload is invalid")
        with self._lock:
            job = self._sync_jobs.get(job_id)
            generation = self._analysis_generations.get(generation_id)
            if generation and generation.get("status") == "READY":
                if generation.get("snapshot_hash") != snapshot_hash:
                    raise ValueError(
                        "generation is already staged with different content"
                    )
                return {
                    "outcome": "ALREADY_READY",
                    "activity_count": generation["activity_count"],
                }
            if not generation or not self._lease_valid(
                job, generation_id=generation_id, lease_token=lease_token
            ) or generation.get("status") != "BUILDING":
                raise ValueError("sync generation lease is unavailable")
            athlete_alias = str(generation["athlete_alias"])
            for section in ("training_status", "load_history", "recovery_history"):
                value = snapshot_payload.get(section)
                if not isinstance(value, Mapping) or value.get("athlete_id") != athlete_alias:
                    raise ValueError("analysis generation athlete scope is invalid")

            staged: list[dict[str, Any]] = []
            activity_set_generation_id = generation_id
            if inherit_activities:
                if activities:
                    raise ValueError(
                        "inherited activity generation must not include rows"
                    )
                base_generation_id = generation.get("base_generation_id")
                if base_generation_id:
                    activity_set_generation_id = self._activity_set_generation_id(
                        str(base_generation_id)
                    )
                    staged = [
                        deepcopy(row)
                        for (candidate_id, _), row in self._generation_activities.items()
                        if candidate_id == activity_set_generation_id
                    ]
                else:
                    for (alias, ref), row in self._activity_catalog.items():
                        if (
                            alias != athlete_alias
                            or not row.get("local_date")
                            or not period_start
                            <= date.fromisoformat(str(row["local_date"]))
                            <= period_end
                        ):
                            continue
                        input_key, canonical_key, shadow_key = (
                            self._pinned_activity_keys(
                                athlete_alias=athlete_alias,
                                activity_ref=ref,
                                input_key=None,
                                canonical_run_key=row.get(
                                    "latest_canonical_run_key"
                                ),
                                shadow_run_key=row.get(
                                    "latest_shadow_run_key"
                                ),
                            )
                        )
                        staged.append(
                            {
                                "athlete_alias": athlete_alias,
                                "activity_ref": ref,
                                "start_at_utc": row.get("start_at_utc"),
                                "local_date": row.get("local_date"),
                                "catalog_payload": deepcopy(row),
                                "payload_hash": self._payload_hash(row),
                                "input_key": input_key,
                                "canonical_run_key": canonical_key,
                                "shadow_run_key": shadow_key,
                            }
                        )
            else:
                for activity in activities:
                    source = activity.get("catalog_payload")
                    payload = (
                        deepcopy(dict(source))
                        if isinstance(source, Mapping)
                        else deepcopy(dict(activity))
                    )
                    activity_ref = str(
                        activity.get("activity_ref")
                        or payload.get("activity_ref")
                        or ""
                    )
                    if not activity_ref:
                        raise ValueError("staged activity identity is missing")
                    input_key, canonical_key, shadow_key = (
                        self._pinned_activity_keys(
                            athlete_alias=athlete_alias,
                            activity_ref=activity_ref,
                            input_key=activity.get("input_key"),
                            canonical_run_key=activity.get(
                                "canonical_run_key",
                                payload.get("latest_canonical_run_key"),
                            ),
                            shadow_run_key=activity.get(
                                "shadow_run_key",
                                payload.get("latest_shadow_run_key"),
                            ),
                        )
                    )
                    staged.append(
                        {
                            "athlete_alias": athlete_alias,
                            "activity_ref": activity_ref,
                            "start_at_utc": activity.get(
                                "start_at_utc", payload.get("start_at_utc")
                            ),
                            "local_date": activity.get(
                                "local_date", payload.get("local_date")
                            ),
                            "catalog_payload": payload,
                            "payload_hash": str(
                                activity.get("payload_hash")
                                or self._payload_hash(payload)
                            ),
                            "input_key": input_key,
                            "canonical_run_key": canonical_key,
                            "shadow_run_key": shadow_key,
                        }
                    )

            staged_by_ref = {row["activity_ref"]: row for row in staged}
            if len(staged_by_ref) != len(staged):
                raise ValueError("staged activities must be unique")
            history = snapshot_payload.get("load_history")
            history_activities = (
                history.get("activities", []) if isinstance(history, Mapping) else []
            )
            if any(
                not isinstance(row, Mapping)
                or row.get("activity_ref") not in staged_by_ref
                for row in history_activities
            ):
                raise ValueError("snapshot references an unstaged activity")

            if activity_set_generation_id == generation_id:
                for activity_ref, row in staged_by_ref.items():
                    self._generation_activities[(generation_id, activity_ref)] = row
            ready_at = datetime.now(timezone.utc)
            generation.update(
                {
                    "status": "READY",
                    "snapshot_schema_version": "athlete-snapshot-v1",
                    "snapshot_hash": snapshot_hash,
                    "snapshot_payload": deepcopy(dict(snapshot_payload)),
                    "period_start": period_start.isoformat(),
                    "period_end": period_end.isoformat(),
                    "as_of": as_of.isoformat(),
                    "provenance": deepcopy(dict(provenance)),
                    "activity_count": len(staged_by_ref),
                    "activity_set_generation_id": activity_set_generation_id,
                    "ready_at": ready_at,
                }
            )
            job["progress_stage"] = "READY"
            job["progress_percent"] = 95
            job["updated_at"] = ready_at
            return {"outcome": "READY", "activity_count": len(staged_by_ref)}

    def activate_analysis_generation(
        self, *, job_id: str, generation_id: str, lease_token: str
    ) -> Mapping[str, Any]:
        with self._lock:
            generation = self._analysis_generations.get(generation_id)
            if not generation:
                return {
                    "outcome": "LEASE_LOST",
                    "active_generation_id": None,
                    "active_revision": None,
                }
            state = self._state(str(generation["athlete_alias"]))
            if (
                generation.get("status") == "ACTIVE"
                and state["active_generation_id"] == generation_id
            ):
                return {
                    "outcome": "ACTIVATED",
                    "active_generation_id": generation_id,
                    "active_revision": state["active_revision"],
                }
            job = self._sync_jobs.get(job_id)
            if not self._lease_valid(
                job, generation_id=generation_id, lease_token=lease_token
            ) or generation.get("job_id") != job_id:
                return {
                    "outcome": "LEASE_LOST",
                    "active_generation_id": state["active_generation_id"],
                    "active_revision": state["active_revision"],
                }
            if (
                generation.get("status") != "READY"
                or state["active_revision"] != generation["base_revision"]
                or state["active_generation_id"]
                != generation["base_generation_id"]
            ):
                if generation.get("status") in {"BUILDING", "READY"}:
                    generation["status"] = "SUPERSEDED"
                    generation["superseded_at"] = datetime.now(timezone.utc)
                job.update(
                    {
                        "status": "SUPERSEDED",
                        "lease_token": None,
                        "lease_owner": None,
                        "lease_expires_at": None,
                        "current_generation_id": None,
                        "completed_at": datetime.now(timezone.utc),
                        "progress_stage": "SUPERSEDED",
                    }
                )
                return {
                    "outcome": "STALE",
                    "active_generation_id": state["active_generation_id"],
                    "active_revision": state["active_revision"],
                }
            previous = self._analysis_generations.get(
                str(state.get("active_generation_id") or "")
            )
            now = datetime.now(timezone.utc)
            if previous and previous.get("status") == "ACTIVE":
                previous["status"] = "SUPERSEDED"
                previous["superseded_at"] = now
            revision = int(state["active_revision"]) + 1
            generation.update(
                {
                    "status": "ACTIVE",
                    "activated_revision": revision,
                    "activated_at": now,
                }
            )
            state.update(
                {
                    "active_generation_id": generation_id,
                    "active_revision": revision,
                    "updated_at": now,
                }
            )
            self._items[str(generation["athlete_alias"])] = deepcopy(
                generation["snapshot_payload"]
            )
            job.update(
                {
                    "status": "SUCCEEDED",
                    "lease_token": None,
                    "lease_owner": None,
                    "lease_expires_at": None,
                    "current_generation_id": None,
                    "completed_at": now,
                    "updated_at": now,
                    "progress_stage": "ACTIVATED",
                    "progress_percent": 100,
                }
            )
            return {
                "outcome": "ACTIVATED",
                "active_generation_id": generation_id,
                "active_revision": revision,
            }

    def fail_sync_job(
        self,
        *,
        job_id: str,
        generation_id: str,
        lease_token: str,
        error_code: str,
        retryable: bool,
        retry_after_seconds: int | None = None,
    ) -> Mapping[str, Any]:
        if not re.fullmatch(r"[A-Z0-9_]{1,64}", error_code):
            raise ValueError("invalid sync error code")
        if retry_after_seconds is not None and not 1 <= retry_after_seconds <= 86_400:
            raise ValueError("invalid retry delay")
        with self._lock:
            job = self._sync_jobs.get(job_id)
            if not self._lease_valid(
                job, generation_id=generation_id, lease_token=lease_token
            ):
                return {"status": "LEASE_LOST", "available_at": None}
            generation = self._analysis_generations.get(generation_id)
            now = datetime.now(timezone.utc)
            if generation and generation.get("status") in {"BUILDING", "READY"}:
                generation["status"] = "FAILED"
                generation["failed_at"] = now
            successor_exists = any(
                candidate["job_id"] != job_id
                and candidate["athlete_alias"] == job["athlete_alias"]
                and candidate["job_kind"] == job["job_kind"]
                and candidate["status"] in {"QUEUED", "RETRY_WAIT"}
                for candidate in self._sync_jobs.values()
            )
            if retryable and successor_exists:
                available_at = None
                status = "SUPERSEDED"
                completed_at = now
            elif retryable and job["attempt_count"] < job["max_attempts"]:
                delay = retry_after_seconds or min(
                    300, 5 * (2 ** max(0, int(job["attempt_count"]) - 1))
                )
                available_at = now + timedelta(seconds=delay)
                status = "RETRY_WAIT"
                completed_at = None
            else:
                available_at = None
                status = "FAILED"
                completed_at = now
            job.update(
                {
                    "status": status,
                    "available_at": available_at or job["available_at"],
                    "lease_token": None,
                    "lease_owner": None,
                    "lease_expires_at": None,
                    "current_generation_id": None,
                    "progress_stage": (
                        "SUPERSEDED"
                        if status == "SUPERSEDED"
                        else None
                        if status == "RETRY_WAIT"
                        else "FAILED"
                    ),
                    "progress_percent": None,
                    "error_code": error_code,
                    "completed_at": completed_at,
                    "updated_at": now,
                }
            )
            return {
                "status": status,
                "available_at": self._iso(available_at),
            }

    def rollback_analysis_generation(
        self, *, athlete_alias: str, target_generation_id: str
    ) -> Mapping[str, Any]:
        with self._lock:
            state = self._state(athlete_alias)
            target = self._analysis_generations.get(target_generation_id)
            if (
                not target
                or target.get("athlete_alias") != athlete_alias
                or target.get("status") != "SUPERSEDED"
                or target.get("activated_revision") is None
                or target.get("activated_at") is None
                or target.get("snapshot_payload") is None
            ):
                raise ValueError("rollback generation is unavailable")
            activity_set_id = self._activity_set_generation_id(
                target_generation_id
            )
            pinned_count = sum(
                candidate_id == activity_set_id
                for candidate_id, _ in self._generation_activities
            )
            if pinned_count != target.get("activity_count"):
                raise ValueError("rollback generation is incomplete")
            current = self._analysis_generations.get(
                str(state.get("active_generation_id") or "")
            )
            if current is None or current.get("status") != "ACTIVE":
                raise ValueError("active analysis is unavailable")
            now = datetime.now(timezone.utc)
            current.update({"status": "SUPERSEDED", "superseded_at": now})
            revision = int(state["active_revision"]) + 1
            target.update(
                {
                    "status": "ACTIVE",
                    "activated_revision": revision,
                    "activated_at": now,
                    "superseded_at": None,
                }
            )
            state.update(
                {
                    "active_generation_id": target_generation_id,
                    "active_revision": revision,
                    "updated_at": now,
                }
            )
            self._items[athlete_alias] = deepcopy(target["snapshot_payload"])
            return {
                "outcome": "ROLLED_BACK",
                "active_generation_id": target_generation_id,
                "active_revision": revision,
            }

    def prune_analysis_generations(
        self,
        *,
        athlete_alias: str,
        keep_superseded: int = 5,
        terminal_older_than_days: int = 30,
        batch_limit: int = 100,
    ) -> Mapping[str, int]:
        del athlete_alias
        if (
            not 1 <= keep_superseded <= 50
            or not 30 <= terminal_older_than_days <= 365
            or not 1 <= batch_limit <= 1000
        ):
            raise ValueError("invalid analysis retention policy")
        return {
            "deleted_generations": 0,
            "deleted_activity_rows": 0,
            "deleted_jobs": 0,
            "deleted_shadow_runs": 0,
            "deleted_canonical_runs": 0,
            "deleted_model_inputs": 0,
            "deleted_catalog_rows": 0,
        }

    def publish_activity_shadow(
        self,
        *,
        athlete_alias: str,
        activity_ref: str,
        input_payload: Mapping[str, Any],
        derived_payload: Mapping[str, Any],
    ) -> str:
        input_hash = str(input_payload.get("input_hash") or "")
        result_hash = str(derived_payload.get("result_hash") or "")
        if len(input_hash) != 64 or len(result_hash) != 64:
            raise ValueError("Activity shadow hashes are invalid")
        run_key = hashlib.sha256(
            f"{athlete_alias}|{activity_ref}|{input_hash}|{result_hash}".encode("utf-8")
        ).hexdigest()
        input_key = hashlib.sha256(
            f"{athlete_alias}|{activity_ref}|{input_hash}".encode("utf-8")
        ).hexdigest()
        with self._lock:
            self._activity_inputs.setdefault(
                (athlete_alias, activity_ref, input_hash), deepcopy(dict(input_payload))
            )
            runs = self._activity_runs.setdefault((athlete_alias, activity_ref), [])
            if not any(run["run_key"] == run_key for run in runs):
                runs.append(
                    {
                        "run_key": run_key,
                        "input_key": input_key,
                        "result_payload": deepcopy(dict(derived_payload)),
                    }
                )
        return run_key

    def publish_canonical_activity_result(
        self,
        *,
        athlete_alias: str,
        activity_ref: str,
        scientific_input_hash: str,
        result_payload: Mapping[str, Any],
    ) -> str:
        return self.store_canonical_activity_result(
            athlete_alias=athlete_alias,
            activity_ref=activity_ref,
            scientific_input_hash=scientific_input_hash,
            result_payload=result_payload,
        )

    def store_canonical_activity_result(
        self,
        *,
        athlete_alias: str,
        activity_ref: str,
        scientific_input_hash: str,
        result_payload: Mapping[str, Any],
    ) -> str:
        if len(scientific_input_hash) != 64:
            raise ValueError("Canonical scientific input hash is invalid")
        rendered = json.dumps(
            dict(result_payload),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        result_hash = hashlib.sha256(rendered.encode("utf-8")).hexdigest()
        run_key = hashlib.sha256(
            (
                f"{athlete_alias}|{activity_ref}|{scientific_input_hash}|"
                f"{result_hash}"
            ).encode("utf-8")
        ).hexdigest()
        with self._lock:
            runs = self._canonical_activity_runs.setdefault(
                (athlete_alias, activity_ref), []
            )
            if not any(run["run_key"] == run_key for run in runs):
                runs.append(
                    {
                        "run_key": run_key,
                        "scientific_input_hash": scientific_input_hash,
                        "result_hash": result_hash,
                        "result_payload": deepcopy(dict(result_payload)),
                    }
                )
        return run_key
    def activity_shadow(
        self, athlete_alias: str, activity_ref: str
    ) -> Mapping[str, Any] | None:
        with self._lock:
            runs = self._activity_runs.get((athlete_alias, activity_ref), [])
            pinned = self._active_activity_row(athlete_alias, activity_ref)
            run = (
                self._run_by_key(runs, pinned.get("shadow_run_key"))
                if pinned is not None
                else (runs[-1] if runs else None)
            )
            return deepcopy(dict(run["result_payload"])) if run else None

    def activity_shadow_index(
        self, athlete_alias: str
    ) -> tuple[Mapping[str, Any], ...]:
        with self._lock:
            generation_id = self._state(athlete_alias).get("active_generation_id")
            if generation_id:
                activity_set_id = self._activity_set_generation_id(
                    str(generation_id)
                )
                rendered = []
                for (candidate_id, activity_ref), pinned in sorted(
                    self._generation_activities.items()
                ):
                    if candidate_id != activity_set_id or not pinned.get("shadow_run_key"):
                        continue
                    run = self._run_by_key(
                        self._activity_runs.get((athlete_alias, activity_ref), []),
                        pinned["shadow_run_key"],
                    )
                    if run is None:
                        continue
                    payload = run.get("result_payload") or {}
                    rendered.append(
                        {
                            "activity_ref": activity_ref,
                            "run_key": run["run_key"],
                            "vflat_model_version": payload.get(
                                "vflat_model_version"
                            ),
                            "hrmod_model_version": payload.get(
                                "hrmod_model_version"
                            ),
                        }
                    )
                return tuple(rendered)
            return tuple(
                {
                    "activity_ref": activity_ref,
                    "run_key": runs[-1]["run_key"],
                    "vflat_model_version": runs[-1]["result_payload"].get(
                        "vflat_model_version"
                    ),
                    "hrmod_model_version": runs[-1]["result_payload"].get(
                        "hrmod_model_version"
                    ),
                }
                for (alias, activity_ref), runs in sorted(self._activity_runs.items())
                if alias == athlete_alias and runs
            )

    def activity_shadow_zone_summaries(
        self, athlete_alias: str, activity_refs: tuple[str, ...]
    ) -> Mapping[str, list[Mapping[str, Any]]]:
        requested = set(activity_refs)
        with self._lock:
            generation_id = self._state(athlete_alias).get("active_generation_id")
            if generation_id:
                activity_set_id = self._activity_set_generation_id(
                    str(generation_id)
                )
                summaries: dict[str, list[Mapping[str, Any]]] = {}
                for activity_ref in requested:
                    pinned = self._generation_activities.get(
                        (activity_set_id, activity_ref)
                    )
                    run = (
                        self._run_by_key(
                            self._activity_runs.get(
                                (athlete_alias, activity_ref), []
                            ),
                            pinned.get("shadow_run_key") if pinned else None,
                        )
                        if pinned
                        else None
                    )
                    if run is not None:
                        summaries[activity_ref] = deepcopy(
                            list(
                                (run.get("result_payload") or {}).get(
                                    "zone_summary"
                                )
                                or []
                            )
                        )
                return summaries
            return {
                activity_ref: deepcopy(
                    list(runs[-1]["result_payload"].get("zone_summary") or [])
                )
                for (alias, activity_ref), runs in self._activity_runs.items()
                if alias == athlete_alias and activity_ref in requested and runs
            }

    def resolve_activity_ref(
        self, athlete_alias: str, provider_activity_key: str
    ) -> str:
        import uuid

        with self._lock:
            return self._activity_identity.setdefault(
                (athlete_alias, provider_activity_key), f"act_{uuid.uuid4().hex}"
            )

    def upsert_activity_catalog(
        self, athlete_alias: str, activities: list[Mapping[str, Any]]
    ) -> None:
        with self._lock:
            for activity in activities:
                activity_ref = str(activity.get("activity_ref") or "")
                existing = self._activity_catalog.get((athlete_alias, activity_ref), {})
                self._activity_catalog[(athlete_alias, activity_ref)] = {
                    **deepcopy(existing),
                    **deepcopy(dict(activity)),
                    "athlete_alias": athlete_alias,
                }

    def activity_calendar(
        self, athlete_alias: str, period_start: date, period_end: date
    ) -> tuple[Mapping[str, Any], ...]:
        with self._lock:
            generation_id = self._state(athlete_alias).get("active_generation_id")
            if generation_id:
                activity_set_id = self._activity_set_generation_id(
                    str(generation_id)
                )
                rows = [
                    {
                        **deepcopy(row["catalog_payload"]),
                        "activity_ref": activity_ref,
                        "latest_canonical_run_key": row.get(
                            "canonical_run_key"
                        ),
                        "latest_shadow_run_key": row.get("shadow_run_key"),
                    }
                    for (candidate_id, activity_ref), row
                    in self._generation_activities.items()
                    if candidate_id == activity_set_id
                    and row.get("local_date")
                    and period_start
                    <= date.fromisoformat(str(row["local_date"]))
                    <= period_end
                ]
            else:
                rows = [
                    deepcopy(row)
                    for (alias, _), row in self._activity_catalog.items()
                    if alias == athlete_alias
                    and row.get("local_date")
                    and period_start
                    <= date.fromisoformat(str(row["local_date"]))
                    <= period_end
                ]
        return tuple(sorted(rows, key=lambda row: (str(row.get("start_at_utc") or ""), str(row["activity_ref"]))))

    def activity_detail(
        self, athlete_alias: str, activity_ref: str
    ) -> Mapping[str, Any] | None:
        with self._lock:
            generation_id = self._state(athlete_alias).get("active_generation_id")
            pinned = self._active_activity_row(athlete_alias, activity_ref)
            if generation_id:
                activity_set_id = self._activity_set_generation_id(
                    str(generation_id)
                )
                if pinned is None:
                    return None
                row = {
                    **deepcopy(pinned["catalog_payload"]),
                    "activity_ref": activity_ref,
                    "latest_canonical_run_key": pinned.get("canonical_run_key"),
                    "latest_shadow_run_key": pinned.get("shadow_run_key"),
                }
                ordered = sorted(
                    (
                        {
                            **candidate["catalog_payload"],
                            "activity_ref": candidate_ref,
                        }
                        for (candidate_id, candidate_ref), candidate
                        in self._generation_activities.items()
                        if candidate_id == activity_set_id
                        and candidate.get("start_at_utc")
                    ),
                    key=lambda item: (
                        str(item.get("start_at_utc")),
                        str(item.get("activity_ref")),
                    ),
                )
            else:
                row = self._activity_catalog.get((athlete_alias, activity_ref))
                if row is None:
                    return None
                ordered = sorted(
                    (
                        item for (alias, _), item in self._activity_catalog.items()
                        if alias == athlete_alias and item.get("start_at_utc")
                    ),
                    key=lambda item: (
                        str(item.get("start_at_utc")),
                        str(item.get("activity_ref")),
                    ),
                )
            refs = [str(item["activity_ref"]) for item in ordered]
            position = refs.index(activity_ref) if activity_ref in refs else -1
            return {
                **deepcopy(row),
                "previous_activity_ref": refs[position - 1] if position > 0 else None,
                "next_activity_ref": refs[position + 1] if 0 <= position < len(refs) - 1 else None,
                "shadow_available": bool(
                    pinned.get("shadow_run_key")
                    if pinned is not None
                    else self._activity_runs.get((athlete_alias, activity_ref))
                ),
            }

    def activity_series(
        self, athlete_alias: str, activity_ref: str
    ) -> Mapping[str, Any] | None:
        from .activity_catalog import downsample_model_input

        with self._lock:
            pinned = self._active_activity_row(athlete_alias, activity_ref)
            if pinned is not None:
                input_key = pinned.get("input_key")
                if not input_key:
                    return None
                for (alias, ref, _), payload in self._activity_inputs.items():
                    if alias == athlete_alias and ref == activity_ref:
                        candidate_key = hashlib.sha256(
                            f"{alias}|{ref}|{payload.get('input_hash', '')}".encode(
                                "utf-8"
                            )
                        ).hexdigest()
                        if candidate_key == input_key:
                            return downsample_model_input(deepcopy(payload))
                return None
            candidates = [
                payload for (alias, ref, _), payload in self._activity_inputs.items()
                if alias == athlete_alias and ref == activity_ref
            ]
            return downsample_model_input(deepcopy(candidates[-1])) if candidates else None

    def latest_activity_input_hash(
        self, athlete_alias: str, activity_ref: str
    ) -> str | None:
        with self._lock:
            hashes = [
                input_hash for alias, ref, input_hash in self._activity_inputs
                if alias == athlete_alias and ref == activity_ref
            ]
            return hashes[-1] if hashes else None

    def latest_activity_shadow_run_metadata(
        self, athlete_alias: str, activity_ref: str
    ) -> Mapping[str, Any] | None:
        with self._lock:
            runs = self._activity_runs.get((athlete_alias, activity_ref), [])
            if not runs:
                return None
            latest = runs[-1]
            payload = latest.get("result_payload")
            fingerprint = (
                payload.get("configuration_fingerprint")
                if isinstance(payload, Mapping)
                else None
            )
            return {
                "run_key": str(latest["run_key"]),
                "configuration_fingerprint": fingerprint,
            }

    def latest_activity_shadow_run_key(
        self, athlete_alias: str, activity_ref: str
    ) -> str | None:
        with self._lock:
            runs = self._activity_runs.get((athlete_alias, activity_ref), [])
            pinned = self._active_activity_row(athlete_alias, activity_ref)
            if pinned is not None:
                return (
                    str(pinned["shadow_run_key"])
                    if pinned.get("shadow_run_key")
                    else None
                )
            return str(runs[-1]["run_key"]) if runs else None
