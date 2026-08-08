from __future__ import annotations

from time import perf_counter

from intervals_inspector.hr_zone_engine import HRZone, calculate_hr_zone_time
from intervals_inspector.stream_normalizer import (
    NormalizerInput,
    approximate_result_size_bytes,
    normalize_stream_intervals,
)


ZONES = (
    HRZone("Z1", 0, 120, lower_inclusive=False),
    HRZone("Z2", 120, 150),
    HRZone("Z3", 150, 180),
    HRZone("Z4", 180, 220, upper_inclusive=True),
)


def _normalized(interval_count: int):
    point_count = interval_count + 1
    return normalize_stream_intervals(
        NormalizerInput(
            offsets=range(point_count),
            metrics={
                "heartrate": [130.0 + index % 40 for index in range(point_count)]
            },
        )
    )


def test_ten_thousand_interval_hr_zone_benchmark_is_one_pass() -> None:
    half_result = _normalized(5_000)
    full_result = _normalized(10_000)

    half_started = perf_counter()
    half_analysis = calculate_hr_zone_time(half_result, ZONES)
    half_elapsed = perf_counter() - half_started

    full_started = perf_counter()
    full_analysis = calculate_hr_zone_time(full_result, ZONES)
    full_elapsed = perf_counter() - full_started

    interval_bytes = approximate_result_size_bytes(full_result)
    aggregate_bytes = approximate_result_size_bytes(full_analysis)
    runtime_ratio = full_elapsed / half_elapsed if half_elapsed else 0.0
    print(
        "hr_zone_interval_benchmark "
        f"intervals=10000 half_sec={half_elapsed:.6f} "
        f"full_sec={full_elapsed:.6f} runtime_ratio={runtime_ratio:.3f} "
        f"interval_result_bytes={interval_bytes} "
        f"zone_aggregate_bytes={aggregate_bytes}"
    )

    # Deterministic work counters confirm one visit per active interval.  The
    # measured runtime ratio is diagnostic only and intentionally has no
    # unstable wall-clock threshold in CI.
    assert half_analysis["processed_active_interval_count"] == 5_000
    assert full_analysis["processed_active_interval_count"] == 10_000
    assert full_analysis["active_duration_sec"] == 10_000
    assert full_analysis["classified_hr_sec"] == 10_000
    assert full_analysis["unclassified_hr_sec"] == 0
    assert interval_bytes > aggregate_bytes > 0
