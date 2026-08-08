"""In-memory bridge from real interval HR aggregates to the shadow model.

Nothing in this module persists data or changes the main demonstrator.  It
operates on privacy-minimized interval objects and returns aggregate-only data.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import date, timedelta
import hashlib
import json
import math
from numbers import Real
from typing import Any, Mapping, Sequence

from biathlon.constants import DEFAULT_BASE_LOADS
from intervals_inspector.model_registry import (
    RESULT_DEFINITIONS,
    WARNING_DEFINITIONS,
    parameter_definition,
)
from intervals_inspector.onflows_intrazone_load import (
    calculate_onflows_intrazone_load,
)
from intervals_inspector.onflows_zone_profile import (
    DEFAULT_PROFILE_ROWS,
    MANUAL_PROFILE_SOURCE,
    OnFlowsZoneProfile,
    build_onflows_zone_profile,
)


SHADOW_MODEL_VERSION = "real-data-shadow-physiology-v1"
TREF_BOUNDS_PROFILE_VERSION = "tref-bounds-safety-v1-uncalibrated"
CONFIG_SCHEMA_VERSION = "shadow-model-config-v1"
RESULT_SCHEMA_VERSION = "shadow-model-comparison-v1"
MAX_TREF_MINUTES_PER_WEEK = 7.0 * 24.0 * 60.0
HISTORY_WINDOW_DAYS = 40
LOW_HR_COVERAGE_PERCENT = 80.0

EDITABLE_FIELDS = (
    "weight_low",
    "weight_high",
    "power",
    "spill_threshold_fraction",
    "spill_down_fraction",
    "spill_up_fraction",
    "tref_min",
    "tref_max",
    "bounds_factor",
)
READ_ONLY_FIELDS = ("profile_version", "tref_bounds_profile_version")
FIELD_UNITS = {
    "weight_low": "относителна тежест",
    "weight_high": "относителна тежест",
    "power": "без единица",
    "spill_threshold_fraction": "% от Tref",
    "spill_down_fraction": "% от превишението",
    "spill_up_fraction": "% от превишението",
    "tref_min": "еквивалентни минути/седмица",
    "tref_max": "еквивалентни минути/седмица",
    "bounds_factor": "без единица",
    "profile_version": "версия",
    "tref_bounds_profile_version": "версия",
}
FIELD_RANGES = {
    "weight_low": (1.0, 1000.0),
    "weight_high": (1.0, 2000.0),
    "power": (0.2, 4.0),
    "spill_threshold_fraction": (0.0, 1.0),
    "spill_down_fraction": (0.0, 1.0),
    "spill_up_fraction": (0.0, 1.0),
    "tref_min": (0.0, MAX_TREF_MINUTES_PER_WEEK),
    "tref_max": (0.0, MAX_TREF_MINUTES_PER_WEEK),
    "bounds_factor": (0.5, 1.5),
}


@dataclass(frozen=True, slots=True)
class ZoneModelSettings:
    zone: str
    hr_low: float
    hr_high: float
    weight_low: float
    weight_high: float
    power: float
    spill_threshold_fraction: float
    spill_down_fraction: float
    spill_up_fraction: float
    tref_min: float
    tref_max: float
    bounds_factor: float


@dataclass(frozen=True, slots=True)
class ShadowModelConfiguration:
    schema_version: str
    physiology_profile_version: str
    tref_bounds_profile_version: str
    zones: tuple[ZoneModelSettings, ...]
    overrides: tuple[str, ...]
    fingerprint: str

    @property
    def is_experimental(self) -> bool:
        return bool(self.overrides)


def _finite(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, Real):
        raise ValueError(f"{field} must be a finite number")
    rendered = float(value)
    if not math.isfinite(rendered):
        raise ValueError(f"{field} must be a finite number")
    return rendered


def _payload(
    zones: Sequence[ZoneModelSettings], overrides: Sequence[str]
) -> dict[str, Any]:
    return {
        "schema_version": CONFIG_SCHEMA_VERSION,
        "physiology_profile_version": SHADOW_MODEL_VERSION,
        "tref_bounds_profile_version": TREF_BOUNDS_PROFILE_VERSION,
        "zones": [
            {field: getattr(zone, field) for field in zone.__dataclass_fields__}
            for zone in zones
        ],
        "overrides": sorted(set(map(str, overrides))),
    }


def _fingerprint(payload: Mapping[str, Any]) -> str:
    canonical = json.dumps(
        payload,
        ensure_ascii=True,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _build_configuration(
    zones: Sequence[ZoneModelSettings], overrides: Sequence[str] = ()
) -> ShadowModelConfiguration:
    validate_zone_settings(zones)
    payload = _payload(zones, overrides)
    return ShadowModelConfiguration(
        schema_version=CONFIG_SCHEMA_VERSION,
        physiology_profile_version=SHADOW_MODEL_VERSION,
        tref_bounds_profile_version=TREF_BOUNDS_PROFILE_VERSION,
        zones=tuple(zones),
        overrides=tuple(payload["overrides"]),
        fingerprint=_fingerprint(payload),
    )


def default_shadow_configuration() -> ShadowModelConfiguration:
    """Build the explicit baseline, including uncalibrated safety bounds."""

    zones = []
    for row in DEFAULT_PROFILE_ROWS:
        zone = str(row["zone"])
        # These are transparent safety bounds, not claimed physiological
        # limits.  The main model's visible base load remains the fallback for
        # tref_raw when no prior real-data history is available.
        zones.append(
            ZoneModelSettings(
                zone=zone,
                hr_low=float(row["hr_low"]),
                hr_high=float(row["hr_high"]),
                weight_low=float(row["weight_low"]),
                weight_high=float(row["weight_high"]),
                power=float(row["power"]),
                spill_threshold_fraction=0.50,
                spill_down_fraction=0.20,
                spill_up_fraction=0.10,
                tref_min=0.0,
                tref_max=MAX_TREF_MINUTES_PER_WEEK,
                bounds_factor=1.0,
            )
        )
    return _build_configuration(zones)


def validate_zone_settings(zones: Sequence[ZoneModelSettings]) -> None:
    if not zones:
        raise ValueError("at least one zone is required")
    names: set[str] = set()
    previous_high: float | None = None
    for zone in zones:
        if not zone.zone or zone.zone in names:
            raise ValueError("zone names must be non-empty and unique")
        names.add(zone.zone)
        if zone.hr_low < 0 or zone.hr_high > 300 or zone.hr_low >= zone.hr_high:
            raise ValueError(f"{zone.zone}: invalid HR bounds")
        if previous_high is not None and zone.hr_low <= previous_high:
            raise ValueError("zones must be ordered and non-overlapping")
        previous_high = zone.hr_high
        for field, (minimum, maximum) in FIELD_RANGES.items():
            value = _finite(getattr(zone, field), field)
            if value < minimum or value > maximum:
                raise ValueError(
                    f"{zone.zone}.{field} must be between {minimum} and {maximum}"
                )
        if zone.weight_high < zone.weight_low:
            raise ValueError(f"{zone.zone}.weight_high must be >= weight_low")
        if zone.tref_max < zone.tref_min:
            raise ValueError(f"{zone.zone}.tref_max must be >= tref_min")


def configuration_with_overrides(
    overrides: Mapping[str, Any],
    *,
    baseline: ShadowModelConfiguration | None = None,
) -> ShadowModelConfiguration:
    """Apply ``parameter.Zn.field`` overrides with strict allow-listing."""

    initial = baseline or default_shadow_configuration()
    by_zone = {
        zone.zone: {
            field: getattr(zone, field) for field in zone.__dataclass_fields__
        }
        for zone in initial.zones
    }
    changed: list[str] = []
    for item_id, raw_value in overrides.items():
        parts = str(item_id).split(".")
        if len(parts) != 3 or parts[0] != "parameter":
            raise ValueError(f"unsupported override identifier: {item_id}")
        _, zone_name, field = parts
        if zone_name not in by_zone:
            raise ValueError(f"unknown zone: {zone_name}")
        if field not in EDITABLE_FIELDS:
            raise ValueError(f"parameter is visible but read-only: {item_id}")
        value = _finite(raw_value, field)
        by_zone[zone_name][field] = value
        original = getattr(next(z for z in initial.zones if z.zone == zone_name), field)
        if not math.isclose(value, float(original), rel_tol=0.0, abs_tol=1e-12):
            changed.append(str(item_id))
    return _build_configuration(
        [ZoneModelSettings(**by_zone[zone.zone]) for zone in initial.zones],
        changed,
    )


def configuration_to_safe_dict(
    configuration: ShadowModelConfiguration,
) -> dict[str, Any]:
    payload = _payload(configuration.zones, configuration.overrides)
    return {**payload, "fingerprint": configuration.fingerprint}


def configuration_from_safe_dict(value: Any) -> ShadowModelConfiguration:
    if not isinstance(value, Mapping):
        raise ValueError("configuration state is not a mapping")
    if value.get("schema_version") != CONFIG_SCHEMA_VERSION:
        raise ValueError("configuration schema version is unsupported")
    if value.get("physiology_profile_version") != SHADOW_MODEL_VERSION:
        raise ValueError("physiology profile version is unsupported")
    if value.get("tref_bounds_profile_version") != TREF_BOUNDS_PROFILE_VERSION:
        raise ValueError("Tref bounds profile version is unsupported")
    rows = value.get("zones")
    if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes, bytearray)):
        raise ValueError("configuration zones are missing")
    zones = []
    for row in rows:
        if not isinstance(row, Mapping):
            raise ValueError("configuration zone is invalid")
        zones.append(
            ZoneModelSettings(
                zone=str(row.get("zone") or ""),
                hr_low=_finite(row.get("hr_low"), "hr_low"),
                hr_high=_finite(row.get("hr_high"), "hr_high"),
                **{
                    field: _finite(row.get(field), field)
                    for field in EDITABLE_FIELDS
                },
            )
        )
    overrides = value.get("overrides") or []
    if not isinstance(overrides, Sequence) or isinstance(overrides, (str, bytes, bytearray)):
        raise ValueError("configuration overrides are invalid")
    configuration = _build_configuration(zones, list(map(str, overrides)))
    expected = value.get("fingerprint")
    if expected is not None and expected != configuration.fingerprint:
        raise ValueError("configuration fingerprint mismatch")
    return configuration


def configuration_from_profile(
    profile: OnFlowsZoneProfile,
) -> ShadowModelConfiguration:
    """Compatibility adapter for the existing session-only profile editor."""

    baseline = default_shadow_configuration()
    if len(profile.zones) != len(baseline.zones):
        raise ValueError("profile zone count does not match the shadow model")
    overrides: dict[str, float] = {}
    for current, initial in zip(profile.zones, baseline.zones):
        if current.zone != initial.zone:
            raise ValueError("profile zones do not match the shadow model")
        for source_field, target_field in (
            ("weight_low", "weight_low"),
            ("weight_high", "weight_high"),
            ("power", "power"),
        ):
            value = float(getattr(current, source_field))
            if not math.isclose(value, getattr(initial, target_field), abs_tol=1e-12):
                overrides[f"parameter.{current.zone}.{target_field}"] = value
    return configuration_with_overrides(overrides, baseline=baseline)


def profile_from_configuration(
    configuration: ShadowModelConfiguration,
) -> OnFlowsZoneProfile:
    return build_onflows_zone_profile(
        [
            {
                "zone": zone.zone,
                "hr_low": zone.hr_low,
                "hr_high": zone.hr_high,
                "weight_low": zone.weight_low,
                "weight_high": zone.weight_high,
                "power": zone.power,
            }
            for zone in configuration.zones
        ],
        source=MANUAL_PROFILE_SOURCE,
    )


def build_model_registry(
    configuration: ShadowModelConfiguration,
    *,
    baseline: ShadowModelConfiguration | None = None,
) -> dict[str, dict[str, Any]]:
    initial = baseline or default_shadow_configuration()
    initial_by_zone = {zone.zone: zone for zone in initial.zones}
    registry: dict[str, dict[str, Any]] = {}
    for zone in configuration.zones:
        original = initial_by_zone[zone.zone]
        for field in (*EDITABLE_FIELDS, *READ_ONLY_FIELDS):
            item_id = f"parameter.{zone.zone}.{field}"
            if field == "profile_version":
                initial_value = current_value = initial.physiology_profile_version
                source = "системна"
                version = initial.physiology_profile_version
            elif field == "tref_bounds_profile_version":
                initial_value = current_value = initial.tref_bounds_profile_version
                source = "профилна"
                version = initial.tref_bounds_profile_version
            else:
                initial_value = getattr(original, field)
                current_value = getattr(zone, field)
                source = (
                    "индивидуален override"
                    if item_id in configuration.overrides
                    else "профилна"
                    if field in {"weight_low", "weight_high", "power", "tref_min", "tref_max", "bounds_factor"}
                    else "системна"
                )
                version = (
                    configuration.tref_bounds_profile_version
                    if field in {"tref_min", "tref_max", "bounds_factor"}
                    else configuration.physiology_profile_version
                )
            definition = parameter_definition(
                zone.zone,
                field,
                initial_value=initial_value,
                current_value=current_value,
                value_source=source,
                version=version,
            )
            registry[item_id] = definition

    for item_id, template in {**RESULT_DEFINITIONS, **WARNING_DEFINITIONS}.items():
        definition = deepcopy(template)
        definition["version"] = (
            configuration.tref_bounds_profile_version
            if "tref" in item_id or "history" in item_id
            else configuration.physiology_profile_version
        )
        registry[item_id] = definition
    return registry


def _tref_values(
    zones: Sequence[ZoneModelSettings],
    history: Sequence[Mapping[str, Any]],
    *,
    activity_date: date | None = None,
) -> tuple[dict[str, float], int, bool, str | None, str | None]:
    candidates = [row for row in history if isinstance(row, Mapping)]
    if activity_date is not None:
        oldest = activity_date - timedelta(days=HISTORY_WINDOW_DAYS)
        dated: list[tuple[date, Mapping[str, Any]]] = []
        for row in candidates:
            raw_date = row.get("date")
            try:
                row_date = date.fromisoformat(str(raw_date)[:10])
            except ValueError:
                continue
            if oldest <= row_date < activity_date:
                dated.append((row_date, row))
        dated.sort(key=lambda item: item[0])
        usable = [row for _row_date, row in dated]
    else:
        usable = candidates[-HISTORY_WINDOW_DAYS:]
    history_days = len(usable)
    fallback_used = history_days == 0
    values: dict[str, float] = {}
    for zone in zones:
        samples = []
        for row in usable:
            raw = row.get(zone.zone)
            if isinstance(raw, Real) and not isinstance(raw, bool) and math.isfinite(float(raw)):
                samples.append(max(0.0, float(raw)))
        if samples:
            values[zone.zone] = 7.0 * math.fsum(samples) / len(samples)
        else:
            values[zone.zone] = 7.0 * float(DEFAULT_BASE_LOADS.get(zone.zone, 0.0))
    history_dates = [
        str(row.get("date"))[:10]
        for row in usable
        if row.get("date") is not None
    ]
    return (
        values,
        history_days,
        fallback_used,
        min(history_dates) if history_dates else None,
        max(history_dates) if history_dates else None,
    )


def calculate_shadow_result(
    intrazone_analysis: Mapping[str, Any],
    configuration: ShadowModelConfiguration,
    *,
    prior_daily_effective: Sequence[Mapping[str, Any]] = (),
    activity_date: date | None = None,
) -> dict[str, Any]:
    """Calculate transparent T/Q/cascade/spill/E/Tref aggregates."""

    settings = {zone.zone: zone for zone in configuration.zones}
    source_rows = {
        str(row.get("zone")): row
        for row in intrazone_analysis.get("zones", [])
        if isinstance(row, Mapping)
    }
    t = {
        zone: max(0.0, float(source_rows.get(zone, {}).get("real_seconds") or 0.0)) / 60.0
        for zone in settings
    }
    q = {
        zone: max(0.0, float(source_rows.get(zone, {}).get("weighted_seconds") or 0.0)) / 60.0
        for zone in settings
    }
    (
        tref_raw,
        history_days,
        fallback_used,
        history_period_start,
        history_period_end,
    ) = _tref_values(
        configuration.zones,
        prior_daily_effective,
        activity_date=activity_date,
    )
    tref_effective: dict[str, float] = {}
    bound_applied: dict[str, str] = {}
    for zone, config in settings.items():
        lower = config.bounds_factor * config.tref_min
        upper = config.bounds_factor * config.tref_max
        raw = tref_raw[zone]
        tref_effective[zone] = min(max(raw, lower), upper)
        bound_applied[zone] = "lower" if raw < lower else "upper" if raw > upper else "none"

    zone_order = list(settings)
    cascade = {
        receiver: math.fsum(q[source] for source in zone_order[index + 1 :])
        for index, receiver in enumerate(zone_order)
    }
    spill_down_out = {zone: 0.0 for zone in zone_order}
    spill_up_out = {zone: 0.0 for zone in zone_order}
    spill_received = {zone: 0.0 for zone in zone_order}
    excess = {zone: 0.0 for zone in zone_order}
    for index, zone in enumerate(zone_order):
        config = settings[zone]
        excess[zone] = max(
            0.0,
            q[zone]
            - config.spill_threshold_fraction * tref_effective[zone],
        )
        if index > 0:
            amount = config.spill_down_fraction * excess[zone]
            spill_down_out[zone] = amount
            spill_received[zone_order[index - 1]] += amount
        if index < len(zone_order) - 1:
            amount = config.spill_up_fraction * excess[zone]
            spill_up_out[zone] = amount
            spill_received[zone_order[index + 1]] += amount

    coverage = float(intrazone_analysis.get("hr_coverage_percent") or 0.0)
    rows = []
    for zone in zone_order:
        rows.append(
            {
                "zone": zone,
                "T_z": t[zone],
                "Q_z": q[zone],
                "cascade": cascade[zone],
                "spillover_excess": excess[zone],
                "spillover_down_out": spill_down_out[zone],
                "spillover_up_out": spill_up_out[zone],
                "spillover_received": spill_received[zone],
                "E_z": q[zone] + cascade[zone] + spill_received[zone],
                "tref_raw": tref_raw[zone],
                "tref_effective": tref_effective[zone],
                "tref_min_effective": settings[zone].bounds_factor * settings[zone].tref_min,
                "tref_max_effective": settings[zone].bounds_factor * settings[zone].tref_max,
                "tref_bound_applied": bound_applied[zone],
            }
        )
    warnings = []
    if history_days < HISTORY_WINDOW_DAYS:
        warnings.append(
            {
                "id": "warning.incomplete_history",
                "message": f"Налични предходни исторически дни: {history_days}/{HISTORY_WINDOW_DAYS}.",
            }
        )
    if coverage < LOW_HR_COVERAGE_PERCENT:
        warnings.append(
            {
                "id": "warning.low_hr_coverage",
                "message": f"HR покритието е {coverage:.1f}% (под {LOW_HR_COVERAGE_PERCENT:.0f}%).",
            }
        )
    if configuration.is_experimental:
        warnings.append(
            {
                "id": "warning.experimental",
                "message": "Използва се временна експериментална конфигурация само в shadow режима.",
            }
        )
    return {
        "model_version": configuration.physiology_profile_version,
        "tref_bounds_profile_version": configuration.tref_bounds_profile_version,
        "configuration_fingerprint": configuration.fingerprint,
        "experimental": configuration.is_experimental,
        "history_days": history_days,
        "history_window_days": HISTORY_WINDOW_DAYS,
        "history_period_start": history_period_start,
        "history_period_end": history_period_end,
        "current_day_excluded": activity_date is not None,
        "history_fallback_used": fallback_used,
        "hr_coverage_percent": coverage,
        "rows": rows,
        "warnings": warnings,
    }


def calculate_shadow_comparison(
    interval_result: Any,
    *,
    experimental_configuration: ShadowModelConfiguration | None = None,
    prior_baseline_effective: Sequence[Mapping[str, Any]] = (),
    prior_experimental_effective: Sequence[Mapping[str, Any]] = (),
    activity_date: date | None = None,
) -> dict[str, Any]:
    baseline_configuration = default_shadow_configuration()
    experimental = experimental_configuration or baseline_configuration
    baseline_analysis = calculate_onflows_intrazone_load(
        interval_result, profile_from_configuration(baseline_configuration)
    )
    experimental_analysis = calculate_onflows_intrazone_load(
        interval_result, profile_from_configuration(experimental)
    )
    baseline_result = calculate_shadow_result(
        baseline_analysis,
        baseline_configuration,
        prior_daily_effective=prior_baseline_effective,
        activity_date=activity_date,
    )
    experimental_result = calculate_shadow_result(
        experimental_analysis,
        experimental,
        prior_daily_effective=prior_experimental_effective,
        activity_date=activity_date,
    )
    baseline_rows = {row["zone"]: row for row in baseline_result["rows"]}
    comparison_rows = []
    for current in experimental_result["rows"]:
        original = baseline_rows[current["zone"]]
        comparison_rows.append(
            {
                "zone": current["zone"],
                **{
                    f"baseline_{field}": original[field]
                    for field in (
                        "T_z", "Q_z", "cascade", "spillover_received", "E_z", "tref_raw", "tref_effective"
                    )
                },
                **{
                    f"experimental_{field}": current[field]
                    for field in (
                        "T_z", "Q_z", "cascade", "spillover_received", "E_z", "tref_raw", "tref_effective"
                    )
                },
                **{
                    f"delta_{field}": current[field] - original[field]
                    for field in (
                        "T_z", "Q_z", "cascade", "spillover_received", "E_z", "tref_raw", "tref_effective"
                    )
                },
            }
        )
    registry = build_model_registry(experimental, baseline=baseline_configuration)
    return {
        "schema_version": RESULT_SCHEMA_VERSION,
        "memory_only": True,
        "affects_main_demonstrator": False,
        "persistence_backend": None,
        "baseline_configuration": configuration_to_safe_dict(baseline_configuration),
        "experimental_configuration": configuration_to_safe_dict(experimental),
        "baseline": baseline_result,
        "experimental": experimental_result,
        "comparison_rows": comparison_rows,
        "registry": registry,
    }


def reset_shadow_configuration() -> ShadowModelConfiguration:
    return default_shadow_configuration()


def _contains_sensitive_key(value: Any) -> bool:
    from intervals_inspector.model_registry import is_sensitive_identifier

    if isinstance(value, Mapping):
        return any(
            is_sensitive_identifier(str(key)) or _contains_sensitive_key(item)
            for key, item in value.items()
        )
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return any(_contains_sensitive_key(item) for item in value)
    return False


def export_shadow_diagnostics_json(comparison: Mapping[str, Any]) -> str:
    """Serialize only the aggregate model payload and fail closed on secrets."""

    if _contains_sensitive_key(comparison):
        raise ValueError("sensitive data is not allowed in shadow diagnostics")
    return json.dumps(
        comparison,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        indent=2,
    )
