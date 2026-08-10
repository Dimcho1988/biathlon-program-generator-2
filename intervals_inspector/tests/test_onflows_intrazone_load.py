from __future__ import annotations

import math

import pytest

from intervals_inspector.hr_zone_engine import calculate_hr_zone_time
from intervals_inspector.intervals_hr_adapter import adapt_intervals_hr_zones
from intervals_inspector.onflows_intrazone_load import (
    ALGORITHM_VERSION,
    calculate_onflows_intrazone_load,
    equivalence_coefficient,
)
from intervals_inspector.onflows_zone_profile import (
    INTRA_ZONE_EQUIVALENCE_VERSION,
    MANUAL_PROFILE_SOURCE,
    build_onflows_zone_profile,
    default_onflows_zone_profile,
)
from intervals_inspector.stream_normalizer import (
    NormalizerInput,
    normalize_stream_intervals,
)


def _result(
    offsets: list[float],
    heartrate: list[object] | None,
    *,
    speed: list[float] | None = None,
    stop_bounds: set[tuple[float, float]] | None = None,
    elapsed: float | None = None,
    recording: float | None = None,
):
    metrics: dict[str, list[object]] = {}
    if heartrate is not None:
        metrics["heartrate"] = heartrate
    if speed is not None:
        metrics["velocity_smooth"] = speed
    return normalize_stream_intervals(
        NormalizerInput(
            offsets=offsets,
            metrics=metrics,
            recording_stop_bounds=frozenset(stop_bounds or set()),
            recording_stop_marker_count=len(stop_bounds or set()),
            elapsed_time_sec=elapsed,
            icu_recording_time_sec=recording,
        )
    )


def _single_zone(
    *,
    hr_low: float = 100.0,
    hr_high: float = 200.0,
    zone: str = "Z1",
    slope: float = 3.0,
):
    return build_onflows_zone_profile(
        [
            {
                "zone": zone,
                "hr_low": hr_low,
                "hr_high": hr_high,
                "equivalence_slope_pp_per_bpm": slope,
            }
        ],
        source=MANUAL_PROFILE_SOURCE,
    )


@pytest.mark.parametrize(
    ("hr_low", "hr_high", "hr", "expected"),
    (
        (140.0, 150.0, 140.0, 0.70),
        (140.0, 150.0, 145.0, 0.85),
        (140.0, 150.0, 150.0, 1.00),
        (140.0, 155.0, 140.0, 0.55),
        (140.0, 155.0, 155.0, 1.00),
        (140.0, 160.0, 140.0, 0.40),
        (140.0, 160.0, 160.0, 1.00),
    ),
)
def test_z1_to_z4_linear_coefficient_uses_three_percentage_points_per_bpm(
    hr_low: float,
    hr_high: float,
    hr: float,
    expected: float,
) -> None:
    zone = _single_zone(hr_low=hr_low, hr_high=hr_high).zones[0]

    assert equivalence_coefficient(hr, zone) == pytest.approx(expected)


@pytest.mark.parametrize(
    ("hr", "expected"),
    ((140.0, 1.00), (145.0, 1.15), (150.0, 1.30), (155.0, 1.45)),
)
def test_z5_linear_coefficient_uses_lower_boundary_as_100_percent(
    hr: float,
    expected: float,
) -> None:
    zone = _single_zone(hr_low=140.0, hr_high=155.0, zone="Z5").zones[0]

    assert equivalence_coefficient(hr, zone) == pytest.approx(expected)


def test_wide_zone_clamps_negative_coefficient_to_zero() -> None:
    zone = _single_zone(hr_low=100.0, hr_high=200.0).zones[0]

    assert equivalence_coefficient(100.0, zone) == 0.0
    analysis = calculate_onflows_intrazone_load(
        _result([0.0, 1.0], [100.0, 100.0]),
        _single_zone(hr_low=100.0, hr_high=200.0),
    )
    assert analysis["zones"][0]["equivalent_seconds"] == 0.0


def test_sixty_minutes_at_140_in_140_to_155_zone_is_33_equivalent_minutes() -> None:
    offsets = [float(second) for second in range(0, 3601, 5)]
    analysis = calculate_onflows_intrazone_load(
        _result(offsets, [140.0] * len(offsets)),
        _single_zone(hr_low=140.0, hr_high=155.0),
    )
    zone = analysis["zones"][0]

    assert zone["real_minutes"] == pytest.approx(60.0)
    assert zone["equivalent_minutes"] == pytest.approx(33.0)
    assert zone["average_minute_value_percent"] == pytest.approx(55.0)


def test_z5_equivalent_time_is_capped_at_valid_hrmax_without_modulating_hr() -> None:
    profile = _single_zone(hr_low=140.0, hr_high=155.0, zone="Z5")
    analysis = calculate_onflows_intrazone_load(
        _result([0.0, 1.0], [180.0, 180.0]), profile
    )
    zone = analysis["zones"][0]

    assert analysis["valid_hr_max_bpm"] == 155.0
    assert zone["real_seconds"] == 1.0
    assert zone["equivalent_seconds"] == pytest.approx(1.45)
    assert zone["average_minute_value_percent"] == pytest.approx(145.0)
    # The future modulation seam must not silently change today's canonical HR.
    assert zone["mean_effective_hr_bpm"] == pytest.approx(180.0)
    assert zone["mean_raw_hr_bpm"] == pytest.approx(180.0)


@pytest.mark.parametrize(
    ("left_hr", "right_hr"),
    ((177.0, 178.0), (177.4, 177.9), (177.5, 178.0)),
)
def test_z4_to_z5_fractional_boundary_keeps_z5_at_least_100_percent(
    left_hr: float,
    right_hr: float,
) -> None:
    analysis = calculate_onflows_intrazone_load(
        _result([0.0, 1.0], [left_hr, right_hr]),
        default_onflows_zone_profile(),
    )
    z5 = next(row for row in analysis["zones"] if row["zone"] == "Z5")

    assert z5["equivalent_seconds"] == pytest.approx(z5["real_seconds"])


def test_irregular_timestamps_and_smart_recording_preserve_exact_duration() -> None:
    profile = _single_zone(hr_low=140.0, hr_high=160.0)
    result = _result(
        [0.0, 1.0, 3.5, 8.5],
        [150.0, 150.0, 150.0, 150.0],
    )
    assert [interval.classification for interval in result.intervals] == [
        "original_1hz",
        "smart_recording",
        "smart_recording",
    ]

    analysis = calculate_onflows_intrazone_load(result, profile)
    zone = analysis["zones"][0]

    assert analysis["active_duration_sec"] == pytest.approx(8.5)
    assert zone["real_seconds"] == pytest.approx(8.5)
    assert zone["equivalent_seconds"] == pytest.approx(8.5 * 0.70)


def test_time_weighted_mean_hr_and_average_minute_value_use_interval_duration() -> None:
    analysis = calculate_onflows_intrazone_load(
        _result([0.0, 1.0, 5.0], [140.0, 140.0, 150.0]),
        _single_zone(hr_low=140.0, hr_high=150.0),
    )
    zone = analysis["zones"][0]

    # 1 s at 140 bpm plus 4 s linearly from 140 to 150 bpm.
    assert zone["real_seconds"] == pytest.approx(5.0)
    assert zone["mean_effective_hr_bpm"] == pytest.approx(144.0)
    assert zone["mean_raw_hr_bpm"] == pytest.approx(144.0)
    assert zone["equivalent_seconds"] == pytest.approx(4.1)
    assert zone["average_minute_value_percent"] == pytest.approx(82.0)


def test_crossing_one_and_multiple_default_zones_preserves_duration() -> None:
    profile = default_onflows_zone_profile()
    one = calculate_onflows_intrazone_load(
        _result([0.0, 1.0], [120.0, 130.0]), profile
    )
    many = calculate_onflows_intrazone_load(
        _result([0.0, 5.0], [100.0, 195.0]), profile
    )

    assert one["classified_hr_sec"] == pytest.approx(1.0)
    assert many["classified_hr_sec"] == pytest.approx(5.0)
    assert sum(row["real_seconds"] for row in many["zones"]) == pytest.approx(5.0)
    assert sum(row["real_seconds"] > 0 for row in many["zones"]) == 5
    assert many["crossed_split_point_count"] >= 4


def test_integer_125_and_126_keep_zone_membership_without_gap() -> None:
    profile = default_onflows_zone_profile()
    at_125 = calculate_onflows_intrazone_load(
        _result([0.0, 1.0], [125.0, 125.0]), profile
    )
    at_126 = calculate_onflows_intrazone_load(
        _result([0.0, 1.0], [126.0, 126.0]), profile
    )
    crossing = calculate_onflows_intrazone_load(
        _result([0.0, 1.0], [125.0, 126.0]), profile
    )

    assert at_125["zones"][0]["real_seconds"] == 1.0
    assert at_126["zones"][1]["real_seconds"] == 1.0
    assert crossing["classified_hr_sec"] == pytest.approx(1.0)
    assert crossing["unclassified_hr_sec"] == pytest.approx(0.0)
    assert crossing["zones"][0]["real_seconds"] == pytest.approx(0.5)
    assert crossing["zones"][1]["real_seconds"] == pytest.approx(0.5)
    assert crossing["zones"][0]["equivalent_seconds"] == pytest.approx(0.5)
    assert crossing["zones"][1]["equivalent_seconds"] == pytest.approx(0.21125)
    assert crossing["zones"][0]["mean_effective_hr_bpm"] == pytest.approx(125.25)
    assert crossing["zones"][1]["mean_effective_hr_bpm"] == pytest.approx(125.75)


@pytest.mark.parametrize(
    ("hr", "expected_zone_index"),
    ((125.25, 0), (125.5, 1), (125.75, 1)),
)
def test_fractional_values_around_125_5_have_deterministic_membership(
    hr: float,
    expected_zone_index: int,
) -> None:
    analysis = calculate_onflows_intrazone_load(
        _result([0.0, 1.0], [hr, hr]),
        default_onflows_zone_profile(),
    )

    assert analysis["classified_hr_sec"] == 1.0
    assert analysis["unclassified_hr_sec"] == 0.0
    assert analysis["zones"][expected_zone_index]["real_seconds"] == 1.0


def test_partial_hr_keeps_coverage_and_excludes_only_unsupported_intervals() -> None:
    analysis = calculate_onflows_intrazone_load(
        _result([0.0, 1.0, 2.0, 3.0], [140.0, 140.0, None, 140.0]),
        _single_zone(hr_low=140.0, hr_high=155.0),
    )

    assert analysis["available"] is True
    assert analysis["active_duration_sec"] == 3.0
    assert analysis["classified_hr_sec"] == 1.0
    assert analysis["unclassified_hr_sec"] == 2.0
    assert analysis["hr_coverage_percent"] == pytest.approx(100.0 / 3.0)
    assert analysis["zones"][0]["real_seconds"] == 1.0
    assert analysis["zones"][0]["equivalent_seconds"] == pytest.approx(0.55)


@pytest.mark.parametrize("heartrate", ([140, "bad"], [140, 0], [140, 301]))
def test_invalid_hr_makes_whole_interval_unclassified(heartrate) -> None:
    analysis = calculate_onflows_intrazone_load(
        _result([0.0, 1.0], heartrate),
        _single_zone(hr_low=140.0, hr_high=155.0),
    )

    assert analysis["total_real_sec"] == 0.0
    assert analysis["total_equivalent_sec"] == 0.0
    assert analysis["unclassified_hr_sec"] == 1.0
    assert analysis["hr_coverage_percent"] == 0.0


def test_missing_hr_stream_reports_unavailable_and_zero_coverage() -> None:
    analysis = calculate_onflows_intrazone_load(
        _result([0.0, 1.0], None),
        default_onflows_zone_profile(),
    )

    assert analysis["available"] is False
    assert analysis["reason"] == "hr_stream_unavailable"
    assert analysis["active_duration_sec"] == 1.0
    assert analysis["hr_coverage_percent"] == 0.0
    assert analysis["overall_average_minute_value_percent"] is None
    assert all(
        row["average_minute_value_percent"] is None
        and row["mean_effective_hr_bpm"] is None
        for row in analysis["zones"]
    )


def test_stops_pauses_and_gaps_have_zero_real_and_equivalent_time() -> None:
    offsets = [0, 1, 41, 42, 60, 61, 450, 451, 452]
    analysis = calculate_onflows_intrazone_load(
        _result(
            offsets,
            [130] * len(offsets),
            speed=[2, 0, 2, 2, 2, 2, 2, 2, 2],
            stop_bounds={(450, 451)},
            elapsed=452,
            recording=412,
        ),
        default_onflows_zone_profile(),
    )

    assert analysis["total_real_sec"] == 4.0
    assert analysis["total_equivalent_sec"] == pytest.approx(4.0 * 0.55)
    assert analysis["excluded_duration_sec"] == 448.0
    assert analysis["excluded_duration_by_classification"] == {
        "probable_pause": 40.0,
        "recording_stop": 1.0,
        "technical_or_unexplained_gap": 389.0,
        "uncertain_gap": 18.0,
    }


@pytest.mark.parametrize("zone_count", [1, 3, 5, 7])
def test_engine_supports_variable_valid_zone_count(zone_count: int) -> None:
    rows = [
        {
            "zone": f"B{index + 1}",
            "hr_low": 60 + index * 20,
            "hr_high": 79 + index * 20,
            "equivalence_slope_pp_per_bpm": 3.0,
        }
        for index in range(zone_count)
    ]
    profile = build_onflows_zone_profile(rows, source=MANUAL_PROFILE_SOURCE)
    hr = 70 + (zone_count - 1) * 20

    analysis = calculate_onflows_intrazone_load(_result([0, 1], [hr, hr]), profile)

    assert len(analysis["zones"]) == zone_count
    assert analysis["classified_hr_sec"] == 1.0


def test_duration_and_equivalent_time_invariants_hold() -> None:
    profile = default_onflows_zone_profile()
    analysis = calculate_onflows_intrazone_load(
        _result([0, 5, 10], [100, 170, 195]), profile
    )

    assert sum(row["real_seconds"] for row in analysis["zones"]) + analysis[
        "unclassified_hr_sec"
    ] == pytest.approx(analysis["active_duration_sec"])
    for row in analysis["zones"]:
        real = row["real_seconds"]
        equivalent = row["equivalent_seconds"]
        if row["zone"] == "Z5":
            assert real <= equivalent + 1e-9
            assert equivalent <= real * 1.51 + 1e-9
        else:
            assert -1e-9 <= equivalent <= real + 1e-9


def test_result_versions_fingerprint_and_deprecated_aliases_are_deterministic() -> None:
    profile = default_onflows_zone_profile()
    result = _result([0, 1, 3, 4], [110, 130, None, 170])

    first = calculate_onflows_intrazone_load(result, profile)
    second = calculate_onflows_intrazone_load(result, profile)

    assert first == second
    assert first["algorithm_version"] == ALGORITHM_VERSION
    assert first["equivalence_version"] == INTRA_ZONE_EQUIVALENCE_VERSION
    assert first["profile_fingerprint"] == profile.fingerprint
    assert first["total_weighted_sec"] == first["total_qref_sec"] == first[
        "total_equivalent_sec"
    ]
    assert math.isfinite(first["total_equivalent_sec"])
    for row in first["zones"]:
        assert row["weighted_seconds"] == row["qref_seconds"] == row[
            "equivalent_seconds"
        ]


def test_engine_never_calls_materialize_1hz(monkeypatch) -> None:
    from intervals_inspector import stream_normalizer

    result = _result([0, 1, 2], [120, 130, 140])
    monkeypatch.setattr(
        stream_normalizer,
        "materialize_1hz",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("materialize_1hz must not be called")
        ),
    )

    analysis = calculate_onflows_intrazone_load(
        result, default_onflows_zone_profile()
    )

    assert analysis["active_duration_sec"] == 2.0


def test_existing_intervals_zone_analysis_is_not_mutated() -> None:
    result = _result([0, 1, 2], [120, 130, 140])
    adapted, reason = adapt_intervals_hr_zones(
        {"icu_hr_zones": [125, 145, 180]}
    )
    assert reason is None and adapted is not None
    before = calculate_hr_zone_time(result, adapted.zones)

    calculate_onflows_intrazone_load(result, default_onflows_zone_profile())
    after = calculate_hr_zone_time(result, adapted.zones)

    assert before == after
