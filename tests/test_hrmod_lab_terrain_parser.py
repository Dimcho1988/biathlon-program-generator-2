from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from hrmod_lab.hrmod_core import compute_hrmod_hr_only
from hrmod_lab.schemas import AthleteHRProfile, HRmodConfig, HRZone
from hrmod_lab.tcx_adapter import parse_tcx


START = datetime(2026, 1, 1, tzinfo=UTC)


def _terrain_tcx(
    *,
    grades: tuple[float | None, ...],
    altitudes: tuple[float | None, ...],
    distances: tuple[float | None, ...],
) -> bytes:
    if not (len(grades) == len(altitudes) == len(distances)):
        raise ValueError("terrain fixture channels must have equal lengths")

    points: list[str] = []
    for index, (grade, altitude, distance) in enumerate(
        zip(grades, altitudes, distances, strict=True)
    ):
        timestamp = (START + timedelta(seconds=index)).isoformat().replace(
            "+00:00", "Z"
        )
        altitude_xml = (
            f"<AltitudeMeters>{altitude}</AltitudeMeters>"
            if altitude is not None
            else ""
        )
        distance_xml = (
            f"<DistanceMeters>{distance}</DistanceMeters>"
            if distance is not None
            else ""
        )
        grade_xml = (
            f"<Extensions><ext:TPX><ext:Grade>{grade}</ext:Grade>"
            "</ext:TPX></Extensions>"
            if grade is not None
            else ""
        )
        points.append(
            "<Trackpoint>"
            f"<Time>{timestamp}</Time>"
            f"<HeartRateBpm><Value>{100 + index}</Value></HeartRateBpm>"
            f"{altitude_xml}{distance_xml}{grade_xml}"
            "</Trackpoint>"
        )

    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<TrainingCenterDatabase xmlns="http://www.garmin.com/xmlschemas/'
        'TrainingCenterDatabase/v2" xmlns:ext="http://example.test/tcx-extension">'
        '<Activities><Activity Sport="Other"><Lap StartTime="2026-01-01T00:00:00Z">'
        "<Track>"
        + "".join(points)
        + "</Track></Lap></Activity></Activities></TrainingCenterDatabase>"
    ).encode("utf-8")


def _profile() -> AthleteHRProfile:
    bounds = (50.0, 100.0, 120.0, 140.0, 160.0, 200.0)
    return AthleteHRProfile(
        hrmax_bpm=200.0,
        hr_floor_bpm=50.0,
        zones=tuple(
            HRZone(f"Z{index + 1}", bounds[index], bounds[index + 1])
            for index in range(5)
        ),
    )


def test_ready_tcx_grade_is_reference_only_and_reports_explicit_coverage() -> None:
    parsed = parse_tcx(
        _terrain_tcx(
            grades=(1.5, None, -3.0),
            altitudes=(100.0, 100.2, 99.9),
            distances=(0.0, 10.0, 20.0),
        )
    )

    reference = parsed.reference_channels
    assert [sample.grade for sample in reference.samples] == [1.5, None, -3.0]
    assert reference.metadata["grade_source"] == "tcx_grade"
    assert reference.metadata["grade_unit"] == "percent"
    assert reference.metadata["grade_is_derived"] is False
    assert reference.metadata["grade_coverage_fraction"] == pytest.approx(2 / 3)
    assert reference.metadata["altitude_coverage_fraction"] == 1.0
    assert reference.metadata["distance_coverage_fraction"] == 1.0
    assert reference.metadata["altitude_distance_joint_coverage_fraction"] == 1.0
    assert "grade" in reference.available_channels

    hr_sample = parsed.hr_input_samples[0]
    assert not hasattr(hr_sample, "grade")
    assert not hasattr(hr_sample, "altitude_m")
    assert not hasattr(hr_sample, "distance_m")


def test_missing_grade_stays_unavailable_while_derivation_inputs_are_reported() -> None:
    parsed = parse_tcx(
        _terrain_tcx(
            grades=(None, None, None),
            altitudes=(100.0, None, 99.0),
            distances=(0.0, 10.0, 20.0),
        )
    )

    reference = parsed.reference_channels
    assert [sample.grade for sample in reference.samples] == [None, None, None]
    assert reference.metadata["grade_source"] == "unavailable"
    assert reference.metadata["grade_unit"] is None
    assert reference.metadata["grade_is_derived"] is False
    assert reference.metadata["grade_coverage_fraction"] == 0.0
    assert reference.metadata["altitude_coverage_fraction"] == pytest.approx(2 / 3)
    assert reference.metadata["distance_coverage_fraction"] == 1.0
    assert reference.metadata["altitude_distance_joint_coverage_fraction"] == pytest.approx(
        2 / 3
    )
    assert "grade" not in reference.available_channels


def test_terrain_channels_cannot_change_hr_only_result_or_hash() -> None:
    sample_count = 30
    flat = parse_tcx(
        _terrain_tcx(
            grades=(0.0,) * sample_count,
            altitudes=(100.0,) * sample_count,
            distances=tuple(float(index * 5) for index in range(sample_count)),
        )
    )
    downhill = parse_tcx(
        _terrain_tcx(
            grades=(-12.0,) * sample_count,
            altitudes=tuple(200.0 - index for index in range(sample_count)),
            distances=tuple(float(index * 8) for index in range(sample_count)),
        )
    )

    assert flat.hr_input_samples == downhill.hr_input_samples
    assert flat.reference_channels != downhill.reference_channels
    flat_core = compute_hrmod_hr_only(
        hr_samples=flat.hr_input_samples,
        athlete_profile=_profile(),
        config=HRmodConfig(),
    )
    downhill_core = compute_hrmod_hr_only(
        hr_samples=downhill.hr_input_samples,
        athlete_profile=_profile(),
        config=HRmodConfig(),
    )
    assert flat_core == downhill_core
    assert flat_core.hr_input_hash == downhill_core.hr_input_hash

