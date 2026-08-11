"""Strict, serialisable schemas for the HR-only HRmod v2 model.

The core input deliberately has no representation for reference channels.
Speed, power, grade, distance, cadence, laps, annotations, and sport type
therefore cannot enter wave detection or area redistribution through this API.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from math import isfinite
from typing import Any, Mapping


MODEL_VERSION = "hrmod_wave_area_shift_v2"
CONFIG_VERSION = "hrmod_config_v2"


def _finite(name: str, value: float) -> float:
    value = float(value)
    if not isfinite(value):
        raise ValueError(f"{name} must be finite")
    return value


def _json_ready(value: Any) -> Any:
    """Convert nested dataclass values to JSON-compatible primitives."""

    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, tuple):
        return [_json_ready(item) for item in value]
    if isinstance(value, list):
        return [_json_ready(item) for item in value]
    if isinstance(value, Mapping):
        return {str(key): _json_ready(item) for key, item in value.items()}
    if hasattr(value, "__dataclass_fields__"):
        return _json_ready(asdict(value))
    return value


@dataclass(frozen=True, slots=True)
class HRSample:
    """One measured HR observation accepted by the model core."""

    timestamp: datetime
    heart_rate_bpm: float | None
    quality_flags: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.timestamp, datetime):
            raise TypeError("timestamp must be a datetime")
        if self.timestamp.tzinfo is None or self.timestamp.utcoffset() is None:
            raise ValueError("timestamp must be timezone-aware")
        if self.heart_rate_bpm is not None:
            object.__setattr__(self, "heart_rate_bpm", float(self.heart_rate_bpm))
        object.__setattr__(
            self,
            "quality_flags",
            tuple(sorted({str(flag) for flag in self.quality_flags if str(flag)})),
        )

    @property
    def hr_bpm(self) -> float | None:
        return self.heart_rate_bpm


HRInputSample = HRSample


@dataclass(frozen=True, slots=True)
class HRZone:
    """An individual, lower-inclusive HR zone."""

    name: str
    lower_bpm: float
    upper_bpm: float

    def __post_init__(self) -> None:
        if not str(self.name).strip():
            raise ValueError("zone name must not be empty")
        object.__setattr__(self, "name", str(self.name).strip())
        object.__setattr__(self, "lower_bpm", _finite("zone lower_bpm", self.lower_bpm))
        object.__setattr__(self, "upper_bpm", _finite("zone upper_bpm", self.upper_bpm))
        if self.lower_bpm >= self.upper_bpm:
            raise ValueError("zone lower_bpm must be below upper_bpm")

    @property
    def label(self) -> str:
        return self.name


@dataclass(frozen=True, slots=True)
class AthleteHRProfile:
    """Explicit individual HR bounds and exactly five individual zones."""

    hrmax_bpm: float
    hr_floor_bpm: float
    zones: tuple[HRZone, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "hrmax_bpm", _finite("hrmax_bpm", self.hrmax_bpm))
        object.__setattr__(self, "hr_floor_bpm", _finite("hr_floor_bpm", self.hr_floor_bpm))
        object.__setattr__(self, "zones", tuple(self.zones))
        if self.hr_floor_bpm >= self.hrmax_bpm:
            raise ValueError("hr_floor_bpm must be below hrmax_bpm")
        if len(self.zones) != 5:
            raise ValueError("exactly five individual HR zones are required")
        names = [zone.name for zone in self.zones]
        if len(set(names)) != len(names):
            raise ValueError("HR zone names must be unique")
        for index, zone in enumerate(self.zones):
            if not isinstance(zone, HRZone):
                raise TypeError("zones must contain HRZone values")
            if index and zone.lower_bpm < self.zones[index - 1].upper_bpm:
                raise ValueError("HR zones must be increasing and non-overlapping")
            if zone.lower_bpm < self.hr_floor_bpm or zone.upper_bpm > self.hrmax_bpm:
                raise ValueError("HR zones must lie within HR_floor and HRmax")

    @property
    def hr_max_bpm(self) -> float:
        return self.hrmax_bpm

    def to_dict(self) -> dict[str, Any]:
        return _json_ready(self)


@dataclass(frozen=True, slots=True)
class HRmodConfig:
    """Versioned exploratory settings for wave-area-shift v2.

    The first four user-facing controls are ``alpha``,
    ``rise_threshold_bpm_s``, ``min_rise_bpm``, and ``smoothing_window_s``.
    All remaining values are technical safeguards or transparent HR cleaning
    settings.  These defaults are starting points, not validated physiology.
    """

    config_version: str = CONFIG_VERSION

    # Four main expert controls.
    alpha: float = 1.0
    rise_threshold_bpm_s: float = 0.15
    min_rise_bpm: float = 5.0
    smoothing_window_s: float = 5.0

    # Advanced detection safeguards.
    min_sustained_rise_s: float = 3.0
    fall_threshold_bpm_s: float = 0.10
    min_sustained_fall_s: float = 3.0
    min_fall_bpm: float = 3.0
    baseline_lookback_s: float = 20.0
    baseline_min_points: int = 3
    return_tolerance_bpm: float = 2.0
    return_sustain_s: float = 3.0
    neutral_slope_tolerance_bpm_s: float = 0.05
    neutral_trough_timeout_s: float = 8.0
    min_receiver_duration_s: float = 3.0
    min_donor_duration_s: float = 3.0
    max_wave_duration_s: float = 600.0
    max_interpolation_gap_s: float = 3.0
    long_gap_threshold_s: float = 10.0
    edge_wave_policy: str = "skip_incomplete"
    max_addition_bpm: float | None = None
    max_removal_bpm: float | None = None

    # Robust detection and transparent cleaning details.
    smoothing_method: str = "robust_local_linear"
    smoothing_min_points: int = 3
    smoothing_robust_iterations: int = 2
    artifact_min_hr_bpm: float = 25.0
    artifact_max_hr_bpm: float = 250.0
    artifact_max_rate_bpm_per_s: float = 20.0
    artifact_spike_deviation_bpm: float = 12.0
    sampling_regularity_tolerance_s: float = 0.25
    area_conservation_tolerance_bpm_s: float = 1e-6

    def __post_init__(self) -> None:
        if self.config_version != CONFIG_VERSION:
            raise ValueError(f"unsupported config_version: {self.config_version!r}")
        if self.smoothing_method not in {"robust_local_linear", "local_linear"}:
            raise ValueError("unsupported smoothing_method")
        if self.edge_wave_policy != "skip_incomplete":
            raise ValueError("v2 supports only edge_wave_policy='skip_incomplete'")

        finite_fields = (
            "alpha",
            "rise_threshold_bpm_s",
            "min_rise_bpm",
            "smoothing_window_s",
            "min_sustained_rise_s",
            "fall_threshold_bpm_s",
            "min_sustained_fall_s",
            "min_fall_bpm",
            "baseline_lookback_s",
            "return_tolerance_bpm",
            "return_sustain_s",
            "neutral_slope_tolerance_bpm_s",
            "neutral_trough_timeout_s",
            "min_receiver_duration_s",
            "min_donor_duration_s",
            "max_wave_duration_s",
            "max_interpolation_gap_s",
            "long_gap_threshold_s",
            "artifact_min_hr_bpm",
            "artifact_max_hr_bpm",
            "artifact_max_rate_bpm_per_s",
            "artifact_spike_deviation_bpm",
            "sampling_regularity_tolerance_s",
            "area_conservation_tolerance_bpm_s",
        )
        for name in finite_fields:
            object.__setattr__(self, name, _finite(name, getattr(self, name)))

        if not 0.0 <= self.alpha <= 1.0:
            raise ValueError("alpha must be in [0, 1]")
        positive_fields = (
            "rise_threshold_bpm_s",
            "min_rise_bpm",
            "smoothing_window_s",
            "min_sustained_rise_s",
            "fall_threshold_bpm_s",
            "min_sustained_fall_s",
            "min_fall_bpm",
            "baseline_lookback_s",
            "return_sustain_s",
            "neutral_trough_timeout_s",
            "min_receiver_duration_s",
            "min_donor_duration_s",
            "max_wave_duration_s",
            "long_gap_threshold_s",
            "artifact_max_rate_bpm_per_s",
            "artifact_spike_deviation_bpm",
            "area_conservation_tolerance_bpm_s",
        )
        for name in positive_fields:
            if getattr(self, name) <= 0.0:
                raise ValueError(f"{name} must be positive")
        nonnegative_fields = (
            "return_tolerance_bpm",
            "neutral_slope_tolerance_bpm_s",
            "max_interpolation_gap_s",
            "sampling_regularity_tolerance_s",
        )
        for name in nonnegative_fields:
            if getattr(self, name) < 0.0:
                raise ValueError(f"{name} must be non-negative")
        if self.max_interpolation_gap_s > self.long_gap_threshold_s:
            raise ValueError("max_interpolation_gap_s cannot exceed long_gap_threshold_s")
        if self.artifact_min_hr_bpm >= self.artifact_max_hr_bpm:
            raise ValueError("artifact HR minimum must be below maximum")

        for name, minimum in (
            ("baseline_min_points", 1),
            ("smoothing_min_points", 2),
            ("smoothing_robust_iterations", 0),
        ):
            value = int(getattr(self, name))
            if value < minimum:
                raise ValueError(f"{name} must be at least {minimum}")
            object.__setattr__(self, name, value)

        for name in ("max_addition_bpm", "max_removal_bpm"):
            value = getattr(self, name)
            if value is not None:
                value = _finite(name, value)
                if value <= 0.0:
                    raise ValueError(f"{name} must be positive when enabled")
                object.__setattr__(self, name, value)

    def to_dict(self) -> dict[str, Any]:
        return _json_ready(self)


@dataclass(frozen=True, slots=True)
class HRmodTimeseriesPoint:
    timestamp: datetime
    elapsed_s: float
    dt_s: float
    raw_hr_bpm: float | None
    clean_hr_bpm: float | None
    h_detect_bpm: float | None
    trend_bpm_per_s: float | None
    segment_id: int | None
    wave_id: int | None
    wave_state: str
    local_baseline_hr_bpm: float | None
    receiver_flag: bool
    donor_flag: bool
    added_bpm: float
    removed_bpm: float
    hrmod_bpm: float | None
    raw_hr_zone: str | None
    clean_hr_zone: str | None
    hrmod_zone: str | None
    quality_flags: tuple[str, ...] = ()
    model_flags: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return _json_ready(self)


@dataclass(frozen=True, slots=True)
class WaveSummary:
    wave_id: int
    segment_id: int
    status: str
    complete: bool
    corrected: bool
    rise_start_timestamp: datetime
    peak_timestamp: datetime
    tail_end_timestamp: datetime
    rise_start_elapsed_s: float
    peak_elapsed_s: float
    tail_end_elapsed_s: float
    end_reason: str
    baseline_hr_bpm: float | None
    donor_floor_bpm: float | None
    rise_bpm: float
    fall_bpm: float
    receiver_duration_s: float
    donor_duration_s: float
    donor_available_area_bpm_s: float
    requested_area_bpm_s: float
    receiver_capacity_bpm_s: float
    moved_area_bpm_s: float
    moved_fraction_of_donor: float
    added_area_bpm_s: float
    removed_area_bpm_s: float
    area_balance_error_bpm_s: float
    capacity_limited_area_bpm_s: float
    capacity_limited: bool
    skip_reason: str | None
    raw_zone_seconds: Mapping[str, float] = field(default_factory=dict)
    clean_zone_seconds: Mapping[str, float] = field(default_factory=dict)
    hrmod_zone_seconds: Mapping[str, float] = field(default_factory=dict)
    hrmod_minus_raw_zone_seconds: Mapping[str, float] = field(default_factory=dict)
    hrmod_minus_clean_zone_seconds: Mapping[str, float] = field(default_factory=dict)
    flags: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return _json_ready(self)


@dataclass(frozen=True, slots=True)
class ZoneSummary:
    zone_name: str
    lower_bpm: float
    upper_bpm: float
    raw_seconds: float
    raw_percent: float
    clean_seconds: float
    clean_percent: float
    hrmod_seconds: float
    hrmod_percent: float
    hrmod_minus_clean_seconds: float

    @property
    def zone_label(self) -> str:
        return self.zone_name

    def to_dict(self) -> dict[str, Any]:
        return _json_ready(self)


@dataclass(frozen=True, slots=True)
class HRmodDiagnostics:
    flags: tuple[str, ...]
    total_samples: int
    valid_raw_samples: int
    clean_samples: int
    hr_coverage_fraction: float
    sampling_interval_count: int
    mean_dt_s: float | None
    median_dt_s: float | None
    dt_cv: float | None
    regular_sampling_fraction: float | None
    interpolated_samples: int
    interpolated_fraction: float
    artifact_samples: int
    artifact_fraction: float
    detection_supported_samples: int
    detection_support_fraction: float
    segment_count: int
    long_gap_count: int
    edge_affected_samples: int
    gap_affected_samples: int
    detected_wave_count: int
    complete_wave_count: int
    incomplete_wave_count: int
    corrected_wave_count: int
    skipped_wave_count: int
    total_donor_available_area_bpm_s: float
    total_requested_area_bpm_s: float
    total_receiver_capacity_bpm_s: float
    total_moved_area_bpm_s: float
    total_added_area_bpm_s: float
    total_removed_area_bpm_s: float
    total_area_balance_error_bpm_s: float
    max_abs_area_balance_error_bpm_s: float
    total_capacity_limited_area_bpm_s: float
    moved_fraction_of_donor: float
    capacity_limited_wave_count: int
    skip_reason_counts: Mapping[str, int] = field(default_factory=dict)
    area_conservation_passed: bool = True
    parameter_sensitivity: Mapping[str, Any] = field(
        default_factory=lambda: {"status": "not_computed_in_single_run"}
    )

    def to_dict(self) -> dict[str, Any]:
        return _json_ready(self)


@dataclass(frozen=True, slots=True)
class HRmodResult:
    timeseries: tuple[HRmodTimeseriesPoint, ...]
    wave_summary: tuple[WaveSummary, ...]
    zone_summary: tuple[ZoneSummary, ...]
    diagnostics: HRmodDiagnostics
    config: HRmodConfig
    hr_input_hash: str
    model_version: str = MODEL_VERSION

    @property
    def waves(self) -> tuple[WaveSummary, ...]:
        return self.wave_summary

    @property
    def zones(self) -> tuple[ZoneSummary, ...]:
        return self.zone_summary

    def to_dict(self) -> dict[str, Any]:
        return _json_ready(self)


__all__ = [
    "AthleteHRProfile",
    "CONFIG_VERSION",
    "HRInputSample",
    "HRmodConfig",
    "HRmodDiagnostics",
    "HRmodResult",
    "HRmodTimeseriesPoint",
    "HRSample",
    "HRZone",
    "MODEL_VERSION",
    "WaveSummary",
    "ZoneSummary",
]
