"""Post-hoc reference evaluation for an already-computed HRmod result.

This module has no dependency on the HRmod computation functions.  It takes a
deep snapshot of a completed core result, records its hash/fingerprint, and
only then joins separate reference rows by timestamp.  No function here can
recalculate or mutate HRmod.
"""

from __future__ import annotations

from bisect import bisect_left
import copy
from dataclasses import dataclass, fields, is_dataclass
from datetime import UTC, datetime
import hashlib
import json
import math
from statistics import mean
from types import MappingProxyType
from typing import Any, Iterable, Mapping, Sequence

from .tcx_adapter import ReferenceChannels


class ReferenceValidationError(ValueError):
    """Raised for an invalid post-hoc validation request."""


@dataclass(frozen=True, slots=True)
class ReferenceZone:
    """One external numeric zone with lower-inclusive/upper-exclusive bounds."""

    label: str
    lower: float
    upper: float | None = None

    def __post_init__(self) -> None:
        if not self.label.strip():
            raise ValueError("Reference zone label cannot be empty")
        if not math.isfinite(float(self.lower)):
            raise ValueError("Reference zone lower bound must be finite")
        if self.upper is not None:
            if not math.isfinite(float(self.upper)):
                raise ValueError("Reference zone upper bound must be finite")
            if self.upper <= self.lower:
                raise ValueError("Reference zone upper bound must exceed lower bound")


@dataclass(frozen=True, slots=True)
class ReferenceValidationConfig:
    """Explicit opt-ins for interpreting independent reference channels."""

    join_tolerance_s: float = 0.51
    sport: str | None = None
    enable_quantitative_power: bool = False
    power_source: str | None = None
    power_zones: tuple[ReferenceZone, ...] = ()
    enable_controlled_treadmill_speed: bool = False
    treadmill_grade_verified: bool = False
    speed_zones: tuple[ReferenceZone, ...] = ()
    external_zone_field: str | None = None
    use_annotation_zones: bool = False
    high_zone_labels: tuple[str, ...] = ("Z4", "Z5")
    max_lag_s: int = 120
    lag_step_s: int = 1

    def __post_init__(self) -> None:
        if self.join_tolerance_s < 0:
            raise ValueError("join_tolerance_s cannot be negative")
        if self.max_lag_s < 0:
            raise ValueError("max_lag_s cannot be negative")
        if self.lag_step_s <= 0:
            raise ValueError("lag_step_s must be positive")
        _validate_zones(self.power_zones, "power_zones")
        _validate_zones(self.speed_zones, "speed_zones")
        if self.enable_quantitative_power and (
            not self.power_source or not self.power_zones
        ):
            raise ValueError(
                "Quantitative power requires an explicit power_source and power_zones"
            )
        if self.enable_controlled_treadmill_speed and (
            not self.treadmill_grade_verified or not self.speed_zones
        ):
            raise ValueError(
                "Controlled treadmill speed requires verified grade and speed_zones"
            )

    def to_dict(self) -> dict[str, Any]:
        return _plain(self)


@dataclass(frozen=True, slots=True)
class ReferenceValidationResult:
    """Serializable post-hoc result that contains no mutable core object."""

    hr_input_hash: str
    model_version: str
    core_result_fingerprint: str
    aligned_timeseries: tuple[Mapping[str, Any], ...]
    metrics: Mapping[str, Any]
    confusion_matrices: Mapping[str, Any]
    lag_diagnostics: Mapping[str, Any]
    annotation_summaries: tuple[Mapping[str, Any], ...]
    flags: tuple[str, ...]
    interpretation: str
    suitable_for_intensity: bool
    reference_config: Mapping[str, Any]

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "aligned_timeseries",
            tuple(_freeze_mapping(row) for row in self.aligned_timeseries),
        )
        object.__setattr__(self, "metrics", _freeze_mapping(self.metrics))
        object.__setattr__(
            self, "confusion_matrices", _freeze_mapping(self.confusion_matrices)
        )
        object.__setattr__(
            self, "lag_diagnostics", _freeze_mapping(self.lag_diagnostics)
        )
        object.__setattr__(
            self,
            "annotation_summaries",
            tuple(_freeze_mapping(row) for row in self.annotation_summaries),
        )
        object.__setattr__(
            self, "reference_config", _freeze_mapping(self.reference_config)
        )

    @property
    def annotation_summary(self) -> tuple[Mapping[str, Any], ...]:
        """Singular-name compatibility alias used by simple UIs."""

        return self.annotation_summaries

    def to_dict(self) -> dict[str, Any]:
        return {
            "hr_input_hash": self.hr_input_hash,
            "model_version": self.model_version,
            "core_result_fingerprint": self.core_result_fingerprint,
            "aligned_timeseries": [_plain(row) for row in self.aligned_timeseries],
            "metrics": _plain(self.metrics),
            "confusion_matrices": _plain(self.confusion_matrices),
            "lag_diagnostics": _plain(self.lag_diagnostics),
            "annotation_summaries": [
                _plain(row) for row in self.annotation_summaries
            ],
            "flags": list(self.flags),
            "interpretation": self.interpretation,
            "suitable_for_intensity": self.suitable_for_intensity,
            "reference_config": _plain(self.reference_config),
        }


def evaluate_against_reference(
    *,
    hrmod_result: Any,
    reference_channels: ReferenceChannels | Mapping[str, Any] | Sequence[Any],
    reference_config: ReferenceValidationConfig | Mapping[str, Any] | None = None,
    optional_annotations: Sequence[Any] | None = None,
) -> ReferenceValidationResult:
    """Evaluate references after core computation without recalculation/leakage.

    ``hrmod_result`` must already contain ``timeseries``, ``hr_input_hash`` and
    ``model_version``.  A deep snapshot and stable fingerprint are created
    before reference rows are inspected or joined.
    """

    config = _coerce_config(reference_config)

    # Snapshot and hash the completed core result before touching reference
    # channels.  The snapshot, rather than the caller's object, is used below.
    try:
        core_snapshot = copy.deepcopy(hrmod_result)
    except (TypeError, ValueError) as exc:
        raise ReferenceValidationError(
            "hrmod_result must be immutable or deep-copyable before validation"
        ) from exc
    core_plain = _plain(core_snapshot)
    if not isinstance(core_plain, Mapping):
        raise ReferenceValidationError("hrmod_result must be a result object or mapping")
    hr_input_hash = core_plain.get("hr_input_hash")
    model_version = core_plain.get("model_version")
    timeseries_value = core_plain.get("timeseries")
    if not isinstance(hr_input_hash, str) or not hr_input_hash:
        raise ReferenceValidationError(
            "hrmod_result must contain a non-empty precomputed hr_input_hash"
        )
    if not isinstance(model_version, str) or not model_version:
        raise ReferenceValidationError(
            "hrmod_result must contain a non-empty model_version"
        )
    if not isinstance(timeseries_value, Sequence) or isinstance(
        timeseries_value, (str, bytes, bytearray)
    ):
        raise ReferenceValidationError("hrmod_result must contain a timeseries sequence")

    fingerprint_before = _stable_fingerprint(core_plain)
    core_rows = _normalize_core_rows(timeseries_value)

    reference_rows, reference_sport, embedded_annotations = _normalize_reference(
        reference_channels
    )
    annotations = _normalize_annotations(
        [*embedded_annotations, *(optional_annotations or ())]
    )
    configured_sport = (config.sport or "").strip().lower()
    source_sport = (reference_sport or "").strip().lower()
    # A caller-facing overlay label must never be able to erase a ski source
    # recorded by the parser.  Raw ski speed is descent-sensitive context, not
    # an intensity signal, even when the same numeric channel is explicitly
    # enabled as a controlled treadmill protocol in another activity.
    is_ski = any(
        "ski" in value or "biathlon" in value
        for value in (source_sport, configured_sport)
    )

    flags: set[str] = set()
    has_speed = any(_finite(row.get("speed_mps")) is not None for row in reference_rows)
    has_power = any(_finite(row.get("power_w")) is not None for row in reference_rows)

    quantitative_field: str | None = None
    numeric_zones: tuple[ReferenceZone, ...] = ()
    interpretation = "context_only"
    if config.enable_quantitative_power and has_power:
        quantitative_field = "power_w"
        numeric_zones = config.power_zones
        interpretation = "configured_quantitative_power"
    elif (
        config.enable_controlled_treadmill_speed
        and config.treadmill_grade_verified
        and has_speed
        and not is_ski
    ):
        quantitative_field = "speed_mps"
        numeric_zones = config.speed_zones
        interpretation = "configured_controlled_treadmill_protocol"

    if is_ski and has_speed:
        flags.add("RAW_SKI_SPEED_CONTEXT_ONLY")
    if has_speed and quantitative_field != "speed_mps":
        flags.add("REFERENCE_NOT_SUITABLE_FOR_INTENSITY")
    if has_power and quantitative_field != "power_w":
        flags.add("REFERENCE_NOT_SUITABLE_FOR_INTENSITY")

    aligned = _align_rows(
        core_rows,
        reference_rows,
        tolerance_s=config.join_tolerance_s,
        quantitative_field=quantitative_field,
        numeric_zones=numeric_zones,
        external_zone_field=config.external_zone_field,
    )
    if config.use_annotation_zones:
        aligned = _apply_annotation_zones(aligned, annotations)

    external_zone_count = sum(
        row.get("reference_external_zone") not in (None, "") for row in aligned
    )
    suitable_for_intensity = quantitative_field is not None or external_zone_count > 0
    if not suitable_for_intensity and (reference_rows or annotations):
        flags.add("REFERENCE_NOT_SUITABLE_FOR_INTENSITY")

    confusion_matrices: dict[str, Any] = {}
    metrics: dict[str, Any] = {
        "core_row_count": len(core_rows),
        "reference_row_count": len(reference_rows),
        "matched_reference_rows": sum(
            row.get("reference_timestamp") is not None for row in aligned
        ),
        "external_zone_row_count": external_zone_count,
        "quantitative_channel": quantitative_field,
        "preliminary_expert_evaluation": True,
    }

    if external_zone_count:
        zone_fields = {
            "raw_hr_vs_external": _first_existing_zone_field(
                aligned, ("raw_hr_zone", "raw_zone", "zone_raw_hr")
            ),
            "clean_hr_vs_external": _first_existing_zone_field(
                aligned, ("clean_hr_zone", "clean_zone", "zone_clean_hr")
            ),
            "hrmod_vs_external": _first_existing_zone_field(
                aligned, ("hrmod_zone", "zone_hrmod")
            ),
        }
        for name, predicted_field in zone_fields.items():
            if predicted_field:
                confusion_matrices[name] = _confusion_matrix(
                    aligned,
                    predicted_field=predicted_field,
                    actual_field="reference_external_zone",
                )

        agreement_clean = _agreement_value(
            confusion_matrices.get("clean_hr_vs_external")
        )
        agreement_hrmod = _agreement_value(
            confusion_matrices.get("hrmod_vs_external")
        )
        metrics["clean_hr_external_zone_agreement"] = agreement_clean
        metrics["hrmod_external_zone_agreement"] = agreement_hrmod
        metrics["hrmod_agreement_improvement_vs_clean"] = (
            agreement_hrmod - agreement_clean
            if agreement_clean is not None and agreement_hrmod is not None
            else None
        )

        high_labels = set(config.high_zone_labels)
        if not high_labels and numeric_zones:
            high_labels = {zone.label for zone in numeric_zones[-2:]}
        metrics["high_external_zone_sensitivity"] = {
            "raw_hr": _high_zone_sensitivity(
                aligned,
                _first_existing_zone_field(
                    aligned, ("raw_hr_zone", "raw_zone", "zone_raw_hr")
                ),
                high_labels,
            ),
            "clean_hr": _high_zone_sensitivity(
                aligned,
                _first_existing_zone_field(
                    aligned, ("clean_hr_zone", "clean_zone", "zone_clean_hr")
                ),
                high_labels,
            ),
            "hrmod": _high_zone_sensitivity(
                aligned,
                _first_existing_zone_field(aligned, ("hrmod_zone", "zone_hrmod")),
                high_labels,
            ),
        }

    lag_diagnostics = (
        _lag_diagnostics(
            core_rows,
            reference_rows,
            reference_field=quantitative_field,
            tolerance_s=config.join_tolerance_s,
            max_lag_s=config.max_lag_s,
            lag_step_s=config.lag_step_s,
        )
        if quantitative_field
        else {
            "available": False,
            "reason": "No explicitly configured quantitative reference channel",
        }
    )
    annotation_summaries = _summarize_annotations(aligned, annotations)

    # Verify our snapshot stayed byte-for-byte stable during every post-hoc
    # operation.  This is an internal anti-mutation assertion, not a core hash.
    fingerprint_after = _stable_fingerprint(_plain(core_snapshot))
    if fingerprint_before != fingerprint_after:
        raise RuntimeError("Reference evaluation mutated the HRmod core snapshot")

    return ReferenceValidationResult(
        hr_input_hash=hr_input_hash,
        model_version=model_version,
        core_result_fingerprint=fingerprint_before,
        aligned_timeseries=tuple(aligned),
        metrics=metrics,
        confusion_matrices=confusion_matrices,
        lag_diagnostics=lag_diagnostics,
        annotation_summaries=tuple(annotation_summaries),
        flags=tuple(sorted(flags)),
        interpretation=interpretation,
        suitable_for_intensity=suitable_for_intensity,
        reference_config=config.to_dict(),
    )


def _coerce_config(
    value: ReferenceValidationConfig | Mapping[str, Any] | None,
) -> ReferenceValidationConfig:
    if value is None:
        return ReferenceValidationConfig()
    if isinstance(value, ReferenceValidationConfig):
        return value
    if not isinstance(value, Mapping):
        raise TypeError("reference_config must be ReferenceValidationConfig or mapping")
    data = dict(value)
    for name in ("power_zones", "speed_zones"):
        if name in data:
            data[name] = tuple(_coerce_zone(zone) for zone in data[name])
    if "high_zone_labels" in data:
        data["high_zone_labels"] = tuple(str(item) for item in data["high_zone_labels"])
    return ReferenceValidationConfig(**data)


def _coerce_zone(value: ReferenceZone | Mapping[str, Any] | Sequence[Any]) -> ReferenceZone:
    if isinstance(value, ReferenceZone):
        return value
    if isinstance(value, Mapping):
        return ReferenceZone(
            label=str(value["label"]),
            lower=float(value.get("lower", value.get("lower_bound"))),
            upper=(
                None
                if value.get("upper", value.get("upper_bound")) is None
                else float(value.get("upper", value.get("upper_bound")))
            ),
        )
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        if len(value) not in (2, 3):
            raise ValueError("Zone tuple must be (label, lower[, upper])")
        return ReferenceZone(
            str(value[0]), float(value[1]), None if len(value) == 2 else float(value[2])
        )
    raise TypeError("Invalid reference zone")


def _validate_zones(zones: tuple[ReferenceZone, ...], name: str) -> None:
    for previous, current in zip(zones, zones[1:]):
        if current.lower < previous.lower:
            raise ValueError(f"{name} must be sorted by lower bound")
        if previous.upper is None or current.lower < previous.upper:
            raise ValueError(f"{name} cannot overlap")


def _normalize_core_rows(timeseries: Sequence[Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for index, item in enumerate(timeseries):
        row = _object_mapping(item)
        if "timestamp" not in row:
            raise ReferenceValidationError(f"Core timeseries row {index} lacks timestamp")
        timestamp = _timestamp(row["timestamp"])
        copied = copy.deepcopy(row)
        copied["timestamp"] = timestamp
        rows.append(copied)
    rows.sort(key=lambda row: row["timestamp"])
    return rows


def _normalize_reference(
    value: ReferenceChannels | Mapping[str, Any] | Sequence[Any],
) -> tuple[list[dict[str, Any]], str | None, list[Any]]:
    sport: str | None = None
    annotations: list[Any] = []
    sample_values: Any = value
    if isinstance(value, ReferenceChannels):
        sport = value.sport
        annotations.extend(value.laps)
        sample_values = value.samples
    elif isinstance(value, Mapping):
        sport_value = value.get("sport")
        sport = str(sport_value) if sport_value is not None else None
        annotations.extend(value.get("laps", value.get("annotations", ())) or ())
        if "samples" in value:
            sample_values = value["samples"]
        elif "timestamp" in value and isinstance(value["timestamp"], Sequence) and not isinstance(
            value["timestamp"], (str, bytes, bytearray)
        ):
            timestamps = value["timestamp"]
            sample_values = [
                {
                    key: column[index]
                    for key, column in value.items()
                    if isinstance(column, Sequence)
                    and not isinstance(column, (str, bytes, bytearray))
                    and index < len(column)
                }
                for index in range(len(timestamps))
            ]
        else:
            sample_values = ()
    if isinstance(sample_values, (str, bytes, bytearray)) or not isinstance(
        sample_values, Sequence
    ):
        raise TypeError("reference_channels must contain a sequence of timestamped samples")

    rows: list[dict[str, Any]] = []
    for index, item in enumerate(sample_values):
        row = _object_mapping(item)
        if "timestamp" not in row:
            raise ReferenceValidationError(
                f"Reference sample {index} lacks a timestamp"
            )
        copied = copy.deepcopy(row)
        copied["timestamp"] = _timestamp(row["timestamp"])
        rows.append(copied)
    rows.sort(key=lambda row: row["timestamp"])
    # Deterministic first-row-wins deduplication is adequate here because TCX
    # parsing already merges optional values before constructing this object.
    deduplicated: list[dict[str, Any]] = []
    for row in rows:
        if deduplicated and row["timestamp"] == deduplicated[-1]["timestamp"]:
            for key, field_value in row.items():
                if deduplicated[-1].get(key) is None and field_value is not None:
                    deduplicated[-1][key] = field_value
        else:
            deduplicated.append(row)
    return deduplicated, sport, annotations


def _normalize_annotations(values: Iterable[Any]) -> list[dict[str, Any]]:
    annotations: list[dict[str, Any]] = []
    for index, item in enumerate(values):
        row = _object_mapping(item)
        start_value = next(
            (
                row[name]
                for name in ("start_time", "start_timestamp", "start")
                if row.get(name) is not None
            ),
            None,
        )
        end_value = next(
            (
                row[name]
                for name in ("end_time", "end_timestamp", "end")
                if row.get(name) is not None
            ),
            None,
        )
        if start_value is None:
            continue
        start = _timestamp(start_value)
        end = _timestamp(end_value) if end_value is not None else start
        if end < start:
            raise ReferenceValidationError("Annotation end precedes its start")
        annotations.append(
            {
                **copy.deepcopy(row),
                "annotation_id": str(
                    row.get("annotation_id", row.get("id", f"annotation-{index + 1}"))
                ),
                "start_time": start,
                "end_time": end,
                "label": row.get("label", row.get("name")),
                "external_zone": row.get("external_zone", row.get("zone")),
            }
        )
    return sorted(annotations, key=lambda row: (row["start_time"], row["annotation_id"]))


def _align_rows(
    core_rows: list[dict[str, Any]],
    reference_rows: list[dict[str, Any]],
    *,
    tolerance_s: float,
    quantitative_field: str | None,
    numeric_zones: tuple[ReferenceZone, ...],
    external_zone_field: str | None,
) -> list[dict[str, Any]]:
    reference_times = [row["timestamp"] for row in reference_rows]
    aligned: list[dict[str, Any]] = []
    for core_row in core_rows:
        result = copy.deepcopy(core_row)
        reference = _nearest_row(
            core_row["timestamp"], reference_rows, reference_times, tolerance_s
        )
        if reference is None:
            result["reference_timestamp"] = None
            result["reference_time_delta_s"] = None
            result["reference_external_zone"] = None
        else:
            result["reference_timestamp"] = reference["timestamp"]
            result["reference_time_delta_s"] = (
                reference["timestamp"] - core_row["timestamp"]
            ).total_seconds()
            for key, value in reference.items():
                if key == "timestamp":
                    continue
                result[f"reference_{key}"] = copy.deepcopy(value)
            external_zone = None
            if external_zone_field:
                external_zone = reference.get(external_zone_field)
            if (
                external_zone in (None, "")
                and quantitative_field
                and numeric_zones
            ):
                external_zone = _classify_numeric(
                    _finite(reference.get(quantitative_field)), numeric_zones
                )
            result["reference_external_zone"] = external_zone
        aligned.append(result)
    return aligned


def _apply_annotation_zones(
    aligned: list[dict[str, Any]], annotations: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    result = copy.deepcopy(aligned)
    for row in result:
        matching = [
            annotation
            for annotation in annotations
            if annotation.get("external_zone") not in (None, "")
            and annotation["start_time"] <= row["timestamp"] <= annotation["end_time"]
        ]
        if matching:
            # Stable annotation ordering makes overlap resolution deterministic.
            row["reference_external_zone"] = matching[-1]["external_zone"]
            row["reference_zone_annotation_id"] = matching[-1]["annotation_id"]
    return result


def _nearest_row(
    timestamp: datetime,
    rows: list[dict[str, Any]],
    times: list[datetime],
    tolerance_s: float,
) -> dict[str, Any] | None:
    if not times:
        return None
    index = bisect_left(times, timestamp)
    candidate_indices = [candidate for candidate in (index - 1, index) if 0 <= candidate < len(times)]
    best_index = min(
        candidate_indices,
        key=lambda candidate: (abs((times[candidate] - timestamp).total_seconds()), candidate),
    )
    delta_s = abs((times[best_index] - timestamp).total_seconds())
    return rows[best_index] if delta_s <= tolerance_s else None


def _classify_numeric(
    value: float | None, zones: tuple[ReferenceZone, ...]
) -> str | None:
    if value is None:
        return None
    for zone in zones:
        if value >= zone.lower and (zone.upper is None or value < zone.upper):
            return zone.label
    return None


def _confusion_matrix(
    rows: list[dict[str, Any]], *, predicted_field: str, actual_field: str
) -> dict[str, Any]:
    counts: dict[str, dict[str, int]] = {}
    seconds: dict[str, dict[str, float]] = {}
    total = 0
    agreement = 0
    total_seconds = 0.0
    agreement_seconds = 0.0
    for row in rows:
        predicted = row.get(predicted_field)
        actual = row.get(actual_field)
        if predicted in (None, "") or actual in (None, ""):
            continue
        predicted_label, actual_label = str(predicted), str(actual)
        duration_s = max(0.0, _finite(row.get("dt_s")) or 0.0)
        counts.setdefault(actual_label, {}).setdefault(predicted_label, 0)
        counts[actual_label][predicted_label] += 1
        seconds.setdefault(actual_label, {}).setdefault(predicted_label, 0.0)
        seconds[actual_label][predicted_label] += duration_s
        total += 1
        total_seconds += duration_s
        if predicted_label == actual_label:
            agreement += 1
            agreement_seconds += duration_s
    return {
        "counts": counts,
        "seconds": seconds,
        "sample_count": total,
        "agreement_fraction": agreement / total if total else None,
        "time_weighted_agreement_fraction": (
            agreement_seconds / total_seconds if total_seconds > 0 else None
        ),
    }


def _agreement_value(matrix: Any) -> float | None:
    if not isinstance(matrix, Mapping):
        return None
    weighted = matrix.get("time_weighted_agreement_fraction")
    if weighted is not None:
        return float(weighted)
    value = matrix.get("agreement_fraction")
    return float(value) if value is not None else None


def _high_zone_sensitivity(
    rows: list[dict[str, Any]],
    predicted_field: str | None,
    high_labels: set[str],
) -> float | None:
    if not predicted_field or not high_labels:
        return None
    true_positive = 0.0
    actual_positive = 0.0
    for row in rows:
        actual = row.get("reference_external_zone")
        predicted = row.get(predicted_field)
        if actual is None:
            continue
        weight = max(0.0, _finite(row.get("dt_s")) or 0.0)
        if str(actual) in high_labels:
            actual_positive += weight
            if predicted is not None and str(predicted) in high_labels:
                true_positive += weight
    return true_positive / actual_positive if actual_positive > 0 else None


def _lag_diagnostics(
    core_rows: list[dict[str, Any]],
    reference_rows: list[dict[str, Any]],
    *,
    reference_field: str,
    tolerance_s: float,
    max_lag_s: int,
    lag_step_s: int,
) -> dict[str, Any]:
    reference_times = [row["timestamp"] for row in reference_rows]
    signal_fields = {
        "raw_hr": _first_existing_numeric_field(
            core_rows, ("raw_hr_bpm", "raw_hr", "heart_rate_bpm")
        ),
        "clean_hr": _first_existing_numeric_field(
            core_rows, ("clean_hr_bpm", "clean_hr")
        ),
        "hrmod": _first_existing_numeric_field(core_rows, ("hrmod_bpm", "hrmod")),
    }
    result: dict[str, Any] = {
        "available": True,
        "reference_field": reference_field,
        "positive_lag_definition": "HR follows reference by lag_s",
        "signals": {},
        "correlation_is_not_validation": True,
    }
    from datetime import timedelta

    for signal_name, signal_field in signal_fields.items():
        if not signal_field:
            continue
        candidates: list[dict[str, Any]] = []
        for lag_s in range(-max_lag_s, max_lag_s + 1, lag_step_s):
            xs: list[float] = []
            ys: list[float] = []
            for row in core_rows:
                hr_value = _finite(row.get(signal_field))
                if hr_value is None:
                    continue
                # Positive lag: reference happened earlier than the HR value.
                target_reference_time = row["timestamp"] - timedelta(seconds=lag_s)
                reference = _nearest_row(
                    target_reference_time,
                    reference_rows,
                    reference_times,
                    tolerance_s,
                )
                if reference is None:
                    continue
                reference_value = _finite(reference.get(reference_field))
                if reference_value is None:
                    continue
                xs.append(reference_value)
                ys.append(hr_value)
            correlation = _pearson(xs, ys)
            candidates.append(
                {"lag_s": lag_s, "correlation": correlation, "pair_count": len(xs)}
            )
        valid = [item for item in candidates if item["correlation"] is not None]
        best = max(valid, key=lambda item: (item["correlation"], -abs(item["lag_s"]))) if valid else None
        result["signals"][signal_name] = {
            "best_lag_s": best["lag_s"] if best else None,
            "best_correlation": best["correlation"] if best else None,
            "pair_count_at_best": best["pair_count"] if best else 0,
            "lag_curve": candidates,
        }
    return result


def _pearson(xs: list[float], ys: list[float]) -> float | None:
    if len(xs) < 3 or len(xs) != len(ys):
        return None
    x_mean, y_mean = mean(xs), mean(ys)
    numerator = sum((x - x_mean) * (y - y_mean) for x, y in zip(xs, ys))
    x_energy = sum((x - x_mean) ** 2 for x in xs)
    y_energy = sum((y - y_mean) ** 2 for y in ys)
    denominator = math.sqrt(x_energy * y_energy)
    return numerator / denominator if denominator > 0 else None


def _summarize_annotations(
    aligned: list[dict[str, Any]], annotations: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    summaries: list[dict[str, Any]] = []
    for annotation in annotations:
        rows = [
            row
            for row in aligned
            if annotation["start_time"] <= row["timestamp"] <= annotation["end_time"]
        ]
        summary: dict[str, Any] = {
            "annotation_id": annotation["annotation_id"],
            "label": annotation.get("label"),
            "start_time": annotation["start_time"],
            "end_time": annotation["end_time"],
            "external_zone": annotation.get("external_zone"),
            "sample_count": len(rows),
            "duration_s": max(
                0.0,
                (annotation["end_time"] - annotation["start_time"]).total_seconds(),
            ),
        }
        for output_name, candidates in {
            "raw_hr_mean": ("raw_hr_bpm", "raw_hr", "heart_rate_bpm"),
            "clean_hr_mean": ("clean_hr_bpm", "clean_hr"),
            "hrmod_mean": ("hrmod_bpm", "hrmod"),
            "reference_power_w_mean": ("reference_power_w",),
            "reference_speed_mps_mean": ("reference_speed_mps",),
        }.items():
            field_name = _first_existing_numeric_field(rows, candidates)
            values = [
                number
                for row in rows
                if field_name and (number := _finite(row.get(field_name))) is not None
            ]
            summary[output_name] = mean(values) if values else None
        summaries.append(summary)
    return summaries


def _first_existing_zone_field(
    rows: list[dict[str, Any]], candidates: tuple[str, ...]
) -> str | None:
    return next(
        (
            candidate
            for candidate in candidates
            if any(row.get(candidate) not in (None, "") for row in rows)
        ),
        None,
    )


def _first_existing_numeric_field(
    rows: list[dict[str, Any]], candidates: tuple[str, ...]
) -> str | None:
    return next(
        (
            candidate
            for candidate in candidates
            if any(_finite(row.get(candidate)) is not None for row in rows)
        ),
        None,
    )


def _finite(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _timestamp(value: Any) -> datetime:
    if isinstance(value, datetime):
        result = value
    elif isinstance(value, str):
        normalized = value.strip()
        if normalized.endswith(("Z", "z")):
            normalized = normalized[:-1] + "+00:00"
        try:
            result = datetime.fromisoformat(normalized)
        except ValueError as exc:
            raise ReferenceValidationError(f"Invalid timestamp: {value!r}") from exc
    else:
        raise ReferenceValidationError(f"Timestamp must be datetime or ISO text, got {type(value).__name__}")
    if result.tzinfo is None or result.utcoffset() is None:
        raise ReferenceValidationError("Reference validation requires timezone-aware timestamps")
    return result.astimezone(UTC)


def _object_mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    if is_dataclass(value) and not isinstance(value, type):
        return {field.name: getattr(value, field.name) for field in fields(value)}
    if hasattr(value, "__dict__"):
        return dict(vars(value))
    raise TypeError(f"Expected a mapping or dataclass row, got {type(value).__name__}")


def _plain(value: Any) -> Any:
    if is_dataclass(value) and not isinstance(value, type):
        return {field.name: _plain(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_plain(item) for item in value]
    if isinstance(value, (set, frozenset)):
        return sorted((_plain(item) for item in value), key=repr)
    if isinstance(value, datetime):
        return value.isoformat()
    if hasattr(value, "value") and value.__class__.__module__ == "enum":
        return _plain(value.value)
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def _stable_fingerprint(value: Any) -> str:
    encoded = json.dumps(
        _plain(value),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _freeze_mapping(value: Mapping[str, Any]) -> Mapping[str, Any]:
    def freeze(item: Any) -> Any:
        if isinstance(item, Mapping):
            return MappingProxyType({str(key): freeze(val) for key, val in item.items()})
        if isinstance(item, list):
            return tuple(freeze(val) for val in item)
        if isinstance(item, tuple):
            return tuple(freeze(val) for val in item)
        return item

    return freeze(dict(value))


__all__ = [
    "ReferenceValidationConfig",
    "ReferenceValidationError",
    "ReferenceValidationResult",
    "ReferenceZone",
    "evaluate_against_reference",
]

