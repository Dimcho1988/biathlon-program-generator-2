from __future__ import annotations

from time import perf_counter

import pytest

from intervals_inspector.onflows_intrazone_load import (
    calculate_onflows_intrazone_load,
)
from intervals_inspector.onflows_zone_profile import (
    default_onflows_zone_profile,
)
from intervals_inspector.stream_normalizer import (
    NormalizerInput,
    approximate_result_size_bytes,
    normalize_stream_intervals,
)


def _normalized(interval_count: int):
    point_count = interval_count + 1
    return normalize_stream_intervals(
        NormalizerInput(
            offsets=range(point_count),
            metrics={
                "heartrate": [
                    100.0 + index % 96 for index in range(point_count)
                ]
            },
        )
    )


def test_ten_thousand_interval_onflows_load_benchmark_is_one_pass() -> None:
    profile = default_onflows_zone_profile()
    half_result = _normalized(5_000)
    full_result = _normalized(10_000)

    half_started = perf_counter()
    half_analysis = calculate_onflows_intrazone_load(half_result, profile)
    half_elapsed = perf_counter() - half_started

    full_started = perf_counter()
    full_analysis = calculate_onflows_intrazone_load(full_result, profile)
    full_elapsed = perf_counter() - full_started

    interval_bytes = approximate_result_size_bytes(full_result)
    aggregate_bytes = approximate_result_size_bytes(full_analysis)
    runtime_ratio = full_elapsed / half_elapsed if half_elapsed else 0.0
    print(
        "onflows_intrazone_benchmark "
        f"intervals=10000 half_sec={half_elapsed:.6f} "
        f"full_sec={full_elapsed:.6f} runtime_ratio={runtime_ratio:.3f} "
        f"crossed_split_points={full_analysis['crossed_split_point_count']} "
        f"interval_result_bytes={interval_bytes} "
        f"load_aggregate_bytes={aggregate_bytes}"
    )

    # Work counters prove one visit per active interval. Runtime remains a
    # diagnostic measurement and deliberately has no strict CI time limit.
    assert half_analysis["processed_active_interval_count"] == 5_000
    assert full_analysis["processed_active_interval_count"] == 10_000
    assert full_analysis["active_duration_sec"] == 10_000
    assert full_analysis["total_real_sec"] == pytest.approx(10_000)
    assert full_analysis["total_weighted_sec"] >= 10_000
    assert interval_bytes > aggregate_bytes > 0
