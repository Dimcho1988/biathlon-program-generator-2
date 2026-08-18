"""Cloud-pilot boundaries: configuration, auth, wellness and snapshots.

This module contains no provider credentials or physiology formulae.  The
single-profile implementation is deliberately an adapter around an
``AthleteContext`` and replaceable repository protocol.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
import hashlib
import hmac
import json
import math
from threading import RLock
from typing import Any, Mapping, Protocol
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


@dataclass(frozen=True)
class AthleteModelSettings:
    """Athlete-specific inputs; shared model versions remain service-wide."""

    zone_bounds_bpm: tuple[int, int, int, int, int, int]
    timezone: str

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
        max_sessions = 2 * (7 - len(self.rest_days))
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


@dataclass(frozen=True)
class AthleteContext:
    public_alias: str
    provider_athlete_id: str
    zone_bounds_bpm: tuple[int, int, int, int, int, int]
    timezone: str
    intra_zone_version: str
    tref_version: str
    recovery_parameter_version: str

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
        if not all((self.timezone, self.tref_version, self.recovery_parameter_version)):
            raise ValueError("all configuration versions are required")
        return self

    @property
    def fingerprint(self) -> str:
        self.validate()
        # Provider identity is intentionally excluded from model configuration.
        payload = {"alias": self.public_alias, "zones": self.zone_bounds_bpm,
                   "timezone": self.timezone, "intra": self.intra_zone_version,
                   "tref": self.tref_version, "recovery": self.recovery_parameter_version}
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def service_token_valid(provided: str | None, expected: str) -> bool:
    """Constant-time validation with identical handling of absent/bad tokens."""
    candidate = provided or ""
    return bool(expected) and hmac.compare_digest(candidate.encode(), expected.encode())


WELLNESS_FIELDS = {
    "sleepSecs": ("sleep_duration", "s"), "sleepScore": ("sleep_score", "score"),
    "fatigue": ("fatigue", "score"), "stress": ("stress", "score"),
    "motivation": ("motivation", "score"), "restingHR": ("resting_hr", "bpm"),
    "hrv": ("hrv", "ms"), "soreness": ("soreness", "score"),
    "illness": ("illness", "boolean"),
}


def normalize_wellness(row: Mapping[str, Any], *, now: datetime | None = None) -> dict[str, Any]:
    """Map only unambiguous Intervals fields; missing never becomes neutral."""
    stamp = row.get("id") or row.get("date")
    observed: datetime | None = None
    if isinstance(stamp, str):
        try:
            observed = datetime.fromisoformat(stamp[:10]).replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    clock = now or datetime.now(timezone.utc)
    values: dict[str, Any] = {}
    invalid: list[str] = []
    for source, (target, unit) in WELLNESS_FIELDS.items():
        raw = row.get(source)
        if raw is None:
            values[target] = {"value": None, "unit": unit, "state": "missing"}
        elif isinstance(raw, bool) and target == "illness":
            values[target] = {"value": raw, "unit": unit, "state": "valid"}
        elif isinstance(raw, (int, float)) and not isinstance(raw, bool) and math.isfinite(float(raw)):
            values[target] = {"value": float(raw), "unit": unit, "state": "valid"}
        else:
            values[target] = {"value": None, "unit": unit, "state": "invalid"}; invalid.append(target)
    age_hours = None if observed is None else max(0.0, (clock - observed).total_seconds() / 3600)
    valid = sum(v["state"] == "valid" for v in values.values())
    return {"source": "intervals", "observed_at": observed.isoformat() if observed else None,
            "freshness": "unknown" if age_hours is None else ("fresh" if age_hours <= 48 else "stale"),
            "coverage": valid / len(values), "values": values,
            "warnings": [f"invalid wellness field: {x}" for x in invalid]}


class SnapshotRepository(Protocol):
    def latest(self, athlete_alias: str) -> Mapping[str, Any] | None: ...
    def replace(self, athlete_alias: str, snapshot: Mapping[str, Any]) -> None: ...


class InMemorySnapshotRepository:
    """Atomic process-local pilot repository; replaceable by persistent storage."""
    def __init__(self) -> None: self._lock, self._items = RLock(), {}
    def latest(self, athlete_alias: str) -> Mapping[str, Any] | None:
        with self._lock: return self._items.get(athlete_alias)
    def replace(self, athlete_alias: str, snapshot: Mapping[str, Any]) -> None:
        with self._lock: self._items[athlete_alias] = dict(snapshot)
