from __future__ import annotations

from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

from vflat_lab import (
    VFlatConfig,
    activity_summary,
    apply_vflat_model,
    grade_speed_ratio,
    parse_tcx,
    prepare_activity,
    segment_timeseries,
)


def _tiny_tcx(samples: int = 90) -> bytes:
    start = datetime(2026, 8, 20, 8, 0, tzinfo=timezone.utc)
    points = []
    for index in range(samples):
        timestamp = (start + timedelta(seconds=index)).isoformat().replace("+00:00", "Z")
        distance = index * 4.0
        altitude = 1000.0 + index * 0.12
        points.append(
            f"""
            <Trackpoint>
              <Time>{timestamp}</Time>
              <AltitudeMeters>{altitude:.3f}</AltitudeMeters>
              <DistanceMeters>{distance:.3f}</DistanceMeters>
              <HeartRateBpm><Value>{145 + index % 3}</Value></HeartRateBpm>
            </Trackpoint>
            """
        )
    return (
        "<?xml version='1.0' encoding='UTF-8'?>"
        "<TrainingCenterDatabase xmlns='http://www.garmin.com/xmlschemas/TrainingCenterDatabase/v2'>"
        "<Activities><Activity Sport='Other'><Id>2026-08-20T08:00:00Z</Id>"
        "<Lap StartTime='2026-08-20T08:00:00Z'><Track>"
        + "".join(points)
        + "</Track></Lap><Creator><Name>Test device</Name><UnitId>42</UnitId></Creator>"
        "</Activity></Activities></TrainingCenterDatabase>"
    ).encode("utf-8")


def test_grade_curve_is_flat_then_saturating() -> None:
    config = VFlatConfig()
    grades = np.array([-3.0, -1.0, 0.0, 1.0, 3.0, 6.0, 9.0, 12.0, 20.0])
    ratio = grade_speed_ratio(grades, config)
    assert ratio[1] == ratio[2] == ratio[3] == 1.0
    assert ratio[0] > 1.0
    assert np.all(np.diff(ratio[3:]) < 0)
    # Saturation means the last step is smaller than the first uphill step.
    assert ratio[-2] - ratio[-1] < ratio[3] - ratio[4]


def test_dynamic_terms_have_expected_direction_and_memory_decays() -> None:
    grade = np.r_[np.full(20, -6.0), np.zeros(25), np.full(20, 7.0), np.zeros(25)]
    acceleration = np.zeros(len(grade))
    acceleration[25] = 0.2
    acceleration[30] = -0.2
    frame = pd.DataFrame(
        {
            "grade_pct": grade,
            "speed_mps": np.full(len(grade), 5.0),
            "accel_mps2": acceleration,
            "block": np.ones(len(grade), dtype=int),
            "turn_flag": np.zeros(len(grade), dtype=bool),
        }
    )
    result = apply_vflat_model(frame, VFlatConfig(output_smoothing_s=1))
    assert result.loc[25, "acceleration_term_kmh"] > 0
    assert result.loc[30, "deceleration_term_kmh"] < 0
    assert result.loc[20, "descent_memory_term_kmh"] < 0
    assert abs(result.loc[44, "descent_memory_term_kmh"]) < abs(result.loc[20, "descent_memory_term_kmh"])
    assert result.loc[65, "climb_memory_term_kmh"] > 0
    assert result.loc[89, "climb_memory_term_kmh"] < result.loc[65, "climb_memory_term_kmh"]


def test_terrain_memory_does_not_cross_recording_gap() -> None:
    frame = pd.DataFrame(
        {
            "grade_pct": np.r_[np.full(20, -7.0), np.zeros(20)],
            "speed_mps": np.full(40, 5.0),
            "accel_mps2": np.zeros(40),
            "block": np.r_[np.ones(20, dtype=int), np.full(20, 2, dtype=int)],
            "turn_flag": np.zeros(40, dtype=bool),
        }
    )
    result = apply_vflat_model(frame, VFlatConfig(output_smoothing_s=1))
    assert result.loc[19, "descent_memory"] > 0
    assert result.loc[20, "descent_memory"] == 0
    assert result.loc[20, "descent_memory_term_kmh"] == 0


def test_tcx_pipeline_produces_segment_diagnostics() -> None:
    config = VFlatConfig(
        altitude_smoothing_m=25,
        speed_smoothing_s=9,
        output_smoothing_s=5,
        segment_s=15,
        min_segment_coverage=0.5,
    )
    parsed = parse_tcx(_tiny_tcx(), filename="controlled_z3.tcx")
    prepared = prepare_activity(parsed, config)
    modelled = apply_vflat_model(prepared, config)
    segments = segment_timeseries(modelled, config)
    summary = activity_summary(segments)

    assert parsed.metadata["trackpoints"] == 90
    assert len(prepared) == 90
    assert modelled.valid.any()
    assert int(summary["segments"]) >= 4
    assert float(summary["final_median_kmh"]) > 14.0
    assert float(summary["final_central90_width_kmh"]) < 1.0
    assert summary["target_5kmh_met"] == "Да"
