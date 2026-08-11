"""Strict, serialisable schemas for the HR-only HRmod model.

The input types in this module deliberately contain no reference-channel fields.
In particular, speed, power, grade, distance, cadence, laps, and annotations
cannot be supplied to :func:`compute_hrmod_hr_only` through these schemas.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from math import isfinite
from typing import Any, Mapping


MODEL_VERSION = "hrmod_inverse_kinetics_conservative_v1"
CONFIG_VERSION = "hrmod_config_v1"


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
    """One measured HR observation accepted by the model core.

    ``quality_flags`` may describe HR provenance/quality only.  Reference data
    intentionally has no representation in this type.
    """

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
        """Concise read-only alias used by a few adapters."""

        return self.heart_rate_bpm


# Explicit semantic alias for TCX adapters.  It is the same strict class, not a
# broader adapter row containing reference channels.
HRInputSample = HRSample


@dataclass(frozen=True, slots=True)
class HRZone:
    """An individual, lower-inclusive HR zone.

    The upper boundary is exclusive except for the final zone in a profile.
    """

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
    """Explicit individual bounds and exactly five individual HR zones."""

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

    @property
    def hr_max_bpm(self) -> float:
        """Read-only spelling alias; ``hrmax_bpm`` remains canonical."""

        return self.hrmax_bpm

    def to_dict(self) -> dict[str, Any]:
        return _json_ready(self)


@dataclass(frozen=True, slots=True)
class HRmodConfig:
    """Versioned exploratory settings for the conservative v1 model.

    These defaults are implementation starting points, not validated
    physiological constants.
    """

    config_version: str = CONFIG_VERSION
    kernel_model: str = "first_order_inverse"
    alpha: float = 1.0
    delay_s: float = 5.0
    tau_on_s: float = 30.0
    tau_off_s: float = 45.0
    smoothing_method: str = "robust_local_linear"
    smoothing_window_s: float = 15.0
    smoothing_min_points: int = 5
    smoothing_robust_iterations: int = 2
    correction_deadband_bpm: float = 0.5
    min_lobe_duration_s: float = 3.0
    min_lobe_area_bpm_s: float = 5.0
    episode_neutral_gap_s: float = 15.0
    episode_balance_tolerance_bpm_s: float = 5.0
    max_episode_duration_s: float = 900.0
    max_interpolation_gap_s: float = 3.0
    long_gap_threshold_s: float = 10.0
    edge_episode_policy: str = "skip_incomplete"
    max_addition_bpm: float | None = None
    max_removal_bpm: float | None = None
    artifact_min_hr_bpm: float = 25.0
    artifact_max_hr_bpm: float = 250.0
    artifact_max_rate_bpm_per_s: float = 20.0
    artifact_spike_deviation_bpm: float = 12.0
    sampling_regularity_tolerance_s: float = 0.25
    area_conservation_tolerance_bpm_s: float = 1e-6

    def __post_init__(self) -> None:
        if self.config_version != CONFIG_VERSION:
            raise ValueError(f"unsupported config_version: {self.config_version!r}")
        if self.kernel_model != "first_order_inverse":
            raise ValueError("v1 supports only kernel_model='first_order_inverse'")
        if self.smoothing_method not in {"robust_local_linear", "local_linear"}:
            raise ValueError("unsupported smoothing_method")
        if self.edge_episode_policy not in {"skip_incomplete", "correct_if_balanced"}:
            raise ValueError("unsupported edge_episode_policy")
        for name in (
            "alpha",
            "delay_s",
            "tau_on_s",
            "tau_off_s",
            "smoothing_window_s",
            "correction_deadband_bpm",
            "min_lobe_duration_s",
            "min_lobe_area_bpm_s",
            "episode_neutral_gap_s",
            "episode_balance_tolerance_bpm_s",
            "max_episode_duration_s",
            "max_interpolation_gap_s",
            "long_gap_threshold_s",
            "artifact_min_hr_bpm",
            "artifact_max_hr_bpm",
            "artifact_max_rate_bpm_per_s",
            "artifact_spike_deviation_bpm",
            "sampling_regularity_tolerance_s",
            "area_conservation_tolerance_bpm_s",
        ):
            object.__setattr__(self, name, _finite(name, getattr(self, name)))
        if not 0.0 <= self.alpha <= 1.0:
            raise ValueError("alpha must be in [0, 1] in conservative mode")
        for name in (
            "delay_s",
            "correction_deadband_bpm",
            "min_lobe_duration_s",
            "min_lobe_area_bpm_s",
            "episode_neutral_gap_s",
            "episode_balance_tolerance_bpm_s",
            "max_interpolation_gap_s",
            "sampling_regularity_tolerance_s",
        ):
            if getattr(self, name) < 0.0:
                raise ValueError(f"{name} must be non-negative")
        for name in (
            "tau_on_s",
            "tau_off_s",
            "smoothing_window_s",
            "max_episode_duration_s",
            "long_gap_threshold_s",
            "artifact_max_rate_bpm_per_s",
            "artifact_spike_deviation_bpm",
            "area_conservation_tolerance_bpm_s",
        ):
            if getattr(self, name) <= 0.0:
                raise ValueError(f"{name} must be positive")
        if self.max_interpolation_gap_s > self.long_gap_threshold_s:
            raise ValueError("max_interpolation_gap_s cannot exceed long_gap_threshold_s")
        if self.artifact_min_hr_bpm >= self.artifact_max_hr_bpm:
            raise ValueError("artifact HR minimum must be below maximum")
        if int(self.smoothing_min_points) < 2:
            raise ValueError("smoothing_min_points must be at least 2")
        if int(self.smoothing_robust_iterations) < 0:
            raise ValueError("smoothing_robust_iterations must be non-negative")
        object.__setattr__(self, "smoothing_min_points", int(self.smoothing_min_points))
        object.__setattr__(
            self, "smoothing_robust_iterations", int(self.smoothing_robust_iterations)
        )
        for name in ("max_addition_bpm", "max_removal_bpm"):
            value = getattr(self, name)
            if value is not None:
                value = _finite(name, value)
                if value < 0.0:
                    raise ValueError(f"{name} must be non-negative when enabled")
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
    smoothed_hr_bpm: float | None
    derivative_bpm_per_s: float | None
    lookahead_hr_bpm: float | None
    provisional_demand_bpm: float | None
    raw_correction_bpm: float | None
    added_correction_bpm: float
    removed_correction_bpm: float
    hrmod_bpm: float | None
    segment_id: int | None
    episode_id: int | None
    episode_state: str
    raw_hr_zone: str | None
    clean_hr_zone: str | None
    hrmod_zone: str | None
    quality_flags: tuple[str, ...] = ()
    model_flags: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return _json_ready(self)


@dataclass(frozen=True, slots=True)
class EpisodeSummary:
    episode_id: int
    segment_id: int
    start_timestamp: datetime
    end_timestamp: datetime
    start_elapsed_s: float
    end_elapsed_s: float
    duration_s: float
    state: str
    complete: bool
    corrected: bool
    incomplete_reason: str | None
    lobe_count: int
    positive_lobe_count: int
    negative_lobe_count: int
    positive_area_bpm_s: float
    negative_area_bpm_s: float
    target_balanced_area_bpm_s: float
    moved_area_bpm_s: float
    added_area_bpm_s: float
    removed_area_bpm_s: float
    area_balance_error_bpm_s: float
    capacity_limited_area_bpm_s: float
    unpaired_positive_area_bpm_s: float
    unpaired_negative_area_bpm_s: float
    positive_capacity_bpm_s: float
    negative_capacity_bpm_s: float
    capacity_ratio: float
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
    derivative_supported_samples: int
    derivative_support_fraction: float
    segment_count: int
    long_gap_count: int
    edge_affected_samples: int
    gap_affected_samples: int
    complete_episode_count: int
    incomplete_episode_count: int
    corrected_episode_count: int
    total_positive_area_bpm_s: float
    total_negative_area_bpm_s: float
    total_added_area_bpm_s: float
    total_removed_area_bpm_s: float
    total_area_balance_error_bpm_s: float
    max_abs_area_balance_error_bpm_s: float
    total_capacity_limited_area_bpm_s: float
    total_unpaired_positive_area_bpm_s: float
    total_unpaired_negative_area_bpm_s: float
    capacity_ratio: float
    area_conservation_passed: bool
    parameter_sensitivity: Mapping[str, Any] = field(
        default_factory=lambda: {"status": "not_computed_in_single_run"}
    )

    def to_dict(self) -> dict[str, Any]:
        return _json_ready(self)


@dataclass(frozen=True, slots=True)
class HRmodResult:
    timeseries: tuple[HRmodTimeseriesPoint, ...]
    episode_summary: tuple[EpisodeSummary, ...]
    zone_summary: tuple[ZoneSummary, ...]
    diagnostics: HRmodDiagnostics
    config: HRmodConfig
    hr_input_hash: str
    model_version: str = MODEL_VERSION

    @property
    def episodes(self) -> tuple[EpisodeSummary, ...]:
        return self.episode_summary

    @property
    def zones(self) -> tuple[ZoneSummary, ...]:
        return self.zone_summary

    def to_dict(self) -> dict[str, Any]:
        return _json_ready(self)


__all__ = [
    "AthleteHRProfile",
    "CONFIG_VERSION",
    "EpisodeSummary",
    "HRInputSample",
    "HRmodConfig",
    "HRmodDiagnostics",
    "HRmodResult",
    "HRmodTimeseriesPoint",
    "HRSample",
    "HRZone",
    "MODEL_VERSION",
    "ZoneSummary",
]
