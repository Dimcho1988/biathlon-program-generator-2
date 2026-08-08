from __future__ import annotations

from dataclasses import dataclass

import pytest

from intervals_inspector.stream_normalizer import (
    NormalizerInput,
    build_normalizer_summary,
    materialize_1hz,
    normalize_stream_intervals,
)


@dataclass(frozen=True)
class SyntheticScenario:
    normalizer_input: NormalizerInput
    expected_classes: tuple[str, ...]
    expected_active_sec: float


@pytest.fixture
def anonymous_scenarios() -> dict[str, SyntheticScenario]:
    """Minimal synthetic shapes only; no real activity or identity data."""

    return {
        "clean_1hz": SyntheticScenario(
            NormalizerInput(
                offsets=[0, 1, 2, 3],
                metrics={"heartrate": [120, 121, 122, 123]},
            ),
            ("original_1hz",) * 3,
            3.0,
        ),
        "smart_recording": SyntheticScenario(
            NormalizerInput(
                offsets=[0, 3, 6],
                metrics={"heartrate": [120, 123, 126]},
            ),
            ("smart_recording", "smart_recording"),
            6.0,
        ),
        "recording_stop": SyntheticScenario(
            NormalizerInput(
                offsets=[0, 1, 40, 41],
                metrics={"heartrate": [120, 121, 122, 123]},
                recording_stop_bounds=frozenset({(1, 40)}),
                recording_stop_marker_count=1,
            ),
            ("original_1hz", "recording_stop", "original_1hz"),
            2.0,
        ),
        "pause_and_uncertain": SyntheticScenario(
            NormalizerInput(
                offsets=[0, 1, 41, 42, 60, 61],
                metrics={
                    "heartrate": [120, 121, 122, 123, 124, 125],
                    "velocity_smooth": [2, 0, 2, 2, 2, 2],
                },
            ),
            (
                "original_1hz",
                "probable_pause",
                "original_1hz",
                "uncertain_gap",
                "original_1hz",
            ),
            3.0,
        ),
        "technical_389_sec": SyntheticScenario(
            NormalizerInput(
                offsets=[0, 1, 390, 391],
                metrics={
                    "heartrate": [120, 121, 122, 123],
                    "velocity_smooth": [2, 2, 2, 2],
                },
            ),
            (
                "original_1hz",
                "technical_or_unexplained_gap",
                "original_1hz",
            ),
            2.0,
        ),
        "active_missing_hr": SyntheticScenario(
            NormalizerInput(
                offsets=[0, 1, 2],
                metrics={"heartrate": [120, None, 122]},
            ),
            ("original_1hz", "original_1hz"),
            2.0,
        ),
    }


def test_anonymous_regression_shapes_remain_conservative_and_deterministic(
    anonymous_scenarios: dict[str, SyntheticScenario],
) -> None:
    for scenario in anonymous_scenarios.values():
        first = normalize_stream_intervals(scenario.normalizer_input)
        second = normalize_stream_intervals(scenario.normalizer_input)

        assert tuple(
            interval.classification for interval in first.intervals
        ) == scenario.expected_classes
        assert first.active_duration_sec == scenario.expected_active_sec
        assert first == second
        assert sum(
            interval.dt_sec
            for interval in first.intervals
            if interval.recording_segment_id is not None
        ) == scenario.expected_active_sec


def test_regression_excluded_intervals_never_materialize_points(
    anonymous_scenarios: dict[str, SyntheticScenario],
) -> None:
    for name in (
        "recording_stop",
        "pause_and_uncertain",
        "technical_389_sec",
    ):
        result = normalize_stream_intervals(
            anonymous_scenarios[name].normalizer_input
        )
        materialized = materialize_1hz(result)
        excluded_ranges = [
            (interval.start_offset_sec, interval.end_offset_sec)
            for interval in result.intervals
            if interval.recording_segment_id is None
        ]

        assert all(
            not (start < point.offset_sec < end)
            for point in materialized.points
            for start, end in excluded_ranges
        )
        assert materialized.active_duration_sec == result.active_duration_sec


def test_clean_1hz_regression_uses_identity_fast_path_without_copy(
    anonymous_scenarios: dict[str, SyntheticScenario],
) -> None:
    result = normalize_stream_intervals(
        anonymous_scenarios["clean_1hz"].normalizer_input
    )

    assert result.fast_path_used is True
    assert build_normalizer_summary(result)["materialize_1hz"][
        "requested"
    ] is False
    materialized = materialize_1hz(result)
    assert materialized.points is result.points
    assert materialized.created_new_points is False


def test_missing_hr_remains_missing_on_active_interval(
    anonymous_scenarios: dict[str, SyntheticScenario],
) -> None:
    result = normalize_stream_intervals(
        anonymous_scenarios["active_missing_hr"].normalizer_input
    )

    assert result.intervals[0].right.value("heartrate") is None
    assert result.intervals[1].left.value("heartrate") is None
    assert all(
        interval.recording_segment_id is not None
        for interval in result.intervals
    )
