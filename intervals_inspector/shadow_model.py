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
    INTRA_ZONE_EQUIVALENCE_VERSION,
    MANUAL_PROFILE_SOURCE,
    OnFlowsZoneProfile,
    build_onflows_zone_profile,
)


SHADOW_MODEL_VERSION = "real-data-shadow-physiology-v3-equivalent-time"
TREF_PROFILE_VERSION = "tref-fixed-expert-v1"
# Deprecated compatibility alias. There are no Tref bounds in this model.
TREF_BOUNDS_PROFILE_VERSION = TREF_PROFILE_VERSION
CONFIG_SCHEMA_VERSION = "shadow-model-config-v3-equivalent-time"
RESULT_SCHEMA_VERSION = "shadow-model-comparison-v3-equivalent-time"
HISTORY_WINDOW_DAYS = 40
LOW_HR_COVERAGE_PERCENT = 80.0
PROFILE_LEVELS = ("fixed",)
INITIAL_TREF_MINUTES = {
    "Z1": 300.0,
    "Z2": 180.0,
    "Z3": 70.0,
    "Z4": 20.0,
    "Z5": 20.0,
}

EDITABLE_FIELDS = (
    "equivalence_slope_pp_per_bpm",
    "spill_threshold_fraction",
    "spill_down_fraction",
    "spill_up_fraction",
)
READ_ONLY_FIELDS = (
    "tref_minutes",
    "profile_version",
    "equivalence_version",
    "tref_profile_version",
)
FIELD_UNITS = {
    "equivalence_slope_pp_per_bpm": "процентни пункта/удар/мин",
    "spill_threshold_fraction": "% от Tref",
    "spill_down_fraction": "% от превишението",
    "spill_up_fraction": "% от превишението",
    "tref_minutes": "приравнени минути",
    "profile_version": "версия",
    "equivalence_version": "версия",
    "tref_profile_version": "версия",
}
FIELD_RANGES = {
    "equivalence_slope_pp_per_bpm": (0.0, 100.0),
    "spill_threshold_fraction": (0.0, 1.0),
    "spill_down_fraction": (0.0, 1.0),
    "spill_up_fraction": (0.0, 1.0),
}


@dataclass(frozen=True, slots=True)
class ZoneModelSettings:
    zone: str
    hr_low: float
    hr_high: float
    equivalence_slope_pp_per_bpm: float
    spill_threshold_fraction: float
    spill_down_fraction: float
    spill_up_fraction: float
    tref_minutes: float


@dataclass(frozen=True, slots=True)
class ShadowModelConfiguration:
    schema_version: str
    physiology_profile_version: str
    equivalence_version: str
    tref_profile_version: str
    zones: tuple[ZoneModelSettings, ...]
    overrides: tuple[str, ...]
    fingerprint: str

    @property
    def is_experimental(self) -> bool:
        return bool(self.overrides)

    @property
    def tref_bounds_profile_version(self) -> str:
        """Deprecated name retained for aggregate-cache compatibility."""

        return self.tref_profile_version

    @property
    def profile_level(self) -> str:
        """Deprecated compatibility value; fixed Tref has no level."""

        return "fixed"


def _finite(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, Real):
        raise ValueError(f"{field} must be a finite number")
    rendered = float(value)
    if not math.isfinite(rendered):
        raise ValueError(f"{field} must be a finite number")
    return rendered


def _payload(
    zones: Sequence[ZoneModelSettings],
    overrides: Sequence[str],
) -> dict[str, Any]:
    return {
        "schema_version": CONFIG_SCHEMA_VERSION,
        "physiology_profile_version": SHADOW_MODEL_VERSION,
        "equivalence_version": INTRA_ZONE_EQUIVALENCE_VERSION,
        "tref_profile_version": TREF_PROFILE_VERSION,
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
    zones: Sequence[ZoneModelSettings],
    overrides: Sequence[str] = (),
) -> ShadowModelConfiguration:
    validate_zone_settings(zones)
    payload = _payload(zones, overrides)
    return ShadowModelConfiguration(
        schema_version=CONFIG_SCHEMA_VERSION,
        physiology_profile_version=SHADOW_MODEL_VERSION,
        equivalence_version=INTRA_ZONE_EQUIVALENCE_VERSION,
        tref_profile_version=TREF_PROFILE_VERSION,
        zones=tuple(zones),
        overrides=tuple(payload["overrides"]),
        fingerprint=_fingerprint(payload),
    )


def default_shadow_configuration() -> ShadowModelConfiguration:
    """Build the explicit baseline with fixed expert Tref values."""

    zones = []
    for row in DEFAULT_PROFILE_ROWS:
        zone = str(row["zone"])
        zones.append(
            ZoneModelSettings(
                zone=zone,
                hr_low=float(row["hr_low"]),
                hr_high=float(row["hr_high"]),
                equivalence_slope_pp_per_bpm=float(
                    row["equivalence_slope_pp_per_bpm"]
                ),
                spill_threshold_fraction=0.50,
                spill_down_fraction=0.20,
                spill_up_fraction=0.10,
                tref_minutes=INITIAL_TREF_MINUTES[zone],
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
        expected_tref = INITIAL_TREF_MINUTES.get(zone.zone)
        if expected_tref is None or not math.isclose(
            zone.tref_minutes,
            expected_tref,
            rel_tol=0.0,
            abs_tol=1e-12,
        ):
            raise ValueError(f"{zone.zone}.tref_minutes is a fixed expert value")


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


def configuration_with_profile_level(
    profile_level: str,
    *,
    baseline: ShadowModelConfiguration | None = None,
) -> ShadowModelConfiguration:
    """Deprecated no-op retained while old session state is discarded."""

    if profile_level not in {"fixed", "low", "medium", "high"}:
        raise ValueError("profile_level is unsupported")
    return baseline or default_shadow_configuration()


def configuration_to_safe_dict(
    configuration: ShadowModelConfiguration,
) -> dict[str, Any]:
    payload = _payload(
        configuration.zones,
        configuration.overrides,
    )
    return {**payload, "fingerprint": configuration.fingerprint}


def configuration_from_safe_dict(value: Any) -> ShadowModelConfiguration:
    if not isinstance(value, Mapping):
        raise ValueError("configuration state is not a mapping")
    if value.get("schema_version") != CONFIG_SCHEMA_VERSION:
        raise ValueError("configuration schema version is unsupported")
    if value.get("physiology_profile_version") != SHADOW_MODEL_VERSION:
        raise ValueError("physiology profile version is unsupported")
    if value.get("equivalence_version") != INTRA_ZONE_EQUIVALENCE_VERSION:
        raise ValueError("equivalence version is unsupported")
    if value.get("tref_profile_version") != TREF_PROFILE_VERSION:
        raise ValueError("Tref profile version is unsupported")
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
                tref_minutes=_finite(row.get("tref_minutes"), "tref_minutes"),
                **{
                    field: _finite(row.get(field), field)
                    for field in EDITABLE_FIELDS
                },
            )
        )
    overrides = value.get("overrides") or []
    if not isinstance(overrides, Sequence) or isinstance(overrides, (str, bytes, bytearray)):
        raise ValueError("configuration overrides are invalid")
    configuration = _build_configuration(
        zones,
        list(map(str, overrides)),
    )
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
        value = float(current.equivalence_slope_pp_per_bpm)
        if not math.isclose(
            value,
            initial.equivalence_slope_pp_per_bpm,
            abs_tol=1e-12,
        ):
            overrides[
                f"parameter.{current.zone}.equivalence_slope_pp_per_bpm"
            ] = value
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
                "equivalence_slope_pp_per_bpm": (
                    zone.equivalence_slope_pp_per_bpm
                ),
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
            elif field == "equivalence_version":
                initial_value = current_value = initial.equivalence_version
                source = "системна"
                version = initial.equivalence_version
            elif field == "tref_profile_version":
                initial_value = current_value = initial.tref_profile_version
                source = "профилна"
                version = initial.tref_profile_version
            else:
                initial_value = getattr(original, field)
                current_value = getattr(zone, field)
                source = (
                    "индивидуален override"
                    if item_id in configuration.overrides
                    else "профилна"
                    if field in {"equivalence_slope_pp_per_bpm", "tref_minutes"}
                    else "системна"
                )
                version = (
                    configuration.tref_profile_version
                    if field == "tref_minutes"
                    else configuration.equivalence_version
                    if field == "equivalence_slope_pp_per_bpm"
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
            configuration.tref_profile_version
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
) -> tuple[
    dict[str, float],
    dict[str, float | None],
    str,
    int,
    bool,
    str | None,
    str | None,
]:
    candidates = [row for row in history if isinstance(row, Mapping)]
    if activity_date is not None:
        oldest = activity_date - timedelta(days=HISTORY_WINDOW_DAYS)
        dated: dict[date, Mapping[str, Any]] = {}
        for row in candidates:
            raw_date = row.get("date")
            try:
                row_date = date.fromisoformat(str(raw_date)[:10])
            except ValueError:
                continue
            if oldest <= row_date < activity_date:
                dated[row_date] = row
        usable = [dated[row_date] for row_date in sorted(dated)]
    else:
        usable = candidates[-HISTORY_WINDOW_DAYS:]
    history_days = len(usable)
    source = "initial expert setting"
    fallback_used = False
    values = {zone.zone: zone.tref_minutes for zone in zones}
    historical_values: dict[str, float | None] = {}
    for zone in zones:
        if not usable:
            historical_values[zone.zone] = None
        else:
            samples = []
            for row in usable:
                raw = row.get(zone.zone, 0.0)
                if (
                    isinstance(raw, Real)
                    and not isinstance(raw, bool)
                    and math.isfinite(float(raw))
                ):
                    samples.append(max(0.0, float(raw)))
                else:
                    samples.append(0.0)
            historical = 7.0 * math.fsum(samples) / history_days
            values[zone.zone] = historical
            historical_values[zone.zone] = historical
    history_dates = [
        str(row.get("date"))[:10]
        for row in usable
        if row.get("date") is not None
    ]
    return (
        values,
        historical_values,
        source,
        history_days,
        fallback_used,
        min(history_dates) if history_dates else None,
        max(history_dates) if history_dates else None,
    )


def calculate_shadow_result(
    intrazone_analysis: Mapping[str, Any],
    configuration: ShadowModelConfiguration,
    *,
    prior_daily_effective_load: Sequence[Mapping[str, Any]] = (),
    prior_daily_equivalent_time: Sequence[Mapping[str, Any]] | None = None,
    prior_daily_qref: Sequence[Mapping[str, Any]] | None = None,
    activity_date: date | None = None,
) -> dict[str, Any]:
    """Calculate T_eq-driven cascade, spillover, effect, H40, and Tref."""

    # Deprecated keyword compatibility. Historical direct-dose names are
    # accepted only as aliases for the one E history used by H40/7-40.
    for legacy_history in (
        prior_daily_equivalent_time,
        prior_daily_qref,
    ):
        if legacy_history is None:
            continue
        if prior_daily_effective_load:
            raise ValueError("provide only one effective-load history")
        prior_daily_effective_load = legacy_history

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
    def source_equivalent_seconds(zone: str) -> float:
        row = source_rows.get(zone, {})
        raw = (
            row.get("equivalent_seconds")
            if "equivalent_seconds" in row
            else row.get("qref_seconds")
        )
        return max(0.0, float(raw or 0.0))

    equivalent_time = {
        zone: source_equivalent_seconds(zone) / 60.0
        for zone in settings
    }
    mean_effective_hr = {
        zone: source_rows.get(zone, {}).get("mean_effective_hr_bpm")
        for zone in settings
    }
    average_minute_value = {
        zone: source_rows.get(zone, {}).get(
            "average_minute_value_percent"
        )
        for zone in settings
    }
    (
        tref_raw,
        tref_history_value,
        tref_source,
        history_days,
        fallback_used,
        history_period_start,
        history_period_end,
    ) = _tref_values(
        configuration.zones,
        prior_daily_effective_load,
        activity_date=activity_date,
    )
    tref_effective = {
        zone: config.tref_minutes for zone, config in settings.items()
    }

    zone_order = list(settings)
    cascade = {
        receiver: math.fsum(
            equivalent_time[source] for source in zone_order[index + 1 :]
        )
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
            equivalent_time[zone]
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
                "T_eq_z": equivalent_time[zone],
                "mean_effective_hr_bpm": mean_effective_hr[zone],
                "average_minute_value_percent": average_minute_value[zone],
                "direct_ratio": equivalent_time[zone]
                / max(tref_effective[zone], 1e-12),
                "cascade": cascade[zone],
                "spillover_excess": excess[zone],
                "spillover_down_out": spill_down_out[zone],
                "spillover_up_out": spill_up_out[zone],
                "spillover_received": spill_received[zone],
                "E_z": equivalent_time[zone]
                + cascade[zone]
                + spill_received[zone],
                "tref_raw": tref_raw[zone],
                "tref_effective": tref_effective[zone],
                "tref_history_value": tref_history_value[zone],
                "h40_equivalent_minutes": tref_history_value[zone],
                "tref_source": tref_source,
                "tref_history_days": history_days,
                # Deprecated aliases. Both point to the one T_eq dose.
                "Q_z": equivalent_time[zone],
                "Qref_z": equivalent_time[zone],
            }
        )
    warnings = []
    if history_days < HISTORY_WINDOW_DAYS:
        warnings.append(
            {
                "id": "warning.incomplete_history",
                "message": (
                    "Налични предходни дни за диагностичния H40: "
                    f"{history_days}/{HISTORY_WINDOW_DAYS}. Фиксираният Tref "
                    "не се променя."
                ),
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
        "equivalence_version": configuration.equivalence_version,
        "tref_profile_version": configuration.tref_profile_version,
        "tref_bounds_profile_version": configuration.tref_bounds_profile_version,
        "configuration_fingerprint": configuration.fingerprint,
        "experimental": configuration.is_experimental,
        "profile_level": configuration.profile_level,
        "tref_source": tref_source,
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
    precomputed_experimental_analysis: Mapping[str, Any] | None = None,
    experimental_configuration: ShadowModelConfiguration | None = None,
    prior_baseline_effective_load: Sequence[Mapping[str, Any]] = (),
    prior_experimental_effective_load: Sequence[Mapping[str, Any]] = (),
    prior_baseline_qref: Sequence[Mapping[str, Any]] | None = None,
    prior_experimental_qref: Sequence[Mapping[str, Any]] | None = None,
    activity_date: date | None = None,
) -> dict[str, Any]:
    if prior_baseline_qref is not None:
        if prior_baseline_effective_load:
            raise ValueError("provide only one baseline effective-load history")
        prior_baseline_effective_load = prior_baseline_qref
    if prior_experimental_qref is not None:
        if prior_experimental_effective_load:
            raise ValueError(
                "provide only one experimental effective-load history"
            )
        prior_experimental_effective_load = prior_experimental_qref
    baseline_configuration = default_shadow_configuration()
    experimental = experimental_configuration or baseline_configuration
    baseline_profile = profile_from_configuration(baseline_configuration)
    experimental_profile = profile_from_configuration(experimental)
    if precomputed_experimental_analysis is None:
        experimental_analysis = calculate_onflows_intrazone_load(
            interval_result,
            experimental_profile,
        )
    else:
        analysis_fingerprint = precomputed_experimental_analysis.get(
            "profile_fingerprint"
        )
        if analysis_fingerprint != experimental_profile.fingerprint:
            raise ValueError(
                "precomputed equivalent-time analysis does not match "
                "the experimental profile"
            )
        experimental_analysis = precomputed_experimental_analysis
    # The governing/experimental T_eq is calculated exactly once.  A distinct
    # baseline integration exists only when the explicit diagnostic comparison
    # uses a genuinely different HR-equivalence profile.
    if baseline_profile.fingerprint == experimental_profile.fingerprint:
        baseline_analysis = experimental_analysis
        intrazone_calculation_count = 1
    else:
        baseline_analysis = calculate_onflows_intrazone_load(
            interval_result,
            baseline_profile,
        )
        intrazone_calculation_count = 2
    baseline_result = calculate_shadow_result(
        baseline_analysis,
        baseline_configuration,
        prior_daily_effective_load=prior_baseline_effective_load,
        activity_date=activity_date,
    )
    experimental_result = calculate_shadow_result(
        experimental_analysis,
        experimental,
        prior_daily_effective_load=prior_experimental_effective_load,
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
                        "T_z", "T_eq_z", "Q_z", "Qref_z", "direct_ratio", "cascade", "spillover_received", "E_z", "tref_raw", "tref_effective"
                    )
                },
                **{
                    f"experimental_{field}": current[field]
                    for field in (
                        "T_z", "T_eq_z", "Q_z", "Qref_z", "direct_ratio", "cascade", "spillover_received", "E_z", "tref_raw", "tref_effective"
                    )
                },
                **{
                    f"delta_{field}": current[field] - original[field]
                    for field in (
                        "T_z", "T_eq_z", "Q_z", "Qref_z", "direct_ratio", "cascade", "spillover_received", "E_z", "tref_raw", "tref_effective"
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
        "intrazone_calculation_count": intrazone_calculation_count,
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


def _deprecated_dose_alias_key(key: Any) -> bool:
    rendered = str(key).casefold()
    return (
        "qref" in rendered
        or rendered in {
            "q",
            "q_z",
            "q_min",
            "weighted_seconds",
            "weighted_minutes",
            "total_weighted_sec",
            "average_k",
            "overall_average_k",
        }
        or rendered.endswith("_q_z")
    )


def _public_diagnostics_copy(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): _public_diagnostics_copy(child)
            for key, child in value.items()
            if not _deprecated_dose_alias_key(key)
        }
    if isinstance(value, Sequence) and not isinstance(
        value, (str, bytes, bytearray)
    ):
        return [_public_diagnostics_copy(child) for child in value]
    return value


def export_shadow_diagnostics_json(comparison: Mapping[str, Any]) -> str:
    """Serialize only the aggregate model payload and fail closed on secrets."""

    if _contains_sensitive_key(comparison):
        raise ValueError("sensitive data is not allowed in shadow diagnostics")
    public_comparison = _public_diagnostics_copy(comparison)
    return json.dumps(
        public_comparison,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        indent=2,
    )
