from __future__ import annotations

import math

import pytest

from biathlon.physiology import hr_intrazone_values, intrazone_coefficient
from intervals_inspector.hr_zone_engine import calculate_hr_zone_time
from intervals_inspector.intervals_hr_adapter import adapt_intervals_hr_zones
from intervals_inspector.onflows_intrazone_load import (
    ALGORITHM_VERSION,
    calculate_onflows_intrazone_load,
    intrazone_values,
)
from intervals_inspector.onflows_zone_profile import (
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


def _single_zone(*, power: float = 1.0):
    return build_onflows_zone_profile(
        [
            {
                "zone": "Only",
                "hr_low": 100,
                "hr_high": 200,
                "weight_low": 100,
                "weight_high": 200,
                "power": power,
            }
        ],
        source=MANUAL_PROFILE_SOURCE,
    )


@pytest.mark.parametrize("hr", [80.0, 100.0, 112.5, 125.0, 140.0])
def test_intrazone_formula_has_exact_parity_with_legacy_physiology(hr: float) -> None:
    zone = default_onflows_zone_profile().zones[0]

    u, weight, coefficient = intrazone_values(hr, zone)
    legacy_u, legacy_weight, legacy_coefficient = hr_intrazone_values(
        hr,
        {
            "hr_low": zone.hr_low,
            "hr_high": zone.hr_high,
            "weight_low": zone.weight_low,
            "weight_high": zone.weight_high,
            "power": zone.power,
        },
    )

    assert (u, weight, coefficient) == pytest.approx(
        (legacy_u, legacy_weight, legacy_coefficient)
    )
    assert coefficient == pytest.approx(
        intrazone_coefficient(
            u, zone.weight_low, zone.weight_high, zone.power
        )
    )


@pytest.mark.parametrize(
    ("hr", "expected_u", "expected_k"),
    ((100, 0.0, 1.0), (150, 0.5, 1.5), (200, 1.0, 2.0)),
)
def test_u_zero_one_and_midpoint(hr: float, expected_u: float, expected_k: float) -> None:
    zone = _single_zone().zones[0]

    u, _weight, coefficient = intrazone_values(hr, zone)

    assert u == expected_u
    assert coefficient == expected_k


@pytest.mark.parametrize("power", [0.5, 1.0, 2.0])
def test_constant_hr_respects_power_curve(power: float) -> None:
    profile = _single_zone(power=power)
    result = _result([0, 1, 2], [150, 150, 150])

    analysis = calculate_onflows_intrazone_load(result, profile)
    expected_k = 1.0 + 0.5**power

    assert analysis["zones"][0]["real_seconds"] == 2.0
    assert analysis["zones"][0]["weighted_seconds"] == pytest.approx(
        2.0 * expected_k
    )
    assert analysis["zones"][0]["average_k"] == pytest.approx(expected_k)


@pytest.mark.parametrize(
    ("power", "expected_average_k"),
    ((0.5, 1.0 + 2.0 / 3.0), (1.0, 1.5), (2.0, 1.0 + 1.0 / 3.0)),
)
def test_linear_hr_inside_zone_uses_closed_form_integral(
    power: float,
    expected_average_k: float,
) -> None:
    profile = _single_zone(power=power)
    result = _result([0, 5], [100, 200])

    analysis = calculate_onflows_intrazone_load(result, profile)

    assert analysis["zones"][0]["real_seconds"] == 5.0
    assert analysis["zones"][0]["weighted_seconds"] == pytest.approx(
        5.0 * expected_average_k
    )


def test_increasing_and_decreasing_hr_have_same_exact_integral() -> None:
    profile = _single_zone(power=1.7)
    increasing = calculate_onflows_intrazone_load(
        _result([0, 5], [110, 190]), profile
    )
    decreasing = calculate_onflows_intrazone_load(
        _result([0, 5], [190, 110]), profile
    )

    assert decreasing["zones"][0]["weighted_seconds"] == pytest.approx(
        increasing["zones"][0]["weighted_seconds"]
    )


def test_crossing_one_and_multiple_default_zones_preserves_duration() -> None:
    profile = default_onflows_zone_profile()
    one = calculate_onflows_intrazone_load(
        _result([0, 1], [120, 130]), profile
    )
    many = calculate_onflows_intrazone_load(
        _result([0, 5], [100, 195]), profile
    )

    assert one["classified_hr_sec"] == pytest.approx(1.0)
    assert many["classified_hr_sec"] == pytest.approx(5.0)
    assert sum(row["real_seconds"] for row in many["zones"]) == pytest.approx(5.0)
    assert sum(row["real_seconds"] > 0 for row in many["zones"]) == 5


def test_integer_125_and_126_keep_legacy_zone_membership_without_gap() -> None:
    profile = default_onflows_zone_profile()
    at_125 = calculate_onflows_intrazone_load(
        _result([0, 1], [125, 125]), profile
    )
    at_126 = calculate_onflows_intrazone_load(
        _result([0, 1], [126, 126]), profile
    )
    crossing = calculate_onflows_intrazone_load(
        _result([0, 1], [125, 126]), profile
    )

    assert at_125["zones"][0]["real_seconds"] == 1.0
    assert at_126["zones"][1]["real_seconds"] == 1.0
    assert crossing["classified_hr_sec"] == pytest.approx(1.0)
    assert crossing["unclassified_hr_sec"] == pytest.approx(0.0)
    assert crossing["zones"][0]["real_seconds"] == pytest.approx(0.5)
    assert crossing["zones"][1]["real_seconds"] == pytest.approx(0.5)


@pytest.mark.parametrize(
    ("hr", "expected_zone_index"),
    ((125.25, 0), (125.5, 1), (125.75, 1)),
)
def test_fractional_values_around_125_5_have_deterministic_membership(
    hr: float,
    expected_zone_index: int,
) -> None:
    analysis = calculate_onflows_intrazone_load(
        _result([0, 1], [hr, hr]),
        default_onflows_zone_profile(),
    )

    assert analysis["classified_hr_sec"] == 1.0
    assert analysis["unclassified_hr_sec"] == 0.0
    assert analysis["zones"][expected_zone_index]["real_seconds"] == 1.0


@pytest.mark.parametrize("heartrate", ([130, None], [130, "bad"], [130, 0], [130, 301]))
def test_missing_or_invalid_hr_makes_whole_interval_unclassified(heartrate) -> None:
    analysis = calculate_onflows_intrazone_load(
        _result([0, 1], heartrate),
        default_onflows_zone_profile(),
    )

    assert analysis["total_real_sec"] == 0.0
    assert analysis["total_weighted_sec"] == 0.0
    assert analysis["unclassified_hr_sec"] == 1.0


def test_stops_pauses_and_gaps_have_zero_real_and_weighted_time() -> None:
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
    assert analysis["total_weighted_sec"] > 4.0
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
            "weight_low": 100 + index * 10,
            "weight_high": 110 + index * 10,
            "power": 0.5 + index / 3,
        }
        for index in range(zone_count)
    ]
    profile = build_onflows_zone_profile(rows, source=MANUAL_PROFILE_SOURCE)
    hr = 70 + (zone_count - 1) * 20

    analysis = calculate_onflows_intrazone_load(
        _result([0, 1], [hr, hr]), profile
    )

    assert len(analysis["zones"]) == zone_count
    assert analysis["classified_hr_sec"] == 1.0


def test_both_duration_and_weight_bounds_invariants_hold() -> None:
    profile = default_onflows_zone_profile()
    analysis = calculate_onflows_intrazone_load(
        _result([0, 5, 10], [100, 170, 195]), profile
    )

    assert sum(row["real_seconds"] for row in analysis["zones"]) + analysis[
        "unclassified_hr_sec"
    ] == pytest.approx(analysis["active_duration_sec"])
    for row in analysis["zones"]:
        real = row["real_seconds"]
        weighted = row["weighted_seconds"]
        assert real <= weighted + 1e-9
        assert weighted <= real * row["weight_high"] / row["weight_low"] + 1e-9


def test_result_and_fingerprint_are_deterministic() -> None:
    profile = default_onflows_zone_profile()
    result = _result([0, 1, 3, 4], [110, 130, None, 170])

    first = calculate_onflows_intrazone_load(result, profile)
    second = calculate_onflows_intrazone_load(result, profile)

    assert first == second
    assert first["algorithm_version"] == ALGORITHM_VERSION
    assert first["profile_fingerprint"] == profile.fingerprint
    assert math.isfinite(first["total_weighted_sec"])


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
