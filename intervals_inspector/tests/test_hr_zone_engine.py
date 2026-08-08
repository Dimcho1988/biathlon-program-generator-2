from __future__ import annotations

import json
import math

import pytest

from intervals_inspector.hr_zone_engine import (
    ALGORITHM_VERSION,
    HRZone,
    calculate_hr_zone_time,
    validate_hr_zones,
)
from intervals_inspector.intervals_hr_adapter import (
    adapt_intervals_hr_zones,
    analyze_intervals_hr_zones,
)
from intervals_inspector.stream_normalizer import (
    NormalizerInput,
    normalize_stream_intervals,
)
from intervals_inspector.onflows_intrazone_load import (
    calculate_onflows_intrazone_load,
)
from intervals_inspector.onflows_zone_profile import (
    default_onflows_zone_profile,
    safe_profile_dict,
)
from intervals_inspector.stream_quality import (
    analyze_stream_quality,
    export_stream_quality_json,
)


def _zones() -> tuple[HRZone, ...]:
    return (
        HRZone("Easy", 0, 120, lower_inclusive=False),
        HRZone("Steady", 120, 160),
        HRZone("Hard", 160, 220, upper_inclusive=True),
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


def _seconds(analysis: dict[str, object]) -> list[float]:
    return [float(row["seconds"]) for row in analysis["zones"]]  # type: ignore[index]


def test_constant_hr_is_duration_weighted_in_one_zone() -> None:
    result = _result([0, 1, 2, 3], [130, 130, 130, 130])

    analysis = calculate_hr_zone_time(result, _zones())

    assert analysis["algorithm_version"] == ALGORITHM_VERSION
    assert _seconds(analysis) == [0.0, 3.0, 0.0]
    assert analysis["classified_hr_sec"] == 3.0
    assert analysis["unclassified_hr_sec"] == 0.0
    assert analysis["hr_coverage_percent"] == 100.0


def test_linear_hr_crosses_one_boundary_at_exact_fraction() -> None:
    result = _result([0, 1], [110, 130])

    analysis = calculate_hr_zone_time(result, _zones())

    assert _seconds(analysis) == pytest.approx([0.5, 0.5, 0.0])


def test_linear_hr_crosses_multiple_boundaries_without_integer_rounding() -> None:
    result = _result([0, 5], [100, 200])

    analysis = calculate_hr_zone_time(result, _zones())

    assert _seconds(analysis) == pytest.approx([1.0, 2.0, 2.0])
    assert analysis["classified_hr_sec"] == pytest.approx(5.0)


def test_hr_exactly_on_half_open_boundary_uses_upper_zone() -> None:
    result = _result([0, 1], [120, 120])

    analysis = calculate_hr_zone_time(result, _zones())

    assert _seconds(analysis) == [0.0, 1.0, 0.0]


@pytest.mark.parametrize(
    "heartrate",
    ([130, None], [130, "invalid"], [130, 0], [130, 301]),
)
def test_missing_or_invalid_hr_is_not_forward_filled(
    heartrate: list[object],
) -> None:
    result = _result([0, 1], heartrate)

    analysis = calculate_hr_zone_time(result, _zones())

    assert _seconds(analysis) == [0.0, 0.0, 0.0]
    assert analysis["classified_hr_sec"] == 0.0
    assert analysis["unclassified_hr_sec"] == 1.0
    assert analysis["unclassified_endpoint_interval_count"] == 1


def test_stops_pauses_and_gaps_contribute_no_zone_time() -> None:
    offsets = [0, 1, 41, 42, 60, 61, 450, 451, 452]
    result = _result(
        offsets,
        [130] * len(offsets),
        speed=[2, 0, 2, 2, 2, 2, 2, 2, 2],
        stop_bounds={(450, 451)},
        elapsed=452,
        recording=412,
    )

    analysis = calculate_hr_zone_time(result, _zones())

    assert _seconds(analysis) == [0.0, 4.0, 0.0]
    assert analysis["active_duration_sec"] == 4.0
    assert analysis["excluded_duration_sec"] == 448.0
    assert analysis["excluded_duration_by_classification"] == {
        "probable_pause": 40.0,
        "recording_stop": 1.0,
        "technical_or_unexplained_gap": 389.0,
        "uncertain_gap": 18.0,
    }


@pytest.mark.parametrize("zone_count", [1, 2, 4, 7])
def test_arbitrary_valid_zone_count_is_supported(zone_count: int) -> None:
    width = 200 / zone_count
    zones = tuple(
        HRZone(
            f"Band {index + 1}",
            index * width,
            (index + 1) * width,
            upper_inclusive=index == zone_count - 1,
        )
        for index in range(zone_count)
    )
    result = _result([0, 1], [100, 100])

    analysis = calculate_hr_zone_time(result, zones)

    assert len(analysis["zones"]) == zone_count
    assert analysis["classified_hr_sec"] == 1.0


@pytest.mark.parametrize(
    "zones",
    (
        (),
        (HRZone("Bad", 150, 120),),
        (HRZone("A", 0, 130), HRZone("B", 120, 160)),
        (
            HRZone("A", 0, 130, upper_inclusive=True),
            HRZone("B", 130, 160, lower_inclusive=True),
        ),
        (HRZone("same", 0, 100), HRZone("same", 100, 200)),
    ),
)
def test_invalid_or_overlapping_zone_configuration_is_rejected(
    zones: tuple[HRZone, ...],
) -> None:
    with pytest.raises(ValueError):
        validate_hr_zones(zones)


def test_active_duration_invariant_and_result_are_deterministic() -> None:
    result = _result([0, 1, 3, 4], [110, 130, None, 170])

    first = calculate_hr_zone_time(result, _zones())
    second = calculate_hr_zone_time(result, _zones())

    assert first == second
    total = math.fsum(row["seconds"] for row in first["zones"])
    assert total + first["unclassified_hr_sec"] == pytest.approx(
        first["active_duration_sec"], abs=first["invariant_tolerance_sec"]
    )
    assert first["invariant_delta_sec"] == pytest.approx(0.0)


def test_missing_hr_stream_returns_safe_unavailable_aggregate() -> None:
    result = _result([0, 1, 2], None, speed=[2, 2, 2])

    analysis = calculate_hr_zone_time(result, _zones())

    assert analysis["available"] is False
    assert analysis["reason"] == "hr_stream_unavailable"
    assert analysis["unclassified_hr_sec"] == 2.0


def test_zone_engine_never_calls_materialize_1hz(monkeypatch) -> None:
    from intervals_inspector import stream_normalizer

    result = _result([0, 1, 2], [130, 131, 132])
    monkeypatch.setattr(
        stream_normalizer,
        "materialize_1hz",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("materialize_1hz must not be called")
        ),
    )

    analysis = calculate_hr_zone_time(result, _zones())

    assert analysis["active_duration_sec"] == 2.0


def test_intervals_adapter_accepts_observed_upper_bound_arrays() -> None:
    adapted, reason = adapt_intervals_hr_zones(
        {
            "icu_hr_zones": [120, 140, 180],
            "icu_hr_zone_times": [2, 3.5, 4],
        }
    )

    assert reason is None
    assert adapted is not None
    assert [zone.name for zone in adapted.zones] == ["Z1", "Z2", "Z3"]
    assert adapted.zones[0] == HRZone(
        "Z1", 0, 120, lower_inclusive=False, upper_inclusive=True
    )
    assert adapted.zones[1].lower_inclusive is False
    assert adapted.reference_seconds == (2.0, 3.5, 4.0)


@pytest.mark.parametrize(
    ("detail", "reason"),
    (
        ({}, "icu_hr_zones_missing"),
        ({"icu_hr_zones": {}}, "icu_hr_zones_invalid_structure"),
        ({"icu_hr_zones": [120, None]}, "icu_hr_zones_invalid_boundaries"),
        (
            {"icu_hr_zones": [140, 120]},
            "icu_hr_zones_not_strictly_increasing",
        ),
    ),
)
def test_intervals_adapter_rejects_unknown_shapes_without_crashing(
    detail: object,
    reason: str,
) -> None:
    adapted, actual_reason = adapt_intervals_hr_zones(detail)

    assert adapted is None
    assert actual_reason == reason


def test_intervals_reference_is_comparison_only() -> None:
    result = _result([0, 1, 2], [130, 130, 130])
    detail = {
        "icu_hr_zones": [120, 140, 180],
        "icu_hr_zone_times": [10, 20, 30],
    }

    analysis = analyze_intervals_hr_zones(result, detail)

    assert _seconds(analysis) == [0.0, 2.0, 0.0]
    assert analysis["zones"][1]["intervals_reference_sec"] == 20.0
    assert analysis["zones"][1]["difference_sec"] == -18.0
    assert analysis["classified_hr_sec"] == 2.0
    assert "icu_hr_zone_times" not in json.dumps(analysis)


def test_safe_export_contains_only_aggregate_top_level_zone_analysis() -> None:
    result = _result([0, 1, 2], [130, 131, 132])
    zone_analysis = analyze_intervals_hr_zones(
        result,
        {
            "icu_hr_zones": [120, 140, 180],
            "icu_hr_zone_times": [0, 2, 0],
        },
    )
    summary = {
        "algorithm_version": "normalizer-test",
        "zone_analysis": zone_analysis,
        "onflows_load_analysis": calculate_onflows_intrazone_load(
            result, default_onflows_zone_profile()
        ),
        "onflows_zone_profile": safe_profile_dict(
            default_onflows_zone_profile()
        ),
        "intervals": [{"values": [987_654_321]}],
        "one_hz_points": [987_654_322],
        "activity_id": "private-activity",
        "token": "private-token",
        "timestamp": "2026-08-05T12:34:56Z",
        "latlng": [[42.0, 23.0]],
    }
    summary["onflows_load_analysis"]["raw_points"] = [987_654_323]
    summary["onflows_load_analysis"]["activity_id"] = "nested-private"
    quality = analyze_stream_quality(
        {},
        [
            {"type": "time", "data": [0, 1, 2]},
            {"type": "heartrate", "data": [130, 131, 132]},
        ],
    )

    exported = export_stream_quality_json(quality, summary)
    parsed = json.loads(exported)

    assert "zone_analysis" in parsed
    assert "zone_analysis" not in parsed["normalizer"]
    assert parsed["zone_analysis"]["classified_hr_sec"] == 2.0
    assert "onflows_load_analysis" in parsed
    assert "onflows_zone_profile" in parsed
    assert "onflows_load_analysis" not in parsed["normalizer"]
    assert "onflows_zone_profile" not in parsed["normalizer"]
    assert parsed["onflows_load_analysis"]["total_real_sec"] == 2.0
    assert len(parsed["onflows_zone_profile"]["fingerprint"]) == 64
    for forbidden in (
        "987654321",
        "987654322",
        "987654323",
        "private-activity",
        "nested-private",
        "private-token",
        "2026-08-05",
        "latlng",
        "42.0",
        "23.0",
        '"intervals"',
        '"one_hz_points"',
    ):
        assert forbidden not in exported
