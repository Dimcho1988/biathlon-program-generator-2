from __future__ import annotations

from time import perf_counter

from intervals_inspector.stream_normalizer import (
    NormalizerInput,
    approximate_result_size_bytes,
    materialize_1hz,
    normalize_stream_intervals,
)


def test_ten_thousand_point_interval_and_materialization_performance() -> None:
    offsets: list[int] = []
    speed: list[float] = []
    heartrate: list[float] = []
    current = 0
    for index in range(10_000):
        offsets.append(current)
        speed.append(0.0 if index in {2_500, 7_500} else 2.5)
        heartrate.append(120.0 + index % 40)
        if index in {2_500, 7_500}:
            current += 120
        elif index % 97 == 0:
            current += 5
        elif index % 31 == 0:
            current += 2
        else:
            current += 1

    normalizer_input = NormalizerInput(
        offsets=offsets,
        metrics={
            "velocity_smooth": speed,
            "heartrate": heartrate,
        },
        elapsed_time_sec=float(offsets[-1]),
        icu_recording_time_sec=float(offsets[-1] - 240),
        moving_time_sec=float(offsets[-1] - 300),
    )

    half_input = NormalizerInput(
        offsets=offsets[:5_000],
        metrics={
            "velocity_smooth": speed[:5_000],
            "heartrate": heartrate[:5_000],
        },
    )
    half_started = perf_counter()
    half_result = normalize_stream_intervals(half_input)
    half_elapsed = perf_counter() - half_started
    half_size = approximate_result_size_bytes(half_result)

    interval_started = perf_counter()
    interval_result = normalize_stream_intervals(normalizer_input)
    interval_elapsed = perf_counter() - interval_started
    interval_size = approximate_result_size_bytes(interval_result)

    materialize_started = perf_counter()
    one_hz_result = materialize_1hz(interval_result)
    materialize_elapsed = perf_counter() - materialize_started
    one_hz_size = approximate_result_size_bytes(one_hz_result)

    print(
        "normalizer_performance "
        f"half_sec={half_elapsed:.6f} half_bytes={half_size} "
        f"points=10000 interval_sec={interval_elapsed:.6f} "
        f"interval_bytes={interval_size} "
        f"materialize_sec={materialize_elapsed:.6f} "
        f"materialized_bytes={one_hz_size}"
    )

    assert interval_result.input_point_count == 10_000
    assert len(interval_result.intervals) == 9_999
    assert one_hz_result.active_duration_sec == interval_result.active_duration_sec
    assert interval_elapsed < 10.0
    assert materialize_elapsed < 10.0
    assert interval_elapsed <= max(half_elapsed * 4.5, 0.5)
    assert interval_size <= half_size * 2.5
    assert interval_size > 0
    assert one_hz_size > 0
