"""Cloud-pilot boundaries: configuration, auth, wellness and snapshots.

This module contains no provider credentials or physiology formulae.  The
single-profile implementation is deliberately an adapter around an
``AthleteContext`` and replaceable repository protocol.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
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
