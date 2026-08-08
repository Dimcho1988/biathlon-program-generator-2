"""Versioned, privacy-safe onFlows intrazone weighting profiles."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import hashlib
import json
import math
from numbers import Real
from typing import Any


PROFILE_SCHEMA_VERSION = "onflows-zone-profile-v1"
DEFAULT_PROFILE_SOURCE = "default_demo_profile"
MANUAL_PROFILE_SOURCE = "manual_session_profile"
_ALLOWED_SOURCES = frozenset(
    {DEFAULT_PROFILE_SOURCE, MANUAL_PROFILE_SOURCE}
)
_MAX_HR_BPM = 300.0
_FLOAT_TOLERANCE = 1e-12


DEFAULT_PROFILE_ROWS: tuple[dict[str, float | str], ...] = (
    {
        "zone": "Z1",
        "hr_low": 100.0,
        "hr_high": 125.0,
        "weight_low": 100.0,
        "weight_high": 120.0,
        "power": 1.00,
    },
    {
        "zone": "Z2",
        "hr_low": 126.0,
        "hr_high": 145.0,
        "weight_low": 120.0,
        "weight_high": 150.0,
        "power": 1.10,
    },
    {
        "zone": "Z3",
        "hr_low": 146.0,
        "hr_high": 162.0,
        "weight_low": 150.0,
        "weight_high": 220.0,
        "power": 1.15,
    },
    {
        "zone": "Z4",
        "hr_low": 163.0,
        "hr_high": 177.0,
        "weight_low": 220.0,
        "weight_high": 300.0,
        "power": 1.20,
    },
    {
        "zone": "Z5",
        "hr_low": 178.0,
        "hr_high": 195.0,
        "weight_low": 300.0,
        "weight_high": 420.0,
        "power": 1.10,
    },
)


@dataclass(frozen=True, slots=True)
class OnFlowsZone:
    zone: str
    hr_low: float
    hr_high: float
    weight_low: float
    weight_high: float
    power: float
    membership_low_bpm: float
    membership_high_bpm: float
    membership_lower_inclusive: bool
    membership_upper_inclusive: bool


@dataclass(frozen=True, slots=True)
class ProfileWarning:
    code: str
    message: str
    zone: str


@dataclass(frozen=True, slots=True)
class OnFlowsZoneProfile:
    schema_version: str
    source: str
    zones: tuple[OnFlowsZone, ...]
    warnings: tuple[ProfileWarning, ...]
    fingerprint: str
    split_points_bpm: tuple[float, ...]


def _finite_number(value: Any, field: str) -> float:
    if value is None or isinstance(value, bool) or not isinstance(value, Real):
        raise ValueError(f"{field} must be a finite number")
    rendered = float(value)
    if not math.isfinite(rendered):
        raise ValueError(f"{field} must be a finite number")
    return rendered


def _row_value(row: Any, key: str) -> Any:
    if isinstance(row, Mapping):
        return row.get(key)
    return getattr(row, key, None)


def _integer_adjacent(left: float, right: float) -> bool:
    return (
        math.isclose(left, round(left), abs_tol=_FLOAT_TOLERANCE)
        and math.isclose(right, round(right), abs_tol=_FLOAT_TOLERANCE)
        and math.isclose(right - left, 1.0, abs_tol=_FLOAT_TOLERANCE)
    )


def _fingerprint_payload(zones: Sequence[OnFlowsZone]) -> dict[str, Any]:
    return {
        "schema_version": PROFILE_SCHEMA_VERSION,
        "zones": [
            {
                "zone": zone.zone,
                "hr_low": zone.hr_low,
                "hr_high": zone.hr_high,
                "weight_low": zone.weight_low,
                "weight_high": zone.weight_high,
                "power": zone.power,
                "membership_low_bpm": zone.membership_low_bpm,
                "membership_high_bpm": zone.membership_high_bpm,
                "membership_lower_inclusive": (
                    zone.membership_lower_inclusive
                ),
                "membership_upper_inclusive": (
                    zone.membership_upper_inclusive
                ),
            }
            for zone in zones
        ],
    }


def _profile_fingerprint(zones: Sequence[OnFlowsZone]) -> str:
    canonical = json.dumps(
        _fingerprint_payload(zones),
        ensure_ascii=True,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def build_onflows_zone_profile(
    rows: Sequence[Any],
    *,
    source: str,
) -> OnFlowsZoneProfile:
    """Validate and canonicalize an arbitrary ordered profile."""

    if source not in _ALLOWED_SOURCES:
        raise ValueError("unsupported profile source")
    if not isinstance(rows, Sequence) or isinstance(
        rows, (str, bytes, bytearray)
    ):
        raise ValueError("profile rows must be an ordered sequence")
    if not rows:
        raise ValueError("at least one profile zone is required")

    parsed: list[dict[str, Any]] = []
    names: set[str] = set()
    previous_high: float | None = None
    for row in rows:
        raw_name = _row_value(row, "zone")
        name = raw_name.strip() if isinstance(raw_name, str) else ""
        if not name or name in names:
            raise ValueError("zone names must be non-empty and unique")
        hr_low = _finite_number(_row_value(row, "hr_low"), "hr_low")
        hr_high = _finite_number(_row_value(row, "hr_high"), "hr_high")
        weight_low = _finite_number(
            _row_value(row, "weight_low"), "weight_low"
        )
        weight_high = _finite_number(
            _row_value(row, "weight_high"), "weight_high"
        )
        power = _finite_number(_row_value(row, "power"), "power")
        if hr_low < 0 or hr_high > _MAX_HR_BPM or hr_low >= hr_high:
            raise ValueError("each zone requires 0 <= hr_low < hr_high <= 300")
        if previous_high is not None and hr_low <= previous_high:
            raise ValueError("profile zones must be ordered and non-overlapping")
        if weight_low <= 0:
            raise ValueError("weight_low must be positive")
        if weight_high < weight_low:
            raise ValueError("weight_high must be greater than or equal to weight_low")
        if not math.isfinite(weight_high / weight_low):
            raise ValueError("weight_high / weight_low must be finite")
        if power <= 0:
            raise ValueError("power must be positive")
        parsed.append(
            {
                "zone": name,
                "hr_low": hr_low,
                "hr_high": hr_high,
                "weight_low": weight_low,
                "weight_high": weight_high,
                "power": power,
            }
        )
        names.add(name)
        previous_high = hr_high

    membership_lows = [float(row["hr_low"]) for row in parsed]
    membership_highs = [float(row["hr_high"]) for row in parsed]
    lower_inclusive = [True] * len(parsed)
    upper_inclusive = [True] * len(parsed)
    for index in range(len(parsed) - 1):
        current_high = float(parsed[index]["hr_high"])
        next_low = float(parsed[index + 1]["hr_low"])
        if _integer_adjacent(current_high, next_low):
            divider = (current_high + next_low) / 2.0
            membership_highs[index] = divider
            membership_lows[index + 1] = divider
            upper_inclusive[index] = False
            lower_inclusive[index + 1] = True

    zones = tuple(
        OnFlowsZone(
            **row,
            membership_low_bpm=membership_lows[index],
            membership_high_bpm=membership_highs[index],
            membership_lower_inclusive=lower_inclusive[index],
            membership_upper_inclusive=upper_inclusive[index],
        )
        for index, row in enumerate(parsed)
    )
    warnings: list[ProfileWarning] = []
    for left, right in zip(zones, zones[1:]):
        if not math.isclose(
            left.weight_high,
            right.weight_low,
            rel_tol=0.0,
            abs_tol=_FLOAT_TOLERANCE,
        ):
            warnings.append(
                ProfileWarning(
                    code="interzone_weight_discontinuity",
                    message=(
                        "weight_high does not match the next zone's "
                        "weight_low; the profile may introduce a jump"
                    ),
                    zone=f"{left.zone}->{right.zone}",
                )
            )

    split_points = sorted(
        {
            value
            for zone in zones
            for value in (
                zone.hr_low,
                zone.hr_high,
                zone.membership_low_bpm,
                zone.membership_high_bpm,
            )
        }
    )
    return OnFlowsZoneProfile(
        schema_version=PROFILE_SCHEMA_VERSION,
        source=source,
        zones=zones,
        warnings=tuple(warnings),
        fingerprint=_profile_fingerprint(zones),
        split_points_bpm=tuple(split_points),
    )


def default_onflows_zone_profile() -> OnFlowsZoneProfile:
    return build_onflows_zone_profile(
        DEFAULT_PROFILE_ROWS,
        source=DEFAULT_PROFILE_SOURCE,
    )


def profile_edit_rows(
    profile: OnFlowsZoneProfile,
) -> list[dict[str, float | str]]:
    return [
        {
            "zone": zone.zone,
            "hr_low": zone.hr_low,
            "hr_high": zone.hr_high,
            "weight_low": zone.weight_low,
            "weight_high": zone.weight_high,
            "power": zone.power,
        }
        for zone in profile.zones
    ]


def safe_profile_dict(profile: OnFlowsZoneProfile) -> dict[str, Any]:
    """Return canonical, aggregate-safe profile configuration."""

    payload = _fingerprint_payload(profile.zones)
    return {
        **payload,
        "source": profile.source,
        "fingerprint": profile.fingerprint,
        "warnings": [
            {
                "code": warning.code,
                "message": warning.message,
                "zone": warning.zone,
            }
            for warning in profile.warnings
        ],
    }


def profile_from_safe_dict(value: Any) -> OnFlowsZoneProfile:
    if not isinstance(value, Mapping):
        raise ValueError("profile state is not a mapping")
    if value.get("schema_version") != PROFILE_SCHEMA_VERSION:
        raise ValueError("profile schema version is unsupported")
    source = value.get("source")
    zones = value.get("zones")
    if not isinstance(source, str) or not isinstance(zones, Sequence):
        raise ValueError("profile state is incomplete")
    return build_onflows_zone_profile(zones, source=source)
