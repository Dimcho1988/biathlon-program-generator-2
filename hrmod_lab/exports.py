"""Dependency-free exports for HRmod Lab core and post-hoc results."""

from __future__ import annotations

import csv
from dataclasses import fields, is_dataclass
from datetime import datetime
from io import BytesIO, StringIO
import json
import math
from typing import Any, Mapping, Sequence
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo


class ExportError(ValueError):
    """Raised when a requested export does not match the result contract."""


_REFERENCE_ONLY_FIELDS = {
    "distance_m",
    "distance",
    "speed_mps",
    "speed",
    "cadence",
    "cadence_rpm",
    "altitude_m",
    "altitude",
    "grade",
    "slope",
    "power_w",
    "power",
    "watts",
    "external_zone",
    "lap",
    "lap_id",
    "laps",
    "manual_marker",
    "manual_markers",
    "annotation",
    "annotations",
    "sport",
    "smoothed_grade_pct",
    "downhill_mask",
    "terrain_status",
    "terrain_rejection_reason",
    "hrmod_candidate_bpm",
    "hrmod_final_bpm",
}

_TIMESERIES_ORDER = (
    "timestamp",
    "elapsed_s",
    "dt_s",
    "raw_hr_bpm",
    "clean_hr_bpm",
    "h_detect_bpm",
    "trend_bpm_per_s",
    "segment_id",
    "wave_id",
    "wave_state",
    "local_baseline_hr_bpm",
    "receiver_flag",
    "donor_flag",
    "added_bpm",
    "removed_bpm",
    "hrmod_bpm",
    "raw_hr_zone",
    "clean_hr_zone",
    "hrmod_zone",
    "quality_flags",
    "model_flags",
)

_WAVE_ORDER = (
    "wave_id",
    "segment_id",
    "status",
    "morphology",
    "morphology_reason",
    "correction_strategy",
    "complete",
    "corrected",
    "rise_start_timestamp",
    "peak_timestamp",
    "tail_end_timestamp",
    "rise_start_elapsed_s",
    "peak_elapsed_s",
    "tail_end_elapsed_s",
    "end_reason",
    "baseline_hr_bpm",
    "donor_floor_bpm",
    "rise_bpm",
    "fall_bpm",
    "receiver_duration_s",
    "donor_duration_s",
    "donor_available_area_bpm_s",
    "requested_area_bpm_s",
    "receiver_capacity_bpm_s",
    "moved_area_bpm_s",
    "moved_fraction_of_donor",
    "added_area_bpm_s",
    "removed_area_bpm_s",
    "area_balance_error_bpm_s",
    "capacity_limited_area_bpm_s",
    "capacity_limited",
    "skip_reason",
    "raw_zone_seconds",
    "clean_zone_seconds",
    "hrmod_zone_seconds",
    "hrmod_minus_raw_zone_seconds",
    "hrmod_minus_clean_zone_seconds",
    "flags",
)

_ZONE_ORDER = (
    "zone_name",
    "lower_bpm",
    "upper_bpm",
    "raw_seconds",
    "raw_percent",
    "clean_seconds",
    "clean_percent",
    "hrmod_seconds",
    "hrmod_percent",
    "hrmod_minus_clean_seconds",
)

_TERRAIN_TIMESERIES_ORDER = (
    "timestamp",
    "elapsed_s",
    "dt_s",
    "raw_hr_bpm",
    "hrmod_candidate_bpm",
    "hrmod_final_bpm",
    "smoothed_grade_pct",
    "downhill_mask",
    "terrain_status",
    "wave_id",
)

_TERRAIN_WAVE_ORDER = (
    "wave_id",
    "terrain_status",
    "terrain_rejection_reason",
    "downhill_overlap_s",
    "downhill_overlap_fraction",
    "min_smoothed_grade_pct",
    "moved_area_candidate_bpm_s",
    "moved_area_final_bpm_s",
)

_TERRAIN_ZONE_ORDER = (
    "zone_name",
    "lower_bpm",
    "upper_bpm",
    "raw_seconds",
    "raw_percent",
    "hrmod_candidate_seconds",
    "hrmod_candidate_percent",
    "hrmod_final_seconds",
    "hrmod_final_percent",
    "final_minus_candidate_seconds",
    "final_minus_raw_seconds",
)


def export_timeseries_csv(hrmod_result: Any) -> bytes:
    """Return the processed HR-only timeseries CSV as UTF-8 bytes.

    Known reference fields and every ``reference_*`` field are excluded even
    if a caller supplies a broader mapping by mistake.
    """

    rows = _rows(_required_member(hrmod_result, "timeseries"))
    core_rows = [
        {
            key: value
            for key, value in row.items()
            if not _is_reference_field(key)
        }
        for row in rows
    ]
    return _csv_bytes(core_rows, preferred_fields=_TIMESERIES_ORDER)


def export_wave_summary_csv(hrmod_result: Any) -> bytes:
    """Return one row per detected HR wave, including versioned morphology."""

    rows = _rows(_required_member(hrmod_result, "wave_summary"))
    return _csv_bytes(rows, preferred_fields=_WAVE_ORDER)


def export_zone_summary_csv(hrmod_result: Any) -> bytes:
    rows = _rows(_required_member(hrmod_result, "zone_summary"))
    return _csv_bytes(rows, preferred_fields=_ZONE_ORDER)


def export_config_json(hrmod_result: Any) -> bytes:
    payload = {
        "model_version": _member(hrmod_result, "model_version"),
        "hr_input_hash": _member(hrmod_result, "hr_input_hash"),
        "config": _plain(_required_member(hrmod_result, "config")),
    }
    return _json_bytes(payload)


def export_diagnostics_json(hrmod_result: Any) -> bytes:
    payload = {
        "model_version": _member(hrmod_result, "model_version"),
        "hr_input_hash": _member(hrmod_result, "hr_input_hash"),
        "diagnostics": _plain(_required_member(hrmod_result, "diagnostics")),
    }
    return _json_bytes(payload)


def export_terrain_timeseries_csv(terrain_result: Any) -> bytes:
    """Export the post-hoc raw/candidate/final terrain comparison."""

    rows = _rows(_required_member(terrain_result, "timeseries"))
    return _csv_bytes(rows, preferred_fields=_TERRAIN_TIMESERIES_ORDER)


def export_terrain_wave_summary_csv(terrain_result: Any) -> bytes:
    rows = _rows(_required_member(terrain_result, "wave_summary"))
    return _csv_bytes(rows, preferred_fields=_TERRAIN_WAVE_ORDER)


def export_terrain_zone_summary_csv(terrain_result: Any) -> bytes:
    """Export post-gate time-in-zone without changing the core zone export."""

    rows = _rows(_required_member(terrain_result, "zone_summary"))
    return _csv_bytes(rows, preferred_fields=_TERRAIN_ZONE_ORDER)


def export_terrain_result_json(terrain_result: Any) -> bytes:
    """Export provenance and diagnostics without altering the core artefacts."""

    payload = {
        "model_version": _member(terrain_result, "model_version"),
        "terrain_model_version": _member(terrain_result, "terrain_model_version"),
        "hr_input_hash": _member(terrain_result, "hr_input_hash"),
        "terrain_input_hash": _member(terrain_result, "terrain_input_hash"),
        "final_result_hash": _member(terrain_result, "final_result_hash"),
        "config": _plain(_member(terrain_result, "config")),
        "diagnostics": _plain(_member(terrain_result, "diagnostics")),
    }
    return _json_bytes(payload)


def export_reference_comparison_csv(validation_result: Any) -> bytes:
    rows = _rows(_required_member(validation_result, "aligned_timeseries"))
    return _csv_bytes(rows, preferred_fields=_TIMESERIES_ORDER)


def export_reference_validation_json(validation_result: Any) -> bytes:
    value = (
        validation_result.to_dict()
        if callable(getattr(validation_result, "to_dict", None))
        else _plain(validation_result)
    )
    return _json_bytes(value)


def export_annotations_csv(annotations: Sequence[Any]) -> bytes:
    return _csv_bytes(_rows(annotations))


def export_annotations_json(annotations: Sequence[Any]) -> bytes:
    return _json_bytes([_plain(item) for item in annotations])


def build_export_bundle(
    *,
    hrmod_result: Any,
    validation_result: Any | None = None,
    annotations: Sequence[Any] | None = None,
    terrain_result: Any | None = None,
) -> dict[str, bytes]:
    """Build all applicable files without mixing references into core CSV."""

    files = {
        "processed_hr_timeseries.csv": export_timeseries_csv(hrmod_result),
        "wave_summary.csv": export_wave_summary_csv(hrmod_result),
        "zone_summary.csv": export_zone_summary_csv(hrmod_result),
        "run_configuration.json": export_config_json(hrmod_result),
        "diagnostics.json": export_diagnostics_json(hrmod_result),
    }
    if validation_result is not None:
        files["reference_aligned_comparison.csv"] = export_reference_comparison_csv(
            validation_result
        )
        files["reference_validation.json"] = export_reference_validation_json(
            validation_result
        )
        annotation_summaries = _member(validation_result, "annotation_summaries")
        if annotation_summaries:
            files["annotation_summaries.csv"] = _csv_bytes(
                _rows(annotation_summaries)
            )
            files["annotation_summaries.json"] = _json_bytes(
                [_plain(item) for item in annotation_summaries]
            )
    if annotations:
        files["annotations.csv"] = export_annotations_csv(annotations)
        files["annotations.json"] = export_annotations_json(annotations)
    if terrain_result is not None:
        files["terrain_gated_timeseries.csv"] = export_terrain_timeseries_csv(
            terrain_result
        )
        files["terrain_wave_summary.csv"] = export_terrain_wave_summary_csv(
            terrain_result
        )
        files["terrain_zone_summary.csv"] = export_terrain_zone_summary_csv(
            terrain_result
        )
        files["terrain_result.json"] = export_terrain_result_json(terrain_result)

    manifest = {
        "format": "hrmod_lab_export_v4",
        "model_version": _member(hrmod_result, "model_version"),
        "hr_input_hash": _member(hrmod_result, "hr_input_hash"),
        "core_and_reference_exports_are_separate": True,
        "files": sorted(files),
    }
    if terrain_result is not None:
        manifest["terrain_input_hash"] = _member(
            terrain_result, "terrain_input_hash"
        )
        manifest["final_result_hash"] = _member(
            terrain_result, "final_result_hash"
        )
    files["manifest.json"] = _json_bytes(manifest)
    return files


def build_results_zip(
    *,
    hrmod_result: Any,
    validation_result: Any | None = None,
    annotations: Sequence[Any] | None = None,
    terrain_result: Any | None = None,
) -> bytes:
    """Return a deterministic ZIP containing every applicable export."""

    files = build_export_bundle(
        hrmod_result=hrmod_result,
        validation_result=validation_result,
        annotations=annotations,
        terrain_result=terrain_result,
    )
    output = BytesIO()
    with ZipFile(output, mode="w", compression=ZIP_DEFLATED, compresslevel=9) as archive:
        for filename in sorted(files):
            info = ZipInfo(filename=filename, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = ZIP_DEFLATED
            info.external_attr = 0o600 << 16
            archive.writestr(info, files[filename])
    return output.getvalue()


def _member(value: Any, name: str) -> Any:
    if isinstance(value, Mapping):
        return value.get(name)
    return getattr(value, name, None)


def _is_reference_field(name: Any) -> bool:
    """Return whether a row field belongs outside the HR-only export.

    The check is deliberately case-insensitive because callers may provide a
    mapping rather than the strict core dataclass.  This is a final export
    guard; the core result itself remains the primary anti-leakage boundary.
    """

    normalized = str(name).strip().casefold()
    return normalized in _REFERENCE_ONLY_FIELDS or normalized.startswith(
        "reference_"
    )


def _required_member(value: Any, name: str) -> Any:
    result = _member(value, name)
    if result is None:
        raise ExportError(f"Result does not contain {name!r}")
    return result


def _rows(values: Any) -> list[dict[str, Any]]:
    if isinstance(values, (str, bytes, bytearray)) or not isinstance(values, Sequence):
        raise ExportError("CSV source must be a sequence of rows")
    result: list[dict[str, Any]] = []
    for item in values:
        if isinstance(item, Mapping):
            result.append(dict(item))
        elif is_dataclass(item) and not isinstance(item, type):
            result.append({field.name: getattr(item, field.name) for field in fields(item)})
        elif hasattr(item, "__dict__"):
            result.append(dict(vars(item)))
        else:
            raise ExportError(f"CSV row must be mapping/dataclass, got {type(item).__name__}")
    return result


def _csv_bytes(
    rows: list[dict[str, Any]], *, preferred_fields: Sequence[str] = ()
) -> bytes:
    seen: set[str] = set()
    all_fields: list[str] = []
    for field_name in preferred_fields:
        if field_name not in seen:
            seen.add(field_name)
            all_fields.append(field_name)
    extras = sorted(
        {
            str(key)
            for row in rows
            for key in row
            if str(key) not in seen
        }
    )
    all_fields.extend(extras)
    if not all_fields:
        return b""

    stream = StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=all_fields, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow({key: _csv_cell(row.get(key)) for key in all_fields})
    return stream.getvalue().encode("utf-8")


def _csv_cell(value: Any) -> Any:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (Mapping, tuple, list, set, frozenset)) or is_dataclass(value):
        return json.dumps(
            _plain(value), ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
    if isinstance(value, str) and value.startswith(("=", "+", "-", "@")):
        # Avoid spreadsheet formula execution in user-controlled labels.
        return "'" + value
    return value


def _json_bytes(value: Any) -> bytes:
    return (
        json.dumps(
            _plain(value),
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


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
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


# Clear spelling aliases for callers that prefer noun-first export names.
export_processed_timeseries_csv = export_timeseries_csv
export_annotations = export_annotations_json
export_all_zip = build_results_zip


__all__ = [
    "ExportError",
    "build_export_bundle",
    "build_results_zip",
    "export_all_zip",
    "export_annotations",
    "export_annotations_csv",
    "export_annotations_json",
    "export_config_json",
    "export_diagnostics_json",
    "export_processed_timeseries_csv",
    "export_reference_comparison_csv",
    "export_reference_validation_json",
    "export_timeseries_csv",
    "export_terrain_result_json",
    "export_terrain_timeseries_csv",
    "export_terrain_wave_summary_csv",
    "export_terrain_zone_summary_csv",
    "export_wave_summary_csv",
    "export_zone_summary_csv",
]

