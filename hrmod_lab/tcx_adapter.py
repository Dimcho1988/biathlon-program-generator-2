"""Safe TCX ingestion with a hard HR/reference data boundary.

The public :func:`parse_tcx` result deliberately contains two physically
separate collections.  Only ``hr_input_samples`` is suitable for passing to
``compute_hrmod_hr_only``.  All non-HR measurements and TCX laps live in the
``reference_channels`` object and are intended solely for post-hoc analysis.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
import math
import os
from pathlib import Path
import re
from statistics import median
from types import MappingProxyType
from typing import Any, BinaryIO, Mapping, TextIO
import xml.etree.ElementTree as ET

from .schemas import HRInputSample


_FORBIDDEN_XML_DECLARATIONS = re.compile(br"<!\s*(?:DOCTYPE|ENTITY)\b", re.I)
_DEFAULT_MAX_BYTES = 64 * 1024 * 1024


class TCXParseError(ValueError):
    """Raised when a TCX payload cannot be converted into usable samples."""


class TCXSecurityError(TCXParseError):
    """Raised when a payload contains a prohibited XML declaration."""


@dataclass(frozen=True, slots=True)
class TCXParserConfig:
    """Parser-only controls; none of these parameters clean or smooth HR."""

    max_bytes: int = _DEFAULT_MAX_BYTES
    long_gap_threshold_s: float = 10.0
    regularity_target_s: float = 1.0
    regularity_tolerance_s: float = 0.25
    assume_naive_timestamps_utc: bool = True

    def __post_init__(self) -> None:
        if self.max_bytes <= 0:
            raise ValueError("max_bytes must be positive")
        if self.long_gap_threshold_s <= 0:
            raise ValueError("long_gap_threshold_s must be positive")
        if self.regularity_target_s <= 0:
            raise ValueError("regularity_target_s must be positive")
        if self.regularity_tolerance_s < 0:
            raise ValueError("regularity_tolerance_s cannot be negative")


@dataclass(frozen=True, slots=True)
class ReferenceSample:
    """Non-core TCX channels aligned to one unique UTC timestamp.

    ``grade`` is a ready-made TCX grade value in percentage points, when the
    file provides one.  The parser never derives grade from altitude/distance;
    that belongs in a separate post-core terrain/reference layer.
    """

    timestamp: datetime
    elapsed_s: float
    dt_s: float
    distance_m: float | None = None
    speed_mps: float | None = None
    cadence: float | None = None
    altitude_m: float | None = None
    grade: float | None = None
    power_w: float | None = None

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["timestamp"] = self.timestamp.isoformat()
        return result


@dataclass(frozen=True, slots=True)
class TCXLapAnnotation:
    """A TCX lap preserved as an annotation, never as a core interval."""

    annotation_id: str
    start_time: datetime
    end_time: datetime | None
    total_time_s: float | None = None
    distance_m: float | None = None
    trigger_method: str | None = None
    sport: str | None = None
    source: str = "tcx_lap"

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["start_time"] = self.start_time.isoformat()
        result["end_time"] = self.end_time.isoformat() if self.end_time else None
        return result


@dataclass(frozen=True, slots=True)
class ReferenceChannels:
    """The physically separate, post-hoc-only side of a parsed TCX file."""

    samples: tuple[ReferenceSample, ...] = ()
    laps: tuple[TCXLapAnnotation, ...] = ()
    sport: str | None = None
    available_channels: tuple[str, ...] = ()
    metadata: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({})
    )

    def __post_init__(self) -> None:
        # A copied read-only mapping prevents accidental cross-layer mutation.
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))

    @property
    def annotations(self) -> tuple[TCXLapAnnotation, ...]:
        return self.laps

    def to_dict(self) -> dict[str, Any]:
        return {
            "samples": [sample.to_dict() for sample in self.samples],
            "laps": [lap.to_dict() for lap in self.laps],
            "sport": self.sport,
            "available_channels": list(self.available_channels),
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True, slots=True)
class TCXParseDiagnostics:
    trackpoint_count: int
    unique_timestamp_count: int
    duplicate_timestamp_count: int
    missing_timestamp_count: int
    missing_hr_count: int
    timezone_assumed_count: int
    long_gap_count: int
    hr_coverage_fraction: float
    median_dt_s: float | None
    min_dt_s: float | None
    max_dt_s: float | None
    sampling_regularity_fraction: float | None
    start_time: datetime | None
    end_time: datetime | None
    duration_s: float
    flags: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["start_time"] = self.start_time.isoformat() if self.start_time else None
        result["end_time"] = self.end_time.isoformat() if self.end_time else None
        return result


@dataclass(frozen=True, slots=True)
class TCXParseResult:
    """Separated TCX result.  Pass only ``hr_input_samples`` to core."""

    hr_input_samples: tuple[HRInputSample, ...]
    reference_channels: ReferenceChannels
    diagnostics: TCXParseDiagnostics

    def to_dict(self) -> dict[str, Any]:
        return {
            "hr_input_samples": [
                {
                    "timestamp": sample.timestamp.isoformat(),
                    "heart_rate_bpm": sample.heart_rate_bpm,
                    "quality_flags": list(sample.quality_flags),
                }
                for sample in self.hr_input_samples
            ],
            "reference_channels": self.reference_channels.to_dict(),
            "diagnostics": self.diagnostics.to_dict(),
        }


def parse_tcx(
    source: bytes | bytearray | memoryview | str | os.PathLike[str] | BinaryIO | TextIO,
    *,
    config: TCXParserConfig | None = None,
) -> TCXParseResult:
    """Parse TCX bytes/a file into isolated HR-only and reference structures.

    XML DTD and entity declarations are rejected before parsing.  The standard
    library parser is then used without a custom entity resolver.  Timestamps
    are normalized to UTC, sorted, and deterministically deduplicated.
    """

    parser_config = config or TCXParserConfig()
    payload = _read_payload(source, max_bytes=parser_config.max_bytes)
    _reject_unsafe_xml(payload)

    try:
        root = ET.fromstring(payload)
    except ET.ParseError as exc:
        raise TCXParseError(f"Invalid TCX XML: {exc}") from exc

    sport = _extract_sport(root)
    raw_points: list[dict[str, Any]] = []
    missing_timestamp_count = 0
    timezone_assumed_count = 0

    trackpoints = [element for element in root.iter() if _local_name(element.tag) == "trackpoint"]
    for source_index, trackpoint in enumerate(trackpoints):
        time_text = _direct_text(trackpoint, "time")
        if not time_text:
            missing_timestamp_count += 1
            continue
        timestamp, timezone_assumed = _parse_timestamp(
            time_text,
            assume_naive_utc=parser_config.assume_naive_timestamps_utc,
        )
        if timezone_assumed:
            timezone_assumed_count += 1

        raw_points.append(
            {
                "source_index": source_index,
                "timestamp": timestamp,
                "heart_rate_bpm": _extract_heart_rate(trackpoint),
                "distance_m": _direct_number(trackpoint, "distancemeters"),
                "speed_mps": _descendant_number(
                    trackpoint, ("speed", "speedmeterspersecond")
                ),
                "cadence": _first_not_none(
                    _direct_number(trackpoint, "cadence"),
                    _descendant_number(trackpoint, ("runcadence",)),
                ),
                "altitude_m": _direct_number(trackpoint, "altitudemeters"),
                "grade": _descendant_number(trackpoint, ("grade",)),
                "power_w": _descendant_number(
                    trackpoint, ("watts", "power", "powerwatts")
                ),
                "quality_flags": {"NAIVE_TIMESTAMP_ASSUMED_UTC"}
                if timezone_assumed
                else set(),
            }
        )

    if not raw_points:
        raise TCXParseError("TCX contains no trackpoints with valid timestamps")

    unique_points, duplicate_count = _deduplicate_points(raw_points)
    start_time = unique_points[0]["timestamp"]
    previous_timestamp: datetime | None = None
    long_gap_count = 0
    dt_values: list[float] = []
    hr_samples: list[HRInputSample] = []
    reference_samples: list[ReferenceSample] = []

    for point in unique_points:
        timestamp = point["timestamp"]
        elapsed_s = (timestamp - start_time).total_seconds()
        dt_s = (
            0.0
            if previous_timestamp is None
            else (timestamp - previous_timestamp).total_seconds()
        )
        if previous_timestamp is not None:
            dt_values.append(dt_s)
            if dt_s > parser_config.long_gap_threshold_s:
                point["quality_flags"].add("LONG_GAP")
                long_gap_count += 1
        previous_timestamp = timestamp

        if point["heart_rate_bpm"] is None:
            point["quality_flags"].add("MISSING_HR")

        # Construct the objects independently: no row/dict is shared between
        # the HR-only and reference sides of the result.
        hr_samples.append(
            HRInputSample(
                timestamp=timestamp,
                heart_rate_bpm=point["heart_rate_bpm"],
                quality_flags=tuple(sorted(point["quality_flags"])),
            )
        )
        reference_samples.append(
            ReferenceSample(
                timestamp=timestamp,
                elapsed_s=elapsed_s,
                dt_s=dt_s,
                distance_m=point["distance_m"],
                speed_mps=point["speed_mps"],
                cadence=point["cadence"],
                altitude_m=point["altitude_m"],
                grade=point["grade"],
                power_w=point["power_w"],
            )
        )

    missing_hr_count = sum(sample.heart_rate_bpm is None for sample in hr_samples)
    reference_count = len(reference_samples)
    channel_coverage = {
        channel: (
            sum(getattr(sample, channel) is not None for sample in reference_samples)
            / reference_count
        )
        for channel in ("distance_m", "altitude_m", "grade")
    }
    altitude_distance_joint_coverage = (
        sum(
            sample.altitude_m is not None and sample.distance_m is not None
            for sample in reference_samples
        )
        / reference_count
    )
    available_channels = tuple(
        channel
        for channel in (
            "distance_m",
            "speed_mps",
            "cadence",
            "altitude_m",
            "grade",
            "power_w",
        )
        if any(getattr(sample, channel) is not None for sample in reference_samples)
    )

    laps = _extract_laps(
        root,
        sport=sport,
        assume_naive_utc=parser_config.assume_naive_timestamps_utc,
    )
    total_unique = len(unique_points)
    duration_s = (unique_points[-1]["timestamp"] - start_time).total_seconds()
    regular_count = sum(
        abs(dt - parser_config.regularity_target_s)
        <= parser_config.regularity_tolerance_s
        for dt in dt_values
    )
    sampling_regularity_fraction = (
        regular_count / len(dt_values) if dt_values else None
    )

    diagnostic_flags: set[str] = set()
    warnings: list[str] = []
    if missing_hr_count:
        diagnostic_flags.add("MISSING_HR")
        warnings.append(f"{missing_hr_count} timestamped trackpoint(s) have no HR")
    if duplicate_count:
        diagnostic_flags.add("DUPLICATE_TIMESTAMP")
        warnings.append(f"{duplicate_count} duplicate timestamp(s) were merged")
    if long_gap_count:
        diagnostic_flags.add("LONG_GAP")
    if timezone_assumed_count:
        diagnostic_flags.add("NAIVE_TIMESTAMP_ASSUMED_UTC")
    if not available_channels:
        warnings.append("No optional reference channels were found")

    diagnostics = TCXParseDiagnostics(
        trackpoint_count=len(trackpoints),
        unique_timestamp_count=total_unique,
        duplicate_timestamp_count=duplicate_count,
        missing_timestamp_count=missing_timestamp_count,
        missing_hr_count=missing_hr_count,
        timezone_assumed_count=timezone_assumed_count,
        long_gap_count=long_gap_count,
        hr_coverage_fraction=(total_unique - missing_hr_count) / total_unique,
        median_dt_s=median(dt_values) if dt_values else None,
        min_dt_s=min(dt_values) if dt_values else None,
        max_dt_s=max(dt_values) if dt_values else None,
        sampling_regularity_fraction=sampling_regularity_fraction,
        start_time=start_time,
        end_time=unique_points[-1]["timestamp"],
        duration_s=duration_s,
        flags=tuple(sorted(diagnostic_flags)),
        warnings=tuple(warnings),
    )
    reference = ReferenceChannels(
        samples=tuple(reference_samples),
        laps=laps,
        sport=sport,
        available_channels=available_channels,
        metadata={
            "source_format": "TCX",
            "reference_only": True,
            "timestamp_timezone": "UTC",
            # Grade remains raw reference data here.  These explicit fields
            # let the post-core terrain layer choose a ready TCX grade or
            # derive one later from altitude/distance without guessing that a
            # missing grade means flat terrain.
            "grade_source": (
                "tcx_grade" if channel_coverage["grade"] > 0.0 else "unavailable"
            ),
            "grade_unit": (
                "percent" if channel_coverage["grade"] > 0.0 else None
            ),
            "grade_is_derived": False,
            "grade_coverage_fraction": channel_coverage["grade"],
            "altitude_coverage_fraction": channel_coverage["altitude_m"],
            "distance_coverage_fraction": channel_coverage["distance_m"],
            "altitude_distance_joint_coverage_fraction": (
                altitude_distance_joint_coverage
            ),
        },
    )
    return TCXParseResult(tuple(hr_samples), reference, diagnostics)


def _read_payload(
    source: bytes | bytearray | memoryview | str | os.PathLike[str] | BinaryIO | TextIO,
    *,
    max_bytes: int,
) -> bytes:
    if isinstance(source, (bytes, bytearray, memoryview)):
        payload = bytes(source)
    elif isinstance(source, os.PathLike):
        path = Path(source)
        if path.stat().st_size > max_bytes:
            raise TCXParseError(f"TCX exceeds the {max_bytes}-byte size limit")
        payload = path.read_bytes()
    elif isinstance(source, str):
        if source.lstrip().startswith("<"):
            payload = source.encode("utf-8")
        else:
            path = Path(source)
            if path.stat().st_size > max_bytes:
                raise TCXParseError(f"TCX exceeds the {max_bytes}-byte size limit")
            payload = path.read_bytes()
    elif hasattr(source, "read"):
        # Limit the read where possible.  Some upload wrappers only implement
        # read() without a size argument, so retain the length check below.
        try:
            raw = source.read(max_bytes + 1)  # type: ignore[call-arg]
        except TypeError:
            raw = source.read()  # type: ignore[call-arg]
        payload = raw.encode("utf-8") if isinstance(raw, str) else bytes(raw)
    else:
        raise TypeError("source must be TCX bytes, text, a path, or a readable file")

    if len(payload) > max_bytes:
        raise TCXParseError(f"TCX exceeds the {max_bytes}-byte size limit")
    if not payload.strip():
        raise TCXParseError("TCX payload is empty")
    return payload


def _reject_unsafe_xml(payload: bytes) -> None:
    # Removing NUL bytes catches the same declarations in UTF-16 payloads.
    declaration_scan = payload.replace(b"\x00", b"")
    if _FORBIDDEN_XML_DECLARATIONS.search(declaration_scan):
        raise TCXSecurityError("TCX files containing DTD/entity declarations are rejected")


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].split(":", 1)[-1].lower()


def _direct_text(element: ET.Element, name: str) -> str | None:
    wanted = name.lower()
    for child in element:
        if _local_name(child.tag) == wanted and child.text and child.text.strip():
            return child.text.strip()
    return None


def _safe_number(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        number = float(value.strip())
    except (AttributeError, TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _direct_number(element: ET.Element, name: str) -> float | None:
    return _safe_number(_direct_text(element, name))


def _descendant_number(element: ET.Element, names: tuple[str, ...]) -> float | None:
    wanted = {name.lower() for name in names}
    for descendant in element.iter():
        if descendant is element:
            continue
        if _local_name(descendant.tag) in wanted:
            number = _safe_number(descendant.text)
            if number is not None:
                return number
    return None


def _extract_heart_rate(trackpoint: ET.Element) -> float | None:
    # Normal TCX: HeartRateBpm/Value.  Several exporters instead place HR or
    # HeartRate in an extension namespace, so those leaf forms are accepted.
    for child in trackpoint.iter():
        if child is trackpoint:
            continue
        name = _local_name(child.tag)
        if name in {"heartratebpm", "heartrate"}:
            own_value = _safe_number(child.text)
            if own_value is not None:
                return own_value
            nested_value = _descendant_number(child, ("value", "hr"))
            if nested_value is not None:
                return nested_value
    return _descendant_number(trackpoint, ("hr", "heartratebpm", "heartrate"))


def _parse_timestamp(value: str, *, assume_naive_utc: bool) -> tuple[datetime, bool]:
    normalized = value.strip()
    if normalized.endswith(("Z", "z")):
        normalized = normalized[:-1] + "+00:00"
    try:
        timestamp = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise TCXParseError(f"Invalid TCX timestamp: {value!r}") from exc
    timezone_assumed = timestamp.tzinfo is None or timestamp.utcoffset() is None
    if timezone_assumed:
        if not assume_naive_utc:
            raise TCXParseError(f"Naive TCX timestamp is not allowed: {value!r}")
        timestamp = timestamp.replace(tzinfo=UTC)
    return timestamp.astimezone(UTC), timezone_assumed


def _first_not_none(*values: float | None) -> float | None:
    return next((value for value in values if value is not None), None)


def _deduplicate_points(
    raw_points: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], int]:
    ordered = sorted(raw_points, key=lambda point: (point["timestamp"], point["source_index"]))
    by_timestamp: dict[datetime, dict[str, Any]] = {}
    duplicate_count = 0
    measurement_fields = (
        "heart_rate_bpm",
        "distance_m",
        "speed_mps",
        "cadence",
        "altitude_m",
        "grade",
        "power_w",
    )
    for point in ordered:
        timestamp = point["timestamp"]
        existing = by_timestamp.get(timestamp)
        if existing is None:
            by_timestamp[timestamp] = {
                **point,
                "quality_flags": set(point["quality_flags"]),
            }
            continue
        duplicate_count += 1
        existing["quality_flags"].add("DUPLICATE_TIMESTAMP")
        existing["quality_flags"].update(point["quality_flags"])
        for field_name in measurement_fields:
            if existing[field_name] is None and point[field_name] is not None:
                existing[field_name] = point[field_name]
            elif (
                field_name == "heart_rate_bpm"
                and existing[field_name] is not None
                and point[field_name] is not None
                and existing[field_name] != point[field_name]
            ):
                existing["quality_flags"].add("DUPLICATE_HR_CONFLICT")
    return list(by_timestamp.values()), duplicate_count


def _extract_sport(root: ET.Element) -> str | None:
    for element in root.iter():
        if _local_name(element.tag) == "activity":
            for key, value in element.attrib.items():
                if _local_name(key) == "sport" and value.strip():
                    return value.strip()
    return None


def _extract_laps(
    root: ET.Element,
    *,
    sport: str | None,
    assume_naive_utc: bool,
) -> tuple[TCXLapAnnotation, ...]:
    laps: list[TCXLapAnnotation] = []
    for lap_index, element in enumerate(
        item for item in root.iter() if _local_name(item.tag) == "lap"
    ):
        start_value = next(
            (
                value
                for key, value in element.attrib.items()
                if _local_name(key) == "starttime"
            ),
            None,
        )
        trackpoint_times: list[datetime] = []
        for descendant in element.iter():
            if _local_name(descendant.tag) != "trackpoint":
                continue
            text = _direct_text(descendant, "time")
            if text:
                try:
                    timestamp, _ = _parse_timestamp(
                        text, assume_naive_utc=assume_naive_utc
                    )
                except TCXParseError:
                    continue
                trackpoint_times.append(timestamp)
        if start_value:
            try:
                start_time, _ = _parse_timestamp(
                    start_value, assume_naive_utc=assume_naive_utc
                )
            except TCXParseError:
                start_time = min(trackpoint_times) if trackpoint_times else None
        else:
            start_time = min(trackpoint_times) if trackpoint_times else None
        if start_time is None:
            continue
        end_time = max(trackpoint_times) if trackpoint_times else None
        total_time_s = _direct_number(element, "totaltimeseconds")
        if end_time is None and total_time_s is not None:
            from datetime import timedelta

            end_time = start_time + timedelta(seconds=total_time_s)
        laps.append(
            TCXLapAnnotation(
                annotation_id=f"tcx-lap-{lap_index + 1}",
                start_time=start_time,
                end_time=end_time,
                total_time_s=total_time_s,
                distance_m=_direct_number(element, "distancemeters"),
                trigger_method=_direct_text(element, "triggermethod"),
                sport=sport,
            )
        )
    return tuple(sorted(laps, key=lambda lap: (lap.start_time, lap.annotation_id)))


__all__ = [
    "ReferenceChannels",
    "ReferenceSample",
    "TCXLapAnnotation",
    "TCXParseDiagnostics",
    "TCXParseError",
    "TCXParseResult",
    "TCXParserConfig",
    "TCXSecurityError",
    "parse_tcx",
]

